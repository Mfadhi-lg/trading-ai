"""DA-06 extraction evidence and download-failure reconciliation persistence."""
from __future__ import annotations
import json
import sqlite3
from typing import Any
from ....documents.rating_evidence import RATING_EVIDENCE_CLASSIFIER_NAME, RATING_EVIDENCE_CLASSIFIER_VERSION, classify_rating_evidence
from ....documents.recovery import RecoverySourceType
from ...repo import utc_now
from .attempts import _remaining_source_status, _recovery_source_group

def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def reconcile_recovery_for_source(
    conn: sqlite3.Connection,
    *,
    artifact_source_id: int,
    content_id: int,
    reconciled_at_utc: str | None = None,
    _hooks: Any = None,
) -> dict[str, Any] | None:
    """Finalize recovery only after D03 rating-evidence classification."""
    RATING_EVIDENCE_CLASSIFIER_NAME = getattr(_hooks, "RATING_EVIDENCE_CLASSIFIER_NAME", globals()["RATING_EVIDENCE_CLASSIFIER_NAME"])
    RATING_EVIDENCE_CLASSIFIER_VERSION = getattr(_hooks, "RATING_EVIDENCE_CLASSIFIER_VERSION", globals()["RATING_EVIDENCE_CLASSIFIER_VERSION"])
    RecoverySourceType = getattr(_hooks, "RecoverySourceType", globals()["RecoverySourceType"])
    _json = getattr(_hooks, "_json", globals()["_json"])
    _remaining_source_status = getattr(_hooks, "_remaining_source_status", globals()["_remaining_source_status"])
    classify_rating_evidence = getattr(_hooks, "classify_rating_evidence", globals()["classify_rating_evidence"])
    utc_now = getattr(_hooks, "utc_now", globals()["utc_now"])

    now = reconciled_at_utc or utc_now()
    row = conn.execute(
        """SELECT source.recovery_target_id,source.recovery_candidate_id,
                  source.artifact_id,candidate.source_type,candidate.content_class
             FROM artifact_sources AS source
             JOIN artifact_recovery_candidates AS candidate
               ON candidate.candidate_id=source.recovery_candidate_id
            WHERE source.artifact_source_id=?""",
        (artifact_source_id,),
    ).fetchone()
    if row is None:
        return None
    content = conn.execute(
        "SELECT 1 FROM artifact_contents WHERE content_id=? AND artifact_id=?",
        (content_id, row["artifact_id"]),
    ).fetchone()
    if content is None:
        raise ValueError("Recovered content does not belong to the source artifact")
    extraction = conn.execute(
        """SELECT extraction_id FROM document_extractions
            WHERE content_id=? AND extraction_status IN ('ok','partial')
            ORDER BY extraction_id DESC LIMIT 1""",
        (content_id,),
    ).fetchone()
    if extraction is None:
        failed_extraction = conn.execute(
            """SELECT extraction_id FROM document_extractions
                WHERE content_id=? AND extraction_status='failed'
                ORDER BY extraction_id DESC LIMIT 1""",
            (content_id,),
        ).fetchone()
        if failed_extraction is not None:
            target_status = _remaining_source_status(
                conn, int(row["recovery_target_id"])
            )
            with conn:
                conn.execute(
                    """UPDATE artifact_recovery_targets
                          SET status=?,basic_rating_coverage=0,
                              full_rationale_coverage=0,resolved_candidate_id=NULL,
                              lease_owner=NULL,lease_expires_at_utc=NULL,
                              next_attempt_at_utc=NULL,updated_at_utc=?
                        WHERE recovery_target_id=? AND resolved_candidate_id=?
                          AND status NOT IN ('RECOVERED','ALREADY_SATISFIED')""",
                    (
                        target_status,
                        now,
                        row["recovery_target_id"],
                        row["recovery_candidate_id"],
                    ),
                )
            return {
                "recovery_target_id": int(row["recovery_target_id"]),
                "artifact_id": int(row["artifact_id"]),
                "content_id": content_id,
                "extraction_id": int(failed_extraction["extraction_id"]),
                "classification_status": "EXTRACTION_FAILED",
                "content_class": None,
                "basic_rating_coverage": False,
                "full_rationale_coverage": False,
                "target_status": target_status,
            }
        return {
            "recovery_target_id": int(row["recovery_target_id"]),
            "artifact_id": int(row["artifact_id"]),
            "content_id": content_id,
            "classification_status": "PENDING",
            "content_class": None,
            "basic_rating_coverage": False,
            "full_rationale_coverage": False,
        }
    pages = [
        dict(page)
        for page in conn.execute(
            """SELECT page_or_slide_number,selected_text FROM document_pages
                WHERE extraction_id=? ORDER BY page_or_slide_number""",
            (extraction["extraction_id"],),
        )
    ]
    classified = classify_rating_evidence(
        pages, source_type=RecoverySourceType(row["source_type"])
    )
    content_class = classified.content_class
    basis = dict(classified.features)
    basis["annexure_page_range"] = classified.annexure_page_range
    provides_basic = content_class.provides_basic_rating
    with conn:
        conn.execute(
            """INSERT INTO artifact_source_evidence_classifications
               (artifact_source_id,content_id,content_class,
                classification_basis_json,classifier_name,classifier_version,
                classified_at_utc)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(artifact_source_id,content_id,classifier_name,classifier_version)
               DO UPDATE SET content_class=excluded.content_class,
                             classification_basis_json=excluded.classification_basis_json,
                             classified_at_utc=excluded.classified_at_utc""",
            (
                artifact_source_id,
                content_id,
                content_class.value,
                _json(basis),
                RATING_EVIDENCE_CLASSIFIER_NAME,
                RATING_EVIDENCE_CLASSIFIER_VERSION,
                now,
            ),
        )
        conn.execute(
            """UPDATE artifact_recovery_candidates
                  SET content_class=?,updated_at_utc=? WHERE candidate_id=?""",
            (content_class.value, now, row["recovery_candidate_id"]),
        )
        if provides_basic:
            conn.execute(
                """UPDATE artifact_recovery_targets
                      SET status='RECOVERED',basic_rating_coverage=1,
                          full_rationale_coverage=?,resolved_candidate_id=?,
                          lease_owner=NULL,lease_expires_at_utc=NULL,
                          next_attempt_at_utc=NULL,updated_at_utc=?
                    WHERE recovery_target_id=?""",
                (
                    int(content_class.provides_full_rationale),
                    row["recovery_candidate_id"],
                    now,
                    row["recovery_target_id"],
                ),
            )
            conn.execute(
                """UPDATE artifact_download_queue
                      SET status='SKIPPED_ALREADY_HAVE_CONTENT',
                          last_result_status='CONTENT_ALREADY_PRESENT',last_error=NULL,
                          next_attempt_at_utc=NULL,lease_owner=NULL,
                          lease_expires_at_utc=NULL,updated_at_utc=?
                    WHERE artifact_id=? AND status IN ('PENDING','RETRYABLE_FAILURE')""",
                (now, row["artifact_id"]),
            )
            conn.execute(
                """UPDATE credit_rating_download_queue
                      SET status='SKIPPED_ALREADY_HAVE_CONTENT',
                          last_result_status='CONTENT_ALREADY_PRESENT',last_error=NULL,
                          next_attempt_at_utc=NULL,lease_owner=NULL,
                          lease_expires_at_utc=NULL,updated_at_utc=?
                    WHERE artifact_id=? AND status IN (
                      'PENDING','RETRYABLE_FAILURE','INTERACTION_REQUIRED')""",
                (now, row["artifact_id"]),
            )
        else:
            conn.execute(
                """UPDATE artifact_recovery_targets
                      SET status=?,basic_rating_coverage=0,
                          full_rationale_coverage=0,resolved_candidate_id=NULL,
                          lease_owner=NULL,lease_expires_at_utc=NULL,
                          next_attempt_at_utc=NULL,updated_at_utc=?
                    WHERE recovery_target_id=? AND status<>'RECOVERED'""",
                (
                    _remaining_source_status(conn, int(row["recovery_target_id"])),
                    now,
                    row["recovery_target_id"],
                ),
            )
    return {
        "recovery_target_id": int(row["recovery_target_id"]),
        "artifact_id": int(row["artifact_id"]),
        "content_id": content_id,
        "classification_status": "CLASSIFIED",
        "content_class": content_class.value,
        "basic_rating_coverage": provides_basic,
        "full_rationale_coverage": content_class.provides_full_rationale,
    }


def classify_direct_rating_evidence(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    content_id: int,
    classified_at_utc: str | None = None,
    _hooks: Any = None,
) -> list[dict[str, Any]]:
    """Classify extracted direct rating content against its exact retrieval sources.

    Alternate recovery sources are finalized by ``reconcile_recovery_for_source``;
    this path covers already-acquired publisher/exchange documents without
    changing acquisition or recovery state.
    """
    RATING_EVIDENCE_CLASSIFIER_NAME = getattr(_hooks, "RATING_EVIDENCE_CLASSIFIER_NAME", globals()["RATING_EVIDENCE_CLASSIFIER_NAME"])
    RATING_EVIDENCE_CLASSIFIER_VERSION = getattr(_hooks, "RATING_EVIDENCE_CLASSIFIER_VERSION", globals()["RATING_EVIDENCE_CLASSIFIER_VERSION"])
    RecoverySourceType = getattr(_hooks, "RecoverySourceType", globals()["RecoverySourceType"])
    _json = getattr(_hooks, "_json", globals()["_json"])
    classify_rating_evidence = getattr(_hooks, "classify_rating_evidence", globals()["classify_rating_evidence"])
    utc_now = getattr(_hooks, "utc_now", globals()["utc_now"])

    artifact = conn.execute(
        """SELECT artifact_type,title_raw FROM document_artifacts
            WHERE artifact_id=?""",
        (artifact_id,),
    ).fetchone()
    if artifact is None or artifact["artifact_type"] != "credit_rating":
        return []
    extraction = conn.execute(
        """SELECT extraction_id FROM document_extractions
            WHERE content_id=? AND extraction_status IN ('ok','partial')
            ORDER BY extraction_id DESC LIMIT 1""",
        (content_id,),
    ).fetchone()
    if extraction is None:
        return []
    pages = [
        dict(page)
        for page in conn.execute(
            """SELECT page_or_slide_number,selected_text FROM document_pages
                WHERE extraction_id=? ORDER BY page_or_slide_number""",
            (extraction["extraction_id"],),
        )
    ]
    sources = conn.execute(
        """SELECT * FROM (
           SELECT DISTINCT source.artifact_source_id AS artifact_source_id,
                  source.retrieval_source_type AS retrieval_source_type,
                  source.discovery_page_url AS discovery_page_url
             FROM artifact_download_attempts AS attempt
             JOIN artifact_download_attempt_sources AS linked
               USING (download_attempt_id)
             JOIN artifact_sources AS source USING (artifact_source_id)
            WHERE attempt.content_id=? AND source.artifact_id=?
              AND source.recovery_candidate_id IS NULL
            UNION
           SELECT source.artifact_source_id,source.retrieval_source_type,
                  source.discovery_page_url
             FROM manual_document_imports AS imported
             JOIN artifact_sources AS source USING (artifact_source_id)
            WHERE imported.content_id=? AND source.artifact_id=?
                  AND source.recovery_candidate_id IS NULL
        ) ORDER BY artifact_source_id""",
        (content_id, artifact_id, content_id, artifact_id),
    ).fetchall()
    now = classified_at_utc or utc_now()
    results: list[dict[str, Any]] = []
    with conn:
        for source in sources:
            source_type = None
            raw_source_type = source["retrieval_source_type"]
            if raw_source_type:
                try:
                    source_type = RecoverySourceType(str(raw_source_type))
                except ValueError:
                    source_type = None
            classified = classify_rating_evidence(
                pages,
                source_type=source_type,
                filing_context=" ".join(
                    value
                    for value in (
                        str(artifact["title_raw"] or ""),
                        str(source["discovery_page_url"] or ""),
                    )
                    if value
                ),
            )
            basis = dict(classified.features)
            basis.update(
                {
                    "annexure_page_range": classified.annexure_page_range,
                    "classification_scope": "direct_acquired_content",
                    "retrieval_source_type": raw_source_type,
                }
            )
            conn.execute(
                """INSERT INTO artifact_source_evidence_classifications
                   (artifact_source_id,content_id,content_class,
                    classification_basis_json,classifier_name,classifier_version,
                    classified_at_utc)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(artifact_source_id,content_id,classifier_name,classifier_version)
                   DO UPDATE SET content_class=excluded.content_class,
                                 classification_basis_json=excluded.classification_basis_json,
                                 classified_at_utc=excluded.classified_at_utc""",
                (
                    int(source["artifact_source_id"]),
                    content_id,
                    classified.content_class.value,
                    _json(basis),
                    RATING_EVIDENCE_CLASSIFIER_NAME,
                    RATING_EVIDENCE_CLASSIFIER_VERSION,
                    now,
                ),
            )
            results.append(
                {
                    "artifact_source_id": int(source["artifact_source_id"]),
                    "content_id": content_id,
                    "content_class": classified.content_class.value,
                }
            )
    return results


def reopen_recovery_after_download_failure(
    conn: sqlite3.Connection,
    *,
    artifact_source_id: int,
    failed_at_utc: str | None = None,
    _hooks: Any = None,
) -> dict[str, Any] | None:
    """Release a failed selected source so untried source groups remain claimable."""
    _recovery_source_group = getattr(_hooks, "_recovery_source_group", globals()["_recovery_source_group"])
    _remaining_source_status = getattr(_hooks, "_remaining_source_status", globals()["_remaining_source_status"])
    utc_now = getattr(_hooks, "utc_now", globals()["utc_now"])

    now = failed_at_utc or utc_now()
    row = conn.execute(
        """SELECT source.recovery_target_id,source.recovery_candidate_id,
                  candidate.source_type,target.status,target.resolved_candidate_id
             FROM artifact_sources AS source
             JOIN artifact_recovery_candidates AS candidate
               ON candidate.candidate_id=source.recovery_candidate_id
             JOIN artifact_recovery_targets AS target
               ON target.recovery_target_id=source.recovery_target_id
            WHERE source.artifact_source_id=?""",
        (artifact_source_id,),
    ).fetchone()
    if row is None or row["status"] in {"RECOVERED", "ALREADY_SATISFIED"}:
        return None
    target_id = int(row["recovery_target_id"])
    status = _remaining_source_status(conn, target_id)
    with conn:
        conn.execute(
            """UPDATE artifact_recovery_targets
                  SET status=?,resolved_candidate_id=NULL,next_attempt_at_utc=NULL,
                      lease_owner=NULL,lease_expires_at_utc=NULL,updated_at_utc=?
                WHERE recovery_target_id=? AND resolved_candidate_id=?
                  AND status NOT IN ('RECOVERED','ALREADY_SATISFIED')""",
            (status, now, target_id, row["recovery_candidate_id"]),
        )
    return {
        "recovery_target_id": target_id,
        "failed_source_group": _recovery_source_group(str(row["source_type"])),
        "target_status": status,
    }
