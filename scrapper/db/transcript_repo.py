"""Persistence, validation export and official-first preference for D05."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..documents.transcript import (
    OFFICIAL_TRANSCRIPT_OPTIONS,
    OFFICIAL_TRANSCRIPT_PARSER_NAME,
    OFFICIAL_TRANSCRIPT_PARSER_VERSION,
    OfficialTranscript,
    canonical_json,
)


@dataclass(frozen=True)
class PersistedTranscript:
    transcript_id: int
    artifact_id: int
    content_id: int
    reused: bool
    validation_status: str
    segment_count: int
    is_preferred: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "transcript_id": self.transcript_id,
            "artifact_id": self.artifact_id,
            "content_id": self.content_id,
            "reused": self.reused,
            "validation_status": self.validation_status,
            "segment_count": self.segment_count,
            "is_preferred": self.is_preferred,
        }


def _existing(
    conn: sqlite3.Connection, *, content_id: int, options_json: str
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM transcripts
           WHERE content_id=? AND origin='official' AND engine=?
             AND model_version=? AND options_json=?""",
        (
            content_id,
            OFFICIAL_TRANSCRIPT_PARSER_NAME,
            OFFICIAL_TRANSCRIPT_PARSER_VERSION,
            options_json,
        ),
    ).fetchone()


def _result(
    conn: sqlite3.Connection, row: sqlite3.Row, *, reused: bool
) -> PersistedTranscript:
    preferred = conn.execute(
        """SELECT transcript_id FROM preferred_transcripts
           WHERE artifact_id=?""",
        (row["artifact_id"],),
    ).fetchone()
    return PersistedTranscript(
        transcript_id=int(row["transcript_id"]),
        artifact_id=int(row["artifact_id"]),
        content_id=int(row["content_id"]),
        reused=reused,
        validation_status=row["validation_status"],
        segment_count=int(row["segment_count"]),
        is_preferred=bool(
            preferred and int(preferred["transcript_id"]) == int(row["transcript_id"])
        ),
    )


def persist_official_transcript(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    content_id: int,
    extraction_id: int,
    parsed: OfficialTranscript,
    created_at_utc: str,
) -> PersistedTranscript:
    options_json = canonical_json(OFFICIAL_TRANSCRIPT_OPTIONS)
    existing = _existing(conn, content_id=content_id, options_json=options_json)
    if existing is not None:
        if existing["validation_status"] == "accepted":
            with conn:
                conn.execute(
                    """INSERT INTO preferred_transcripts
                       (artifact_id, transcript_id, updated_at_utc)
                       VALUES (?, ?, ?)
                       ON CONFLICT(artifact_id) DO UPDATE SET
                         transcript_id=excluded.transcript_id,
                         updated_at_utc=excluded.updated_at_utc""",
                    (artifact_id, int(existing["transcript_id"]), created_at_utc),
                )
                conn.execute(
                    """UPDATE document_artifacts
                       SET preferred_content_id=?, acquisition_status='validated',
                           transcript_preference_status='official_preferred'
                       WHERE artifact_id=?""",
                    (content_id, artifact_id),
                )
        return _result(conn, existing, reused=True)
    page_start = (
        min(segment.page_start for segment in parsed.segments)
        if parsed.segments
        else parsed.document_marker_page or 1
    )
    page_end = (
        max(segment.page_end for segment in parsed.segments)
        if parsed.segments
        else page_start
    )
    prepared_count = sum(
        segment.section_kind == "prepared_remarks" for segment in parsed.segments
    )
    qa_count = sum(segment.section_kind == "qa" for segment in parsed.segments)
    unknown_count = sum(
        segment.section_kind == "unknown" for segment in parsed.segments
    )
    with conn:
        cursor = conn.execute(
            """INSERT INTO transcripts
               (artifact_id, content_id, extraction_id, origin,
                source_media_content_id, language, engine, model_version,
                options_json, document_marker_raw, document_marker_page,
                created_at_utc, validation_status, segment_count,
                page_start, page_end, prepared_segment_count, qa_segment_count,
                unknown_segment_count, error_json)
               VALUES (?, ?, ?, 'official', NULL, 'und', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')""",
            (
                artifact_id,
                content_id,
                extraction_id,
                OFFICIAL_TRANSCRIPT_PARSER_NAME,
                OFFICIAL_TRANSCRIPT_PARSER_VERSION,
                options_json,
                parsed.document_marker_raw,
                parsed.document_marker_page,
                created_at_utc,
                parsed.status,
                len(parsed.segments),
                page_start,
                page_end,
                prepared_count,
                qa_count,
                unknown_count,
            ),
        )
        transcript_id = int(cursor.lastrowid)
        for boundary in parsed.boundaries:
            conn.execute(
                """INSERT INTO transcript_section_boundaries
                   (transcript_id, boundary_position, section_kind, page_number,
                    element_position, start_ms, label_raw, text_origin, bbox_json)
                   VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
                (
                    transcript_id,
                    boundary.position,
                    boundary.section_kind,
                    boundary.page_number,
                    boundary.element_position,
                    boundary.label_raw,
                    boundary.text_origin,
                    canonical_json(boundary.bbox) if boundary.bbox is not None else None,
                ),
            )
        for segment in parsed.segments:
            conn.execute(
                """INSERT INTO transcript_segments
                   (transcript_id, segment_position, page_start, page_end,
                    element_start, element_end, start_ms, end_ms, speaker_raw,
                    speaker_normalized, text_raw, confidence, section_kind,
                    text_origin, source_fragments_json)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, ?, ?, ?)""",
                (
                    transcript_id,
                    segment.position,
                    segment.page_start,
                    segment.page_end,
                    segment.element_start,
                    segment.element_end,
                    segment.speaker_raw,
                    segment.speaker_normalized,
                    segment.text_raw,
                    segment.section_kind,
                    segment.text_origin,
                    canonical_json(
                        [fragment.to_dict() for fragment in segment.fragments]
                    ),
                ),
            )
        for validation in parsed.validations:
            conn.execute(
                """INSERT INTO transcript_validations
                   (transcript_id, rule_code, severity, status, expected_value,
                    actual_value, details_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    transcript_id,
                    validation["rule_code"],
                    validation["severity"],
                    "passed" if validation["passed"] else "failed",
                    canonical_json(validation["expected"]),
                    canonical_json(validation["actual"]),
                    canonical_json(validation["details"]),
                ),
            )
        if parsed.status == "accepted":
            conn.execute(
                """INSERT INTO preferred_transcripts
                   (artifact_id, transcript_id, updated_at_utc)
                   VALUES (?, ?, ?)
                   ON CONFLICT(artifact_id) DO UPDATE SET
                     transcript_id=excluded.transcript_id,
                     updated_at_utc=excluded.updated_at_utc""",
                (artifact_id, transcript_id, created_at_utc),
            )
            conn.execute(
                """UPDATE document_artifacts
                   SET preferred_content_id=?, acquisition_status='validated',
                       transcript_preference_status='official_preferred'
                   WHERE artifact_id=?""",
                (content_id, artifact_id),
            )
        else:
            preferred = conn.execute(
                """SELECT transcript_id FROM preferred_transcripts
                   WHERE artifact_id=?""",
                (artifact_id,),
            ).fetchone()
            if preferred is None:
                conn.execute(
                    """UPDATE document_artifacts
                       SET preferred_content_id=CASE
                             WHEN preferred_content_id=? THEN NULL
                             ELSE preferred_content_id END,
                           transcript_preference_status='official_candidate'
                       WHERE artifact_id=?""",
                    (content_id, artifact_id),
                )
    row = conn.execute(
        "SELECT * FROM transcripts WHERE transcript_id=?", (transcript_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to persist official transcript")
    return _result(conn, row, reused=False)


def transcript_payload(conn: sqlite3.Connection, transcript_id: int) -> dict[str, Any]:
    transcript = conn.execute(
        "SELECT * FROM transcripts WHERE transcript_id=?", (transcript_id,)
    ).fetchone()
    if transcript is None:
        raise ValueError(f"Unknown transcript: {transcript_id}")
    preferred = conn.execute(
        "SELECT transcript_id FROM preferred_transcripts WHERE artifact_id=?",
        (transcript["artifact_id"],),
    ).fetchone()
    boundaries = conn.execute(
        """SELECT * FROM transcript_section_boundaries WHERE transcript_id=?
           ORDER BY boundary_position""",
        (transcript_id,),
    ).fetchall()
    segments = conn.execute(
        """SELECT * FROM transcript_segments WHERE transcript_id=?
           ORDER BY segment_position""",
        (transcript_id,),
    ).fetchall()
    validations = conn.execute(
        """SELECT * FROM transcript_validations WHERE transcript_id=?
           ORDER BY rule_code""",
        (transcript_id,),
    ).fetchall()
    return {
        "transcript_id": transcript_id,
        "artifact_id": transcript["artifact_id"],
        "content_id": transcript["content_id"],
        "extraction_id": transcript["extraction_id"],
        "origin": transcript["origin"],
        "source_media_content_id": transcript["source_media_content_id"],
        "language": transcript["language"],
        "engine": transcript["engine"],
        "model_version": transcript["model_version"],
        "options": json.loads(transcript["options_json"]),
        "document_marker_raw": transcript["document_marker_raw"],
        "document_marker_page": transcript["document_marker_page"],
        "created_at_utc": transcript["created_at_utc"],
        "validation_status": transcript["validation_status"],
        "is_preferred": bool(
            preferred and int(preferred["transcript_id"]) == transcript_id
        ),
        "segment_count": transcript["segment_count"],
        "page_start": transcript["page_start"],
        "page_end": transcript["page_end"],
        "prepared_segment_count": transcript["prepared_segment_count"],
        "qa_segment_count": transcript["qa_segment_count"],
        "unknown_segment_count": transcript["unknown_segment_count"],
        "boundaries": [
            {
                "boundary_position": row["boundary_position"],
                "section_kind": row["section_kind"],
                "page_number": row["page_number"],
                "element_position": row["element_position"],
                "start_ms": row["start_ms"],
                "label_raw": row["label_raw"],
                "text_origin": row["text_origin"],
                "bbox": json.loads(row["bbox_json"]) if row["bbox_json"] else None,
            }
            for row in boundaries
        ],
        "segments": [
            {
                "segment_position": row["segment_position"],
                "page_start": row["page_start"],
                "page_end": row["page_end"],
                "element_start": row["element_start"],
                "element_end": row["element_end"],
                "start_ms": row["start_ms"],
                "end_ms": row["end_ms"],
                "speaker_raw": row["speaker_raw"],
                "speaker_normalized": row["speaker_normalized"],
                "text_raw": row["text_raw"],
                "confidence": row["confidence"],
                "uncertainty": json.loads(row["uncertainty_json"]),
                "section_kind": row["section_kind"],
                "text_origin": row["text_origin"],
                "source_fragments": json.loads(row["source_fragments_json"]),
            }
            for row in segments
        ],
        "validations": [
            {
                "rule_code": row["rule_code"],
                "severity": row["severity"],
                "status": row["status"],
                "expected": json.loads(row["expected_value"]),
                "actual": json.loads(row["actual_value"]),
                "details": json.loads(row["details_json"]),
            }
            for row in validations
        ],
    }
