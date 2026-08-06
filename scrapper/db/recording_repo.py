"""Persistence and export helpers for D06 recording audio normalization."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..config import DOCUMENT_CONTENT_SNAPSHOTS
from ..documents.media import MediaProbe, MediaValidation, NORMALIZED_AUDIO_MEDIA_TYPE
from .document_artifact_repo import register_artifact_content
from .document_download_repo import store_response_blob


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class PersistedRecordingNormalization:
    normalization_id: int
    normalized_content_id: int | None
    normalization_status: str


def persist_recording_normalization(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    source_content_id: int,
    source_probe: MediaProbe | None,
    normalized_probe: MediaProbe | None,
    normalized_body: bytes | None,
    validations: Sequence[MediaValidation],
    probe_engine: str,
    probe_engine_version: str,
    normalizer_engine: str,
    normalizer_engine_version: str,
    options: dict[str, Any],
    started_at_utc: str,
    completed_at_utc: str,
    error: dict[str, Any],
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
) -> PersistedRecordingNormalization:
    """Store a complete success or an explicit failed attempt atomically."""

    options_json = _json(options)
    existing = conn.execute(
        """SELECT normalization_id, normalized_content_id, normalization_status
           FROM recording_normalizations
           WHERE source_content_id=? AND probe_engine=? AND probe_engine_version=?
             AND normalizer_engine=? AND normalizer_engine_version=?
             AND options_json=?""",
        (
            source_content_id,
            probe_engine,
            probe_engine_version,
            normalizer_engine,
            normalizer_engine_version,
            options_json,
        ),
    ).fetchone()
    if existing is not None:
        return PersistedRecordingNormalization(
            normalization_id=int(existing["normalization_id"]),
            normalized_content_id=(
                int(existing["normalized_content_id"])
                if existing["normalized_content_id"] is not None
                else None
            ),
            normalization_status=existing["normalization_status"],
        )

    passed = not error and validations and all(row.passed for row in validations)
    status = "accepted" if passed else "failed"
    normalized_content_id: int | None = None
    with conn:
        if status == "accepted":
            if normalized_body is None or normalized_probe is None:
                raise ValueError("Accepted recording normalization requires derived audio")
            blob_id, _ = store_response_blob(
                conn,
                body=normalized_body,
                media_type=NORMALIZED_AUDIO_MEDIA_TYPE,
                observed_at_utc=completed_at_utc,
                snapshot_root=snapshot_root,
            )
            normalized_content_id = register_artifact_content(
                conn,
                artifact_id=artifact_id,
                blob_id=blob_id,
                media_type=NORMALIZED_AUDIO_MEDIA_TYPE,
                is_official=False,
                content_origin="generated_derivative",
                created_at_utc=completed_at_utc,
            )
            conn.execute(
                """INSERT INTO artifact_content_relationships
                   (artifact_id, source_content_id, derived_content_id,
                    relationship_type, created_at_utc)
                   VALUES (?, ?, ?, 'normalized_audio', ?)""",
                (
                    artifact_id,
                    source_content_id,
                    normalized_content_id,
                    completed_at_utc,
                ),
            )
        source_formats = list(source_probe.container_formats) if source_probe else []
        source_streams = (
            [row.to_dict() for row in source_probe.streams] if source_probe else []
        )
        normalized_formats = (
            list(normalized_probe.container_formats) if normalized_probe else []
        )
        normalized_streams = (
            [row.to_dict() for row in normalized_probe.streams]
            if normalized_probe
            else []
        )
        cursor = conn.execute(
            """INSERT INTO recording_normalizations
               (artifact_id, source_content_id, normalized_content_id,
                resolution_basis, probe_engine, probe_engine_version,
                normalizer_engine, normalizer_engine_version, options_json,
                started_at_utc, completed_at_utc, normalization_status,
                source_duration_ms, source_container_formats_json,
                source_streams_json, normalized_duration_ms,
                normalized_container_formats_json, normalized_streams_json,
                error_json)
               VALUES (?, ?, ?, 'primary_source_validated_download', ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artifact_id,
                source_content_id,
                normalized_content_id,
                probe_engine,
                probe_engine_version,
                normalizer_engine,
                normalizer_engine_version,
                options_json,
                started_at_utc,
                completed_at_utc,
                status,
                source_probe.duration_ms if source_probe else None,
                _json(source_formats),
                _json(source_streams),
                normalized_probe.duration_ms if normalized_probe else None,
                _json(normalized_formats),
                _json(normalized_streams),
                _json(error),
            ),
        )
        normalization_id = int(cursor.lastrowid)
        conn.executemany(
            """INSERT INTO recording_normalization_validations
               (normalization_id, rule_code, severity, status, expected_value,
                actual_value, details_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    normalization_id,
                    row.rule_code,
                    row.severity,
                    "passed" if row.passed else "failed",
                    row.expected_value,
                    row.actual_value,
                    _json(row.details),
                )
                for row in validations
            ],
        )
        if status == "accepted":
            # The source recording remains preferred; normalization is a linked
            # derivative and never replaces the publisher's original bytes.
            conn.execute(
                """UPDATE document_artifacts
                   SET preferred_content_id=?, acquisition_status='validated'
                   WHERE artifact_id=?""",
                (source_content_id, artifact_id),
            )
    return PersistedRecordingNormalization(
        normalization_id=normalization_id,
        normalized_content_id=normalized_content_id,
        normalization_status=status,
    )


def recording_normalization_payload(
    conn: sqlite3.Connection, normalization_id: int
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM recording_normalizations WHERE normalization_id=?",
        (normalization_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown recording normalization: {normalization_id}")
    source = conn.execute(
        """SELECT content.content_id, content.media_type, content.content_sha256,
                  content.byte_size, content.is_official, content.content_origin,
                  blob.local_path
           FROM artifact_contents AS content
           JOIN response_blobs AS blob USING (blob_id)
           WHERE content.content_id=?""",
        (row["source_content_id"],),
    ).fetchone()
    derived = None
    relationship = None
    if row["normalized_content_id"] is not None:
        derived = conn.execute(
            """SELECT content.content_id, content.media_type,
                      content.content_sha256, content.byte_size,
                      content.is_official, content.content_origin, blob.local_path
               FROM artifact_contents AS content
               JOIN response_blobs AS blob USING (blob_id)
               WHERE content.content_id=?""",
            (row["normalized_content_id"],),
        ).fetchone()
        relationship = conn.execute(
            """SELECT * FROM artifact_content_relationships
               WHERE source_content_id=? AND derived_content_id=?""",
            (row["source_content_id"], row["normalized_content_id"]),
        ).fetchone()
    provenance = conn.execute(
        """SELECT DISTINCT attempt.download_attempt_id, source.artifact_source_id,
                  source.discovered_from_source, source.discovery_page_url,
                  source.document_url, source.publisher_claim,
                  source.is_primary_source, attempt.requested_url,
                  attempt.final_url, attempt.fetched_at_utc
           FROM artifact_download_attempts AS attempt
           JOIN artifact_download_attempt_sources AS link USING (download_attempt_id)
           JOIN artifact_sources AS source USING (artifact_source_id)
           WHERE attempt.content_id=? AND attempt.result_status='validated'
           ORDER BY attempt.download_attempt_id, source.artifact_source_id""",
        (row["source_content_id"],),
    ).fetchall()
    validations = conn.execute(
        """SELECT * FROM recording_normalization_validations
           WHERE normalization_id=? ORDER BY rule_code""",
        (normalization_id,),
    ).fetchall()

    def content_payload(content: sqlite3.Row | None) -> dict[str, Any] | None:
        if content is None:
            return None
        return {
            "content_id": int(content["content_id"]),
            "media_type": content["media_type"],
            "content_sha256": content["content_sha256"],
            "byte_size": int(content["byte_size"]),
            "is_official": bool(content["is_official"]),
            "content_origin": content["content_origin"],
            "local_path": content["local_path"],
        }

    return {
        "normalization_id": normalization_id,
        "artifact_id": int(row["artifact_id"]),
        "resolution_basis": row["resolution_basis"],
        "normalization_status": row["normalization_status"],
        "probe_engine": row["probe_engine"],
        "probe_engine_version": row["probe_engine_version"],
        "normalizer_engine": row["normalizer_engine"],
        "normalizer_engine_version": row["normalizer_engine_version"],
        "options": json.loads(row["options_json"]),
        "started_at_utc": row["started_at_utc"],
        "completed_at_utc": row["completed_at_utc"],
        "source": {
            **(content_payload(source) or {}),
            "duration_ms": row["source_duration_ms"],
            "container_formats": json.loads(row["source_container_formats_json"]),
            "streams": json.loads(row["source_streams_json"]),
            "resolution_paths": [
                {
                    "download_attempt_id": int(item["download_attempt_id"]),
                    "artifact_source_id": int(item["artifact_source_id"]),
                    "discovered_from_source": item["discovered_from_source"],
                    "discovery_page_url": item["discovery_page_url"],
                    "document_url": item["document_url"],
                    "publisher_claim": item["publisher_claim"],
                    "is_primary_source": bool(item["is_primary_source"]),
                    "requested_url": item["requested_url"],
                    "final_url": item["final_url"],
                    "fetched_at_utc": item["fetched_at_utc"],
                }
                for item in provenance
            ],
        },
        "normalized_audio": (
            {
                **(content_payload(derived) or {}),
                "duration_ms": row["normalized_duration_ms"],
                "container_formats": json.loads(
                    row["normalized_container_formats_json"]
                ),
                "streams": json.loads(row["normalized_streams_json"]),
            }
            if derived is not None
            else None
        ),
        "relationship": (
            {
                "relationship_id": int(relationship["relationship_id"]),
                "source_content_id": int(relationship["source_content_id"]),
                "derived_content_id": int(relationship["derived_content_id"]),
                "relationship_type": relationship["relationship_type"],
                "created_at_utc": relationship["created_at_utc"],
            }
            if relationship is not None
            else None
        ),
        "validations": [
            {
                "rule_code": item["rule_code"],
                "severity": item["severity"],
                "status": item["status"],
                "expected_value": item["expected_value"],
                "actual_value": item["actual_value"],
                "details": json.loads(item["details_json"]),
            }
            for item in validations
        ],
        "error": json.loads(row["error_json"]),
    }
