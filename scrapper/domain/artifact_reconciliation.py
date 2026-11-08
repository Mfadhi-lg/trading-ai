"""Shared durable expectation and source-refresh value contracts."""
from __future__ import annotations
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol
from ..documents.availability import ArtifactCheckResult, RetryPolicy

@dataclass(frozen=True)
class ClaimedExpectation:
    expectation_id: int
    company_id: int
    artifact_type: str
    event_id: int
    event_key: str
    event_type: str
    event_date: str | None
    period_end: str | None
    fiscal_year: int | None
    fiscal_quarter: int | None
    expectation_basis: str
    expected_after_utc: str
    availability_status: str
    next_check_at_utc: str
    attempt_count: int
    claim_token: str | None
    schedule: dict[str, Any]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ClaimedExpectation":
        return cls(
            expectation_id=int(row["expectation_id"]),
            company_id=int(row["company_id"]),
            artifact_type=row["artifact_type"],
            event_id=int(row["event_id"]),
            event_key=row["event_key"],
            event_type=row["event_type"],
            event_date=row["event_date"],
            period_end=row["period_end"],
            fiscal_year=row["fiscal_year"],
            fiscal_quarter=row["fiscal_quarter"],
            expectation_basis=row["expectation_basis"],
            expected_after_utc=row["expected_after_utc"],
            availability_status=row["availability_status"],
            next_check_at_utc=row["next_check_at_utc"],
            attempt_count=int(row["attempt_count"]),
            claim_token=row["claim_token"],
            schedule=json.loads(row["schedule_json"]),
        )

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            offsets_hours=tuple(self.schedule["offsets_hours"]),
            unknown_retry_limit=int(self.schedule["unknown_retry_limit"]),
            claim_lease_seconds=int(self.schedule["claim_lease_seconds"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "expectation_id": self.expectation_id,
            "company_id": self.company_id,
            "artifact_type": self.artifact_type,
            "event_id": self.event_id,
            "event_key": self.event_key,
            "event_type": self.event_type,
            "event_date": self.event_date,
            "period_end": self.period_end,
            "fiscal_year": self.fiscal_year,
            "fiscal_quarter": self.fiscal_quarter,
            "expectation_basis": self.expectation_basis,
            "expected_after_utc": self.expected_after_utc,
            "availability_status": self.availability_status,
            "next_check_at_utc": self.next_check_at_utc,
            "attempt_count": self.attempt_count,
            "claim_token": self.claim_token,
            "schedule": self.schedule,
        }


class ArtifactReconciliationAdapter(Protocol):
    """Source adapter boundary.

    Implementations may run the resumable D01-D07 discovery/acquisition pipeline.
    They return ``available`` only after the matching artifact is accepted by that
    pipeline; the scheduler itself never fabricates an accepted artifact.
    """

    def check(
        self,
        conn: sqlite3.Connection,
        expectation: ClaimedExpectation,
        checked_at_utc: str,
    ) -> ArtifactCheckResult: ...


@dataclass(frozen=True)
class ScreenerRefreshTarget:
    """One source fetch shared by all due artifacts for a result event."""

    company_id: int
    event_id: int
    company_path: str
    reporting_scope: str
    result_period: str
    expectation_ids: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "event_id": self.event_id,
            "company_path": self.company_path,
            "reporting_scope": self.reporting_scope,
            "result_period": self.result_period,
            "expectation_ids": list(self.expectation_ids),
        }


class ScreenerSourceRefresher(Protocol):
    def __call__(self, target: ScreenerRefreshTarget) -> Any: ...
