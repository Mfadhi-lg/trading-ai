"""Queue only active exchange-master companies with no accepted Screener parse."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _name_key(value: str) -> str:
    """Conservative name key used only to refuse a potentially duplicate insert."""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def run_unaccepted_backfill(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    """Requeue missing captures without touching a master company already accepted.

    The target is deliberately narrower than every company that has no queue
    acceptance.  It is the active ISIN-linked exchange universe whose *current
    NSE symbol or BSE code* has no exact Screener/source-identifier connection.
    This preserves the established 1,448-identity gap and does not revive the
    old below-scope population just because it lacks a queue-state pointer.
    """
    snapshot = conn.execute(
        "SELECT snapshot_id FROM exchange_source_snapshots ORDER BY snapshot_id LIMIT 1"
    ).fetchone()
    if snapshot is None:
        raise RuntimeError("No exchange source snapshot is available")
    snapshot_id = int(snapshot["snapshot_id"])
    now = _now()
    rows = conn.execute(
        """WITH active AS (
                 SELECT r.registry_company_id, r.isin, r.canonical_name_raw,
                        MAX(CASE WHEN s.exchange='NSE' THEN s.nse_symbol END) AS nse,
                        MAX(CASE WHEN s.exchange='BSE' THEN s.bse_scrip_code END) AS bse
                   FROM exchange_company_registry r
                   JOIN exchange_company_links l
                     ON l.registry_company_id=r.registry_company_id AND l.is_current=1
                   JOIN exchange_securities s ON s.exchange_security_id=l.exchange_security_id
                  WHERE s.is_present=1 AND s.current_status='active'
                    AND s.verification_status!='conflict'
                  GROUP BY r.registry_company_id
             )
             SELECT * FROM active a
              WHERE NOT EXISTS (
                    SELECT 1 FROM source_identifiers x
                     WHERE x.source='screener' AND x.source_symbol IN (a.nse, a.bse)
              )
                AND NOT EXISTS (
                    SELECT 1 FROM security_identifiers i
                     WHERE i.is_current=1 AND i.identifier_value IN (a.nse, a.bse)
              )
              ORDER BY registry_company_id"""
    ).fetchall()
    counts = {
        "exact_unmatched_master_identities": 0,
        "already_accepted": 0,
        "queued_new": 0,
        "requeued_failed": 0,
        "already_pending": 0,
        "manual_review": 0,
        "name_collision_manual_review": 0,
    }
    accepted_names: dict[str, set[int]] = {}
    for company in conn.execute(
        """SELECT DISTINCT c.company_id, c.current_legal_name
             FROM companies c
             JOIN accepted_page_versions a ON a.company_id=c.company_id
            WHERE a.source='screener'"""
    ):
        accepted_names.setdefault(_name_key(str(company["current_legal_name"])), set()).add(
            int(company["company_id"])
        )
    for row in rows:
        registry_id = int(row["registry_company_id"])
        counts["exact_unmatched_master_identities"] += 1
        nse = str(row["nse"] or "").strip()
        bse = str(row["bse"] or "").strip()
        if not nse and not bse:
            counts["manual_review"] += 1
            continue
        identifier = nse or bse
        path = f"/company/{identifier}/"
        exchange = "NSE" if nse else "BSE"
        exchange_key = identifier
        accepted = conn.execute(
            """SELECT 1 FROM screener_company_queue
                 WHERE company_path_normalized=? AND reporting_scope='standalone'
                   AND status='accepted' LIMIT 1""",
            (path,),
        ).fetchone()
        if accepted is not None:
            counts["already_accepted"] += 1
            continue
        queued = conn.execute(
            """SELECT queue_item_id, status FROM screener_company_queue
                 WHERE company_path_normalized=? AND reporting_scope='standalone'
                 ORDER BY queue_item_id DESC LIMIT 1""",
            (path,),
        ).fetchone()
        if queued is not None:
            status = str(queued["status"])
            if status in {"pending", "claimed", "running"}:
                counts["already_pending"] += 1
                continue
            counts["requeued_failed"] += 1
            if dry_run:
                continue
            queue_id = int(queued["queue_item_id"])
            with conn:
                conn.execute(
                    """UPDATE screener_company_queue SET status='pending',
                           next_attempt_at_utc=?, claim_token=NULL, claimed_at_utc=NULL,
                           claim_expires_at_utc=NULL, updated_at_utc=?
                         WHERE queue_item_id=?""",
                    (now, now, queue_id),
                )
                conn.execute(
                    """INSERT INTO screener_company_queue_events
                       (queue_item_id,event_at_utc,from_status,to_status,evidence_json)
                       VALUES (?,? ,?,'pending',?)""",
                    (queue_id, now, status, json.dumps({"reason": "active_master_unaccepted_backfill"})),
                )
            continue
        # A path-less historical acceptance can otherwise become a second
        # company row.  Do not make a name-based merge (that would be unsafe);
        # leave it visible for explicit identity resolution instead.
        if _name_key(str(row["canonical_name_raw"])) in accepted_names:
            counts["name_collision_manual_review"] += 1
            continue
        counts["queued_new"] += 1
        if dry_run:
            continue
        isin = str(row["isin"])
        name = str(row["canonical_name_raw"])
        with conn:
            filing_key = f"master_backfill_standalone_{isin}"
            conn.execute(
                """INSERT OR IGNORE INTO exchange_result_filings
                   (snapshot_id,exchange,filing_key,exchange_key,isin,company_name_raw,
                    result_period,period_end_date,reporting_scope,broadcast_raw,
                    broadcast_at_utc,raw_row_json,discovered_at_utc)
                   VALUES (?,?,?,?,?,?, '1970-01','1970-01-01','standalone','',?,'{}',?)""",
                (snapshot_id, exchange, filing_key, exchange_key, isin, name, now, now),
            )
            filing_id = int(conn.execute(
                "SELECT filing_id FROM exchange_result_filings WHERE exchange=? AND filing_key=?",
                (exchange, filing_key),
            ).fetchone()["filing_id"])
            cursor = conn.execute(
                """INSERT INTO screener_company_queue
                   (origin,candidate_id,exchange_filing_id,company_path_normalized,
                    reporting_scope,result_period,queue_reason,status,attempt_count,
                    next_attempt_at_utc,created_at_utc,updated_at_utc)
                   VALUES ('exchange_filing',NULL,? ,?,'standalone','1970-01',
                           'new_company','pending',0,?,?,?)""",
                (filing_id, path, now, now, now),
            )
            queue_id = int(cursor.lastrowid)
            conn.execute(
                """INSERT INTO screener_company_queue_events
                   (queue_item_id,event_at_utc,from_status,to_status,evidence_json)
                   VALUES (?, ?, NULL, 'pending', ?)""",
                (queue_id, now, json.dumps({"reason": "active_master_unaccepted_backfill"})),
            )
            conn.execute(
                """INSERT OR IGNORE INTO exchange_screener_enqueues
                   (filing_id,decision,decision_reason,company_path_normalized,
                    reporting_scope,result_period,path_basis,registry_company_id,
                    queue_item_id,created_at_utc)
                   VALUES (?,'queued','active_master_unaccepted_backfill',?,
                           'standalone','1970-01',?,?,?,?)""",
                (filing_id, path, "nse_symbol" if nse else "bse_scrip_code", registry_id, queue_id, now),
            )
    return {"dry_run": dry_run, "counts": counts}
