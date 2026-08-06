"""D05 offline official-transcript ingestion from persisted D03 pages."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from ..config import DB_PATH, DOCUMENT_TRANSCRIPT_EXPORTS
from ..db.database import connect, init_db
from ..db.document_extraction_repo import extraction_payload
from ..db.repo import utc_now
from ..db.transcript_repo import persist_official_transcript, transcript_payload
from ..documents.transcript import (
    parse_official_transcript,
)


OFFICIAL_TRANSCRIPT_REPORT_SCHEMA_VERSION = "1.0.0"


def _load_extraction(
    conn: Any, *, artifact_id: int, extraction_id: int | None
) -> Any:
    parameters: tuple[Any, ...]
    extraction_filter = ""
    if extraction_id is None:
        parameters = (artifact_id,)
    else:
        extraction_filter = "AND extraction.extraction_id=?"
        parameters = (artifact_id, extraction_id)
    row = conn.execute(
        f"""SELECT extraction.*, content.content_sha256, content.byte_size,
                   content.media_type, content.blob_id, content.is_official,
                   content.content_origin, blob.local_path, artifact.title_raw,
                   artifact.publisher, artifact.event_date, artifact.period_end,
                   artifact.preferred_content_id
            FROM document_artifacts AS artifact
            JOIN artifact_contents AS content
              ON content.artifact_id=artifact.artifact_id
            JOIN document_extractions AS extraction
              ON extraction.content_id=content.content_id
            JOIN response_blobs AS blob USING (blob_id)
            WHERE artifact.artifact_id=? AND artifact.artifact_type='transcript'
              AND content.is_official=1
              AND content.content_origin='publisher_original'
              AND extraction.extraction_status='ok'
              {extraction_filter}
            ORDER BY (content.content_id=artifact.preferred_content_id) DESC,
                     extraction.extraction_id DESC
            LIMIT 1""",
        parameters,
    ).fetchone()
    if row is None:
        raise ValueError(
            f"Artifact {artifact_id} has no matching usable official transcript extraction"
        )
    if row["media_type"] != "application/pdf":
        raise ValueError("D05 official transcript ingestion currently requires PDF content")
    return row


def _verify_content_bytes(row: Any) -> None:
    path = Path(row["local_path"])
    if not path.is_file():
        raise RuntimeError(f"Immutable transcript bytes are missing: {path}")
    body = path.read_bytes()
    if len(body) != int(row["byte_size"]):
        raise RuntimeError("Immutable transcript content byte size changed")
    if hashlib.sha256(body).hexdigest() != row["content_sha256"]:
        raise RuntimeError("Immutable transcript content hash changed")


def _default_output_path(artifact_id: int, content_id: int) -> Path:
    return DOCUMENT_TRANSCRIPT_EXPORTS / str(artifact_id) / f"content-{content_id}.json"


def ingest_official_transcript(
    *,
    artifact_id: int,
    extraction_id: int | None = None,
    db_path: Path = DB_PATH,
    output: Path | None = None,
    now_utc: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    """Ingest official text only; this command never accesses a recording/network."""

    conn = connect(db_path)
    try:
        init_db(conn)
        extraction_row = _load_extraction(
            conn, artifact_id=artifact_id, extraction_id=extraction_id
        )
        _verify_content_bytes(extraction_row)
        extraction = extraction_payload(conn, int(extraction_row["extraction_id"]))
        parsed = parse_official_transcript(extraction)
        persisted = persist_official_transcript(
            conn,
            artifact_id=artifact_id,
            content_id=int(extraction_row["content_id"]),
            extraction_id=int(extraction_row["extraction_id"]),
            parsed=parsed,
            created_at_utc=now_utc(),
        )
        reused = persisted.reused
        transcript_id = persisted.transcript_id
        transcript = transcript_payload(conn, transcript_id)
        fallback = conn.execute(
            """SELECT recording_transcription_needed, preferred_transcript_id,
                      preferred_origin
               FROM official_transcript_fallback_status WHERE artifact_id=?""",
            (artifact_id,),
        ).fetchone()
        payload = {
            "schema_version": OFFICIAL_TRANSCRIPT_REPORT_SCHEMA_VERSION,
            "artifact": {
                "artifact_id": artifact_id,
                "artifact_type": "transcript",
                "title_raw": extraction_row["title_raw"],
                "publisher": extraction_row["publisher"],
                "event_date": extraction_row["event_date"],
                "period_end": extraction_row["period_end"],
                "preferred_content_id": extraction_row["preferred_content_id"],
            },
            "content": {
                "content_id": int(extraction_row["content_id"]),
                "blob_id": int(extraction_row["blob_id"]),
                "media_type": extraction_row["media_type"],
                "content_sha256": extraction_row["content_sha256"],
                "byte_size": int(extraction_row["byte_size"]),
                "is_official": bool(extraction_row["is_official"]),
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
            "transcript": transcript,
            "fallback": {
                "recording_transcription_needed": bool(
                    fallback["recording_transcription_needed"]
                ),
                "preferred_transcript_id": fallback["preferred_transcript_id"],
                "preferred_origin": fallback["preferred_origin"],
            },
        }
        # Re-read the mutable artifact pointer after preference evaluation.
        preferred_content = conn.execute(
            """SELECT preferred_content_id, transcript_preference_status
               FROM document_artifacts WHERE artifact_id=?""",
            (artifact_id,),
        ).fetchone()
        payload["artifact"]["preferred_content_id"] = preferred_content[
            "preferred_content_id"
        ]
        payload["artifact"]["transcript_preference_status"] = preferred_content[
            "transcript_preference_status"
        ]
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
