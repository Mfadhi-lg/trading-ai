"""D09 lease-safe delayed-artifact reconciliation orchestration."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from ..config import (
    DB_PATH,
    DOCUMENT_RECONCILIATION_EXPORTS,
    SCREENER_MIN_MARKET_CAP_CRORE,
)
from ..db.artifact_reconciliation_repo import (
    ClaimedExpectation,
    claim_due_expectations,
    complete_artifact_check,
    preview_due_expectations,
    release_claims,
    screener_market_cap_above_floor,
)
from ..core.collection_scope import is_scope_exclusion
from ..db.database import connect, init_db
from ..db.repo import utc_now
from ..documents.availability import ArtifactCheckResult


RECONCILIATION_SCHEMA_VERSION = "1.0.0"


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


def _same_event(expectation: ClaimedExpectation, artifact: sqlite3.Row) -> bool:
    return bool(
        (expectation.event_date is not None and artifact["event_date"] == expectation.event_date)
        or (expectation.period_end is not None and artifact["period_end"] == expectation.period_end)
        or (
            expectation.fiscal_year is not None
            and expectation.fiscal_quarter is not None
            and artifact["fiscal_year"] == expectation.fiscal_year
            and artifact["fiscal_quarter"] == expectation.fiscal_quarter
        )
    )


def _accepted_artifact(
    conn: sqlite3.Connection, expectation: ClaimedExpectation
) -> sqlite3.Row | None:
    candidates = conn.execute(
        """SELECT * FROM document_artifacts
           WHERE company_id=? AND artifact_type=? ORDER BY artifact_id""",
        (expectation.company_id, expectation.artifact_type),
    ).fetchall()
    for artifact in reversed(candidates):
        if not _same_event(expectation, artifact):
            continue
        artifact_id = int(artifact["artifact_id"])
        if expectation.artifact_type == "transcript":
            accepted = conn.execute(
                """SELECT 1 FROM transcripts WHERE artifact_id=?
                   AND origin='official' AND validation_status='accepted' LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        elif expectation.artifact_type == "recording":
            accepted = conn.execute(
                """SELECT 1 FROM recording_normalizations WHERE artifact_id=?
                   AND normalization_status='accepted' LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        elif expectation.artifact_type == "credit_rating":
            accepted = conn.execute(
                """SELECT 1 FROM credit_rating_normalizations WHERE artifact_id=?
                   AND normalization_status='accepted' LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        else:
            accepted = conn.execute(
                """SELECT 1 FROM document_extractions AS extraction
                   JOIN artifact_contents AS content USING (content_id)
                   WHERE content.artifact_id=? AND extraction.extraction_status='ok'
                   LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        if accepted is not None:
            return artifact
    return None


class PersistedArtifactAdapter:
    """Reconcile source-adapter work already persisted by D01-D07.

    Production publisher adapters can wrap this class: check their source, pass a
    newly discovered item through D01-D07, then call this check to verify the
    durable accepted handoff before reporting availability.
    """

    source = "persisted_artifact_pipeline"

    def check(
        self,
        conn: sqlite3.Connection,
        expectation: ClaimedExpectation,
        checked_at_utc: str,
    ) -> ArtifactCheckResult:
        artifact = _accepted_artifact(conn, expectation)
        if artifact is not None:
            return ArtifactCheckResult(
                "available",
                self.source,
                evidence={
                    "acceptance_basis": "accepted_d01_d07_artifact",
                    "artifact_type": expectation.artifact_type,
                },
                discovered_artifact_id=int(artifact["artifact_id"]),
            )

        matching = [
            row
            for row in conn.execute(
                """SELECT artifact.*, source.document_url,
                          source.availability_status AS source_status
                   FROM document_artifacts AS artifact
                   LEFT JOIN artifact_sources AS source USING (artifact_id)
                   WHERE artifact.company_id=? AND artifact.artifact_type=?
                   ORDER BY artifact.artifact_id, source.artifact_source_id""",
                (expectation.company_id, expectation.artifact_type),
            ).fetchall()
            if _same_event(expectation, row)
        ]
        explicit = [
            row for row in matching
            if row["source_status"] in {
                "not_offered", "not_applicable", "source_not_covered",
                "inaccessible", "discovery_failed", "unknown",
            }
        ]
        if explicit and len(explicit) == len(matching):
            status_order = (
                "inaccessible", "discovery_failed", "unknown",
                "not_offered", "not_applicable", "source_not_covered",
            )
            status = next(
                item for item in status_order
                if any(row["source_status"] == item for row in explicit)
            )
            selected = next(row for row in explicit if row["source_status"] == status)
            return ArtifactCheckResult(
                status,
                self.source,
                requested_url=selected["document_url"],
                evidence={"basis": "persisted_explicit_source_status"},
                retryable=status in {"discovery_failed", "unknown"},
            )

        machine = None
        if expectation.artifact_type == "transcript":
            matching_artifact_ids = tuple(
                dict.fromkeys(int(row["artifact_id"]) for row in matching)
            )
            if matching_artifact_ids:
                placeholders = ",".join("?" for _ in matching_artifact_ids)
                machine = conn.execute(
                    f"""SELECT transcription_run_id
                        FROM machine_transcription_runs
                        WHERE transcript_artifact_id IN ({placeholders})
                          AND run_status='accepted'
                        ORDER BY transcription_run_id DESC LIMIT 1""",
                    matching_artifact_ids,
                ).fetchone()
        return ArtifactCheckResult(
            "expected_not_yet_published",
            self.source,
            evidence={
                "basis": "event_evidenced_artifact_not_accepted",
                "recording_fallback_present": machine is not None,
                "machine_transcription_run_id": (
                    int(machine["transcription_run_id"]) if machine is not None else None
                ),
            },
            retryable=True,
        )


def _source_refresh_targets(
    conn: sqlite3.Connection,
    due_rows: tuple[ClaimedExpectation, ...],
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
        state = conn.execute(
            """SELECT company_path_normalized, reporting_scope,
                      latest_accepted_result_period
                 FROM screener_company_collection_state
                WHERE company_id=? AND reporting_scope='standalone'
                ORDER BY updated_at_utc DESC LIMIT 1""",
            (company_id,),
        ).fetchone()
        if state is None or not state["latest_accepted_result_period"]:
            continue
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
                company_path=str(state["company_path_normalized"]),
                reporting_scope=str(state["reporting_scope"]),
                result_period=result_period,
                expectation_ids=tuple(
                    sorted(item.expectation_id for item in expectations)
                ),
            )
        )
    return tuple(targets)


def _default_screener_refresher(
    *,
    db_path: Path,
) -> ScreenerSourceRefresher:
    """Build one throttled client shared across this reconciliation batch."""

    from ..core.http import ScreenerHostClient
    from .screener_vertical import scrape_screener_company_path

    host_client = ScreenerHostClient()

    def refresh(target: ScreenerRefreshTarget) -> Any:
        return scrape_screener_company_path(
            target.company_path,
            target.reporting_scope,
            expected_result_period=target.result_period,
            db_path=db_path,
            host_client=host_client,
            document_only=True,
            expected_company_id=target.company_id,
        )

    return refresh


def _default_output(checked_at_utc: str, run_label: str) -> Path:
    safe = checked_at_utc.replace(":", "").replace("+", "_")
    safe_label = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in run_label
    )
    return DOCUMENT_RECONCILIATION_EXPORTS / f"{safe}-{safe_label}.json"


def _write(payload: dict[str, Any], output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {**payload, "output": str(output.resolve())}


def reconcile_artifacts(
    *,
    due: bool,
    limit: int = 100,
    dry_run: bool = False,
    db_path: Path = DB_PATH,
    output: Path | None = None,
    adapter: ArtifactReconciliationAdapter | None = None,
    source_refresher: ScreenerSourceRefresher | None = None,
    now_utc: Callable[[], str] = utc_now,
    worker_id: str | None = None,
) -> dict[str, Any]:
    if not due:
        raise ValueError("D09 reconciliation currently requires --due")
    production_adapter = adapter is None
    adapter = adapter or PersistedArtifactAdapter()
    checked_at = now_utc()
    worker_id = worker_id or f"reconciler-{uuid.uuid4().hex}"
    conn = connect(db_path)
    claimed: tuple[ClaimedExpectation, ...] = ()
    completed_ids: set[int] = set()
    checks: list[dict[str, Any]] = []
    refreshes: list[dict[str, Any]] = []
    refresh_failures: dict[int, tuple[str, bool, dict[str, str]]] = {}
    try:
        init_db(conn)
        if dry_run:
            due_preview = preview_due_expectations(
                conn, now_utc=checked_at, limit=limit
            )
            refresh_targets = _source_refresh_targets(conn, due_preview)
            return _write(
                {
                    "schema_version": RECONCILIATION_SCHEMA_VERSION,
                    "mode": "dry_run",
                    "checked_at_utc": checked_at,
                    "worker_id": None,
                    "policy_source": "persisted_per_expectation",
                    "summary": {
                        "due_count": len(due_preview),
                        "claimed_count": 0,
                        "completed_count": 0,
                        "source_refresh_count": len(refresh_targets),
                        "status_counts": {},
                    },
                    "due": [row.to_dict() for row in due_preview],
                    "source_refreshes": [
                        {**target.to_dict(), "status": "would_refresh"}
                        for target in refresh_targets
                    ],
                    "checks": [],
                },
                output or _default_output(checked_at, "dry-run"),
            )
        claimed = claim_due_expectations(
            conn,
            now_utc=checked_at,
            limit=limit,
            worker_id=worker_id,
        )
        refreshable: list[ClaimedExpectation] = []
        market_cap_eligibility: dict[int, bool] = {}
        for expectation in claimed:
            is_delayed_result_document = bool(
                expectation.artifact_type in {"presentation", "transcript"}
                and expectation.expectation_basis
                == "accepted_exchange_result_filing_requires_primary_source_discovery"
            )
            if is_delayed_result_document:
                eligible = market_cap_eligibility.get(expectation.company_id)
                if eligible is None:
                    eligible = screener_market_cap_above_floor(
                        conn, expectation.company_id
                    )
                    market_cap_eligibility[expectation.company_id] = eligible
                if not eligible:
                    refresh_failures[expectation.expectation_id] = (
                        "source_not_covered",
                        False,
                        {
                            "type": "ScreenerMarketCapOutOfScope",
                            "message": (
                                "Latest accepted Screener market cap is not above "
                                f"{SCREENER_MIN_MARKET_CAP_CRORE:g} INR crore"
                            ),
                        },
                    )
                    continue
            refreshable.append(expectation)
        refresh_targets = _source_refresh_targets(conn, tuple(refreshable))
        if production_adapter or source_refresher is not None:
            refresher = source_refresher or _default_screener_refresher(
                db_path=db_path
            )
            for position, target in enumerate(refresh_targets):
                try:
                    result = refresher(target)
                    refreshes.append(
                        {
                            **target.to_dict(),
                            "status": "refreshed",
                            "parse_id": getattr(result, "parse_id", None),
                        }
                    )
                except Exception as exc:
                    error = {"type": type(exc).__name__, "message": str(exc)}
                    below_scope = is_scope_exclusion(str(exc))
                    status = "source_not_covered" if below_scope else "discovery_failed"
                    refreshes.append(
                        {**target.to_dict(), "status": "failed", "error": error}
                    )
                    for expectation_id in target.expectation_ids:
                        refresh_failures[expectation_id] = (
                            status,
                            not below_scope,
                            error,
                        )
                    if "status=429" in str(exc):
                        deferred_error = {
                            "type": "ScreenerHostRateLimited",
                            "message": "Batch stopped after Screener returned HTTP 429",
                        }
                        for deferred in refresh_targets[position + 1 :]:
                            refreshes.append(
                                {
                                    **deferred.to_dict(),
                                    "status": "deferred_host_rate_limit",
                                    "error": deferred_error,
                                }
                            )
                            for expectation_id in deferred.expectation_ids:
                                refresh_failures[expectation_id] = (
                                    "discovery_failed",
                                    True,
                                    deferred_error,
                                )
                        break
        for expectation in claimed:
            refresh_failure = refresh_failures.get(expectation.expectation_id)
            try:
                if refresh_failure is not None:
                    failure_status, retryable, refresh_error = refresh_failure
                    result = ArtifactCheckResult(
                        failure_status,
                        "screener_company_page_refresh",
                        evidence={"event_key": expectation.event_key},
                        retryable=retryable,
                        error=refresh_error,
                    )
                else:
                    result = adapter.check(conn, expectation, checked_at)
            except Exception as exc:
                result = ArtifactCheckResult(
                    "discovery_failed",
                    "reconciliation_adapter",
                    evidence={"adapter": type(adapter).__name__},
                    retryable=True,
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
            check_id = complete_artifact_check(
                conn,
                expectation=expectation,
                result=result,
                checked_at_utc=checked_at,
            )
            completed_ids.add(expectation.expectation_id)
            state = conn.execute(
                """SELECT availability_status, attempt_count, next_check_at_utc,
                          terminal_reason FROM artifact_expectations
                   WHERE expectation_id=?""",
                (expectation.expectation_id,),
            ).fetchone()
            checks.append(
                {
                    "check_id": check_id,
                    "expectation_id": expectation.expectation_id,
                    "event_key": expectation.event_key,
                    "artifact_type": expectation.artifact_type,
                    "result_status": result.result_status,
                    "source": result.source,
                    "requested_url": result.requested_url,
                    "discovered_artifact_id": result.discovered_artifact_id,
                    "retryable": result.retryable,
                    "attempt_count": int(state["attempt_count"]),
                    "next_check_at_utc": state["next_check_at_utc"],
                    "terminal_reason": state["terminal_reason"],
                }
            )
    finally:
        remaining = tuple(
            row for row in claimed if row.expectation_id not in completed_ids
        )
        if remaining:
            release_claims(conn, remaining, released_at_utc=checked_at)
        conn.close()
    counts = Counter(row["result_status"] for row in checks)
    return _write(
        {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "mode": "execute",
            "checked_at_utc": checked_at,
            "worker_id": worker_id,
            "policy_source": "persisted_per_expectation",
            "summary": {
                "due_count": len(claimed),
                "claimed_count": len(claimed),
                "completed_count": len(checks),
                "source_refresh_count": len(refreshes),
                "source_refresh_failure_count": sum(
                    row["status"] != "refreshed" for row in refreshes
                ),
                "status_counts": dict(sorted(counts.items())),
            },
            "due": [row.to_dict() for row in claimed],
            "source_refreshes": refreshes,
            "checks": checks,
        },
        output or _default_output(checked_at, worker_id),
    )
