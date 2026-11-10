"""Immutable recovery discovery, candidates and same-artifact attachment."""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from ....config import DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS
from ....documents.recovery import RecoveryCandidate
from ...document_download_queue_repo import ensure_recovery_download_scope
from ...repo import utc_now
from .targets import load_recovery_target
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)

def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@atomic_repository('documents')
def store_recovery_discovery_blob(
    conn: sqlite3.Connection,
    *,
    content: bytes,
    source: str,
    requested_url: str,
    content_type: str,
    observed_at_utc: str,
    snapshot_root: Path = DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS,
    _hooks: Any = None,
) -> int:
    """Persist immutable search-response evidence, never candidate document bytes."""

    digest = hashlib.sha256(content).hexdigest()
    suffix = ".json" if "json" in content_type.lower() else ".html"
    destination = snapshot_root / source.lower() / digest[:2] / f"{digest}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
    with repository_transaction(conn):
        execute(conn,
            statement(conn, """INSERT OR IGNORE INTO response_blobs
               (content_sha256,content_type,encoding,byte_size,local_path,
                first_observed_at_utc,created_at_utc)
               VALUES (?,?,NULL,?,?,?,?)""", """INSERT INTO response_blobs
               (content_sha256,content_type,encoding,byte_size,local_path,
                first_observed_at_utc,created_at_utc)
               VALUES (?,?,NULL,?,?,?,?) ON CONFLICT DO NOTHING"""),
            (
                digest,
                content_type,
                len(content),
                str(destination.resolve()),
                observed_at_utc,
                observed_at_utc,
            ),
        )
    row = execute(conn,
        "SELECT blob_id FROM response_blobs WHERE content_sha256=?", (digest,)
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"Failed to retain recovery discovery response for {requested_url}"
        )
    return int(row["blob_id"])


@atomic_repository('documents')
def persist_recovery_candidates(
    conn: sqlite3.Connection,
    *,
    recovery_target_id: int,
    candidates: tuple[RecoveryCandidate, ...],
    discovery_blob_id: int,
    persisted_at_utc: str | None = None,
    _hooks: Any = None,
) -> dict[str, Any]:
    """Upsert one search response's candidates and arbitrate repeated plausibility."""
    _json = getattr(_hooks, "_json", globals()["_json"])
    load_recovery_target = getattr(_hooks, "load_recovery_target", globals()["load_recovery_target"])
    utc_now = getattr(_hooks, "utc_now", globals()["utc_now"])

    now = persisted_at_utc or utc_now()
    target = load_recovery_target(conn, recovery_target_id)
    before = int(
        execute(conn,
            "SELECT COUNT(*) FROM artifact_recovery_candidates WHERE recovery_target_id=?",
            (recovery_target_id,),
        ).fetchone()[0]
    )
    with repository_transaction(conn):
        for candidate in candidates:
            if candidate.artifact_id != target.artifact_id:
                raise ValueError("Candidate belongs to a different logical artifact")
            if candidate.recovery_target_id != recovery_target_id:
                raise ValueError("Candidate belongs to a different recovery target")
            evidence = dict(candidate.source_evidence)
            discovery_page_url = str(evidence.pop("discovery_page_url", "")) or None
            published_at = evidence.pop("published_at_utc", None)
            available_at = str(
                evidence.pop("available_at_utc", candidate.discovered_at_utc)
            )
            availability_basis = str(
                evidence.pop("availability_basis", "first_observation")
            )
            execute(conn,
                """INSERT INTO artifact_recovery_candidates
                   (recovery_target_id,artifact_id,candidate_url,retrieval_host,
                    source_type,discovered_at_utc,discovery_page_url,
                    discovery_blob_id,document_title,publication_date,
                    published_at_utc,available_at_utc,availability_basis,
                    company_identifiers_json,document_originator,rating,outlook,
                    instrument_or_facility,source_evidence_json,
                    matching_features_json,confidence,rejection_reason,
                    content_class,contract_version,created_at_utc,updated_at_utc)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(recovery_target_id,source_type,candidate_url) DO UPDATE SET
                     document_title=excluded.document_title,
                     publication_date=excluded.publication_date,
                     published_at_utc=COALESCE(
                       artifact_recovery_candidates.published_at_utc,
                       excluded.published_at_utc),
                     available_at_utc=CASE
                       WHEN artifact_recovery_candidates.availability_basis='first_observation'
                        AND excluded.availability_basis='exchange_filing_timestamp'
                       THEN excluded.available_at_utc
                       ELSE artifact_recovery_candidates.available_at_utc END,
                     availability_basis=CASE
                       WHEN artifact_recovery_candidates.availability_basis='first_observation'
                        AND excluded.availability_basis='exchange_filing_timestamp'
                       THEN excluded.availability_basis
                       ELSE artifact_recovery_candidates.availability_basis END,
                     source_evidence_json=excluded.source_evidence_json,
                     matching_features_json=excluded.matching_features_json,
                     confidence=CASE WHEN attached_source_id IS NULL
                       THEN excluded.confidence ELSE confidence END,
                     rejection_reason=CASE WHEN attached_source_id IS NULL
                       THEN excluded.rejection_reason ELSE rejection_reason END,
                     updated_at_utc=excluded.updated_at_utc""",
                (
                    recovery_target_id,
                    candidate.artifact_id,
                    candidate.candidate_url,
                    candidate.retrieval_host,
                    candidate.source_type.value,
                    candidate.discovered_at_utc,
                    discovery_page_url,
                    discovery_blob_id,
                    candidate.document_title,
                    candidate.publication_date,
                    published_at,
                    available_at,
                    availability_basis,
                    _json(candidate.company_identifiers),
                    candidate.document_originator.value
                    if candidate.document_originator
                    else None,
                    candidate.rating,
                    candidate.outlook,
                    candidate.instrument_or_facility,
                    _json(evidence),
                    _json(candidate.matching_features),
                    candidate.confidence.value,
                    candidate.rejection_reason,
                    candidate.content_class.value,
                    candidate.contract_version,
                    now,
                    now,
                ),
            )
        plausible = execute(conn,
            """SELECT candidate_id FROM artifact_recovery_candidates
                WHERE recovery_target_id=? AND confidence IN ('EXACT','HIGH')
                  AND attached_source_id IS NULL""",
            (recovery_target_id,),
        ).fetchall()
        if len(plausible) > 1:
            execute(conn,
                """UPDATE artifact_recovery_candidates
                      SET confidence='AMBIGUOUS',
                          rejection_reason='multiple plausible official filings',
                          updated_at_utc=?
                    WHERE recovery_target_id=? AND confidence IN ('EXACT','HIGH')
                      AND attached_source_id IS NULL""",
                (now, recovery_target_id),
            )
        counts = {
            str(row["confidence"]): int(row["count"])
            for row in execute(conn,
                """SELECT confidence,COUNT(*) AS count
                    FROM artifact_recovery_candidates WHERE recovery_target_id=?
                    GROUP BY confidence""",
                (recovery_target_id,),
            )
        }
        status = (
            "CANDIDATE_FOUND"
            if counts.get("EXACT", 0) + counts.get("HIGH", 0)
            else "AMBIGUOUS"
            if counts.get("AMBIGUOUS", 0)
            else "SEARCHING"
        )
        execute(conn,
            """UPDATE artifact_recovery_targets SET status=?,updated_at_utc=?
                WHERE recovery_target_id=?
                  AND status NOT IN ('RECOVERED','ALREADY_SATISFIED')""",
            (status, now, recovery_target_id),
        )
    after = int(
        execute(conn,
            "SELECT COUNT(*) FROM artifact_recovery_candidates WHERE recovery_target_id=?",
            (recovery_target_id,),
        ).fetchone()[0]
    )
    return {
        "created_candidate_count": after - before,
        "confidence_counts": counts,
        "target_status": status,
    }


@atomic_repository('documents')
def attach_recovery_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_id: int,
    attached_at_utc: str | None = None,
    queue_priority: int = 50,
    _hooks: Any = None,
) -> dict[str, Any]:
    """Attach one accepted official retrieval URL to the existing artifact."""
    ensure_recovery_download_scope = getattr(_hooks, "ensure_recovery_download_scope", globals()["ensure_recovery_download_scope"])
    utc_now = getattr(_hooks, "utc_now", globals()["utc_now"])

    now = attached_at_utc or utc_now()
    candidate = execute(conn,
        """SELECT candidate.*,target.company_id,target.blocked_source_id,
                  target.document_originator AS target_originator,
                  target.resolved_candidate_id AS target_resolved_candidate_id
             FROM artifact_recovery_candidates AS candidate
             JOIN artifact_recovery_targets AS target USING (recovery_target_id)
            WHERE candidate.candidate_id=?""",
        (candidate_id,),
    ).fetchone()
    if candidate is None:
        raise ValueError(f"Unknown recovery candidate: {candidate_id}")
    if candidate["confidence"] not in {"EXACT", "HIGH"}:
        raise ValueError("Only EXACT/HIGH candidates may be attached automatically")
    if candidate["target_resolved_candidate_id"] not in {None, candidate_id}:
        raise ValueError("Recovery target already selected a different alternate source")
    competing = int(
        execute(conn,
            """SELECT COUNT(*) FROM artifact_recovery_candidates
                WHERE recovery_target_id=? AND confidence IN ('EXACT','HIGH')
                  AND candidate_id<>? AND attached_source_id IS NULL""",
            (candidate["recovery_target_id"], candidate_id),
        ).fetchone()[0]
    )
    if competing:
        raise ValueError("Multiple plausible candidates require manual resolution")
    source_kind = str(candidate["source_type"])
    discovered_from = (
        "nse"
        if source_kind.startswith("NSE_")
        else "bse"
        if source_kind.startswith("BSE_")
        else "company_ir"
        if source_kind == "ISSUER_IR_ATTACHMENT"
        else "nse"
    )
    discovery_page_url = candidate["discovery_page_url"]
    if not discovery_page_url or candidate["discovery_blob_id"] is None:
        raise ValueError(
            "Accepted candidates require immutable discovery response evidence"
        )
    source_evidence = json.loads(candidate["source_evidence_json"])
    announcement_id = source_evidence.get("announcement_id") or source_evidence.get(
        "filing_id"
    )
    with repository_transaction(conn):
        execute(conn,
            statement(conn, """INSERT OR IGNORE INTO artifact_discovery_pages
               (company_id,discovered_from_source,discovery_page_url,publisher_claim,
                blob_id,parser_name,parser_version,first_observed_at_utc)
               VALUES (?,?,?,?,?,'da06-official-source','1.0.0',?)""", """INSERT INTO artifact_discovery_pages
               (company_id,discovered_from_source,discovery_page_url,publisher_claim,
                blob_id,parser_name,parser_version,first_observed_at_utc)
               VALUES (?,?,?,?,?,'da06-official-source','1.0.0',?) ON CONFLICT DO NOTHING"""),
            (
                candidate["company_id"],
                discovered_from,
                discovery_page_url,
                candidate["target_originator"],
                candidate["discovery_blob_id"],
                now,
            ),
        )
        page = execute(conn,
            """SELECT discovery_page_id FROM artifact_discovery_pages
                WHERE company_id=? AND discovered_from_source=?
                  AND discovery_page_url=? AND blob_id=?
                  AND parser_name='da06-official-source'
                  AND parser_version='1.0.0'""",
            (
                candidate["company_id"],
                discovered_from,
                discovery_page_url,
                candidate["discovery_blob_id"],
            ),
        ).fetchone()
        if page is None:
            raise RuntimeError("Failed to register recovery discovery page")
        artifact_count_before = int(
            execute(conn, "SELECT COUNT(*) FROM document_artifacts").fetchone()[0]
        )
        execute(conn,
            statement(conn, """INSERT OR IGNORE INTO artifact_sources
               (artifact_id,discovery_page_id,discovered_from_source,
                discovery_page_url,document_url,publisher_claim,
                exchange_announcement_id,source_position,source_title_raw,
                classification_evidence_json,is_primary_source,
                first_observed_at_utc,last_checked_at_utc,availability_status,
                automation_status,document_originator,retrieval_source_type,
                retrieval_host,published_at_utc,available_at_utc,
                availability_basis,recovery_target_id,recovery_candidate_id,
                matching_confidence,recovery_evidence_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,'available','AVAILABLE',?,?,?,?,?,?,?,?,?,?)""", """INSERT INTO artifact_sources
               (artifact_id,discovery_page_id,discovered_from_source,
                discovery_page_url,document_url,publisher_claim,
                exchange_announcement_id,source_position,source_title_raw,
                classification_evidence_json,is_primary_source,
                first_observed_at_utc,last_checked_at_utc,availability_status,
                automation_status,document_originator,retrieval_source_type,
                retrieval_host,published_at_utc,available_at_utc,
                availability_basis,recovery_target_id,recovery_candidate_id,
                matching_confidence,recovery_evidence_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,'available','AVAILABLE',?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING"""),
            (
                candidate["artifact_id"],
                page["discovery_page_id"],
                discovered_from,
                discovery_page_url,
                candidate["candidate_url"],
                candidate["target_originator"],
                announcement_id,
                candidate_id,
                candidate["document_title"]
                or "Official alternate rating evidence",
                candidate["matching_features_json"],
                now,
                now,
                candidate["target_originator"],
                source_kind,
                candidate["retrieval_host"],
                candidate["published_at_utc"],
                candidate["available_at_utc"],
                candidate["availability_basis"],
                candidate["recovery_target_id"],
                candidate_id,
                candidate["confidence"],
                candidate["source_evidence_json"],
            ),
        )
        source = execute(conn,
            """SELECT * FROM artifact_sources
                WHERE artifact_id=? AND discovered_from_source=?
                  AND discovery_page_url=? AND document_url=?""",
            (
                candidate["artifact_id"],
                discovered_from,
                discovery_page_url,
                candidate["candidate_url"],
            ),
        ).fetchone()
        if source is None:
            raise RuntimeError("Failed to attach recovery source")
        if (
            source["recovery_candidate_id"] is None
            and source["retrieval_source_type"] is None
        ):
            execute(conn,
                """UPDATE artifact_sources
                      SET automation_status='AVAILABLE',document_originator=?,
                          retrieval_source_type=?,retrieval_host=?,
                          published_at_utc=COALESCE(published_at_utc,?),
                          available_at_utc=COALESCE(available_at_utc,?),
                          availability_basis=COALESCE(availability_basis,?),
                          recovery_target_id=?,recovery_candidate_id=?,
                          matching_confidence=?,recovery_evidence_json=?
                    WHERE artifact_source_id=?
                      AND retrieval_source_type IS NULL
                      AND recovery_candidate_id IS NULL""",
                (
                    candidate["target_originator"],
                    source_kind,
                    candidate["retrieval_host"],
                    candidate["published_at_utc"],
                    candidate["available_at_utc"],
                    candidate["availability_basis"],
                    candidate["recovery_target_id"],
                    candidate_id,
                    candidate["confidence"],
                    candidate["source_evidence_json"],
                    source["artifact_source_id"],
                ),
            )
            source = execute(conn,
                "SELECT * FROM artifact_sources WHERE artifact_source_id=?",
                (source["artifact_source_id"],),
            ).fetchone()
        if source["recovery_candidate_id"] not in {None, candidate_id}:
            raise ValueError(
                "Existing source provenance belongs to a different candidate"
            )
        if source["retrieval_source_type"] != source_kind:
            raise ValueError(
                "Existing source provenance cannot be overwritten by another adapter"
            )
        execute(conn,
            """UPDATE artifact_recovery_candidates
                  SET attached_source_id=?,updated_at_utc=? WHERE candidate_id=?""",
            (source["artifact_source_id"], now, candidate_id),
        )
        execute(conn,
            """UPDATE artifact_recovery_targets
                  SET status='CANDIDATE_FOUND',resolved_candidate_id=?,updated_at_utc=?
                WHERE recovery_target_id=?""",
            (candidate_id, now, candidate["recovery_target_id"]),
        )
        blocked = execute(conn,
            """SELECT automation_status FROM artifact_sources
                WHERE artifact_source_id=?""",
            (candidate["blocked_source_id"],),
        ).fetchone()
        if blocked is None or blocked["automation_status"] != "BLOCKED_TERMS":
            raise RuntimeError(
                "Blocked publisher source policy changed during recovery"
            )
        artifact_count_after = int(
            execute(conn, "SELECT COUNT(*) FROM document_artifacts").fetchone()[0]
        )
        if artifact_count_before != artifact_count_after:
            raise RuntimeError(
                "Recovery source attachment created a logical artifact"
            )
    queue = ensure_recovery_download_scope(
        conn,
        artifact_id=int(candidate["artifact_id"]),
        company_id=int(candidate["company_id"]),
        created_at_utc=now,
        priority=queue_priority,
    )
    return {
        "artifact_id": int(candidate["artifact_id"]),
        "artifact_source_id": int(source["artifact_source_id"]),
        "candidate_id": candidate_id,
        "recovery_target_id": int(candidate["recovery_target_id"]),
        "queue": queue,
    }
