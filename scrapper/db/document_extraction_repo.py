"""Idempotent persistence and validation for D03 document extraction."""

from __future__ import annotations

import json
import sqlite3
from .document_work_claims import OwnershipLost, assert_extraction_owned, release_extraction
from .repository_backend import execute, generated_id, transaction, commit, is_postgresql, statement
from dataclasses import dataclass
from typing import Any

from ..documents.extraction import (
    DOCUMENT_PARSER_NAME,
    DOCUMENT_PARSER_VERSION,
    ExtractedDocument,
    canonical_json,
)


@dataclass(frozen=True)
class PersistedExtraction:
    extraction_id: int
    content_id: int
    reused: bool
    extraction_status: str
    page_or_slide_count: int
    native_page_count: int
    ocr_page_count: int
    unusable_page_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "extraction_id": self.extraction_id,
            "content_id": self.content_id,
            "reused": self.reused,
            "extraction_status": self.extraction_status,
            "page_or_slide_count": self.page_or_slide_count,
            "native_page_count": self.native_page_count,
            "ocr_page_count": self.ocr_page_count,
            "unusable_page_count": self.unusable_page_count,
        }


def _validation_rows(extracted: ExtractedDocument | None) -> tuple[dict[str, Any], ...]:
    pages = extracted.pages if extracted is not None else ()
    expected_numbers = list(range(1, len(pages) + 1))
    actual_numbers = [page.number for page in pages]
    origins_consistent = all(
        (page.selected_text_origin == "native" and page.native_text_usable and page.selected_text == page.native_text)
        or (page.selected_text_origin == "ocr" and not page.native_text_usable and page.selected_text == page.ocr_text)
        or (page.selected_text_origin == "none" and page.selected_text == "")
        for page in pages
    )
    ocr_fallback_only = all(
        not page.ocr_text or not page.native_text_usable for page in pages
    )
    element_order = all(
        [element.position for element in page.elements]
        == list(range(len(page.elements)))
        for page in pages
    )
    return (
        {
            "rule_code": "document_pages_present",
            "severity": "error",
            "passed": bool(pages),
            "expected": ">=1",
            "actual": str(len(pages)),
            "details": {},
        },
        {
            "rule_code": "document_page_order_contiguous",
            "severity": "error",
            "passed": actual_numbers == expected_numbers,
            "expected": expected_numbers,
            "actual": actual_numbers,
            "details": {},
        },
        {
            "rule_code": "document_selected_text_origin_consistent",
            "severity": "error",
            "passed": origins_consistent,
            "expected": True,
            "actual": origins_consistent,
            "details": {},
        },
        {
            "rule_code": "document_ocr_fallback_only",
            "severity": "error",
            "passed": ocr_fallback_only,
            "expected": True,
            "actual": ocr_fallback_only,
            "details": {
                "ocr_pages": [page.number for page in pages if page.ocr_text]
            },
        },
        {
            "rule_code": "document_element_order_contiguous",
            "severity": "error",
            "passed": element_order,
            "expected": True,
            "actual": element_order,
            "details": {},
        },
    )


def _existing(
    conn: sqlite3.Connection,
    *,
    content_id: int,
    ocr_engine: str,
    ocr_engine_version: str,
    options_json: str,
) -> sqlite3.Row | None:
    return execute(conn,
        """SELECT * FROM document_extractions
           WHERE content_id=? AND parser_name=? AND parser_version=?
             AND ocr_engine=? AND ocr_engine_version=? AND options_json=?""",
        (
            content_id,
            DOCUMENT_PARSER_NAME,
            DOCUMENT_PARSER_VERSION,
            ocr_engine,
            ocr_engine_version,
            options_json,
        ),
    ).fetchone()


def _result(row: sqlite3.Row, *, reused: bool) -> PersistedExtraction:
    return PersistedExtraction(
        extraction_id=int(row["extraction_id"]),
        content_id=int(row["content_id"]),
        reused=reused,
        extraction_status=row["extraction_status"],
        page_or_slide_count=int(row["page_or_slide_count"]),
        native_page_count=int(row["native_page_count"]),
        ocr_page_count=int(row["ocr_page_count"]),
        unusable_page_count=int(row["unusable_page_count"]),
    )


def persist_extraction(
    conn: sqlite3.Connection,
    *,
    content_id: int,
    extracted: ExtractedDocument | None,
    ocr_engine: str,
    ocr_engine_version: str,
    options: dict[str, Any],
    started_at_utc: str,
    completed_at_utc: str,
    error: dict[str, Any] | None = None,
    claim: dict[str, Any] | None = None,
) -> PersistedExtraction:
    """Persist atomically; native callers retain ownership of the outer transaction.

    PostgreSQL serializes each exact extraction identity even for legacy callers
    without a claim, and locks a supplied claim until persistence commits. A
    failure rolls back this operation's savepoint without closing the connection.
    """
    arguments = dict(content_id=content_id, extracted=extracted, ocr_engine=ocr_engine,
        ocr_engine_version=ocr_engine_version, options=options,
        started_at_utc=started_at_utc, completed_at_utc=completed_at_utc,
        error=error, claim=claim)
    if not is_postgresql(conn):
        return _persist_extraction(conn, **arguments)
    with transaction(conn):
        from .repository_backend import lock_write
        lock_write(conn)
        key = canonical_json([content_id, DOCUMENT_PARSER_NAME, DOCUMENT_PARSER_VERSION,
                              ocr_engine, ocr_engine_version, canonical_json(options)])
        conn.execute("""SELECT pg_advisory_xact_lock(hashtextextended(
            current_schema() || ':document-extraction:' || %s, 0))""", (key,))
        return _persist_extraction(conn, **arguments)


def _persist_extraction(
    conn: sqlite3.Connection,
    *,
    content_id: int,
    extracted: ExtractedDocument | None,
    ocr_engine: str,
    ocr_engine_version: str,
    options: dict[str, Any],
    started_at_utc: str,
    completed_at_utc: str,
    error: dict[str, Any] | None = None,
    claim: dict[str, Any] | None = None,
) -> PersistedExtraction:
    options_json = canonical_json(options)
    if claim is not None:
        expected_key = canonical_json([content_id, DOCUMENT_PARSER_NAME,
            DOCUMENT_PARSER_VERSION, ocr_engine, ocr_engine_version, options_json])
        if claim['resource_key'] != expected_key:
            raise OwnershipLost('Extraction claim does not match content/parser/OCR/options')
        if not is_postgresql(conn):
            execute(conn, 'BEGIN IMMEDIATE')
        try:
            assert_extraction_owned(conn, claim)
        except BaseException:
            if not is_postgresql(conn):
                conn.rollback()
            raise
    existing = _existing(
        conn,
        content_id=content_id,
        ocr_engine=ocr_engine,
        ocr_engine_version=ocr_engine_version,
        options_json=options_json,
    )
    if existing is not None:
        if claim is not None:
            release_extraction(conn, claim)
            commit(conn)
        return _result(existing, reused=True)
    content = execute(conn,
        """SELECT content.artifact_id, content.media_type, content.extraction_status,
                  content.is_official
           FROM artifact_contents AS content WHERE content.content_id=?""",
        (content_id,),
    ).fetchone()
    if content is None:
        raise ValueError(f"Unknown artifact content: {content_id}")
    if extracted is not None and extracted.media_type != content["media_type"]:
        raise ValueError("Extracted media type does not match registered content")
    validations = _validation_rows(extracted)
    blocking_failed = any(
        row["severity"] == "error" and not row["passed"] for row in validations
    )
    status = "failed" if extracted is None or blocking_failed else extracted.status
    pages = extracted.pages if extracted is not None else ()
    with transaction(conn):
        cursor = execute(conn,
            """INSERT INTO document_extractions
               (content_id, parser_name, parser_version, ocr_engine,
                ocr_engine_version, options_json, started_at_utc, completed_at_utc,
                extraction_status, page_or_slide_count, native_page_count,
                ocr_page_count, unusable_page_count, error_json, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                content_id,
                DOCUMENT_PARSER_NAME,
                DOCUMENT_PARSER_VERSION,
                ocr_engine,
                ocr_engine_version,
                options_json,
                started_at_utc,
                completed_at_utc,
                status,
                len(pages),
                extracted.native_page_count if extracted else 0,
                extracted.ocr_page_count if extracted else 0,
                extracted.unusable_page_count if extracted else 0,
                canonical_json(error or {}),
                completed_at_utc,
            ),
            returning="extraction_id",
        )
        extraction_id = generated_id(cursor, "extraction_id")
        for page in pages:
            execute(conn,
                """INSERT INTO document_pages
                   (extraction_id, content_id, page_or_slide_number, page_kind,
                    title_raw, native_text, ocr_text, selected_text,
                    selected_text_origin, native_text_usable,
                    native_unusable_reason, extraction_confidence,
                    speaker_notes_available, speaker_notes_text, layout_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    extraction_id,
                    content_id,
                    page.number,
                    page.kind,
                    page.title_raw,
                    page.native_text,
                    page.ocr_text,
                    page.selected_text,
                    page.selected_text_origin,
                    int(page.native_text_usable),
                    page.native_unusable_reason,
                    page.extraction_confidence,
                    int(page.speaker_notes_available),
                    page.speaker_notes_text,
                    canonical_json(page.layout),
                ),
            )
            for element in page.elements:
                execute(conn,
                    """INSERT INTO document_page_elements
                       (extraction_id, page_or_slide_number, element_position,
                        element_kind, text_origin, text_raw, title_raw, rows_json,
                        reference_json, bbox_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        extraction_id,
                        page.number,
                        element.position,
                        element.kind,
                        element.text_origin,
                        element.text_raw,
                        element.title_raw,
                        canonical_json(element.rows) if element.rows is not None else None,
                        canonical_json(element.reference) if element.reference is not None else None,
                        canonical_json(element.bbox) if element.bbox is not None else None,
                    ),
                )
        for validation in validations:
            execute(conn,
                """INSERT INTO document_extraction_validations
                   (extraction_id, rule_code, severity, status, expected_value,
                    actual_value, details_json) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    extraction_id,
                    validation["rule_code"],
                    validation["severity"],
                    "passed" if validation["passed"] else "failed",
                    canonical_json(validation["expected"]),
                    canonical_json(validation["actual"]),
                    canonical_json(validation["details"]),
                ),
            )
        execute(conn,
            "UPDATE artifact_contents SET extraction_status=? WHERE content_id=?",
            (status, content_id),
        )
        if status == "ok":
            preferred = execute(conn,
                statement(conn,
                    "SELECT preferred_content_id FROM document_artifacts WHERE artifact_id=?",
                    "SELECT preferred_content_id FROM document_artifacts WHERE artifact_id=? FOR UPDATE"),
                (int(content["artifact_id"]),),
            ).fetchone()
            if preferred is not None and preferred["preferred_content_id"] is None:
                transcript_status = "not_applicable"
                artifact_type = execute(conn,
                    "SELECT artifact_type FROM document_artifacts WHERE artifact_id=?",
                    (int(content["artifact_id"]),),
                ).fetchone()["artifact_type"]
                if artifact_type == "transcript":
                    transcript_status = "official_preferred" if content["is_official"] else "machine_preferred"
                execute(conn,
                    """UPDATE document_artifacts
                       SET preferred_content_id=?, acquisition_status='validated',
                           transcript_preference_status=? WHERE artifact_id=?""",
                    (content_id, transcript_status, int(content["artifact_id"])),
                )
        if claim is not None:
            release_extraction(conn, claim)
    row = execute(conn,
        "SELECT * FROM document_extractions WHERE extraction_id=?", (extraction_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to persist document extraction")
    return _result(row, reused=False)


def extraction_payload(conn: sqlite3.Connection, extraction_id: int) -> dict[str, Any]:
    extraction = execute(conn,
        "SELECT * FROM document_extractions WHERE extraction_id=?", (extraction_id,)
    ).fetchone()
    if extraction is None:
        raise ValueError(f"Unknown extraction: {extraction_id}")
    pages = execute(conn,
        """SELECT * FROM document_pages WHERE extraction_id=?
           ORDER BY page_or_slide_number""",
        (extraction_id,),
    ).fetchall()
    output_pages: list[dict[str, Any]] = []
    for page in pages:
        elements = execute(conn,
            """SELECT * FROM document_page_elements
               WHERE extraction_id=? AND page_or_slide_number=?
               ORDER BY element_position""",
            (extraction_id, page["page_or_slide_number"]),
        ).fetchall()
        output_pages.append(
            {
                "page_or_slide_number": page["page_or_slide_number"],
                "page_kind": page["page_kind"],
                "title_raw": page["title_raw"],
                "native_text": page["native_text"],
                "ocr_text": page["ocr_text"],
                "selected_text": page["selected_text"],
                "selected_text_origin": page["selected_text_origin"],
                "native_text_usable": bool(page["native_text_usable"]),
                "native_unusable_reason": page["native_unusable_reason"],
                "extraction_confidence": page["extraction_confidence"],
                "speaker_notes_available": bool(page["speaker_notes_available"]),
                "speaker_notes_text": page["speaker_notes_text"],
                "layout": json.loads(page["layout_json"]),
                "elements": [
                    {
                        "element_position": element["element_position"],
                        "element_kind": element["element_kind"],
                        "text_origin": element["text_origin"],
                        "text_raw": element["text_raw"],
                        "title_raw": element["title_raw"],
                        "rows": json.loads(element["rows_json"]) if element["rows_json"] else None,
                        "reference": json.loads(element["reference_json"]) if element["reference_json"] else None,
                        "bbox": json.loads(element["bbox_json"]) if element["bbox_json"] else None,
                    }
                    for element in elements
                ],
            }
        )
    validations = execute(conn,
        """SELECT rule_code, severity, status, expected_value, actual_value,
                  details_json FROM document_extraction_validations
           WHERE extraction_id=? ORDER BY rule_code""",
        (extraction_id,),
    ).fetchall()
    return {
        "extraction_id": extraction_id,
        "content_id": extraction["content_id"],
        "parser_name": extraction["parser_name"],
        "parser_version": extraction["parser_version"],
        "ocr_engine": extraction["ocr_engine"],
        "ocr_engine_version": extraction["ocr_engine_version"],
        "options": json.loads(extraction["options_json"]),
        "started_at_utc": extraction["started_at_utc"],
        "completed_at_utc": extraction["completed_at_utc"],
        "extraction_status": extraction["extraction_status"],
        "page_or_slide_count": extraction["page_or_slide_count"],
        "native_page_count": extraction["native_page_count"],
        "ocr_page_count": extraction["ocr_page_count"],
        "unusable_page_count": extraction["unusable_page_count"],
        "error": json.loads(extraction["error_json"]),
        "pages": output_pages,
        "validations": [
            {
                "rule_code": row["rule_code"],
                "severity": row["severity"],
                "status": row["status"],
                "expected": json.loads(row["expected_value"]) if row["expected_value"] is not None else None,
                "actual": json.loads(row["actual_value"]) if row["actual_value"] is not None else None,
                "details": json.loads(row["details_json"]),
            }
            for row in validations
        ],
    }
