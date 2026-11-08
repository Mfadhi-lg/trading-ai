"""Lease-based DA-05 worker for the general artifact download queue."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

import requests

from ..core.access_policy import (
    BlockedTermsError,
    PUBLISHER_TERMS_ERROR,
    PUBLISHER_TERMS_RESULT,
)
from ..config import (
    DOCUMENT_CONTENT_SNAPSHOTS,
    DOCUMENT_DOWNLOAD_EXPORTS,
    DOCUMENT_STORE,
    THROTTLE_SECONDS,
)
from ..db.document_download_queue_repo import (
    attach_download_attempt,
    blocked_terms_hosts,
    claim_download_job,
    finish_download_job,
    queue_counts,
    source_ids_for_job,
)
from ..db.documents.recovery.reconciliation import (
    reconcile_recovery_for_source,
    reopen_recovery_after_download_failure,
)
from ..db.documents.download import persist_download_result
from ..db.repo import utc_now
from ..documents.downloader import (
    PDF,
    DownloadPolicy,
    DownloadResult,
    cleanup_recording_temporary_files,
    download_recording_to_store,
    download_url,
)
from ..documents.credit_rating_resolver import is_genuine_html_credit_report
from ..db.documents.rendering import render_and_persist_html_pdf
from ..documents.store import assert_document_store_ready


DOWNLOAD_QUEUE_REPORT_SCHEMA_VERSION = "1.0.0"


def _retryable(result: DownloadResult) -> bool:
    """Compatible helper entry point for the extracted owner."""
    from ..documents.download.retry import _retryable as _owner
    return _owner(result)


def _blocks_host(result: DownloadResult) -> bool:
    """Compatible helper entry point for the extracted owner."""
    from ..documents.download.retry import _blocks_host as _owner
    return _owner(result)


def _transport_host_failure(result: DownloadResult) -> bool:
    """Compatible helper entry point for the extracted owner."""
    from ..documents.download.retry import _transport_host_failure as _owner
    return _owner(result)


def _iso_after(now_utc: str, seconds: int) -> str:
    """Compatible helper entry point for the extracted owner."""
    from ..documents.download.retry import _iso_after as _owner
    return _owner(now_utc, seconds, datetime=datetime, timedelta=timedelta, timezone=timezone)


def _retry_after_seconds(result: DownloadResult, *, now_utc: str) -> int | None:
    """Compatible helper entry point for the extracted owner."""
    from ..documents.download.retry import _retry_after_seconds as _owner
    return _owner(result, now_utc=now_utc, datetime=datetime, parsedate_to_datetime=parsedate_to_datetime, timezone=timezone)


def _record_host_block(
    conn: sqlite3.Connection,
    *,
    host: str,
    result: DownloadResult,
    scope_id: str | None,
    now_utc: str,
) -> str:
    """Compatible helper entry point for the extracted owner."""
    from ..db.documents.host_health import _record_host_block as _owner
    return _owner(conn, host=host, result=result, scope_id=scope_id, now_utc=now_utc, _iso_after=_iso_after, _retry_after_seconds=_retry_after_seconds)


def _record_host_success(
    conn: sqlite3.Connection, *, host: str, result: DownloadResult, now_utc: str
) -> None:
    """Compatible helper entry point for the extracted owner."""
    from ..db.documents.host_health import _record_host_success as _owner
    return _owner(conn, host=host, result=result, now_utc=now_utc)


def _record_transport_host_failure(
    conn: sqlite3.Connection,
    *,
    host: str,
    result: DownloadResult,
    scope_id: str | None,
    now_utc: str,
    threshold: int = 3,
) -> str | None:
    """Compatible helper entry point for the extracted owner."""
    from ..db.documents.host_health import _record_transport_host_failure as _owner
    return _owner(conn, host=host, result=result, scope_id=scope_id, now_utc=now_utc, threshold=threshold, _iso_after=_iso_after)


def _error_text(result: DownloadResult) -> str | None:
    """Compatible helper entry point for the extracted owner."""
    from ..documents.download.retry import _error_text as _owner
    return _owner(result)


def _scope_policy(conn: sqlite3.Connection, scope_id: str | None) -> dict[str, Any]:
    """Compatible entry point for the extracted owner."""
    from ..db.documents.download_reporting import _scope_policy as _owner
    return _owner(conn, scope_id)


def build_download_queue_report(
    conn: sqlite3.Connection, *, scope_id: str | None
) -> dict[str, Any]:
    """Compatible entry point for the extracted owner."""
    from ..db.documents.download_reporting import build_download_queue_report as _owner
    return _owner(conn, scope_id=scope_id, queue_counts=queue_counts, schema_version=DOWNLOAD_QUEUE_REPORT_SCHEMA_VERSION)


def write_download_queue_report(
    conn: sqlite3.Connection,
    *,
    scope_id: str | None,
    output: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    payload = build_download_queue_report(conn, scope_id=scope_id)
    name = scope_id or "all-scopes"
    path = output or DOCUMENT_DOWNLOAD_EXPORTS / "queue" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload, path


def run_document_download_worker(
    conn: sqlite3.Connection,
    *,
    limit: int = 25,
    scope_id: str | None = None,
    artifact_types: tuple[str, ...] = (),
    policy: DownloadPolicy | None = None,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
    document_store: Path = DOCUMENT_STORE,
    worker: str | None = None,
    lease_seconds: int = 600,
    max_attempts: int = 3,
    throttle_seconds: float = THROTTLE_SECONDS,
    session_factory: Callable[[], Any] = requests.Session,
    now: Callable[[], str] = utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    health_check: Callable[[Path], Any] = assert_document_store_ready,
    lifecycle: Any = None,
    shared_permits: bool = False,
    eligible_scopes: tuple[str, ...] = (),
    allowed_job_ids: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """Compatible entry point for the extracted owner."""
    from ..workflows.documents.download import run_document_download_worker as _owner
    return _owner(conn, limit=limit, scope_id=scope_id, artifact_types=artifact_types, policy=policy, snapshot_root=snapshot_root, document_store=document_store, worker=worker, lease_seconds=lease_seconds, max_attempts=max_attempts, throttle_seconds=throttle_seconds, session_factory=session_factory, now=now, monotonic=monotonic, sleep=sleep, health_check=health_check, lifecycle=lifecycle, shared_permits=shared_permits, eligible_scopes=eligible_scopes, allowed_job_ids=allowed_job_ids, _hooks=__import__(__name__, fromlist=["*"]))
