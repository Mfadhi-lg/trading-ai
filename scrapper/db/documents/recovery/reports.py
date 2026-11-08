"""Read-only DA-06 residual/accounting report projection."""
from __future__ import annotations
import sqlite3
from collections import Counter
from typing import Any
from ...repo import utc_now

RECOVERY_REPORT_SCHEMA_VERSION = "1.0.0"

def _source_bucket(source_type: str | None) -> str:
    if source_type == "NSE_EXCHANGE_ATTACHMENT":
        return "NSE"
    if source_type == "BSE_EXCHANGE_ATTACHMENT":
        return "BSE"
    if source_type == "ISSUER_IR_ATTACHMENT":
        return "issuer_ir"
    if source_type in {"NSE_DEBT_DOCUMENT", "BSE_DEBT_DOCUMENT"}:
        return "debt_issue_document"
    return "other"


def build_alternate_source_recovery_report(
    conn: sqlite3.Connection, *, generated_at_utc: str | None = None, _hooks: Any = None
) -> dict[str, Any]:
    utc_now = getattr(_hooks, "utc_now", globals()["utc_now"])
    _source_bucket = getattr(_hooks, "_source_bucket", globals()["_source_bucket"])
    RECOVERY_REPORT_SCHEMA_VERSION = getattr(_hooks, "RECOVERY_REPORT_SCHEMA_VERSION", globals()["RECOVERY_REPORT_SCHEMA_VERSION"])
    generated = generated_at_utc or utc_now()
    targets = conn.execute(
        """SELECT target.*,candidate.source_type,candidate.content_class
             FROM artifact_recovery_targets AS target
             LEFT JOIN artifact_recovery_candidates AS candidate
               ON candidate.candidate_id=target.resolved_candidate_id
            ORDER BY target.recovery_target_id"""
    ).fetchall()
    quarantine = conn.execute(
        "SELECT * FROM artifact_recovery_quarantine ORDER BY artifact_id"
    ).fetchall()
    status_counts = Counter(str(row["status"]) for row in targets)
    recovered = [row for row in targets if row["status"] == "RECOVERED"]
    full_by_source: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    for row in recovered:
        content_class = str(row["content_class"] or "UNKNOWN_RATING_DOCUMENT")
        class_counts[content_class] += 1
        if content_class in {
            "FULL_RATING_RATIONALE", "ISSUE_DOCUMENT_EMBEDDED_RATIONALE"
        }:
            full_by_source[_source_bucket(row["source_type"])] += 1

    already = status_counts["ALREADY_SATISFIED"]
    ambiguous = status_counts["AMBIGUOUS"]
    no_official = status_counts["NO_OFFICIAL_SOURCE_FOUND"]
    manual = (
        status_counts["LICENSE_OR_MANUAL_REQUIRED"]
        + status_counts["TERMINAL_FAILURE"]
        + len(quarantine)
    )
    in_progress_statuses = {
        "PENDING", "SEARCHING", "CANDIDATE_FOUND", "RETRYABLE_FAILURE"
    }
    in_progress = sum(status_counts[status] for status in in_progress_statuses)
    total = len(targets) + len(quarantine)
    accounted = already + len(recovered) + ambiguous + no_official + manual + in_progress
    if accounted != total:
        raise RuntimeError(
            f"Alternate-source recovery accounting mismatch: {accounted} != {total}"
        )
    residual = [
        row for row in targets
        if row["status"] not in {"RECOVERED", "ALREADY_SATISFIED"}
    ]
    residual_by_originator = Counter(str(row["document_originator"]) for row in residual)
    payload = {
        "schema_version": RECOVERY_REPORT_SCHEMA_VERSION,
        "generated_at_utc": generated,
        "summary": {
            "total_blocked_logical_artifacts": total,
            "already_satisfied": already,
            "full_rationale_recovered": {
                "NSE": full_by_source["NSE"],
                "BSE": full_by_source["BSE"],
                "issuer_ir": full_by_source["issuer_ir"],
                "debt_issue_document": full_by_source["debt_issue_document"],
            },
            "rating_letter_recovered": class_counts["RATING_LETTER"],
            "rating_action_intimation_recovered": class_counts[
                "RATING_ACTION_INTIMATION"
            ],
            "rating_rationale_excerpt_recovered": class_counts[
                "RATING_RATIONALE_EXCERPT"
            ],
            "metadata_only_recovered": (
                class_counts["REGULATORY_RATING_METADATA"]
                + class_counts["ANNUAL_REPORT_RATING_SUMMARY"]
            ),
            "unknown_rating_document_recovered": class_counts[
                "UNKNOWN_RATING_DOCUMENT"
            ],
            "ambiguous_candidates": ambiguous,
            "no_official_source_found": no_official,
            "manual_authorized_import_required": manual,
            "licensed_feed_or_written_permission_required": no_official,
            "crisil_residual": residual_by_originator["CRISIL"],
            "icra_residual": residual_by_originator["ICRA"],
            "quarantined_unresolvable": len(quarantine),
            "in_progress": in_progress,
            "recovered_total": len(recovered),
            "accounted_total": accounted,
        },
        "status_counts": dict(sorted(status_counts.items())),
        "targets": [dict(row) for row in targets],
        "quarantine": [dict(row) for row in quarantine],
    }
    return payload


def retryable_jobs(conn, *, scope_id):
    where = "q.status = 'RETRYABLE_FAILURE'"
    params: list[str] = []
    if scope_id:
        where += " AND q.scope_id = ?"
        params.append(scope_id)

    return conn.execute(
        f"""SELECT q.download_job_id, q.scope_id, q.requested_url, q.target_host,
                   q.attempt_count, q.next_attempt_at_utc, q.last_result_status,
                   q.last_error, q.updated_at_utc, a.artifact_type
              FROM artifact_download_queue q
              JOIN document_artifacts a ON a.artifact_id = q.artifact_id
             WHERE {where}""",
        params,
    ).fetchall()



def retryable_host_recency(conn, *, like, host):
    rec = conn.execute(
        """SELECT
             MAX(CASE WHEN result_status = 'validated' THEN fetched_at_utc END) latest_success,
             MAX(CASE WHEN result_status <> 'validated' THEN fetched_at_utc END) latest_failure,
             SUM(result_status = 'validated') validated,
             COUNT(*) attempts
           FROM artifact_download_attempts
          WHERE requested_url LIKE ?""",
        (like,),
    ).fetchone()
    health = conn.execute(
        "SELECT blocked_until_utc, consecutive_blocks, last_result_status"
        "  FROM artifact_download_host_health WHERE host = ?",
        (host,),
    ).fetchone()
    return rec, health


def document_recovery_jobs(conn, *, scope_id=None, include_pending=False):
    generic_scope = " AND queue.scope_id=?" if scope_id is not None else ""
    generic_parameters: tuple[Any, ...] = (scope_id,) if scope_id is not None else ()
    statuses = (
        "'RETRYABLE_FAILURE','TERMINAL_FAILURE','SKIPPED_POLICY'"
        + (",'PENDING'" if include_pending else "")
    )
    rows: list[dict[str, Any]] = []
    generic = conn.execute(
        f"""SELECT 'generic' AS queue_kind, queue.download_job_id,
                   queue.scope_id, queue.artifact_id, artifact.artifact_type,
                   company.current_legal_name,
                   (SELECT identifier_value FROM security_identifiers
                     WHERE company_id=artifact.company_id AND is_current=1
                       AND identifier_type IN ('NSE', 'BSE')
                     ORDER BY CASE identifier_type WHEN 'NSE' THEN 0 ELSE 1 END
                     LIMIT 1) AS symbol,
                   queue.requested_url, queue.target_host, queue.status,
                   queue.attempt_count, queue.next_attempt_at_utc,
                   queue.last_result_status, queue.last_error,
                   EXISTS(SELECT 1 FROM artifact_contents AS content
                           WHERE content.artifact_id=queue.artifact_id) AS covered
              FROM artifact_download_queue AS queue
              JOIN document_artifacts AS artifact USING (artifact_id)
              JOIN companies AS company USING (company_id)
             WHERE queue.status IN ({statuses}) {generic_scope}
             ORDER BY queue.download_job_id""",
        generic_parameters,
    ).fetchall()
    rows.extend(dict(row) for row in generic)
    if scope_id is None:
        rating_statuses = (
            "'RETRYABLE_FAILURE','TERMINAL_FAILURE','INTERACTION_REQUIRED',"
            "'SKIPPED_POLICY'" + (",'PENDING'" if include_pending else "")
        )
        rating = conn.execute(
            f"""SELECT 'credit_rating' AS queue_kind, queue.download_job_id,
                       NULL AS scope_id, queue.artifact_id, artifact.artifact_type,
                       company.current_legal_name,
                       (SELECT identifier_value FROM security_identifiers
                         WHERE company_id=artifact.company_id AND is_current=1
                           AND identifier_type IN ('NSE', 'BSE')
                         ORDER BY CASE identifier_type WHEN 'NSE' THEN 0 ELSE 1 END
                         LIMIT 1) AS symbol,
                       queue.requested_url, queue.target_host, queue.status,
                       queue.attempt_count, queue.next_attempt_at_utc,
                       queue.last_result_status, queue.last_error,
                       EXISTS(SELECT 1 FROM artifact_contents AS content
                               WHERE content.artifact_id=queue.artifact_id) AS covered
                  FROM credit_rating_download_queue AS queue
                  JOIN document_artifacts AS artifact USING (artifact_id)
                  JOIN companies AS company USING (company_id)
                 WHERE queue.status IN ({rating_statuses})
                 ORDER BY queue.download_job_id"""
        ).fetchall()
        rows.extend(dict(row) for row in rating)

    return rows
