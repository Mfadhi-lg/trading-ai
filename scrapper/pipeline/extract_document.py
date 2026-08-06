"""D03 offline extraction orchestration for validated PDF/PPT/PPTX contents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from ..config import DB_PATH, DOCUMENT_EXTRACTION_EXPORTS
from ..db.database import connect, init_db
from ..db.document_extraction_repo import extraction_payload, persist_extraction
from ..db.repo import utc_now
from ..documents.downloader import PDF, PPT, PPTX
from ..documents.extraction import (
    DOCUMENT_PARSER_NAME,
    DOCUMENT_PARSER_VERSION,
    canonical_json,
)
from ..documents.ocr import OcrEngine, TesseractOcr
from ..documents.pdf import extract_pdf
from ..documents.powerpoint import extract_powerpoint


EXTRACTION_REPORT_SCHEMA_VERSION = "1.0.0"
SUPPORTED_MEDIA_TYPES = frozenset({PDF, PPT, PPTX})


def _load_content(
    conn: Any, *, artifact_id: int, content_id: int | None
) -> Any:
    if content_id is None:
        row = conn.execute(
            """SELECT content.*, blob.local_path
               FROM document_artifacts AS artifact
               JOIN artifact_contents AS content
                 ON content.artifact_id=artifact.artifact_id
               JOIN response_blobs AS blob USING (blob_id)
               WHERE artifact.artifact_id=?
               ORDER BY (content.content_id=artifact.preferred_content_id) DESC,
                        content.is_official DESC, content.content_id DESC
               LIMIT 1""",
            (artifact_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT content.*, blob.local_path
               FROM artifact_contents AS content
               JOIN response_blobs AS blob USING (blob_id)
               WHERE content.artifact_id=? AND content.content_id=?""",
            (artifact_id, content_id),
        ).fetchone()
    if row is None:
        raise ValueError(f"Artifact {artifact_id} has no matching downloaded content")
    if row["media_type"] not in SUPPORTED_MEDIA_TYPES:
        raise ValueError(
            f"Content {row['content_id']} media type is not extractable in D03: "
            f"{row['media_type']}"
        )
    return row


def _verified_bytes(content: Any) -> bytes:
    path = Path(content["local_path"])
    if not path.is_file():
        raise RuntimeError(f"Immutable content bytes are missing: {path}")
    body = path.read_bytes()
    if len(body) != int(content["byte_size"]):
        raise RuntimeError("Immutable content byte size changed")
    if hashlib.sha256(body).hexdigest() != content["content_sha256"]:
        raise RuntimeError("Immutable content hash changed")
    return body


def _default_output_path(artifact_id: int, content_id: int) -> Path:
    return DOCUMENT_EXTRACTION_EXPORTS / str(artifact_id) / f"content-{content_id}.json"


def extract_document(
    *,
    artifact_id: int,
    content_id: int | None = None,
    db_path: Path = DB_PATH,
    output: Path | None = None,
    ocr_engine: OcrEngine | None = None,
    now_utc: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    """Extract a downloaded document without any network access."""

    engine = ocr_engine or TesseractOcr()
    engine_version = engine.version
    engine_options = (
        engine.options() if callable(getattr(engine, "options", None)) else {
            "language": engine.language
        }
    )
    options = {
        "ocr": engine_options,
        "native_usability_rule": "alphanumeric_3_replacement_ratio_0_05_v1",
    }
    options_json = canonical_json(options)
    conn = connect(db_path)
    try:
        init_db(conn)
        content = _load_content(
            conn, artifact_id=artifact_id, content_id=content_id
        )
        body = _verified_bytes(content)
        existing = conn.execute(
            """SELECT extraction_id FROM document_extractions
               WHERE content_id=? AND parser_name=? AND parser_version=?
                 AND ocr_engine=? AND ocr_engine_version=? AND options_json=?""",
            (
                int(content["content_id"]),
                DOCUMENT_PARSER_NAME,
                DOCUMENT_PARSER_VERSION,
                engine.name,
                engine_version,
                options_json,
            ),
        ).fetchone()
        reused = existing is not None
        if reused:
            extraction_id = int(existing["extraction_id"])
        else:
            started_at = now_utc()
            extracted = None
            error: dict[str, Any] = {}
            try:
                if content["media_type"] == PDF:
                    extracted = extract_pdf(body, ocr_engine=engine)
                else:
                    extracted = extract_powerpoint(
                        body, media_type=content["media_type"], ocr_engine=engine
                    )
            except Exception as exc:  # recorded failure, never partial success
                error = {"type": type(exc).__name__, "message": str(exc)}
            completed_at = now_utc()
            persisted = persist_extraction(
                conn,
                content_id=int(content["content_id"]),
                extracted=extracted,
                ocr_engine=engine.name,
                ocr_engine_version=engine_version,
                options=options,
                started_at_utc=started_at,
                completed_at_utc=completed_at,
                error=error,
            )
            extraction_id = persisted.extraction_id
        document = extraction_payload(conn, extraction_id)
        artifact = conn.execute(
            """SELECT artifact.artifact_id, artifact.artifact_type,
                      artifact.title_raw, artifact.publisher, artifact.event_date,
                      artifact.period_end, artifact.preferred_content_id,
                      content.media_type, content.content_sha256, content.byte_size,
                      content.is_official
               FROM document_artifacts AS artifact
               JOIN artifact_contents AS content ON content.content_id=?
               WHERE artifact.artifact_id=?""",
            (int(content["content_id"]), artifact_id),
        ).fetchone()
        payload = {
            "schema_version": EXTRACTION_REPORT_SCHEMA_VERSION,
            "artifact": {
                "artifact_id": artifact_id,
                "artifact_type": artifact["artifact_type"],
                "title_raw": artifact["title_raw"],
                "publisher": artifact["publisher"],
                "event_date": artifact["event_date"],
                "period_end": artifact["period_end"],
                "preferred_content_id": artifact["preferred_content_id"],
            },
            "content": {
                "content_id": int(content["content_id"]),
                "media_type": artifact["media_type"],
                "content_sha256": artifact["content_sha256"],
                "byte_size": artifact["byte_size"],
                "is_official": bool(artifact["is_official"]),
            },
            "reused": reused,
            "extraction": document,
        }
        output_path = output or _default_output_path(
            artifact_id, int(content["content_id"])
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        payload["output"] = str(output_path.resolve())
        return payload
    finally:
        conn.close()
