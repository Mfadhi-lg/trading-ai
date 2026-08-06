"""D09 company/period/artifact coverage and retry-state export."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from ..config import DOCUMENT_ARTIFACT_COVERAGE_EXPORTS
from ..db.repo import utc_now


ARTIFACT_COVERAGE_SCHEMA_VERSION = "1.0.0"


def _json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    return json.loads(value)


def _period_key(row: sqlite3.Row) -> str:
    if row["event_date"]:
        return f"event:{row['event_date']}"
    if row["period_end"]:
        return f"period:{row['period_end']}"
    if row["fiscal_year"] is not None:
        quarter = f"-q{row['fiscal_quarter']}" if row["fiscal_quarter"] else ""
        return f"fy:{row['fiscal_year']}{quarter}"
    return f"event-key:{row['event_key']}"


def build_artifact_coverage_report(
    conn: sqlite3.Connection,
    *,
    symbols: Iterable[str] = (),
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    requested = tuple(dict.fromkeys(symbol.upper() for symbol in symbols))
    parameters: list[Any] = []
    symbol_filter = ""
    if requested:
        placeholders = ",".join("?" for _ in requested)
        symbol_filter = f"AND identifier.identifier_value IN ({placeholders})"
        parameters.extend(requested)
    rows = conn.execute(
        f"""SELECT expectation.*, event.event_key, event.event_type,
                   event.event_date, event.period_end, event.fiscal_year,
                   event.fiscal_quarter, event.evidence_source,
                   company.current_legal_name,
                   identifier.identifier_value AS symbol
            FROM artifact_expectations AS expectation
            JOIN artifact_events AS event USING (event_id)
            JOIN companies AS company USING (company_id)
            LEFT JOIN security_identifiers AS identifier
              ON identifier.company_id=company.company_id
             AND identifier.identifier_type='NSE' AND identifier.is_current=1
            WHERE 1=1 {symbol_filter}
            ORDER BY company.company_id, event.event_id,
                     expectation.artifact_type, expectation.expectation_id""",
        tuple(parameters),
    ).fetchall()
    if requested:
        found = {row["symbol"] for row in rows}
        missing = sorted(set(requested) - found)
        if missing:
            raise ValueError(
                "No artifact expectations found for symbol(s): " + ", ".join(missing)
            )

    expectations: list[dict[str, Any]] = []
    for row in rows:
        checks = conn.execute(
            """SELECT * FROM artifact_check_events WHERE expectation_id=?
               ORDER BY check_id""",
            (int(row["expectation_id"]),),
        ).fetchall()
        expectations.append(
            {
                "expectation_id": int(row["expectation_id"]),
                "company_id": int(row["company_id"]),
                "symbol": row["symbol"],
                "company_name": row["current_legal_name"],
                "event_id": int(row["event_id"]),
                "event_key": row["event_key"],
                "event_type": row["event_type"],
                "period_key": _period_key(row),
                "event_date": row["event_date"],
                "period_end": row["period_end"],
                "fiscal_year": row["fiscal_year"],
                "fiscal_quarter": row["fiscal_quarter"],
                "artifact_type": row["artifact_type"],
                "expectation_basis": row["expectation_basis"],
                "evidence_source": row["evidence_source"],
                "expected_after_utc": row["expected_after_utc"],
                "availability_status": row["availability_status"],
                "first_checked_at_utc": row["first_checked_at_utc"],
                "last_checked_at_utc": row["last_checked_at_utc"],
                "next_check_at_utc": row["next_check_at_utc"],
                "attempt_count": int(row["attempt_count"]),
                "terminal_reason": row["terminal_reason"],
                "last_error": _json(row["last_error_json"], {}),
                "is_claimed": row["claim_token"] is not None,
                "claim_expires_at_utc": row["claim_expires_at_utc"],
                "schedule": _json(row["schedule_json"], {}),
                "checks": [
                    {
                        "check_id": int(check["check_id"]),
                        "checked_at_utc": check["checked_at_utc"],
                        "source": check["source"],
                        "requested_url": check["requested_url"],
                        "result_status": check["result_status"],
                        "evidence": _json(check["evidence_json"], {}),
                        "discovered_artifact_id": check["discovered_artifact_id"],
                        "error": _json(check["error_json"], {}),
                        "retryable": bool(check["retryable"]),
                        "next_check_at_utc": check["next_check_at_utc"],
                    }
                    for check in checks
                ],
            }
        )

    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for item in expectations:
        key = (item["company_id"], item["event_id"])
        group = grouped.setdefault(
            key,
            {
                "company_id": item["company_id"],
                "symbol": item["symbol"],
                "company_name": item["company_name"],
                "event_id": item["event_id"],
                "event_key": item["event_key"],
                "period_key": item["period_key"],
                "artifact_types": {},
            },
        )
        group["artifact_types"][item["artifact_type"]] = {
            "availability_status": item["availability_status"],
            "attempt_count": item["attempt_count"],
            "next_check_at_utc": item["next_check_at_utc"],
            "terminal_reason": item["terminal_reason"],
        }

    counts = Counter(item["availability_status"] for item in expectations)
    pending_count = sum(item["next_check_at_utc"] is not None for item in expectations)
    review_count = sum(
        item["next_check_at_utc"] is None
        and item["availability_status"] in {"unknown", "discovery_failed", "inaccessible"}
        for item in expectations
    )
    payload: dict[str, Any] = {
        "schema_version": ARTIFACT_COVERAGE_SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc or utc_now(),
        "symbols": list(requested),
        "coverage_status": "complete" if expectations else "empty",
        "queue_status": (
            "needs_review" if review_count else "active" if pending_count else "settled"
        ),
        "summary": {
            "company_count": len({item["company_id"] for item in expectations}),
            "event_count": len({item["event_id"] for item in expectations}),
            "expectation_count": len(expectations),
            "check_event_count": sum(len(item["checks"]) for item in expectations),
            "pending_retry_count": pending_count,
            "review_count": review_count,
            "status_counts": dict(sorted(counts.items())),
        },
        "period_coverage": list(grouped.values()),
        "expectations": expectations,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    payload["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def write_artifact_coverage_report(
    conn: sqlite3.Connection,
    output: Path | None = None,
    *,
    symbols: Iterable[str] = (),
    generated_at_utc: str | None = None,
) -> Path:
    payload = build_artifact_coverage_report(
        conn, symbols=symbols, generated_at_utc=generated_at_utc
    )
    requested = tuple(dict.fromkeys(symbol.upper() for symbol in symbols))
    name = "-".join(requested) if requested else "all"
    target = output or DOCUMENT_ARTIFACT_COVERAGE_EXPORTS / f"{name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return target
