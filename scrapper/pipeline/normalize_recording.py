"""D06 offline normalization of validated official recordings to PCM WAV."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from ..config import (
    DB_PATH,
    DOCUMENT_CONTENT_SNAPSHOTS,
    DOCUMENT_MEDIA_PROCESS_TIMEOUT_SECONDS,
    DOCUMENT_NORMALIZED_AUDIO_MAX_BYTES,
    DOCUMENT_RECORDING_EXPORTS,
)
from ..db.database import connect, init_db
from ..db.recording_repo import (
    persist_recording_normalization,
    recording_normalization_payload,
)
from ..db.repo import utc_now
from ..documents.downloader import AUDIO_VIDEO_MEDIA_TYPES
from ..documents.media import (
    FfmpegMediaTool,
    MediaTool,
    MediaValidation,
    canonical_json,
    validate_normalized_audio,
)


RECORDING_REPORT_SCHEMA_VERSION = "1.0.0"


def _load_source_content(
    conn: Any, *, artifact_id: int, content_id: int | None
) -> Any:
    parameters: tuple[Any, ...]
    content_filter = ""
    if content_id is None:
        parameters = (artifact_id,)
    else:
        content_filter = "AND content.content_id=?"
        parameters = (artifact_id, content_id)
    row = conn.execute(
        f"""SELECT artifact.artifact_id, artifact.artifact_type,
                   artifact.title_raw, artifact.publisher, artifact.event_date,
                   artifact.period_end, artifact.preferred_content_id,
                   artifact.transcript_preference_status,
                   content.*, blob.local_path
            FROM document_artifacts AS artifact
            JOIN artifact_contents AS content
              ON content.artifact_id=artifact.artifact_id
            JOIN response_blobs AS blob USING (blob_id)
            WHERE artifact.artifact_id=? AND artifact.artifact_type='recording'
              {content_filter}
              AND content.is_official=1
              AND content.content_origin='publisher_original'
              AND EXISTS (
                SELECT 1
                FROM artifact_download_attempts AS attempt
                JOIN artifact_download_attempt_sources AS link
                  USING (download_attempt_id)
                JOIN artifact_sources AS source USING (artifact_source_id)
                WHERE attempt.content_id=content.content_id
                  AND attempt.result_status='validated'
                  AND source.is_primary_source=1
              )
            ORDER BY (content.content_id=artifact.preferred_content_id) DESC,
                     content.content_id DESC
            LIMIT 1""",
        parameters,
    ).fetchone()
    if row is None:
        raise ValueError(
            f"Artifact {artifact_id} has no validated official primary-source recording"
        )
    if row["media_type"] not in AUDIO_VIDEO_MEDIA_TYPES:
        raise ValueError(
            f"Content {row['content_id']} is not supported recording media: "
            f"{row['media_type']}"
        )
    return row


def _verify_content_bytes(content: Any) -> Path:
    path = Path(content["local_path"])
    if not path.is_file():
        raise RuntimeError(f"Immutable recording bytes are missing: {path}")
    if path.stat().st_size != int(content["byte_size"]):
        raise RuntimeError("Immutable recording content byte size changed")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != content["content_sha256"]:
        raise RuntimeError("Immutable recording content hash changed")
    return path


def _verify_derived_payload(payload: dict[str, Any]) -> None:
    derived = payload["normalized_audio"]
    if derived is None:
        return
    path = Path(derived["local_path"])
    if not path.is_file():
        raise RuntimeError(f"Normalized audio bytes are missing: {path}")
    body = path.read_bytes()
    if len(body) != int(derived["byte_size"]):
        raise RuntimeError("Normalized audio byte size changed")
    if hashlib.sha256(body).hexdigest() != derived["content_sha256"]:
        raise RuntimeError("Normalized audio hash changed")


def _default_output_path(artifact_id: int, source_content_id: int) -> Path:
    return (
        DOCUMENT_RECORDING_EXPORTS
        / str(artifact_id)
        / f"source-content-{source_content_id}.json"
    )


def normalize_recording(
    *,
    artifact_id: int,
    content_id: int | None = None,
    db_path: Path = DB_PATH,
    output: Path | None = None,
    media_tool: MediaTool | None = None,
    now_utc: Callable[[], str] = utc_now,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
    max_output_bytes: int = DOCUMENT_NORMALIZED_AUDIO_MAX_BYTES,
) -> dict[str, Any]:
    """Resolve one D02 recording and retain a deterministic linked WAV derivative."""

    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    tool = media_tool or FfmpegMediaTool(
        timeout_seconds=DOCUMENT_MEDIA_PROCESS_TIMEOUT_SECONDS,
        max_output_bytes=max_output_bytes,
    )
    options = tool.options()
    options_json = canonical_json(options)
    conn = connect(db_path)
    try:
        init_db(conn)
        source = _load_source_content(
            conn, artifact_id=artifact_id, content_id=content_id
        )
        source_path = _verify_content_bytes(source)
        existing = conn.execute(
            """SELECT normalization_id FROM recording_normalizations
               WHERE source_content_id=? AND probe_engine=?
                 AND probe_engine_version=? AND normalizer_engine=?
                 AND normalizer_engine_version=? AND options_json=?""",
            (
                int(source["content_id"]),
                tool.probe_name,
                tool.probe_version,
                tool.name,
                tool.version,
                options_json,
            ),
        ).fetchone()
        reused = existing is not None
        if reused:
            normalization_id = int(existing["normalization_id"])
        else:
            started_at = now_utc()
            source_probe = None
            normalized_probe = None
            normalized_body = None
            validations: list[MediaValidation] = [
                MediaValidation(
                    "recording_primary_source_provenance",
                    True,
                    "validated D02 download with primary source",
                    "validated",
                    {},
                )
            ]
            error: dict[str, Any] = {}
            try:
                source_probe = tool.probe(source_path)
                with tempfile.TemporaryDirectory(prefix="scrapper-d06-") as temporary:
                    destination = Path(temporary) / "normalized.wav"
                    tool.normalize_audio(source_path, destination)
                    if destination.stat().st_size > max_output_bytes:
                        raise ValueError(
                            "Normalized audio exceeds configured byte limit: "
                            f"{destination.stat().st_size} > {max_output_bytes}"
                        )
                    normalized_body = destination.read_bytes()
                    normalized_probe = tool.probe(destination)
                normalized_sha256 = hashlib.sha256(normalized_body).hexdigest()
                validations.extend(
                    validate_normalized_audio(
                        source_probe,
                        normalized_probe,
                        normalized_body=normalized_body,
                        source_sha256=source["content_sha256"],
                        normalized_sha256=normalized_sha256,
                    )
                )
            except Exception as exc:  # retain a complete explicit failure
                error = {"type": type(exc).__name__, "message": str(exc)}
                validations.append(
                    MediaValidation(
                        "recording_media_processing_completed",
                        False,
                        "probe and normalize without error",
                        type(exc).__name__,
                        {"message": str(exc)},
                    )
                )
            completed_at = now_utc()
            persisted = persist_recording_normalization(
                conn,
                artifact_id=artifact_id,
                source_content_id=int(source["content_id"]),
                source_probe=source_probe,
                normalized_probe=normalized_probe,
                normalized_body=normalized_body,
                validations=validations,
                probe_engine=tool.probe_name,
                probe_engine_version=tool.probe_version,
                normalizer_engine=tool.name,
                normalizer_engine_version=tool.version,
                options=options,
                started_at_utc=started_at,
                completed_at_utc=completed_at,
                error=error,
                snapshot_root=snapshot_root,
            )
            normalization_id = persisted.normalization_id
        normalization = recording_normalization_payload(conn, normalization_id)
        _verify_derived_payload(normalization)
        artifact = conn.execute(
            """SELECT artifact_id, artifact_type, title_raw, publisher,
                      event_date, period_end, preferred_content_id,
                      acquisition_status, transcript_preference_status
               FROM document_artifacts WHERE artifact_id=?""",
            (artifact_id,),
        ).fetchone()
        payload = {
            "schema_version": RECORDING_REPORT_SCHEMA_VERSION,
            "artifact": {
                "artifact_id": artifact_id,
                "artifact_type": artifact["artifact_type"],
                "title_raw": artifact["title_raw"],
                "publisher": artifact["publisher"],
                "event_date": artifact["event_date"],
                "period_end": artifact["period_end"],
                "preferred_content_id": artifact["preferred_content_id"],
                "acquisition_status": artifact["acquisition_status"],
                "transcript_preference_status": artifact[
                    "transcript_preference_status"
                ],
            },
            "reused": reused,
            "normalization": normalization,
        }
        target = output or _default_output_path(
            artifact_id, int(source["content_id"])
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return {
            **payload,
            "output": str(target.resolve()),
        }
    finally:
        conn.close()
