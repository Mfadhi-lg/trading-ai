"""Durable DA-06 target/candidate registry primitives."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from ..config import DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS
from ..core.access_policy import BLOCKED_TERMS_HOSTS, host_matches_policy
from ..documents.rating_evidence import (
    RATING_EVIDENCE_CLASSIFIER_NAME,
    RATING_EVIDENCE_CLASSIFIER_VERSION,
    classify_rating_evidence,
)
from ..documents.recovery import (
    RECOVERY_CONTRACT_VERSION,
    DocumentOriginator,
    RecoveryCandidate,
    RecoveryConfidence,
    RecoverySourceType,
    RecoveryTarget,
    RecoveryTargetStatus,
)
from .document_download_queue_repo import ensure_recovery_download_scope
from .repo import utc_now


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


RECOVERY_SOURCE_GROUPS = ("nse", "bse", "issuer-ir")


def _recovery_source_group(source_type: str) -> str | None:
    """Compatible delegation to recovery lease/source-attempt persistence."""
    from .documents.recovery import attempts
    return attempts._recovery_source_group(source_type, _hooks=__import__(__name__, fromlist=["*"]))


def _remaining_source_status(conn: sqlite3.Connection, target_id: int) -> str:
    """Compatible delegation to recovery lease/source-attempt persistence."""
    from .documents.recovery import attempts
    return attempts._remaining_source_status(conn, target_id, _hooks=__import__(__name__, fromlist=["*"]))


def _after(value: str, seconds: int) -> str:
    """Compatible delegation to recovery lease/source-attempt persistence."""
    from .documents.recovery import attempts
    return attempts._after(value, seconds, _hooks=__import__(__name__, fromlist=["*"]))


def _originator(host: str) -> DocumentOriginator | None:
    """Compatible delegation to recovery target persistence."""
    from .documents.recovery import targets
    return targets._originator(host, _hooks=__import__(__name__, fromlist=["*"]))


def _blocked_artifacts(
    conn: sqlite3.Connection,
    artifact_ids: tuple[int, ...] | None = None,
) -> tuple[sqlite3.Row, ...]:
    """Compatible delegation to recovery target persistence."""
    from .documents.recovery import targets
    return targets._blocked_artifacts(conn, artifact_ids, _hooks=__import__(__name__, fromlist=["*"]))


def _identities(conn: sqlite3.Connection, company_id: int) -> dict[str, tuple[str, ...]]:
    """Compatible delegation to recovery target persistence."""
    from .documents.recovery import targets
    return targets._identities(conn, company_id, _hooks=__import__(__name__, fromlist=["*"]))


def _rating_context(conn: sqlite3.Connection, artifact_id: int) -> dict[str, str | None]:
    """Compatible delegation to recovery target persistence."""
    from .documents.recovery import targets
    return targets._rating_context(conn, artifact_id, _hooks=__import__(__name__, fromlist=["*"]))


def _already_satisfied(conn: sqlite3.Connection, artifact_id: int) -> bool:
    """Compatible delegation to recovery target persistence."""
    from .documents.recovery import targets
    return targets._already_satisfied(conn, artifact_id, _hooks=__import__(__name__, fromlist=["*"]))


def _target_from_row(
    conn: sqlite3.Connection, row: sqlite3.Row, originator: DocumentOriginator
) -> RecoveryTarget:
    """Compatible delegation to recovery target persistence."""
    from .documents.recovery import targets
    return targets._target_from_row(conn, row, originator, _hooks=__import__(__name__, fromlist=["*"]))


def build_recovery_targets(
    conn: sqlite3.Connection,
    *,
    artifact_type: str = "credit_rating",
    dry_run: bool = False,
    observed_at_utc: str | None = None,
    artifact_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    """Compatible delegation to recovery target persistence."""
    from .documents.recovery import targets
    return targets.build_recovery_targets(conn, artifact_type=artifact_type, dry_run=dry_run, observed_at_utc=observed_at_utc, artifact_ids=artifact_ids, _hooks=__import__(__name__, fromlist=["*"]))


def load_recovery_target(conn: sqlite3.Connection, recovery_target_id: int) -> RecoveryTarget:
    """Compatible delegation to recovery target persistence."""
    from .documents.recovery import targets
    return targets.load_recovery_target(conn, recovery_target_id, _hooks=__import__(__name__, fromlist=["*"]))


def claim_recovery_target(
    conn: sqlite3.Connection,
    *,
    source: str,
    worker: str,
    now_utc: str,
    lease_seconds: int = 600,
) -> tuple[RecoveryTarget, int] | None:
    """Compatible delegation to recovery lease/source-attempt persistence."""
    from .documents.recovery import attempts
    return attempts.claim_recovery_target(conn, source=source, worker=worker, now_utc=now_utc, lease_seconds=lease_seconds, _hooks=__import__(__name__, fromlist=["*"]))


def finish_recovery_attempt(
    conn: sqlite3.Connection,
    *,
    recovery_attempt_id: int,
    worker: str,
    status: str,
    completed_at_utc: str,
    result_evidence: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    retry_seconds: int | None = None,
) -> None:
    """Compatible delegation to recovery lease/source-attempt persistence."""
    from .documents.recovery import attempts
    return attempts.finish_recovery_attempt(conn, recovery_attempt_id=recovery_attempt_id, worker=worker, status=status, completed_at_utc=completed_at_utc, result_evidence=result_evidence, error=error, retry_seconds=retry_seconds, _hooks=__import__(__name__, fromlist=["*"]))


def store_recovery_discovery_blob(
    conn: sqlite3.Connection,
    *,
    content: bytes,
    source: str,
    requested_url: str,
    content_type: str,
    observed_at_utc: str,
    snapshot_root: Path = DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS,
) -> int:
    """Compatible delegation to recovery candidate/attachment persistence."""
    from .documents.recovery import candidates as candidate_repository
    return candidate_repository.store_recovery_discovery_blob(conn, content=content, source=source, requested_url=requested_url, content_type=content_type, observed_at_utc=observed_at_utc, snapshot_root=snapshot_root, _hooks=__import__(__name__, fromlist=["*"]))


def persist_recovery_candidates(
    conn: sqlite3.Connection,
    *,
    recovery_target_id: int,
    candidates: tuple[RecoveryCandidate, ...],
    discovery_blob_id: int,
    persisted_at_utc: str | None = None,
) -> dict[str, Any]:
    """Compatible delegation to recovery candidate/attachment persistence."""
    from .documents.recovery import candidates as candidate_repository
    return candidate_repository.persist_recovery_candidates(conn, recovery_target_id=recovery_target_id, candidates=candidates, discovery_blob_id=discovery_blob_id, persisted_at_utc=persisted_at_utc, _hooks=__import__(__name__, fromlist=["*"]))


def attach_recovery_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_id: int,
    attached_at_utc: str | None = None,
    queue_priority: int = 50,
) -> dict[str, Any]:
    """Compatible delegation to recovery candidate/attachment persistence."""
    from .documents.recovery import candidates as candidate_repository
    return candidate_repository.attach_recovery_candidate(conn, candidate_id=candidate_id, attached_at_utc=attached_at_utc, queue_priority=queue_priority, _hooks=__import__(__name__, fromlist=["*"]))


def reconcile_recovery_for_source(
    conn: sqlite3.Connection,
    *,
    artifact_source_id: int,
    content_id: int,
    reconciled_at_utc: str | None = None,
) -> dict[str, Any] | None:
    """Compatible delegation to recovery reconciliation persistence."""
    from .documents.recovery import reconciliation
    return reconciliation.reconcile_recovery_for_source(conn, artifact_source_id=artifact_source_id, content_id=content_id, reconciled_at_utc=reconciled_at_utc, _hooks=__import__(__name__, fromlist=["*"]))


def classify_direct_rating_evidence(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    content_id: int,
    classified_at_utc: str | None = None,
) -> list[dict[str, Any]]:
    """Compatible delegation to recovery reconciliation persistence."""
    from .documents.recovery import reconciliation
    return reconciliation.classify_direct_rating_evidence(conn, artifact_id=artifact_id, content_id=content_id, classified_at_utc=classified_at_utc, _hooks=__import__(__name__, fromlist=["*"]))


def reopen_recovery_after_download_failure(
    conn: sqlite3.Connection,
    *,
    artifact_source_id: int,
    failed_at_utc: str | None = None,
) -> dict[str, Any] | None:
    """Compatible delegation to recovery reconciliation persistence."""
    from .documents.recovery import reconciliation
    return reconciliation.reopen_recovery_after_download_failure(conn, artifact_source_id=artifact_source_id, failed_at_utc=failed_at_utc, _hooks=__import__(__name__, fromlist=["*"]))
