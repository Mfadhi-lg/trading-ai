"""exchange-bulk-enqueue

One-shot bootstrap tool that queues every company in exchange_company_registry that
does not yet have an entry in screener_company_queue.

To satisfy strict database constraints (foreign keys, CHECK constraints, and joins
used in coverage reports):
  1. We create a placeholder filing in exchange_result_filings for period "1970-01".
  2. We insert the queue item with origin="exchange_filing" linked to that placeholder.
  3. We add a corresponding entry to exchange_screener_enqueues.

The normal scheduler-driven flow (enqueue_result_filings) remains standard going
forward: it only enqueues when a company publishes a new result and advances.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from scrapper.db.exchange_repo import resolve_screener_path


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_bulk_enqueue(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    """Enqueue all registry companies not yet in the screener queue.

    Returns a summary dict suitable for JSON serialisation.
    """
    now = _now_utc()
    counts: dict[str, int] = {
        "queued": 0,
        "skipped_already_queued": 0,
        "skipped_conflict": 0,
        "skipped_no_security": 0,
    }

    # Fetch any valid snapshot_id to satisfy foreign key constraint on filings
    snapshot = conn.execute("SELECT snapshot_id FROM exchange_source_snapshots LIMIT 1").fetchone()
    if snapshot is None:
        raise RuntimeError("No exchange source snapshots found in the database. Run masters refresh first.")
    snapshot_id = int(snapshot["snapshot_id"])

    # All registry companies that have at least one non-conflicted, current security
    companies = conn.execute("""
        SELECT DISTINCT ecl.registry_company_id, ecr.isin, ecr.canonical_name_raw
        FROM exchange_company_links ecl
        JOIN exchange_securities es USING (exchange_security_id)
        JOIN exchange_company_registry ecr ON ecr.registry_company_id = ecl.registry_company_id
        WHERE ecl.is_current = 1
          AND es.verification_status != 'conflict'
    """).fetchall()

    for row in companies:
        registry_company_id = int(row["registry_company_id"])
        isin = str(row["isin"])
        company_name = str(row["canonical_name_raw"])

        # Pick the best security for this company (NSE preferred over BSE)
        security = conn.execute("""
            SELECT es.* FROM exchange_securities es
            JOIN exchange_company_links ecl USING (exchange_security_id)
            WHERE ecl.registry_company_id = ?
              AND ecl.is_current = 1
              AND es.verification_status != 'conflict'
            ORDER BY
              CASE es.exchange WHEN 'NSE' THEN 0 ELSE 1 END,
              es.is_present DESC,
              CASE es.segment WHEN 'equity' THEN 0 ELSE 1 END,
              es.exchange_security_id
            LIMIT 1
        """, (registry_company_id,)).fetchone()

        if security is None:
            counts["skipped_no_security"] += 1
            continue

        exchange = str(security["exchange"])
        exchange_key = str(security["exchange_key"])

        for scope in ("standalone",):
            resolution = resolve_screener_path(
                conn,
                exchange=exchange,
                exchange_key=exchange_key,
                reporting_scope=scope,
            )
            if resolution["decision"] != "resolved":
                counts["skipped_conflict"] += 1
                continue

            path = str(resolution["company_path_normalized"])
            path_basis = str(resolution["path_basis"])

            # consolidated scope produces a path ending in /consolidated/
            # if it doesn't, skip — this company only has a standalone page
            if scope == "consolidated" and not path.endswith("/consolidated/"):
                continue

            # Check if this company/scope is already in the queue
            existing = conn.execute(
                "SELECT 1 FROM screener_company_queue "
                "WHERE company_path_normalized=? AND reporting_scope=?",
                (path, scope),
            ).fetchone()

            if existing:
                counts["skipped_already_queued"] += 1
                continue

            if dry_run:
                counts["queued"] += 1
                continue

            with conn:
                # 1. Create unique placeholder filing key
                filing_key = f"bulk_{scope}_{isin}"

                # 2. Insert placeholder filing
                conn.execute("""
                    INSERT OR IGNORE INTO exchange_result_filings
                    (snapshot_id, exchange, filing_key, exchange_key, isin,
                     company_name_raw, result_period, period_end_date, reporting_scope,
                     broadcast_raw, broadcast_at_utc, raw_row_json, discovered_at_utc)
                    VALUES (?, ?, ?, ?, ?, ?, '1970-01', '1970-01-01', ?, '', ?, '{}', ?)
                """, (snapshot_id, exchange, filing_key, exchange_key, isin, company_name, scope, now, now))

                filing = conn.execute(
                    "SELECT filing_id FROM exchange_result_filings WHERE exchange=? AND filing_key=?",
                    (exchange, filing_key)
                ).fetchone()
                filing_id = int(filing["filing_id"])

                # 3. Enqueue the company with origin='exchange_filing'
                cursor = conn.execute("""
                    INSERT OR IGNORE INTO screener_company_queue
                    (origin, candidate_id, exchange_filing_id,
                     company_path_normalized, reporting_scope, result_period,
                     queue_reason, status, attempt_count,
                     next_attempt_at_utc, created_at_utc, updated_at_utc)
                    VALUES ('exchange_filing', NULL, ?, ?, ?, '1970-01',
                            'new_company', 'pending', 0, ?, ?, ?)
                """, (filing_id, path, scope, now, now, now))

                if cursor.rowcount == 1:
                    queue_item_id = int(cursor.lastrowid)

                    # 4. Add queue event
                    conn.execute("""
                        INSERT INTO screener_company_queue_events
                        (queue_item_id, event_at_utc, from_status, to_status, evidence_json)
                        VALUES (?, ?, NULL, 'pending', ?)
                    """, (
                        queue_item_id, now,
                        json.dumps({
                            "origin": "bulk_enqueue",
                            "queue_reason": "new_company",
                            "exchange": exchange,
                            "exchange_key": exchange_key,
                            "path_basis": path_basis,
                            "filing_id": filing_id,
                        }),
                    ))

                    # 5. Add to exchange_screener_enqueues to align decision state
                    conn.execute("""
                        INSERT OR IGNORE INTO exchange_screener_enqueues
                        (filing_id, decision, decision_reason, company_path_normalized,
                         reporting_scope, result_period, path_basis, registry_company_id,
                         queue_item_id, created_at_utc)
                        VALUES (?, 'queued', 'bulk_enqueue', ?, ?, '1970-01', ?, ?, ?, ?)
                    """, (filing_id, path, scope, path_basis, registry_company_id, queue_item_id, now))

                    counts["queued"] += 1
                else:
                    counts["skipped_already_queued"] += 1

    return {"dry_run": dry_run, "counts": counts}
