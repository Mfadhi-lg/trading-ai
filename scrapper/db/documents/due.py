"""Due selection, leases and completed-check persistence (D09)."""
from __future__ import annotations
import sqlite3
import uuid
from datetime import timedelta
from urllib.parse import urlsplit
from ...sources.screener.result_screen import canonical_company_path
from typing import Any, Iterable
from ...domain.artifact_reconciliation import ClaimedExpectation, ScreenerRefreshTarget
from ...documents.availability import ArtifactCheckResult, parse_utc, format_utc
from .expectations import canonical_json
from .readiness import _accepted_artifact
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)

_DUE_SELECT = """SELECT expectation.*, event.event_key, event.event_type,
                         event.event_date, event.period_end, event.fiscal_year,
                         event.fiscal_quarter
                  FROM artifact_expectations AS expectation
                  JOIN artifact_events AS event USING (event_id)
                  WHERE expectation.next_check_at_utc IS NOT NULL
                    AND expectation.next_check_at_utc <= ?
                    AND (expectation.claim_token IS NULL
                         OR expectation.claim_expires_at_utc <= ?)
                  ORDER BY CASE
                             WHEN expectation.artifact_type IN
                                  ('presentation', 'transcript') THEN 0
                             ELSE 1
                           END,
                           expectation.next_check_at_utc,
                           expectation.event_id,
                           expectation.expectation_id
                  LIMIT ?"""

_DUE_SELECT_NATIVE = "SELECT expectation.*, event.event_key, event.event_type,\n                         event.event_date, event.period_end, event.fiscal_year,\n                         event.fiscal_quarter\n                  FROM artifact_expectations AS expectation\n                  JOIN artifact_events AS event USING (event_id)\n                  WHERE expectation.next_check_at_utc IS NOT NULL\n                    AND CAST(expectation.next_check_at_utc AS timestamptz) <= CAST(? AS timestamptz)\n                    AND (expectation.claim_token IS NULL\n                         OR CAST(expectation.claim_expires_at_utc AS timestamptz) <= CAST(? AS timestamptz))\n                  ORDER BY CASE\n                             WHEN expectation.artifact_type IN\n                                  ('presentation', 'transcript') THEN 0\n                             ELSE 1\n                           END,\n                           expectation.next_check_at_utc,\n                           expectation.event_id,\n                           expectation.expectation_id\n                  LIMIT ?"


@atomic_repository('documents')
def preview_due_expectations(
    conn: sqlite3.Connection, *, now_utc: str, limit: int,
    ClaimedExpectation=ClaimedExpectation, _DUE_SELECT=_DUE_SELECT, parse_utc=parse_utc,
) -> tuple[ClaimedExpectation, ...]:
    if limit < 1:
        raise ValueError("Limit must be positive")
    parse_utc(now_utc)
    return tuple(
        ClaimedExpectation.from_row(row)
        for row in execute(conn, statement(conn, _DUE_SELECT, _DUE_SELECT_NATIVE) if _DUE_SELECT == globals()['_DUE_SELECT'] else _DUE_SELECT, (now_utc, now_utc, limit)).fetchall()
    )


@atomic_repository('documents')
def claim_due_expectations(
    conn: sqlite3.Connection,
    *,
    now_utc: str,
    limit: int,
    worker_id: str,
    ClaimedExpectation=ClaimedExpectation, _DUE_SELECT=_DUE_SELECT, format_utc=format_utc, parse_utc=parse_utc, timedelta=timedelta, uuid=uuid,
) -> tuple[ClaimedExpectation, ...]:
    """Atomically lease only due rows; concurrent SQLite workers cannot overlap."""

    if limit < 1:
        raise ValueError("Limit must be positive")
    if not worker_id.strip():
        raise ValueError("Worker id is required")
    now = parse_utc(now_utc)
    claimed: list[ClaimedExpectation] = []
    begin_immediate(conn, lock='documents')
    try:
        rows = execute(conn, statement(conn, _DUE_SELECT, _DUE_SELECT_NATIVE) if _DUE_SELECT == globals()['_DUE_SELECT'] else _DUE_SELECT, (now_utc, now_utc, limit)).fetchall()
        for row in rows:
            persisted_policy = ClaimedExpectation.from_row(row).retry_policy()
            expires = format_utc(
                now + timedelta(seconds=persisted_policy.claim_lease_seconds)
            )
            token = f"{worker_id}:{uuid.uuid4().hex}"
            changed = execute(conn,
                statement(conn, """UPDATE artifact_expectations
                   SET claim_token=?, claimed_at_utc=?, claim_expires_at_utc=?,
                       updated_at_utc=?
                   WHERE expectation_id=?
                     AND (claim_token IS NULL OR claim_expires_at_utc <= ?)""", 'UPDATE artifact_expectations\n                   SET claim_token=?, claimed_at_utc=?, claim_expires_at_utc=?,\n                       updated_at_utc=?\n                   WHERE expectation_id=?\n                     AND (claim_token IS NULL OR CAST(claim_expires_at_utc AS timestamptz) <= CAST(? AS timestamptz))'),
                (token, now_utc, expires, now_utc, int(row["expectation_id"]), now_utc),
            ).rowcount
            if changed:
                claimed.append(
                    ClaimedExpectation.from_row(
                        execute(conn,
                            """SELECT expectation.*, event.event_key,
                                      event.event_type, event.event_date,
                                      event.period_end, event.fiscal_year,
                                      event.fiscal_quarter
                               FROM artifact_expectations AS expectation
                               JOIN artifact_events AS event USING (event_id)
                               WHERE expectation.expectation_id=?""",
                            (int(row["expectation_id"]),),
                        ).fetchone()
                    )
                )
        commit(conn)
    except Exception:
        rollback(conn)
        raise
    return tuple(claimed)


@atomic_repository('documents')
def complete_artifact_check(
    conn: sqlite3.Connection,
    *,
    expectation: ClaimedExpectation,
    result: ArtifactCheckResult,
    checked_at_utc: str,
    canonical_json=canonical_json, parse_utc=parse_utc,
) -> int:
    if expectation.claim_token is None:
        raise ValueError("A claim token is required to complete a check")
    parse_utc(checked_at_utc)
    completed_attempts = expectation.attempt_count + 1
    persisted_policy = expectation.retry_policy()
    next_check, terminal_reason = persisted_policy.next_after_result(
        expected_after_utc=expectation.expected_after_utc,
        completed_attempt_count=completed_attempts,
        status=result.result_status,
        retryable=result.retryable,
        checked_at_utc=checked_at_utc,
    )
    with repository_transaction(conn):
        current = execute(conn,
            "SELECT claim_token FROM artifact_expectations WHERE expectation_id=?",
            (expectation.expectation_id,),
        ).fetchone()
        if current is None or current["claim_token"] != expectation.claim_token:
            raise RuntimeError(
                f"Lost reconciliation claim for expectation {expectation.expectation_id}"
            )
        cursor = execute(conn,
            """INSERT INTO artifact_check_events
               (expectation_id, checked_at_utc, source, requested_url,
                result_status, evidence_json, discovered_artifact_id,
                error_json, retryable, next_check_at_utc, claim_token)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                expectation.expectation_id,
                checked_at_utc,
                result.source,
                result.requested_url,
                result.result_status,
                canonical_json(result.evidence_payload()),
                result.discovered_artifact_id,
                canonical_json(result.error_payload()),
                int(result.retryable),
                next_check,
                expectation.claim_token,
            ),
         returning='check_id')
        execute(conn,
            """UPDATE artifact_expectations
               SET availability_status=?,
                   first_checked_at_utc=COALESCE(first_checked_at_utc, ?),
                   last_checked_at_utc=?, next_check_at_utc=?, attempt_count=?,
                   terminal_reason=?, last_error_json=?, claim_token=NULL,
                   claimed_at_utc=NULL, claim_expires_at_utc=NULL,
                   updated_at_utc=?
               WHERE expectation_id=? AND claim_token=?""",
            (
                result.result_status,
                checked_at_utc,
                checked_at_utc,
                next_check,
                completed_attempts,
                terminal_reason,
                canonical_json(result.error_payload()),
                checked_at_utc,
                expectation.expectation_id,
                expectation.claim_token,
            ),
        )
    return generated_id(cursor, 'check_id')


@atomic_repository('documents')
def release_claims(
    conn: sqlite3.Connection,
    expectations: Iterable[ClaimedExpectation],
    *,
    released_at_utc: str,
) -> None:
    """Release unprocessed leases after an orderly worker interruption."""

    with repository_transaction(conn):
        for row in expectations:
            if row.claim_token is None:
                continue
            execute(conn,
                """UPDATE artifact_expectations
                   SET claim_token=NULL, claimed_at_utc=NULL,
                       claim_expires_at_utc=NULL, updated_at_utc=?
                   WHERE expectation_id=? AND claim_token=?""",
                (released_at_utc, row.expectation_id, row.claim_token),
            )


@atomic_repository('documents')
def _source_refresh_targets(
    conn: sqlite3.Connection,
    due_rows: tuple[ClaimedExpectation, ...],
    *,
    ScreenerRefreshTarget=ScreenerRefreshTarget, _accepted_artifact=_accepted_artifact,
) -> tuple[ScreenerRefreshTarget, ...]:
    """Group due PPT/transcript checks into one Screener fetch per event."""

    grouped: dict[tuple[int, int], list[ClaimedExpectation]] = {}
    for expectation in due_rows:
        if expectation.artifact_type not in {"presentation", "transcript"}:
            continue
        if _accepted_artifact(conn, expectation) is not None:
            continue
        grouped.setdefault(
            (expectation.company_id, expectation.event_id), []
        ).append(expectation)

    targets: list[ScreenerRefreshTarget] = []
    for (company_id, event_id), expectations in grouped.items():
        state = execute(conn,
            """SELECT company_path_normalized, reporting_scope,
                      latest_accepted_result_period
                 FROM screener_company_collection_state
                WHERE company_id=? AND reporting_scope='standalone'
                ORDER BY updated_at_utc DESC LIMIT 1""",
            (company_id,),
        ).fetchone()
        if state is None or not state["latest_accepted_result_period"]:
            continue
        # Identity-verified canonical URLs survive ticker/slug changes; the
        # financial collection state can retain an obsolete path indefinitely.
        mapping = execute(conn, """SELECT canonical_url FROM source_identifiers
            WHERE company_id=? AND source='screener' AND mapping_status='verified'""",
            (company_id,)).fetchone()
        company_path = str(state["company_path_normalized"])
        if mapping:
            url = urlsplit(mapping["canonical_url"])
            if (url.scheme == 'https' and url.netloc == 'www.screener.in'
                    and not url.query and not url.fragment):
                try:
                    company_path = canonical_company_path(url.path)
                except ValueError:
                    pass
        period_end = expectations[0].period_end
        result_period = (
            str(period_end)[:7]
            if period_end
            else str(state["latest_accepted_result_period"])
        )
        if result_period == "1970-01":
            # Exchange rows without an exact period use this explicit sentinel.
            # It is not safe evidence for attaching the newest call documents.
            continue
        targets.append(
            ScreenerRefreshTarget(
                company_id=company_id,
                event_id=event_id,
                company_path=company_path,
                reporting_scope=str(state["reporting_scope"]),
                result_period=result_period,
                expectation_ids=tuple(
                    sorted(item.expectation_id for item in expectations)
                ),
            )
        )
    return tuple(targets)


@atomic_repository('documents')
def checked_expectation_state(conn: sqlite3.Connection, expectation: ClaimedExpectation) -> sqlite3.Row:
    """Read persisted counters after successful completion; no transaction added."""
    return execute(conn,
        """SELECT availability_status, attempt_count, next_check_at_utc,
                  terminal_reason FROM artifact_expectations
           WHERE expectation_id=?""",
        (expectation.expectation_id,),
    ).fetchone()
