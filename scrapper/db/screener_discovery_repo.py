"""Persistence and overlap-safe queue primitives for Screener discovery."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..sources.screener.result_screen import ParsedResultScreen


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


def enqueue_screen_candidates(
    conn: sqlite3.Connection,
    screen_run_id: int,
    *,
    now_utc: str,
) -> dict[str, int]:
    screen = conn.execute(
        "SELECT coverage_status FROM screener_screen_runs WHERE screen_run_id=?",
        (screen_run_id,),
    ).fetchone()
    if screen is None:
        raise ValueError(f"Unknown screen run: {screen_run_id}")
    counts = {"new": 0, "advanced": 0, "unchanged": 0, "stale": 0, "reused": 0}
    if screen["coverage_status"] != "complete":
        return counts
    candidates = conn.execute(
        """SELECT * FROM screener_result_candidates
           WHERE screen_run_id=? ORDER BY source_position""",
        (screen_run_id,),
    ).fetchall()
    with conn:
        for item in candidates:
            path = str(item["company_path_normalized"])
            scope = str(item["reporting_scope_hint"])
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
            if accepted is not None and period < accepted:
                counts["stale"] += 1
                continue
            if accepted == period:
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
           WHERE (
             status IN ('pending', 'failed_retryable')
             AND (next_attempt_at_utc IS NULL OR next_attempt_at_utc <= ?)
           ) OR (status IN ('claimed', 'running') AND claim_expires_at_utc <= ?)
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
) -> tuple[ClaimedCompany, ...]:
    if not worker_id.strip() or limit < 1 or lease_seconds < 1:
        raise ValueError("worker_id, positive limit and lease are required")
    expires = (_parse_utc(now_utc) + timedelta(seconds=lease_seconds)).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        ids = [
            int(row["queue_item_id"])
            for row in conn.execute(
                """SELECT queue_item_id FROM screener_company_queue
                   WHERE (
                     status IN ('pending', 'failed_retryable')
                     AND (next_attempt_at_utc IS NULL OR next_attempt_at_utc <= ?)
                   ) OR (status IN ('claimed', 'running') AND claim_expires_at_utc <= ?)
                   ORDER BY result_period DESC, queue_item_id LIMIT ?""",
                (now_utc, now_utc, limit),
            ).fetchall()
        ]
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
        """SELECT 1 FROM accepted_page_versions
           WHERE company_id=? AND source='screener' AND reporting_scope=?
             AND resolved_source_period=? AND parse_id=?""",
        (company_id, item.reporting_scope, item.result_period, parse_id),
    ).fetchone()
    if accepted is None:
        raise ValueError(
            "Queue acceptance requires the full company pipeline's accepted parse"
        )
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
                company_id, item.result_period, item.result_period,
                item.result_period, parse_id, now_utc,
                item.company_path_normalized, item.reporting_scope,
            ),
        )
        conn.execute(
            """INSERT INTO screener_company_queue_events
               (queue_item_id, event_at_utc, from_status, to_status, worker_id,
                evidence_json) VALUES (?, ?, 'running', 'accepted', ?, ?)""",
            (item.queue_item_id, now_utc, worker_id, _json({"parse_id": parse_id, "company_id": company_id})),
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
