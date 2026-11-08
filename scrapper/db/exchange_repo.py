"""Durable exchange universe registry, filing discovery and queue bridge.

Every dated snapshot is preserved. Securities are never deleted: they are
effective-dated, marked absent, and kept queryable. Companies are unioned by
ISIN only; NSE symbols and BSE scrip codes stay in separate columns, and no code
path merges two securities from name text.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..config import EXCHANGE_ABSENCE_CONFIRMATIONS, SNAPSHOTS
from ..core.http import FetchResponse
from ..sources.exchange.corporate_changes import ParsedCorporateChanges
from ..sources.exchange.result_filings import ParsedResultFilings, ResultFiling
from ..sources.exchange.security_master import ParsedSecurityMaster, SecurityRecord
from ..sources.exchange.snapshot_guard import (
    GuardVerdict,
    ScopeMetrics,
    SnapshotBaseline,
)
from ..sources.exchange.tabular import ExcludedRow, RejectedRow
from ..sources.screener.result_screen import canonical_company_path
from .repo import utc_now
from .exchange import snapshots as _snapshots
from .exchange import securities as _securities
from .exchange import filings as _filings
from .exchange import schedule as _schedule


# The suffix describes the bytes as served, because the stored blob is exactly
# what the exchange published; a gzipped report is kept gzipped.
DATASET_SUFFIX = _snapshots.DATASET_SUFFIX
COMPRESSION_SUFFIX = _snapshots.COMPRESSION_SUFFIX


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


# --------------------------------------------------------------------------
# Immutable evidence
# --------------------------------------------------------------------------


def store_exchange_blob(
    conn: sqlite3.Connection,
    response: FetchResponse,
    *,
    exchange: str,
    dataset: str,
    effective_date: str,
    observed_at_utc: str,
    snapshot_root: Path = SNAPSHOTS,
    compression: str = "none",
) -> int:
    return _snapshots.store_exchange_blob(conn, response, exchange=exchange, dataset=dataset, effective_date=effective_date, observed_at_utc=observed_at_utc, snapshot_root=snapshot_root, compression=compression, utc_now=utc_now, DATASET_SUFFIX=DATASET_SUFFIX, COMPRESSION_SUFFIX=COMPRESSION_SUFFIX)


def record_exchange_fetch(
    conn: sqlite3.Connection,
    run_id: int,
    response: FetchResponse,
    *,
    exchange: str,
    blob_id: int,
    fetched_at_utc: str,
) -> int:
    return _snapshots.record_exchange_fetch(conn, run_id, response, exchange=exchange, blob_id=blob_id, fetched_at_utc=fetched_at_utc)


def persist_snapshot(
    conn: sqlite3.Connection,
    *,
    exchange: str,
    dataset: str,
    effective_date: str,
    source_label: str,
    requested_url: str | None,
    acquisition_mode: str,
    fetch_id: int | None,
    blob_id: int,
    parser_name: str,
    parser_version: str,
    source_encoding: str,
    observed_at_utc: str,
    headers: Iterable[str],
    accepted_row_count: int,
    rejected: Iterable[RejectedRow],
    validation_status: str = "passed",
    compression: str = "none",
    excluded: Iterable[ExcludedRow] = (),
) -> tuple[int, bool]:
    return _snapshots.persist_snapshot(conn, exchange=exchange, dataset=dataset, effective_date=effective_date, source_label=source_label, requested_url=requested_url, acquisition_mode=acquisition_mode, fetch_id=fetch_id, blob_id=blob_id, parser_name=parser_name, parser_version=parser_version, source_encoding=source_encoding, observed_at_utc=observed_at_utc, headers=headers, accepted_row_count=accepted_row_count, rejected=rejected, validation_status=validation_status, compression=compression, excluded=excluded, _json=_json)


# --------------------------------------------------------------------------
# Snapshot-level validation
# --------------------------------------------------------------------------


def latest_passed_snapshot(
    conn: sqlite3.Connection, dataset: str, *, before_snapshot_id: int | None = None
) -> SnapshotBaseline | None:
    return _snapshots.latest_passed_snapshot(conn, dataset, before_snapshot_id=before_snapshot_id)


def record_snapshot_validation(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    verdict: GuardVerdict,
    evaluated_at_utc: str,
) -> None:
    return _snapshots.record_snapshot_validation(conn, snapshot_id=snapshot_id, verdict=verdict, evaluated_at_utc=evaluated_at_utc, _json=_json)


def sweep_scope(
    parsed: ParsedSecurityMaster,
    authoritative_segments: Sequence[tuple[str, str]] | None = None,
) -> tuple[tuple[str, str], ...]:
    return _securities.sweep_scope(parsed, authoritative_segments)


def snapshot_content_is_new(
    conn: sqlite3.Connection, *, dataset: str, snapshot_id: int
) -> bool:
    return _snapshots.snapshot_content_is_new(conn, dataset=dataset, snapshot_id=snapshot_id)


# What one attempt found. The two `published_` outcomes are both successes: an
# exchange that publishes an unchanged file has published, and a day on which no
# security changed is an ordinary day, not a failure to look at again.
SCHEDULE_OUTCOMES = _schedule.SCHEDULE_OUTCOMES

# The outcomes that satisfy a scheduled day and stop the ladder for a dataset.
SATISFYING_OUTCOMES = _schedule.SATISFYING_OUTCOMES

SCHEDULE_ROLES = _schedule.SCHEDULE_ROLES


def active_source_access_holds(
    conn: sqlite3.Connection, *, before_utc: str | None = None,
) -> dict[str, dict[str, str]]:
    """Return the latest access event per dataset when it is a BLOCKED event."""
    return {
        str(row["dataset"]): dict(row)
        for row in conn.execute(
            """SELECT event.dataset, event.action, event.event_at_utc, event.evidence
               FROM exchange_source_access_events AS event
               WHERE event.event_id = (
                   SELECT MAX(later.event_id) FROM exchange_source_access_events AS later
                   WHERE later.dataset=event.dataset
                     AND (? IS NULL OR later.event_at_utc < ?)
               ) AND event.action='BLOCKED'""",
            (before_utc, before_utc),
        )
    }


def record_source_access_event(
    conn: sqlite3.Connection, *, dataset: str, action: str,
    event_at_utc: str, evidence: str,
) -> None:
    if not dataset.startswith("bse_") or dataset not in DATASET_SUFFIX:
        raise ValueError("Only a registered BSE dataset may have an access hold")
    if action not in {"BLOCKED", "RESUMED"} or not evidence.strip():
        raise ValueError("Access event needs a valid action and evidence")
    with conn:
        conn.execute(
            """INSERT INTO exchange_source_access_events
               (dataset, action, event_at_utc, evidence) VALUES (?, ?, ?, ?)""",
            (dataset, action, event_at_utc, evidence.strip()),
        )


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
) -> None:
    return _schedule.record_schedule_run(conn, dataset=dataset, scheduled_for=scheduled_for, attempt=attempt, outcome=outcome, snapshot_id=snapshot_id, error=error, started_at_utc=started_at_utc, finished_at_utc=finished_at_utc, result_period=result_period, role=role, SCHEDULE_OUTCOMES=SCHEDULE_OUTCOMES, SCHEDULE_ROLES=SCHEDULE_ROLES)


def datasets_published_on(conn: sqlite3.Connection, scheduled_for: str) -> set[str]:
    return _schedule.datasets_published_on(conn, scheduled_for, SATISFYING_OUTCOMES=SATISFYING_OUTCOMES)


def schedule_day(conn: sqlite3.Connection, scheduled_for: str) -> list[dict[str, Any]]:
    return _schedule.schedule_day(conn, scheduled_for)


def absence_preview(
    conn: sqlite3.Connection,
    parsed: ParsedSecurityMaster,
    *,
    effective_date: str,
    authoritative_segments: Sequence[tuple[str, str]] | None = None,
) -> tuple[ScopeMetrics, ...]:
    return _securities.absence_preview(conn, parsed, effective_date=effective_date, authoritative_segments=authoritative_segments)


# --------------------------------------------------------------------------
# Security registry
# --------------------------------------------------------------------------


def _record_event(
    conn: sqlite3.Connection,
    *,
    exchange_security_id: int,
    snapshot_id: int | None,
    event_date: str,
    event_type: str,
    from_value: str | None,
    to_value: str | None,
    evidence: Mapping[str, Any] | None,
    now_utc: str,
) -> None:
    return _securities._record_event(conn, exchange_security_id=exchange_security_id, snapshot_id=snapshot_id, event_date=event_date, event_type=event_type, from_value=from_value, to_value=to_value, evidence=evidence, now_utc=now_utc, _json=_json)


def _insert_version(
    conn: sqlite3.Connection,
    *,
    exchange_security_id: int,
    snapshot_id: int,
    record: SecurityRecord,
    effective_date: str,
    now_utc: str,
) -> int:
    return _securities._insert_version(conn, exchange_security_id=exchange_security_id, snapshot_id=snapshot_id, record=record, effective_date=effective_date, now_utc=now_utc, _json=_json)


def _open_conflict(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    exchange_security_id: int | None,
    conflict_type: str,
    exchange: str,
    exchange_key: str,
    existing_value: str | None,
    incoming_value: str | None,
    existing_source_type: str | None,
    now_utc: str,
) -> bool:
    return _securities._open_conflict(conn, snapshot_id=snapshot_id, exchange_security_id=exchange_security_id, conflict_type=conflict_type, exchange=exchange, exchange_key=exchange_key, existing_value=existing_value, incoming_value=incoming_value, existing_source_type=existing_source_type, now_utc=now_utc)


def _link_isin(
    conn: sqlite3.Connection,
    *,
    exchange_security_id: int,
    record: SecurityRecord,
    effective_date: str,
    snapshot_id: int,
    now_utc: str,
) -> int | None:
    return _securities._link_isin(conn, exchange_security_id=exchange_security_id, record=record, effective_date=effective_date, snapshot_id=snapshot_id, now_utc=now_utc)


def _link_isin_value(
    conn: sqlite3.Connection,
    *,
    exchange_security_id: int,
    exchange: str,
    isin: str,
    name_raw: str,
    effective_date: str,
    now_utc: str,
) -> int:
    return _securities._link_isin_value(conn, exchange_security_id=exchange_security_id, exchange=exchange, isin=isin, name_raw=name_raw, effective_date=effective_date, now_utc=now_utc)


def _already_absent_on(
    conn: sqlite3.Connection, last_absent_snapshot_id: Any, *, effective_date: str
) -> bool:
    return _securities._already_absent_on(conn, last_absent_snapshot_id, effective_date=effective_date)


def _link_or_conflict(
    conn: sqlite3.Connection,
    *,
    exchange_security_id: int,
    record: SecurityRecord,
    effective_date: str,
    snapshot_id: int,
    now_utc: str,
    keys_in_file: set[tuple[str, str]],
) -> tuple[int | None, bool]:
    return _securities._link_or_conflict(conn, exchange_security_id=exchange_security_id, record=record, effective_date=effective_date, snapshot_id=snapshot_id, now_utc=now_utc, keys_in_file=keys_in_file)


def apply_security_master(
    conn: sqlite3.Connection,
    parsed: ParsedSecurityMaster,
    *,
    snapshot_id: int,
    effective_date: str,
    now_utc: str,
    acquisition_mode: str = "fetched",
    authoritative_segments: Sequence[tuple[str, str]] | None = None,
) -> dict[str, int]:
    return _securities.apply_security_master(conn, parsed, snapshot_id=snapshot_id, effective_date=effective_date, now_utc=now_utc, acquisition_mode=acquisition_mode, authoritative_segments=authoritative_segments, EXCHANGE_ABSENCE_CONFIRMATIONS=EXCHANGE_ABSENCE_CONFIRMATIONS)


CONFLICT_RESOLUTIONS = _securities.CONFLICT_RESOLUTIONS


def open_identity_conflicts(
    conn: sqlite3.Connection, *, exchange_security_id: int | None = None
) -> list[dict[str, Any]]:
    return _securities.open_identity_conflicts(conn, exchange_security_id=exchange_security_id)


def resolve_identity_conflict(
    conn: sqlite3.Connection,
    *,
    conflict_id: int,
    resolution: str,
    now_utc: str,
) -> dict[str, Any]:
    return _securities.resolve_identity_conflict(conn, conflict_id=conflict_id, resolution=resolution, now_utc=now_utc, CONFLICT_RESOLUTIONS=CONFLICT_RESOLUTIONS)


def apply_corporate_changes(
    conn: sqlite3.Connection,
    parsed: ParsedCorporateChanges,
    *,
    snapshot_id: int,
    now_utc: str,
) -> dict[str, int]:
    return _securities.apply_corporate_changes(conn, parsed, snapshot_id=snapshot_id, now_utc=now_utc)


# --------------------------------------------------------------------------
# Result filings
# --------------------------------------------------------------------------


def ingest_result_filings(
    conn: sqlite3.Connection,
    parsed: ParsedResultFilings,
    *,
    snapshot_id: int,
    now_utc: str,
) -> dict[str, Any]:
    return _filings.ingest_result_filings(conn, parsed, snapshot_id=snapshot_id, now_utc=now_utc)


def resolve_screener_path(
    conn: sqlite3.Connection, *, exchange: str, exchange_key: str, reporting_scope: str
) -> dict[str, Any]:
    return _filings.resolve_screener_path(conn, exchange=exchange, exchange_key=exchange_key, reporting_scope=reporting_scope, canonical_company_path=canonical_company_path)


def _decide(
    conn: sqlite3.Connection, filing: sqlite3.Row, *, now_utc: str
) -> dict[str, Any]:
    return _filings._decide(conn, filing, now_utc=now_utc, _json=_json)


def preview_filing_decisions(
    conn: sqlite3.Connection, parsed: ParsedResultFilings
) -> dict[str, int]:
    return _filings.preview_filing_decisions(conn, parsed)


def enqueue_result_filings(
    conn: sqlite3.Connection, *, snapshot_id: int | None = None, now_utc: str
) -> dict[str, int]:
    return _filings.enqueue_result_filings(conn, snapshot_id=snapshot_id, now_utc=now_utc)
