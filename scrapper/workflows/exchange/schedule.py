"""Unattended refresh of the exchange universe, with a retry ladder.

Everything before this plan step was operator-triggered. Turning it on is only
safe because C1 rejects a bad file before it can rewrite state, C2 needs a streak
of absences to remove a security, C3 refuses to overwrite an operator's row, and
C6 reports zero open conflicts. This module adds the one remaining thing: a clock,
and a ledger of what it did.

The design rule is that a failure never degrades the universe. An attempt that
cannot fetch, or whose file the guard rejects, leaves the last accepted snapshot
exactly where it was and schedules a retry. Only after the final attempt is a
dataset recorded as unrefreshed, and that exits non-zero — a day on which an
exchange published nothing is a fact an operator has to see, not a silent no-op.

The scheduling logic is a pure function of an injected clock and fetcher, so the
retry ladder is tested without APScheduler, a network or a real day passing.
"""

from __future__ import annotations

import json
import sqlite3
import time
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from ...config import (
    DB_PATH,
    EXCHANGE_REPORT_DAY,
    EXCHANGE_REPORT_TIME,
    EXCHANGE_RESULT_REVISION_ATTEMPT,
    EXCHANGE_SCHEDULE_ATTEMPTS,
    EXCHANGE_SCHEDULE_EXPORTS,
    SNAPSHOTS,
)
from ...core.http import FetchResponse, fetch_exchange_dataset
from ...db.database import connect, init_db
from ...db.exchange.schedule import (
    SATISFYING_OUTCOMES,
    record_schedule_run,
    datasets_published_on,
    schedule_day,
)
from ...db.exchange_repo import (
    active_source_access_holds,
    record_source_access_event,
    snapshot_content_is_new,
)
from ...db.repo import utc_now
from ...exports.exchange_reconciliation_report import (
    write_exchange_reconciliation_report,
)
from ...exports.exchange_universe_coverage import write_exchange_universe_coverage
from ...sources.exchange.snapshot_guard import GuardThresholds
from ...sources.exchange.datasets import (
    CORPORATE_CHANGE,
    ON_DEMAND,
    RESULT_FILING,
    SECURITY_MASTER,
    DATASETS,
    dataset_config,
    default_url_for,
)
from ...sources.exchange.tabular import IST
from ...sources.exchange.reporting_periods import (
    current_result_period, previous_result_period, priority_result_periods,
)
from ...workflows.exchange.universe import (
    DatasetNotPublished,
    ExchangeSourceAccessBlocked,
    discover_exchange_results,
    refresh_exchange_universe,
)


SCHEDULER_SCHEMA_VERSION = "1.0.0"
DEFAULT_CATCHUP_DAYS = 7

# Result feeds run last on purpose. A filing for a company that listed this
# morning resolves to a registry entry only once the master that lists it has
# been folded in; the other order routes it to manual review for no reason.
KIND_ORDER = (SECURITY_MASTER, CORPORATE_CHANGE, RESULT_FILING)


@dataclass(frozen=True)
class ScheduleAttempt:
    """One rung of the retry ladder, in IST."""

    attempt: int
    ist_time: str
    day_offset: int
    final: bool

    def fires_at(self, scheduled_for: str) -> datetime:
        hour, minute = (int(part) for part in self.ist_time.split(":"))
        day = date.fromisoformat(scheduled_for) + timedelta(days=self.day_offset)
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "ist_time": self.ist_time,
            "day_offset": self.day_offset,
            "final": self.final,
        }


def _ladder(*, EXCHANGE_SCHEDULE_ATTEMPTS=EXCHANGE_SCHEDULE_ATTEMPTS) -> tuple[ScheduleAttempt, ...]:
    rungs = tuple(EXCHANGE_SCHEDULE_ATTEMPTS)
    return tuple(
        ScheduleAttempt(index, ist_time, day_offset, index == len(rungs))
        for index, (ist_time, day_offset) in enumerate(rungs, start=1)
    )


LADDER = _ladder()
FINAL_ATTEMPT = LADDER[-1].attempt

# The scheduler commonly runs on a developer machine, where Docker is paused
# while the host sleeps.  APScheduler otherwise drops a run that is more than
# an hour late, which leaves a whole trading day with no result poll even after
# the machine wakes.  Jobs derive their scheduled day from the current clock,
# so replaying a coalesced job after a wake-up remains idempotent and records
# the actual day being refreshed rather than fabricating a stale snapshot.
MISFIRE_GRACE_SECONDS = 7 * 24 * 60 * 60


def scheduled_datasets(*, DATASETS=DATASETS, KIND_ORDER=KIND_ORDER, ON_DEMAND=ON_DEMAND) -> tuple[str, ...]:
    """Every dataset the scheduler refreshes, in dependency order."""
    return tuple(
        config.key
        for kind in KIND_ORDER
        for config in DATASETS.values()
        if config.kind == kind and config.cadence != ON_DEMAND
    )


def ist_day(now_utc_value: str, *, IST=IST, datetime=datetime) -> str:
    """The IST trading day a UTC instant belongs to.

    The ladder's last rung fires the next morning, so it must still be working on
    the previous day's publication rather than starting a new one.
    """
    moment = datetime.fromisoformat(now_utc_value).astimezone(IST)
    return moment.date().isoformat()


def _run_one(dataset: str, *, scheduled_for: str, result_period: str, db_path: Path, snapshot_root: Path, now_utc: Callable[[], str], fetcher: Callable[[str, str], FetchResponse], thresholds: Callable[[str], GuardThresholds] | None, Any=Any, DatasetNotPublished=DatasetNotPublished, ExchangeSourceAccessBlocked=ExchangeSourceAccessBlocked, RESULT_FILING=RESULT_FILING, connect=connect, dataset_config=dataset_config, default_url_for=default_url_for, discover_exchange_results=discover_exchange_results, init_db=init_db, refresh_exchange_universe=refresh_exchange_universe, snapshot_content_is_new=snapshot_content_is_new) -> dict[str, Any]:
    """Refresh one dataset, classifying whatever happened into an outcome."""
    config = dataset_config(dataset)
    period = result_period if config.kind == RESULT_FILING else None
    try:
        url = default_url_for(dataset, result_period=period)
    except ValueError as exc:
        return {"outcome": "skipped_no_url", "error": str(exc), "snapshot_id": None}
    if url is None:
        # A dataset with no wired URL was never automatable; that is a decision
        # recorded in the registry, not a failure of this run.
        return {
            "outcome": "skipped_no_url",
            "error": f"{dataset} has no registry default URL",
            "snapshot_id": None,
        }
    options: dict[str, Any] = dict(
        dataset=dataset, url=url, effective_date=scheduled_for, db_path=db_path,
        snapshot_root=snapshot_root, now_utc=now_utc, fetcher=fetcher,
        thresholds=thresholds(dataset) if thresholds else None,
    )
    try:
        if config.kind == RESULT_FILING:
            payload = discover_exchange_results(**options, result_period=period)
        else:
            payload = refresh_exchange_universe(**options)
    except DatasetNotPublished as exc:
        # The endpoint answered, and its answer was that there is no artifact for
        # this day or period. That is the only evidence of non-publication the
        # scheduler gets, and it is the only thing recorded as one.
        return {"outcome": "not_published", "error": str(exc), "snapshot_id": None}
    except ExchangeSourceAccessBlocked as exc:
        return {"outcome": "source_access_blocked", "error": str(exc), "snapshot_id": None}
    except RuntimeError as exc:
        # Raised when the response never got as far as parsing: an interstitial,
        # a 403, an empty body. The previous snapshot stands untouched.
        return {"outcome": "fetch_failed", "error": str(exc), "snapshot_id": None}
    except (OSError, ValueError) as exc:
        return {"outcome": "error", "error": str(exc), "snapshot_id": None}

    snapshot_id = payload.get("snapshot_id")
    if (payload.get("validation") or {}).get("status") == "failed":
        codes = [item["code"] for item in payload["validation"]["findings"]]
        return {
            "outcome": "guard_failed",
            "error": f"snapshot rejected: {codes}",
            "snapshot_id": snapshot_id,
            "payload": payload,
        }
    conn = connect(db_path)
    try:
        init_db(conn)
        changed = snapshot_content_is_new(
            conn, dataset=dataset, snapshot_id=int(snapshot_id)
        )
    finally:
        conn.close()
    return {
        # The file exists and the guard accepted it, so the exchange published.
        # Whether its bytes moved is a separate fact, worth recording and worth
        # nothing else: a trading day with no listing, delisting or symbol change
        # produces a byte-identical master, and the day is satisfied either way.
        "outcome": "published_changed" if changed else "published_unchanged",
        "error": "" if changed else "published content identical to the previous file",
        "snapshot_id": snapshot_id,
        "payload": payload,
    }


def _attempt_dataset(dataset: str, *, scheduled_for: str, attempt: int, result_period: str, db_path: Path, snapshot_root: Path, now_utc: Callable[[], str], fetcher: Callable[[str, str], FetchResponse], thresholds: Callable[[str], GuardThresholds] | None, role: str='primary', RESULT_FILING=RESULT_FILING, _run_one=_run_one, connect=connect, dataset_config=dataset_config, init_db=init_db, record_schedule_run=record_schedule_run, record_source_access_event=record_source_access_event) -> dict[str, Any]:
    """Run one dataset for one rung and write what happened to the ledger."""
    started = now_utc()
    outcome = _run_one(
        dataset,
        scheduled_for=scheduled_for, result_period=result_period, db_path=db_path,
        snapshot_root=snapshot_root, now_utc=now_utc, fetcher=fetcher,
        thresholds=thresholds,
    )
    finished = now_utc()
    conn = connect(db_path)
    try:
        init_db(conn)
        if outcome["outcome"] == "source_access_blocked":
            # Persist the hold before the ledger row. A process interruption can
            # then never turn the next scheduled rung into another source call.
            record_source_access_event(
                conn, dataset=dataset, action="BLOCKED", event_at_utc=finished,
                evidence=str(outcome["error"]),
            )
        record_schedule_run(
            conn,
            dataset=dataset, scheduled_for=scheduled_for, attempt=attempt,
            role=role,
            result_period=(
                result_period
                if dataset_config(dataset).kind == RESULT_FILING
                else ""
            ),
            outcome=str(outcome["outcome"]),
            snapshot_id=outcome.get("snapshot_id"),
            error=str(outcome.get("error") or ""),
            started_at_utc=started, finished_at_utc=finished,
        )
    finally:
        conn.close()
    return {
        "dataset": dataset,
        "role": role,
        "outcome": str(outcome["outcome"]),
        "error": str(outcome.get("error") or ""),
        "snapshot_id": outcome.get("snapshot_id"),
        "registry": (outcome.get("payload") or {}).get("registry"),
        "enqueue": (outcome.get("payload") or {}).get("enqueue"),
    }


def revision_datasets(datasets: Sequence[str], *, RESULT_FILING=RESULT_FILING, dataset_config=dataset_config) -> tuple[str, ...]:
    """The feeds a revision sweep can ask about an already-collected period.

    Only a period-addressed feed can be asked for an old period at all; the BSE
    endpoint returns a rolling window of quarters on every call, so its
    revisions arrive with the day's primary run and re-fetching it would be the
    same request twice.
    """
    return tuple(
        dataset
        for dataset in datasets
        if dataset_config(dataset).kind == RESULT_FILING
        and dataset_config(dataset).url_needs_result_period
    )


def _sweep_revisions(datasets: Sequence[str], *, scheduled_for: str, attempt: int, result_period: str, db_path: Path, snapshot_root: Path, now_utc: Callable[[], str], fetcher: Callable[[str, str], FetchResponse], thresholds: Callable[[str], GuardThresholds] | None, EXCHANGE_RESULT_REVISION_ATTEMPT=EXCHANGE_RESULT_REVISION_ATTEMPT, _attempt_dataset=_attempt_dataset, revision_datasets=revision_datasets) -> list[dict[str, Any]]:
    """Re-read the previous reporting period once a day, on one rung only.

    A company can report late, correct or restate a filing after its quarter has
    closed. The watermark keeps the newer broadcast, so the only thing needed to
    pick those up is to keep asking — but at a fraction of the primary cadence,
    because the answer changes rarely.
    """
    if attempt != EXCHANGE_RESULT_REVISION_ATTEMPT:
        return []
    return [
        _attempt_dataset(
            dataset,
            scheduled_for=scheduled_for, attempt=attempt,
            result_period=result_period, db_path=db_path,
            snapshot_root=snapshot_root, now_utc=now_utc, fetcher=fetcher,
            thresholds=thresholds, role="revision",
        )
        for dataset in revision_datasets(datasets)
    ]


def run_attempt(*, attempt: int, scheduled_for: str | None=None, datasets: Sequence[str] | None=None, result_period: str | None=None, db_path: Path=DB_PATH, snapshot_root: Path=SNAPSHOTS, now_utc: Callable[[], str]=utc_now, fetcher: Callable[[str, str], FetchResponse]=fetch_exchange_dataset, thresholds: Callable[[str], GuardThresholds] | None=None, Any=Any, FINAL_ATTEMPT=FINAL_ATTEMPT, SATISFYING_OUTCOMES=SATISFYING_OUTCOMES, SCHEDULER_SCHEMA_VERSION=SCHEDULER_SCHEMA_VERSION, _attempt_dataset=_attempt_dataset, _sweep_revisions=_sweep_revisions, active_source_access_holds=active_source_access_holds, connect=connect, current_result_period=current_result_period, datasets_published_on=datasets_published_on, init_db=init_db, ist_day=ist_day, previous_result_period=previous_result_period, record_schedule_run=record_schedule_run, scheduled_datasets=scheduled_datasets) -> dict[str, Any]:
    """Run one rung of the ladder over every dataset not already refreshed."""
    started = now_utc()
    scheduled_for = scheduled_for or ist_day(started)
    priorities = priority_result_periods(scheduled_for=scheduled_for, override=result_period)
    period = priorities[0]
    wanted = tuple(datasets) if datasets is not None else scheduled_datasets()
    final = attempt >= FINAL_ATTEMPT

    conn = connect(db_path)
    try:
        init_db(conn)
        done = datasets_published_on(conn, scheduled_for)
        holds = active_source_access_holds(conn)
    finally:
        conn.close()

    results: list[dict[str, Any]] = []
    for dataset in wanted:
        if dataset in holds:
            results.append({
                "dataset": dataset, "outcome": "source_access_blocked",
                "error": holds[dataset]["evidence"], "snapshot_id": None,
                "access_hold_since_utc": holds[dataset]["event_at_utc"],
            })
            continue
        if dataset in done:
            results.append(
                {"dataset": dataset, "outcome": "already_refreshed", "error": "",
                 "snapshot_id": None}
            )
            continue
        results.append(
            _attempt_dataset(
                dataset,
                scheduled_for=scheduled_for, attempt=attempt, result_period=period,
                db_path=db_path, snapshot_root=snapshot_root, now_utc=now_utc,
                fetcher=fetcher, thresholds=thresholds,
            )
        )

    newly_blocked = {item["dataset"] for item in results
                     if item["outcome"] == "source_access_blocked"}
    revision_period = priorities[1] if len(priorities) > 1 else None
    revisions = [] if revision_period is None else _sweep_revisions(
        tuple(dataset for dataset in wanted
              if dataset not in holds and dataset not in newly_blocked),
        scheduled_for=scheduled_for, attempt=attempt, result_period=revision_period,
        db_path=db_path, snapshot_root=snapshot_root, now_utc=now_utc,
        fetcher=fetcher, thresholds=thresholds,
    )

    # A dataset that is skipped for want of a URL was never expected to publish,
    # so it is reported but does not make the day incomplete. Nor does a revision
    # sweep: it re-reads a period that was already collected, and a failure there
    # costs a delay in noticing a restatement, not today's universe.
    satisfied = SATISFYING_OUTCOMES + ("already_refreshed", "skipped_no_url")
    blocked = [item["dataset"] for item in results
               if item["outcome"] == "source_access_blocked"]
    retryable = [item["dataset"] for item in results
                 if item["outcome"] not in satisfied
                 and item["outcome"] != "source_access_blocked"]
    outstanding = blocked + retryable
    if final and retryable:
        conn = connect(db_path)
        try:
            init_db(conn)
            for dataset in retryable:
                record_schedule_run(
                    conn,
                    dataset=dataset, scheduled_for=scheduled_for,
                    attempt=attempt + 1, outcome="unrefreshed", snapshot_id=None,
                    error="no accepted publication after the final attempt",
                    started_at_utc=started, finished_at_utc=now_utc(),
                )
        finally:
            conn.close()
    return {
        "schema_version": SCHEDULER_SCHEMA_VERSION,
        "job": "attempt",
        "attempt": attempt,
        "final_attempt": final,
        "scheduled_for": scheduled_for,
        "result_period": period,
        "revision_period": revision_period,
        "started_at_utc": started,
        "finished_at_utc": now_utc(),
        "datasets": results,
        "revisions": revisions,
        "outstanding": outstanding,
        "access_blocked_datasets": blocked,
        "access_blocked_action": (
            "Keep blocked datasets held; continue unaffected sources. Review official "
            "access permission/configuration before a separately authorized, "
            "evidence-backed source resume. BSE-only coverage remains unproven."
            if blocked else None
        ),
        "retry_scheduled": bool(retryable) and not final,
        "status": ("incomplete" if final and retryable else
                   "access_blocked" if blocked else "ok"),
    }


def catch_up_missed_days(*, db_path: Path=DB_PATH, snapshot_root: Path=SNAPSHOTS, max_days: int=DEFAULT_CATCHUP_DAYS, now_utc: Callable[[], str]=utc_now, fetcher: Callable[[str, str], FetchResponse]=fetch_exchange_dataset, thresholds: Callable[[str], GuardThresholds] | None=None, Any=Any, FINAL_ATTEMPT=FINAL_ATTEMPT, SCHEDULER_SCHEMA_VERSION=SCHEDULER_SCHEMA_VERSION, connect=connect, date=date, init_db=init_db, ist_day=ist_day, run_attempt=run_attempt, time=time, timedelta=timedelta) -> dict[str, Any]:
    """Run final attempts for recent trading days missed while offline.

    APScheduler can coalesce jobs only while its process is alive.  A container
    restart used to leave missed days absent from the ledger, so their result
    filings never reached the company queue.  Catch-up is bounded, skips
    Sundays, and never replays a day that already has a primary attempt.
    """
    if max_days < 1:
        raise ValueError("max_days must be positive")
    started = now_utc()
    today = date.fromisoformat(ist_day(started))
    candidate_days = tuple(
        today - timedelta(days=offset)
        for offset in range(1, max_days + 1)
        if (today - timedelta(days=offset)).weekday() != 6
    )
    conn = connect(db_path)
    try:
        init_db(conn)
        latest_row = conn.execute(
            """SELECT MAX(scheduled_for) AS scheduled_for
               FROM exchange_schedule_runs WHERE role='primary'"""
        ).fetchone()
        latest_recorded_day = (
            str(latest_row["scheduled_for"])
            if latest_row is not None and latest_row["scheduled_for"] is not None
            else None
        )
        if candidate_days:
            placeholders = ",".join("?" for _ in candidate_days)
            seen = {
                str(row["scheduled_for"])
                for row in conn.execute(
                    f"""SELECT DISTINCT scheduled_for FROM exchange_schedule_runs
                        WHERE role='primary' AND scheduled_for IN ({placeholders})""",
                    tuple(day.isoformat() for day in candidate_days),
                ).fetchall()
            }
        else:
            seen = set()
    finally:
        conn.close()
    missed = sorted(
        day.isoformat()
        for day in candidate_days
        if day.isoformat() not in seen
        and (latest_recorded_day is None or day.isoformat() > latest_recorded_day)
    )
    attempts: list[dict[str, Any]] = []
    for scheduled_for in missed:
        for lock_attempt in range(4):
            try:
                attempts.append(
                    run_attempt(
                        attempt=FINAL_ATTEMPT,
                        scheduled_for=scheduled_for,
                        db_path=db_path,
                        snapshot_root=snapshot_root,
                        now_utc=now_utc,
                        fetcher=fetcher,
                        thresholds=thresholds,
                    )
                )
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or lock_attempt == 3:
                    attempts.append(
                        {
                            "scheduled_for": scheduled_for,
                            "status": "incomplete",
                            "error": f"catch-up database lock: {exc}",
                        }
                    )
                    break
                time.sleep(lock_attempt + 1)
    return {
        "schema_version": SCHEDULER_SCHEMA_VERSION,
        "job": "catch_up",
        "started_at_utc": started,
        "max_days": max_days,
        "latest_recorded_day": latest_recorded_day,
        "missed_days": missed,
        "attempts": attempts,
        "status": "ok" if all(item["status"] == "ok" for item in attempts) else "incomplete",
    }


def run_weekly_report(*, db_path: Path=DB_PATH, output_root: Path=EXCHANGE_SCHEDULE_EXPORTS, now_utc: Callable[[], str]=utc_now, SCHEDULER_SCHEMA_VERSION=SCHEDULER_SCHEMA_VERSION, connect=connect, init_db=init_db, ist_day=ist_day, json=json, write_exchange_reconciliation_report=write_exchange_reconciliation_report, write_exchange_universe_coverage=write_exchange_universe_coverage) -> dict[str, Any]:
    """The weekly gate: reconcile the registry and re-measure its coverage.

    The reconciliation report is the one that can block — it exits non-zero while
    an identity conflict is open — and the coverage report is the exchange-side
    quality measure that says whether the universe is whole.
    """
    started = now_utc()
    day = ist_day(started)
    output_root.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        init_db(conn)
        reconciliation = write_exchange_reconciliation_report(
            conn, output_root / f"{day}-exchange-reconciliation.json"
        )
        coverage = write_exchange_universe_coverage(
            conn, output_root / f"{day}-exchange-universe-coverage.json"
        )
    finally:
        conn.close()
    report = json.loads(reconciliation.read_text(encoding="utf-8"))
    return {
        "schema_version": SCHEDULER_SCHEMA_VERSION,
        "job": "weekly_report",
        "scheduled_for": day,
        "started_at_utc": started,
        "finished_at_utc": now_utc(),
        "reconciliation_report": str(reconciliation.resolve()),
        "coverage_report": str(coverage.resolve()),
        "reconciliation_status": report["reconciliation_status"],
        "open_conflict_count": report["summary"]["open_conflict_count"],
        "status": "ok" if report["reconciliation_status"] == "clear" else "blocked",
    }


def day_status(scheduled_for: str | None=None, *, db_path: Path=DB_PATH, now_utc: Callable[[], str]=utc_now, IST=IST, LADDER=LADDER, SCHEDULER_SCHEMA_VERSION=SCHEDULER_SCHEMA_VERSION, active_source_access_holds=active_source_access_holds, connect=connect, datasets_published_on=datasets_published_on, date=date, datetime=datetime, init_db=init_db, ist_day=ist_day, schedule_day=schedule_day, scheduled_datasets=scheduled_datasets, timedelta=timedelta, timezone=timezone) -> dict[str, Any]:
    """Every attempt recorded for one scheduled day."""
    day = scheduled_for or ist_day(now_utc())
    day_start = date.fromisoformat(day)
    next_day_utc = (
        datetime(day_start.year, day_start.month, day_start.day, tzinfo=IST)
        + timedelta(days=1)
    ).astimezone(timezone.utc).isoformat()
    conn = connect(db_path, read_only=True)
    try:
        init_db(conn)
        attempts = schedule_day(conn, day)
        published = sorted(datasets_published_on(conn, day))
        holds = active_source_access_holds(conn, before_utc=next_day_utc)
        current_holds = active_source_access_holds(conn)
        historical_success = {
            str(row["dataset"])
            for row in conn.execute(
                """SELECT DISTINCT dataset FROM exchange_schedule_runs
                   WHERE scheduled_for<=?
                     AND outcome IN ('published_changed', 'published_unchanged')""",
                (day,),
            )
        }
    finally:
        conn.close()
    latest = {str(row["dataset"]): row for row in attempts
              if row["role"] == "primary" and row["outcome"] != "unrefreshed"}
    states: dict[str, str] = {}
    for dataset in scheduled_datasets():
        if dataset in holds:
            states[dataset] = "ACCESS_BLOCKED"
        elif dataset in published:
            states[dataset] = "REFRESHED"
        elif dataset not in latest:
            states[dataset] = (
                "NOT_CHECKED_TODAY" if dataset in historical_success else "NEVER_REFRESHED"
            )
        elif latest[dataset]["outcome"] == "not_published":
            states[dataset] = "ARTIFACT_NOT_FOUND"
        elif latest[dataset]["outcome"] == "skipped_no_url":
            states[dataset] = "NO_CONFIGURED_SOURCE"
        elif latest[dataset]["outcome"] == "source_access_blocked":
            states[dataset] = "ACCESS_RECHECK_PENDING"
        else:
            states[dataset] = "FETCH_FAILED"
    return {
        "schema_version": SCHEDULER_SCHEMA_VERSION,
        "job": "status",
        "scheduled_for": day,
        "ladder": [rung.as_dict() for rung in LADDER],
        "attempts": attempts,
        "published_datasets": published,
        "dataset_states": states,
        "access_blocked_datasets": sorted(holds),
        "current_access_holds": sorted(current_holds),
        "unpublished_datasets": [
            dataset for dataset, state in states.items()
            if state == "ARTIFACT_NOT_FOUND"
        ],
    }


def resume_source_access(dataset: str, evidence: str, *, db_path: Path=DB_PATH, now_utc: Callable[[], str]=utc_now, DATASETS=DATASETS, active_source_access_holds=active_source_access_holds, connect=connect, init_db=init_db, record_source_access_event=record_source_access_event) -> dict[str, Any]:
    """Operator acknowledgement after approved machine access is established.

    This records evidence and releases only the named dataset. It never fetches
    a source or changes any result checkpoint, snapshot, or company queue.
    """
    if not evidence.strip():
        raise ValueError("--resume-evidence is required")
    if dataset not in DATASETS or DATASETS[dataset].exchange != "BSE":
        raise ValueError("--resume-access requires a registered BSE dataset")
    conn = connect(db_path)
    try:
        init_db(conn)
        if dataset not in active_source_access_holds(conn):
            raise ValueError(f"{dataset} has no active access hold")
        resumed_at = now_utc()
        record_source_access_event(
            conn, dataset=dataset, action="RESUMED",
            event_at_utc=resumed_at, evidence=evidence,
        )
    finally:
        conn.close()
    return {"dataset": dataset, "access_state": "RESUMED",
            "resumed_at_utc": resumed_at, "evidence": evidence.strip()}


def alert(payload: dict[str, Any], *, json=json, sys=sys) -> None:
    """Structured JSON on stderr. Alerting is the existing operational surface.

    A non-zero exit and a machine-readable line are what the rest of this
    pipeline already emits; adding a notification dependency here would be a
    second thing to run and keep credentials for.
    """
    print(json.dumps(payload, sort_keys=True), file=sys.stderr)


