"""D04 offline credit-rating normalization from a persisted D03 extraction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from ..config import DB_PATH, DOCUMENT_CREDIT_RATING_EXPORTS
from ..db.credit_rating_repo import credit_rating_payload, persist_credit_rating
from ..db.database import connect, init_db
from ..db.document_extraction_repo import extraction_payload
from ..db.repo import utc_now
from ..documents.credit_rating import (
    CREDIT_RATING_OPTIONS,
    CREDIT_RATING_PARSER_NAME,
    CREDIT_RATING_PARSER_VERSION,
    canonical_json,
    normalize_credit_rating,
)


CREDIT_RATING_REPORT_SCHEMA_VERSION = "1.0.0"


def _load_extraction(
    conn: Any, *, artifact_id: int, extraction_id: int | None
) -> Any:
    if extraction_id is None:
        row = conn.execute(
            """SELECT extraction.*, content.content_sha256, content.byte_size,
                      content.media_type, content.blob_id, blob.local_path,
                      artifact.title_raw, artifact.publisher,
                      artifact.preferred_content_id
               FROM document_artifacts AS artifact
               JOIN artifact_contents AS content
                 ON content.artifact_id=artifact.artifact_id
               JOIN document_extractions AS extraction
                 ON extraction.content_id=content.content_id
               JOIN response_blobs AS blob USING (blob_id)
               WHERE artifact.artifact_id=?
                 AND artifact.artifact_type='credit_rating'
                 AND extraction.extraction_status='ok'
               ORDER BY (content.content_id=artifact.preferred_content_id) DESC,
                        content.is_official DESC, extraction.extraction_id DESC
               LIMIT 1""",
            (artifact_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT extraction.*, content.content_sha256, content.byte_size,
                      content.media_type, content.blob_id, blob.local_path,
                      artifact.title_raw, artifact.publisher,
                      artifact.preferred_content_id
               FROM document_artifacts AS artifact
               JOIN artifact_contents AS content
                 ON content.artifact_id=artifact.artifact_id
               JOIN document_extractions AS extraction
                 ON extraction.content_id=content.content_id
               JOIN response_blobs AS blob USING (blob_id)
               WHERE artifact.artifact_id=?
                 AND artifact.artifact_type='credit_rating'
                 AND extraction.extraction_id=?
                 AND extraction.extraction_status='ok'""",
            (artifact_id, extraction_id),
        ).fetchone()
    if row is None:
        raise ValueError(
            f"Artifact {artifact_id} has no matching usable credit-rating extraction"
        )
    if row["media_type"] != "application/pdf":
        raise ValueError("D04 credit-rating normalization currently requires PDF content")
    return row


def _verify_content_bytes(row: Any) -> None:
    path = Path(row["local_path"])
    if not path.is_file():
        raise RuntimeError(f"Immutable content bytes are missing: {path}")
    body = path.read_bytes()
    if len(body) != int(row["byte_size"]):
        raise RuntimeError("Immutable credit-rating content byte size changed")
    if hashlib.sha256(body).hexdigest() != row["content_sha256"]:
        raise RuntimeError("Immutable credit-rating content hash changed")


def _default_output_path(artifact_id: int, content_id: int) -> Path:
    return DOCUMENT_CREDIT_RATING_EXPORTS / str(artifact_id) / f"content-{content_id}.json"


def normalize_credit_rating_document(
    *,
    artifact_id: int,
    extraction_id: int | None = None,
    db_path: Path = DB_PATH,
    output: Path | None = None,
    now_utc: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    """Normalize one rating report without network access or source mutation."""

    conn = connect(db_path)
    try:
        init_db(conn)
        extraction_row = _load_extraction(
            conn, artifact_id=artifact_id, extraction_id=extraction_id
        )
        _verify_content_bytes(extraction_row)
        options_json = canonical_json(CREDIT_RATING_OPTIONS)
        existing = conn.execute(
            """SELECT normalization_id FROM credit_rating_normalizations
               WHERE extraction_id=? AND parser_name=? AND parser_version=?
                 AND options_json=?""",
            (
                int(extraction_row["extraction_id"]),
                CREDIT_RATING_PARSER_NAME,
                CREDIT_RATING_PARSER_VERSION,
                options_json,
            ),
        ).fetchone()
        reused = existing is not None
        extraction = extraction_payload(conn, int(extraction_row["extraction_id"]))
        if existing is None:
            started_at = now_utc()
            normalized = normalize_credit_rating(extraction)
            completed_at = now_utc()
            persisted = persist_credit_rating(
                conn,
                artifact_id=artifact_id,
                content_id=int(extraction_row["content_id"]),
                extraction_id=int(extraction_row["extraction_id"]),
                normalized=normalized,
                started_at_utc=started_at,
                completed_at_utc=completed_at,
            )
            normalization_id = persisted.normalization_id
        else:
            normalization_id = int(existing["normalization_id"])
        normalization = credit_rating_payload(conn, normalization_id)
        payload = {
            "schema_version": CREDIT_RATING_REPORT_SCHEMA_VERSION,
            "artifact": {
                "artifact_id": artifact_id,
                "artifact_type": "credit_rating",
                "title_raw": extraction_row["title_raw"],
                "publisher": extraction_row["publisher"],
                "preferred_content_id": extraction_row["preferred_content_id"],
            },
            "content": {
                "content_id": int(extraction_row["content_id"]),
                "blob_id": int(extraction_row["blob_id"]),
                "media_type": extraction_row["media_type"],
                "content_sha256": extraction_row["content_sha256"],
                "byte_size": int(extraction_row["byte_size"]),
            },
            "extraction": {
                "extraction_id": int(extraction_row["extraction_id"]),
                "parser_name": extraction["parser_name"],
                "parser_version": extraction["parser_version"],
                "source_pages": [
                    {
                        "page_number": page["page_or_slide_number"],
                        "selected_text_origin": page["selected_text_origin"],
                        "selected_text": page["selected_text"],
                    }
                    for page in extraction["pages"]
                ],
            },
            "reused": reused,
            "normalization": normalization,
        }
        output_path = output or _default_output_path(
            artifact_id, int(extraction_row["content_id"])
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        payload["output"] = str(output_path.resolve())
        return payload
    finally:
        conn.close()
