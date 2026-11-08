"""Read-only historical financial observation coverage audit.

The period universe is inferred from periods present in each company's statement
section. This deliberately avoids inventing fiscal periods the source never
published or the acquisition pipeline never captured.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from ..config import EXPORTS


def _metric_key(row: dict) -> str:
    code = row.get("line_item_code")
    if code:
        return f"code:{code}"
    label = " ".join(str(row.get("line_item_raw") or "").casefold().split())
    return f"label:{label}"


def _period_key(row: dict) -> tuple[str, str]:
    period_type = str(row.get("period_type") or "unknown")
    period = str(row.get("period_end") or row.get("period_label_raw") or "unknown")
    return period_type, period


def _value_status(row: dict | None) -> str:
    if row is None:
        return "MISSING_OBSERVATION"
    missing_kind = str(row.get("missing_kind") or "").strip().casefold()
    if missing_kind:
        if "unavailable" in missing_kind or "not_available" in missing_kind:
            return "UNAVAILABLE"
        return "MISSING_VALUE"
    value = row.get("value_decimal")
    if value is None:
        return "MISSING_VALUE"
    try:
        return "PRESENT_ZERO" if Decimal(str(value)) == 0 else "PRESENT"
    except (InvalidOperation, ValueError):
        return "MISSING_VALUE"


def build_financial_coverage_report(
    conn: sqlite3.Connection, symbols: Iterable[str] = ()
) -> dict:
    """Build a deterministic company/statement/metric/period coverage matrix.

    The caller must provide a read-only connection. This function issues SELECTs
    only and does not initialize schema, repair labels, refresh sources or mutate
    application state.
    """
    wanted = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
    query = """
        SELECT apv.company_id, c.current_legal_name, si.source_symbol,
               apv.parse_id, apv.resolved_source_period, apv.accepted_at_utc,
               fo.financial_observation_id, fo.reporting_scope, fo.section_code,
               fo.line_item_code, fo.line_item_raw, fo.period_label_raw,
               fo.period_end, fo.period_type, fo.fiscal_year,
               fo.display_value, fo.value_decimal, fo.unit_code,
               fo.scale_multiplier, fo.missing_kind
          FROM accepted_page_versions AS apv
          JOIN companies AS c ON c.company_id = apv.company_id
          JOIN source_identifiers AS si
            ON si.company_id = apv.company_id AND si.source = apv.source
          JOIN financial_observations AS fo ON fo.parse_id = apv.parse_id
         WHERE apv.source = 'screener'
           AND apv.reporting_scope = 'standalone'
           AND fo.reporting_scope = 'standalone'
         ORDER BY apv.company_id, fo.section_code, fo.period_type,
                  fo.period_end, fo.line_item_code, fo.line_item_raw,
                  apv.accepted_at_utc DESC, apv.parse_id DESC
    """
    selected = []
    for raw in conn.execute(query):
        row = dict(raw)
        symbol = str(row.get("source_symbol") or "").upper()
        if wanted and symbol not in wanted:
            continue
        selected.append(row)
    found = {str(row.get("source_symbol") or "").upper() for row in selected}
    missing_symbols = sorted(wanted - found)
    if missing_symbols:
        raise ValueError(f"No accepted standalone financial observations for: {missing_symbols}")

    # Keep the newest accepted representation of a metric-period cell.
    cells: dict[tuple, dict] = {}
    periods: dict[tuple, dict[tuple[str, str], dict]] = defaultdict(dict)
    metrics: dict[tuple, dict[str, dict]] = defaultdict(dict)
    companies: dict[int, dict] = {}
    for row in selected:
        cid = int(row["company_id"])
        companies[cid] = {
            "company_id": cid,
            "company_name": row["current_legal_name"],
            "symbol": row["source_symbol"],
        }
        period_type = str(row["period_type"])
        section_key = (cid, str(row["section_code"]), period_type)
        period_key = _period_key(row)
        periods[section_key].setdefault(period_key, {
            "period_type": period_key[0],
            "period_end": row.get("period_end"),
            "period_label": row.get("period_label_raw"),
            "fiscal_year": row.get("fiscal_year"),
        })
        metric_key = _metric_key(row)
        metrics[section_key].setdefault(metric_key, {
            "metric_key": metric_key,
            "metric_code": row.get("line_item_code"),
            "metric_label": row.get("line_item_raw"),
            "unit_code": row.get("unit_code"),
            "scale_multiplier": row.get("scale_multiplier"),
        })
        cell_key = (cid, str(row["section_code"]), period_key, metric_key)
        cells.setdefault(cell_key, row)

    report_rows = []
    for (cid, section, period_type), period_map in sorted(periods.items()):
        for period_key, period_info in sorted(period_map.items()):
            for metric_key, metric_info in sorted(metrics[(cid, section, period_type)].items()):
                observation = cells.get((cid, section, period_key, metric_key))
                status = _value_status(observation)
                report_rows.append({
                    **companies[cid],
                    "section_code": section,
                    **metric_info,
                    **period_info,
                    "status": status,
                    "missing_kind": observation.get("missing_kind") if observation else None,
                    "display_value": observation.get("display_value") if observation else None,
                    "value_decimal": observation.get("value_decimal") if observation else None,
                    "parse_id": observation.get("parse_id") if observation else None,
                    "accepted_at_utc": observation.get("accepted_at_utc") if observation else None,
                })

    report_rows.sort(key=lambda r: (
        str(r["symbol"] or ""), r["section_code"], r["period_type"],
        str(r["period_end"] or r["period_label"] or ""), r["metric_key"],
    ))
    counts = Counter(row["status"] for row in report_rows)
    total = len(report_rows)
    present = counts["PRESENT"] + counts["PRESENT_ZERO"]
    return {
        "report_version": "financial-coverage-v1",
        "source": "screener",
        "reporting_scope": "standalone",
        "read_only": True,
        "period_universe_basis": (
            "Observed periods per company, statement section and period type; "
            "the audit does not infer periods absent from every source row."
        ),
        "summary": {
            "company_count": len(companies),
            "statement_section_count": len({r["section_code"] for r in report_rows}),
            "metric_count": len({(r["company_id"], r["section_code"], r["metric_key"]) for r in report_rows}),
            "metric_period_cells": total,
            "present_count": present,
            "coverage_ratio": round(present / total, 6) if total else 0.0,
            "status_counts": dict(sorted(counts.items())),
        },
        "rows": report_rows,
    }


def write_financial_coverage_report(
    report: dict,
    *,
    json_path: Path | None = None,
    csv_path: Path | None = None,
) -> tuple[Path, Path]:
    json_path = Path(json_path or EXPORTS / "financial-coverage-audit.json")
    csv_path = Path(csv_path or EXPORTS / "financial-coverage-audit.csv")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    columns = [
        "company_id", "company_name", "symbol", "section_code", "metric_key",
        "metric_code", "metric_label", "period_type", "period_end",
        "period_label", "fiscal_year", "status", "missing_kind",
        "display_value", "value_decimal", "unit_code", "scale_multiplier",
        "parse_id", "accepted_at_utc",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["rows"])
    return json_path, csv_path


def run_financial_coverage_audit(
    conn: sqlite3.Connection,
    *,
    symbols: Iterable[str] = (),
    json_path: Path | None = None,
    csv_path: Path | None = None,
) -> dict:
    report = build_financial_coverage_report(conn, symbols)
    json_output, csv_output = write_financial_coverage_report(
        report, json_path=json_path, csv_path=csv_path
    )
    return {
        "summary": report["summary"],
        "json_output": str(json_output.resolve()),
        "csv_output": str(csv_output.resolve()),
        "report_version": report["report_version"],
    }
