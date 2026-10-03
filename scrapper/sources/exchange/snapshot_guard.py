"""Snapshot-level acceptance rules for one dated exchange file.

Per-row rejection already keeps a malformed row out of the registry. It cannot
see a file that parsed perfectly but is the wrong file: a truncated publication,
a half-written export, a reshaped report. Those pass row by row and then rewrite
current state, so a whole snapshot is judged before anything is applied.

Pure functions only: no database, no network, no clock of its own. The caller
supplies the measured facts, the prior accepted baseline and the thresholds, and
gets back a verdict it can store verbatim. Every failing rule is reported, not
just the first, so one run tells an operator everything that is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .tabular import normalize_header


PASSED = "passed"
FAILED = "failed"
OVERRIDDEN = "overridden"


@dataclass(frozen=True)
class GuardThresholds:
    """Per-dataset limits. Every value is recorded with the verdict it produced.

    `None` disables a rule for datasets it cannot mean anything for. A daily
    result feed carries however many companies reported that day, so comparing
    its size to yesterday's is noise, and one company filing both standalone and
    consolidated repeats its ISIN by design. Those rules exist for the full
    security masters, where a shrunken file is evidence of a broken publication.
    """

    row_count_drop_ratio: float | None = 0.10
    row_floor: int = 50
    rejected_row_rate: float = 0.05
    duplicate_isin_rate: float | None = 0.01
    absence_volume_floor: int = 100
    absence_volume_ratio: float = 0.05

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_count_drop_ratio": self.row_count_drop_ratio,
            "row_floor": self.row_floor,
            "rejected_row_rate": self.rejected_row_rate,
            "duplicate_isin_rate": self.duplicate_isin_rate,
            "absence_volume_floor": self.absence_volume_floor,
            "absence_volume_ratio": self.absence_volume_ratio,
        }


@dataclass(frozen=True)
class ScopeMetrics:
    """One (exchange, segment) the candidate file is authoritative for.

    A file that declares itself a complete statement of two scopes gets swept
    over both, so both have to be judged. Totals hide the case this exists for:
    a combined master keeping its NSE rows while losing a third of its
    BSE-exclusive ones moves the total by a few percent and every whole-file
    rule passes, while the sweep marks the missing third absent.
    """

    exchange: str
    segment: str
    accepted_row_count: int
    would_absent_count: int = 0
    present_count: int = 0

    @property
    def scope(self) -> tuple[str, str]:
        return (self.exchange, self.segment)

    def as_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "segment": self.segment,
            "accepted_row_count": self.accepted_row_count,
            "would_absent_count": self.would_absent_count,
            "present_count": self.present_count,
        }


@dataclass(frozen=True)
class SnapshotBaseline:
    """The last snapshot of this dataset that was accepted."""

    snapshot_id: int
    accepted_row_count: int
    headers: tuple[str, ...]
    distinct_isin_count: int
    effective_date: str
    # Rows the baseline carried per authoritative scope. Empty for a baseline
    # taken before scopes were measured, which disables the per-scope size rule
    # for exactly one snapshot rather than failing it.
    scope_row_counts: tuple[tuple[str, str, int], ...] = ()

    @property
    def rows_by_scope(self) -> dict[tuple[str, str], int]:
        return {(exchange, segment): count
                for exchange, segment, count in self.scope_row_counts}

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "accepted_row_count": self.accepted_row_count,
            "headers": list(self.headers),
            "distinct_isin_count": self.distinct_isin_count,
            "effective_date": self.effective_date,
            "scope_row_counts": [list(item) for item in self.scope_row_counts],
        }


@dataclass(frozen=True)
class SnapshotMetrics:
    """The same facts, measured on the candidate snapshot."""

    accepted_row_count: int
    rejected_row_count: int
    headers: tuple[str, ...]
    distinct_isin_count: int
    duplicate_isin_row_count: int
    non_null_isin_count: int
    effective_date: str
    # Securities this file would mark absent, and how many are currently present
    # in the exchange and segments the file is authoritative for. Both are zero
    # for datasets that never sweep for absence.
    would_absent_count: int = 0
    present_count: int = 0
    # The same measurement broken out per authoritative scope. Empty for a
    # dataset that declares none, where the totals are the only statement
    # available.
    scope_metrics: tuple[ScopeMetrics, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted_row_count": self.accepted_row_count,
            "rejected_row_count": self.rejected_row_count,
            "headers": list(self.headers),
            "distinct_isin_count": self.distinct_isin_count,
            "duplicate_isin_row_count": self.duplicate_isin_row_count,
            "non_null_isin_count": self.non_null_isin_count,
            "effective_date": self.effective_date,
            "would_absent_count": self.would_absent_count,
            "present_count": self.present_count,
            "scope_metrics": [item.as_dict() for item in self.scope_metrics],
        }


@dataclass(frozen=True)
class GuardFinding:
    code: str
    severity: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


@dataclass(frozen=True)
class GuardVerdict:
    status: str
    findings: tuple[GuardFinding, ...] = ()
    metrics: SnapshotMetrics | None = None
    baseline: SnapshotBaseline | None = None
    thresholds: GuardThresholds = field(default_factory=GuardThresholds)

    @property
    def accepted(self) -> bool:
        return self.status in (PASSED, OVERRIDDEN)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "findings": [item.as_dict() for item in self.findings],
            "metrics": self.metrics.as_dict() if self.metrics else None,
            "baseline": self.baseline.as_dict() if self.baseline else None,
            "thresholds": self.thresholds.as_dict(),
        }


def isin_metrics(isins: Sequence[str | None]) -> tuple[int, int, int]:
    """Return (distinct, duplicate-row, non-null) counts for one file's ISINs."""
    seen: dict[str, int] = {}
    for isin in isins:
        if not isin:
            continue
        seen[isin] = seen.get(isin, 0) + 1
    non_null = sum(seen.values())
    duplicates = sum(count - 1 for count in seen.values() if count > 1)
    return len(seen), duplicates, non_null


def _absence_is_excessive(
    would_absent: int, present: int, thresholds: GuardThresholds
) -> bool:
    return (
        would_absent > thresholds.absence_volume_floor
        and present > 0
        and would_absent / present > thresholds.absence_volume_ratio
    )


def evaluate_snapshot(
    metrics: SnapshotMetrics,
    baseline: SnapshotBaseline | None,
    thresholds: GuardThresholds | None = None,
    *,
    today: str,
) -> GuardVerdict:
    """Judge one candidate snapshot against the last accepted one.

    Without a baseline only the checks that need no history apply — the floor,
    the rejected-row rate, the duplicate-ISIN rate and the date sanity checks —
    so bootstrapping a new dataset stays possible without an override.
    """
    thresholds = thresholds or GuardThresholds()
    findings: list[GuardFinding] = []

    if metrics.accepted_row_count < thresholds.row_floor:
        findings.append(
            GuardFinding(
                "row_count_floor",
                "error",
                f"accepted rows {metrics.accepted_row_count} below floor "
                f"{thresholds.row_floor}",
            )
        )

    considered = metrics.accepted_row_count + metrics.rejected_row_count
    if considered:
        rate = metrics.rejected_row_count / considered
        if rate > thresholds.rejected_row_rate:
            findings.append(
                GuardFinding(
                    "rejected_row_rate",
                    "error",
                    f"{metrics.rejected_row_count}/{considered} rows rejected "
                    f"({rate:.4f} > {thresholds.rejected_row_rate})",
                )
            )

    if thresholds.duplicate_isin_rate is not None and metrics.non_null_isin_count:
        duplicate_rate = metrics.duplicate_isin_row_count / metrics.non_null_isin_count
        if duplicate_rate > thresholds.duplicate_isin_rate:
            findings.append(
                GuardFinding(
                    "duplicate_isin_rate",
                    "error",
                    f"{metrics.duplicate_isin_row_count} repeated ISIN rows of "
                    f"{metrics.non_null_isin_count} ({duplicate_rate:.4f} > "
                    f"{thresholds.duplicate_isin_rate})",
                )
            )

    if metrics.effective_date > today:
        findings.append(
            GuardFinding(
                "future_effective_date",
                "error",
                f"effective date {metrics.effective_date} is after today {today}",
            )
        )

    # A file that would mark a large slice of the universe absent is the exact
    # shape of a truncated publication. Both bounds must be crossed: a big
    # absolute number alone is normal for a large exchange, and a big proportion
    # alone is normal for a small segment.
    if _absence_is_excessive(
        metrics.would_absent_count, metrics.present_count, thresholds
    ):
        share = metrics.would_absent_count / metrics.present_count
        findings.append(
            GuardFinding(
                "absence_volume",
                "error",
                f"{metrics.would_absent_count} of {metrics.present_count} present "
                f"securities would be absent ({share:.4f} > "
                f"{thresholds.absence_volume_ratio} and > "
                f"{thresholds.absence_volume_floor})",
            )
        )

    # The same rule again per scope. The totals can absorb one scope collapsing
    # into the others' normality, and it is that scope the sweep is about to run
    # over, so each is held to the bound on its own.
    for scope in metrics.scope_metrics:
        if _absence_is_excessive(
            scope.would_absent_count, scope.present_count, thresholds
        ):
            share = scope.would_absent_count / scope.present_count
            findings.append(
                GuardFinding(
                    "scope_absence_volume",
                    "error",
                    f"{scope.exchange}/{scope.segment}: {scope.would_absent_count} of "
                    f"{scope.present_count} present securities would be absent "
                    f"({share:.4f} > {thresholds.absence_volume_ratio} and > "
                    f"{thresholds.absence_volume_floor})",
                )
            )

    if baseline is not None:
        if thresholds.row_count_drop_ratio is not None and baseline.accepted_row_count:
            drop = (
                baseline.accepted_row_count - metrics.accepted_row_count
            ) / baseline.accepted_row_count
            if drop > thresholds.row_count_drop_ratio:
                findings.append(
                    GuardFinding(
                        "row_count_collapse",
                        "error",
                        f"accepted rows fell from {baseline.accepted_row_count} to "
                        f"{metrics.accepted_row_count} ({drop:.4f} > "
                        f"{thresholds.row_count_drop_ratio})",
                    )
                )
        if thresholds.row_count_drop_ratio is not None:
            prior_rows = baseline.rows_by_scope
            for scope in metrics.scope_metrics:
                before = prior_rows.get(scope.scope)
                if not before:
                    continue
                drop = (before - scope.accepted_row_count) / before
                if drop > thresholds.row_count_drop_ratio:
                    findings.append(
                        GuardFinding(
                            "scope_row_count_collapse",
                            "error",
                            f"{scope.exchange}/{scope.segment}: rows fell from "
                            f"{before} to {scope.accepted_row_count} ({drop:.4f} > "
                            f"{thresholds.row_count_drop_ratio})",
                        )
                    )
        current = {normalize_header(name) for name in metrics.headers}
        missing = sorted(
            name
            for name in baseline.headers
            if normalize_header(name) and normalize_header(name) not in current
        )
        if missing:
            findings.append(
                GuardFinding(
                    "header_drift",
                    "error",
                    f"headers present in snapshot {baseline.snapshot_id} are missing: "
                    f"{missing}",
                )
            )
        if metrics.effective_date < baseline.effective_date:
            findings.append(
                GuardFinding(
                    "stale_effective_date",
                    "error",
                    f"effective date {metrics.effective_date} is older than the "
                    f"baseline's {baseline.effective_date}",
                )
            )

    status = FAILED if any(item.severity == "error" for item in findings) else PASSED
    return GuardVerdict(
        status=status,
        findings=tuple(findings),
        metrics=metrics,
        baseline=baseline,
        thresholds=thresholds,
    )


def override(verdict: GuardVerdict) -> GuardVerdict:
    """Record a deliberate operator override without discarding the findings."""
    if verdict.status != FAILED:
        return verdict
    return GuardVerdict(
        status=OVERRIDDEN,
        findings=verdict.findings,
        metrics=verdict.metrics,
        baseline=verdict.baseline,
        thresholds=verdict.thresholds,
    )
