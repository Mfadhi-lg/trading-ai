"""Persistence for portable PDF derivatives of validated HTML documents."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..config import DOCUMENT_CONTENT_SNAPSHOTS
from ..documents.html_pdf import (
    HTML_PDF_MEDIA_TYPE,
    HTML_PDF_RENDERER,
    HTML_PDF_RENDERER_VERSION,
    render_validated_html_to_pdf,
)
from .document_artifact_repo import register_artifact_content
from .document_download_repo import store_response_blob
from .repo import utc_now


from .documents.rendering import PersistedHtmlPdfRendering


def _source_content(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    source_content_id: int,
    allow_browser_snapshot: bool = False,
) -> tuple[sqlite3.Row, bytes]:
    """Compatible entry point for the extracted owner."""
    from .documents.rendering import _source_content as _owner
    return _owner(
        conn,
        artifact_id=artifact_id,
        source_content_id=source_content_id,
        allow_browser_snapshot=allow_browser_snapshot,
    )


def render_and_persist_html_pdf(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    source_content_id: int,
    source_url: str,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
    created_at_utc: str | None = None,
    allow_browser_snapshot: bool = False,
) -> PersistedHtmlPdfRendering:
    """Compatible entry point for the extracted owner."""
    from .documents.rendering import render_and_persist_html_pdf as _owner
    return _owner(
        conn,
        artifact_id=artifact_id,
        source_content_id=source_content_id,
        source_url=source_url,
        snapshot_root=snapshot_root,
        created_at_utc=created_at_utc,
        allow_browser_snapshot=allow_browser_snapshot,
        _source_content=_source_content,
        register_artifact_content=register_artifact_content,
        render_validated_html_to_pdf=render_validated_html_to_pdf,
        store_response_blob=store_response_blob,
        utc_now=utc_now,
        HTML_PDF_MEDIA_TYPE=HTML_PDF_MEDIA_TYPE,
        HTML_PDF_RENDERER=HTML_PDF_RENDERER,
        HTML_PDF_RENDERER_VERSION=HTML_PDF_RENDERER_VERSION,
    )
