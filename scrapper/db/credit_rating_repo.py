"""Idempotent D04 credit-rating persistence and page-evidence export."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..documents.credit_rating import (
    CREDIT_RATING_OPTIONS,
    CREDIT_RATING_PARSER_NAME,
    CREDIT_RATING_PARSER_VERSION,
    CreditRatingNormalization,
    RatingEvidence,
    canonical_json,
)


@dataclass(frozen=True)
class PersistedCreditRating:
    normalization_id: int
    artifact_id: int
    extraction_id: int
    reused: bool
    normalization_status: str
    accepted_action_count: int
    review_action_count: int
    conflict_action_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "normalization_id": self.normalization_id,
            "artifact_id": self.artifact_id,
            "extraction_id": self.extraction_id,
            "reused": self.reused,
            "normalization_status": self.normalization_status,
            "accepted_action_count": self.accepted_action_count,
            "review_action_count": self.review_action_count,
            "conflict_action_count": self.conflict_action_count,
        }


def _existing(
    conn: sqlite3.Connection, *, extraction_id: int, options_json: str
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM credit_rating_normalizations
           WHERE extraction_id=? AND parser_name=? AND parser_version=?
             AND options_json=?""",
        (
            extraction_id,
            CREDIT_RATING_PARSER_NAME,
            CREDIT_RATING_PARSER_VERSION,
            options_json,
        ),
    ).fetchone()


def _result(row: sqlite3.Row, *, reused: bool) -> PersistedCreditRating:
    return PersistedCreditRating(
        normalization_id=int(row["normalization_id"]),
        artifact_id=int(row["artifact_id"]),
        extraction_id=int(row["extraction_id"]),
        reused=reused,
        normalization_status=row["normalization_status"],
        accepted_action_count=int(row["accepted_action_count"]),
        review_action_count=int(row["review_action_count"]),
        conflict_action_count=int(row["conflict_action_count"]),
    )


def _insert_evidence(
    conn: sqlite3.Connection,
    *,
    normalization_id: int,
    extraction_id: int,
    evidence: RatingEvidence,
    rating_action_id: int | None = None,
    source_table_id: int | None = None,
) -> None:
    conn.execute(
        """INSERT INTO credit_rating_field_evidence
           (normalization_id, rating_action_id, extraction_id, source_table_id,
            field_code, page_number, element_position, source_row_position,
            source_column_position, source_value_raw, text_origin,
            evidence_kind, bbox_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            normalization_id,
            rating_action_id,
            extraction_id,
            source_table_id,
            evidence.field_code,
            evidence.page_number,
            evidence.element_position,
            evidence.source_row_position,
            evidence.source_column_position,
            evidence.source_value_raw,
            evidence.text_origin,
            evidence.evidence_kind,
            canonical_json(evidence.bbox) if evidence.bbox is not None else None,
        ),
    )


def persist_credit_rating(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    content_id: int,
    extraction_id: int,
    normalized: CreditRatingNormalization,
    started_at_utc: str,
    completed_at_utc: str,
) -> PersistedCreditRating:
    options_json = canonical_json(CREDIT_RATING_OPTIONS)
    existing = _existing(
        conn, extraction_id=extraction_id, options_json=options_json
    )
    if existing is not None:
        return _result(existing, reused=True)

    accepted_count = sum(
        action.canonical_status == "accepted" for action in normalized.actions
    )
    review_count = sum(
        action.canonical_status == "review" for action in normalized.actions
    )
    conflict_count = sum(
        action.canonical_status == "conflict" for action in normalized.actions
    )
    with conn:
        cursor = conn.execute(
            """INSERT INTO credit_rating_normalizations
               (artifact_id, content_id, extraction_id, parser_name,
                parser_version, options_json, document_class, agency_raw,
                agency_code, action_date_raw, action_date,
                normalization_status, accepted_action_count,
                review_action_count, conflict_action_count, started_at_utc,
                completed_at_utc, error_json, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)""",
            (
                artifact_id,
                content_id,
                extraction_id,
                CREDIT_RATING_PARSER_NAME,
                CREDIT_RATING_PARSER_VERSION,
                options_json,
                normalized.document_class,
                normalized.agency_raw,
                normalized.agency_code,
                normalized.action_date_raw,
                normalized.action_date,
                normalized.status,
                accepted_count,
                review_count,
                conflict_count,
                started_at_utc,
                completed_at_utc,
                completed_at_utc,
            ),
        )
        normalization_id = int(cursor.lastrowid)
        table_ids: list[int] = []
        for table in normalized.source_tables:
            table_cursor = conn.execute(
                """INSERT INTO credit_rating_source_tables
                   (normalization_id, extraction_id, page_number,
                    element_position, source_rows_json, header_mapping_json,
                    bbox_json, table_status, review_reason_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    normalization_id,
                    extraction_id,
                    table.page_number,
                    table.element_position,
                    canonical_json(table.rows),
                    canonical_json(table.header_mapping),
                    canonical_json(table.bbox) if table.bbox is not None else None,
                    table.status,
                    canonical_json(table.review_reasons),
                ),
            )
            table_ids.append(int(table_cursor.lastrowid))

        for passage in normalized.passages:
            conn.execute(
                """INSERT INTO credit_rating_passages
                   (normalization_id, extraction_id, page_number,
                    passage_position, element_position, passage_kind, text_raw,
                    text_origin, bbox_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    normalization_id,
                    extraction_id,
                    passage.page_number,
                    passage.passage_position,
                    passage.element_position,
                    passage.passage_kind,
                    passage.text_raw,
                    passage.text_origin,
                    canonical_json(passage.bbox) if passage.bbox is not None else None,
                ),
            )

        for evidence in normalized.document_evidence:
            _insert_evidence(
                conn,
                normalization_id=normalization_id,
                extraction_id=extraction_id,
                evidence=evidence,
            )

        for action in normalized.actions:
            source_table_id = table_ids[action.source_table_index]
            action_cursor = conn.execute(
                """INSERT INTO credit_rating_actions
                   (normalization_id, source_table_id, source_row_position,
                    facility_or_instrument_raw, facility_or_instrument_code,
                    amount_display, amount_decimal, currency, unit_code,
                    scale_multiplier, rating_raw, rating_code, outlook_raw,
                    outlook_code, rating_action_raw, rating_action_code,
                    action_date_raw, action_date, canonical_status, is_canonical,
                    source_row_json, ambiguity_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    normalization_id,
                    source_table_id,
                    action.source_row_position,
                    action.facility_or_instrument_raw,
                    action.facility_or_instrument_code,
                    action.amount_display,
                    action.amount_decimal,
                    action.currency,
                    action.unit_code,
                    action.scale_multiplier,
                    action.rating_raw,
                    action.rating_code,
                    action.outlook_raw,
                    action.outlook_code,
                    action.rating_action_raw,
                    action.rating_action_code,
                    action.action_date_raw,
                    action.action_date,
                    action.canonical_status,
                    int(action.canonical_status == "accepted"),
                    canonical_json(action.source_row),
                    canonical_json(action.ambiguity_reasons),
                ),
            )
            rating_action_id = int(action_cursor.lastrowid)
            for evidence in action.evidence:
                _insert_evidence(
                    conn,
                    normalization_id=normalization_id,
                    extraction_id=extraction_id,
                    evidence=evidence,
                    rating_action_id=rating_action_id,
                    source_table_id=(
                        source_table_id
                        if evidence.evidence_kind == "table_cell"
                        else None
                    ),
                )

        for validation in normalized.validations:
            conn.execute(
                """INSERT INTO credit_rating_normalization_validations
                   (normalization_id, rule_code, severity, status,
                    expected_value, actual_value, details_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    normalization_id,
                    validation["rule_code"],
                    validation["severity"],
                    "passed" if validation["passed"] else "failed",
                    canonical_json(validation["expected"]),
                    canonical_json(validation["actual"]),
                    canonical_json(validation["details"]),
                ),
            )
        if normalized.status == "accepted":
            conn.execute(
                """INSERT INTO preferred_credit_rating_normalizations
                   (artifact_id, normalization_id, updated_at_utc)
                   VALUES (?, ?, ?)
                   ON CONFLICT(artifact_id) DO UPDATE SET
                     normalization_id=excluded.normalization_id,
                     updated_at_utc=excluded.updated_at_utc""",
                (artifact_id, normalization_id, completed_at_utc),
            )
    row = conn.execute(
        "SELECT * FROM credit_rating_normalizations WHERE normalization_id=?",
        (normalization_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to persist credit-rating normalization")
    return _result(row, reused=False)


def _evidence_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "field_code": row["field_code"],
        "page_number": row["page_number"],
        "element_position": row["element_position"],
        "source_row_position": row["source_row_position"],
        "source_column_position": row["source_column_position"],
        "source_value_raw": row["source_value_raw"],
        "text_origin": row["text_origin"],
        "evidence_kind": row["evidence_kind"],
        "bbox": json.loads(row["bbox_json"]) if row["bbox_json"] else None,
    }


def credit_rating_payload(
    conn: sqlite3.Connection, normalization_id: int
) -> dict[str, Any]:
    normalization = conn.execute(
        "SELECT * FROM credit_rating_normalizations WHERE normalization_id=?",
        (normalization_id,),
    ).fetchone()
    if normalization is None:
        raise ValueError(f"Unknown credit-rating normalization: {normalization_id}")
    preferred = conn.execute(
        """SELECT normalization_id FROM preferred_credit_rating_normalizations
           WHERE artifact_id=?""",
        (normalization["artifact_id"],),
    ).fetchone()
    tables = conn.execute(
        """SELECT * FROM credit_rating_source_tables WHERE normalization_id=?
           ORDER BY page_number, element_position""",
        (normalization_id,),
    ).fetchall()
    actions = conn.execute(
        """SELECT action.*, source_table.page_number, source_table.element_position
           FROM credit_rating_actions AS action
           JOIN credit_rating_source_tables AS source_table USING (source_table_id)
           WHERE action.normalization_id=?
           ORDER BY source_table.page_number, source_table.element_position,
                    action.source_row_position""",
        (normalization_id,),
    ).fetchall()
    passages = conn.execute(
        """SELECT * FROM credit_rating_passages WHERE normalization_id=?
           ORDER BY passage_position""",
        (normalization_id,),
    ).fetchall()
    document_evidence = conn.execute(
        """SELECT * FROM credit_rating_field_evidence
           WHERE normalization_id=? AND rating_action_id IS NULL
           ORDER BY page_number, COALESCE(element_position, -1), evidence_id""",
        (normalization_id,),
    ).fetchall()
    action_payloads: list[dict[str, Any]] = []
    for action in actions:
        evidence = conn.execute(
            """SELECT * FROM credit_rating_field_evidence
               WHERE rating_action_id=?
               ORDER BY page_number, COALESCE(element_position, -1),
                        COALESCE(source_column_position, -1), evidence_id""",
            (action["rating_action_id"],),
        ).fetchall()
        action_payloads.append(
            {
                "rating_action_id": action["rating_action_id"],
                "page_number": action["page_number"],
                "element_position": action["element_position"],
                "source_row_position": action["source_row_position"],
                "facility_or_instrument_raw": action["facility_or_instrument_raw"],
                "facility_or_instrument_code": action["facility_or_instrument_code"],
                "amount_display": action["amount_display"],
                "amount_decimal": action["amount_decimal"],
                "currency": action["currency"],
                "unit_code": action["unit_code"],
                "scale_multiplier": action["scale_multiplier"],
                "rating_raw": action["rating_raw"],
                "rating_code": action["rating_code"],
                "outlook_raw": action["outlook_raw"],
                "outlook_code": action["outlook_code"],
                "rating_action_raw": action["rating_action_raw"],
                "rating_action_code": action["rating_action_code"],
                "action_date_raw": action["action_date_raw"],
                "action_date": action["action_date"],
                "canonical_status": action["canonical_status"],
                "is_canonical": bool(action["is_canonical"]),
                "source_row": json.loads(action["source_row_json"]),
                "ambiguity_reasons": json.loads(action["ambiguity_json"]),
                "evidence": [_evidence_payload(row) for row in evidence],
            }
        )
    validations = conn.execute(
        """SELECT * FROM credit_rating_normalization_validations
           WHERE normalization_id=? ORDER BY rule_code""",
        (normalization_id,),
    ).fetchall()
    return {
        "normalization_id": normalization_id,
        "artifact_id": normalization["artifact_id"],
        "content_id": normalization["content_id"],
        "extraction_id": normalization["extraction_id"],
        "parser_name": normalization["parser_name"],
        "parser_version": normalization["parser_version"],
        "options": json.loads(normalization["options_json"]),
        "document_class": normalization["document_class"],
        "agency_raw": normalization["agency_raw"],
        "agency_code": normalization["agency_code"],
        "action_date_raw": normalization["action_date_raw"],
        "action_date": normalization["action_date"],
        "normalization_status": normalization["normalization_status"],
        "is_preferred": bool(
            preferred and preferred["normalization_id"] == normalization_id
        ),
        "accepted_action_count": normalization["accepted_action_count"],
        "review_action_count": normalization["review_action_count"],
        "conflict_action_count": normalization["conflict_action_count"],
        "started_at_utc": normalization["started_at_utc"],
        "completed_at_utc": normalization["completed_at_utc"],
        "document_evidence": [
            _evidence_payload(row) for row in document_evidence
        ],
        "source_tables": [
            {
                "source_table_id": row["source_table_id"],
                "page_number": row["page_number"],
                "element_position": row["element_position"],
                "source_rows": json.loads(row["source_rows_json"]),
                "header_mapping": json.loads(row["header_mapping_json"]),
                "bbox": json.loads(row["bbox_json"]) if row["bbox_json"] else None,
                "table_status": row["table_status"],
                "review_reasons": json.loads(row["review_reason_json"]),
            }
            for row in tables
        ],
        "passages": [
            {
                "page_number": row["page_number"],
                "passage_position": row["passage_position"],
                "element_position": row["element_position"],
                "passage_kind": row["passage_kind"],
                "text_raw": row["text_raw"],
                "text_origin": row["text_origin"],
                "bbox": json.loads(row["bbox_json"]) if row["bbox_json"] else None,
            }
            for row in passages
        ],
        "actions": action_payloads,
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
