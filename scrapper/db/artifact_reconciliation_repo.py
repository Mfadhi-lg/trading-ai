"""Persistence primitives for D09 delayed-artifact reconciliation."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable

from ..config import SCREENER_MIN_MARKET_CAP_CRORE
from ..documents.availability import (
    ArtifactCheckResult,
    RetryPolicy,
    format_utc,
    parse_utc,
)


# These are requirements created by an accepted official result filing.  They
# are deliberately expectations, not guessed URLs: D01 still has to establish
# a primary-source document link before D02 may fetch anything.
RESULT_ARTIFACT_TYPES = (
    "presentation",
    "transcript",
    "recording",
    "credit_rating",
)
DELAYED_SCREENER_RESULT_TYPES = {"presentation", "transcript"}


def screener_market_cap_above_floor(
    conn: sqlite3.Connection, company_id: int
) -> bool:
    from .documents.expectations import screener_market_cap_above_floor as _owner
    return _owner(conn, company_id, SCREENER_MIN_MARKET_CAP_CRORE=SCREENER_MIN_MARKET_CAP_CRORE)


def canonical_json(value: Any) -> str:
    from .documents.expectations import canonical_json as _owner
    return _owner(value, json=json)


from ..domain.artifact_reconciliation import ClaimedExpectation


def register_artifact_event(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    event_key: str,
    event_type: str,
    evidenced_at_utc: str,
    evidence_source: str,
    evidence: dict[str, Any],
    event_date: str | None = None,
    period_end: str | None = None,
    fiscal_year: int | None = None,
    fiscal_quarter: int | None = None,
) -> int:
    from .documents.expectations import register_artifact_event as _owner
    return _owner(conn, company_id=company_id, event_key=event_key, event_type=event_type, evidenced_at_utc=evidenced_at_utc, evidence_source=evidence_source, evidence=evidence, event_date=event_date, period_end=period_end, fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter, canonical_json=canonical_json, parse_utc=parse_utc)


def ensure_artifact_expectation(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    artifact_type: str,
    expectation_basis: str,
    expected_after_utc: str,
    created_at_utc: str,
    policy: RetryPolicy,
) -> int:
    from .documents.expectations import ensure_artifact_expectation as _owner
    return _owner(conn, event_id=event_id, artifact_type=artifact_type, expectation_basis=expectation_basis, expected_after_utc=expected_after_utc, created_at_utc=created_at_utc, policy=policy, canonical_json=canonical_json, parse_utc=parse_utc)


def ensure_result_artifact_expectations(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    filing_id: int,
    exchange: str,
    exchange_key: str,
    result_period: str,
    period_end: str,
    broadcast_at_utc: str,
    created_at_utc: str,
    policy: RetryPolicy | None = None,
) -> tuple[int, ...]:
    from .documents.expectations import ensure_result_artifact_expectations as _owner
    return _owner(conn, company_id=company_id, filing_id=filing_id, exchange=exchange, exchange_key=exchange_key, result_period=result_period, period_end=period_end, broadcast_at_utc=broadcast_at_utc, created_at_utc=created_at_utc, policy=policy, DELAYED_SCREENER_RESULT_TYPES=DELAYED_SCREENER_RESULT_TYPES, RESULT_ARTIFACT_TYPES=RESULT_ARTIFACT_TYPES, RetryPolicy=RetryPolicy, ensure_artifact_expectation=ensure_artifact_expectation, register_artifact_event=register_artifact_event, screener_market_cap_above_floor=screener_market_cap_above_floor)


def backfill_result_artifact_expectations(
    conn: sqlite3.Connection,
    *,
    created_at_utc: str,
    limit: int | None = None,
) -> dict[str, int]:
    from .documents.expectations import backfill_result_artifact_expectations as _owner
    return _owner(conn, created_at_utc=created_at_utc, limit=limit, ensure_result_artifact_expectations=ensure_result_artifact_expectations)


def ensure_discovered_artifact_expectations(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    artifact_ids: Iterable[int],
    observed_at_utc: str,
    policy: RetryPolicy | None = None,
) -> tuple[int, ...]:
    from .documents.expectations import ensure_discovered_artifact_expectations as _owner
    return _owner(conn, company_id=company_id, artifact_ids=artifact_ids, observed_at_utc=observed_at_utc, policy=policy, RetryPolicy=RetryPolicy, ensure_artifact_expectation=ensure_artifact_expectation, register_artifact_event=register_artifact_event)


from .documents.due import _DUE_SELECT


def preview_due_expectations(
    conn: sqlite3.Connection, *, now_utc: str, limit: int
) -> tuple[ClaimedExpectation, ...]:
    from .documents.due import preview_due_expectations as _owner
    return _owner(conn, now_utc=now_utc, limit=limit, ClaimedExpectation=ClaimedExpectation, _DUE_SELECT=_DUE_SELECT, parse_utc=parse_utc)


def claim_due_expectations(
    conn: sqlite3.Connection,
    *,
    now_utc: str,
    limit: int,
    worker_id: str,
) -> tuple[ClaimedExpectation, ...]:
    from .documents.due import claim_due_expectations as _owner
    return _owner(conn, now_utc=now_utc, limit=limit, worker_id=worker_id, ClaimedExpectation=ClaimedExpectation, _DUE_SELECT=_DUE_SELECT, format_utc=format_utc, parse_utc=parse_utc, timedelta=timedelta, uuid=uuid)


def complete_artifact_check(
    conn: sqlite3.Connection,
    *,
    expectation: ClaimedExpectation,
    result: ArtifactCheckResult,
    checked_at_utc: str,
) -> int:
    from .documents.due import complete_artifact_check as _owner
    return _owner(conn, expectation=expectation, result=result, checked_at_utc=checked_at_utc, canonical_json=canonical_json, parse_utc=parse_utc)


def release_claims(
    conn: sqlite3.Connection,
    expectations: Iterable[ClaimedExpectation],
    *,
    released_at_utc: str,
) -> None:
    from .documents.due import release_claims as _owner
    return _owner(conn, expectations, released_at_utc=released_at_utc)
