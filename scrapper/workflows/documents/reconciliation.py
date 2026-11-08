"""Bounded delayed document-only refresh and reconciliation workflow."""
from __future__ import annotations
import json
import sqlite3
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from ...config import DB_PATH, DOCUMENT_RECONCILIATION_EXPORTS, SCREENER_MIN_MARKET_CAP_CRORE
from ...domain.artifact_reconciliation import ClaimedExpectation, ArtifactReconciliationAdapter, ScreenerRefreshTarget, ScreenerSourceRefresher
from ...db.documents.due import checked_expectation_state, preview_due_expectations, claim_due_expectations, complete_artifact_check, release_claims, _source_refresh_targets
from ...db.documents.expectations import screener_market_cap_above_floor
from ...core.collection_scope import is_scope_exclusion
from ...db.database import connect, init_db
from ...db.repo import utc_now
from ...documents.availability import ArtifactCheckResult
from ...documents.readiness.persisted import PersistedArtifactAdapter
RECONCILIATION_SCHEMA_VERSION = "1.0.0"

def _default_screener_refresher(
    *,
    db_path: Path,
) -> ScreenerSourceRefresher:
    """Build one throttled client shared across this reconciliation batch."""

    from ...core.http import ScreenerHostClient
    from ..screener.ingest import scrape_screener_company_path

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


def _default_output(checked_at_utc: str, run_label: str, *, DOCUMENT_RECONCILIATION_EXPORTS=DOCUMENT_RECONCILIATION_EXPORTS) -> Path:
    safe = checked_at_utc.replace(":", "").replace("+", "_")
    safe_label = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in run_label
    )
    return DOCUMENT_RECONCILIATION_EXPORTS / f"{safe}-{safe_label}.json"


def _write(payload: dict[str, Any], output: Path, *, json=json) -> dict[str, Any]:
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
    shared_permits: bool = False,
    shared_client_factory=None,
    document_connection_factory=None,
    ArtifactCheckResult=ArtifactCheckResult,
    Counter=Counter,
    PersistedArtifactAdapter=PersistedArtifactAdapter,
    RECONCILIATION_SCHEMA_VERSION=RECONCILIATION_SCHEMA_VERSION,
    SCREENER_MIN_MARKET_CAP_CRORE=SCREENER_MIN_MARKET_CAP_CRORE,
    _default_output=_default_output,
    _default_screener_refresher=_default_screener_refresher,
    _source_refresh_targets=_source_refresh_targets,
    _write=_write,
    claim_due_expectations=claim_due_expectations,
    complete_artifact_check=complete_artifact_check,
    connect=connect,
    init_db=init_db,
    is_scope_exclusion=is_scope_exclusion,
    preview_due_expectations=preview_due_expectations,
    release_claims=release_claims,
    screener_market_cap_above_floor=screener_market_cap_above_floor,
    uuid=uuid,
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
    shared_client = None
    deferred_ids = set()
    try:
        init_db(conn)
        if shared_permits and not dry_run:
            if shared_client_factory is None:
                raise ValueError("Shared reconciliation requires an explicit transport factory")
            if source_refresher is not None or not production_adapter:
                raise ValueError('Shared reconciliation requires the canonical refresher/adapter')
            shared_client = shared_client_factory(conn,owner=worker_id)
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
            if shared_client is not None:
                from ..screener.ingest import scrape_screener_company_path
                def refresher(target):
                    rows = [e for e in claimed if e.expectation_id in target.expectation_ids]
                    shared_client.bind([('expectation',e.expectation_id,e.claim_token) for e in rows],now_utc)
                    return scrape_screener_company_path(target.company_path,target.reporting_scope,expected_result_period=target.result_period,db_path=db_path,host_client=shared_client,document_only=True,expected_company_id=target.company_id,**({"document_connection_factory":document_connection_factory} if document_connection_factory else {}))
            else:
                refresher = source_refresher or _default_screener_refresher(db_path=db_path)
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
                    from ...core.host_request_contracts import AdministrativeDefer
                    from ...db.document_work_claims import OwnershipLost
                    if shared_permits and isinstance(exc,OwnershipLost):
                        raise
                    if shared_permits and isinstance(exc,AdministrativeDefer):
                        from ...db.screener_host_defer import defer
                        from ...config import SNAPSHOTS
                        shared_client.check()
                        shared_client.preserve_partial(snapshot_root=SNAPSHOTS,now_utc=now_utc())
                        for expectation_id in target.expectation_ids:
                            expectation = next(e for e in claimed if e.expectation_id == expectation_id)
                            defer(conn,'expectation',expectation_id,expectation.claim_token,now=now_utc(),retry_at=exc.retry_at,dispatched=shared_client.transport.dispatched,reason=exc.reason)
                            deferred_ids.add(expectation_id)
                            completed_ids.add(expectation_id)
                        refreshes.append({**target.to_dict(),'status':'administrative_defer','reason':exc.reason,'retry_at':exc.retry_at,'dispatched':shared_client.transport.dispatched})
                        continue
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
            if expectation.expectation_id in deferred_ids:
                continue
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
            if shared_permits:
                from ...db.screener_host_defer import renew
                renew(conn,'expectation',expectation.expectation_id,expectation.claim_token,now=now_utc())
            check_id = complete_artifact_check(
                conn,
                expectation=expectation,
                result=result,
                checked_at_utc=checked_at,
            )
            completed_ids.add(expectation.expectation_id)
            state = checked_expectation_state(conn, expectation)
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
        if shared_client is not None:
            shared_client.close()
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
