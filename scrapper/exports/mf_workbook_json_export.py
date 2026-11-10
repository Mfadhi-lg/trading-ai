"""Deterministic source, normalized, registry, and acceptance workbook export."""

from __future__ import annotations
from ..db.repository_backend import statement

import json
import sqlite3
from pathlib import Path

from .writers import write_json_payload


def build_mf_workbook_export(conn: sqlite3.Connection, import_file_id: int) -> dict:
    imported = conn.execute(
        """SELECT f.*, b.local_path, b.safety_status
           FROM mf_import_files AS f
           JOIN mf_workbook_blobs AS b
             ON b.workbook_blob_id=f.workbook_blob_id
           WHERE f.import_file_id=?""",
        (import_file_id,),
    ).fetchone()
    if imported is None:
        raise ValueError(f"Unknown mutual-fund import: {import_file_id}")
    holdings = conn.execute(
        """SELECT h.*, s.sheet_position, s.sheet_name_raw,
                  d.match_method, d.match_key, d.rule_version AS match_rule_version,
                  d.candidate_security_ids_json, d.evidence_json
           FROM mf_holdings AS h
           JOIN mf_workbook_sheets AS s ON s.sheet_id=h.sheet_id
           JOIN mf_security_match_decisions AS d
             ON d.holding_id=h.holding_id AND d.is_current=1
           WHERE h.import_file_id=?
           ORDER BY s.sheet_position, h.source_table_position, h.source_row""",
        (import_file_id,),
    ).fetchall()
    source_cells = conn.execute(
        """SELECT s.sheet_position, s.sheet_name_raw, h.source_table_position,
                  h.source_table_kind, h.source_row, c.column_position,
                  c.coordinate, c.raw_value, c.display_value, c.formula,
                  c.data_type, c.number_format, c.cell_role,
                  c.normalized_field_code
           FROM mf_holdings AS h
           JOIN mf_workbook_sheets AS s ON s.sheet_id=h.sheet_id
           JOIN mf_workbook_cells AS c
             ON c.sheet_id=h.sheet_id AND c.row_position=h.source_row
           WHERE h.import_file_id=?
           ORDER BY s.sheet_position, h.source_table_position, h.source_row,
                    c.column_position""",
        (import_file_id,),
    ).fetchall()
    validations = conn.execute(
        """SELECT rule_code, severity, passed, details_json
           FROM mf_workbook_validation_results
           WHERE import_file_id=?
           ORDER BY rule_code""",
        (import_file_id,),
    ).fetchall()
    registry = conn.execute(
        statement(conn, """SELECT s.scheme_id, s.publisher, s.scheme_code, s.current_name,
                  c.scheme_classification_id, c.scheme_management_type,
                  c.is_equity_scheme, c.scheme_category, c.valid_from_month,
                  c.valid_to_month, c.classification_source,
                  c.classification_rule_version,
                  CASE WHEN av.import_file_id=? THEN 1 ELSE 0 END AS is_current_accepted_version
           FROM mf_schemes AS s
           JOIN mf_holdings AS h
             ON h.scheme_id=s.scheme_id AND h.import_file_id=?
           JOIN mf_scheme_classifications AS c
             ON c.scheme_classification_id=(
               SELECT MAX(c2.scheme_classification_id)
               FROM mf_scheme_classifications AS c2
               WHERE c2.scheme_id=s.scheme_id
                 AND c2.valid_from_month<=h.disclosure_month
                 AND (c2.valid_to_month IS NULL OR c2.valid_to_month>=h.disclosure_month)
             )
           LEFT JOIN mf_accepted_versions AS av
             ON av.publisher=h.publisher AND av.scheme_id=h.scheme_id
            AND av.disclosure_month=h.disclosure_month
           GROUP BY s.scheme_id
           ORDER BY s.scheme_code""", 'SELECT s.scheme_id, s.publisher, s.scheme_code, s.current_name,\n                  c.scheme_classification_id, c.scheme_management_type,\n                  c.is_equity_scheme, c.scheme_category, c.valid_from_month,\n                  c.valid_to_month, c.classification_source,\n                  c.classification_rule_version,\n                  CASE WHEN av.import_file_id=? THEN 1 ELSE 0 END AS is_current_accepted_version\n           FROM mf_schemes AS s\n           JOIN mf_holdings AS h\n             ON h.scheme_id=s.scheme_id AND h.import_file_id=?\n           JOIN mf_scheme_classifications AS c\n             ON c.scheme_classification_id=(\n               SELECT MAX(c2.scheme_classification_id)\n               FROM mf_scheme_classifications AS c2\n               WHERE c2.scheme_id=s.scheme_id\n                 AND c2.valid_from_month<=h.disclosure_month\n                 AND (c2.valid_to_month IS NULL OR c2.valid_to_month>=h.disclosure_month)\n             )\n           LEFT JOIN mf_accepted_versions AS av\n             ON av.publisher=h.publisher AND av.scheme_id=h.scheme_id\n            AND av.disclosure_month=h.disclosure_month\n           GROUP BY s.scheme_id,c.scheme_classification_id,av.import_file_id\n           ORDER BY s.scheme_code'),
        (import_file_id, import_file_id),
    ).fetchall()
    state_events = conn.execute(
        """SELECT state_event_id, from_state, to_state, changed_at_utc,
                  changed_by, reason_json
           FROM mf_import_state_events WHERE import_file_id=?
           ORDER BY state_event_id""",
        (import_file_id,),
    ).fetchall()
    scope_counts = {
        "all_scheme_accepted_holdings": int(
            conn.execute(
                """SELECT COUNT(*) FROM mf_accepted_holdings
                   WHERE import_file_id=?""",
                (import_file_id,),
            ).fetchone()[0]
        ),
        "active_equity_accepted_holdings": int(
            conn.execute(
                """SELECT COUNT(*) FROM mf_active_equity_accepted_holdings
                   WHERE import_file_id=?""",
                (import_file_id,),
            ).fetchone()[0]
        ),
        "all_scheme_stock_feature_eligible_holdings": int(
            conn.execute(
                """SELECT COUNT(*) FROM mf_stock_feature_eligible_holdings
                   WHERE import_file_id=?""",
                (import_file_id,),
            ).fetchone()[0]
        ),
        "active_equity_stock_feature_eligible_holdings": int(
            conn.execute(
                """SELECT COUNT(*) FROM mf_active_equity_stock_feature_eligible_holdings
                   WHERE import_file_id=?""",
                (import_file_id,),
            ).fetchone()[0]
        ),
    }
    change_sets = conn.execute(
        """SELECT cs.*,
                  CASE WHEN current_sets.change_set_id IS NOT NULL THEN 1 ELSE 0 END
                    AS is_current
           FROM mf_scheme_month_change_sets AS cs
           LEFT JOIN mf_current_scheme_month_change_sets AS current_sets
             ON current_sets.change_set_id=cs.change_set_id
           WHERE cs.previous_import_file_id=? OR cs.current_import_file_id=?
           ORDER BY cs.previous_month, cs.current_month, cs.scheme_id,
                    cs.change_set_id""",
        (import_file_id, import_file_id),
    ).fetchall()
    changes = conn.execute(
        """SELECT changes.*
           FROM mf_current_scheme_security_month_changes AS changes
           WHERE changes.previous_import_file_id=? OR changes.current_import_file_id=?
           ORDER BY changes.current_month, changes.scheme_id,
                    changes.isin_normalized""",
        (import_file_id, import_file_id),
    ).fetchall()
    stock_features = conn.execute(
        """SELECT DISTINCT feature.*
           FROM mf_current_stock_monthly_features AS feature
           JOIN mf_stock_month_feature_inputs AS input
             ON input.feature_set_id=feature.feature_set_id
           WHERE input.previous_import_file_id=? OR input.current_import_file_id=?
           ORDER BY feature.disclosure_month, feature.feature_scope,
                    feature.isin_normalized""",
        (import_file_id, import_file_id),
    ).fetchall()
    stock_feature_lineage: dict[int, list[dict]] = {}
    for row in conn.execute(
        statement(conn, """SELECT feature.stock_month_feature_id,
                  change.scheme_security_change_id, change.change_set_id,
                  change.scheme_id, scheme.scheme_code,
                  classification.scheme_management_type,
                  classification.is_equity_scheme,
                  classification.classification_rule_version,
                  change.change_class, change.quantity_change_decimal,
                  change.portfolio_weight_change_decimal,
                  change.previous_import_file_id,
                  change.current_import_file_id,
                  lineage.previous_content_sha256,
                  lineage.current_content_sha256
           FROM mf_current_stock_monthly_features AS feature
           JOIN mf_stock_month_feature_lineage AS lineage
             ON lineage.stock_month_feature_id=feature.stock_month_feature_id
           JOIN mf_scheme_security_month_changes AS change
             ON change.scheme_security_change_id=lineage.scheme_security_change_id
           JOIN mf_schemes AS scheme ON scheme.scheme_id=change.scheme_id
           JOIN mf_scheme_classifications AS classification
             ON classification.scheme_classification_id=lineage.scheme_classification_id
           JOIN mf_stock_month_feature_inputs AS input
             ON input.feature_set_id=feature.feature_set_id
           WHERE input.previous_import_file_id=? OR input.current_import_file_id=?
           GROUP BY feature.stock_month_feature_id,
                    change.scheme_security_change_id
           ORDER BY feature.stock_month_feature_id, change.scheme_id""", 'SELECT DISTINCT feature.stock_month_feature_id,\n                  change.scheme_security_change_id, change.change_set_id,\n                  change.scheme_id, scheme.scheme_code,\n                  classification.scheme_management_type,\n                  classification.is_equity_scheme,\n                  classification.classification_rule_version,\n                  change.change_class, change.quantity_change_decimal,\n                  change.portfolio_weight_change_decimal,\n                  change.previous_import_file_id,\n                  change.current_import_file_id,\n                  lineage.previous_content_sha256,\n                  lineage.current_content_sha256\n           FROM mf_current_stock_monthly_features AS feature\n           JOIN mf_stock_month_feature_lineage AS lineage\n             ON lineage.stock_month_feature_id=feature.stock_month_feature_id\n           JOIN mf_scheme_security_month_changes AS change\n             ON change.scheme_security_change_id=lineage.scheme_security_change_id\n           JOIN mf_schemes AS scheme ON scheme.scheme_id=change.scheme_id\n           JOIN mf_scheme_classifications AS classification\n             ON classification.scheme_classification_id=lineage.scheme_classification_id\n           JOIN mf_stock_month_feature_inputs AS input\n             ON input.feature_set_id=feature.feature_set_id\n           WHERE input.previous_import_file_id=? OR input.current_import_file_id=?\n           \n           ORDER BY feature.stock_month_feature_id, change.scheme_id'),
        (import_file_id, import_file_id),
    ):
        stock_feature_lineage.setdefault(
            int(row["stock_month_feature_id"]), []
        ).append(
            {
                **{
                    key: row[key]
                    for key in row.keys()
                    if key != "stock_month_feature_id"
                    and key != "is_equity_scheme"
                },
                "is_equity_scheme": bool(row["is_equity_scheme"]),
            }
        )
    change_validations: dict[int, list[dict]] = {}
    for row in conn.execute(
        """SELECT validation.change_set_id, validation.rule_code,
                  validation.severity, validation.validation_status,
                  validation.details_json
           FROM mf_scheme_month_change_validations AS validation
           JOIN mf_scheme_month_change_sets AS cs
             ON cs.change_set_id=validation.change_set_id
           WHERE cs.previous_import_file_id=? OR cs.current_import_file_id=?
           ORDER BY validation.change_set_id, validation.rule_code""",
        (import_file_id, import_file_id),
    ):
        change_validations.setdefault(int(row["change_set_id"]), []).append(
            {
                "rule_code": row["rule_code"],
                "severity": row["severity"],
                "validation_status": row["validation_status"],
                "details": json.loads(row["details_json"]),
            }
        )
    return {
        "schema_version": "1.3.0",
        "provenance": {
            key: imported[key]
            for key in (
                "import_file_id", "publisher", "detected_publisher",
                "requested_disclosure_month", "detected_disclosure_month",
                "original_filename", "content_sha256", "byte_size",
                "workbook_type", "workbook_manifest_version", "import_state",
                "first_observed_at_utc", "accepted_at_utc", "local_path",
                "safety_status",
            )
        },
        "source_faithful": {
            "holding_row_cells": [dict(row) for row in source_cells],
        },
        "normalized": {
            "holdings": [
                {
                    key: row[key]
                    for key in (
                        "holding_id", "sheet_position", "sheet_name_raw",
                        "source_table_position", "source_table_kind", "source_row",
                        "publisher", "source", "source_quality", "scheme_id",
                        "scheme_name_raw", "scheme_code", "scheme_category",
                        "disclosure_month", "source_security_code_raw",
                        "security_name_raw", "isin_raw", "isin_normalized",
                        "isin_validation_status", "quantity_decimal",
                        "quantity_missing_kind", "market_value_decimal",
                        "market_value_missing_kind", "percentage_of_nav_decimal",
                        "percentage_of_aum_decimal", "percentage_missing_kind",
                        "percentage_qualifier", "percentage_basis",
                        "instrument_type_raw", "instrument_type_code",
                        "industry_or_rating_raw", "position_direction_raw", "currency",
                        "quantity_unit_code", "market_value_unit_code",
                        "market_value_scale_multiplier", "percentage_unit_code",
                        "normalization_rule_version", "first_observed_at_utc",
                        "accepted_at_utc", "security_id", "security_match_status",
                        "match_method", "match_key", "match_rule_version",
                    )
                }
                | {
                    "candidate_security_ids": json.loads(
                        row["candidate_security_ids_json"]
                    ),
                    "match_evidence": json.loads(row["evidence_json"]),
                }
                for row in holdings
            ],
            "scheme_registry": [
                {
                    **{
                        key: row[key]
                        for key in (
                            "scheme_id", "publisher", "scheme_code", "current_name",
                            "scheme_classification_id", "scheme_management_type",
                            "scheme_category", "valid_from_month", "valid_to_month",
                            "classification_source", "classification_rule_version",
                        )
                    },
                    "is_equity_scheme": bool(row["is_equity_scheme"]),
                    "is_current_accepted_version": bool(
                        row["is_current_accepted_version"]
                    ),
                }
                for row in registry
            ],
            "accepted_scope_counts": scope_counts,
            "scheme_security_month_changes": [
                {
                    **{
                        key: row[key]
                        for key in row.keys()
                        if key != "completeness_evidence_json"
                    },
                    "completeness_evidence": json.loads(
                        row["completeness_evidence_json"]
                    ),
                }
                for row in changes
            ],
            "stock_monthly_features": [
                {
                    **dict(row),
                    "number_of_funds_increasing": row[
                        "funds_increasing_quantity"
                    ],
                    "lineage": stock_feature_lineage.get(
                        int(row["stock_month_feature_id"]), []
                    ),
                }
                for row in stock_features
            ],
        },
        "quality": {
            "validations": [
                {
                    "rule_code": row["rule_code"],
                    "severity": row["severity"],
                    "passed": bool(row["passed"]),
                    "details": json.loads(row["details_json"]),
                }
                for row in validations
            ],
            "state_events": [
                {
                    **{
                        key: row[key]
                        for key in (
                            "state_event_id", "from_state", "to_state",
                            "changed_at_utc", "changed_by",
                        )
                    },
                    "reason": json.loads(row["reason_json"]),
                }
                for row in state_events
            ],
            "month_change_sets": [
                {
                    **{
                        key: row[key]
                        for key in (
                            "change_set_id", "publisher", "scheme_id",
                            "previous_month", "current_month",
                            "previous_import_file_id", "current_import_file_id",
                            "comparison_status", "reason_code", "engine_version",
                            "input_fingerprint", "generated_at_utc",
                        )
                    },
                    "is_current": bool(row["is_current"]),
                    "completeness_evidence": json.loads(
                        row["completeness_evidence_json"]
                    ),
                    "validations": change_validations.get(
                        int(row["change_set_id"]), []
                    ),
                }
                for row in change_sets
            ],
        },
    }


def write_mf_workbook_export(
    conn: sqlite3.Connection, import_file_id: int, output: Path
) -> Path:
    payload = build_mf_workbook_export(conn, import_file_id)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json_payload(payload, output)
    return output
