"""Exact M06 stock-month features and workbook/change lineage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from .changes import adjacent_month
from .feature_metrics import (
    aggregate_feature_rows,
    decimal_value,
    is_active_equity,
)


FEATURE_ENGINE_VERSION = "mf_stock_month_features_v1"
FEATURE_SCOPES = ("all_scheme", "active_equity")


@dataclass(frozen=True)
class FeatureGenerationSummary:
    publisher: str
    previous_month: str
    current_month: str
    aggregation_status: str
    reason_code: str
    feature_set_id: int
    all_scheme_feature_count: int
    active_equity_feature_count: int
    lineage_count: int

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _effective_classification(
    conn: sqlite3.Connection, scheme_id: int, month: str
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM mf_scheme_classifications
           WHERE scheme_id=? AND valid_from_month<=?
             AND (valid_to_month IS NULL OR valid_to_month>=?)
           ORDER BY scheme_classification_id DESC LIMIT 1""",
        (scheme_id, month, month),
    ).fetchone()


def _pair_inputs(
    conn: sqlite3.Connection, publisher: str, previous_month: str, current_month: str
) -> tuple[list[dict], list[dict], bool]:
    scheme_ids = [
        int(row["scheme_id"])
        for row in conn.execute(
            """SELECT previous.scheme_id
               FROM mf_accepted_versions AS previous
               WHERE previous.publisher=? AND previous.disclosure_month=?
               UNION
               SELECT current.scheme_id
               FROM mf_accepted_versions AS current
               WHERE current.publisher=? AND current.disclosure_month=?
                 AND NOT EXISTS (
                   SELECT 1 FROM mf_scheme_classifications AS lifecycle
                   WHERE lifecycle.scheme_id=current.scheme_id
                     AND lifecycle.valid_from_month=?
                 )
               ORDER BY scheme_id""",
            (
                publisher, previous_month,
                publisher, current_month, current_month,
            ),
        )
    ]
    evidence: list[dict] = []
    complete_inputs: list[dict] = []
    for scheme_id in scheme_ids:
        latest = conn.execute(
            """SELECT * FROM mf_scheme_month_change_sets
               WHERE publisher=? AND scheme_id=?
                 AND previous_month=? AND current_month=?
               ORDER BY change_set_id DESC LIMIT 1""",
            (publisher, scheme_id, previous_month, current_month),
        ).fetchone()
        current = conn.execute(
            """SELECT * FROM mf_current_scheme_month_change_sets
               WHERE publisher=? AND scheme_id=?
                 AND previous_month=? AND current_month=?""",
            (publisher, scheme_id, previous_month, current_month),
        ).fetchone()
        item = {
            "scheme_id": scheme_id,
            "latest_change_set_id": int(latest["change_set_id"]) if latest else None,
            "latest_comparison_status": latest["comparison_status"] if latest else None,
            "latest_reason_code": latest["reason_code"] if latest else None,
            "current_generated_change_set_id": (
                int(current["change_set_id"]) if current else None
            ),
        }
        classification = _effective_classification(conn, scheme_id, current_month)
        if classification is None:
            item["classification_status"] = "missing"
        else:
            item.update({
                "scheme_classification_id": int(
                    classification["scheme_classification_id"]
                ),
                "scheme_management_type": str(
                    classification["scheme_management_type"]
                ),
                "is_equity_scheme": bool(classification["is_equity_scheme"]),
                "classification_rule_version": str(
                    classification["classification_rule_version"]
                ),
            })
        evidence.append(item)
        if current is None or classification is None:
            continue
        previous_import_id = int(current["previous_import_file_id"])
        current_import_id = int(current["current_import_file_id"])
        hashes = conn.execute(
            """SELECT
                 (SELECT content_sha256 FROM mf_import_files WHERE import_file_id=?)
                   AS previous_hash,
                 (SELECT content_sha256 FROM mf_import_files WHERE import_file_id=?)
                   AS current_hash""",
            (previous_import_id, current_import_id),
        ).fetchone()
        resolved = {
            **item,
            "change_set_id": int(current["change_set_id"]),
            "scheme_classification_id": int(
                classification["scheme_classification_id"]
            ),
            "scheme_management_type": str(
                classification["scheme_management_type"]
            ),
            "is_equity_scheme": bool(classification["is_equity_scheme"]),
            "classification_rule_version": str(
                classification["classification_rule_version"]
            ),
            "previous_import_file_id": previous_import_id,
            "current_import_file_id": current_import_id,
            "previous_content_sha256": str(hashes["previous_hash"]),
            "current_content_sha256": str(hashes["current_hash"]),
        }
        complete_inputs.append(resolved)
    complete = bool(scheme_ids) and len(complete_inputs) == len(scheme_ids)
    return evidence, complete_inputs, complete


def _summary(conn: sqlite3.Connection, feature_set_id: int) -> FeatureGenerationSummary:
    feature_set = conn.execute(
        "SELECT * FROM mf_stock_month_feature_sets WHERE feature_set_id=?",
        (feature_set_id,),
    ).fetchone()
    counts = {
        str(row["feature_scope"]): int(row["count"])
        for row in conn.execute(
            """SELECT feature_scope, COUNT(*) AS count
               FROM mf_stock_monthly_features WHERE feature_set_id=?
               GROUP BY feature_scope""",
            (feature_set_id,),
        )
    }
    lineage_count = int(conn.execute(
        """SELECT COUNT(*) FROM mf_stock_month_feature_lineage AS lineage
           JOIN mf_stock_monthly_features AS feature
             ON feature.stock_month_feature_id=lineage.stock_month_feature_id
           WHERE feature.feature_set_id=?""",
        (feature_set_id,),
    ).fetchone()[0])
    return FeatureGenerationSummary(
        str(feature_set["publisher"]), str(feature_set["previous_month"]),
        str(feature_set["current_month"]), str(feature_set["aggregation_status"]),
        str(feature_set["reason_code"]), feature_set_id,
        counts.get("all_scheme", 0), counts.get("active_equity", 0), lineage_count,
    )


def _persist_validations(
    conn: sqlite3.Connection, feature_set_id: int, *, complete: bool,
    classification_complete: bool, expected_counts: dict[str, int] | None = None,
    actual_counts: dict[str, int] | None = None, lineage_complete: bool = False,
    equations_complete: bool = False,
) -> None:
    evaluated = expected_counts is not None and actual_counts is not None
    rules = (
        ("m06_complete_current_change_sets", "passed" if complete else "failed", {}),
        (
            "m06_effective_scheme_classifications",
            "passed" if classification_complete else "failed",
            {},
        ),
        (
            "m06_feature_security_union_complete",
            (
                "not_evaluated" if not evaluated
                else "passed" if expected_counts == actual_counts else "failed"
            ),
            {"expected": expected_counts, "actual": actual_counts},
        ),
        (
            "m06_exact_feature_reconciliation",
            "not_evaluated" if not evaluated else "passed" if equations_complete else "failed",
            {},
        ),
        (
            "m06_feature_lineage_complete",
            "not_evaluated" if not evaluated else "passed" if lineage_complete else "failed",
            {},
        ),
        (
            "m06_active_equity_scope_filter",
            "not_evaluated" if not evaluated else "passed",
            {"scope_rule": "active_and_equity_only"},
        ),
    )
    conn.executemany(
        """INSERT OR IGNORE INTO mf_stock_month_feature_validations
           (feature_set_id, rule_code, severity, validation_status, details_json)
           VALUES (?, ?, 'error', ?, ?)""",
        ((feature_set_id, code, status, _json(details)) for code, status, details in rules),
    )


def generate_stock_month_features(
    conn: sqlite3.Connection, *, publisher: str, current_month: str,
    generated_at_utc: str,
) -> FeatureGenerationSummary:
    """Aggregate one complete current M05 pair with exact Decimal arithmetic."""
    previous_month = adjacent_month(current_month, -1)
    evidence, inputs, complete = _pair_inputs(
        conn, publisher, previous_month, current_month
    )
    classification_complete = all(
        item.get("scheme_classification_id") is not None for item in evidence
    ) and bool(evidence)
    reason = (
        "complete_current_scheme_change_sets" if complete
        else "incomplete_or_noncurrent_scheme_change_sets"
    )
    fingerprint_payload = {
        "engine_version": FEATURE_ENGINE_VERSION,
        "publisher": publisher,
        "previous_month": previous_month,
        "current_month": current_month,
        "reason_code": reason,
        "scheme_inputs": evidence,
    }
    fingerprint = hashlib.sha256(
        _json(fingerprint_payload).encode("utf-8")
    ).hexdigest()
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO mf_stock_month_feature_sets
               (publisher, previous_month, current_month, aggregation_status,
                reason_code, completeness_evidence_json, engine_version,
                input_fingerprint, generated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                publisher, previous_month, current_month,
                "generated" if complete else "skipped", reason,
                _json({"scheme_inputs": evidence, "all_inputs_complete": complete}),
                FEATURE_ENGINE_VERSION, fingerprint, generated_at_utc,
            ),
        )
        feature_set_id = int(conn.execute(
            """SELECT feature_set_id FROM mf_stock_month_feature_sets
               WHERE input_fingerprint=?""", (fingerprint,)
        ).fetchone()[0])
        if not complete:
            _persist_validations(
                conn, feature_set_id, complete=False,
                classification_complete=classification_complete,
            )
            return _summary(conn, feature_set_id)
        existing = conn.execute(
            """SELECT 1 FROM mf_stock_month_feature_inputs
               WHERE feature_set_id=? LIMIT 1""", (feature_set_id,)
        ).fetchone()
        if existing is not None:
            return _summary(conn, feature_set_id)
        for item in inputs:
            conn.execute(
                """INSERT INTO mf_stock_month_feature_inputs
                   (feature_set_id, change_set_id, scheme_id,
                    scheme_classification_id, previous_import_file_id,
                    current_import_file_id, previous_content_sha256,
                    current_content_sha256)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    feature_set_id, item["change_set_id"], item["scheme_id"],
                    item["scheme_classification_id"], item["previous_import_file_id"],
                    item["current_import_file_id"], item["previous_content_sha256"],
                    item["current_content_sha256"],
                ),
            )
        rows = conn.execute(
            """SELECT change.*
               FROM mf_current_scheme_security_month_changes AS change
               JOIN mf_stock_month_feature_inputs AS input
                 ON input.change_set_id=change.change_set_id
               WHERE input.feature_set_id=?
               ORDER BY change.security_id, change.scheme_id""",
            (feature_set_id,),
        ).fetchall()
        classifications = {int(item["scheme_id"]): item for item in inputs}
        by_security: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            by_security.setdefault(int(row["security_id"]), []).append(row)
        expected_counts = {"all_scheme": len(by_security), "active_equity": 0}
        expected_lineage = 0
        equations_complete = True
        for security_id, security_rows in by_security.items():
            isin = str(security_rows[0]["isin_normalized"])
            for scope in FEATURE_SCOPES:
                scoped_rows = (
                    security_rows if scope == "all_scheme" else [
                        row for row in security_rows
                        if is_active_equity(classifications[int(row["scheme_id"])])
                    ]
                )
                if not scoped_rows:
                    continue
                if scope == "active_equity":
                    expected_counts[scope] += 1
                metrics = aggregate_feature_rows(scoped_rows, classifications)
                equations_complete = equations_complete and (
                    metrics["accumulation_breadth"]
                    == metrics["new_fund_entries"] + metrics["funds_increasing_quantity"]
                    and metrics["distribution_breadth"]
                    == metrics["complete_fund_exits"] + metrics["funds_decreasing_quantity"]
                    and decimal_value(metrics["net_quantity_change"], "net")
                    == decimal_value(metrics["gross_quantity_bought"], "bought")
                    - decimal_value(metrics["gross_quantity_sold"], "sold")
                )
                columns = tuple(metrics)
                placeholders = ", ".join("?" for _ in columns)
                cursor = conn.execute(
                    f"""INSERT INTO mf_stock_monthly_features
                        (feature_set_id, publisher, security_id, isin_normalized,
                         disclosure_month, feature_scope, {', '.join(columns)},
                         generated_at_utc)
                        VALUES (?, ?, ?, ?, ?, ?, {placeholders}, ?)""",
                    (
                        feature_set_id, publisher, security_id, isin, current_month,
                        scope, *(metrics[key] for key in columns), generated_at_utc,
                    ),
                )
                feature_id = int(cursor.lastrowid)
                for row in scoped_rows:
                    item = classifications[int(row["scheme_id"])]
                    conn.execute(
                        """INSERT INTO mf_stock_month_feature_lineage
                           (stock_month_feature_id, scheme_security_change_id,
                            scheme_classification_id, previous_content_sha256,
                            current_content_sha256)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            feature_id, int(row["scheme_security_change_id"]),
                            item["scheme_classification_id"],
                            item["previous_content_sha256"],
                            item["current_content_sha256"],
                        ),
                    )
                    expected_lineage += 1
        actual_counts = {
            scope: int(conn.execute(
                """SELECT COUNT(*) FROM mf_stock_monthly_features
                   WHERE feature_set_id=? AND feature_scope=?""",
                (feature_set_id, scope),
            ).fetchone()[0])
            for scope in FEATURE_SCOPES
        }
        actual_lineage = int(conn.execute(
            """SELECT COUNT(*) FROM mf_stock_month_feature_lineage AS lineage
               JOIN mf_stock_monthly_features AS feature
                 ON feature.stock_month_feature_id=lineage.stock_month_feature_id
               WHERE feature.feature_set_id=?""", (feature_set_id,)
        ).fetchone()[0])
        _persist_validations(
            conn, feature_set_id, complete=True, classification_complete=True,
            expected_counts=expected_counts, actual_counts=actual_counts,
            lineage_complete=actual_lineage == expected_lineage,
            equations_complete=equations_complete,
        )
        if expected_counts != actual_counts or actual_lineage != expected_lineage:
            raise RuntimeError("M06 feature rows or lineage did not reconcile")
    return _summary(conn, feature_set_id)


def derive_stock_month_features_through(
    conn: sqlite3.Connection, *, publisher: str, through_month: str,
    generated_at_utc: str,
) -> tuple[FeatureGenerationSummary, ...]:
    months = [
        str(row["current_month"])
        for row in conn.execute(
            """SELECT DISTINCT current_month FROM mf_scheme_month_change_sets
               WHERE publisher=? AND current_month<=? ORDER BY current_month""",
            (publisher, through_month),
        )
    ]
    return tuple(
        generate_stock_month_features(
            conn, publisher=publisher, current_month=month,
            generated_at_utc=generated_at_utc,
        )
        for month in months
    )
