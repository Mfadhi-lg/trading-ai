"""Persistence and overlap-safe queue primitives for Screener discovery."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.collection_scope import is_scope_exclusion
from .collection_scope_repo import record_scope
from ..sources.screener.result_screen import ParsedResultScreen, canonical_company_path


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ClaimedCompany:
    queue_item_id: int
    candidate_id: int | None
    company_path_normalized: str
    reporting_scope: str
    result_period: str
    queue_reason: str
    attempt_count: int
    claim_token: str
    origin: str = "screener_screen"
    exchange_filing_id: int | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ClaimedCompany":
        return cls(
            int(row["queue_item_id"]),
            int(row["candidate_id"]) if row["candidate_id"] is not None else None,
            str(row["company_path_normalized"]),
            str(row["reporting_scope"]),
            str(row["result_period"]),
            str(row["queue_reason"]),
            int(row["attempt_count"]),
            str(row["claim_token"]),
            str(row["origin"]),
            int(row["exchange_filing_id"]) if row["exchange_filing_id"] is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue_item_id": self.queue_item_id,
            "origin": self.origin,
            "candidate_id": self.candidate_id,
            "exchange_filing_id": self.exchange_filing_id,
            "company_path_normalized": self.company_path_normalized,
            "reporting_scope": self.reporting_scope,
            "result_period": self.result_period,
            "queue_reason": self.queue_reason,
            "attempt_count": self.attempt_count,
            "claim_token": self.claim_token,
        }


def persist_screen(
    conn: sqlite3.Connection,
    parsed: ParsedResultScreen,
    *,
    query_role: str,
    fetch_id: int,
    blob_id: int,
    observed_at_utc: str,
    parser_name: str,
    parser_version: str,
) -> tuple[int, bool]:
    if not query_role.strip():
        raise ValueError("query_role is required")
    with conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO screener_screen_runs
               (query_role, query_raw, requested_url, fetch_id, blob_id,
                parser_name, parser_version, observed_at_utc, result_count,
                page_number, page_count, captured_row_count, coverage_status,
                validation_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'passed')""",
            (
                query_role.strip(), parsed.query_raw, parsed.requested_url,
                fetch_id, blob_id, parser_name, parser_version, observed_at_utc,
                parsed.result_count, parsed.page_number, parsed.page_count,
                parsed.captured_row_count, parsed.coverage_status,
            ),
        )
        created = cursor.rowcount == 1
        row = conn.execute(
            """SELECT screen_run_id FROM screener_screen_runs
               WHERE blob_id=? AND parser_name=? AND parser_version=? AND query_raw=?""",
            (blob_id, parser_name, parser_version, parsed.query_raw),
        ).fetchone()
        if row is None:
            raise RuntimeError("Failed to persist Screener screen run")
        screen_run_id = int(row["screen_run_id"])
        if created:
            conn.executemany(
                """INSERT INTO screener_screen_source_cells
                   (screen_run_id, row_position, column_position, row_label_raw,
                    column_label_raw, raw_text, normalized_display_text,
                    link_url, cell_role)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        screen_run_id, cell.row_position, cell.column_position,
                        cell.row_label_raw, cell.column_label_raw, cell.raw_text,
                        cell.normalized_display_text, cell.link_url, cell.cell_role,
                    )
                    for cell in parsed.source_cells
                ],
            )
            conn.executemany(
                """INSERT INTO screener_result_candidates
                   (screen_run_id, source_position, screen_row_company_id,
                    company_name_raw, company_href_raw, company_path_normalized,
                    reporting_scope_hint, last_result_date_display, result_period,
                    discovered_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        screen_run_id, item.source_position,
                        item.screen_row_company_id, item.company_name_raw,
                        item.company_href_raw, item.company_path_normalized,
                        item.reporting_scope_hint, item.last_result_date_display,
                        item.result_period, observed_at_utc,
                    )
                    for item in parsed.rows
                ],
            )
    return screen_run_id, created


def persist_rejected_screen(
    conn: sqlite3.Connection,
    *,
    query_role: str,
    query_raw: str,
    requested_url: str,
    fetch_id: int,
    blob_id: int,
    observed_at_utc: str,
    parser_name: str,
    parser_version: str,
) -> int:
    """Retain permitted response evidence even when its structure is rejected."""
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO screener_screen_runs
               (query_role, query_raw, requested_url, fetch_id, blob_id,
                parser_name, parser_version, observed_at_utc, result_count,
                page_number, page_count, captured_row_count, coverage_status,
                validation_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 1, 0, 'rejected', 'failed')""",
            (
                query_role, query_raw, requested_url, fetch_id, blob_id,
                parser_name, parser_version, observed_at_utc,
            ),
        )
    row = conn.execute(
        """SELECT screen_run_id FROM screener_screen_runs
           WHERE blob_id=? AND parser_name=? AND parser_version=? AND query_raw=?""",
        (blob_id, parser_name, parser_version, query_raw),
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to persist rejected Screener screen evidence")
    return int(row["screen_run_id"])


def has_accepted_quarter(conn, path: str, scope: str, period: str) -> bool:
    """A period watermark is insufficient; require accepted statement rows."""
    return conn.execute(
        """SELECT 1 FROM accepted_page_versions a
           JOIN screener_company_collection_state s ON s.company_id=a.company_id
           JOIN financial_observations f ON f.parse_id=a.parse_id
           WHERE s.company_path_normalized=? AND a.source='screener'
             AND s.reporting_scope=? AND a.reporting_scope=s.reporting_scope
             AND f.section_code='quarters' AND f.reporting_scope=a.reporting_scope
             AND substr(f.period_end,1,7)=? LIMIT 1""",
        (path, scope, period),
    ).fetchone() is not None


def enqueue_screen_candidates(conn, screen_run_id, *, now_utc):
    return _enqueue_screen_candidates(conn,screen_run_id,now_utc=now_utc)


def enqueue_complete_screen_pages(conn, screen_run_ids, *, now_utc):
    """Admit a fully captured paginated screen without relabeling partial pages."""
    ids=tuple(screen_run_ids)
    if not ids or len(ids)>100 or len(set(ids))!=len(ids):
        raise ValueError('Distinct bounded screen page IDs required')
    marks=','.join('?' for _ in ids)
    pages=conn.execute('SELECT * FROM screener_screen_runs WHERE screen_run_id IN ('+marks+') ORDER BY page_number',ids).fetchall()
    if len(pages)!=len(ids):raise ValueError('Missing screen page evidence')
    first=pages[0];count=int(first['result_count']);page_count=int(first['page_count'])
    if [r['page_number'] for r in pages]!=list(range(1,page_count+1)):
        raise ValueError('Incomplete screen pagination')
    if any(r['result_count']!=count or r['page_count']!=page_count or r['query_raw']!=first['query_raw'] or r['query_role']!=first['query_role'] or r['validation_status']!='passed' for r in pages):
        raise ValueError('Screen changed during pagination; no queue admission')
    candidates=conn.execute('SELECT screen_row_company_id,company_path_normalized,result_period FROM screener_result_candidates WHERE screen_run_id IN ('+marks+')',ids).fetchall()
    if len(candidates)!=count or sum(r['captured_row_count'] for r in pages)!=count or len({r['screen_row_company_id'] for r in candidates})!=count:
        raise ValueError('Incomplete or duplicate company coverage; no queue admission')
    totals={key:0 for key in ('new','advanced','unchanged','stale','reused')}
    for page in pages:
        result=_enqueue_screen_candidates(conn,page['screen_run_id'],now_utc=now_utc,verified_page_set=True)
        for key,value in result.items():totals[key]+=value
    return {'complete':True,'captured_companies':count,'page_count':page_count,'screen_run_ids':list(ids),'enqueue':totals}


def enqueue_recurring_screen_pages(conn, screen_run_ids, *, now_utc):
    """Admit observed rows from a moving screen without claiming full coverage.

    Every numbered page must still be present, validated, and from the same
    query. Only changing result counts permit this incomplete-coverage mode.
    A stable screen retains the stricter full-capture validation above.
    """
    ids=tuple(screen_run_ids)
    if not ids or len(ids)>100 or len(set(ids))!=len(ids):
        raise ValueError('Distinct bounded screen page IDs required')
    marks=','.join('?' for _ in ids)
    pages=conn.execute('SELECT * FROM screener_screen_runs WHERE screen_run_id IN ('+marks+') ORDER BY page_number',ids).fetchall()
    if len(pages)!=len(ids):raise ValueError('Missing screen page evidence')
    first=pages[0]
    if [r['page_number'] for r in pages]!=list(range(1,first['page_count']+1)):
        raise ValueError('Incomplete screen pagination')
    if any(r['page_count']!=first['page_count'] or r['query_raw']!=first['query_raw'] or
           r['query_role']!=first['query_role'] or r['validation_status']!='passed' for r in pages):
        raise ValueError('Screen query, page coverage or validation changed; no queue admission')
    counts={r['result_count'] for r in pages}
    if len(counts)==1:
        return enqueue_complete_screen_pages(conn,ids,now_utc=now_utc)
    identities=set()
    for page in pages:
        rows=conn.execute('SELECT screen_row_company_id FROM screener_result_candidates WHERE screen_run_id=?',(page['screen_run_id'],)).fetchall()
        unique={r[0] for r in rows}
        if len(rows)!=page['captured_row_count'] or len(unique)!=len(rows):
            raise ValueError('Missing or duplicate company evidence within a screen page')
        identities.update(unique)
    totals={key:0 for key in ('new','advanced','unchanged','stale','reused')}
    for page in pages:
        result=_enqueue_screen_candidates(conn,page['screen_run_id'],now_utc=now_utc,verified_page_set=True)
        for key,value in result.items():totals[key]+=value
    return {'complete':False,'coverage_status':'moving_screen_partial',
        'captured_companies':len(identities),'page_count':first['page_count'],
        'source_count_range':[min(counts),max(counts)],'screen_run_ids':list(ids),'enqueue':totals}


def _enqueue_screen_candidates(
    conn: sqlite3.Connection,
    screen_run_id: int,
    *,
    now_utc: str,
    verified_page_set: bool = False,
) -> dict[str, int]:
    screen = conn.execute(
        "SELECT coverage_status FROM screener_screen_runs WHERE screen_run_id=?",
        (screen_run_id,),
    ).fetchone()
    if screen is None:
        raise ValueError(f"Unknown screen run: {screen_run_id}")
    counts = {"new": 0, "advanced": 0, "unchanged": 0, "stale": 0, "reused": 0}
    if screen["coverage_status"] != "complete" and not verified_page_set:
        return counts
    candidates = conn.execute(
        """SELECT * FROM screener_result_candidates
           WHERE screen_run_id=? ORDER BY source_position""",
        (screen_run_id,),
    ).fetchall()
    with conn:
        for item in candidates:
            # A screen can show both technical page variants for one company.
            # They must collapse before becoming collection work.
            path = canonical_company_path(str(item["company_path_normalized"]))
            # Result screens occasionally link the consolidated view.  Preserve
            # that raw link in screener_result_candidates, but all financial
            # collection is explicitly redirected to the real standalone URL.
            scope = "standalone"
            period = str(item["result_period"])
            state = conn.execute(
                """SELECT * FROM screener_company_collection_state
                   WHERE company_path_normalized=? AND reporting_scope=?""",
                (path, scope),
            ).fetchone()
            accepted = str(state["latest_accepted_result_period"]) if state and state["latest_accepted_result_period"] else None
            discovered = str(state["latest_discovered_result_period"]) if state else None
            latest_discovered = max(filter(None, (period, discovered)))
            conn.execute(
                """INSERT INTO screener_company_collection_state
                   (company_path_normalized, reporting_scope,
                    latest_discovered_result_period, updated_at_utc)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(company_path_normalized, reporting_scope) DO UPDATE SET
                     latest_discovered_result_period=CASE
                       WHEN excluded.latest_discovered_result_period > latest_discovered_result_period
                       THEN excluded.latest_discovered_result_period
                       ELSE latest_discovered_result_period END,
                     updated_at_utc=excluded.updated_at_utc""",
                (path, scope, latest_discovered, now_utc),
            )
            captured = has_accepted_quarter(conn, path, scope, period)
            if captured and accepted is not None and period < accepted:
                counts["stale"] += 1
                continue
            if captured and accepted == period:
                counts["unchanged"] += 1
                continue
            reason = "new_company" if accepted is None else "result_period_advanced"
            cursor = conn.execute(
                """INSERT OR IGNORE INTO screener_company_queue
                   (candidate_id, company_path_normalized, reporting_scope,
                    result_period, queue_reason, status, attempt_count,
                    next_attempt_at_utc, created_at_utc, updated_at_utc)
                   VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)""",
                (
                    int(item["candidate_id"]), path, scope, period, reason,
                    now_utc, now_utc, now_utc,
                ),
            )
            if cursor.rowcount == 1:
                queue_id = int(cursor.lastrowid)
                conn.execute(
                    """INSERT INTO screener_company_queue_events
                       (queue_item_id, event_at_utc, from_status, to_status,
                        evidence_json) VALUES (?, ?, NULL, 'pending', ?)""",
                    (queue_id, now_utc, _json({"queue_reason": reason, "screen_run_id": screen_run_id})),
                )
                counts["new" if reason == "new_company" else "advanced"] += 1
            else:
                counts["reused"] += 1
    return counts


def preview_due_companies(
    conn: sqlite3.Connection, *, now_utc: str, limit: int
) -> tuple[ClaimedCompany, ...]:
    if limit < 1:
        raise ValueError("limit must be positive")
    rows = conn.execute(
        """SELECT queue.*, '' AS claim_token FROM screener_company_queue AS queue
           WHERE collection_disposition IS NULL
           AND NOT EXISTS (SELECT 1 FROM screener_scope_aliases a
             JOIN screener_collection_scope s ON s.company_path=a.scope_path
             WHERE a.company_path=company_path_normalized AND s.eligibility='below_floor')
           AND ((
             status IN ('pending', 'failed_retryable')
             AND (next_attempt_at_utc IS NULL OR next_attempt_at_utc <= ?)
           ) OR (status IN ('claimed', 'running') AND claim_expires_at_utc <= ?))
           ORDER BY result_period DESC, queue_item_id LIMIT ?""",
        (now_utc, now_utc, limit),
    ).fetchall()
    return tuple(ClaimedCompany.from_row(row) for row in rows)


def claim_due_companies(
    conn: sqlite3.Connection,
    *,
    now_utc: str,
    limit: int,
    worker_id: str,
    lease_seconds: int = 900,
    queue_item_ids: tuple[int, ...] | None = None,
) -> tuple[ClaimedCompany, ...]:
    if not worker_id.strip() or limit < 1 or lease_seconds < 1:
        raise ValueError("worker_id, positive limit and lease are required")
    if queue_item_ids is not None and (len(queue_item_ids) != limit or
            len(set(queue_item_ids)) != limit or any(i < 1 for i in queue_item_ids)):
        raise ValueError("Selected queue identities must be distinct and match the limit")
    expires = (_parse_utc(now_utc) + timedelta(seconds=lease_seconds)).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        selection = "" if queue_item_ids is None else " AND queue_item_id IN (" + ",".join("?" for _ in queue_item_ids) + ")"
        ids = [
            int(row["queue_item_id"])
            for row in conn.execute(
                f"""SELECT queue_item_id FROM screener_company_queue
                   WHERE collection_disposition IS NULL
           AND NOT EXISTS (SELECT 1 FROM screener_scope_aliases a
             JOIN screener_collection_scope s ON s.company_path=a.scope_path
             WHERE a.company_path=company_path_normalized AND s.eligibility='below_floor')
           AND ((
                     status IN ('pending', 'failed_retryable')
                     AND (next_attempt_at_utc IS NULL OR next_attempt_at_utc <= ?)
                   ) OR (status IN ('claimed', 'running') AND claim_expires_at_utc <= ?))
                   {selection} ORDER BY result_period DESC, queue_item_id LIMIT ?""",
                (now_utc, now_utc, *(queue_item_ids or ()), limit),
            ).fetchall()
        ]
        if queue_item_ids is not None:
            if set(ids) != set(queue_item_ids):
                raise RuntimeError("Selected work is no longer due or eligible; no claims made")
            ids = list(queue_item_ids)
            # A new official filing can reuse an older screen-created identity.
            # Attach provenance atomically with the selected claim; retain the
            # original candidate reference in the existing event history.
            for queue_id in ids:
                old_row=conn.execute("SELECT * FROM screener_company_queue WHERE queue_item_id=?",(queue_id,)).fetchone()
                if old_row['origin']!='screener_screen':continue
                filing=conn.execute('''SELECT f.filing_id FROM exchange_screener_enqueues e
                  JOIN exchange_result_filings f USING(filing_id) WHERE e.queue_item_id=?
                  AND e.company_path_normalized=? AND e.reporting_scope=? AND e.result_period=?
                  AND f.result_period=? ORDER BY f.broadcast_at_utc DESC,f.filing_id DESC LIMIT 1''',
                  (queue_id,old_row['company_path_normalized'],old_row['reporting_scope'],old_row['result_period'],old_row['result_period'])).fetchone()
                if filing is None:continue
                conn.execute("UPDATE screener_company_queue SET origin='exchange_filing',candidate_id=NULL,exchange_filing_id=? WHERE queue_item_id=?",(filing['filing_id'],queue_id))
                conn.execute('''INSERT INTO screener_company_queue_events
                  (queue_item_id,event_at_utc,from_status,to_status,worker_id,evidence_json)
                  VALUES(?,?,?,?,?,?)''',(queue_id,now_utc,old_row['status'],old_row['status'],worker_id,
                  _json({'reason':'official_source_attached','filing_id':filing['filing_id'],
                         'previous_candidate_id':old_row['candidate_id'],'previous_origin':old_row['origin']})))
        claimed: list[ClaimedCompany] = []
        for queue_id in ids:
            old = conn.execute(
                "SELECT status FROM screener_company_queue WHERE queue_item_id=?",
                (queue_id,),
            ).fetchone()
            token = f"{worker_id}:{uuid.uuid4().hex}"
            conn.execute(
                """UPDATE screener_company_queue SET status='claimed',
                   claim_token=?, claimed_at_utc=?, claim_expires_at_utc=?,
                   updated_at_utc=? WHERE queue_item_id=?""",
                (token, now_utc, expires, now_utc, queue_id),
            )
            conn.execute(
                """INSERT INTO screener_company_queue_events
                   (queue_item_id, event_at_utc, from_status, to_status,
                    worker_id, evidence_json)
                   VALUES (?, ?, ?, 'claimed', ?, ?)""",
                (queue_id, now_utc, old["status"], worker_id, _json({"claim_expires_at_utc": expires})),
            )
            row = conn.execute(
                "SELECT * FROM screener_company_queue WHERE queue_item_id=?",
                (queue_id,),
            ).fetchone()
            claimed.append(ClaimedCompany.from_row(row))
        conn.commit()
        return tuple(claimed)
    except Exception:
        conn.rollback()
        raise


def mark_running(
    conn: sqlite3.Connection, item: ClaimedCompany, *, worker_id: str, now_utc: str
) -> None:
    with conn:
        cursor = conn.execute(
            """UPDATE screener_company_queue SET status='running', updated_at_utc=?
               WHERE queue_item_id=? AND status='claimed' AND claim_token=?""",
            (now_utc, item.queue_item_id, item.claim_token),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Queue claim is no longer owned by this worker")
        conn.execute(
            """INSERT INTO screener_company_queue_events
               (queue_item_id, event_at_utc, from_status, to_status, worker_id)
               VALUES (?, ?, 'claimed', 'running', ?)""",
            (item.queue_item_id, now_utc, worker_id),
        )


def skip_if_already_accepted(
    conn: sqlite3.Connection,
    item: ClaimedCompany,
    *,
    worker_id: str,
    now_utc: str,
) -> bool:
    reason = conn.execute("SELECT last_error_json FROM screener_company_queue WHERE queue_item_id=?", (item.queue_item_id,)).fetchone()
    if reason and json.loads(reason[0] or "{}").get("reason") == "market_cap_reentry":
        return False
    state = conn.execute(
        """SELECT latest_accepted_result_period
           FROM screener_company_collection_state
           WHERE company_path_normalized=? AND reporting_scope=?""",
        (item.company_path_normalized, item.reporting_scope),
    ).fetchone()
    if (
        state is None
        or state["latest_accepted_result_period"] is None
        or str(state["latest_accepted_result_period"]) < item.result_period
    ):
        return False
    if not has_accepted_quarter(conn, item.company_path_normalized,
                                item.reporting_scope, item.result_period):
        return False
    with conn:
        cursor = conn.execute(
            """UPDATE screener_company_queue
               SET status='skipped_already_accepted', claim_token=NULL,
                   claimed_at_utc=NULL, claim_expires_at_utc=NULL, updated_at_utc=?
               WHERE queue_item_id=? AND status='claimed' AND claim_token=?""",
            (now_utc, item.queue_item_id, item.claim_token),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Queue claim is no longer owned by this worker")
        conn.execute(
            """INSERT INTO screener_company_queue_events
               (queue_item_id, event_at_utc, from_status, to_status, worker_id,
                evidence_json)
               VALUES (?, ?, 'claimed', 'skipped_already_accepted', ?, ?)""",
            (
                item.queue_item_id, now_utc, worker_id,
                _json({"latest_accepted_result_period": state["latest_accepted_result_period"]}),
            ),
        )
    return True


def complete_company(
    conn: sqlite3.Connection,
    item: ClaimedCompany,
    *,
    parse_id: int,
    company_id: int,
    export_path: str,
    worker_id: str,
    now_utc: str,
) -> None:
    accepted = conn.execute(
        """SELECT resolved_source_period FROM accepted_page_versions
           WHERE company_id=? AND source='screener' AND reporting_scope=?
             AND parse_id=? ORDER BY resolved_source_period DESC LIMIT 1""",
        (company_id, item.reporting_scope, parse_id),
    ).fetchone()
    requested_present = item.result_period == "1970-01" or conn.execute(
        """SELECT 1 FROM financial_observations WHERE parse_id=?
           AND section_code='quarters' AND reporting_scope=?
           AND substr(period_end,1,7)=? LIMIT 1""",
        (parse_id, item.reporting_scope, item.result_period),
    ).fetchone() is not None
    if accepted is None or not requested_present:
        raise ValueError("Queue acceptance requires the full company pipeline's accepted parse and requested quarter")
    resolved_period = str(accepted["resolved_source_period"])
    with conn:
        cursor = conn.execute(
            """UPDATE screener_company_queue SET status='accepted',
               attempt_count=attempt_count+1, accepted_parse_id=?, accepted_export_path=?,
               claim_token=NULL, claimed_at_utc=NULL, claim_expires_at_utc=NULL,
               last_error_json='{}', updated_at_utc=?
               WHERE queue_item_id=? AND status='running' AND claim_token=?""",
            (parse_id, export_path, now_utc, item.queue_item_id, item.claim_token),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Queue claim is no longer owned by this worker")
        conn.execute(
            """UPDATE screener_company_collection_state SET
               company_id=?,
               latest_accepted_result_period=CASE
                 WHEN latest_accepted_result_period IS NULL OR latest_accepted_result_period < ?
                 THEN ? ELSE latest_accepted_result_period END,
               latest_accepted_parse_id=CASE
                 WHEN latest_accepted_result_period IS NULL OR latest_accepted_result_period <= ?
                 THEN ? ELSE latest_accepted_parse_id END,
               updated_at_utc=?
               WHERE company_path_normalized=? AND reporting_scope=?""",
            (
                company_id, resolved_period, resolved_period,
                resolved_period, parse_id, now_utc,
                item.company_path_normalized, item.reporting_scope,
            ),
        )
        conn.execute(
            """INSERT INTO screener_company_queue_events
               (queue_item_id, event_at_utc, from_status, to_status, worker_id,
                evidence_json) VALUES (?, ?, 'running', 'accepted', ?, ?)""",
            (item.queue_item_id, now_utc, worker_id, _json({"parse_id": parse_id, "company_id": company_id})),
        )
        if item.exchange_filing_id is not None and item.result_period != "1970-01":
            filing = conn.execute(
                """SELECT filing_id, exchange, exchange_key, result_period,
                          period_end_date, broadcast_at_utc
                   FROM exchange_result_filings WHERE filing_id=?""",
                (item.exchange_filing_id,),
            ).fetchone()
            if filing is None:
                raise RuntimeError(
                    f"Accepted queue item references missing exchange filing {item.exchange_filing_id}"
                )
            # Imported lazily to keep the core queue module free of the D09
            # orchestration dependency while retaining one atomic acceptance.
            from .documents.expectations import ensure_result_artifact_expectations

            ensure_result_artifact_expectations(
                conn,
                company_id=company_id,
                filing_id=int(filing["filing_id"]),
                exchange=str(filing["exchange"]),
                exchange_key=str(filing["exchange_key"]),
                result_period=str(filing["result_period"]),
                period_end=str(filing["period_end_date"]),
                broadcast_at_utc=str(filing["broadcast_at_utc"]),
                created_at_utc=now_utc,
            )
        # Preserve each accepted reporting view for the unified company-period
        # identity.  This is deliberately additive: it must not replace the
        # other scope merely because consolidated is the preferred ML view.
        conn.execute(
            """INSERT INTO company_period_scope_policy
               (company_id, period, reporting_scope, parse_id, accepted_at_utc)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(company_id, period, reporting_scope) DO UPDATE SET
                 parse_id=excluded.parse_id, accepted_at_utc=excluded.accepted_at_utc""",
            (company_id, resolved_period, item.reporting_scope, parse_id, now_utc),
        )


def fail_company(
    conn: sqlite3.Connection,
    item: ClaimedCompany,
    *,
    worker_id: str,
    now_utc: str,
    error: dict[str, Any],
    retryable: bool,
    stop_host: bool = False,
) -> None:
    status = "failed_retryable" if retryable else "failed_terminal"
    retry_at = (
        (_parse_utc(now_utc) + timedelta(hours=min(24, 2 ** min(item.attempt_count, 4)))).isoformat()
        if retryable else None
    )
    evidence = {"error": error, "stop_host": stop_host}
    excluded = is_scope_exclusion(error.get("message", ""))
    if excluded:
        status, retry_at = "failed_terminal", None
    with conn:
        cursor = conn.execute(
            """UPDATE screener_company_queue SET status=?,
               attempt_count=attempt_count+1, next_attempt_at_utc=?,
               claim_token=NULL, claimed_at_utc=NULL, claim_expires_at_utc=NULL,
               last_error_json=?, updated_at_utc=?
               WHERE queue_item_id=? AND status IN ('claimed', 'running')
                 AND claim_token=?""",
            (status, retry_at, _json(evidence), now_utc, item.queue_item_id, item.claim_token),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Queue claim is no longer owned by this worker")
        conn.execute(
            """INSERT INTO screener_company_queue_events
               (queue_item_id, event_at_utc, from_status, to_status, worker_id,
                evidence_json) VALUES (?, ?, 'running', ?, ?, ?)""",
            (item.queue_item_id, now_utc, status, worker_id, _json(evidence)),
        )
        if excluded:
            alias = conn.execute("SELECT scope_path FROM screener_scope_aliases WHERE company_path=?", (item.company_path_normalized,)).fetchone()
            path = alias[0] if alias else item.company_path_normalized
            state = conn.execute("SELECT company_id FROM screener_company_collection_state WHERE company_path_normalized=? AND reporting_scope='standalone'", (item.company_path_normalized,)).fetchone()
            record_scope(conn,path=path,company_id=state[0] if state else None,
                         value=error.get("market_cap_crore"),observed_at=now_utc,
                         evidence=evidence,legacy_below=True)
            conn.execute("INSERT OR IGNORE INTO screener_scope_aliases VALUES (?,?,?)", (item.company_path_normalized,path,"worker_scope_observation"))
            conn.execute("UPDATE screener_company_queue SET collection_disposition='below_market_cap' WHERE queue_item_id=?", (item.queue_item_id,))



REQUEUEABLE_STATUSES = ("failed_retryable", "failed_terminal", "manual_review")


def preview_requeue_candidates(
    conn: sqlite3.Connection,
    *,
    statuses: tuple[str, ...],
    paths: tuple[str, ...] = (),
    limit: int | None = None,
) -> tuple[dict[str, Any], ...]:
    """List the settled failures a requeue would move back to pending."""
    rows = conn.execute(*_requeue_select(statuses, paths, limit)).fetchall()
    return tuple(
        {
            "queue_item_id": int(row["queue_item_id"]),
            "company_path_normalized": str(row["company_path_normalized"]),
            "reporting_scope": str(row["reporting_scope"]),
            "result_period": str(row["result_period"]),
            "status": str(row["status"]),
            "attempt_count": int(row["attempt_count"]),
            "last_error": json.loads(row["last_error_json"] or "{}").get("error", {}),
        }
        for row in rows
    )


def _requeue_select(
    statuses: tuple[str, ...], paths: tuple[str, ...], limit: int | None
) -> tuple[str, tuple[Any, ...]]:
    if not statuses:
        raise ValueError("At least one status is required")
    unknown = set(statuses) - set(REQUEUEABLE_STATUSES)
    if unknown:
        raise ValueError(
            f"Only settled failures can be requeued; refusing {sorted(unknown)}"
        )
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    clauses = [f"status IN ({','.join('?' * len(statuses))})"]
    params: list[Any] = list(statuses)
    if paths:
        clauses.append(f"company_path_normalized IN ({','.join('?' * len(paths))})")
        params.extend(paths)
    sql = (
        "SELECT queue_item_id, company_path_normalized, reporting_scope, "
        "result_period, status, attempt_count, last_error_json "
        f"FROM screener_company_queue WHERE {' AND '.join(clauses)} "
        "ORDER BY result_period DESC, queue_item_id"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return sql, tuple(params)


def requeue_failed_companies(
    conn: sqlite3.Connection,
    *,
    now_utc: str,
    statuses: tuple[str, ...] = ("failed_terminal",),
    paths: tuple[str, ...] = (),
    limit: int | None = None,
    reason: str = "operator_requeue",
) -> tuple[dict[str, Any], ...]:
    """Return settled failures to `pending` so the worker retries them.

    Claimed and running items are never touched: their lease still belongs to a
    worker. The attempt count and the failing error are preserved on the row so
    the requeue does not erase why the item failed the first time.
    """
    candidates = preview_requeue_candidates(
        conn, statuses=statuses, paths=paths, limit=limit
    )
    if not candidates:
        return ()
    requeued: list[dict[str, Any]] = []
    with conn:
        for item in candidates:
            cursor = conn.execute(
                """UPDATE screener_company_queue SET status='pending',
                   next_attempt_at_utc=?, claim_token=NULL, claimed_at_utc=NULL,
                   claim_expires_at_utc=NULL, updated_at_utc=?
                   WHERE queue_item_id=? AND status=?""",
                (now_utc, now_utc, item["queue_item_id"], item["status"]),
            )
            if cursor.rowcount != 1:
                continue
            conn.execute(
                """INSERT INTO screener_company_queue_events
                   (queue_item_id, event_at_utc, from_status, to_status,
                    evidence_json) VALUES (?, ?, ?, 'pending', ?)""",
                (
                    item["queue_item_id"],
                    now_utc,
                    item["status"],
                    _json({"reason": reason, "requeued_from": item["status"]}),
                ),
            )
            requeued.append(item)
    return tuple(requeued)


# The custom ratio panel is fetched as a separate AJAX fragment, so a company
# page accepted before that fetch existed carries only Screener's nine public
# ratios. The gap is a provenance fact, not a metric count: an accepting run
# with no successful `quick_ratios` fetch never got the panel. A run that did
# get one and found it empty is a company with no custom ratios, and is
# deliberately not a candidate -- refetching it would change nothing. The
# status filter matters because a failed attempt also records a fetch row.
_INCOMPLETE_METRIC_SELECT = """
    SELECT q.queue_item_id, q.company_path_normalized, q.reporting_scope,
           q.result_period, q.status, q.attempt_count, q.last_error_json,
           s.latest_accepted_parse_id AS parse_id,
           (SELECT COUNT(*) FROM company_metric_observations cmo
             WHERE cmo.parse_id = s.latest_accepted_parse_id) AS metric_count
      FROM screener_company_queue q
      JOIN screener_company_collection_state s
        ON s.company_path_normalized = q.company_path_normalized
       AND s.reporting_scope = q.reporting_scope
      JOIN parse_artifacts p ON p.parse_id = s.latest_accepted_parse_id
      JOIN fetch_observations f
        ON f.blob_id = p.blob_id AND f.resource_kind = 'company_page'
     WHERE q.status = 'accepted'
       AND NOT EXISTS (
             SELECT 1 FROM fetch_observations qr
              WHERE qr.run_id = f.run_id
                AND qr.resource_kind = 'quick_ratios'
                AND qr.fetch_status = 'ok'
           )
"""


def _incomplete_metric_select(
    paths: tuple[str, ...], limit: int | None
) -> tuple[str, tuple[Any, ...]]:
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    sql = _INCOMPLETE_METRIC_SELECT
    params: list[Any] = []
    if paths:
        sql += f" AND q.company_path_normalized IN ({','.join('?' * len(paths))})"
        params.extend(paths)
    sql += " ORDER BY q.result_period DESC, q.queue_item_id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return sql, tuple(params)


def preview_incomplete_metric_candidates(
    conn: sqlite3.Connection,
    *,
    paths: tuple[str, ...] = (),
    limit: int | None = None,
) -> tuple[dict[str, Any], ...]:
    """List accepted companies whose accepted parse never fetched the ratio panel."""
    rows = conn.execute(*_incomplete_metric_select(paths, limit)).fetchall()
    return tuple(
        {
            "queue_item_id": int(row["queue_item_id"]),
            "company_path_normalized": str(row["company_path_normalized"]),
            "reporting_scope": str(row["reporting_scope"]),
            "result_period": str(row["result_period"]),
            "status": str(row["status"]),
            "attempt_count": int(row["attempt_count"]),
            "accepted_parse_id": int(row["parse_id"]),
            "metric_count": int(row["metric_count"]),
        }
        for row in rows
    )


def requeue_incomplete_metric_companies(
    conn: sqlite3.Connection,
    *,
    now_utc: str,
    paths: tuple[str, ...] = (),
    limit: int | None = None,
    reason: str = "quick_ratios_backfill",
) -> tuple[dict[str, Any], ...]:
    """Return metric-incomplete acceptances to `pending` for a full re-scrape.

    Unlike `requeue_failed_companies` this moves items out of `accepted`, so it
    stays narrow on purpose: only the missing-panel provenance gap qualifies,
    and the existing acceptance stays the current pointer until the re-scrape
    produces a new one.
    """
    candidates = preview_incomplete_metric_candidates(conn, paths=paths, limit=limit)
    if not candidates:
        return ()
    requeued: list[dict[str, Any]] = []
    with conn:
        for item in candidates:
            cursor = conn.execute(
                """UPDATE screener_company_queue SET status='pending',
                   next_attempt_at_utc=?, claim_token=NULL, claimed_at_utc=NULL,
                   claim_expires_at_utc=NULL, updated_at_utc=?
                   WHERE queue_item_id=? AND status='accepted'""",
                (now_utc, now_utc, item["queue_item_id"]),
            )
            if cursor.rowcount != 1:
                continue
            conn.execute(
                """INSERT INTO screener_company_queue_events
                   (queue_item_id, event_at_utc, from_status, to_status,
                    evidence_json) VALUES (?, ?, 'accepted', 'pending', ?)""",
                (
                    item["queue_item_id"],
                    now_utc,
                    _json(
                        {
                            "reason": reason,
                            "requeued_from": "accepted",
                            "accepted_parse_id": item["accepted_parse_id"],
                            "metric_count": item["metric_count"],
                            "missing_resource_kind": "quick_ratios",
                        }
                    ),
                ),
            )
            requeued.append(item)
    return tuple(requeued)


def release_claims(
    conn: sqlite3.Connection,
    items: tuple[ClaimedCompany, ...],
    *,
    worker_id: str,
    now_utc: str,
) -> None:
    with conn:
        for item in items:
            cursor = conn.execute(
                """UPDATE screener_company_queue SET status='pending', claim_token=NULL,
                   claimed_at_utc=NULL, claim_expires_at_utc=NULL, updated_at_utc=?
                   WHERE queue_item_id=? AND status='claimed' AND claim_token=?""",
                (now_utc, item.queue_item_id, item.claim_token),
            )
            if cursor.rowcount:
                conn.execute(
                    """INSERT INTO screener_company_queue_events
                       (queue_item_id, event_at_utc, from_status, to_status,
                        worker_id, evidence_json)
                       VALUES (?, ?, 'claimed', 'pending', ?, '{"reason":"batch_stopped"}')""",
                    (item.queue_item_id, now_utc, worker_id),
                )
