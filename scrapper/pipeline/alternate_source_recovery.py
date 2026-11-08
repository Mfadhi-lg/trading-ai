"""Resumable DA-06 official alternate-source discovery worker."""

from __future__ import annotations

import os
import socket
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS, THROTTLE_SECONDS
from ..core.http import (
    FetchResponse,
    fetch_exchange_recovery_surface,
    validate_exchange_dataset_url,
)
from ..db.documents.recovery.attempts import (
    RECOVERY_SOURCE_GROUPS,
    claim_recovery_target,
    finish_recovery_attempt,
)
from ..db.documents.recovery.candidates import (
    store_recovery_discovery_blob,
    persist_recovery_candidates,
    attach_recovery_candidate,
)
from ..db.repo import utc_now
from ..documents.recovery_matching import match_candidate
from ..sources.documents.bse_recovery import BSECorporateRatingResolver
from ..sources.documents.debt_recovery import DebtIssueDocumentResolver
from ..sources.documents.issuer_ir_recovery import IssuerIRResolver
from ..sources.documents.nse_recovery import NSECorporateAnnouncementResolver
from ..sources.documents.recovery_common import ISSUE_DOCUMENT_TERMS


def _exchange_observations(
    *,
    conn: Any,
    target: Any,
    source: str,
    discovered_at_utc: str,
    fetcher: Callable[..., FetchResponse],
    sleep: Callable[[float], None],
    throttle_seconds: float,
    discovery_snapshot_root: Path,
) -> tuple[list[tuple[Any, int]], dict[str, Any]]:
    """Compatible delegation retaining local dependency patch points."""
    from ..documents.recovery import alternate_sources
    return alternate_sources._exchange_observations(conn=conn, target=target, source=source, discovered_at_utc=discovered_at_utc, fetcher=fetcher, sleep=sleep, throttle_seconds=throttle_seconds, discovery_snapshot_root=discovery_snapshot_root, _hooks=__import__(__name__, fromlist=["*"]))


def _issuer_observations(
    conn: Any,
    *,
    target: Any,
    discovered_at_utc: str,
) -> tuple[list[tuple[Any, int]], dict[str, Any]]:
    """Compatible delegation retaining local dependency patch points."""
    from ..documents.recovery import alternate_sources
    return alternate_sources._issuer_observations(conn, target=target, discovered_at_utc=discovered_at_utc, _hooks=__import__(__name__, fromlist=["*"]))


def _process_claim(
    conn: Any,
    *,
    target: Any,
    recovery_attempt_id: int,
    source: str,
    worker: str,
    fetcher: Callable[..., FetchResponse],
    now: Callable[[], str],
    sleep: Callable[[float], None],
    throttle_seconds: float,
    discovery_snapshot_root: Path,
) -> str:
    """Compatible delegation retaining local dependency patch points."""
    from ..documents.recovery import alternate_sources
    return alternate_sources._process_claim(conn, target=target, recovery_attempt_id=recovery_attempt_id, source=source, worker=worker, fetcher=fetcher, now=now, sleep=sleep, throttle_seconds=throttle_seconds, discovery_snapshot_root=discovery_snapshot_root, _hooks=__import__(__name__, fromlist=["*"]))


def run_document_recovery_worker(
    conn: Any,
    *,
    sources: Iterable[str],
    limit: int = 25,
    lease_seconds: int = 600,
    throttle_seconds: float = THROTTLE_SECONDS,
    worker: str | None = None,
    fetcher: Callable[..., FetchResponse] = fetch_exchange_recovery_surface,
    now: Callable[[], str] = utc_now,
    sleep: Callable[[float], None] = time.sleep,
    discovery_snapshot_root: Path = DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS,
) -> dict[str, Any]:
    """Compatible delegation retaining local dependency patch points."""
    from ..documents.recovery import alternate_sources
    return alternate_sources.run_document_recovery_worker(conn, sources=sources, limit=limit, lease_seconds=lease_seconds, throttle_seconds=throttle_seconds, worker=worker, fetcher=fetcher, now=now, sleep=sleep, discovery_snapshot_root=discovery_snapshot_root, _hooks=__import__(__name__, fromlist=["*"]))
