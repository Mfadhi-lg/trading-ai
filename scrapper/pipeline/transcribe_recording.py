"""D07 official-first recording-to-transcript orchestration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from ..config import (
    DB_PATH,
    DOCUMENT_CONTENT_SNAPSHOTS,
    DOCUMENT_MACHINE_TRANSCRIPT_EXPORTS,
)
from ..db.database import connect, init_db
from ..db.machine_transcript_repo import (
    existing_machine_run,
    machine_run_payload,
    persist_machine_transcript,
    persist_machine_transcription_failure,
)
from ..db.repo import utc_now
from ..documents.transcription import Transcriber, validate_machine_transcript


MACHINE_TRANSCRIPT_REPORT_SCHEMA_VERSION = "1.0.0"
_IDENTITY_FIELDS = ("event_date", "period_end", "fiscal_year", "fiscal_quarter")


def _artifact_payload(row: Any) -> dict[str, Any]:
    return {
        "artifact_id": int(row["artifact_id"]),
        "artifact_type": row["artifact_type"],
        "company_id": int(row["company_id"]),
        "title_raw": row["title_raw"],
        "publisher": row["publisher"],
        "event_date": row["event_date"],
        "period_end": row["period_end"],
        "fiscal_year": row["fiscal_year"],
        "fiscal_quarter": row["fiscal_quarter"],
        "preferred_content_id": row["preferred_content_id"],
        "acquisition_status": row["acquisition_status"],
        "transcript_preference_status": row["transcript_preference_status"],
    }


def _load_transcript_artifact(conn: Any, artifact_id: int) -> Any:
    row = conn.execute(
        "SELECT * FROM document_artifacts WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()
    if row is None or row["artifact_type"] != "transcript":
        raise ValueError(f"Artifact {artifact_id} is not a transcript artifact")
    return row


def _accepted_official(conn: Any, artifact_id: int) -> Any:
    return conn.execute(
        """SELECT transcript_id, content_id FROM transcripts
           WHERE artifact_id=? AND origin='official'
             AND validation_status='accepted'
           ORDER BY transcript_id DESC LIMIT 1""",
        (artifact_id,),
    ).fetchone()


def _same_event(transcript: Any, recording: Any) -> bool:
    matches = 0
    for field in _IDENTITY_FIELDS:
        left = transcript[field]
        right = recording[field]
        if left is not None and right is not None:
            if left != right:
                return False
            matches += 1
    return matches > 0


def _load_recording_audio(
    conn: Any,
    *,
    transcript: Any,
    recording_artifact_id: int | None,
) -> Any:
    parameters: list[Any] = [int(transcript["company_id"])]
    recording_filter = ""
    if recording_artifact_id is not None:
        recording_filter = "AND recording.artifact_id=?"
        parameters.append(recording_artifact_id)
    rows = conn.execute(
        f"""SELECT recording.*, normalization.normalization_id,
                   normalization.normalized_duration_ms,
                   source.content_id AS source_recording_content_id,
                   source.content_sha256 AS source_recording_sha256,
                   source.byte_size AS source_recording_byte_size,
                   source_blob.local_path AS source_recording_local_path,
                   media.content_id AS source_media_content_id,
                   media.content_sha256 AS source_media_sha256,
                   media.byte_size AS source_media_byte_size,
                   media.media_type AS source_media_type,
                   media_blob.local_path AS source_media_local_path
            FROM document_artifacts AS recording
            JOIN recording_normalizations AS normalization
              ON normalization.artifact_id=recording.artifact_id
             AND normalization.normalization_status='accepted'
            JOIN artifact_contents AS source
              ON source.content_id=normalization.source_content_id
            JOIN response_blobs AS source_blob ON source_blob.blob_id=source.blob_id
            JOIN artifact_contents AS media
              ON media.content_id=normalization.normalized_content_id
            JOIN response_blobs AS media_blob ON media_blob.blob_id=media.blob_id
            WHERE recording.company_id=? AND recording.artifact_type='recording'
              {recording_filter}
            ORDER BY recording.artifact_id, normalization.normalization_id DESC""",
        tuple(parameters),
    ).fetchall()
    matching = [row for row in rows if _same_event(transcript, row)]
    recording_ids = tuple(dict.fromkeys(int(row["artifact_id"]) for row in matching))
    if not recording_ids:
        detail = (
            f" recording artifact {recording_artifact_id}"
            if recording_artifact_id is not None
            else ""
        )
        raise ValueError(
            f"Transcript artifact {transcript['artifact_id']} has no matching accepted"
            f" D06 audio{detail}"
        )
    if len(recording_ids) > 1:
        raise ValueError(
            "Multiple recording artifacts match transcript event identity: "
            + ", ".join(str(item) for item in recording_ids)
        )
    return next(row for row in matching if int(row["artifact_id"]) == recording_ids[0])


def _verify(path_value: str, *, byte_size: int, sha256: str, label: str) -> Path:
    path = Path(path_value)
    if not path.is_file():
        raise RuntimeError(f"Immutable {label} bytes are missing: {path}")
    body = path.read_bytes()
    if len(body) != byte_size:
        raise RuntimeError(f"Immutable {label} byte size changed")
    if hashlib.sha256(body).hexdigest() != sha256:
        raise RuntimeError(f"Immutable {label} content hash changed")
    return path


def _verify_run_contents(run: dict[str, Any]) -> None:
    for key, label in (
        ("source_media", "normalized audio"),
        ("source_recording", "source recording"),
        ("generated_content", "machine transcript"),
    ):
        content = run.get(key)
        if content is None:
            continue
        _verify(
            content["local_path"],
            byte_size=int(content["byte_size"]),
            sha256=content["content_sha256"],
            label=label,
        )


def _default_output_path(artifact_id: int) -> Path:
    return DOCUMENT_MACHINE_TRANSCRIPT_EXPORTS / str(artifact_id) / "transcript.json"


def _write(payload: dict[str, Any], target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {**payload, "output": str(target.resolve())}


def transcribe_recording(
    *,
    artifact_id: int,
    transcriber: Transcriber,
    recording_artifact_id: int | None = None,
    comparison_requested: bool = False,
    db_path: Path = DB_PATH,
    output: Path | None = None,
    now_utc: Callable[[], str] = utc_now,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
) -> dict[str, Any]:
    """Transcribe accepted D06 audio only when the official-first gate permits it."""

    target = output or _default_output_path(artifact_id)
    conn = connect(db_path)
    try:
        init_db(conn)
        transcript_artifact = _load_transcript_artifact(conn, artifact_id)
        official = _accepted_official(conn, artifact_id)
        if official is not None and not comparison_requested:
            return _write(
                {
                    "schema_version": MACHINE_TRANSCRIPT_REPORT_SCHEMA_VERSION,
                    "artifact": _artifact_payload(transcript_artifact),
                    "reused": False,
                    "gate": {
                        "status": "skipped_official_preferred",
                        "comparison_requested": False,
                        "official_transcript_id": int(official["transcript_id"]),
                    },
                    "transcription": None,
                },
                target,
            )
        source = _load_recording_audio(
            conn,
            transcript=transcript_artifact,
            recording_artifact_id=recording_artifact_id,
        )
        media_path = _verify(
            source["source_media_local_path"],
            byte_size=int(source["source_media_byte_size"]),
            sha256=source["source_media_sha256"],
            label="normalized audio",
        )
        _verify(
            source["source_recording_local_path"],
            byte_size=int(source["source_recording_byte_size"]),
            sha256=source["source_recording_sha256"],
            label="source recording",
        )
        options = transcriber.options()
        existing = existing_machine_run(
            conn,
            source_media_content_id=int(source["source_media_content_id"]),
            engine=transcriber.engine,
            engine_version=transcriber.engine_version,
            model_version=transcriber.model_version,
            options=options,
        )
        reused = existing is not None
        if existing is not None:
            run_id = int(existing["transcription_run_id"])
        else:
            started_at = now_utc()
            try:
                engine_result = transcriber.transcribe(media_path)
                parsed = validate_machine_transcript(
                    engine_result,
                    source_duration_ms=int(source["normalized_duration_ms"]),
                )
            except Exception as exc:
                completed_at = now_utc()
                run_id = persist_machine_transcription_failure(
                    conn,
                    transcript_artifact_id=artifact_id,
                    recording_artifact_id=int(source["artifact_id"]),
                    normalization_id=int(source["normalization_id"]),
                    source_media_content_id=int(source["source_media_content_id"]),
                    source_media_sha256=source["source_media_sha256"],
                    source_media_duration_ms=int(source["normalized_duration_ms"]),
                    source_recording_content_id=int(
                        source["source_recording_content_id"]
                    ),
                    source_recording_sha256=source["source_recording_sha256"],
                    engine=transcriber.engine,
                    engine_version=transcriber.engine_version,
                    model_version=transcriber.model_version,
                    options=options,
                    comparison_requested=comparison_requested,
                    started_at_utc=started_at,
                    completed_at_utc=completed_at,
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
            else:
                completed_at = now_utc()
                run_id = persist_machine_transcript(
                    conn,
                    transcript_artifact_id=artifact_id,
                    recording_artifact_id=int(source["artifact_id"]),
                    normalization_id=int(source["normalization_id"]),
                    source_media_content_id=int(source["source_media_content_id"]),
                    source_media_sha256=source["source_media_sha256"],
                    source_recording_content_id=int(
                        source["source_recording_content_id"]
                    ),
                    source_recording_sha256=source["source_recording_sha256"],
                    source_duration_ms=int(source["normalized_duration_ms"]),
                    engine=transcriber.engine,
                    engine_version=transcriber.engine_version,
                    model_version=transcriber.model_version,
                    options=options,
                    comparison_requested=comparison_requested,
                    parsed=parsed,
                    started_at_utc=started_at,
                    completed_at_utc=completed_at,
                    snapshot_root=snapshot_root,
                )
        run = machine_run_payload(conn, run_id)
        _verify_run_contents(run)
        current_artifact = _load_transcript_artifact(conn, artifact_id)
        return _write(
            {
                "schema_version": MACHINE_TRANSCRIPT_REPORT_SCHEMA_VERSION,
                "artifact": _artifact_payload(current_artifact),
                "reused": reused,
                "gate": {
                    "status": (
                        "human_comparison_allowed"
                        if comparison_requested
                        else "no_usable_official"
                    ),
                    "comparison_requested": comparison_requested,
                    "official_transcript_id": (
                        int(official["transcript_id"]) if official is not None else None
                    ),
                },
                "transcription": run,
            },
            target,
        )
    finally:
        conn.close()
