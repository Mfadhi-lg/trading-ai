"""Lease-based DA-05 worker for the general artifact download queue."""

from __future__ import annotations

from ...db.document_work_claims import OwnershipLost
from ...operations.workers.renewal import queue_renewal

from scrapper.core.attempts import claim_attempt, retry_remaining, retry_delay, BROWSER_MAX_INVOCATIONS

import os
import socket
import sqlite3
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import requests

from ...core.access_policy import (
    BlockedTermsError,
    PUBLISHER_TERMS_ERROR,
    PUBLISHER_TERMS_RESULT,
)
from ...config import (
    DOCUMENT_CONTENT_SNAPSHOTS,
    DOCUMENT_STORE,
    THROTTLE_SECONDS,
)
from ...db.document_download_queue_repo import (
    attach_download_attempt,
    blocked_terms_hosts,
    claim_download_job,
    finish_download_job,
    queue_counts,
    source_ids_for_job,
)
from ...db.document_recovery_repo import (
    reconcile_recovery_for_source,
    reopen_recovery_after_download_failure,
)
from ...db.documents.download import persist_download_result
from ...db.repo import utc_now
from ...documents.downloader import (
    PDF,
    DownloadPolicy,
    cleanup_recording_temporary_files,
    download_recording_to_store,
    download_url,
)
from ...documents.credit_rating_resolver import is_genuine_html_credit_report
from ...db.documents.rendering import render_and_persist_html_pdf
from ...documents.store import assert_document_store_ready




from ...db.documents.download_reporting import _scope_policy
from ...documents.download.retry import _retryable, _blocks_host, _transport_host_failure, _error_text
from ...db.documents.host_health import _record_host_block, _record_host_success, _record_transport_host_failure
from .download_reconciliation import reconcile_download_success, reconcile_download_outcome

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
    _hooks: Any = None,
    shared_permits: bool = False,
    eligible_scopes: tuple[str, ...] = (),
    allowed_job_ids: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """Process a bounded batch while preserving resumability and host policy."""

    from ...operations.workers.host_requests import HostSession, AdministrativeDefer
    from ...db.host_request_defer import defer_job, require_shared_schema
    from datetime import datetime

    # Read legacy patch points per invocation; never mutate owner globals.
    _bindings = globals() if _hooks is None else vars(_hooks)
    BlockedTermsError = _bindings["BlockedTermsError"]
    Counter = _bindings["Counter"]
    PDF = _bindings["PDF"]
    PUBLISHER_TERMS_ERROR = _bindings["PUBLISHER_TERMS_ERROR"]
    PUBLISHER_TERMS_RESULT = _bindings["PUBLISHER_TERMS_RESULT"]
    _blocks_host = _bindings["_blocks_host"]
    _error_text = _bindings["_error_text"]
    _record_host_block = _bindings["_record_host_block"]
    _record_host_success = _bindings["_record_host_success"]
    _record_transport_host_failure = _bindings["_record_transport_host_failure"]
    _retryable = _bindings["_retryable"]
    _scope_policy = _bindings["_scope_policy"]
    _transport_host_failure = _bindings["_transport_host_failure"]
    attach_download_attempt = _bindings["attach_download_attempt"]
    blocked_terms_hosts = _bindings["blocked_terms_hosts"]
    claim_download_job = _bindings["claim_download_job"]
    cleanup_recording_temporary_files = _bindings["cleanup_recording_temporary_files"]
    download_recording_to_store = _bindings["download_recording_to_store"]
    download_url = _bindings["download_url"]
    finish_download_job = _bindings["finish_download_job"]
    is_genuine_html_credit_report = _bindings["is_genuine_html_credit_report"]
    os = _bindings["os"]
    persist_download_result = _bindings["persist_download_result"]
    queue_counts = _bindings["queue_counts"]
    reconcile_recovery_for_source = _bindings["reconcile_recovery_for_source"]
    render_and_persist_html_pdf = _bindings["render_and_persist_html_pdf"]
    reopen_recovery_after_download_failure = _bindings["reopen_recovery_after_download_failure"]
    replace = _bindings["replace"]
    socket = _bindings["socket"]
    source_ids_for_job = _bindings["source_ids_for_job"]

    if limit < 1 or max_attempts < 1 or lease_seconds < 1:
        raise ValueError("limit, max_attempts, and lease_seconds must be positive")
    if throttle_seconds < 0:
        raise ValueError("throttle_seconds cannot be negative")
    unknown_types = set(artifact_types) - {"transcript", "presentation", "credit_rating", "recording"}
    if unknown_types:
        raise ValueError(f"Unsupported artifact type filter: {sorted(unknown_types)}")
    scope_claims = None
    if eligible_scopes:
        if scope_id is not None:
            raise ValueError("eligible scopes cannot be combined with scope_id")
        from ...operations.workers.generic_scopes import ScopeClaims
        scope_claims = ScopeClaims(conn, eligible_scopes, claim_download_job,
                                   stopped=lifecycle.is_set if lifecycle is not None else lambda: False)
        claim_download_job = scope_claims
        scope_policies = {scope: _scope_policy(conn, scope) for scope in scope_claims.scopes}
        shared_permits = True
    if shared_permits:
        require_shared_schema(conn)
    health = health_check(document_store)
    if lifecycle is not None and not lifecycle.is_set():
        from scrapper.operations.workers.health import bind_role_health
        bind_role_health(lifecycle, conn, "generic", dependencies={"document_store": lambda: bool(health_check(document_store))})
    policy = policy or DownloadPolicy()
    cleaned_recording_partials = (
        cleanup_recording_temporary_files(snapshot_root)
        if "recording" in artifact_types
        else 0
    )
    worker = worker or f"{socket.gethostname()}:{os.getpid()}"
    scope_policy = {} if scope_claims is not None else _scope_policy(conn, scope_id)
    blocked_hosts: dict[str, str] = {}
    visited_hosts: set[str] = set()
    sessions: dict[str, Any] = {}
    last_request_at: dict[str, float] = {}
    outcomes: Counter[str] = Counter()
    processed = 0
    try:
        while processed < limit:
            if lifecycle is not None:
                lifecycle.beat("READY")
                if lifecycle.is_set():
                    break
            job = claim_download_job(
                conn,
                worker=worker,
                now_utc=now(),
                lease_seconds=lease_seconds,
                scope_id=scope_id,
                excluded_hosts=tuple(set(blocked_hosts) | visited_hosts),
                artifact_types=artifact_types,
                **({'allowed_job_ids': allowed_job_ids} if allowed_job_ids is not None else {}),
            )
            # Give every currently claimable publisher one turn before taking a
            # second job from the same host.  This prevents a run of stale URLs
            # from one publisher monopolising a batch and the monitor's recent
            # outcomes, while still draining a single-host queue normally.
            if job is None and visited_hosts and (lifecycle is None or not lifecycle.is_set()):
                visited_hosts.clear()
                job = claim_download_job(
                    conn,
                    worker=worker,
                    now_utc=now(),
                    lease_seconds=lease_seconds,
                    scope_id=scope_id,
                    excluded_hosts=tuple(blocked_hosts),
                    artifact_types=artifact_types,
                    **({'allowed_job_ids': allowed_job_ids} if allowed_job_ids is not None else {}),
                )
            if job is None:
                if lifecycle is not None:
                    lifecycle.beat("IDLE")
                break
            if scope_claims is not None:
                scope_policy = scope_policies[str(job["scope_id"])]
            if lifecycle is not None:
                lifecycle.claimed(int(job["download_job_id"]))
            with queue_renewal(conn, "generic", job, seconds=lease_seconds, now=now) as renewal:
                host = str(job["target_host"])
                visited_hosts.add(host)
                previous = last_request_at.get(host)
                if previous is not None:
                    remaining = throttle_seconds - (monotonic() - previous)
                    if remaining > 0:
                        if lifecycle is None:
                            sleep(remaining)
                        else:
                            lifecycle.wait(remaining, state="THROTTLED", interruptible=False)
                shared_session = None
                if shared_permits:
                    shared_session = HostSession(conn, mode="generic", owner=worker,
                        policy=policy, check=lambda: renewal.check(conn),
                        now=lambda: datetime.fromisoformat(now()).timestamp(), sleep=sleep)
                    session = shared_session
                else:
                    session = sessions.setdefault(host, session_factory())
                if lifecycle is not None:
                    lifecycle.beat("WORKING")
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
                            and result.result_status == "rejected_unexpected_html"
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
                    renewal.check(conn)
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
                    reconcile_download_success(
                        conn, persisted=persisted, result=result, now=now,
                        reconcile_recovery_for_source=reconcile_recovery_for_source,
                    )
                    if result.is_validated:
                        status = "SUCCEEDED"
                        retry_seconds = None
                    elif _retryable(result) and retry_remaining(claim_attempt(job, "generic"), max_attempts):
                        status = "RETRYABLE_FAILURE"
                        retry_seconds = retry_delay(claim_attempt(job, "generic"))
                    else:
                        status = "TERMINAL_FAILURE"
                        retry_seconds = None
                    renewal.check(conn)
                    finish_download_job(
                        conn,
                        job_id=int(job["download_job_id"]),
                        worker=worker,
                        claim_attempt_count=claim_attempt(job, "generic"),
                        claim_token=job["claim_token"],
                        status=status,
                        result_status=result.result_status,
                        error=_error_text(result),
                        now_utc=now(),
                        retry_seconds=retry_seconds,
                    )
                    reconcile_download_outcome(
                        conn, job=job, persisted=persisted, result=result, status=status, now=now,
                        blocked_terms_hosts=blocked_terms_hosts,
                        reopen_recovery_after_download_failure=reopen_recovery_after_download_failure,
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
                except AdministrativeDefer as exc:
                    renewal.check(conn)
                    if shared_session.dispatched:
                        persisted = persist_download_result(conn,
                            artifact_id=int(job["artifact_id"]),
                            artifact_source_ids=source_ids_for_job(conn,
                                artifact_id=int(job["artifact_id"]), requested_url=str(job["requested_url"])),
                            result=replace(shared_session.deferred_result(str(job["requested_url"]), exc.reason),
                                result_status="rejected_http_status"),
                            policy=policy, started_at_utc=started_at, fetched_at_utc=now(), snapshot_root=snapshot_root)
                        attach_download_attempt(conn, job_id=int(job["download_job_id"]),
                            download_attempt_id=persisted.download_attempt_id)
                    defer_job(conn, "generic", job, now=now(), retry_at=exc.retry_at,
                        dispatched=bool(shared_session.dispatched), reason=exc.reason)
                    blocked_hosts[host] = exc.reason
                    outcomes[exc.reason] += 1
                except BlockedTermsError:
                    # Policy may change after claim. The downloader refuses before
                    # HTTP and the lease is closed as a durable policy skip.
                    renewal.check(conn)
                    if shared_session is not None and not shared_session.dispatched:
                        defer_job(conn, "generic", job, now=now(), retry_at=0,
                            dispatched=False, reason="PUBLISHER_POLICY_BLOCKED")
                    else:
                        finish_download_job(
                            conn,
                            job_id=int(job["download_job_id"]),
                            worker=worker,
                            claim_attempt_count=claim_attempt(job, "generic"),
                            claim_token=job["claim_token"],
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
                except OwnershipLost:
                    raise
                except Exception as exc:
                    if isinstance(exc, sqlite3.DatabaseError) or (isinstance(exc, OSError) and not isinstance(exc, requests.RequestException)):
                        raise
                    attempts = claim_attempt(job, "generic")
                    status = (
                        "RETRYABLE_FAILURE"
                        if retry_remaining(attempts, max_attempts)
                        else "TERMINAL_FAILURE"
                    )
                    renewal.check(conn)
                    finish_download_job(
                        conn,
                        job_id=int(job["download_job_id"]),
                        worker=worker,
                        claim_attempt_count=claim_attempt(job, "generic"),
                        claim_token=job["claim_token"],
                        status=status,
                        result_status="WORKER_EXCEPTION",
                        error=f"{type(exc).__name__}: {exc}",
                        now_utc=now(),
                        retry_seconds=retry_delay(attempts)
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
                finally:
                    if shared_session is not None:
                        shared_session.close()
            processed += 1
            if lifecycle is not None:
                lifecycle.finished()
    finally:
        for session in sessions.values():
            close = getattr(session, "close", None)
            if close is not None:
                close()

    payload = {
        "worker": worker,
        "scope_id": scope_id,
        "artifact_types": list(artifact_types),
        "processed_job_count": processed,
        "outcome_counts": dict(sorted(outcomes.items())),
        "blocked_hosts": blocked_hosts,
        "cleaned_recording_partial_count": cleaned_recording_partials,
        "queue": ({scope: queue_counts(conn, scope_id=scope) for scope in scope_claims.scopes}
                  if scope_claims is not None else queue_counts(conn, scope_id=scope_id)),
        "document_store": health.to_dict(),
    }

    if scope_claims is not None:
        payload["eligible_scopes"] = list(scope_claims.scopes)
        payload["scope_claim_counts"] = scope_claims.counts
    return payload
