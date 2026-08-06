"""M07 coverage report for a bounded official-workbook vertical slice."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


SCHEMA_VERSION = "1.0.0"


def adjacent_month(month: str, offset: int) -> str:
    year, number = (int(part) for part in month.split("-"))
    absolute = year * 12 + number - 1 + offset
    return f"{absolute // 12:04d}-{absolute % 12 + 1:02d}"


def expected_months(through_month: str, month_count: int) -> list[str]:
    if month_count < 2:
        raise ValueError("A vertical slice requires at least two months")
    return [
        adjacent_month(through_month, offset)
        for offset in range(1 - month_count, 1)
    ]


def _current_month_rows(
    conn: sqlite3.Connection, publisher: str, month: str
) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT av.scheme_id, scheme.scheme_code, av.import_file_id,
                  file.import_state, file.content_sha256,
                  file.detected_disclosure_month,
                  file.workbook_manifest_version
           FROM mf_accepted_versions AS av
           JOIN mf_schemes AS scheme ON scheme.scheme_id=av.scheme_id
           JOIN mf_import_files AS file
             ON file.import_file_id=av.import_file_id
           WHERE av.publisher=? AND av.disclosure_month=?
           ORDER BY scheme.scheme_code""",
        (publisher, month),
    ).fetchall()


def _workbook_coverage(
    conn: sqlite3.Connection, publisher: str, month: str
) -> dict:
    rows = _current_month_rows(conn, publisher, month)
    import_ids = sorted({int(row["import_file_id"]) for row in rows})
    acceptance = None
    if len(import_ids) == 1:
        acceptance = conn.execute(
            """SELECT validation_status, scheme_count, decided_at_utc
               FROM mf_workbook_acceptance_events WHERE import_file_id=?""",
            (import_ids[0],),
        ).fetchone()
    pointer_count = len(rows)
    expected_pointer_count = int(acceptance["scheme_count"]) if acceptance else None
    accepted = bool(
        len(import_ids) == 1
        and acceptance is not None
        and pointer_count == expected_pointer_count
        and all(row["import_state"] == "accepted" for row in rows)
        and all(row["detected_disclosure_month"] == month for row in rows)
    )
    import_id = import_ids[0] if len(import_ids) == 1 else None
    source_cell_count = 0
    normalized_holding_count = 0
    failed_validations: list[str] = []
    if import_id is not None:
        source_cell_count = int(conn.execute(
            """SELECT COUNT(*) FROM mf_workbook_cells AS cell
               JOIN mf_workbook_sheets AS sheet ON sheet.sheet_id=cell.sheet_id
               WHERE sheet.import_file_id=?""",
            (import_id,),
        ).fetchone()[0])
        normalized_holding_count = int(conn.execute(
            "SELECT COUNT(*) FROM mf_holdings WHERE import_file_id=?",
            (import_id,),
        ).fetchone()[0])
        failed_validations = [
            str(row["rule_code"])
            for row in conn.execute(
                """SELECT rule_code FROM mf_workbook_validation_results
                   WHERE import_file_id=? AND severity='error' AND passed=0
                   ORDER BY rule_code""",
                (import_id,),
            )
        ]
    return {
        "month": month,
        "coverage_status": "complete" if accepted and not failed_validations else "incomplete",
        "current_import_file_ids": import_ids,
        "content_sha256": str(rows[0]["content_sha256"]) if len(import_ids) == 1 else None,
        "manifest_version": (
            str(rows[0]["workbook_manifest_version"])
            if len(import_ids) == 1 else None
        ),
        "accepted_validation_status": (
            str(acceptance["validation_status"]) if acceptance else None
        ),
        "accepted_at_utc": str(acceptance["decided_at_utc"]) if acceptance else None,
        "scheme_pointer_count": pointer_count,
        "expected_scheme_pointer_count": expected_pointer_count,
        "source_cell_count": source_cell_count,
        "normalized_holding_count": normalized_holding_count,
        "failed_blocking_validations": failed_validations,
    }


def _scheme_coverage(
    conn: sqlite3.Connection, publisher: str, month: str
) -> dict:
    rows = _current_month_rows(conn, publisher, month)
    schemes: list[dict] = []
    for row in rows:
        classification = conn.execute(
            """SELECT scheme_management_type, is_equity_scheme,
                      classification_rule_version
               FROM mf_scheme_classifications
               WHERE scheme_id=? AND valid_from_month<=?
                 AND (valid_to_month IS NULL OR valid_to_month>=?)
               ORDER BY scheme_classification_id DESC LIMIT 1""",
            (row["scheme_id"], month, month),
        ).fetchone()
        schemes.append({
            "scheme_id": int(row["scheme_id"]),
            "scheme_code": str(row["scheme_code"]),
            "import_file_id": int(row["import_file_id"]),
            "classification_status": "present" if classification else "missing",
            "scheme_management_type": (
                str(classification["scheme_management_type"]) if classification else None
            ),
            "is_equity_scheme": (
                bool(classification["is_equity_scheme"]) if classification else None
            ),
            "classification_rule_version": (
                str(classification["classification_rule_version"])
                if classification else None
            ),
        })
    missing_classifications = [
        item["scheme_code"]
        for item in schemes if item["classification_status"] == "missing"
    ]
    return {
        "month": month,
        "coverage_status": (
            "complete" if schemes and not missing_classifications else "incomplete"
        ),
        "scheme_count": len(schemes),
        "missing_classifications": missing_classifications,
        "schemes": schemes,
    }


def _security_coverage(
    conn: sqlite3.Connection, publisher: str, month: str
) -> dict:
    rows = _current_month_rows(conn, publisher, month)
    import_ids = sorted({int(row["import_file_id"]) for row in rows})
    if len(import_ids) != 1:
        return {
            "month": month,
            "coverage_status": "not_evaluated",
            "holding_count": 0,
            "primary_holding_count": 0,
            "feature_eligible_count": 0,
            "primary_unmatched_count": 0,
            "non_primary_count": 0,
            "invalid_isin_count": 0,
            "matched_security_count": 0,
            "unexplained_dropped_holding_count": 0,
        }
    import_id = import_ids[0]
    counts = conn.execute(
        """SELECT COUNT(*) AS holding_count,
                  SUM(source_table_kind='primary_portfolio') AS primary_count,
                  SUM(source_table_kind!='primary_portfolio') AS non_primary_count,
                  SUM(source_table_kind='primary_portfolio'
                      AND security_id IS NOT NULL
                      AND security_match_status='matched') AS eligible_count,
                  SUM(source_table_kind='primary_portfolio'
                      AND NOT (security_id IS NOT NULL
                               AND security_match_status='matched'))
                    AS primary_unmatched_count,
                  SUM(isin_validation_status LIKE 'invalid%') AS invalid_isin_count,
                  COUNT(DISTINCT CASE WHEN security_id IS NOT NULL
                                      AND security_match_status='matched'
                                      THEN security_id END) AS security_count
           FROM mf_holdings WHERE import_file_id=?""",
        (import_id,),
    ).fetchone()
    holding_count = int(counts["holding_count"] or 0)
    primary_count = int(counts["primary_count"] or 0)
    non_primary_count = int(counts["non_primary_count"] or 0)
    eligible_count = int(counts["eligible_count"] or 0)
    primary_unmatched_count = int(counts["primary_unmatched_count"] or 0)
    unexplained = holding_count - non_primary_count - eligible_count - primary_unmatched_count
    return {
        "month": month,
        "coverage_status": "complete" if holding_count and unexplained == 0 else "incomplete",
        "holding_count": holding_count,
        "primary_holding_count": primary_count,
        "feature_eligible_count": eligible_count,
        "primary_unmatched_count": primary_unmatched_count,
        "non_primary_count": non_primary_count,
        "invalid_isin_count": int(counts["invalid_isin_count"] or 0),
        "matched_security_count": int(counts["security_count"] or 0),
        "unexplained_dropped_holding_count": unexplained,
    }


def _pair_coverage(
    conn: sqlite3.Connection, publisher: str, previous_month: str,
    current_month: str,
) -> dict:
    previous_rows = _current_month_rows(conn, publisher, previous_month)
    current_rows = _current_month_rows(conn, publisher, current_month)
    previous_scheme_ids = {int(row["scheme_id"]) for row in previous_rows}
    current_scheme_ids = {int(row["scheme_id"]) for row in current_rows}
    # A scheme first disclosed in the current month has no prior source row and
    # is outside that pair's comparable population. Its skipped M05 audit stays
    # stored; it must not become a fabricated new entry.
    current_only_scheme_ids = current_scheme_ids - previous_scheme_ids
    lifecycle_excluded_scheme_ids = sorted(
        scheme_id for scheme_id in current_only_scheme_ids
        if conn.execute(
            """SELECT 1 FROM mf_scheme_classifications
               WHERE scheme_id=? AND valid_from_month=? LIMIT 1""",
            (scheme_id, current_month),
        ).fetchone() is not None
    )
    expected_scheme_ids = (
        previous_scheme_ids
        | (current_only_scheme_ids - set(lifecycle_excluded_scheme_ids))
    )
    change_sets = conn.execute(
        """SELECT change_set_id, scheme_id
           FROM mf_current_scheme_month_change_sets
           WHERE publisher=? AND previous_month=? AND current_month=?
           ORDER BY scheme_id""",
        (publisher, previous_month, current_month),
    ).fetchall()
    generated_scheme_ids = {int(row["scheme_id"]) for row in change_sets}
    change_count = 0
    if change_sets:
        placeholders = ",".join("?" for _ in change_sets)
        change_count = int(conn.execute(
            f"""SELECT COUNT(*) FROM mf_scheme_security_month_changes
                WHERE change_set_id IN ({placeholders})""",
            [int(row["change_set_id"]) for row in change_sets],
        ).fetchone()[0])
    feature_sets = conn.execute(
        """SELECT * FROM mf_current_stock_month_feature_sets
           WHERE publisher=? AND previous_month=? AND current_month=?
           ORDER BY feature_set_id""",
        (publisher, previous_month, current_month),
    ).fetchall()
    feature_set_id = int(feature_sets[0]["feature_set_id"]) if len(feature_sets) == 1 else None
    feature_counts = {"all_scheme": 0, "active_equity": 0}
    failed_feature_validations: list[str] = []
    feature_input_count = 0
    if feature_set_id is not None:
        feature_input_count = int(conn.execute(
            "SELECT COUNT(*) FROM mf_stock_month_feature_inputs WHERE feature_set_id=?",
            (feature_set_id,),
        ).fetchone()[0])
        for row in conn.execute(
            """SELECT feature_scope, COUNT(*) AS count
               FROM mf_stock_monthly_features WHERE feature_set_id=?
               GROUP BY feature_scope""",
            (feature_set_id,),
        ):
            feature_counts[str(row["feature_scope"])] = int(row["count"])
        failed_feature_validations = [
            str(row["rule_code"])
            for row in conn.execute(
                """SELECT rule_code FROM mf_stock_month_feature_validations
                   WHERE feature_set_id=? AND validation_status!='passed'
                   ORDER BY rule_code""",
                (feature_set_id,),
            )
        ]
    missing_change_schemes = sorted(expected_scheme_ids - generated_scheme_ids)
    comparison_complete = bool(
        expected_scheme_ids
        and not missing_change_schemes
        and len(generated_scheme_ids) == len(expected_scheme_ids)
    )
    feature_complete = bool(
        comparison_complete
        and feature_set_id is not None
        and feature_input_count == len(expected_scheme_ids)
        and not failed_feature_validations
    )
    return {
        "previous_month": previous_month,
        "current_month": current_month,
        "comparison_status": "complete" if comparison_complete else "incomplete",
        "feature_status": "complete" if feature_complete else "incomplete",
        "expected_scheme_count": len(expected_scheme_ids),
        "lifecycle_excluded_scheme_ids": lifecycle_excluded_scheme_ids,
        "generated_change_set_count": len(change_sets),
        "missing_change_scheme_ids": missing_change_schemes,
        "scheme_security_change_count": change_count,
        "feature_set_id": feature_set_id,
        "feature_input_count": feature_input_count,
        "all_scheme_feature_count": feature_counts["all_scheme"],
        "active_equity_feature_count": feature_counts["active_equity"],
        "failed_feature_validations": failed_feature_validations,
    }


def build_mf_coverage_report(
    conn: sqlite3.Connection, *, publisher: str, through_month: str,
    month_count: int = 12,
) -> dict:
    months = expected_months(through_month, month_count)
    workbooks = [_workbook_coverage(conn, publisher, month) for month in months]
    schemes = [_scheme_coverage(conn, publisher, month) for month in months]
    securities = [_security_coverage(conn, publisher, month) for month in months]
    pairs = [
        _pair_coverage(conn, publisher, months[index - 1], months[index])
        for index in range(1, len(months))
    ]
    rules = {
        "m07_exact_workbook_month_coverage": all(
            item["coverage_status"] == "complete" for item in workbooks
        ),
        "m07_scheme_registry_coverage": all(
            item["coverage_status"] == "complete" for item in schemes
        ),
        "m07_security_match_accounting": all(
            item["coverage_status"] == "complete"
            and item["unexplained_dropped_holding_count"] == 0
            for item in securities
        ),
        "m07_consecutive_pair_coverage": all(
            item["comparison_status"] == "complete" for item in pairs
        ),
        "m07_feature_scope_coverage": all(
            item["feature_status"] == "complete" for item in pairs
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "selection": {
            "publisher": publisher,
            "start_month": months[0],
            "through_month": through_month,
            "expected_month_count": month_count,
            "expected_pair_count": month_count - 1,
        },
        "coverage_status": "complete" if all(rules.values()) else "incomplete",
        "summary": {
            "accepted_workbook_month_count": sum(
                item["coverage_status"] == "complete" for item in workbooks
            ),
            "complete_scheme_month_count": sum(
                item["coverage_status"] == "complete" for item in schemes
            ),
            "complete_security_accounting_month_count": sum(
                item["coverage_status"] == "complete" for item in securities
            ),
            "complete_comparison_count": sum(
                item["comparison_status"] == "complete" for item in pairs
            ),
            "complete_feature_month_count": sum(
                item["feature_status"] == "complete" for item in pairs
            ),
            "all_scheme_feature_count": sum(
                item["all_scheme_feature_count"] for item in pairs
            ),
            "active_equity_feature_count": sum(
                item["active_equity_feature_count"] for item in pairs
            ),
            "unexplained_dropped_holding_count": sum(
                item["unexplained_dropped_holding_count"] for item in securities
            ),
        },
        "rules": {
            code: "passed" if passed else "failed" for code, passed in rules.items()
        },
        "coverage": {
            "workbooks": workbooks,
            "schemes": schemes,
            "security_matches": securities,
            "month_pairs": pairs,
        },
    }


def write_mf_coverage_report(
    conn: sqlite3.Connection, *, publisher: str, through_month: str,
    output: Path, month_count: int = 12,
) -> Path:
    payload = build_mf_coverage_report(
        conn, publisher=publisher, through_month=through_month,
        month_count=month_count,
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output
