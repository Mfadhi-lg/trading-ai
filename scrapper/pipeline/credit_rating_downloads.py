"""Durable credit-rating queue and resolver worker orchestration."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import requests

from ..config import DOCUMENT_CONTENT_SNAPSHOTS, DOCUMENT_STORE, THROTTLE_SECONDS
from ..db.document_download_repo import persist_download_result, store_response_blob
from ..db.html_document_rendering_repo import render_and_persist_html_pdf
from ..db.repo import utc_now
from ..documents.credit_rating_resolver import RESOLVER_VERSION, resolve_credit_rating
from ..documents.downloader import DownloadPolicy
from ..documents.store import assert_document_store_ready


def queue_credit_rating_downloads(
    conn: sqlite3.Connection,
    *,
    artifact_ids: Iterable[int] | None = None,
    created_at_utc: str | None = None,
    include_queue_counts: bool = True,
) -> dict[str, int]:
    """Materialize every promoted credit-rating URL, skipping acquired artifacts."""

    created_at_utc = created_at_utc or utc_now()
    selected_ids = tuple(sorted({int(value) for value in artifact_ids or ()}))
    if artifact_ids is not None and not selected_ids:
        return _queue_counts(
            conn,
            created_job_count=0,
            include_queue_counts=include_queue_counts,
        )
    artifact_filter = ""
    parameters: list[Any] = [created_at_utc, created_at_utc]
    if artifact_ids is not None:
        placeholders = ",".join("?" for _ in selected_ids)
        artifact_filter = f" AND artifact.artifact_id IN ({placeholders})"
        parameters.extend(selected_ids)
    before = conn.total_changes
    with conn:
        conn.execute(
            f"""INSERT OR IGNORE INTO credit_rating_download_queue
               (artifact_id, requested_url, priority, status,
                created_at_utc, updated_at_utc)
               SELECT artifact.artifact_id, source.document_url,
                      CASE WHEN artifact.published_at_utc IS NULL THEN 100 ELSE 50 END,
                      CASE WHEN EXISTS (
                        SELECT 1 FROM artifact_contents AS content
                         WHERE content.artifact_id=artifact.artifact_id
                      ) THEN 'SKIPPED_ALREADY_HAVE_CONTENT' ELSE 'PENDING' END,
                      ?, ?
                 FROM document_artifacts AS artifact
                 JOIN artifact_sources AS source USING (artifact_id)
                WHERE artifact.artifact_type='credit_rating'
                  AND source.availability_status NOT IN ('not_offered', 'not_applicable')
                  {artifact_filter}
                GROUP BY artifact.artifact_id, source.document_url""",
            parameters,
        )
        created = conn.total_changes - before
        for row in conn.execute(
            """SELECT download_job_id, requested_url
                 FROM credit_rating_download_queue WHERE target_host=''"""
        ).fetchall():
            host = (urlsplit(row["requested_url"]).hostname or "").lower()
            conn.execute(
                "UPDATE credit_rating_download_queue SET target_host=? WHERE download_job_id=?",
                (host, int(row["download_job_id"])),
            )
        conn.execute(
            """UPDATE credit_rating_download_queue
                  SET status='SUCCEEDED', last_result_status='CONTENT_ALREADY_PRESENT',
                      next_attempt_at_utc=NULL, lease_owner=NULL,
                      lease_expires_at_utc=NULL, updated_at_utc=?
                WHERE status <> 'SUCCEEDED' AND EXISTS (
                  SELECT 1 FROM artifact_contents AS content
                   WHERE content.artifact_id=credit_rating_download_queue.artifact_id
                )""",
            (created_at_utc,),
        )
        conn.execute(
            """UPDATE credit_rating_download_queue
                  SET status='SKIPPED_POLICY', next_attempt_at_utc=NULL,
                      lease_owner=NULL, lease_expires_at_utc=NULL,
                      last_result_status='PUBLISHER_TERMS_BLOCK_AUTOMATION',
                      last_error='Automated acquisition disabled by reviewed publisher terms',
                      updated_at_utc=?
                WHERE EXISTS (
                  SELECT 1 FROM credit_rating_host_policy AS policy
                   WHERE policy.automation_status='BLOCKED_TERMS'
                     AND (
                       credit_rating_download_queue.target_host=policy.host
                       OR credit_rating_download_queue.target_host LIKE '%.' || policy.host
                     )
                ) AND status IN (
                  'PENDING', 'LEASED', 'RETRYABLE_FAILURE', 'INTERACTION_REQUIRED'
                )""",
            (created_at_utc,),
        )
    return _queue_counts(
        conn,
        created_job_count=created,
        include_queue_counts=include_queue_counts,
    )


def _queue_counts(
    conn: sqlite3.Connection,
    *,
    created_job_count: int,
    include_queue_counts: bool = True,
) -> dict[str, int]:
    if not include_queue_counts:
        return {"created_job_count": created_job_count, "total_job_count": 0}
    counts = {
        row["status"]: int(row["count"])
        for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM credit_rating_download_queue GROUP BY status"
        )
    }
    return {
        "created_job_count": created_job_count,
        "total_job_count": sum(counts.values()),
        **counts,
    }


def _terms_blocked_artifact_ids(
    conn: sqlite3.Connection, artifact_ids: Iterable[int]
) -> tuple[int, ...]:
    """Select the just-queued artifacts a reviewed publisher blocks us from fetching."""

    selected = tuple(sorted({int(value) for value in artifact_ids}))
    if not selected:
        return ()
    placeholders = ",".join("?" for _ in selected)
    return tuple(
        int(row[0])
        for row in conn.execute(
            f"""SELECT DISTINCT artifact_id FROM credit_rating_download_queue
                 WHERE artifact_id IN ({placeholders})
                   AND status='SKIPPED_POLICY'
                   AND last_result_status='PUBLISHER_TERMS_BLOCK_AUTOMATION'""",
            selected,
        )
    )


def enqueue_credit_ratings_for_accepted_parse(
    conn: sqlite3.Connection,
    *,
    parse_id: int,
    include_queue_counts: bool = True,
) -> dict[str, Any]:
    """Promote and enqueue only links from the just-accepted company parse."""

    from ..db.document_recovery_repo import build_recovery_targets
    from .promote_document_links import promote_document_links

    promotion = promote_document_links(
        conn, document_type="credit_rating", parse_id=parse_id
    )
    queue = queue_credit_rating_downloads(
        conn,
        artifact_ids=promotion["artifact_ids"],
        include_queue_counts=include_queue_counts,
    )
    # A link we may never fetch directly is only worth queueing if the search for
    # an official alternate starts in the same breath, so build the DA-06 targets
    # here rather than leaving them to a separate backfill pass.
    recovery = build_recovery_targets(
        conn, artifact_ids=_terms_blocked_artifact_ids(conn, promotion["artifact_ids"])
    )
    return {"promotion": promotion, "queue": queue, "recovery": recovery}


def _iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _claim_job(
    conn: sqlite3.Connection,
    *,
    worker: str,
    now: str,
    lease_seconds: int = 600,
    allowed_hosts: tuple[str, ...] = (),
) -> sqlite3.Row | None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        # A dead worker's lease is safely reclaimable.
        conn.execute(
            """UPDATE credit_rating_download_queue
                  SET status='RETRYABLE_FAILURE', lease_owner=NULL,
                      lease_expires_at_utc=NULL, updated_at_utc=?
                WHERE status='LEASED' AND lease_expires_at_utc < ?""",
            (now, now),
        )
        host_filter = ""
        parameters: list[Any] = [now]
        if allowed_hosts:
            placeholders = ",".join("?" for _ in allowed_hosts)
            host_filter = f" AND target_host IN ({placeholders})"
            parameters.extend(allowed_hosts)
        parameters.append(now)
        row = conn.execute(
            f"""SELECT * FROM credit_rating_download_queue
                WHERE status IN ('PENDING', 'RETRYABLE_FAILURE')
                  AND NOT EXISTS (SELECT 1 FROM screener_collection_scope sc
                    JOIN document_artifacts da ON da.company_id=sc.company_id
                    WHERE da.artifact_id=credit_rating_download_queue.artifact_id
                      AND sc.eligibility='below_floor')
                  AND (next_attempt_at_utc IS NULL OR next_attempt_at_utc <= ?)
                  {host_filter}
                  AND NOT EXISTS (
                    SELECT 1 FROM credit_rating_host_policy AS policy
                     WHERE policy.automation_status='BLOCKED_TERMS'
                       AND (
                         credit_rating_download_queue.target_host=policy.host
                         OR credit_rating_download_queue.target_host LIKE '%.' || policy.host
                       )
                  )
                  AND (
                    SELECT COUNT(*) FROM credit_rating_download_queue AS active
                     WHERE active.target_host=credit_rating_download_queue.target_host
                       AND active.status='LEASED'
                  ) < COALESCE(
                    (SELECT policy.max_concurrency
                       FROM credit_rating_host_policy AS policy
                      WHERE policy.host=credit_rating_download_queue.target_host),
                    1
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM credit_rating_host_health AS health
                     WHERE health.host=credit_rating_download_queue.target_host
                       AND health.blocked_until_utc IS NOT NULL
                       AND health.blocked_until_utc > ?
                )
                ORDER BY priority, attempt_count, download_job_id LIMIT 1""",
            parameters,
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            """UPDATE credit_rating_download_queue
                  SET status='LEASED', attempt_count=attempt_count+1,
                      lease_owner=?, lease_expires_at_utc=?, updated_at_utc=?
                WHERE download_job_id=?""",
            (worker, _iso_after(lease_seconds), now, int(row["download_job_id"])),
        )
        conn.commit()
        return conn.execute(
            "SELECT * FROM credit_rating_download_queue WHERE download_job_id=?",
            (int(row["download_job_id"]),),
        ).fetchone()
    except Exception:
        conn.rollback()
        raise


def _finish_job(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    status: str,
    result_status: str,
    error: str | None,
    retry_seconds: int | None = None,
) -> None:
    now = utc_now()
    with conn:
        conn.execute(
            """UPDATE credit_rating_download_queue
                  SET status=?, next_attempt_at_utc=?, lease_owner=NULL,
                      lease_expires_at_utc=NULL, last_result_status=?,
                      last_error=?, updated_at_utc=?
                WHERE download_job_id=?""",
            (
                status,
                _iso_after(retry_seconds) if retry_seconds is not None else None,
                result_status,
                error,
                now,
                job_id,
            ),
        )


def _source_ids(conn: sqlite3.Connection, artifact_id: int, url: str) -> tuple[int, ...]:
    return tuple(
        int(row["artifact_source_id"])
        for row in conn.execute(
            """SELECT artifact_source_id FROM artifact_sources
                WHERE artifact_id=? AND document_url=? ORDER BY artifact_source_id""",
            (artifact_id, url),
        )
    )


def _host_block_status(resolution: Any) -> int | None:
    for step in resolution.steps:
        if step.result.http_status in {401, 403, 429}:
            return int(step.result.http_status)
        if bool(step.evidence.get("hard_block_marker")):
            return int(step.result.http_status or 403)
    return None


def _record_host_block(
    conn: sqlite3.Connection,
    *,
    host: str,
    http_status: int,
    result_status: str,
) -> None:
    now = utc_now()
    blocked_until = _iso_after(24 * 60 * 60 if http_status in {401, 403} else 6 * 60 * 60)
    with conn:
        conn.execute(
            """INSERT INTO credit_rating_host_health
               (host, blocked_until_utc, last_http_status, consecutive_blocks,
                last_result_status, updated_at_utc)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT(host) DO UPDATE SET
                 blocked_until_utc=excluded.blocked_until_utc,
                 last_http_status=excluded.last_http_status,
                 consecutive_blocks=credit_rating_host_health.consecutive_blocks+1,
                 last_result_status=excluded.last_result_status,
                 updated_at_utc=excluded.updated_at_utc""",
            (host, blocked_until, http_status, result_status, now),
        )


def _persist_resolution(
    conn: sqlite3.Connection,
    *,
    job: sqlite3.Row,
    resolution: Any,
    started_at_utc: str,
    snapshot_root: Path,
    policy: DownloadPolicy,
) -> int | None:
    with conn:
        cursor = conn.execute(
            """INSERT INTO credit_rating_resolution_attempts
               (download_job_id, artifact_id, original_url, started_at_utc,
                completed_at_utc, resolution_status, resolver_version,
                final_url, error_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(job["download_job_id"]), int(job["artifact_id"]),
                job["requested_url"], started_at_utc, utc_now(), resolution.status,
                RESOLVER_VERSION,
                resolution.final_result.final_url if resolution.final_result else None,
                json.dumps({"message": resolution.error} if resolution.error else {}),
            ),
        )
        attempt_id = int(cursor.lastrowid)
        for position, step in enumerate(resolution.steps):
            blob_id = None
            if step.result.body is not None:
                blob_id, _ = store_response_blob(
                    conn,
                    body=step.result.body,
                    media_type=step.result.detected_media_type or step.result.declared_media_type,
                    observed_at_utc=utc_now(),
                    snapshot_root=snapshot_root,
                )
            conn.execute(
                """INSERT INTO credit_rating_resolution_steps
                   (resolution_attempt_id, step_position, step_type, requested_url,
                    final_url, request_method, http_status, declared_media_type,
                    detected_media_type, blob_id, selected, evidence_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    attempt_id, position, step.step_type, step.request_url,
                    step.result.final_url, step.method, step.result.http_status,
                    step.result.declared_media_type, step.result.detected_media_type,
                    blob_id, int(step.selected),
                    json.dumps(dict(step.evidence), sort_keys=True, separators=(",", ":")),
                ),
            )
    if resolution.final_result is None:
        return None
    persisted = persist_download_result(
        conn,
        artifact_id=int(job["artifact_id"]),
        artifact_source_ids=_source_ids(conn, int(job["artifact_id"]), job["requested_url"]),
        result=resolution.final_result,
        policy=policy,
        started_at_utc=started_at_utc,
        fetched_at_utc=utc_now(),
        snapshot_root=snapshot_root,
    )
    if (
        persisted.content_id is not None
        and resolution.final_result.detected_media_type == "text/html"
    ):
        render_and_persist_html_pdf(
            conn,
            artifact_id=int(job["artifact_id"]),
            source_content_id=persisted.content_id,
            source_url=(
                resolution.final_result.final_url
                or resolution.final_result.requested_url
            ),
            snapshot_root=snapshot_root,
        )
    return persisted.content_id


def run_credit_rating_download_worker(
    conn: sqlite3.Connection,
    *,
    limit: int = 25,
    policy: DownloadPolicy | None = None,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
    document_store: Path = DOCUMENT_STORE,
    worker: str | None = None,
    throttle_seconds: float = THROTTLE_SECONDS,
    allowed_hosts: Iterable[str] = (),
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Claim and resolve a bounded batch; another invocation resumes safely."""

    if limit < 1:
        raise ValueError("limit must be positive")
    health = assert_document_store_ready(document_store)
    policy = policy or DownloadPolicy()
    worker = worker or f"{socket.gethostname()}:{os.getpid()}"
    hosts = tuple(sorted({host.strip().lower() for host in allowed_hosts if host.strip()}))
    if throttle_seconds < 0:
        raise ValueError("throttle_seconds cannot be negative")
    if hosts:
        rows = conn.execute(
            f"""SELECT host, min_delay_seconds FROM credit_rating_host_policy
                 WHERE host IN ({','.join('?' for _ in hosts)})""",
            hosts,
        ).fetchall()
        minimum = max((float(row["min_delay_seconds"]) for row in rows), default=0.0)
        if throttle_seconds < minimum:
            raise ValueError(
                f"throttle_seconds={throttle_seconds:g} is below reviewed host minimum {minimum:g}"
            )
    outcomes: Counter[str] = Counter()
    processed = 0
    for position in range(limit):
        job = _claim_job(
            conn, worker=worker, now=utc_now(), allowed_hosts=hosts
        )
        if job is None:
            break
        started = utc_now()
        try:
            with requests.Session() as session:
                resolution = resolve_credit_rating(
                    job["requested_url"], policy=policy, session=session
                )
            content_id = _persist_resolution(
                conn, job=job, resolution=resolution, started_at_utc=started,
                snapshot_root=snapshot_root, policy=policy,
            )
            blocked_status = _host_block_status(resolution)
            if content_id is not None:
                queue_status = "SUCCEEDED"
                retry = None
            elif resolution.status == "SKIPPED_POLICY":
                queue_status = "SKIPPED_POLICY"
                retry = None
            elif blocked_status is not None:
                _record_host_block(
                    conn, host=job["target_host"], http_status=blocked_status,
                    result_status=resolution.status,
                )
                queue_status = "RETRYABLE_FAILURE"
                retry = 24 * 60 * 60 if blocked_status in {401, 403} else 6 * 60 * 60
            elif resolution.status == "INTERACTION_REQUIRED":
                queue_status = "INTERACTION_REQUIRED"
                retry = None
            elif resolution.status == "TERMINAL_INVALID_CONTENT":
                queue_status = "TERMINAL_FAILURE"
                retry = None
            else:
                queue_status = "RETRYABLE_FAILURE"
                retry = min(86400, 900 * (2 ** min(int(job["attempt_count"]), 6)))
            _finish_job(
                conn, job_id=int(job["download_job_id"]), status=queue_status,
                result_status=resolution.status, error=resolution.error,
                retry_seconds=retry,
            )
            outcomes[resolution.status] += 1
        except Exception as exc:
            retry = min(86400, 900 * (2 ** min(int(job["attempt_count"]), 6)))
            _finish_job(
                conn, job_id=int(job["download_job_id"]), status="RETRYABLE_FAILURE",
                result_status="WORKER_EXCEPTION", error=f"{type(exc).__name__}: {exc}",
                retry_seconds=retry,
            )
            outcomes["WORKER_EXCEPTION"] += 1
        processed += 1
        if position + 1 < limit and throttle_seconds > 0:
            sleep(throttle_seconds)
    queue_counts = {
        row["status"]: int(row["count"])
        for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM credit_rating_download_queue GROUP BY status"
        )
    }
    return {
        "worker": worker,
        "allowed_hosts": list(hosts),
        "processed_job_count": processed,
        "outcome_counts": dict(sorted(outcomes.items())),
        "queue_counts": queue_counts,
        "document_store": health.to_dict(),
    }
