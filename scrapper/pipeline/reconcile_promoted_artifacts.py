"""Immediate, append-only D09 repair for promoted/recovered artifacts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from typing import Any

from ..db.repo import utc_now
from ..db.repository_backend import statement


PROMOTED_RECONCILER_VERSION = "1.0.0"


def _event_match_basis(
    expectation: sqlite3.Row, artifact: sqlite3.Row
) -> dict[str, Any]:
    """Match event date, period end, or the complete FY+quarter pair."""

    basis: dict[str, Any] = {}
    if expectation["event_date"] is not None and artifact["event_date"] == expectation["event_date"]:
        basis["event_date"] = expectation["event_date"]
    if expectation["period_end"] is not None and artifact["period_end"] == expectation["period_end"]:
        basis["period_end"] = expectation["period_end"]
    if (
        expectation["fiscal_year"] is not None
        and expectation["fiscal_quarter"] is not None
        and artifact["fiscal_year"] == expectation["fiscal_year"]
        and artifact["fiscal_quarter"] == expectation["fiscal_quarter"]
    ):
        basis["fiscal_year"] = expectation["fiscal_year"]
        basis["fiscal_quarter"] = expectation["fiscal_quarter"]
    return basis


def _event_match(expectation: sqlite3.Row, artifact: sqlite3.Row) -> bool:
    return bool(_event_match_basis(expectation, artifact))


def _accepted_artifacts(
    conn: sqlite3.Connection, expectation: sqlite3.Row
) -> tuple[sqlite3.Row, ...]:
    artifacts = conn.execute(
        """SELECT * FROM document_artifacts
            WHERE company_id=? AND artifact_type=? ORDER BY artifact_id""",
        (expectation["company_id"], expectation["artifact_type"]),
    ).fetchall()
    accepted: list[sqlite3.Row] = []
    for artifact in artifacts:
        if not _event_match(expectation, artifact):
            continue
        artifact_id = int(artifact["artifact_id"])
        if expectation["artifact_type"] == "credit_rating":
            exists = conn.execute(
                """SELECT 1 FROM credit_rating_normalizations
                    WHERE artifact_id=? AND normalization_status='accepted'
                    UNION ALL
                    SELECT 1 FROM artifact_recovery_targets
                    WHERE artifact_id=? AND status='RECOVERED'
                      AND basic_rating_coverage=1 LIMIT 1""",
                (artifact_id, artifact_id),
            ).fetchone()
        elif expectation["artifact_type"] == "transcript":
            exists = conn.execute(
                """SELECT 1 FROM transcripts WHERE artifact_id=?
                    AND origin='official' AND validation_status='accepted' LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        elif expectation["artifact_type"] == "recording":
            exists = conn.execute(
                """SELECT 1 FROM recording_normalizations WHERE artifact_id=?
                    AND normalization_status='accepted' LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        else:
            exists = conn.execute(
                """SELECT 1 FROM document_extractions AS extraction
                    JOIN artifact_contents AS content USING (content_id)
                    WHERE content.artifact_id=?
                      AND extraction.extraction_status='ok' LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        if exists is not None:
            accepted.append(artifact)
    return tuple(accepted)


def _availability_evidence(
    conn: sqlite3.Connection, artifact_id: int
) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT artifact_source_id,document_url,document_originator,
                  retrieval_source_type,retrieval_host,published_at_utc,
                  available_at_utc,availability_basis,first_observed_at_utc
             FROM artifact_sources WHERE artifact_id=?
            ORDER BY (retrieval_source_type IS NOT NULL) DESC,artifact_source_id""",
        (artifact_id,),
    ).fetchall()
    return {
        "sources": [dict(row) for row in rows],
        "historical_as_of_backfill": False,
        "availability_rule": (
            "source publication/availability is preserved; reconciliation check "
            "is recorded only at the current check time"
        ),
    }


def _claim_token(expectation_id: int, artifact_id: int) -> str:
    identity = f"promoted:{PROMOTED_RECONCILER_VERSION}:{expectation_id}:{artifact_id}"
    return "promoted-reconcile:" + hashlib.sha256(identity.encode()).hexdigest()


def reconcile_promoted_artifacts(
    conn: sqlite3.Connection,
    *,
    limit: int = 500,
    dry_run: bool = False,
    checked_at_utc: str | None = None,
) -> dict[str, Any]:
    """Close matching old expectations now, without waiting for retry timing."""

    if limit < 1:
        raise ValueError("limit must be positive")
    checked = checked_at_utc or utc_now()
    expectations = conn.execute(
        """SELECT expectation.*,event.event_key,event.event_type,event.event_date,
                  event.period_end,event.fiscal_year,event.fiscal_quarter
             FROM artifact_expectations AS expectation
             JOIN artifact_events AS event USING (event_id)
            WHERE expectation.availability_status<>'available'
            ORDER BY expectation.expectation_id LIMIT ?""",
        (limit,),
    ).fetchall()
    outcomes: Counter[str] = Counter()
    repairs: list[dict[str, Any]] = []
    for expectation in expectations:
        candidates = _accepted_artifacts(conn, expectation)
        if not candidates:
            outcomes["UNRESOLVED"] += 1
            continue
        if len(candidates) > 1:
            outcomes["AMBIGUOUS"] += 1
            repairs.append({
                "expectation_id": int(expectation["expectation_id"]),
                "status": "AMBIGUOUS",
                "candidate_artifact_ids": [int(row["artifact_id"]) for row in candidates],
            })
            continue
        artifact = candidates[0]
        artifact_id = int(artifact["artifact_id"])
        token = _claim_token(int(expectation["expectation_id"]), artifact_id)
        existing = conn.execute(
            "SELECT check_id FROM artifact_check_events WHERE claim_token=?", (token,)
        ).fetchone()
        if existing is not None:
            outcomes["ALREADY_RECONCILED"] += 1
            continue
        evidence = {
            "reconciler_version": PROMOTED_RECONCILER_VERSION,
            "match_basis": _event_match_basis(expectation, artifact),
            "artifact_first_observed_at_utc": artifact["first_observed_at_utc"],
            "artifact_published_at_utc": artifact["published_at_utc"],
            **_availability_evidence(conn, artifact_id),
        }
        repairs.append({
            "expectation_id": int(expectation["expectation_id"]),
            "status": "WOULD_RECONCILE" if dry_run else "RECONCILED",
            "artifact_id": artifact_id,
            "match_basis": evidence["match_basis"],
        })
        if dry_run:
            outcomes["WOULD_RECONCILE"] += 1
            continue
        with conn:
            cursor = conn.execute(
                statement(conn, """INSERT OR IGNORE INTO artifact_check_events
                   (expectation_id,checked_at_utc,source,requested_url,
                    result_status,evidence_json,discovered_artifact_id,
                    error_json,retryable,next_check_at_utc,claim_token)
                   VALUES (?,?,'promoted_artifact_reconciliation',NULL,'available',
                           ?,?,'{}',0,NULL,?)""", "INSERT INTO artifact_check_events\n                   (expectation_id,checked_at_utc,source,requested_url,\n                    result_status,evidence_json,discovered_artifact_id,\n                    error_json,retryable,next_check_at_utc,claim_token)\n                   VALUES (?,?,'promoted_artifact_reconciliation',NULL,'available',\n                           ?,?,'{}',0,NULL,?) ON CONFLICT DO NOTHING"),
                (
                    expectation["expectation_id"],
                    checked,
                    json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                    artifact_id,
                    token,
                ),
            )
            if cursor.rowcount != 1:
                outcomes["ALREADY_RECONCILED"] += 1
                continue
            conn.execute(
                """UPDATE artifact_expectations
                      SET availability_status='available',
                          first_checked_at_utc=COALESCE(first_checked_at_utc,?),
                          last_checked_at_utc=?,next_check_at_utc=NULL,
                          attempt_count=attempt_count+1,terminal_reason=NULL,
                          last_error_json='{}',claim_token=NULL,claimed_at_utc=NULL,
                          claim_expires_at_utc=NULL,updated_at_utc=?
                    WHERE expectation_id=?""",
                (checked, checked, checked, expectation["expectation_id"]),
            )
        outcomes["RECONCILED"] += 1
    return {
        "schema_version": PROMOTED_RECONCILER_VERSION,
        "mode": "dry_run" if dry_run else "execute",
        "checked_at_utc": checked,
        "considered_expectation_count": len(expectations),
        "outcome_counts": dict(sorted(outcomes.items())),
        "repairs": repairs,
    }
