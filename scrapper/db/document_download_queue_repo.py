"""SQLite repository for the DA-05 durable artifact download queue."""

from __future__ import annotations

import json
import sqlite3
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit

from ..core.access_policy import (
    BLOCKED_TERMS,
    PUBLISHER_TERMS_ERROR,
    PUBLISHER_TERMS_RESULT,
    host_matches_policy,
)
from .repo import utc_now
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)


RECOVERY_DOWNLOAD_SCOPE_ID = "da06-rating-recovery"


def _after(now_utc: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).isoformat()


@atomic_repository('documents')
def _scope(conn: sqlite3.Connection, scope_id: str) -> sqlite3.Row:
    row = execute(conn,
        "SELECT * FROM artifact_download_scopes WHERE scope_id=?", (scope_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown artifact download scope: {scope_id}")
    return row


@atomic_repository('documents')
def blocked_terms_hosts(conn: sqlite3.Connection) -> frozenset[str]:
    """Return hosts whose reviewed terms prohibit automated acquisition."""

    return frozenset(
        str(row["host"]).lower().rstrip(".")
        for row in execute(conn,
            "SELECT host FROM credit_rating_host_policy WHERE automation_status=?",
            (BLOCKED_TERMS,),
        )
    )


@atomic_repository('documents')
def _skip_blocked_terms_jobs(
    conn: sqlite3.Connection,
    *,
    now_utc: str,
    scope_id: str | None = None,
) -> int:
    """Move every actionable generic job covered by BLOCKED_TERMS to policy skip."""

    scope_filter = " AND queue.scope_id=?" if scope_id is not None else ""
    parameters: tuple[Any, ...] = (
        PUBLISHER_TERMS_RESULT,
        PUBLISHER_TERMS_ERROR,
        now_utc,
        BLOCKED_TERMS,
        *((scope_id,) if scope_id is not None else ()),
    )
    cursor = execute(conn,
        f"""UPDATE artifact_download_queue AS queue
               SET status='SKIPPED_POLICY', next_attempt_at_utc=NULL,
                   lease_owner=NULL, lease_expires_at_utc=NULL,
                   last_result_status=?, last_error=?, updated_at_utc=?
             WHERE queue.status IN ('PENDING', 'LEASED', 'RETRYABLE_FAILURE')
               AND EXISTS (
                 SELECT 1 FROM credit_rating_host_policy AS policy
                  WHERE policy.automation_status=?
                    AND (
                      queue.target_host=policy.host
                      OR queue.target_host LIKE '%.' || policy.host
                    )
               )
               {scope_filter}""",
        parameters,
    )
    return int(cursor.rowcount)


@atomic_repository('documents')
def _target_rows(
    conn: sqlite3.Connection,
    scope_id: str,
    artifact_ids: Iterable[int] | None = None,
) -> tuple[sqlite3.Row, ...]:
    scope = _scope(conn, scope_id)
    artifact_types = tuple(json.loads(scope["artifact_types_json"]))
    if not artifact_types:
        return ()
    placeholders = ",".join("?" for _ in artifact_types)
    selected_ids = tuple(sorted({int(value) for value in artifact_ids or ()}))
    if artifact_ids is not None and not selected_ids:
        return ()
    artifact_filter = ""
    parameters: tuple[Any, ...] = (scope_id, *artifact_types)
    if artifact_ids is not None:
        id_placeholders = ",".join("?" for _ in selected_ids)
        artifact_filter = f" AND artifact.artifact_id IN ({id_placeholders})"
        parameters = (*parameters, *selected_ids)
    return tuple(
        execute(conn,
            statement(conn, f"""SELECT artifact.artifact_id, artifact.artifact_type,
                       source.document_url,
                       CASE WHEN EXISTS (
                         SELECT 1 FROM artifact_contents AS content
                          WHERE content.artifact_id=artifact.artifact_id
                       ) AND NOT (
                         source.recovery_target_id IS NOT NULL
                         AND EXISTS (
                           SELECT 1 FROM artifact_recovery_targets AS recovery
                            WHERE recovery.recovery_target_id=source.recovery_target_id
                              AND recovery.status NOT IN ('RECOVERED','ALREADY_SATISFIED')
                         )
                       ) THEN 1 ELSE 0 END AS has_content
                  FROM artifact_download_scope_companies AS member
                  JOIN document_artifacts AS artifact
                    ON artifact.company_id=member.company_id
                  JOIN artifact_sources AS source USING (artifact_id)
                 WHERE member.scope_id=?
                   AND artifact.artifact_type IN ({placeholders})
                   {artifact_filter}
                   AND (
                     NOT EXISTS (
                       SELECT 1 FROM artifact_download_scope_artifacts AS any_selected
                        WHERE any_selected.scope_id=member.scope_id
                     )
                     OR EXISTS (
                       SELECT 1 FROM artifact_download_scope_artifacts AS selected
                        WHERE selected.scope_id=member.scope_id
                          AND selected.artifact_id=artifact.artifact_id
                     )
                   )
                   AND source.availability_status NOT IN (
                     'not_offered', 'not_applicable', 'source_not_covered'
                   )
                 GROUP BY artifact.artifact_id, artifact.artifact_type,
                          source.document_url
                 ORDER BY artifact.artifact_id, source.document_url""", f"""SELECT artifact.artifact_id, artifact.artifact_type,
                       source.document_url,
                       MIN(CASE WHEN EXISTS (
                         SELECT 1 FROM artifact_contents AS content
                          WHERE content.artifact_id=artifact.artifact_id
                       ) AND NOT (
                         source.recovery_target_id IS NOT NULL
                         AND EXISTS (
                           SELECT 1 FROM artifact_recovery_targets AS recovery
                            WHERE recovery.recovery_target_id=source.recovery_target_id
                              AND recovery.status NOT IN ('RECOVERED','ALREADY_SATISFIED')
                         )
                       ) THEN 1 ELSE 0 END) AS has_content
                  FROM artifact_download_scope_companies AS member
                  JOIN document_artifacts AS artifact
                    ON artifact.company_id=member.company_id
                  JOIN artifact_sources AS source USING (artifact_id)
                 WHERE member.scope_id=?
                   AND artifact.artifact_type IN ({placeholders})
                   {artifact_filter}
                   AND (
                     NOT EXISTS (
                       SELECT 1 FROM artifact_download_scope_artifacts AS any_selected
                        WHERE any_selected.scope_id=member.scope_id
                     )
                     OR EXISTS (
                       SELECT 1 FROM artifact_download_scope_artifacts AS selected
                        WHERE selected.scope_id=member.scope_id
                          AND selected.artifact_id=artifact.artifact_id
                     )
                   )
                   AND source.availability_status NOT IN (
                     'not_offered', 'not_applicable', 'source_not_covered'
                   )
                 GROUP BY artifact.artifact_id, artifact.artifact_type,
                          source.document_url
                 ORDER BY artifact.artifact_id, source.document_url"""),
            parameters,
        )
    )


@atomic_repository('documents')
def preview_download_queue(conn: sqlite3.Connection, *, scope_id: str) -> dict[str, Any]:
    targets = _target_rows(conn, scope_id)
    hosts: dict[str, int] = {}
    types: dict[str, int] = {}
    already = 0
    invalid = 0
    for row in targets:
        host = (urlsplit(row["document_url"]).hostname or "").lower()
        if not host:
            invalid += 1
        else:
            hosts[host] = hosts.get(host, 0) + 1
        artifact_type = str(row["artifact_type"])
        types[artifact_type] = types.get(artifact_type, 0) + 1
        already += int(row["has_content"])
    return {
        "scope_id": scope_id,
        "target_count": len(targets),
        "already_have_content_count": already,
        "invalid_url_count": invalid,
        "artifact_type_counts": dict(sorted(types.items())),
        "host_counts": dict(sorted(hosts.items())),
    }


@atomic_repository('documents')
def materialize_download_queue(
    conn: sqlite3.Connection,
    *,
    scope_id: str,
    priority: int = 100,
    created_at_utc: str | None = None,
    artifact_ids: Iterable[int] | None = None,
    include_queue_counts: bool = True,
    reuse_existing_jobs: bool = False,
) -> dict[str, Any]:
    """Idempotently materialize artifact/URL jobs for one explicit scope."""

    now = created_at_utc or utc_now()
    targets = _target_rows(conn, scope_id, artifact_ids)
    created = 0
    invalid_targets = 0
    blocked_targets = 0
    policy_hosts = blocked_terms_hosts(conn)
    with repository_transaction(conn):
        for row in targets:
            url = str(row["document_url"])
            if reuse_existing_jobs and execute(conn,
                'SELECT 1 FROM artifact_download_queue WHERE artifact_id=? AND requested_url=?',
                (row['artifact_id'],url)).fetchone():
                continue
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            valid_url = parsed.scheme in {"http", "https"} and bool(host)
            if not valid_url:
                status = "SKIPPED_POLICY"
                result_status = "INVALID_URL"
                error = "Only absolute HTTP(S) document URLs are eligible"
                invalid_targets += 1
            elif host_matches_policy(host, policy_hosts):
                status = "SKIPPED_POLICY"
                result_status = PUBLISHER_TERMS_RESULT
                error = PUBLISHER_TERMS_ERROR
                blocked_targets += 1
            elif bool(row["has_content"]) and not revision_scope(conn, scope_id, int(row["artifact_id"]), url):
                status = "SKIPPED_ALREADY_HAVE_CONTENT"
                result_status = None
                error = None
            else:
                status = "PENDING"
                result_status = None
                error = None
            cursor = execute(conn,
                statement(conn, """INSERT OR IGNORE INTO artifact_download_queue
                   (artifact_id, requested_url, target_host, priority, scope_id,
                    status, last_result_status, last_error,
                    created_at_utc, updated_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", """INSERT INTO artifact_download_queue
                   (artifact_id, requested_url, target_host, priority, scope_id,
                    status, last_result_status, last_error,
                    created_at_utc, updated_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING"""),
                (
                    int(row["artifact_id"]),
                    url,
                    host,
                    priority,
                    scope_id,
                    status,
                    result_status,
                    error,
                    now,
                    now,
                ),
            )
            created += cursor.rowcount
        _skip_blocked_terms_jobs(conn, now_utc=now, scope_id=scope_id)
        # A rerun after another URL acquired the artifact must not re-download it.
        execute(conn,
            statement(conn, """UPDATE artifact_download_queue
                  SET status='SKIPPED_ALREADY_HAVE_CONTENT',
                      next_attempt_at_utc=NULL, lease_owner=NULL,
                      lease_expires_at_utc=NULL,
                      last_result_status='CONTENT_ALREADY_PRESENT',
                      last_error=NULL, updated_at_utc=?
                WHERE scope_id=?
                  AND status IN ('PENDING', 'RETRYABLE_FAILURE')
                  AND NOT EXISTS (SELECT 1 FROM artifact_download_scopes rs
                    WHERE rs.scope_id=artifact_download_queue.scope_id
                    AND json_extract(rs.policy_json,'$.revision_artifact_id')=artifact_download_queue.artifact_id
                    AND json_extract(rs.policy_json,'$.revision_url')=artifact_download_queue.requested_url
                    AND json_extract(rs.policy_json,'$.revision_indicator') IS NOT NULL)
                  AND EXISTS (
                    SELECT 1 FROM artifact_contents AS content
                     WHERE content.artifact_id=artifact_download_queue.artifact_id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM artifact_sources AS source
                    JOIN artifact_recovery_targets AS recovery
                      ON recovery.recovery_target_id=source.recovery_target_id
                   WHERE source.artifact_id=artifact_download_queue.artifact_id
                     AND source.document_url=artifact_download_queue.requested_url
                     AND recovery.status NOT IN ('RECOVERED','ALREADY_SATISFIED')
                  )""", """UPDATE artifact_download_queue
                  SET status='SKIPPED_ALREADY_HAVE_CONTENT',
                      next_attempt_at_utc=NULL, lease_owner=NULL,
                      lease_expires_at_utc=NULL,
                      last_result_status='CONTENT_ALREADY_PRESENT',
                      last_error=NULL, updated_at_utc=?
                WHERE scope_id=?
                  AND status IN ('PENDING', 'RETRYABLE_FAILURE')
                  AND NOT EXISTS (SELECT 1 FROM artifact_download_scopes rs
                    WHERE rs.scope_id=artifact_download_queue.scope_id
                    AND CAST((CAST(rs.policy_json AS jsonb)->>'revision_artifact_id') AS bigint)=artifact_download_queue.artifact_id
                    AND (CAST(rs.policy_json AS jsonb)->>'revision_url')=artifact_download_queue.requested_url
                    AND (CAST(rs.policy_json AS jsonb)->>'revision_indicator') IS NOT NULL)
                  AND EXISTS (
                    SELECT 1 FROM artifact_contents AS content
                     WHERE content.artifact_id=artifact_download_queue.artifact_id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM artifact_sources AS source
                    JOIN artifact_recovery_targets AS recovery
                      ON recovery.recovery_target_id=source.recovery_target_id
                   WHERE source.artifact_id=artifact_download_queue.artifact_id
                     AND source.document_url=artifact_download_queue.requested_url
                     AND recovery.status NOT IN ('RECOVERED','ALREADY_SATISFIED')
                  )"""),
            (now, scope_id),
        )
    counts = (
        queue_counts(conn, scope_id=scope_id)
        if include_queue_counts
        else {"scope_id": scope_id, "total_job_count": 0, "status_counts": {}}
    )
    return {
        **counts,
        "created_job_count": created,
        "invalid_target_count": invalid_targets,
        "policy_blocked_target_count": blocked_targets,
    }


@atomic_repository('documents')
def claim_download_job(
    conn: sqlite3.Connection,
    *,
    worker: str,
    now_utc: str,
    lease_seconds: int = 600,
    scope_id: str | None = None,
    excluded_hosts: Iterable[str] = (),
    artifact_types: Iterable[str] = (),
    allowed_job_ids: Iterable[int] | None = None,
) -> sqlite3.Row | None:
    """Claim one row under ``BEGIN IMMEDIATE`` and recover stale leases."""

    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    selected_jobs = None if allowed_job_ids is None else tuple(dict.fromkeys(allowed_job_ids))
    if selected_jobs is not None:
        if not selected_jobs:
            return None
        if len(selected_jobs) > 100 or any(type(x) is not int or x <= 0 for x in selected_jobs):
            raise ValueError('Select at most 100 positive download job IDs')
    excluded = tuple(sorted({host.lower() for host in excluded_hosts}))
    selected_types = tuple(sorted({str(value) for value in artifact_types}))
    begin_immediate(conn, lock='documents')
    try:
        parameters: list[Any] = [now_utc]
        scope_filter = ""
        if scope_id is not None:
            _scope(conn, scope_id)
            scope_filter = " AND scope_id=?"
            parameters.append(scope_id)
        if selected_jobs is not None:
            scope_filter += f" AND download_job_id IN ({','.join('?' for _ in selected_jobs)})"
            parameters.extend(selected_jobs)
        # Re-evaluate policy on every claim so rows queued before a policy
        # review (including active/stale leases) cannot reach a worker.
        if selected_jobs is None:
            _skip_blocked_terms_jobs(conn, now_utc=now_utc, scope_id=scope_id)
        execute(conn,
            statement(conn, f"""UPDATE artifact_download_queue
                   SET status='RETRYABLE_FAILURE', lease_owner=NULL,
                       lease_expires_at_utc=NULL,
                       last_result_status='LEASE_EXPIRED',
                       last_error='Worker lease expired before completion',
                       updated_at_utc=?
                 WHERE status='LEASED' AND lease_expires_at_utc <= ?
                       {scope_filter}""", f"UPDATE artifact_download_queue\n                   SET status='RETRYABLE_FAILURE', lease_owner=NULL,\n                       lease_expires_at_utc=NULL,\n                       last_result_status='LEASE_EXPIRED',\n                       last_error='Worker lease expired before completion',\n                       updated_at_utc=?\n                 WHERE status='LEASED' AND CAST(lease_expires_at_utc AS timestamptz) <= CAST(? AS timestamptz)\n                       {scope_filter}"),
            (now_utc, now_utc, *parameters[1:]),
        )
        execute(conn,
            statement(conn, f"""UPDATE artifact_download_queue
                   SET status='SKIPPED_ALREADY_HAVE_CONTENT',
                       next_attempt_at_utc=NULL, lease_owner=NULL,
                       lease_expires_at_utc=NULL,
                       last_result_status='CONTENT_ALREADY_PRESENT',
                       last_error=NULL, updated_at_utc=?
                 WHERE status IN ('PENDING', 'RETRYABLE_FAILURE')
                       {scope_filter}
                   AND NOT EXISTS (SELECT 1 FROM artifact_download_scopes rs
                     WHERE rs.scope_id=artifact_download_queue.scope_id
                     AND json_extract(rs.policy_json,'$.revision_artifact_id')=artifact_download_queue.artifact_id
                     AND json_extract(rs.policy_json,'$.revision_url')=artifact_download_queue.requested_url
                    AND json_extract(rs.policy_json,'$.revision_indicator') IS NOT NULL)
                   AND EXISTS (
                     SELECT 1 FROM artifact_contents AS content
                      WHERE content.artifact_id=artifact_download_queue.artifact_id
                   )
                   AND NOT EXISTS (
                     SELECT 1 FROM artifact_sources AS source
                     JOIN artifact_recovery_targets AS recovery
                       ON recovery.recovery_target_id=source.recovery_target_id
                    WHERE source.artifact_id=artifact_download_queue.artifact_id
                      AND source.document_url=artifact_download_queue.requested_url
                      AND recovery.status NOT IN ('RECOVERED','ALREADY_SATISFIED')
                   )""", f"UPDATE artifact_download_queue\n                   SET status='SKIPPED_ALREADY_HAVE_CONTENT',\n                       next_attempt_at_utc=NULL, lease_owner=NULL,\n                       lease_expires_at_utc=NULL,\n                       last_result_status='CONTENT_ALREADY_PRESENT',\n                       last_error=NULL, updated_at_utc=?\n                 WHERE status IN ('PENDING', 'RETRYABLE_FAILURE')\n                       {scope_filter}\n                   AND NOT EXISTS (SELECT 1 FROM artifact_download_scopes rs\n                     WHERE rs.scope_id=artifact_download_queue.scope_id\n                     AND CAST((CAST(rs.policy_json AS jsonb)->>'revision_artifact_id') AS bigint)=artifact_download_queue.artifact_id\n                     AND (CAST(rs.policy_json AS jsonb)->>'revision_url')=artifact_download_queue.requested_url\n                    AND (CAST(rs.policy_json AS jsonb)->>'revision_indicator') IS NOT NULL)\n                   AND EXISTS (\n                     SELECT 1 FROM artifact_contents AS content\n                      WHERE content.artifact_id=artifact_download_queue.artifact_id\n                   )\n                   AND NOT EXISTS (\n                     SELECT 1 FROM artifact_sources AS source\n                     JOIN artifact_recovery_targets AS recovery\n                       ON recovery.recovery_target_id=source.recovery_target_id\n                    WHERE source.artifact_id=artifact_download_queue.artifact_id\n                      AND source.document_url=artifact_download_queue.requested_url\n                      AND recovery.status NOT IN ('RECOVERED','ALREADY_SATISFIED')\n                   )"),
            (now_utc, *parameters[1:]),
        )
        where = [
            """NOT EXISTS (SELECT 1 FROM screener_collection_scope s
                WHERE s.company_id=artifact.company_id AND s.eligibility='below_floor')""",
            "status IN ('PENDING', 'RETRYABLE_FAILURE')",
            "(next_attempt_at_utc IS NULL OR next_attempt_at_utc <= ?)",
            """NOT EXISTS (
                 SELECT 1 FROM artifact_download_queue AS active
                  WHERE active.target_host=queue.target_host
                    AND active.status='LEASED'
                    AND active.lease_expires_at_utc > ?
               )""",
            """NOT EXISTS (
                 SELECT 1 FROM artifact_download_host_health AS health
                  WHERE health.host=queue.target_host
                    AND health.blocked_until_utc IS NOT NULL
                    AND health.blocked_until_utc > ?
               )""",
            """NOT EXISTS (
                 SELECT 1 FROM credit_rating_host_policy AS policy
                  WHERE policy.automation_status='BLOCKED_TERMS'
                    AND (
                      queue.target_host=policy.host
                      OR queue.target_host LIKE '%.' || policy.host
                    )
               )""",
        ]
        # Once handed off, publisher rating links have one durable owner.
        from ..documents.rating_browser import browser_host_sql
        where.append(f"""NOT (artifact.artifact_type='credit_rating'
            AND {browser_host_sql('queue.target_host')}
            AND EXISTS(SELECT 1 FROM credit_rating_download_queue rating
              WHERE rating.artifact_id=queue.artifact_id
                AND rating.requested_url=queue.requested_url))""")
        select_parameters: list[Any] = [now_utc, now_utc, now_utc]
        if scope_id is not None:
            where.append("scope_id=?")
            select_parameters.append(scope_id)
        if selected_jobs is not None:
            where.append(f"queue.download_job_id IN ({','.join('?' for _ in selected_jobs)})")
            select_parameters.extend(selected_jobs)
        if excluded:
            where.append(f"target_host NOT IN ({','.join('?' for _ in excluded)})")
            select_parameters.extend(excluded)
        if selected_types:
            where.append(
                f"artifact.artifact_type IN ({','.join('?' for _ in selected_types)})"
            )
            select_parameters.extend(selected_types)
        row = execute(conn,
            statement(conn, f"""SELECT queue.*, artifact.artifact_type
                  FROM artifact_download_queue AS queue
                  JOIN document_artifacts AS artifact USING (artifact_id)
                 WHERE {' AND '.join(where)}
                 ORDER BY
                       queue.priority,
                       CASE
                         WHEN artifact.period_end IS NOT NULL
                           OR (
                             artifact.fiscal_year IS NOT NULL
                             AND artifact.fiscal_quarter IS NOT NULL
                           )
                         THEN 0 ELSE 1
                       END,
                       artifact.period_end DESC,
                       artifact.fiscal_year DESC,
                       artifact.fiscal_quarter DESC,
                       COALESCE(
                         artifact.event_date,
                         substr(artifact.published_at_utc, 1, 10),
                         substr(artifact.first_observed_at_utc, 1, 10),
                         ''
                       ) DESC,
                       queue.attempt_count,
                       queue.download_job_id
                 LIMIT 1""", f"""SELECT queue.*, artifact.artifact_type
                  FROM artifact_download_queue AS queue
                  JOIN document_artifacts AS artifact USING (artifact_id)
                 WHERE {' AND '.join(where)}
                 ORDER BY
                       queue.priority,
                       CASE
                         WHEN artifact.period_end IS NOT NULL
                           OR (
                             artifact.fiscal_year IS NOT NULL
                             AND artifact.fiscal_quarter IS NOT NULL
                           )
                         THEN 0 ELSE 1
                       END,
                       artifact.period_end DESC NULLS LAST,
                       artifact.fiscal_year DESC NULLS LAST,
                       artifact.fiscal_quarter DESC NULLS LAST,
                       COALESCE(
                         artifact.event_date,
                         substr(artifact.published_at_utc, 1, 10),
                         substr(artifact.first_observed_at_utc, 1, 10),
                         ''
                       ) DESC,
                       queue.attempt_count,
                       queue.download_job_id
                 LIMIT 1"""),
            select_parameters,
        ).fetchone()
        if row is None:
            commit(conn)
            return None
        job_id = int(row["download_job_id"])
        execute(conn,
            """UPDATE artifact_download_queue
                  SET status='LEASED', attempt_count=attempt_count+1,
                      lease_owner=?, lease_expires_at_utc=?, updated_at_utc=?, claim_token=?
                WHERE download_job_id=?""",
            (worker, _after(now_utc, lease_seconds), now_utc, secrets.token_hex(16), job_id),
        )
        claimed = execute(conn,
            """SELECT queue.*, artifact.artifact_type
                 FROM artifact_download_queue AS queue
                 JOIN document_artifacts AS artifact USING (artifact_id)
                WHERE download_job_id=?""",
            (job_id,),
        ).fetchone()
        commit(conn)
        return claimed
    except Exception:
        rollback(conn)
        raise


@atomic_repository('documents')
def finish_download_job(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    worker: str,
    claim_attempt_count: int,
    claim_token: str,
    status: str,
    result_status: str,
    error: str | None,
    now_utc: str,
    retry_seconds: int | None = None,
) -> None:
    if status not in {
        "SUCCEEDED", "RETRYABLE_FAILURE", "TERMINAL_FAILURE",
        "SKIPPED_ALREADY_HAVE_CONTENT", "SKIPPED_POLICY",
    }:
        raise ValueError(f"Invalid completed queue status: {status}")
    next_attempt = (
        _after(now_utc, retry_seconds)
        if status == "RETRYABLE_FAILURE" and retry_seconds is not None
        else None
    )
    with repository_transaction(conn):
        cursor = execute(conn,
            """UPDATE artifact_download_queue
                  SET status=?, next_attempt_at_utc=?, lease_owner=NULL,
                      lease_expires_at_utc=NULL, last_result_status=?,
                      last_error=?, updated_at_utc=?
                WHERE download_job_id=? AND status='LEASED' AND lease_owner=? AND attempt_count=? AND claim_token=?""",
            (
                status,
                next_attempt,
                result_status,
                error,
                now_utc,
                job_id,
                worker,
                claim_attempt_count,
                claim_token,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Download job lease is no longer owned by this worker")


@atomic_repository('documents')
def source_ids_for_job(
    conn: sqlite3.Connection, *, artifact_id: int, requested_url: str
) -> tuple[int, ...]:
    return tuple(
        int(row["artifact_source_id"])
        for row in execute(conn,
            """SELECT artifact_source_id FROM artifact_sources
                WHERE artifact_id=? AND document_url=?
                ORDER BY artifact_source_id""",
            (artifact_id, requested_url),
        )
    )


@atomic_repository('documents')
def attach_download_attempt(
    conn: sqlite3.Connection, *, job_id: int, download_attempt_id: int
) -> None:
    """Link the append-only D02 attempt to the queue claim that produced it."""

    with repository_transaction(conn):
        execute(conn,
            """INSERT INTO artifact_download_queue_attempts
               (download_job_id, download_attempt_id) VALUES (?, ?)""",
            (job_id, download_attempt_id),
        )


@atomic_repository('documents')
def queue_counts(conn: sqlite3.Connection, *, scope_id: str | None = None) -> dict[str, Any]:
    where = " WHERE scope_id=?" if scope_id is not None else ""
    parameters = (scope_id,) if scope_id is not None else ()
    counts = {
        str(row["status"]): int(row["count"])
        for row in execute(conn,
            f"SELECT status, COUNT(*) AS count FROM artifact_download_queue{where} GROUP BY status",
            parameters,
        )
    }
    return {
        "scope_id": scope_id,
        "total_job_count": sum(counts.values()),
        "status_counts": dict(sorted(counts.items())),
    }


@atomic_repository('documents')
def ensure_recovery_download_scope(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    company_id: int,
    created_at_utc: str | None = None,
    priority: int = 50,
) -> dict[str, Any]:
    """Add one accepted DA-06 artifact to the existing durable D02 queue."""

    now = created_at_utc or utc_now()
    artifact = execute(conn,
        """SELECT artifact_type FROM document_artifacts
            WHERE artifact_id=? AND company_id=?""",
        (artifact_id, company_id),
    ).fetchone()
    if artifact is None or artifact["artifact_type"] != "credit_rating":
        raise ValueError("Recovery queue accepts only the target credit_rating artifact")
    policy = json.dumps(
        {"pdf_only": False, "recording_fallback_only": True},
        sort_keys=True,
        separators=(",", ":"),
    )
    artifact_types = json.dumps(["credit_rating"], separators=(",", ":"))
    with repository_transaction(conn):
        execute(conn,
            statement(conn, """INSERT OR IGNORE INTO artifact_download_scopes
               (scope_id,scope_kind,description,artifact_types_json,policy_json,
                created_at_utc,updated_at_utc)
               VALUES (?,'custom','DA-06 official alternate-source recovery',?,?,?,?)""", """INSERT INTO artifact_download_scopes
               (scope_id,scope_kind,description,artifact_types_json,policy_json,
                created_at_utc,updated_at_utc)
               VALUES (?,'custom','DA-06 official alternate-source recovery',?,?,?,?) ON CONFLICT DO NOTHING"""),
            (RECOVERY_DOWNLOAD_SCOPE_ID, artifact_types, policy, now, now),
        )
        scope = execute(conn,
            """SELECT artifact_types_json,policy_json FROM artifact_download_scopes
                WHERE scope_id=?""",
            (RECOVERY_DOWNLOAD_SCOPE_ID,),
        ).fetchone()
        if (
            scope is None
            or scope["artifact_types_json"] != artifact_types
            or scope["policy_json"] != policy
        ):
            raise ValueError("DA-06 recovery scope has an incompatible existing definition")
        identity = execute(conn,
            """SELECT identifier_value FROM security_identifiers
                WHERE company_id=? AND identifier_type='NSE' AND is_current=1
                ORDER BY security_identifier_id LIMIT 1""",
            (company_id,),
        ).fetchone()
        label = str(identity[0]) if identity is not None else f"RECOVERY:{company_id}"
        execute(conn,
            statement(conn, """INSERT OR IGNORE INTO artifact_download_scope_companies
               (scope_id,company_id,symbol,outcome_label,archetype)
               VALUES (?,?,?,NULL,NULL)""", """INSERT INTO artifact_download_scope_companies
               (scope_id,company_id,symbol,outcome_label,archetype)
               VALUES (?,?,?,NULL,NULL) ON CONFLICT DO NOTHING"""),
            (RECOVERY_DOWNLOAD_SCOPE_ID, company_id, label),
        )
        execute(conn,
            statement(conn, """INSERT OR IGNORE INTO artifact_download_scope_artifacts
               (scope_id,artifact_id) VALUES (?,?)""", """INSERT INTO artifact_download_scope_artifacts
               (scope_id,artifact_id) VALUES (?,?) ON CONFLICT DO NOTHING"""),
            (RECOVERY_DOWNLOAD_SCOPE_ID, artifact_id),
        )
    return materialize_download_queue(
        conn,
        scope_id=RECOVERY_DOWNLOAD_SCOPE_ID,
        priority=priority,
        created_at_utc=now,
        artifact_ids=(artifact_id,),
    )


@atomic_repository('documents')
def revision_scope(conn, scope_id, artifact_id, url):
    """Narrow exception to content skipping, backed by immutable scope evidence."""
    row = execute(conn, 'SELECT policy_json FROM artifact_download_scopes WHERE scope_id=?', (scope_id,)).fetchone()
    policy = json.loads(row[0]) if row else {}
    return (policy.get('revision_artifact_id') == artifact_id and
            policy.get('revision_url') == url and bool(policy.get('revision_indicator')))
