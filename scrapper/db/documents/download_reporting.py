"""Read-only policy and accounting queries for generic download jobs."""
from __future__ import annotations
import json
import sqlite3
from typing import Any
from ..document_download_queue_repo import queue_counts
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)

DOWNLOAD_QUEUE_REPORT_SCHEMA_VERSION = "1.0.0"

@atomic_repository('documents')
def _scope_policy(conn: sqlite3.Connection, scope_id: str | None) -> dict[str, Any]:
    if scope_id is None:
        return {}
    row = execute(conn,
        "SELECT policy_json FROM artifact_download_scopes WHERE scope_id=?",
        (scope_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown artifact download scope: {scope_id}")
    return dict(json.loads(row["policy_json"]))


@atomic_repository('documents')
def build_download_queue_report(
    conn: sqlite3.Connection, *, scope_id: str | None,
    queue_counts=queue_counts, schema_version=DOWNLOAD_QUEUE_REPORT_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Return queue, host, type, and actual byte-size accounting."""

    scope_filter = " WHERE queue.scope_id=?" if scope_id is not None else ""
    parameters = (scope_id,) if scope_id is not None else ()
    attempt = execute(conn,
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
    unique = execute(conn,
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
        for row in execute(conn,
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
        for row in execute(conn,
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
        "schema_version": schema_version,
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
