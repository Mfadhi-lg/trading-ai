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
from ..db.document_recovery_repo import (
    reconcile_recovery_for_source,
    reopen_recovery_after_download_failure,
)
from ..db.document_download_repo import persist_download_result
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
from ..db.html_document_rendering_repo import render_and_persist_html_pdf
from ..documents.store import assert_document_store_ready


DOWNLOAD_QUEUE_REPORT_SCHEMA_VERSION = "1.0.0"


def _retryable(result: DownloadResult) -> bool:
    if result.result_status in {"network_error", "timed_out", "rejected_unexpected_html"}:
        return True
    return result.result_status == "rejected_http_status" and (
        result.http_status in {401, 403, 408, 425, 429}
        or bool(result.http_status and result.http_status >= 500)
    )


def _blocks_host(result: DownloadResult) -> bool:
    return (
        result.result_status == "rejected_http_status"
        and result.http_status in {401, 403, 429}
    )


def _transport_host_failure(result: DownloadResult) -> bool:
    return result.result_status in {
        "network_error", "timed_out", "rejected_unexpected_html"
    } or (
        result.result_status == "rejected_http_status"
        and bool(result.http_status and result.http_status >= 500)
    )


def _iso_after(now_utc: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).isoformat()


def _retry_after_seconds(result: DownloadResult, *, now_utc: str) -> int | None:
    raw = result.response_headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = int(raw.strip())
    except ValueError:
        try:
            target = parsedate_to_datetime(raw)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            current = datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            seconds = int((target - current).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return max(60, min(24 * 60 * 60, seconds))


def _record_host_block(
    conn: sqlite3.Connection,
    *,
    host: str,
    result: DownloadResult,
    scope_id: str | None,
    now_utc: str,
) -> str:
    if result.http_status in {401, 403}:
        default_seconds = 24 * 60 * 60
    elif result.http_status == 429:
        default_seconds = 6 * 60 * 60
    else:
        default_seconds = 60 * 60
    seconds = _retry_after_seconds(result, now_utc=now_utc) or default_seconds
    blocked_until = _iso_after(now_utc, seconds)
    raw_retry_after = result.response_headers.get("retry-after")
    with conn:
        conn.execute(
            """INSERT INTO artifact_download_host_health
               (host, blocked_until_utc, last_http_status, consecutive_blocks,
                last_result_status, retry_after_raw, updated_at_utc)
               VALUES (?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(host) DO UPDATE SET
                 blocked_until_utc=excluded.blocked_until_utc,
                 last_http_status=excluded.last_http_status,
                 consecutive_blocks=artifact_download_host_health.consecutive_blocks+1,
                 last_result_status=excluded.last_result_status,
                 retry_after_raw=excluded.retry_after_raw,
                 updated_at_utc=excluded.updated_at_utc""",
            (
                host,
                blocked_until,
                result.http_status,
                result.result_status,
                raw_retry_after,
                now_utc,
            ),
        )
    return blocked_until


def _record_host_success(
    conn: sqlite3.Connection, *, host: str, result: DownloadResult, now_utc: str
) -> None:
    with conn:
        conn.execute(
            """UPDATE artifact_download_host_health
                  SET blocked_until_utc=NULL, last_http_status=?,
                      consecutive_blocks=0, last_result_status=?,
                      retry_after_raw=NULL, updated_at_utc=?
                WHERE host=?""",
            (result.http_status, result.result_status, now_utc, host),
        )


def _record_transport_host_failure(
    conn: sqlite3.Connection,
    *,
    host: str,
    result: DownloadResult,
    scope_id: str | None,
    now_utc: str,
    threshold: int = 3,
) -> str | None:
    """Open a bounded circuit only after repeated transport/server failures."""

    if threshold < 1:
        raise ValueError("Host failure threshold must be positive")
    current = conn.execute(
        "SELECT consecutive_blocks,last_result_status FROM artifact_download_host_health WHERE host=?",
        (host,),
    ).fetchone()
    previous = (
        int(current["consecutive_blocks"])
        if current is not None
        and str(current["last_result_status"] or "")
        in {
            "network_error", "timed_out", "rejected_http_status",
            "rejected_unexpected_html",
        }
        else 0
    )
    failures = previous + 1
    blocked_until = _iso_after(now_utc, 60 * 60) if failures >= threshold else None
    with conn:
        conn.execute(
            """INSERT INTO artifact_download_host_health
               (host,blocked_until_utc,last_http_status,consecutive_blocks,
                last_result_status,retry_after_raw,updated_at_utc)
               VALUES (?,?,?,?,?,NULL,?)
               ON CONFLICT(host) DO UPDATE SET
                 blocked_until_utc=excluded.blocked_until_utc,
                 last_http_status=excluded.last_http_status,
                 consecutive_blocks=excluded.consecutive_blocks,
                 last_result_status=excluded.last_result_status,
                 retry_after_raw=NULL,updated_at_utc=excluded.updated_at_utc""",
            (
                host,
                blocked_until,
                result.http_status,
                failures,
                result.result_status,
                now_utc,
            ),
        )
    return blocked_until


def _error_text(result: DownloadResult) -> str | None:
    if not result.error_code and not result.error_message:
        return None
    return ": ".join(
        value for value in (result.error_code, result.error_message) if value
    )


def _scope_policy(conn: sqlite3.Connection, scope_id: str | None) -> dict[str, Any]:
    if scope_id is None:
        return {}
    row = conn.execute(
        "SELECT policy_json FROM artifact_download_scopes WHERE scope_id=?",
        (scope_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown artifact download scope: {scope_id}")
    return dict(json.loads(row["policy_json"]))


def build_download_queue_report(
    conn: sqlite3.Connection, *, scope_id: str | None
) -> dict[str, Any]:
    """Return queue, host, type, and actual byte-size accounting."""

    scope_filter = " WHERE queue.scope_id=?" if scope_id is not None else ""
    parameters = (scope_id,) if scope_id is not None else ()
    attempt = conn.execute(
        f"""SELECT COUNT(*) AS attempt_count,
                   COALESCE(SUM(download.byte_size), 0) AS response_bytes,
                   COALESCE(SUM(CASE WHEN download.result_status='validated'
                                     THEN download.byte_size ELSE 0 END), 0)
                     AS validated_response_bytes
              FROM artifact_download_queue AS queue
              JOIN artifact_download_queue_attempts AS link USING (download_job_id)
              JOIN artifact_download_attempts AS download USING (download_attempt_id)
              {scope_filter}""",
        parameters,
    ).fetchone()
    unique = conn.execute(
        f"""SELECT COUNT(*) AS content_count,
                   COALESCE(SUM(byte_size), 0) AS content_bytes
              FROM (
                SELECT DISTINCT content.content_id, content.byte_size
                  FROM artifact_download_queue AS queue
                  JOIN artifact_download_queue_attempts AS link USING (download_job_id)
                  JOIN artifact_download_attempts AS download USING (download_attempt_id)
                  JOIN artifact_contents AS content USING (content_id)
                  {scope_filter}
              )""",
        parameters,
    ).fetchone()
    group_parameters = parameters
    by_host = {
        str(row["target_host"]): {
            "job_count": int(row["job_count"]),
            "validated_job_count": int(row["validated_job_count"]),
        }
        for row in conn.execute(
            f"""SELECT queue.target_host, COUNT(*) AS job_count,
                       SUM(CASE WHEN queue.status='SUCCEEDED' THEN 1 ELSE 0 END)
                         AS validated_job_count
                  FROM artifact_download_queue AS queue
                  {scope_filter}
                 GROUP BY queue.target_host ORDER BY queue.target_host""",
            group_parameters,
        )
    }
    by_type = {
        str(row["artifact_type"]): {
            "job_count": int(row["job_count"]),
            "validated_job_count": int(row["validated_job_count"]),
        }
        for row in conn.execute(
            f"""SELECT artifact.artifact_type, COUNT(*) AS job_count,
                       SUM(CASE WHEN queue.status='SUCCEEDED' THEN 1 ELSE 0 END)
                         AS validated_job_count
                  FROM artifact_download_queue AS queue
                  JOIN document_artifacts AS artifact USING (artifact_id)
                  {scope_filter}
                 GROUP BY artifact.artifact_type ORDER BY artifact.artifact_type""",
            group_parameters,
        )
    }
    return {
        "schema_version": DOWNLOAD_QUEUE_REPORT_SCHEMA_VERSION,
        "scope_id": scope_id,
        "queue": queue_counts(conn, scope_id=scope_id),
        "bytes": {
            "attempt_count": int(attempt["attempt_count"]),
            "response_bytes": int(attempt["response_bytes"]),
            "validated_response_bytes": int(attempt["validated_response_bytes"]),
            "unique_content_count": int(unique["content_count"]),
            "unique_content_bytes": int(unique["content_bytes"]),
        },
        "by_host": by_host,
        "by_artifact_type": by_type,
    }


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
) -> dict[str, Any]:
    """Process a bounded batch while preserving resumability and host policy."""

    if limit < 1 or max_attempts < 1 or lease_seconds < 1:
        raise ValueError("limit, max_attempts, and lease_seconds must be positive")
    if throttle_seconds < 0:
        raise ValueError("throttle_seconds cannot be negative")
    unknown_types = set(artifact_types) - {"transcript", "presentation", "credit_rating", "recording"}
    if unknown_types:
        raise ValueError(f"Unsupported artifact type filter: {sorted(unknown_types)}")
    health = health_check(document_store)
    policy = policy or DownloadPolicy()
    cleaned_recording_partials = (
        cleanup_recording_temporary_files(snapshot_root)
        if "recording" in artifact_types
        else 0
    )
    worker = worker or f"{socket.gethostname()}:{os.getpid()}"
    scope_policy = _scope_policy(conn, scope_id)
    blocked_hosts: dict[str, str] = {}
    visited_hosts: set[str] = set()
    sessions: dict[str, Any] = {}
    last_request_at: dict[str, float] = {}
    outcomes: Counter[str] = Counter()
    processed = 0
    try:
        while processed < limit:
            job = claim_download_job(
                conn,
                worker=worker,
                now_utc=now(),
                lease_seconds=lease_seconds,
                scope_id=scope_id,
                excluded_hosts=tuple(set(blocked_hosts) | visited_hosts),
                artifact_types=artifact_types,
            )
            # Give every currently claimable publisher one turn before taking a
            # second job from the same host.  This prevents a run of stale URLs
            # from one publisher monopolising a batch and the monitor's recent
            # outcomes, while still draining a single-host queue normally.
            if job is None and visited_hosts:
                visited_hosts.clear()
                job = claim_download_job(
                    conn,
                    worker=worker,
                    now_utc=now(),
                    lease_seconds=lease_seconds,
                    scope_id=scope_id,
                    excluded_hosts=tuple(blocked_hosts),
                    artifact_types=artifact_types,
                )
            if job is None:
                break
            host = str(job["target_host"])
            visited_hosts.add(host)
            previous = last_request_at.get(host)
            if previous is not None:
                remaining = throttle_seconds - (monotonic() - previous)
                if remaining > 0:
                    sleep(remaining)
            session = sessions.setdefault(host, session_factory())
            started_at = now()
            try:
                if str(job["artifact_type"]) == "recording":
                    result = download_recording_to_store(
                        str(job["requested_url"]),
                        snapshot_root=snapshot_root,
                        policy=policy,
                        session=session,
                        monotonic=monotonic,
                        blocked_terms_hosts=blocked_terms_hosts(conn),
                    )
                else:
                    result = download_url(
                        str(job["requested_url"]),
                        artifact_type=str(job["artifact_type"]),
                        policy=policy,
                        session=session,
                        monotonic=monotonic,
                        blocked_terms_hosts=blocked_terms_hosts(conn),
                    )
                    if (
                        str(job["artifact_type"]) == "credit_rating"
                        and result.http_status == 200
                        and result.detected_media_type == "text/html"
                        and result.body
                    ):
                        genuine, _ = is_genuine_html_credit_report(
                            result.body,
                            final_url=result.final_url or str(job["requested_url"]),
                        )
                        if genuine:
                            result = replace(
                                result,
                                result_status="validated",
                                error_code=None,
                                error_message=None,
                            )
                last_request_at[host] = monotonic()
                if (
                    scope_policy.get("pdf_only")
                    and result.is_validated
                    # Genuine HTML remains the publisher-original content and
                    # immediately receives a linked PDF derivative below.
                    and result.detected_media_type not in {PDF, "text/html"}
                ):
                    result = replace(
                        result,
                        result_status="rejected_artifact_type",
                        error_code="scope_requires_pdf",
                        error_message="The P0 pilot stores PDF artifacts only",
                    )
                persisted = persist_download_result(
                    conn,
                    artifact_id=int(job["artifact_id"]),
                    artifact_source_ids=source_ids_for_job(
                        conn,
                        artifact_id=int(job["artifact_id"]),
                        requested_url=str(job["requested_url"]),
                    ),
                    result=result,
                    policy=policy,
                    started_at_utc=started_at,
                    fetched_at_utc=now(),
                    snapshot_root=snapshot_root,
                )
                if (
                    persisted.content_id is not None
                    and result.detected_media_type == "text/html"
                ):
                    render_and_persist_html_pdf(
                        conn,
                        artifact_id=int(job["artifact_id"]),
                        source_content_id=persisted.content_id,
                        source_url=result.final_url or str(job["requested_url"]),
                        snapshot_root=snapshot_root,
                    )
                attach_download_attempt(
                    conn,
                    job_id=int(job["download_job_id"]),
                    download_attempt_id=persisted.download_attempt_id,
                )
                if result.is_validated and persisted.content_id is not None:
                    for artifact_source_id in persisted.artifact_source_ids:
                        reconcile_recovery_for_source(
                            conn,
                            artifact_source_id=artifact_source_id,
                            content_id=persisted.content_id,
                            reconciled_at_utc=now(),
                        )
                if result.is_validated:
                    status = "SUCCEEDED"
                    retry_seconds = None
                elif _retryable(result) and int(job["attempt_count"]) < max_attempts:
                    status = "RETRYABLE_FAILURE"
                    retry_seconds = min(
                        24 * 60 * 60,
                        15 * 60 * 2 ** (int(job["attempt_count"]) - 1),
                    )
                else:
                    status = "TERMINAL_FAILURE"
                    retry_seconds = None
                finish_download_job(
                    conn,
                    job_id=int(job["download_job_id"]),
                    worker=worker,
                    status=status,
                    result_status=result.result_status,
                    error=_error_text(result),
                    now_utc=now(),
                    retry_seconds=retry_seconds,
                )
                if (result.http_status == 200 and result.detected_media_type == "text/html"
                        and result.body and persisted.artifact_source_ids):
                    from ..documents.landing_page import issuer_pdf_link
                    from .document_download_recovery import register_replacement_document_url
                    attachment = issuer_pdf_link(
                        result.body, page_url=result.final_url or str(job["requested_url"]),
                        artifact_type=str(job["artifact_type"]), blocked_hosts=blocked_terms_hosts(conn))
                    if attachment:
                        register_replacement_document_url(
                            conn, artifact_id=int(job["artifact_id"]),
                            replaces_artifact_source_id=persisted.artifact_source_ids[0],
                            replacement_url=attachment, reason_code="RESOLVER_UPDATED",
                            reason_detail="Unambiguous PDF attachment on the issuer document landing page",
                            evidence={"landing_url":result.final_url,"download_attempt_id":persisted.download_attempt_id},
                        )
                if status == "TERMINAL_FAILURE":
                    for artifact_source_id in persisted.artifact_source_ids:
                        reopen_recovery_after_download_failure(
                            conn,
                            artifact_source_id=artifact_source_id,
                            failed_at_utc=now(),
                        )
                if _blocks_host(result):
                    blocked_hosts[host] = result.result_status
                    _record_host_block(
                        conn,
                        host=host,
                        result=result,
                        scope_id=scope_id,
                        now_utc=now(),
                    )
                elif _transport_host_failure(result):
                    blocked_until = _record_transport_host_failure(
                        conn,
                        host=host,
                        result=result,
                        scope_id=scope_id,
                        now_utc=now(),
                    )
                    if blocked_until is not None:
                        blocked_hosts[host] = result.result_status
                elif result.http_status is not None:
                    _record_host_success(conn, host=host, result=result, now_utc=now())
                outcomes[result.result_status] += 1
            except BlockedTermsError:
                # Policy may change after claim. The downloader refuses before
                # HTTP and the lease is closed as a durable policy skip.
                finish_download_job(
                    conn,
                    job_id=int(job["download_job_id"]),
                    worker=worker,
                    status="SKIPPED_POLICY",
                    result_status=PUBLISHER_TERMS_RESULT,
                    error=PUBLISHER_TERMS_ERROR,
                    now_utc=now(),
                )
                for artifact_source_id in source_ids_for_job(
                    conn,
                    artifact_id=int(job["artifact_id"]),
                    requested_url=str(job["requested_url"]),
                ):
                    reopen_recovery_after_download_failure(
                        conn,
                        artifact_source_id=artifact_source_id,
                        failed_at_utc=now(),
                    )
                outcomes[PUBLISHER_TERMS_RESULT] += 1
            except Exception as exc:
                attempts = int(job["attempt_count"])
                status = (
                    "RETRYABLE_FAILURE"
                    if attempts < max_attempts
                    else "TERMINAL_FAILURE"
                )
                finish_download_job(
                    conn,
                    job_id=int(job["download_job_id"]),
                    worker=worker,
                    status=status,
                    result_status="WORKER_EXCEPTION",
                    error=f"{type(exc).__name__}: {exc}",
                    now_utc=now(),
                    retry_seconds=min(24 * 60 * 60, 15 * 60 * 2 ** (attempts - 1))
                    if status == "RETRYABLE_FAILURE"
                    else None,
                )
                if status == "TERMINAL_FAILURE":
                    for artifact_source_id in source_ids_for_job(
                        conn,
                        artifact_id=int(job["artifact_id"]),
                        requested_url=str(job["requested_url"]),
                    ):
                        reopen_recovery_after_download_failure(
                            conn,
                            artifact_source_id=artifact_source_id,
                            failed_at_utc=now(),
                        )
                outcomes["WORKER_EXCEPTION"] += 1
            processed += 1
    finally:
        for session in sessions.values():
            close = getattr(session, "close", None)
            if close is not None:
                close()

    return {
        "worker": worker,
        "scope_id": scope_id,
        "artifact_types": list(artifact_types),
        "processed_job_count": processed,
        "outcome_counts": dict(sorted(outcomes.items())),
        "blocked_hosts": blocked_hosts,
        "cleaned_recording_partial_count": cleaned_recording_partials,
        "queue": queue_counts(conn, scope_id=scope_id),
        "document_store": health.to_dict(),
    }
