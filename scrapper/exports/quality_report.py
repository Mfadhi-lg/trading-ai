"""Cross-source quality report for every persisted acceptance decision."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..config import EXPORTS


QUALITY_REPORT_SCHEMA_VERSION = "1.1.0"

_NORMALIZED_TABLES = (
    "quote_observations",
    "company_metric_observations",
    "company_profile_links",
    "company_text_observations",
    "financial_observations",
    "growth_observations",
    "shareholding_observations",
    "document_links",
    "peer_observations",
    "trendlyne_monthly_pages",
    "trendlyne_month_links",
    "trendlyne_mf_page_summaries",
    "trendlyne_fund_source_rows",
    "trendlyne_fund_observations",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_or_text(value: str | None) -> object:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _rows(
    conn: sqlite3.Connection, query: str, params: tuple[object, ...] = ()
) -> list[dict]:
    return [dict(row) for row in conn.execute(query, params)]


def _validation_rows(conn: sqlite3.Connection, parse_id: int) -> list[dict]:
    rows = _rows(
        conn,
        """SELECT rule_code, severity, status, expected_value, actual_value,
                  details_json, created_at_utc
           FROM validation_results WHERE parse_id=?
           ORDER BY validation_id""",
        (parse_id,),
    )
    for row in rows:
        row["details"] = _json_or_text(row.pop("details_json"))
    return rows


def _sections(conn: sqlite3.Connection, parse_id: int) -> list[dict]:
    sections = _rows(
        conn,
        """SELECT section_id, section_code, heading_raw, position,
                  parse_status, error_json
           FROM source_sections WHERE parse_id=?
           ORDER BY position, section_id""",
        (parse_id,),
    )
    for section in sections:
        section_id = int(section.pop("section_id"))
        cells = _rows(
            conn,
            """SELECT table_position, row_position, column_position
               FROM source_table_cells WHERE section_id=?""",
            (section_id,),
        )
        tables = {int(cell["table_position"]) for cell in cells}
        source_rows = {
            (int(cell["table_position"]), int(cell["row_position"]))
            for cell in cells
        }
        source_columns = {
            (int(cell["table_position"]), int(cell["column_position"]))
            for cell in cells
        }
        section["error"] = _json_or_text(section.pop("error_json"))
        section["row_counts"] = {
            "source_fields": int(
                conn.execute(
                    "SELECT COUNT(*) FROM source_fields WHERE section_id=?",
                    (section_id,),
                ).fetchone()[0]
            ),
            "source_cells": len(cells),
            "source_tables": len(tables),
            "source_rows": len(source_rows),
            "source_columns": len(source_columns),
        }
    return sections


def _normalized_counts(conn: sqlite3.Connection, parse_id: int) -> dict[str, int]:
    return {
        table: int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE parse_id=?", (parse_id,)
            ).fetchone()[0]
        )
        for table in _NORMALIZED_TABLES
    }


def _unknown_labels(conn: sqlite3.Connection, parse_id: int) -> list[dict]:
    contracts = (
        (
            "company_metric",
            "company_metric_observations",
            "label_raw",
            "metric_code IS NULL",
        ),
        (
            "financial_line_item",
            "financial_observations",
            "line_item_raw",
            "line_item_code IS NULL",
        ),
        (
            "growth_metric",
            "growth_observations",
            "metric_label_raw",
            "metric_code IS NULL",
        ),
        (
            "shareholding_holder",
            "shareholding_observations",
            "holder_label_raw",
            "holder_code IS NULL",
        ),
        (
            "peer_metric",
            "peer_observations",
            "metric_label_raw",
            "metric_code IS NULL",
        ),
        (
            "document_type",
            "document_links",
            "title_raw",
            "document_type='concall_other'",
        ),
    )
    unknown: list[dict] = []
    for category, table, label_column, predicate in contracts:
        labels = [
            str(row[0])
            for row in conn.execute(
                f"""SELECT DISTINCT {label_column} FROM {table}
                    WHERE parse_id=? AND {predicate}
                    ORDER BY {label_column}""",
                (parse_id,),
            )
        ]
        if labels:
            unknown.append({"category": category, "labels": labels})
    return unknown


def _artifact(conn: sqlite3.Connection, parse_id: int) -> dict:
    row = conn.execute(
        """SELECT pa.parse_id, pa.blob_id, pa.parser_name, pa.parser_version,
                  pa.manifest_version, pa.parsed_at_utc, pa.parse_status,
                  pa.validation_status, rb.content_sha256, rb.byte_size,
                  rb.content_type, rb.first_observed_at_utc
           FROM parse_artifacts AS pa
           JOIN response_blobs AS rb ON rb.blob_id=pa.blob_id
           WHERE pa.parse_id=?""",
        (parse_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown parse artifact: {parse_id}")
    payload = dict(row)
    payload["hashes"] = {"source_content_sha256": payload.pop("content_sha256")}
    return payload


def _artifact_quality(conn: sqlite3.Connection, parse_id: int) -> dict:
    validations = _validation_rows(conn, parse_id)
    failed = [row for row in validations if row["status"] == "failed"]
    return {
        "artifact": _artifact(conn, parse_id),
        "sections": _sections(conn, parse_id),
        "row_counts": {
            "normalized": _normalized_counts(conn, parse_id),
            "validation_rules": len(validations),
        },
        "validation_failures": [
            row for row in failed if row["severity"] in {"error", "manual_review"}
        ],
        "validation_warnings": [
            row for row in failed if row["severity"] == "warning"
        ],
        "unknown_labels": _unknown_labels(conn, parse_id),
    }


def _supplemental_artifacts(conn: sqlite3.Connection, parse_id: int) -> list[dict]:
    supplements = _rows(
        conn,
        """SELECT component_code, supplemental_parse_id
           FROM supplemental_parse_links WHERE parent_parse_id=?
           ORDER BY component_code""",
        (parse_id,),
    )
    results: list[dict] = []
    for supplement in supplements:
        quality = _artifact_quality(conn, int(supplement["supplemental_parse_id"]))
        quality["component_code"] = supplement["component_code"]
        results.append(quality)
    return results


def _accepted_versions(conn: sqlite3.Connection) -> list[dict]:
    return _rows(
        conn,
        """SELECT apv.company_id, c.current_legal_name AS company_name,
                  apv.source, apv.reporting_scope, apv.resolved_source_period,
                  apv.parse_id, apv.accepted_at_utc, apv.accepted_by,
                  apv.acceptance_mode, pa.parser_name, pa.parser_version,
                  pa.manifest_version, rb.content_sha256
           FROM accepted_page_versions AS apv
           JOIN companies AS c ON c.company_id=apv.company_id
           JOIN parse_artifacts AS pa ON pa.parse_id=apv.parse_id
           JOIN response_blobs AS rb ON rb.blob_id=pa.blob_id
           ORDER BY apv.company_id, apv.source, apv.reporting_scope,
                    apv.resolved_source_period"""
    )


def _is_current_version(
    accepted_versions: list[dict], event: sqlite3.Row
) -> bool:
    return any(
        int(version["company_id"]) == int(event["company_id"])
        and version["source"] == event["source"]
        and version["reporting_scope"] == event["reporting_scope"]
        and version["resolved_source_period"] == event["resolved_source_period"]
        and int(version["parse_id"]) == int(event["parse_id"])
        for version in accepted_versions
    )


def _trendlyne_pilot_reports(conn: sqlite3.Connection) -> list[dict]:
    """Report P03 request accounting, accepted coverage, and stored anomalies."""
    plans = _rows(
        conn,
        """SELECT plan_id, plan_key, pilot_code, end_source_period, month_count,
                  company_count, request_count, batch_size, status,
                  created_at_utc, updated_at_utc
           FROM trendlyne_backfill_plans ORDER BY plan_id""",
    )
    reports: list[dict] = []
    for plan in plans:
        plan_id = int(plan["plan_id"])
        items = _rows(
            conn,
            """SELECT bi.request_position, bi.source_company_id,
                      bi.source_symbol, bi.source_slug,
                      bi.requested_source_period, bi.requested_url,
                      bi.status AS item_status, bi.attempt_count,
                      bi.fetch_id, bi.parse_id, bi.last_error_json,
                      apv.parse_id AS accepted_parse_id
               FROM trendlyne_backfill_items AS bi
               LEFT JOIN accepted_page_versions AS apv
                 ON apv.company_id=bi.company_id
                AND apv.source='trendlyne'
                AND apv.reporting_scope='not_applicable'
                AND apv.resolved_source_period=bi.requested_source_period
               WHERE bi.plan_id=? ORDER BY bi.request_position""",
            (plan_id,),
        )
        anomalies: list[dict] = []
        coverage_mismatches: list[dict] = []
        for item in items:
            item["last_error"] = _json_or_text(item.pop("last_error_json"))
            accepted_parse_id = item["accepted_parse_id"]
            if (item["item_status"] == "accepted") != (accepted_parse_id is not None):
                coverage_mismatches.append(
                    {
                        "source_symbol": item["source_symbol"],
                        "source_period": item["requested_source_period"],
                        "item_status": item["item_status"],
                        "accepted_parse_id": accepted_parse_id,
                    }
                )
            if accepted_parse_id is not None:
                anomalies.extend(
                    {
                        "source_symbol": item["source_symbol"],
                        "source_period": item["requested_source_period"],
                        "parse_id": int(accepted_parse_id),
                        "rule_code": row["rule_code"],
                        "severity": row["severity"],
                        "status": row["status"],
                    }
                    for row in conn.execute(
                        """SELECT rule_code, severity, status
                           FROM validation_results
                           WHERE parse_id=? AND status='failed'
                           ORDER BY validation_id""",
                        (accepted_parse_id,),
                    )
                )
        status_counts = Counter(item["item_status"] for item in items)
        accepted_periods = [
            item["requested_source_period"]
            for item in items
            if item["accepted_parse_id"] is not None
        ]
        missing_periods = [
            item["requested_source_period"]
            for item in items
            if item["accepted_parse_id"] is None
        ]
        reports.append(
            {
                **plan,
                "request_accounting": {
                    "planned_request_count": len(items),
                    "attempt_count": sum(int(item["attempt_count"]) for item in items),
                    "successful_item_count": status_counts.get("accepted", 0),
                    "failed_item_count": status_counts.get("failed", 0),
                    "running_item_count": status_counts.get("running", 0),
                    "pending_item_count": status_counts.get("pending", 0),
                },
                "month_coverage": {
                    "accepted_count": len(accepted_periods),
                    "missing_count": len(missing_periods),
                    "accepted_periods": accepted_periods,
                    "missing_periods": missing_periods,
                    "coverage_status": "complete" if not missing_periods else "incomplete",
                },
                "anomaly_count": len(anomalies),
                "anomalies": anomalies,
                "coverage_pointer_mismatches": coverage_mismatches,
                "items": items,
            }
        )
    return reports


def build_quality_report(
    conn: sqlite3.Connection, *, generated_at_utc: str | None = None
) -> dict:
    """Build one deterministic report over all persisted acceptance events."""
    accepted_versions = _accepted_versions(conn)
    events = conn.execute(
        """SELECT ae.*, c.current_legal_name AS company_name
           FROM acceptance_events AS ae
           JOIN companies AS c ON c.company_id=ae.company_id
           ORDER BY ae.acceptance_id"""
    ).fetchall()
    results: list[dict] = []
    for event in events:
        parse_id = int(event["parse_id"])
        quality = _artifact_quality(conn, parse_id)
        supplemental = _supplemental_artifacts(conn, parse_id)
        manual_review = any(
            row["severity"] == "manual_review"
            for row in quality["validation_failures"]
        ) or any(
            row["severity"] == "manual_review"
            for item in supplemental
            for row in item["validation_failures"]
        )
        result = str(event["action"])
        if result == "rejected" and manual_review:
            result = "manual_review"
        result_payload = {
            "acceptance_id": int(event["acceptance_id"]),
            "result": result,
            "acceptance_action": event["action"],
            "is_current_accepted_version": _is_current_version(
                accepted_versions, event
            ),
            "company": {
                "company_id": int(event["company_id"]),
                "name": event["company_name"],
            },
            "source": event["source"],
            "reporting_scope": event["reporting_scope"],
            "resolved_source_period": event["resolved_source_period"],
            "decided_at_utc": event["accepted_at_utc"],
            "accepted_by": event["accepted_by"],
            "acceptance_mode": event["acceptance_mode"],
            "decision_reason": _json_or_text(event["reason_json"]),
            **quality,
            "supplemental_artifacts": supplemental,
        }
        results.append(result_payload)

    counts = Counter(result["result"] for result in results)
    report = {
        "report_schema_version": QUALITY_REPORT_SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc or _utc_now(),
        "summary": {
            "decision_count": len(results),
            "decision_counts": dict(sorted(counts.items())),
            "current_accepted_version_count": len(accepted_versions),
            "parse_artifact_count": int(
                conn.execute("SELECT COUNT(*) FROM parse_artifacts").fetchone()[0]
            ),
        },
        "accepted_versions": accepted_versions,
        "trendlyne_pilot": _trendlyne_pilot_reports(conn),
        "results": results,
    }
    canonical = json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    report["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    return report


def write_quality_report(
    conn: sqlite3.Connection,
    destination: Path | None = None,
    *,
    generated_at_utc: str | None = None,
) -> Path:
    path = destination or (EXPORTS / "quality-report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            build_quality_report(conn, generated_at_utc=generated_at_utc),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
