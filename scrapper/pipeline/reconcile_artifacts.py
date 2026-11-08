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
from ..documents.readiness.persisted import PersistedArtifactAdapter as _PersistedArtifactAdapter


RECONCILIATION_SCHEMA_VERSION = "1.0.0"


from ..domain.artifact_reconciliation import ArtifactReconciliationAdapter


from ..domain.artifact_reconciliation import ScreenerRefreshTarget


from ..domain.artifact_reconciliation import ScreenerSourceRefresher


def _same_event(expectation: ClaimedExpectation, artifact: sqlite3.Row) -> bool:
    from ..db.documents.readiness import _same_event as _owner
    return _owner(expectation, artifact)


def _accepted_artifact(
    conn: sqlite3.Connection, expectation: ClaimedExpectation
) -> sqlite3.Row | None:
    from ..db.documents.readiness import _accepted_artifact as _owner
    return _owner(conn, expectation, _same_event=_same_event)


class PersistedArtifactAdapter(_PersistedArtifactAdapter):
    def check(
        self,
        conn: sqlite3.Connection,
        expectation: ClaimedExpectation,
        checked_at_utc: str,
    ) -> ArtifactCheckResult:
        return super().check(conn, expectation, checked_at_utc,
                             _accepted_artifact=_accepted_artifact, _same_event=_same_event)


def _source_refresh_targets(
    conn: sqlite3.Connection,
    due_rows: tuple[ClaimedExpectation, ...],
) -> tuple[ScreenerRefreshTarget, ...]:
    from ..db.documents.due import _source_refresh_targets as _owner
    return _owner(conn, due_rows, ScreenerRefreshTarget=ScreenerRefreshTarget, _accepted_artifact=_accepted_artifact)


def _default_screener_refresher(
    *,
    db_path: Path,
) -> ScreenerSourceRefresher:
    from ..workflows.documents.reconciliation import _default_screener_refresher as _owner
    return _owner(db_path=db_path)


def _default_output(checked_at_utc: str, run_label: str) -> Path:
    from ..workflows.documents.reconciliation import _default_output as _owner
    return _owner(checked_at_utc, run_label, DOCUMENT_RECONCILIATION_EXPORTS=DOCUMENT_RECONCILIATION_EXPORTS)


def _write(payload: dict[str, Any], output: Path) -> dict[str, Any]:
    from ..workflows.documents.reconciliation import _write as _owner
    return _owner(payload, output, json=json)


def shared_client_factory(*args, **kwargs):
    from ..operations.workers.screener_requests import SharedScreenerClient
    return SharedScreenerClient(*args, **kwargs)


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
) -> dict[str, Any]:
    from ..workflows.documents.reconciliation import reconcile_artifacts as _owner
    return _owner(due=due, limit=limit, dry_run=dry_run, db_path=db_path, output=output, adapter=adapter, source_refresher=source_refresher, now_utc=now_utc, worker_id=worker_id, shared_permits=shared_permits, shared_client_factory=shared_client_factory, ArtifactCheckResult=ArtifactCheckResult, Counter=Counter, PersistedArtifactAdapter=PersistedArtifactAdapter, RECONCILIATION_SCHEMA_VERSION=RECONCILIATION_SCHEMA_VERSION, SCREENER_MIN_MARKET_CAP_CRORE=SCREENER_MIN_MARKET_CAP_CRORE, _default_output=_default_output, _default_screener_refresher=_default_screener_refresher, _source_refresh_targets=_source_refresh_targets, _write=_write, claim_due_expectations=claim_due_expectations, complete_artifact_check=complete_artifact_check, connect=connect, init_db=init_db, is_scope_exclusion=is_scope_exclusion, preview_due_expectations=preview_due_expectations, release_claims=release_claims, screener_market_cap_above_floor=screener_market_cap_above_floor, uuid=uuid)
