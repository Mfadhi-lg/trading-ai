"""Exchange schedule attempt history and publication queries; no scheduling startup."""
from __future__ import annotations

import sqlite3
from typing import Any

SCHEDULE_OUTCOMES = (
    "published_changed", "published_unchanged", "already_refreshed",
    "not_published", "guard_failed", "fetch_failed", "error", "skipped_no_url",
    "unrefreshed", "source_access_blocked",
)

SATISFYING_OUTCOMES = ("published_changed", "published_unchanged")

SCHEDULE_ROLES = ("primary", "revision")

def record_schedule_run(
    conn: sqlite3.Connection,
    *,
    dataset: str,
    scheduled_for: str,
    attempt: int,
    outcome: str,
    snapshot_id: int | None,
    error: str,
    started_at_utc: str,
    finished_at_utc: str,
    result_period: str = "",
    role: str = "primary",
    SCHEDULE_OUTCOMES=SCHEDULE_OUTCOMES, SCHEDULE_ROLES=SCHEDULE_ROLES,
) -> None:
    """Write one attempt's outcome to the schedule ledger.

    A run is identified by its role as well as its rung, because a revision
    sweep re-reads an already-collected period alongside the day's primary run
    and is a different fact about a different period.
    """
    if outcome not in SCHEDULE_OUTCOMES:
        raise ValueError(f"Unknown schedule outcome: {outcome!r}")
    if role not in SCHEDULE_ROLES:
        raise ValueError(f"Unknown schedule role: {role!r}")
    with conn:
        conn.execute(
            """INSERT INTO exchange_schedule_runs
               (dataset, scheduled_for, attempt, role, result_period, outcome,
                snapshot_id, error, started_at_utc, finished_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(dataset, scheduled_for, attempt, role) DO UPDATE SET
                 result_period=excluded.result_period, outcome=excluded.outcome,
                 snapshot_id=excluded.snapshot_id,
                 error=excluded.error, started_at_utc=excluded.started_at_utc,
                 finished_at_utc=excluded.finished_at_utc""",
            (
                dataset, scheduled_for, attempt, role, result_period, outcome,
                snapshot_id, error, started_at_utc, finished_at_utc,
            ),
        )


def datasets_published_on(
    conn: sqlite3.Connection, scheduled_for: str, *,
    SATISFYING_OUTCOMES=SATISFYING_OUTCOMES,
) -> set[str]:
    """Datasets an earlier primary attempt already got a publication of.

    A revision sweep re-reads an old period and says nothing about whether today
    was published, so it never satisfies the day.
    """
    placeholders = ", ".join("?" for _ in SATISFYING_OUTCOMES)
    return {
        str(row["dataset"])
        for row in conn.execute(
            f"""SELECT DISTINCT dataset FROM exchange_schedule_runs
                WHERE scheduled_for=? AND role='primary'
                  AND outcome IN ({placeholders})""",
            (scheduled_for, *SATISFYING_OUTCOMES),
        )
    }


def schedule_day(conn: sqlite3.Connection, scheduled_for: str) -> list[dict[str, Any]]:
    """Every recorded attempt for one scheduled day, oldest first."""
    return [
        dict(row)
        for row in conn.execute(
            """SELECT * FROM exchange_schedule_runs WHERE scheduled_for=?
               ORDER BY attempt, dataset""",
            (scheduled_for,),
        )
    ]
