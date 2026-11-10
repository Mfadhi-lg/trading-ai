"""Deterministic exchange universe, change and result-discovery coverage."""

from __future__ import annotations
from ..db.repository_backend import statement

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..config import EXPORTS


SCHEMA_VERSION = "1.1.0"
# A usable universe needs at least the two full equity masters; the SME, change
# and result feeds extend it but cannot substitute for it.
REQUIRED_DATASETS = ("nse_equity_master", "bse_security_master")


def _counts(conn: sqlite3.Connection, sql: str, *parameters: Any) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in conn.execute(sql, parameters)}


def build_exchange_universe_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    snapshots = [
        dict(row)
        for row in conn.execute(
            statement(conn, """SELECT snapshot.dataset, snapshot.exchange,
                      COUNT(*) AS snapshot_count,
                      MIN(snapshot.effective_date) AS first_effective_date,
                      MAX(snapshot.effective_date) AS latest_effective_date,
                      SUM(snapshot.accepted_row_count) AS accepted_rows,
                      SUM(snapshot.rejected_row_count) AS rejected_rows,
                      SUM(snapshot.validation_status = 'failed') AS failed_snapshots
               FROM exchange_source_snapshots AS snapshot
               GROUP BY snapshot.dataset, snapshot.exchange
               ORDER BY snapshot.dataset""", "SELECT snapshot.dataset, snapshot.exchange,\n                      COUNT(*) AS snapshot_count,\n                      MIN(snapshot.effective_date) AS first_effective_date,\n                      MAX(snapshot.effective_date) AS latest_effective_date,\n                      SUM(snapshot.accepted_row_count) AS accepted_rows,\n                      SUM(snapshot.rejected_row_count) AS rejected_rows,\n                      SUM(CASE WHEN snapshot.validation_status = 'failed' THEN 1 ELSE 0 END) AS failed_snapshots\n               FROM exchange_source_snapshots AS snapshot\n               GROUP BY snapshot.dataset, snapshot.exchange\n               ORDER BY snapshot.dataset")
        )
    ]
    present_datasets = {item["dataset"] for item in snapshots}
    securities = _counts(
        conn,
        """SELECT exchange || '_' || segment || '_' || current_status, COUNT(*)
           FROM exchange_securities GROUP BY 1 ORDER BY 1""",
    )
    presence = _counts(
        conn,
        """SELECT CASE is_present WHEN 1 THEN 'present' ELSE 'absent' END, COUNT(*)
           FROM exchange_securities GROUP BY 1 ORDER BY 1""",
    )
    events = _counts(
        conn,
        "SELECT event_type, COUNT(*) FROM exchange_security_events GROUP BY 1 ORDER BY 1",
    )
    rejected = _counts(
        conn,
        """SELECT reason_code, COUNT(*) FROM exchange_source_rejected_rows
           GROUP BY 1 ORDER BY 1""",
    )
    decisions = _counts(
        conn,
        "SELECT decision, COUNT(*) FROM exchange_screener_enqueues GROUP BY 1 ORDER BY 1",
    )
    filings_by_period = _counts(
        conn,
        """SELECT result_period, COUNT(*) FROM exchange_result_filings
           GROUP BY 1 ORDER BY 1""",
    )
    filings_by_scope = _counts(
        conn,
        """SELECT reporting_scope, COUNT(*) FROM exchange_result_filings
           GROUP BY 1 ORDER BY 1""",
    )
    queue_origins = _counts(
        conn,
        """SELECT origin || '_' || status, COUNT(*) FROM screener_company_queue
           GROUP BY 1 ORDER BY 1""",
    )
    registry_row = conn.execute(
        """SELECT COUNT(*) AS companies,
                  (SELECT COUNT(*) FROM exchange_company_links WHERE is_current=1) AS current_links,
                  (SELECT COUNT(*) FROM exchange_securities
                    WHERE current_isin IS NULL) AS securities_without_isin
           FROM exchange_company_registry"""
    ).fetchone()
    multi_exchange = int(
        conn.execute(
            """SELECT COUNT(*) FROM (
                 SELECT link.registry_company_id
                 FROM exchange_company_links AS link
                 JOIN exchange_securities AS security USING (exchange_security_id)
                 WHERE link.is_current=1
                 GROUP BY link.registry_company_id
                 HAVING COUNT(DISTINCT security.exchange) > 1)"""
        ).fetchone()[0]
    )
    watermarks = [dict(row) for row in conn.execute(
        """SELECT exchange, dataset, last_broadcast_at_utc, last_snapshot_id,
                  updated_at_utc FROM exchange_result_watermarks
           ORDER BY exchange, dataset"""
    )]
    review = [dict(row) for row in conn.execute(
        """SELECT decision.filing_id, decision.decision_reason, filing.exchange,
                  filing.exchange_key, filing.company_name_raw, filing.result_period,
                  filing.reporting_scope, filing.broadcast_at_utc
           FROM exchange_screener_enqueues AS decision
           JOIN exchange_result_filings AS filing USING (filing_id)
           WHERE decision.decision='manual_review'
           ORDER BY filing.broadcast_at_utc, decision.filing_id"""
    )]
    collisions = [dict(row) for row in conn.execute(
        """SELECT collision_id, exchange, filing_key, existing_filing_id,
                  snapshot_id, raw_sha256, first_seen_at_utc
           FROM exchange_result_filing_collisions
           WHERE review_status='pending'
           ORDER BY first_seen_at_utc, collision_id"""
    )]
    skipped_later_filings = [dict(row) for row in conn.execute(
        """SELECT filing.filing_id, filing.exchange, filing.exchange_key,
                  filing.result_period, filing.reporting_scope,
                  filing.broadcast_at_utc, decision.decision,
                  decision.decision_reason
           FROM exchange_screener_enqueues decision
           JOIN exchange_result_filings filing USING (filing_id)
           WHERE decision.decision IN ('already_accepted','stale')
             AND EXISTS (SELECT 1 FROM exchange_result_filings prior
               WHERE prior.exchange=filing.exchange
                 AND prior.exchange_key=filing.exchange_key
                 AND prior.reporting_scope=filing.reporting_scope
                 AND prior.result_period=filing.result_period
                 AND (prior.broadcast_at_utc<filing.broadcast_at_utc
                   OR (prior.broadcast_at_utc=filing.broadcast_at_utc
                     AND prior.filing_id<filing.filing_id)))
           ORDER BY filing.broadcast_at_utc, filing.filing_id"""
    )]
    unmatched_changes = [dict(row) for row in conn.execute(
        """SELECT exchange, change_kind, old_value, new_value, effective_date
           FROM exchange_symbol_changes WHERE applied_status='unmatched'
           ORDER BY effective_date, symbol_change_id"""
    )]
    failed_snapshots = sum(int(item["failed_snapshots"] or 0) for item in snapshots)
    missing = [dataset for dataset in REQUIRED_DATASETS if dataset not in present_datasets]
    if not snapshots:
        coverage_status = "none"
    elif missing or failed_snapshots or review or collisions or skipped_later_filings:
        coverage_status = "partial"
    else:
        coverage_status = "complete"
    return {
        "schema_version": SCHEMA_VERSION,
        "coverage_status": coverage_status,
        "missing_required_datasets": missing,
        "summary": {
            "snapshot_dataset_count": len(snapshots),
            "failed_snapshot_count": failed_snapshots,
            "security_counts": securities,
            "security_presence_counts": presence,
            "security_event_counts": events,
            "rejected_row_counts": rejected,
            "registry_company_count": int(registry_row["companies"]),
            "current_link_count": int(registry_row["current_links"]),
            "securities_without_isin": int(registry_row["securities_without_isin"]),
            "multi_exchange_company_count": multi_exchange,
            "filing_count": sum(filings_by_period.values()),
            "filings_by_result_period": filings_by_period,
            "filings_by_reporting_scope": filings_by_scope,
            "enqueue_decision_counts": decisions,
            "queue_counts_by_origin_status": queue_origins,
            "manual_review_count": len(review),
            "filing_identity_collision_count": len(collisions),
            "historically_skipped_later_filing_count": len(skipped_later_filings),
            "unmatched_change_count": len(unmatched_changes),
        },
        "snapshots": snapshots,
        "watermarks": watermarks,
        "manual_review": review,
        "filing_identity_collisions": collisions,
        "historically_skipped_later_filings": skipped_later_filings,
        "unmatched_changes": unmatched_changes,
    }


def write_exchange_universe_coverage(
    conn: sqlite3.Connection, output: Path | None = None
) -> Path:
    output = output or EXPORTS / "exchange-universe-coverage.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_exchange_universe_coverage(conn), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output
