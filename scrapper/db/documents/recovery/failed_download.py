"""Persistence for audited failed download repairs; existing transaction boundaries."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from ....config import (
    DOCUMENT_CONTENT_SNAPSHOTS,
    DOCUMENT_MAX_RESPONSE_BYTES,
)
from ....core.access_policy import (
    PUBLISHER_TERMS_ERROR,
    PUBLISHER_TERMS_RESULT,
    is_blocked_terms_target,
)
from ...document_download_queue_repo import blocked_terms_hosts
from ...document_artifact_repo import register_artifact_content
from ...document_download_repo import store_response_blob
from ...repo import utc_now
from ....documents.downloader import ALLOWED_MEDIA_BY_ARTIFACT, detect_media_type


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


from ....documents.recovery.failed_download import _normalized_http_url

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
    """Requeue explicit settled jobs; policy skips and covered artifacts fail closed."""
    QUEUE_TABLES = getattr(_hooks, "QUEUE_TABLES", globals()["QUEUE_TABLES"])
    RECOVERY_REASON_CODES = getattr(_hooks, "RECOVERY_REASON_CODES", globals()["RECOVERY_REASON_CODES"])
    REQUEUEABLE = getattr(_hooks, "REQUEUEABLE", globals()["REQUEUEABLE"])
    utc_now = getattr(_hooks, "utc_now", globals()["utc_now"])

    if queue_kind not in QUEUE_TABLES:
        raise ValueError(f"Unknown queue kind: {queue_kind}")
    if reason_code not in RECOVERY_REASON_CODES:
        raise ValueError(f"Unknown recovery reason: {reason_code}")
    selected = tuple(sorted({int(value) for value in job_ids}))
    if not selected:
        raise ValueError("At least one explicit download job id is required")
    table = QUEUE_TABLES[queue_kind]
    placeholders = ",".join("?" for _ in selected)
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE download_job_id IN ({placeholders}) ORDER BY download_job_id",
        selected,
    ).fetchall()
    if len(rows) != len(selected):
        found = {int(row["download_job_id"]) for row in rows}
        raise ValueError(f"Unknown {queue_kind} download jobs: {sorted(set(selected) - found)}")
    for row in rows:
        status = str(row["status"])
        unresolved_alternate = queue_kind == "generic" and conn.execute(
            """SELECT 1
                 FROM artifact_sources AS source
                 JOIN artifact_recovery_targets AS recovery
                   ON recovery.recovery_target_id=source.recovery_target_id
                WHERE source.artifact_id=? AND source.document_url=?
                  AND source.automation_status='AVAILABLE'
                  AND recovery.status NOT IN ('RECOVERED','ALREADY_SATISFIED')
                LIMIT 1""",
            (int(row["artifact_id"]), row["requested_url"]),
        ).fetchone() is not None
        if status not in REQUEUEABLE[queue_kind] and not (
            status == "SKIPPED_ALREADY_HAVE_CONTENT" and unresolved_alternate
        ):
            raise ValueError(
                f"Job {row['download_job_id']} status {status} is not safely requeueable"
            )
        covered = conn.execute(
            "SELECT 1 FROM artifact_contents WHERE artifact_id=? LIMIT 1",
            (int(row["artifact_id"]),),
        ).fetchone()
        if covered is not None and not unresolved_alternate:
            raise ValueError(
                f"Job {row['download_job_id']} artifact already has validated content"
            )
    preview = [
        {
            "download_job_id": int(row["download_job_id"]),
            "artifact_id": int(row["artifact_id"]),
            "previous_status": row["status"],
            "previous_result_status": row["last_result_status"],
            "requested_url": row["requested_url"],
        }
        for row in rows
    ]
    if dry_run:
        return {
            "dry_run": True,
            "queue_kind": queue_kind,
            "reason_code": reason_code,
            "requeued_job_count": 0,
            "jobs": preview,
        }

    applied_at = applied_at_utc or utc_now()
    with conn:
        for row in rows:
            conn.execute(
                f"""UPDATE {table}
                       SET status='PENDING', attempt_count=0,
                           next_attempt_at_utc=NULL, lease_owner=NULL,
                           lease_expires_at_utc=NULL,
                           last_result_status='RECOVERY_REQUEUED',
                           last_error=NULL, updated_at_utc=?
                     WHERE download_job_id=?""",
                (applied_at, int(row["download_job_id"])),
            )
            conn.execute(
                """INSERT INTO document_download_recovery_actions
                   (artifact_id, queue_kind, download_job_id, action_type,
                    reason_code, reason_detail, previous_status,
                    previous_result_status, previous_error, old_url,
                    evidence_json, applied_at_utc)
                   VALUES (?, ?, ?, 'REQUEUE', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(row["artifact_id"]), queue_kind,
                    int(row["download_job_id"]), reason_code, reason_detail,
                    row["status"], row["last_result_status"], row["last_error"],
                    row["requested_url"],
                    json.dumps(
                        {"previous_attempt_count": int(row["attempt_count"])},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    applied_at,
                ),
            )
    return {
        "dry_run": False,
        "queue_kind": queue_kind,
        "reason_code": reason_code,
        "requeued_job_count": len(rows),
        "jobs": preview,
    }


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
    """Attach a replacement URL to the same artifact and queue it independently."""
    PUBLISHER_TERMS_ERROR = getattr(_hooks, "PUBLISHER_TERMS_ERROR", globals()["PUBLISHER_TERMS_ERROR"])
    PUBLISHER_TERMS_RESULT = getattr(_hooks, "PUBLISHER_TERMS_RESULT", globals()["PUBLISHER_TERMS_RESULT"])
    RECOVERY_REASON_CODES = getattr(_hooks, "RECOVERY_REASON_CODES", globals()["RECOVERY_REASON_CODES"])
    _normalized_http_url = getattr(_hooks, "_normalized_http_url", globals()["_normalized_http_url"])
    blocked_terms_hosts = getattr(_hooks, "blocked_terms_hosts", globals()["blocked_terms_hosts"])
    is_blocked_terms_target = getattr(_hooks, "is_blocked_terms_target", globals()["is_blocked_terms_target"])
    utc_now = getattr(_hooks, "utc_now", globals()["utc_now"])

    if reason_code not in RECOVERY_REASON_CODES:
        raise ValueError(f"Unknown recovery reason: {reason_code}")
    normalized = _normalized_http_url(replacement_url)
    source = conn.execute(
        """SELECT source.*, artifact.artifact_type
             FROM artifact_sources AS source
             JOIN document_artifacts AS artifact USING (artifact_id)
            WHERE source.artifact_source_id=? AND source.artifact_id=?""",
        (replaces_artifact_source_id, artifact_id),
    ).fetchone()
    if source is None:
        raise ValueError("Replacement source does not belong to the logical artifact")
    if normalized == source["document_url"]:
        raise ValueError("Replacement URL is identical to the failed source URL")
    scopes = tuple(
        str(row["scope_id"])
        for row in conn.execute(
            """SELECT DISTINCT scope_id FROM artifact_download_queue
                WHERE artifact_id=? ORDER BY scope_id""",
            (artifact_id,),
        )
    )
    queue_kinds = (["generic"] if scopes else []) + (
        ["credit_rating"] if source["artifact_type"] == "credit_rating" else []
    )
    if not queue_kinds:
        raise ValueError("Artifact is not part of a durable download queue")
    preview = {
        "dry_run": dry_run,
        "artifact_id": artifact_id,
        "replaces_artifact_source_id": replaces_artifact_source_id,
        "old_url": source["document_url"],
        "new_url": normalized,
        "queue_kinds": queue_kinds,
        "scope_ids": list(scopes),
        "reason_code": reason_code,
    }
    if dry_run:
        return preview

    applied_at = applied_at_utc or utc_now()
    inherited = json.loads(source["classification_evidence_json"] or "{}")
    inherited["recovery"] = {
        "reason_code": reason_code,
        "reason_detail": reason_detail,
        "replaces_artifact_source_id": replaces_artifact_source_id,
        "operator_evidence": evidence or {},
        "registered_at_utc": applied_at,
    }
    host = (urlsplit(normalized).hostname or "").lower()
    policy_blocked = is_blocked_terms_target(
        normalized, additional_hosts=blocked_terms_hosts(conn)
    )
    created_jobs: list[dict[str, Any]] = []
    with conn:
        position = int(
            conn.execute(
                """SELECT COALESCE(MAX(source_position), -1) + 1
                     FROM artifact_sources WHERE discovery_page_id=?""",
                (int(source["discovery_page_id"]),),
            ).fetchone()[0]
        )
        conn.execute(
            """INSERT OR IGNORE INTO artifact_sources
               (artifact_id, discovery_page_id, discovered_from_source,
                discovery_page_url, document_url, publisher_claim,
                exchange_announcement_id, source_position, source_title_raw,
                classification_evidence_json, is_primary_source,
                first_observed_at_utc, last_checked_at_utc, availability_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available')""",
            (
                artifact_id, int(source["discovery_page_id"]),
                source["discovered_from_source"], source["discovery_page_url"],
                normalized, source["publisher_claim"], source["exchange_announcement_id"],
                position, source["source_title_raw"],
                json.dumps(inherited, sort_keys=True, separators=(",", ":")),
                int(source["is_primary_source"]), applied_at, applied_at,
            ),
        )
        replacement = conn.execute(
            """SELECT artifact_source_id FROM artifact_sources
                WHERE artifact_id=? AND discovered_from_source=?
                  AND discovery_page_url=? AND document_url=?""",
            (
                artifact_id, source["discovered_from_source"],
                source["discovery_page_url"], normalized,
            ),
        ).fetchone()
        if replacement is None:
            raise RuntimeError("Failed to register replacement artifact source")
        replacement_source_id = int(replacement["artifact_source_id"])
        conn.execute(
            """UPDATE artifact_sources
                  SET availability_status='inaccessible', last_checked_at_utc=?
                WHERE artifact_source_id=?""",
            (applied_at, replaces_artifact_source_id),
        )
        has_content = conn.execute(
            "SELECT 1 FROM artifact_contents WHERE artifact_id=? LIMIT 1",
            (artifact_id,),
        ).fetchone() is not None
        for scope in scopes:
            status = (
                "SKIPPED_ALREADY_HAVE_CONTENT" if has_content
                else "SKIPPED_POLICY" if policy_blocked else "PENDING"
            )
            result = PUBLISHER_TERMS_RESULT if policy_blocked else None
            error = PUBLISHER_TERMS_ERROR if policy_blocked else None
            conn.execute(
                """INSERT OR IGNORE INTO artifact_download_queue
                   (artifact_id, requested_url, target_host, priority, scope_id,
                    status, last_result_status, last_error,
                    created_at_utc, updated_at_utc)
                   VALUES (?, ?, ?, 50, ?, ?, ?, ?, ?, ?)""",
                (
                    artifact_id, normalized, host, scope, status, result, error,
                    applied_at, applied_at,
                ),
            )
            job = conn.execute(
                """SELECT download_job_id FROM artifact_download_queue
                    WHERE artifact_id=? AND requested_url=? AND scope_id=?""",
                (artifact_id, normalized, scope),
            ).fetchone()
            created_jobs.append(
                {"queue_kind": "generic", "download_job_id": int(job[0]), "scope_id": scope}
            )
        if source["artifact_type"] == "credit_rating":
            status = (
                "SKIPPED_ALREADY_HAVE_CONTENT" if has_content
                else "SKIPPED_POLICY" if policy_blocked else "PENDING"
            )
            conn.execute(
                """INSERT OR IGNORE INTO credit_rating_download_queue
                   (artifact_id, requested_url, target_host, priority, status,
                    last_result_status, last_error, created_at_utc, updated_at_utc)
                   VALUES (?, ?, ?, 50, ?, ?, ?, ?, ?)""",
                (
                    artifact_id, normalized, host, status,
                    PUBLISHER_TERMS_RESULT if policy_blocked else None,
                    PUBLISHER_TERMS_ERROR if policy_blocked else None,
                    applied_at, applied_at,
                ),
            )
            job = conn.execute(
                """SELECT download_job_id FROM credit_rating_download_queue
                    WHERE artifact_id=? AND requested_url=?""",
                (artifact_id, normalized),
            ).fetchone()
            created_jobs.append(
                {"queue_kind": "credit_rating", "download_job_id": int(job[0]), "scope_id": None}
            )
        for job in created_jobs:
            conn.execute(
                """INSERT INTO document_download_recovery_actions
                   (artifact_id, queue_kind, download_job_id, action_type,
                    reason_code, reason_detail, replaced_artifact_source_id,
                    replacement_artifact_source_id, old_url, new_url,
                    evidence_json, applied_at_utc)
                   VALUES (?, ?, ?, 'REGISTER_REPLACEMENT_URL', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    artifact_id, job["queue_kind"], job["download_job_id"],
                    reason_code, reason_detail, replaces_artifact_source_id,
                    replacement_source_id, source["document_url"], normalized,
                    json.dumps(evidence or {}, sort_keys=True, separators=(",", ":")),
                    applied_at,
                ),
            )
    return {
        **preview,
        "replacement_artifact_source_id": replacement_source_id,
        "created_jobs": created_jobs,
        "policy_blocked": policy_blocked,
    }


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
    """Validate and content-address an operator-acquired publisher document."""
    ALLOWED_MEDIA_BY_ARTIFACT = getattr(_hooks, "ALLOWED_MEDIA_BY_ARTIFACT", globals()["ALLOWED_MEDIA_BY_ARTIFACT"])
    detect_media_type = getattr(_hooks, "detect_media_type", globals()["detect_media_type"])
    register_artifact_content = getattr(_hooks, "register_artifact_content", globals()["register_artifact_content"])
    store_response_blob = getattr(_hooks, "store_response_blob", globals()["store_response_blob"])
    utc_now = getattr(_hooks, "utc_now", globals()["utc_now"])

    source = conn.execute(
        """SELECT source.*, artifact.artifact_type
             FROM artifact_sources AS source
             JOIN document_artifacts AS artifact USING (artifact_id)
            WHERE source.artifact_source_id=? AND source.artifact_id=?""",
        (artifact_source_id, artifact_id),
    ).fetchone()
    if source is None:
        raise ValueError("Manual import source does not belong to the logical artifact")
    if not reason_detail.strip():
        raise ValueError("Manual import requires a non-empty reason detail")
    if not path.is_file():
        raise ValueError(f"Manual import path is not a file: {path}")
    byte_size = path.stat().st_size
    if byte_size <= 0:
        raise ValueError("Manual import file is empty")
    if byte_size > max_bytes:
        raise ValueError(f"Manual import exceeds the configured {max_bytes}-byte limit")
    body = path.read_bytes()
    detected = detect_media_type(body)
    allowed = ALLOWED_MEDIA_BY_ARTIFACT[str(source["artifact_type"])]
    if detected not in allowed:
        raise ValueError(
            f"Detected media type {detected!r} is not valid for "
            f"{source['artifact_type']!r}"
        )
    imported_at = imported_at_utc or utc_now()
    with conn:
        blob_id, digest = store_response_blob(
            conn,
            body=body,
            media_type=detected,
            observed_at_utc=imported_at,
            snapshot_root=snapshot_root,
        )
        content_id = register_artifact_content(
            conn,
            artifact_id=artifact_id,
            blob_id=blob_id,
            media_type=detected,
            is_official=official,
            created_at_utc=imported_at,
        )
        conn.execute(
            """INSERT OR IGNORE INTO manual_document_imports
               (artifact_id, artifact_source_id, content_id, input_filename,
                input_sha256, byte_size, detected_media_type, official_assertion,
                reason_detail, evidence_json, imported_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artifact_id, artifact_source_id, content_id, path.name,
                digest, len(body), detected, int(official), reason_detail.strip(),
                json.dumps(evidence or {}, sort_keys=True, separators=(",", ":")),
                imported_at,
            ),
        )
        manual_import_id = int(
            conn.execute(
                """SELECT manual_import_id FROM manual_document_imports
                    WHERE artifact_id=? AND artifact_source_id=? AND content_id=?""",
                (artifact_id, artifact_source_id, content_id),
            ).fetchone()[0]
        )
        conn.execute(
            """UPDATE document_artifacts SET acquisition_status='downloaded'
                WHERE artifact_id=? AND acquisition_status <> 'validated'""",
            (artifact_id,),
        )
        conn.execute(
            """UPDATE artifact_download_queue
                  SET status='SKIPPED_ALREADY_HAVE_CONTENT',
                      next_attempt_at_utc=NULL, lease_owner=NULL,
                      lease_expires_at_utc=NULL,
                      last_result_status='CONTENT_IMPORTED_MANUALLY',
                      last_error=NULL, updated_at_utc=?
                WHERE artifact_id=? AND status <> 'SUCCEEDED'""",
            (imported_at, artifact_id),
        )
        conn.execute(
            """UPDATE credit_rating_download_queue
                  SET status='SUCCEEDED', next_attempt_at_utc=NULL,
                      lease_owner=NULL, lease_expires_at_utc=NULL,
                      last_result_status='CONTENT_IMPORTED_MANUALLY',
                      last_error=NULL, updated_at_utc=?
                WHERE artifact_id=? AND status <> 'SUCCEEDED'""",
            (imported_at, artifact_id),
        )
    return {
        "manual_import_id": manual_import_id,
        "artifact_id": artifact_id,
        "artifact_source_id": artifact_source_id,
        "content_id": content_id,
        "content_sha256": digest,
        "byte_size": len(body),
        "detected_media_type": detected,
        "official": official,
        "snapshot_root": str(snapshot_root.resolve()),
    }
