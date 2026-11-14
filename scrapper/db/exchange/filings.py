"""Exchange result filing provenance, identity resolution and Screener enqueues."""
from __future__ import annotations
from ..repository_backend import (
    atomic_repository, begin_immediate, commit, execute, executemany, generated_id,
    is_postgresql, rollback, statement, transaction,
)

import hashlib
import json
import sqlite3
from typing import Any

from ...sources.exchange.result_filings import ParsedResultFilings
from ...sources.screener.result_screen import canonical_company_path

def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))

@atomic_repository('financial-repositories')
def ingest_result_filings(
    conn: sqlite3.Connection,
    parsed: ParsedResultFilings,
    *,
    snapshot_id: int,
    now_utc: str,
) -> dict[str, Any]:
    """Store filings idempotently and advance the broadcast watermark."""
    previous = execute(conn,
        """SELECT last_broadcast_at_utc FROM exchange_result_watermarks
           WHERE exchange=? AND dataset=?""",
        (parsed.exchange, parsed.dataset),
    ).fetchone()
    watermark = str(previous["last_broadcast_at_utc"]) if previous else None
    if is_postgresql(conn):
        from datetime import datetime
        timestamp_key = datetime.fromisoformat
    else:
        timestamp_key = str
    counts = {"stored": 0, "already_stored": 0, "identity_collisions": 0,
              "after_watermark": 0, "at_or_before_watermark": 0}
    with transaction(conn):
        for filing in parsed.filings:
            cursor = execute(conn,
                statement(conn, 'INSERT OR IGNORE INTO exchange_result_filings\n                   (snapshot_id, exchange, filing_key, exchange_key, isin,\n                    company_name_raw, result_period, period_end_date, reporting_scope,\n                    audited_raw, cumulative_raw, relating_to_raw, broadcast_raw,\n                    broadcast_at_utc, raw_row_json, discovered_at_utc)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', 'INSERT INTO exchange_result_filings\n                   (snapshot_id, exchange, filing_key, exchange_key, isin,\n                    company_name_raw, result_period, period_end_date, reporting_scope,\n                    audited_raw, cumulative_raw, relating_to_raw, broadcast_raw,\n                    broadcast_at_utc, raw_row_json, discovered_at_utc)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING'),
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
                existing = execute(conn,
                    """SELECT filing_id, raw_row_json FROM exchange_result_filings
                       WHERE exchange=? AND filing_key=?""",
                    (filing.exchange, filing.filing_key),
                ).fetchone()
                if existing is not None and str(existing["raw_row_json"]) != filing.raw_row_json:
                    collision = execute(conn,
                        statement(conn, 'INSERT OR IGNORE INTO exchange_result_filing_collisions\n                           (exchange, filing_key, existing_filing_id, snapshot_id,\n                            raw_sha256, raw_row_json, first_seen_at_utc)\n                           VALUES (?, ?, ?, ?, ?, ?, ?)', 'INSERT INTO exchange_result_filing_collisions\n                           (exchange, filing_key, existing_filing_id, snapshot_id,\n                            raw_sha256, raw_row_json, first_seen_at_utc)\n                           VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING'),
                        (filing.exchange, filing.filing_key, int(existing["filing_id"]),
                         snapshot_id, hashlib.sha256(filing.raw_row_json.encode()).hexdigest(),
                         filing.raw_row_json, now_utc),
                    )
                    counts["identity_collisions"] += collision.rowcount
            if watermark is None or timestamp_key(filing.broadcast_at_utc) > timestamp_key(watermark):
                counts["after_watermark"] += 1
            else:
                counts["at_or_before_watermark"] += 1
        newest = (max((filing.broadcast_at_utc for filing in parsed.filings), key=timestamp_key, default=None)
                  if is_postgresql(conn) else parsed.max_broadcast_at_utc)
        if newest is not None and (watermark is None or timestamp_key(newest) > timestamp_key(watermark)):
            execute(conn,
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
        "watermark_utc": max(filter(None, (watermark, newest)), key=timestamp_key, default=None),
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
    security = execute(conn,
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
    link = execute(conn,
        """SELECT registry_company_id FROM exchange_company_links
           WHERE exchange_security_id=? AND is_current=1""",
        (int(security["exchange_security_id"]),),
    ).fetchone()
    registry_company_id = int(link["registry_company_id"]) if link else None
    candidates = [security]
    if registry_company_id is not None:
        candidates = execute(conn,
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


@atomic_repository('financial-repositories')
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
    state = execute(conn,
        """SELECT latest_accepted_result_period FROM screener_company_collection_state
           WHERE company_path_normalized=? AND reporting_scope=?""",
        (path, scope),
    ).fetchone()
    accepted = (
        str(state["latest_accepted_result_period"])
        if state and state["latest_accepted_result_period"] else None
    )
    prior = execute(conn,
        """SELECT earlier.filing_id FROM exchange_result_filings earlier
           JOIN exchange_screener_enqueues decision ON decision.filing_id=earlier.filing_id
           WHERE earlier.exchange=? AND earlier.exchange_key=?
             AND earlier.reporting_scope=? AND earlier.result_period=?
             AND earlier.filing_id<>?
           ORDER BY earlier.broadcast_at_utc DESC, earlier.filing_id DESC LIMIT 1""",
        (filing["exchange"], filing["exchange_key"], filing["reporting_scope"],
         period, filing["filing_id"]),
    ).fetchone()
    execute(conn,
        statement(conn, 'INSERT INTO screener_company_collection_state\n           (company_path_normalized, reporting_scope, latest_discovered_result_period,\n            updated_at_utc)\n           VALUES (?, ?, ?, ?)\n           ON CONFLICT(company_path_normalized, reporting_scope) DO UPDATE SET\n             latest_discovered_result_period=CASE\n               WHEN excluded.latest_discovered_result_period > latest_discovered_result_period\n               THEN excluded.latest_discovered_result_period\n               ELSE latest_discovered_result_period END,\n             updated_at_utc=excluded.updated_at_utc', 'INSERT INTO screener_company_collection_state\n           (company_path_normalized, reporting_scope, latest_discovered_result_period,\n            updated_at_utc)\n           VALUES (?, ?, ?, ?)\n           ON CONFLICT(company_path_normalized, reporting_scope) DO UPDATE SET\n             latest_discovered_result_period=CASE\n               WHEN excluded.latest_discovered_result_period > screener_company_collection_state.latest_discovered_result_period\n               THEN excluded.latest_discovered_result_period\n               ELSE screener_company_collection_state.latest_discovered_result_period END,\n             updated_at_utc=excluded.updated_at_utc'),
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
    cursor = execute(conn,
        statement(conn, "INSERT OR IGNORE INTO screener_company_queue\n           (origin, candidate_id, exchange_filing_id, company_path_normalized,\n            reporting_scope, result_period, queue_reason, status, attempt_count,\n            next_attempt_at_utc, created_at_utc, updated_at_utc)\n           VALUES ('exchange_filing', NULL, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)", "INSERT INTO screener_company_queue\n           (origin, candidate_id, exchange_filing_id, company_path_normalized,\n            reporting_scope, result_period, queue_reason, status, attempt_count,\n            next_attempt_at_utc, created_at_utc, updated_at_utc)\n           VALUES ('exchange_filing', NULL, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?) ON CONFLICT DO NOTHING"),
        (
            int(filing["filing_id"]), path, scope, period, reason,
            now_utc, now_utc, now_utc,
        ),
     returning='queue_item_id')
    if cursor.rowcount == 1:
        queue_item_id = int(generated_id(cursor, 'queue_item_id'))
        execute(conn,
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
        existing = execute(conn,
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
        state = execute(conn,
            """SELECT latest_accepted_result_period FROM screener_company_collection_state
               WHERE company_path_normalized=? AND reporting_scope=?""",
            key[:2],
        ).fetchone()
        accepted = (
            str(state["latest_accepted_result_period"])
            if state and state["latest_accepted_result_period"] else None
        )
        existing = execute(conn,
            """SELECT 1 FROM screener_company_queue
               WHERE company_path_normalized=? AND reporting_scope=? AND result_period=?""",
            key,
        ).fetchone()
        prior = execute(conn,
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


@atomic_repository('financial-repositories')
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
    with transaction(conn):
        rows = execute(conn, query, parameters).fetchall()
        for position, filing in enumerate(rows):
            if max_new_queue_entries is not None and counts['queued'] >= max_new_queue_entries:
                # Leave remaining filings undecided, so a later bounded intake
                # can resume them even when the feed has a new snapshot hash.
                counts['deferred'] = len(rows) - position
                break
            outcome = _decide(conn, filing, now_utc=now_utc)
            decision = str(outcome["decision"])
            counts[decision] = counts.get(decision, 0) + 1
            execute(conn,
                statement(conn, 'INSERT OR IGNORE INTO exchange_screener_enqueues\n                   (filing_id, decision, decision_reason, company_path_normalized,\n                    reporting_scope, result_period, path_basis, registry_company_id,\n                    queue_item_id, created_at_utc)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', 'INSERT INTO exchange_screener_enqueues\n                   (filing_id, decision, decision_reason, company_path_normalized,\n                    reporting_scope, result_period, path_basis, registry_company_id,\n                    queue_item_id, created_at_utc)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING'),
                (
                    int(filing["filing_id"]), decision, str(outcome["reason"]),
                    outcome.get("company_path_normalized"),
                    "standalone", str(filing["result_period"]),
                    outcome.get("path_basis"), outcome.get("registry_company_id"),
                    outcome.get("queue_item_id"), now_utc,
                ),
            )
            execute(conn,
                statement(conn, 'INSERT INTO exchange_company_result_state\n                   (exchange, exchange_key, reporting_scope, latest_filed_result_period,\n                    latest_broadcast_at_utc, latest_filing_id, latest_queued_result_period,\n                    updated_at_utc)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n                   ON CONFLICT(exchange, exchange_key, reporting_scope) DO UPDATE SET\n                     latest_filed_result_period=MAX(latest_filed_result_period, excluded.latest_filed_result_period),\n                     latest_broadcast_at_utc=MAX(latest_broadcast_at_utc, excluded.latest_broadcast_at_utc),\n                     latest_filing_id=CASE\n                       WHEN excluded.latest_broadcast_at_utc >= latest_broadcast_at_utc\n                       THEN excluded.latest_filing_id ELSE latest_filing_id END,\n                     latest_queued_result_period=CASE\n                       WHEN excluded.latest_queued_result_period IS NOT NULL\n                        AND (latest_queued_result_period IS NULL\n                             OR excluded.latest_queued_result_period > latest_queued_result_period)\n                       THEN excluded.latest_queued_result_period\n                       ELSE latest_queued_result_period END,\n                     updated_at_utc=excluded.updated_at_utc', 'INSERT INTO exchange_company_result_state\n                   (exchange, exchange_key, reporting_scope, latest_filed_result_period,\n                    latest_broadcast_at_utc, latest_filing_id, latest_queued_result_period,\n                    updated_at_utc)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n                   ON CONFLICT(exchange, exchange_key, reporting_scope) DO UPDATE SET\n                     latest_filed_result_period=CASE WHEN exchange_company_result_state.latest_filed_result_period IS NULL OR excluded.latest_filed_result_period IS NULL THEN NULL ELSE GREATEST(exchange_company_result_state.latest_filed_result_period, excluded.latest_filed_result_period) END,\n                     latest_broadcast_at_utc=CASE WHEN exchange_company_result_state.latest_broadcast_at_utc IS NULL OR excluded.latest_broadcast_at_utc IS NULL THEN NULL WHEN exchange_company_result_state.latest_broadcast_at_utc::timestamptz >= excluded.latest_broadcast_at_utc::timestamptz THEN exchange_company_result_state.latest_broadcast_at_utc ELSE excluded.latest_broadcast_at_utc END,\n                     latest_filing_id=CASE\n                       WHEN CAST(excluded.latest_broadcast_at_utc AS timestamptz) >= CAST(exchange_company_result_state.latest_broadcast_at_utc AS timestamptz)\n                       THEN excluded.latest_filing_id ELSE exchange_company_result_state.latest_filing_id END,\n                     latest_queued_result_period=CASE\n                       WHEN excluded.latest_queued_result_period IS NOT NULL\n                        AND (exchange_company_result_state.latest_queued_result_period IS NULL\n                             OR excluded.latest_queued_result_period > exchange_company_result_state.latest_queued_result_period)\n                       THEN excluded.latest_queued_result_period\n                       ELSE exchange_company_result_state.latest_queued_result_period END,\n                     updated_at_utc=excluded.updated_at_utc'),
                (
                    str(filing["exchange"]), str(filing["exchange_key"]),
                    str(filing["reporting_scope"]), str(filing["result_period"]),
                    str(filing["broadcast_at_utc"]), int(filing["filing_id"]),
                    str(filing["result_period"]) if decision == "queued" else None,
                    now_utc,
                ),
            )
    # A result establishes an expectation, not document availability. This
    # separate idempotent handoff cannot commit a half-finished queue decision.
    # It also repairs a previous interrupted handoff on the next bounded poll.
    from ..documents.freshness import register_recent_filing_expectations
    register_recent_filing_expectations(conn, now_utc=now_utc)
    return counts
