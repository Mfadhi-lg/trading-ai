"""Failed download repair strategies using canonical queues and immutable storage."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from ...config import (
    DOCUMENT_CONTENT_SNAPSHOTS,
    DOCUMENT_MAX_RESPONSE_BYTES,
)
from ...core.access_policy import (
    PUBLISHER_TERMS_ERROR,
    PUBLISHER_TERMS_RESULT,
    is_blocked_terms_target,
)
from ...db.document_download_queue_repo import blocked_terms_hosts
from ...db.document_artifact_repo import register_artifact_content
from ...db.document_download_repo import store_response_blob
from ...db.repo import utc_now
from ..downloader import ALLOWED_MEDIA_BY_ARTIFACT, detect_media_type


RECOVERY_REPORT_SCHEMA_VERSION = "1.0.0"
RECOVERY_REASON_CODES = (
    "TRANSIENT_SOURCE_FAILURE",
    "SOURCE_REPUBLISHED",
    "RESOLVER_UPDATED",
    "OPERATOR_VERIFIED",
    "DISCOVERY_REFRESH",
)
QUEUE_TABLES = {
    "generic": "artifact_download_queue",
    "credit_rating": "credit_rating_download_queue",
}
REQUEUEABLE = {
    "generic": frozenset({"RETRYABLE_FAILURE", "TERMINAL_FAILURE"}),
    "credit_rating": frozenset(
        {"RETRYABLE_FAILURE", "TERMINAL_FAILURE", "INTERACTION_REQUIRED"}
    ),
}


def _normalized_http_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Replacement URL has an invalid port") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError(
            "Replacement URL must be absolute HTTP(S) without embedded credentials"
        )
    host = parsed.hostname.lower().rstrip(".")
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit(
        (parsed.scheme.lower(), host, parsed.path or "/", parsed.query, "")
    )


def requeue_document_downloads(
    conn: sqlite3.Connection,
    *,
    queue_kind: str,
    job_ids: Iterable[int],
    reason_code: str,
    reason_detail: str | None = None,
    dry_run: bool = False,
    applied_at_utc: str | None = None,
    _hooks: Any = None,
) -> dict[str, Any]:
    """Run the audited strategy through canonical recovery persistence."""
    from ...db.documents.recovery import failed_download
    hooks = _hooks if _hooks is not None else __import__(__name__, fromlist=["*"])
    return failed_download.requeue_document_downloads(conn, queue_kind=queue_kind, job_ids=job_ids, reason_code=reason_code, reason_detail=reason_detail, dry_run=dry_run, applied_at_utc=applied_at_utc, _hooks=hooks)

def register_replacement_document_url(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    replaces_artifact_source_id: int,
    replacement_url: str,
    reason_code: str,
    reason_detail: str | None = None,
    evidence: dict[str, Any] | None = None,
    dry_run: bool = False,
    applied_at_utc: str | None = None,
    _hooks: Any = None,
) -> dict[str, Any]:
    """Run the audited strategy through canonical recovery persistence."""
    from ...db.documents.recovery import failed_download
    hooks = _hooks if _hooks is not None else __import__(__name__, fromlist=["*"])
    return failed_download.register_replacement_document_url(conn, artifact_id=artifact_id, replaces_artifact_source_id=replaces_artifact_source_id, replacement_url=replacement_url, reason_code=reason_code, reason_detail=reason_detail, evidence=evidence, dry_run=dry_run, applied_at_utc=applied_at_utc, _hooks=hooks)

def import_manual_document(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    artifact_source_id: int,
    path: Path,
    official: bool,
    reason_detail: str,
    evidence: dict[str, Any] | None = None,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
    max_bytes: int = DOCUMENT_MAX_RESPONSE_BYTES,
    imported_at_utc: str | None = None,
    _hooks: Any = None,
) -> dict[str, Any]:
    """Run the audited strategy through canonical recovery persistence."""
    from ...db.documents.recovery import failed_download
    hooks = _hooks if _hooks is not None else __import__(__name__, fromlist=["*"])
    return failed_download.import_manual_document(conn, artifact_id=artifact_id, artifact_source_id=artifact_source_id, path=path, official=official, reason_detail=reason_detail, evidence=evidence, snapshot_root=snapshot_root, max_bytes=max_bytes, imported_at_utc=imported_at_utc, _hooks=hooks)
