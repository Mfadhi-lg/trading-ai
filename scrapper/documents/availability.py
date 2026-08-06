"""D09 availability states and bounded delayed-publication retry policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


AVAILABILITY_STATUSES = (
    "available",
    "expected_not_yet_published",
    "not_offered",
    "not_applicable",
    "no_rows_reported",
    "source_not_covered",
    "inaccessible",
    "discovery_failed",
    "unknown",
)

TERMINAL_STATUSES = {
    "available",
    "not_offered",
    "not_applicable",
    "no_rows_reported",
    "source_not_covered",
}

# +2h, +6h, +12h, +24h; daily through day 7; weekly through week 6.
DEFAULT_RETRY_OFFSETS_HOURS = (
    2,
    6,
    12,
    24,
    48,
    72,
    96,
    120,
    144,
    168,
    336,
    504,
    672,
    840,
    1008,
)


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp must include a UTC offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class RetryPolicy:
    offsets_hours: tuple[float, ...] = DEFAULT_RETRY_OFFSETS_HOURS
    unknown_retry_limit: int = 3
    claim_lease_seconds: int = 900

    def __post_init__(self) -> None:
        if not self.offsets_hours:
            raise ValueError("Retry schedule cannot be empty")
        if any(value <= 0 for value in self.offsets_hours):
            raise ValueError("Retry offsets must be positive")
        if tuple(sorted(set(self.offsets_hours))) != self.offsets_hours:
            raise ValueError("Retry offsets must be unique and increasing")
        if self.unknown_retry_limit < 1:
            raise ValueError("Unknown retry limit must be positive")
        if self.claim_lease_seconds < 1:
            raise ValueError("Claim lease must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "offsets_hours": list(self.offsets_hours),
            "unknown_retry_limit": self.unknown_retry_limit,
            "claim_lease_seconds": self.claim_lease_seconds,
        }

    def check_at(self, expected_after_utc: str, attempt_index: int) -> str | None:
        if attempt_index < 0:
            raise ValueError("Attempt index cannot be negative")
        if attempt_index >= len(self.offsets_hours):
            return None
        return format_utc(
            parse_utc(expected_after_utc)
            + timedelta(hours=self.offsets_hours[attempt_index])
        )

    def next_after_result(
        self,
        *,
        expected_after_utc: str,
        completed_attempt_count: int,
        status: str,
        retryable: bool,
    ) -> tuple[str | None, str | None]:
        """Return ``(next_check, terminal_reason)`` after a completed check."""

        if status not in AVAILABILITY_STATUSES:
            raise ValueError(f"Unknown availability status: {status!r}")
        if status in TERMINAL_STATUSES:
            return None, status
        if status == "inaccessible" and not retryable:
            return None, "inaccessible_not_actionable"
        if status == "unknown" and completed_attempt_count >= self.unknown_retry_limit:
            return None, "unknown_retry_limit_reached"
        if not retryable:
            return None, f"{status}_not_retryable"
        next_check = self.check_at(expected_after_utc, completed_attempt_count)
        if next_check is None:
            return None, "retry_schedule_exhausted"
        return next_check, None


@dataclass(frozen=True)
class ArtifactCheckResult:
    result_status: str
    source: str
    requested_url: str | None = None
    evidence: dict[str, Any] | None = None
    discovered_artifact_id: int | None = None
    retryable: bool = False
    error: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.result_status not in AVAILABILITY_STATUSES:
            raise ValueError(f"Unknown availability status: {self.result_status!r}")
        if not self.source.strip():
            raise ValueError("Check source is required")
        if self.result_status == "available" and self.discovered_artifact_id is None:
            raise ValueError("Available checks require a discovered artifact")
        if self.result_status != "available" and self.discovered_artifact_id is not None:
            raise ValueError("Only available checks may attach a discovered artifact")

    def evidence_payload(self) -> dict[str, Any]:
        return dict(self.evidence or {})

    def error_payload(self) -> dict[str, Any]:
        return dict(self.error or {})
