"""Deterministic discovery, queue and acceptance coverage export."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import EXPORTS


SCHEMA_VERSION = "1.0.0"


def build_screener_discovery_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for row in conn.execute(
        """SELECT run.*, blob.content_sha256
           FROM screener_screen_runs AS run
           JOIN response_blobs AS blob USING (blob_id)
           ORDER BY observed_at_utc, screen_run_id"""
    ):
        item = dict(row)
        candidates = [
            dict(candidate)
            for candidate in conn.execute(
                """SELECT candidate_id, source_position, screen_row_company_id,
                          company_name_raw, company_href_raw,
                          company_path_normalized, reporting_scope_hint,
                          last_result_date_display, result_period
                   FROM screener_result_candidates WHERE screen_run_id=?
                   ORDER BY source_position""",
                (row["screen_run_id"],),
            )
        ]
        item["candidates_by_result_period"] = dict(
            sorted(Counter(candidate["result_period"] for candidate in candidates).items())
        )
        item["candidates_by_reporting_scope"] = dict(
            sorted(Counter(candidate["reporting_scope_hint"] for candidate in candidates).items())
        )
        runs.append(item)
    queue = [
        dict(row)
        for row in conn.execute(
            """SELECT queue.queue_item_id, queue.origin, queue.exchange_filing_id,
                      queue.company_path_normalized,
                      queue.reporting_scope, queue.result_period,
                      queue.queue_reason, queue.status, queue.attempt_count,
                      queue.next_attempt_at_utc, queue.accepted_parse_id,
                      queue.accepted_export_path,
                      queue.last_error_json, queue.created_at_utc,
                      queue.updated_at_utc,
                      COALESCE(candidate.company_name_raw, filing.company_name_raw)
                        AS company_name_raw
               FROM screener_company_queue AS queue
               LEFT JOIN screener_result_candidates AS candidate USING (candidate_id)
               LEFT JOIN exchange_result_filings AS filing
                 ON filing.filing_id = queue.exchange_filing_id
               ORDER BY queue.queue_item_id"""
        )
    ]
    status_counts = dict(sorted(Counter(item["status"] for item in queue).items()))
    origin_counts = dict(sorted(Counter(item["origin"] for item in queue).items()))
    reason_counts = dict(sorted(Counter(item["queue_reason"] for item in queue).items()))
    coverage_counts = dict(sorted(Counter(item["coverage_status"] for item in runs).items()))
    failures = [
        item for item in queue
        if item["status"] in {"failed_retryable", "failed_terminal", "manual_review"}
    ]
    candidate_decisions = Counter(
        {"new": 0, "advanced": 0, "unchanged": 0, "ambiguous": 0, "stale": 0}
    )
    for candidate in conn.execute(
        """SELECT candidate.company_path_normalized,
                  candidate.reporting_scope_hint, candidate.result_period,
                  state.latest_accepted_result_period, queue.queue_reason,
                  screen.coverage_status
           FROM screener_result_candidates AS candidate
           JOIN screener_screen_runs AS screen USING (screen_run_id)
           LEFT JOIN screener_company_collection_state AS state
             ON state.company_path_normalized=candidate.company_path_normalized
            AND state.reporting_scope=candidate.reporting_scope_hint
           LEFT JOIN screener_company_queue AS queue
             ON queue.company_path_normalized=candidate.company_path_normalized
            AND queue.reporting_scope=candidate.reporting_scope_hint
            AND queue.result_period=candidate.result_period"""
    ):
        if candidate["coverage_status"] != "complete":
            candidate_decisions["incomplete_coverage"] += 1
        elif candidate["queue_reason"] == "new_company":
            candidate_decisions["new"] += 1
        elif candidate["queue_reason"] == "result_period_advanced":
            candidate_decisions["advanced"] += 1
        elif (
            candidate["latest_accepted_result_period"] is not None
            and candidate["result_period"] < candidate["latest_accepted_result_period"]
        ):
            candidate_decisions["stale"] += 1
        else:
            candidate_decisions["unchanged"] += 1
    host_outcomes = Counter()
    for failure in failures:
        detail = str(failure["last_error_json"]).lower()
        if "status=429" in detail:
            host_outcomes["rate_limited"] += 1
        if "status=401" in detail or "login" in detail:
            host_outcomes["session_expired"] += 1
        if "status=403" in detail or "access-denied" in detail or '"stop_host":true' in detail:
            host_outcomes["blocked_host"] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "coverage_status": (
            "none" if not runs else "partial" if any(item["coverage_status"] != "complete" for item in runs) else "complete"
        ),
        "summary": {
            "screen_run_count": len(runs),
            "coverage_counts": coverage_counts,
            "queue_item_count": len(queue),
            "queue_status_counts": status_counts,
            "queue_origin_counts": origin_counts,
            "queue_reason_counts": reason_counts,
            "candidate_decision_counts": dict(sorted(candidate_decisions.items())),
            "host_outcome_counts": dict(sorted(host_outcomes.items())),
            "failure_count": len(failures),
        },
        "screen_runs": runs,
        "queue": queue,
        "failures": failures,
    }


def write_screener_discovery_coverage(
    conn: sqlite3.Connection, output: Path | None = None
) -> Path:
    output = output or EXPORTS / "screener-discovery-coverage.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_screener_discovery_coverage(conn), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output
