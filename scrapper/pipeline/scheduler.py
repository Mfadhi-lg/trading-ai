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
import time
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from ..config import (
    DB_PATH,
    EXCHANGE_REPORT_DAY,
    EXCHANGE_REPORT_TIME,
    EXCHANGE_RESULT_REVISION_ATTEMPT,
    EXCHANGE_SCHEDULE_ATTEMPTS,
    EXCHANGE_SCHEDULE_EXPORTS,
    SNAPSHOTS,
)
from ..core.http import FetchResponse, fetch_exchange_dataset
from ..db.database import connect, init_db
from ..db.exchange.schedule import (
    SATISFYING_OUTCOMES,
    record_schedule_run,
    datasets_published_on,
    schedule_day,
)
from ..db.exchange_repo import (
    active_source_access_holds,
    record_source_access_event,
    snapshot_content_is_new,
)
from ..db.repo import utc_now
from ..exports.exchange_reconciliation_report import (
    write_exchange_reconciliation_report,
)
from ..exports.exchange_universe_coverage import write_exchange_universe_coverage
from ..sources.exchange.snapshot_guard import GuardThresholds
from ..sources.exchange.datasets import (
    CORPORATE_CHANGE,
    ON_DEMAND,
    RESULT_FILING,
    SECURITY_MASTER,
    DATASETS,
    dataset_config,
    default_url_for,
)
from ..sources.exchange.tabular import IST
from ..workflows.exchange.universe import (
    DatasetNotPublished,
    ExchangeSourceAccessBlocked,
    discover_exchange_results,
    refresh_exchange_universe,
)


from ..workflows.exchange import schedule as _owner
ScheduleAttempt = _owner.ScheduleAttempt
DEFAULT_CATCHUP_DAYS = _owner.DEFAULT_CATCHUP_DAYS
FINAL_ATTEMPT = _owner.FINAL_ATTEMPT
KIND_ORDER = _owner.KIND_ORDER
LADDER = _owner.LADDER
MISFIRE_GRACE_SECONDS = _owner.MISFIRE_GRACE_SECONDS
SCHEDULER_SCHEMA_VERSION = _owner.SCHEDULER_SCHEMA_VERSION


def _ladder() -> tuple[ScheduleAttempt, ...]:
    return _owner._ladder(EXCHANGE_SCHEDULE_ATTEMPTS=EXCHANGE_SCHEDULE_ATTEMPTS)


def scheduled_datasets() -> tuple[str, ...]:
    return _owner.scheduled_datasets(DATASETS=DATASETS, KIND_ORDER=KIND_ORDER, ON_DEMAND=ON_DEMAND)


def ist_day(now_utc_value: str) -> str:
    return _owner.ist_day(now_utc_value, IST=IST, datetime=datetime)


def current_result_period(scheduled_for: str) -> str:
    return _owner.current_result_period(scheduled_for, date=date, timedelta=timedelta)


def previous_result_period(result_period: str) -> str:
    return _owner.previous_result_period(result_period)


def _run_one(dataset: str, *, scheduled_for: str, result_period: str, db_path: Path, snapshot_root: Path, now_utc: Callable[[], str], fetcher: Callable[[str, str], FetchResponse], thresholds: Callable[[str], GuardThresholds] | None) -> dict[str, Any]:
    return _owner._run_one(dataset, scheduled_for=scheduled_for, result_period=result_period, db_path=db_path, snapshot_root=snapshot_root, now_utc=now_utc, fetcher=fetcher, thresholds=thresholds, Any=Any, DatasetNotPublished=DatasetNotPublished, ExchangeSourceAccessBlocked=ExchangeSourceAccessBlocked, RESULT_FILING=RESULT_FILING, connect=connect, dataset_config=dataset_config, default_url_for=default_url_for, discover_exchange_results=discover_exchange_results, init_db=init_db, refresh_exchange_universe=refresh_exchange_universe, snapshot_content_is_new=snapshot_content_is_new)


def _attempt_dataset(dataset: str, *, scheduled_for: str, attempt: int, result_period: str, db_path: Path, snapshot_root: Path, now_utc: Callable[[], str], fetcher: Callable[[str, str], FetchResponse], thresholds: Callable[[str], GuardThresholds] | None, role: str='primary') -> dict[str, Any]:
    return _owner._attempt_dataset(dataset, scheduled_for=scheduled_for, attempt=attempt, result_period=result_period, db_path=db_path, snapshot_root=snapshot_root, now_utc=now_utc, fetcher=fetcher, thresholds=thresholds, role=role, RESULT_FILING=RESULT_FILING, _run_one=_run_one, connect=connect, dataset_config=dataset_config, init_db=init_db, record_schedule_run=record_schedule_run, record_source_access_event=record_source_access_event)


def revision_datasets(datasets: Sequence[str]) -> tuple[str, ...]:
    return _owner.revision_datasets(datasets, RESULT_FILING=RESULT_FILING, dataset_config=dataset_config)


def _sweep_revisions(datasets: Sequence[str], *, scheduled_for: str, attempt: int, result_period: str, db_path: Path, snapshot_root: Path, now_utc: Callable[[], str], fetcher: Callable[[str, str], FetchResponse], thresholds: Callable[[str], GuardThresholds] | None) -> list[dict[str, Any]]:
    return _owner._sweep_revisions(datasets, scheduled_for=scheduled_for, attempt=attempt, result_period=result_period, db_path=db_path, snapshot_root=snapshot_root, now_utc=now_utc, fetcher=fetcher, thresholds=thresholds, EXCHANGE_RESULT_REVISION_ATTEMPT=EXCHANGE_RESULT_REVISION_ATTEMPT, _attempt_dataset=_attempt_dataset, revision_datasets=revision_datasets)


def run_attempt(*, attempt: int, scheduled_for: str | None=None, datasets: Sequence[str] | None=None, result_period: str | None=None, db_path: Path=DB_PATH, snapshot_root: Path=SNAPSHOTS, now_utc: Callable[[], str]=utc_now, fetcher: Callable[[str, str], FetchResponse]=fetch_exchange_dataset, thresholds: Callable[[str], GuardThresholds] | None=None) -> dict[str, Any]:
    return _owner.run_attempt(attempt=attempt, scheduled_for=scheduled_for, datasets=datasets, result_period=result_period, db_path=db_path, snapshot_root=snapshot_root, now_utc=now_utc, fetcher=fetcher, thresholds=thresholds, Any=Any, FINAL_ATTEMPT=FINAL_ATTEMPT, SATISFYING_OUTCOMES=SATISFYING_OUTCOMES, SCHEDULER_SCHEMA_VERSION=SCHEDULER_SCHEMA_VERSION, _attempt_dataset=_attempt_dataset, _sweep_revisions=_sweep_revisions, active_source_access_holds=active_source_access_holds, connect=connect, current_result_period=current_result_period, datasets_published_on=datasets_published_on, init_db=init_db, ist_day=ist_day, previous_result_period=previous_result_period, record_schedule_run=record_schedule_run, scheduled_datasets=scheduled_datasets)


def catch_up_missed_days(*, db_path: Path=DB_PATH, snapshot_root: Path=SNAPSHOTS, max_days: int=DEFAULT_CATCHUP_DAYS, now_utc: Callable[[], str]=utc_now, fetcher: Callable[[str, str], FetchResponse]=fetch_exchange_dataset, thresholds: Callable[[str], GuardThresholds] | None=None) -> dict[str, Any]:
    return _owner.catch_up_missed_days(db_path=db_path, snapshot_root=snapshot_root, max_days=max_days, now_utc=now_utc, fetcher=fetcher, thresholds=thresholds, Any=Any, FINAL_ATTEMPT=FINAL_ATTEMPT, SCHEDULER_SCHEMA_VERSION=SCHEDULER_SCHEMA_VERSION, connect=connect, date=date, init_db=init_db, ist_day=ist_day, run_attempt=run_attempt, time=time, timedelta=timedelta)


def run_weekly_report(*, db_path: Path=DB_PATH, output_root: Path=EXCHANGE_SCHEDULE_EXPORTS, now_utc: Callable[[], str]=utc_now) -> dict[str, Any]:
    return _owner.run_weekly_report(db_path=db_path, output_root=output_root, now_utc=now_utc, SCHEDULER_SCHEMA_VERSION=SCHEDULER_SCHEMA_VERSION, connect=connect, init_db=init_db, ist_day=ist_day, json=json, write_exchange_reconciliation_report=write_exchange_reconciliation_report, write_exchange_universe_coverage=write_exchange_universe_coverage)


def day_status(scheduled_for: str | None=None, *, db_path: Path=DB_PATH, now_utc: Callable[[], str]=utc_now) -> dict[str, Any]:
    return _owner.day_status(scheduled_for, db_path=db_path, now_utc=now_utc, IST=IST, LADDER=LADDER, SCHEDULER_SCHEMA_VERSION=SCHEDULER_SCHEMA_VERSION, active_source_access_holds=active_source_access_holds, connect=connect, datasets_published_on=datasets_published_on, date=date, datetime=datetime, init_db=init_db, ist_day=ist_day, schedule_day=schedule_day, scheduled_datasets=scheduled_datasets, timedelta=timedelta, timezone=timezone)


def resume_source_access(dataset: str, evidence: str, *, db_path: Path=DB_PATH, now_utc: Callable[[], str]=utc_now) -> dict[str, Any]:
    return _owner.resume_source_access(dataset, evidence, db_path=db_path, now_utc=now_utc, DATASETS=DATASETS, active_source_access_holds=active_source_access_holds, connect=connect, init_db=init_db, record_source_access_event=record_source_access_event)


def alert(payload: dict[str, Any]) -> None:
    return _owner.alert(payload, json=json, sys=sys)


def run_scheduler(
    *,
    once: bool = False,
    attempt: int | None = None,
    db_path: Path = DB_PATH,
    snapshot_root: Path = SNAPSHOTS,
    now_utc: Callable[[], str] = utc_now,
    fetcher: Callable[[str, str], FetchResponse] = fetch_exchange_dataset,
) -> dict[str, Any]:
    """Run one attempt now, or block on the schedule until interrupted."""
    if once:
        payload = run_attempt(
            attempt=attempt or 1, db_path=db_path, snapshot_root=snapshot_root,
            now_utc=now_utc, fetcher=fetcher,
        )
        if payload["status"] != "ok":
            alert(payload)
        return payload
    return _serve(
        db_path=db_path, snapshot_root=snapshot_root, now_utc=now_utc, fetcher=fetcher
    )


def _serve(*, db_path, snapshot_root, now_utc, fetcher):
    from types import SimpleNamespace
    from ..operations.coordinator.exchange import serve
    workflow = SimpleNamespace(**{name: globals()[name] for name in (
        'LADDER', 'MISFIRE_GRACE_SECONDS', 'DEFAULT_CATCHUP_DAYS',
        'SCHEDULER_SCHEMA_VERSION', 'EXCHANGE_REPORT_DAY', 'EXCHANGE_REPORT_TIME',
        'scheduled_datasets', 'ist_day', 'run_attempt', 'run_weekly_report', 'alert')})
    return serve(db_path=db_path, snapshot_root=snapshot_root,
                 now_utc=now_utc, fetcher=fetcher, workflow=workflow)
