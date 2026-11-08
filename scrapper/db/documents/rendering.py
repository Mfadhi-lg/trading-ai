"""Persistence for portable PDF derivatives of validated HTML documents."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ...config import DOCUMENT_CONTENT_SNAPSHOTS
from ...documents.download.derivatives import (
    HTML_PDF_MEDIA_TYPE,
    HTML_PDF_RENDERER,
    HTML_PDF_RENDERER_VERSION,
    render_validated_html_to_pdf,
)
from ..document_artifact_repo import register_artifact_content
from .download import store_response_blob
from ..repo import utc_now


@dataclass(frozen=True)
class PersistedHtmlPdfRendering:
    rendering_id: int
    artifact_id: int
    source_content_id: int
    rendered_content_id: int
    reused: bool
    visible_character_count: int
    block_count: int

PersistedHtmlPdfRendering.__module__ = 'scrapper.db.html_document_rendering_repo'


def _source_content(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    source_content_id: int,
    allow_browser_snapshot: bool = False,
) -> tuple[sqlite3.Row, bytes]:
    row = conn.execute(
        """SELECT content.*,blob.local_path,artifact.title_raw
             FROM artifact_contents AS content
             JOIN response_blobs AS blob USING (blob_id)
             JOIN document_artifacts AS artifact USING (artifact_id)
            WHERE content.artifact_id=? AND content.content_id=?""",
        (artifact_id, source_content_id),
    ).fetchone()
    if row is None:
        raise ValueError("HTML source content does not belong to the artifact")
    if row["media_type"] != "text/html":
        raise ValueError("PDF rendering requires text/html source content")
    if row["content_origin"] != "publisher_original" and not (
        allow_browser_snapshot and row["content_origin"] == "generated_derivative"
    ):
        raise ValueError("PDF rendering requires publisher-original HTML")
    path = Path(str(row["local_path"]))
    if not path.is_file():
        raise RuntimeError(f"Immutable HTML source is missing: {path}")
    body = path.read_bytes()
    if len(body) != int(row["byte_size"]):
        raise RuntimeError("Immutable HTML source byte size changed")
    if hashlib.sha256(body).hexdigest() != str(row["content_sha256"]):
        raise RuntimeError("Immutable HTML source hash changed")
    return row, body


def render_and_persist_html_pdf(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    source_content_id: int,
    source_url: str,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
    created_at_utc: str | None = None,
    allow_browser_snapshot: bool = False,
    _source_content=_source_content,
    register_artifact_content=register_artifact_content,
    render_validated_html_to_pdf=render_validated_html_to_pdf,
    store_response_blob=store_response_blob,
    utc_now=utc_now,
    HTML_PDF_MEDIA_TYPE=HTML_PDF_MEDIA_TYPE,
    HTML_PDF_RENDERER=HTML_PDF_RENDERER,
    HTML_PDF_RENDERER_VERSION=HTML_PDF_RENDERER_VERSION,
) -> PersistedHtmlPdfRendering:
    """Create or reuse one auditable PDF derivative for validated HTML."""

    options = {
        "external_subresources": "disabled",
        "format": "A4",
        "render_mode": "visible_text_blocks",
    }
    options_json = json.dumps(options, sort_keys=True, separators=(",", ":"))
    existing = conn.execute(
        """SELECT * FROM html_document_renderings
            WHERE source_content_id=? AND renderer_name=?
              AND renderer_version=? AND options_json=?""",
        (
            source_content_id,
            HTML_PDF_RENDERER,
            HTML_PDF_RENDERER_VERSION,
            options_json,
        ),
    ).fetchone()
    if existing is not None:
        return PersistedHtmlPdfRendering(
            rendering_id=int(existing["rendering_id"]),
            artifact_id=int(existing["artifact_id"]),
            source_content_id=int(existing["source_content_id"]),
            rendered_content_id=int(existing["rendered_content_id"]),
            reused=True,
            visible_character_count=int(existing["visible_character_count"]),
            block_count=int(existing["block_count"]),
        )

    source, source_body = _source_content(
        conn,
        artifact_id=artifact_id,
        source_content_id=source_content_id,
        allow_browser_snapshot=allow_browser_snapshot,
    )
    rendered = render_validated_html_to_pdf(
        source_body,
        source_url=source_url,
        document_title=str(source["title_raw"] or ""),
    )
    now = created_at_utc or utc_now()
    with conn:
        blob_id, _ = store_response_blob(
            conn,
            body=rendered.body,
            media_type=HTML_PDF_MEDIA_TYPE,
            observed_at_utc=now,
            snapshot_root=snapshot_root,
        )
        rendered_content_id = register_artifact_content(
            conn,
            artifact_id=artifact_id,
            blob_id=blob_id,
            media_type=HTML_PDF_MEDIA_TYPE,
            is_official=False,
            content_origin="generated_derivative",
            created_at_utc=now,
        )
        # A renderer update can leave some reports byte-identical. The physical
        # content and existing rendering are reused rather than inserting a
        # second row against the unique rendered_content_id.
        identical = conn.execute(
            "SELECT * FROM html_document_renderings WHERE rendered_content_id=?",
            (rendered_content_id,),
        ).fetchone()
        if identical is not None:
            if int(identical["source_content_id"]) != source_content_id:
                raise ValueError("Identical rendered content has a different HTML source")
            return PersistedHtmlPdfRendering(
                rendering_id=int(identical["rendering_id"]), artifact_id=artifact_id,
                source_content_id=source_content_id, rendered_content_id=rendered_content_id,
                reused=True, visible_character_count=rendered.visible_character_count,
                block_count=rendered.block_count,
            )
        conn.execute(
            """INSERT OR IGNORE INTO artifact_content_relationships
               (artifact_id,source_content_id,derived_content_id,
                relationship_type,created_at_utc)
               VALUES (?,?,?,'rendered_pdf',?)""",
            (artifact_id, source_content_id, rendered_content_id, now),
        )
        cursor = conn.execute(
            """INSERT INTO html_document_renderings
               (artifact_id,source_content_id,rendered_content_id,
                renderer_name,renderer_version,options_json,
                visible_character_count,block_count,created_at_utc)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                artifact_id,
                source_content_id,
                rendered_content_id,
                HTML_PDF_RENDERER,
                HTML_PDF_RENDERER_VERSION,
                options_json,
                rendered.visible_character_count,
                rendered.block_count,
                now,
            ),
        )
    return PersistedHtmlPdfRendering(
        rendering_id=int(cursor.lastrowid),
        artifact_id=artifact_id,
        source_content_id=source_content_id,
        rendered_content_id=rendered_content_id,
        reused=False,
        visible_character_count=rendered.visible_character_count,
        block_count=rendered.block_count,
    )
