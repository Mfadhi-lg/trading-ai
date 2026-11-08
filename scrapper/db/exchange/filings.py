"""Exchange result filing provenance, identity resolution and Screener enqueues."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ...sources.exchange.result_filings import ParsedResultFilings
from ...sources.screener.result_screen import canonical_company_path

def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))

def ingest_result_filings(
    conn: sqlite3.Connection,
    parsed: ParsedResultFilings,
    *,
    snapshot_id: int,
    now_utc: str,
) -> dict[str, Any]:
    """Store filings idempotently and advance the broadcast watermark."""
    previous = conn.execute(
        """SELECT last_broadcast_at_utc FROM exchange_result_watermarks
           WHERE exchange=? AND dataset=?""",
        (parsed.exchange, parsed.dataset),
    ).fetchone()
    watermark = str(previous["last_broadcast_at_utc"]) if previous else None
    counts = {"stored": 0, "already_stored": 0, "identity_collisions": 0,
              "after_watermark": 0, "at_or_before_watermark": 0}
    with conn:
        for filing in parsed.filings:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO exchange_result_filings
                   (snapshot_id, exchange, filing_key, exchange_key, isin,
                    company_name_raw, result_period, period_end_date, reporting_scope,
                    audited_raw, cumulative_raw, relating_to_raw, broadcast_raw,
                    broadcast_at_utc, raw_row_json, discovered_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id, filing.exchange, filing.filing_key, filing.exchange_key,
                    filing.isin, filing.company_name_raw, filing.result_period,
                    filing.period_end_date, filing.reporting_scope, filing.audited_raw,
                    filing.cumulative_raw, filing.relating_to_raw, filing.broadcast_raw,
                    filing.broadcast_at_utc, filing.raw_row_json, now_utc,
                ),
            )
            counts["stored" if cursor.rowcount == 1 else "already_stored"] += 1
            if cursor.rowcount == 0:
                existing = conn.execute(
                    """SELECT filing_id, raw_row_json FROM exchange_result_filings
                       WHERE exchange=? AND filing_key=?""",
                    (filing.exchange, filing.filing_key),
                ).fetchone()
                if existing is not None and str(existing["raw_row_json"]) != filing.raw_row_json:
                    collision = conn.execute(
                        """INSERT OR IGNORE INTO exchange_result_filing_collisions
                           (exchange, filing_key, existing_filing_id, snapshot_id,
                            raw_sha256, raw_row_json, first_seen_at_utc)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (filing.exchange, filing.filing_key, int(existing["filing_id"]),
                         snapshot_id, hashlib.sha256(filing.raw_row_json.encode()).hexdigest(),
                         filing.raw_row_json, now_utc),
                    )
                    counts["identity_collisions"] += collision.rowcount
            if watermark is None or filing.broadcast_at_utc > watermark:
                counts["after_watermark"] += 1
            else:
                counts["at_or_before_watermark"] += 1
        newest = parsed.max_broadcast_at_utc
        if newest is not None and (watermark is None or newest > watermark):
            conn.execute(
                """INSERT INTO exchange_result_watermarks
                   (exchange, dataset, last_broadcast_at_utc, last_snapshot_id, updated_at_utc)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(exchange, dataset) DO UPDATE SET
                     last_broadcast_at_utc=excluded.last_broadcast_at_utc,
                     last_snapshot_id=excluded.last_snapshot_id,
                     updated_at_utc=excluded.updated_at_utc""",
                (parsed.exchange, parsed.dataset, newest, snapshot_id, now_utc),
            )
    return {
        "previous_watermark_utc": watermark,
        "watermark_utc": max(filter(None, (watermark, parsed.max_broadcast_at_utc)), default=None),
        **counts,
    }


def resolve_screener_path(
    conn: sqlite3.Connection, *, exchange: str, exchange_key: str, reporting_scope: str,
    canonical_company_path=canonical_company_path,
) -> dict[str, Any]:
    """Derive the Screener company path an exchange filing must be collected at.

    Screener addresses NSE-listed companies by their NSE symbol and BSE-only
    companies by their scrip code. The preference is resolved through the
    ISIN-keyed registry so a BSE filing for a dual-listed company still collects
    the NSE company page.
    """
    security = conn.execute(
        """SELECT * FROM exchange_securities WHERE exchange=? AND exchange_key=?
           ORDER BY is_present DESC, exchange_security_id LIMIT 1""",
        (exchange, exchange_key),
    ).fetchone()
    if security is None:
        return {
            "decision": "manual_review",
            "reason": "security_not_in_registry",
            "registry_company_id": None,
        }
    link = conn.execute(
        """SELECT registry_company_id FROM exchange_company_links
           WHERE exchange_security_id=? AND is_current=1""",
        (int(security["exchange_security_id"]),),
    ).fetchone()
    registry_company_id = int(link["registry_company_id"]) if link else None
    candidates = [security]
    if registry_company_id is not None:
        candidates = conn.execute(
            """SELECT security.* FROM exchange_securities AS security
               JOIN exchange_company_links AS link USING (exchange_security_id)
               WHERE link.registry_company_id=? AND link.is_current=1
               ORDER BY
                 CASE security.exchange WHEN 'NSE' THEN 0 ELSE 1 END,
                 security.is_present DESC,
                 CASE security.segment WHEN 'equity' THEN 0 ELSE 1 END,
                 security.exchange_security_id""",
            (registry_company_id,),
        ).fetchall() or [security]
    chosen = candidates[0]
    # A security whose identity is disputed never becomes queued work: the
    # company page we would collect might not be the company that filed.
    for security_row in (security, chosen):
        if str(security_row["verification_status"]) == "conflict":
            return {
                "decision": "manual_review",
                "reason": "identity_conflict",
                "registry_company_id": registry_company_id,
                "exchange_security_id": int(security_row["exchange_security_id"]),
            }
    if chosen["nse_symbol"]:
        identifier, basis = str(chosen["nse_symbol"]), "nse_symbol"
    else:
        identifier, basis = str(chosen["bse_scrip_code"]), "bse_scrip_code"
    try:
        # The filing scope is raw exchange provenance. It is deliberately not
        # part of collection identity: one company and one quarter produce one
        # queue item and one Screener scrape.
        path = canonical_company_path(f"/company/{identifier}/")
    except ValueError as exc:
        return {
            "decision": "manual_review",
            "reason": f"unusable_company_path: {exc}",
            "registry_company_id": registry_company_id,
        }
    return {
        "decision": "resolved",
        "reason": "resolved",
        "company_path_normalized": path,
        "path_basis": basis,
        "registry_company_id": registry_company_id,
        "exchange_security_id": int(security["exchange_security_id"]),
    }


def _decide(
    conn: sqlite3.Connection, filing: sqlite3.Row, *, now_utc: str, _json=_json
) -> dict[str, Any]:
    resolution = resolve_screener_path(
        conn,
        exchange=str(filing["exchange"]),
        exchange_key=str(filing["exchange_key"]),
        reporting_scope=str(filing["reporting_scope"]),
    )
    period = str(filing["result_period"])
    # The exchange filing scope is immutable provenance, not collection scope.
    # Every filing leads (when resolvable) to the actual standalone Screener
    # statement; there is no consolidated fallback path.
    scope = "standalone"
    if resolution["decision"] != "resolved":
        return {**resolution, "queue_item_id": None, "result_period": period}
    path = str(resolution["company_path_normalized"])
    state = conn.execute(
        """SELECT latest_accepted_result_period FROM screener_company_collection_state
           WHERE company_path_normalized=? AND reporting_scope=?""",
        (path, scope),
    ).fetchone()
    accepted = (
        str(state["latest_accepted_result_period"])
        if state and state["latest_accepted_result_period"] else None
    )
    prior = conn.execute(
        """SELECT earlier.filing_id FROM exchange_result_filings earlier
           JOIN exchange_screener_enqueues decision ON decision.filing_id=earlier.filing_id
           WHERE earlier.exchange=? AND earlier.exchange_key=?
             AND earlier.reporting_scope=? AND earlier.result_period=?
             AND earlier.filing_id<>?
           ORDER BY earlier.broadcast_at_utc DESC, earlier.filing_id DESC LIMIT 1""",
        (filing["exchange"], filing["exchange_key"], filing["reporting_scope"],
         period, filing["filing_id"]),
    ).fetchone()
    conn.execute(
        """INSERT INTO screener_company_collection_state
           (company_path_normalized, reporting_scope, latest_discovered_result_period,
            updated_at_utc)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(company_path_normalized, reporting_scope) DO UPDATE SET
             latest_discovered_result_period=CASE
               WHEN excluded.latest_discovered_result_period > latest_discovered_result_period
               THEN excluded.latest_discovered_result_period
               ELSE latest_discovered_result_period END,
             updated_at_utc=excluded.updated_at_utc""",
        (path, scope, period, now_utc),
    )
    from ..screener_discovery_repo import has_accepted_quarter
    captured = has_accepted_quarter(conn, path, scope, period)
    if accepted is not None and period <= accepted and prior is not None:
        # Distinct official filing event for a period already accepted. A new
        # timestamp alone cannot prove corrected financial content, so hold
        # it for source-grounded revision review instead of silently skipping.
        return {**resolution, "decision": "manual_review",
                "reason": f"possible_same_period_revision:prior_filing={prior['filing_id']}",
                "queue_item_id": None, "result_period": period}
    if captured and accepted is not None and period < accepted:
        return {**resolution, "decision": "stale", "reason": "older_than_accepted",
                "queue_item_id": None, "result_period": period}
    if captured and accepted == period:
        return {**resolution, "decision": "already_accepted", "reason": "period_already_accepted",
                "queue_item_id": None, "result_period": period}
    reason = "new_company" if accepted is None else "result_period_advanced"
    cursor = conn.execute(
        """INSERT OR IGNORE INTO screener_company_queue
           (origin, candidate_id, exchange_filing_id, company_path_normalized,
            reporting_scope, result_period, queue_reason, status, attempt_count,
            next_attempt_at_utc, created_at_utc, updated_at_utc)
           VALUES ('exchange_filing', NULL, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)""",
        (
            int(filing["filing_id"]), path, scope, period, reason,
            now_utc, now_utc, now_utc,
        ),
    )
    if cursor.rowcount == 1:
        queue_item_id = int(cursor.lastrowid)
        conn.execute(
            """INSERT INTO screener_company_queue_events
               (queue_item_id, event_at_utc, from_status, to_status, evidence_json)
               VALUES (?, ?, NULL, 'pending', ?)""",
            (
                queue_item_id, now_utc,
                _json(
                    {
                        "origin": "exchange_filing",
                        "queue_reason": reason,
                        "filing_id": int(filing["filing_id"]),
                        "exchange": str(filing["exchange"]),
                        "exchange_key": str(filing["exchange_key"]),
                        "path_basis": resolution["path_basis"],
                        "reporting_scope_policy": "standalone_only",
                    }
                ),
            ),
        )
        decision = "queued"
    else:
        existing = conn.execute(
            """SELECT queue_item_id FROM screener_company_queue
               WHERE company_path_normalized=? AND reporting_scope=? AND result_period=?""",
            (path, scope, period),
        ).fetchone()
        queue_item_id = int(existing["queue_item_id"]) if existing else None
        decision = "reused"
    return {
        **resolution,
        "decision": decision,
        "reason": reason,
        "queue_item_id": queue_item_id,
        "result_period": period,
    }


def preview_filing_decisions(
    conn: sqlite3.Connection, parsed: ParsedResultFilings
) -> dict[str, int]:
    """Project queue decisions without writing anything (dry-run only)."""
    counts: dict[str, int] = {
        "queued": 0, "reused": 0, "stale": 0, "already_accepted": 0, "manual_review": 0,
    }
    planned: set[tuple[str, str, str]] = set()
    for filing in parsed.filings:
        resolution = resolve_screener_path(
            conn,
            exchange=filing.exchange,
            exchange_key=filing.exchange_key,
            reporting_scope=filing.reporting_scope,
        )
        if resolution["decision"] != "resolved":
            counts["manual_review"] += 1
            continue
        key = (
            str(resolution["company_path_normalized"]),
            "standalone",
            filing.result_period,
        )
        state = conn.execute(
            """SELECT latest_accepted_result_period FROM screener_company_collection_state
               WHERE company_path_normalized=? AND reporting_scope=?""",
            key[:2],
        ).fetchone()
        accepted = (
            str(state["latest_accepted_result_period"])
            if state and state["latest_accepted_result_period"] else None
        )
        existing = conn.execute(
            """SELECT 1 FROM screener_company_queue
               WHERE company_path_normalized=? AND reporting_scope=? AND result_period=?""",
            key,
        ).fetchone()
        prior = conn.execute(
            """SELECT 1 FROM exchange_result_filings earlier
               JOIN exchange_screener_enqueues decision ON decision.filing_id=earlier.filing_id
               WHERE earlier.exchange=? AND earlier.exchange_key=?
                 AND earlier.reporting_scope=? AND earlier.result_period=? LIMIT 1""",
            (filing.exchange, filing.exchange_key, filing.reporting_scope,
             filing.result_period),
        ).fetchone()
        if accepted is not None and filing.result_period <= accepted and prior is not None:
            counts["manual_review"] += 1
        elif accepted is not None and filing.result_period < accepted:
            counts["stale"] += 1
        elif accepted == filing.result_period:
            counts["already_accepted"] += 1
        elif existing is not None or key in planned:
            counts["reused"] += 1
        else:
            planned.add(key)
            counts["queued"] += 1
    return counts


def enqueue_result_filings(
    conn: sqlite3.Connection, *, snapshot_id: int | None = None, now_utc: str,
    max_new_queue_entries: int | None = None, filing_keys: tuple[str, ...] | None = None,
) -> dict[str, int]:
    """Queue only undecided filings; a company enters work when its period advances."""
    if max_new_queue_entries is not None and (type(max_new_queue_entries) is not int or max_new_queue_entries < 0):
        raise ValueError('Queue admission limit must be a nonnegative integer')
    if filing_keys is not None and snapshot_id is not None:
        raise ValueError('Select either source filing keys or one snapshot')
    query = """SELECT * FROM exchange_result_filings AS filing
               WHERE NOT EXISTS (
                 SELECT 1 FROM exchange_screener_enqueues AS decision
                 WHERE decision.filing_id = filing.filing_id
               )"""
    parameters: tuple[Any, ...] = ()
    if snapshot_id is not None:
        query += " AND filing.snapshot_id=?"
        parameters = (snapshot_id,)
    if filing_keys is not None:
        if not filing_keys:
            return {'queued': 0, 'deferred': 0}
        query += ' AND filing.filing_key IN (' + ','.join('?' for _ in filing_keys) + ')'
        parameters = filing_keys
    query += " ORDER BY filing.broadcast_at_utc, filing.filing_id"
    counts: dict[str, int] = {
        "queued": 0, "reused": 0, "unchanged": 0, "stale": 0,
        "already_accepted": 0, "manual_review": 0,
    }
    with conn:
        rows = conn.execute(query, parameters).fetchall()
        for position, filing in enumerate(rows):
            if max_new_queue_entries is not None and counts['queued'] >= max_new_queue_entries:
                # Leave remaining filings undecided, so a later bounded intake
                # can resume them even when the feed has a new snapshot hash.
                counts['deferred'] = len(rows) - position
                break
            outcome = _decide(conn, filing, now_utc=now_utc)
            decision = str(outcome["decision"])
            counts[decision] = counts.get(decision, 0) + 1
            conn.execute(
                """INSERT OR IGNORE INTO exchange_screener_enqueues
                   (filing_id, decision, decision_reason, company_path_normalized,
                    reporting_scope, result_period, path_basis, registry_company_id,
                    queue_item_id, created_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(filing["filing_id"]), decision, str(outcome["reason"]),
                    outcome.get("company_path_normalized"),
                    "standalone", str(filing["result_period"]),
                    outcome.get("path_basis"), outcome.get("registry_company_id"),
                    outcome.get("queue_item_id"), now_utc,
                ),
            )
            conn.execute(
                """INSERT INTO exchange_company_result_state
                   (exchange, exchange_key, reporting_scope, latest_filed_result_period,
                    latest_broadcast_at_utc, latest_filing_id, latest_queued_result_period,
                    updated_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(exchange, exchange_key, reporting_scope) DO UPDATE SET
                     latest_filed_result_period=MAX(latest_filed_result_period, excluded.latest_filed_result_period),
                     latest_broadcast_at_utc=MAX(latest_broadcast_at_utc, excluded.latest_broadcast_at_utc),
                     latest_filing_id=CASE
                       WHEN excluded.latest_broadcast_at_utc >= latest_broadcast_at_utc
                       THEN excluded.latest_filing_id ELSE latest_filing_id END,
                     latest_queued_result_period=CASE
                       WHEN excluded.latest_queued_result_period IS NOT NULL
                        AND (latest_queued_result_period IS NULL
                             OR excluded.latest_queued_result_period > latest_queued_result_period)
                       THEN excluded.latest_queued_result_period
                       ELSE latest_queued_result_period END,
                     updated_at_utc=excluded.updated_at_utc""",
                (
                    str(filing["exchange"]), str(filing["exchange_key"]),
                    str(filing["reporting_scope"]), str(filing["result_period"]),
                    str(filing["broadcast_at_utc"]), int(filing["filing_id"]),
                    str(filing["result_period"]) if decision == "queued" else None,
                    now_utc,
                ),
            )
    return counts
