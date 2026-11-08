"""DA-06 target leases and immutable official-source attempt history.

Retains existing transaction, expiry and retry accounting contracts. Per-call
hooks preserve compatibility patch points without importing the old facade.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from ....documents.recovery import RecoveryTarget
from .targets import load_recovery_target

RECOVERY_SOURCE_GROUPS = ("nse", "bse", "issuer-ir")

def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _recovery_source_group(source_type: str, *, _hooks: Any = None) -> str | None:
    if source_type.startswith("NSE_"):
        return "nse"
    if source_type.startswith("BSE_"):
        return "bse"
    if source_type == "ISSUER_IR_ATTACHMENT":
        return "issuer-ir"
    return None


def _remaining_source_status(conn: sqlite3.Connection, target_id: int, *, _hooks: Any = None) -> str:
    RECOVERY_SOURCE_GROUPS = getattr(_hooks, "RECOVERY_SOURCE_GROUPS", globals()["RECOVERY_SOURCE_GROUPS"])
    completed_sources = {
        str(row["source_type"])
        for row in conn.execute(
            """SELECT DISTINCT source_type FROM artifact_recovery_attempts
                WHERE recovery_target_id=? AND status IN (
                  'CANDIDATE_FOUND','AMBIGUOUS','NO_OFFICIAL_SOURCE_FOUND',
                  'TERMINAL_FAILURE','INTERACTION_REQUIRED')""",
            (target_id,),
        )
    }
    return (
        "NO_OFFICIAL_SOURCE_FOUND"
        if set(RECOVERY_SOURCE_GROUPS).issubset(completed_sources)
        else "PENDING"
    )


def _after(value: str, seconds: int, *, _hooks: Any = None) -> str:
    datetime = getattr(_hooks, "datetime", globals()["datetime"])
    timedelta = getattr(_hooks, "timedelta", globals()["timedelta"])
    timezone = getattr(_hooks, "timezone", globals()["timezone"])
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).isoformat()


def claim_recovery_target(
    conn: sqlite3.Connection,
    *,
    source: str,
    worker: str,
    now_utc: str,
    lease_seconds: int = 600,
    _hooks: Any = None,
) -> tuple[RecoveryTarget, int] | None:
    """Lease one target not yet settled for this official source group."""
    RECOVERY_SOURCE_GROUPS = getattr(_hooks, "RECOVERY_SOURCE_GROUPS", globals()["RECOVERY_SOURCE_GROUPS"])
    _after = getattr(_hooks, "_after", globals()["_after"])
    load_recovery_target = getattr(_hooks, "load_recovery_target", globals()["load_recovery_target"])

    if source not in RECOVERY_SOURCE_GROUPS:
        raise ValueError(f"Unsupported recovery source: {source}")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """UPDATE artifact_recovery_targets
                  SET status='RETRYABLE_FAILURE',lease_owner=NULL,
                      lease_expires_at_utc=NULL,updated_at_utc=?
                WHERE status='SEARCHING' AND lease_expires_at_utc<=?""",
            (now_utc, now_utc),
        )
        row = conn.execute(
            """SELECT target.recovery_target_id
                 FROM artifact_recovery_targets AS target
                WHERE target.status IN (
                  'PENDING','AMBIGUOUS','RETRYABLE_FAILURE','NO_OFFICIAL_SOURCE_FOUND'
                )
                  AND (target.next_attempt_at_utc IS NULL
                       OR target.next_attempt_at_utc<=?)
                  AND NOT EXISTS (
                    SELECT 1 FROM artifact_recovery_attempts AS attempt
                     WHERE attempt.recovery_target_id=target.recovery_target_id
                       AND attempt.source_type=?
                       AND attempt.status IN (
                         'CANDIDATE_FOUND','AMBIGUOUS','NO_OFFICIAL_SOURCE_FOUND',
                         'TERMINAL_FAILURE','INTERACTION_REQUIRED'
                       )
                  )
                ORDER BY target.attempt_count,target.recovery_target_id LIMIT 1""",
            (now_utc, source),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        target_id = int(row["recovery_target_id"])
        attempt_number = int(
            conn.execute(
                """SELECT COALESCE(MAX(attempt_number),0)+1
                    FROM artifact_recovery_attempts
                    WHERE recovery_target_id=? AND source_type=?""",
                (target_id, source),
            ).fetchone()[0]
        )
        cursor = conn.execute(
            """INSERT INTO artifact_recovery_attempts
               (recovery_target_id,source_type,attempt_number,status,started_at_utc)
               VALUES (?,?,?,'SEARCHING',?)""",
            (target_id, source, attempt_number, now_utc),
        )
        attempt_id = int(cursor.lastrowid)
        conn.execute(
            """UPDATE artifact_recovery_targets
                  SET status='SEARCHING',attempt_count=attempt_count+1,
                      lease_owner=?,lease_expires_at_utc=?,updated_at_utc=?
                WHERE recovery_target_id=?""",
            (worker, _after(now_utc, lease_seconds), now_utc, target_id),
        )
        conn.commit()
        return load_recovery_target(conn, target_id), attempt_id
    except Exception:
        conn.rollback()
        raise


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
    _hooks: Any = None,
) -> None:
    RECOVERY_SOURCE_GROUPS = getattr(_hooks, "RECOVERY_SOURCE_GROUPS", globals()["RECOVERY_SOURCE_GROUPS"])
    _after = getattr(_hooks, "_after", globals()["_after"])
    _json = getattr(_hooks, "_json", globals()["_json"])
    allowed = {
        "CANDIDATE_FOUND", "AMBIGUOUS", "NO_OFFICIAL_SOURCE_FOUND",
        "RETRYABLE_FAILURE", "TERMINAL_FAILURE", "INTERACTION_REQUIRED",
    }
    if status not in allowed:
        raise ValueError(f"Unsupported recovery attempt status: {status}")
    attempt = conn.execute(
        """SELECT recovery_target_id,source_type FROM artifact_recovery_attempts
            WHERE recovery_attempt_id=?""",
        (recovery_attempt_id,),
    ).fetchone()
    if attempt is None:
        raise ValueError(f"Unknown recovery attempt: {recovery_attempt_id}")
    target_id = int(attempt["recovery_target_id"])
    with conn:
        cursor = conn.execute(
            """UPDATE artifact_recovery_attempts
                  SET status=?,completed_at_utc=?,result_evidence_json=?,error_json=?
                WHERE recovery_attempt_id=? AND status='SEARCHING'""",
            (
                status,
                completed_at_utc,
                _json(result_evidence or {}),
                _json(error or {}),
                recovery_attempt_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Recovery attempt is no longer active")
        target = conn.execute(
            "SELECT status,lease_owner FROM artifact_recovery_targets WHERE recovery_target_id=?",
            (target_id,),
        ).fetchone()
        if target is None or target["lease_owner"] != worker:
            raise RuntimeError("Recovery target lease is no longer owned by this worker")
        if target["status"] == "RECOVERED":
            target_status = "RECOVERED"
        elif status == "CANDIDATE_FOUND":
            target_status = "CANDIDATE_FOUND"
        elif status == "AMBIGUOUS":
            target_status = "AMBIGUOUS"
        elif status == "RETRYABLE_FAILURE":
            target_status = "RETRYABLE_FAILURE"
        else:
            completed_sources = {
                str(row["source_type"])
                for row in conn.execute(
                    """SELECT DISTINCT source_type FROM artifact_recovery_attempts
                        WHERE recovery_target_id=? AND status IN (
                          'CANDIDATE_FOUND','AMBIGUOUS','NO_OFFICIAL_SOURCE_FOUND',
                          'TERMINAL_FAILURE','INTERACTION_REQUIRED')""",
                    (target_id,),
                )
            }
            all_sources_done = set(RECOVERY_SOURCE_GROUPS).issubset(completed_sources)
            if not all_sources_done:
                target_status = "PENDING"
            elif status == "INTERACTION_REQUIRED":
                target_status = "LICENSE_OR_MANUAL_REQUIRED"
            elif status == "TERMINAL_FAILURE":
                target_status = "TERMINAL_FAILURE"
            else:
                target_status = "NO_OFFICIAL_SOURCE_FOUND"
        conn.execute(
            """UPDATE artifact_recovery_targets
                  SET status=?,next_attempt_at_utc=?,lease_owner=NULL,
                      lease_expires_at_utc=NULL,updated_at_utc=?
                WHERE recovery_target_id=? AND lease_owner=?""",
            (
                target_status,
                _after(completed_at_utc, retry_seconds)
                if status == "RETRYABLE_FAILURE" and retry_seconds
                else None,
                completed_at_utc,
                target_id,
                worker,
            ),
        )
