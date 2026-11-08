"""Durable DA-06 target/candidate registry primitives."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from ..config import DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS
from ..core.access_policy import BLOCKED_TERMS_HOSTS, host_matches_policy
from ..documents.rating_evidence import (
    RATING_EVIDENCE_CLASSIFIER_NAME,
    RATING_EVIDENCE_CLASSIFIER_VERSION,
    classify_rating_evidence,
)
from ..documents.recovery import (
    RECOVERY_CONTRACT_VERSION,
    DocumentOriginator,
    RecoveryCandidate,
    RecoveryConfidence,
    RecoverySourceType,
    RecoveryTarget,
    RecoveryTargetStatus,
)
from .document_download_queue_repo import ensure_recovery_download_scope
from .repo import utc_now


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


RECOVERY_SOURCE_GROUPS = ("nse", "bse", "issuer-ir")


def _recovery_source_group(source_type: str) -> str | None:
    if source_type.startswith("NSE_"):
        return "nse"
    if source_type.startswith("BSE_"):
        return "bse"
    if source_type == "ISSUER_IR_ATTACHMENT":
        return "issuer-ir"
    return None


def _remaining_source_status(conn: sqlite3.Connection, target_id: int) -> str:
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


def _after(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).isoformat()


def _originator(host: str) -> DocumentOriginator | None:
    normalized = host.lower().rstrip(".")
    if normalized == "crisil.com" or normalized.endswith(".crisil.com"):
        return DocumentOriginator.CRISIL
    if normalized == "crisilratings.com" or normalized.endswith(".crisilratings.com"):
        return DocumentOriginator.CRISIL
    if normalized == "icra.in" or normalized.endswith(".icra.in"):
        return DocumentOriginator.ICRA
    return None


def _blocked_artifacts(
    conn: sqlite3.Connection,
    artifact_ids: tuple[int, ...] | None = None,
) -> tuple[sqlite3.Row, ...]:
    artifact_filter = ""
    parameters: tuple[Any, ...] = ()
    if artifact_ids is not None:
        placeholders = ",".join("?" for _ in artifact_ids)
        artifact_filter = f" AND artifact.artifact_id IN ({placeholders})"
        parameters = artifact_ids
    rows = conn.execute(
        f"""SELECT artifact.artifact_id, artifact.company_id,
                  artifact.artifact_type, artifact.event_date,
                  artifact.period_end, artifact.fiscal_year,
                  artifact.fiscal_quarter, artifact.publisher,
                  company.current_legal_name,
                  source.artifact_source_id, source.document_url
             FROM document_artifacts AS artifact
             JOIN companies AS company USING (company_id)
             JOIN artifact_sources AS source USING (artifact_id)
            WHERE artifact.artifact_type='credit_rating'
            {artifact_filter}
            ORDER BY artifact.artifact_id, source.artifact_source_id""",
        parameters,
    ).fetchall()
    return tuple(
        row
        for row in rows
        if (host := (urlsplit(str(row["document_url"])).hostname or "").lower())
        and host_matches_policy(host, BLOCKED_TERMS_HOSTS)
    )


def _identities(conn: sqlite3.Connection, company_id: int) -> dict[str, tuple[str, ...]]:
    identifiers = conn.execute(
        """SELECT upper(identifier_type) AS kind, identifier_value
             FROM security_identifiers WHERE company_id=?
             ORDER BY is_current DESC, security_identifier_id""",
        (company_id,),
    ).fetchall()
    nse = {str(row["identifier_value"]).upper() for row in identifiers if row["kind"] == "NSE"}
    bse = {str(row["identifier_value"]) for row in identifiers if row["kind"] == "BSE"}
    isins = {str(row["identifier_value"]).upper() for row in identifiers if row["kind"] == "ISIN"}
    aliases: set[str] = set()
    try:
        rows = conn.execute(
            """SELECT security.exchange, security.nse_symbol,
                      security.bse_scrip_code, security.current_isin,
                      version.name_raw
                 FROM exchange_company_registry AS registry
                 JOIN exchange_company_links AS link USING (registry_company_id)
                 JOIN exchange_securities AS security USING (exchange_security_id)
                 LEFT JOIN exchange_security_versions AS version
                   ON version.exchange_security_id=security.exchange_security_id
                WHERE registry.company_id=?
                ORDER BY link.is_current DESC, version.version_id""",
            (company_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = ()
    for row in rows:
        if row["nse_symbol"]:
            nse.add(str(row["nse_symbol"]).upper())
        if row["bse_scrip_code"]:
            bse.add(str(row["bse_scrip_code"]))
        if row["current_isin"]:
            isins.add(str(row["current_isin"]).upper())
        if row["name_raw"]:
            aliases.add(str(row["name_raw"]).strip())
    return {
        "aliases": tuple(sorted(aliases)),
        "nse": tuple(sorted(nse)),
        "bse": tuple(sorted(bse)),
        "isins": tuple(sorted(isins)),
    }


def _rating_context(conn: sqlite3.Connection, artifact_id: int) -> dict[str, str | None]:
    row = conn.execute(
        """SELECT normalization.agency_code, normalization.action_date,
                  action.rating_code, action.outlook_code,
                  action.rating_action_code,
                  action.facility_or_instrument_raw,
                  action.amount_decimal, action.unit_code
             FROM preferred_credit_rating_normalizations AS preferred
             JOIN credit_rating_normalizations AS normalization
               USING (normalization_id)
             LEFT JOIN credit_rating_actions AS action
               ON action.normalization_id=normalization.normalization_id
              AND action.is_canonical=1
            WHERE preferred.artifact_id=?
            ORDER BY action.rating_action_id LIMIT 1""",
        (artifact_id,),
    ).fetchone()
    if row is None:
        return {key: None for key in (
            "action_date", "rating", "outlook", "rating_action", "instrument",
            "amount", "amount_unit",
        )}
    return {
        "action_date": row["action_date"],
        "rating": row["rating_code"],
        "outlook": row["outlook_code"],
        "rating_action": row["rating_action_code"],
        "instrument": row["facility_or_instrument_raw"],
        "amount": row["amount_decimal"],
        "amount_unit": row["unit_code"],
    }


def _already_satisfied(conn: sqlite3.Connection, artifact_id: int) -> bool:
    return conn.execute(
        """SELECT 1
             FROM artifact_contents AS content
             JOIN artifact_source_evidence_classifications AS evidence
               USING (content_id)
            WHERE content.artifact_id=? AND content.is_official=1
              AND evidence.content_class<>'UNKNOWN_RATING_DOCUMENT'
              AND evidence.classification_id=(
                SELECT MAX(latest.classification_id)
                  FROM artifact_source_evidence_classifications AS latest
                 WHERE latest.artifact_source_id=evidence.artifact_source_id
                   AND latest.content_id=evidence.content_id
                   AND latest.classifier_name=evidence.classifier_name
              )
            LIMIT 1""",
        (artifact_id,),
    ).fetchone() is not None


def _target_from_row(
    conn: sqlite3.Connection, row: sqlite3.Row, originator: DocumentOriginator
) -> RecoveryTarget:
    company_id = int(row["company_id"])
    identities = _identities(conn, company_id)
    context = _rating_context(conn, int(row["artifact_id"]))
    host = (urlsplit(str(row["document_url"])).hostname or "").lower().rstrip(".")
    event_date = row["event_date"] or context["action_date"]
    fiscal_period = row["period_end"]
    if fiscal_period is None and row["fiscal_year"] and row["fiscal_quarter"]:
        fiscal_period = f"FY{int(row['fiscal_year'])}-Q{int(row['fiscal_quarter'])}"
    provenance = {
        "company_canonical_name": ("companies.current_legal_name",),
        "company_aliases": ("exchange_security_versions.name_raw",),
        "nse_symbols": ("security_identifiers/exchange_securities",),
        "bse_codes": ("security_identifiers/exchange_securities",),
        "isins": ("security_identifiers/exchange_company_registry",),
        "event_date": (
            "document_artifacts.event_date" if row["event_date"] else
            "preferred_credit_rating_normalization.action_date"
        ,),
        "rating_fields": ("preferred_credit_rating_normalization",),
    }
    return RecoveryTarget(
        recovery_target_id=None,
        artifact_id=int(row["artifact_id"]),
        company_id=company_id,
        artifact_type=str(row["artifact_type"]),
        document_originator=originator,
        blocked_source_id=int(row["artifact_source_id"]),
        blocked_source_url=str(row["document_url"]),
        blocked_source_host=host,
        company_canonical_name=str(row["current_legal_name"]),
        company_aliases=identities["aliases"],
        nse_symbols=identities["nse"],
        bse_codes=identities["bse"],
        isins=identities["isins"],
        event_date=event_date,
        fiscal_period=fiscal_period,
        rating=context["rating"],
        outlook=context["outlook"],
        rating_action=context["rating_action"],
        instrument_or_facility=context["instrument"],
        amount=context["amount"],
        amount_unit=context["amount_unit"],
        source_confidence="DATABASE_TRUTH",
        field_provenance=provenance,
    )


def build_recovery_targets(
    conn: sqlite3.Connection,
    *,
    artifact_type: str = "credit_rating",
    dry_run: bool = False,
    observed_at_utc: str | None = None,
    artifact_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    """Build one durable target per blocked logical artifact, idempotently."""

    if artifact_type != "credit_rating":
        raise ValueError("DA-06 currently supports only credit_rating")
    now = observed_at_utc or utc_now()
    # An explicit id set lets the scrape-time hook build targets for just the
    # links it queued instead of rescanning every rating artifact.
    selected_ids = None if artifact_ids is None else tuple(sorted({int(v) for v in artifact_ids}))
    if selected_ids is not None and not selected_ids:
        return {
            "schema_version": RECOVERY_CONTRACT_VERSION,
            "mode": "dry_run" if dry_run else "execute",
            "total_blocked_artifacts": 0,
            "already_satisfied": 0,
            "recovery_target_count": 0,
            "targets_created": 0,
            "quarantined_unresolvable": 0,
            "crisil_count": 0,
            "icra_count": 0,
            "identity_completeness": 0,
            "event_date_completeness": 0,
            "rating_completeness": 0,
            "instrument_completeness": 0,
            "accounted_total": 0,
        }
    rows = _blocked_artifacts(conn, selected_ids)
    grouped: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(int(row["artifact_id"]), []).append(row)

    target_plans: list[tuple[RecoveryTarget, RecoveryTargetStatus]] = []
    quarantines: list[tuple[int, str, dict[str, Any]]] = []
    for artifact_id, sources in grouped.items():
        originators = {
            value for value in (
                _originator((urlsplit(str(row["document_url"])).hostname or ""))
                for row in sources
            ) if value is not None
        }
        if len(originators) != 1:
            quarantines.append((
                artifact_id,
                "CONFLICTING_DOCUMENT_ORIGINATOR",
                {"blocked_source_ids": [int(row["artifact_source_id"]) for row in sources]},
            ))
            continue
        selected = sources[0]
        target = _target_from_row(conn, selected, next(iter(originators)))
        status = (
            RecoveryTargetStatus.ALREADY_SATISFIED
            if _already_satisfied(conn, artifact_id)
            else RecoveryTargetStatus.PENDING
        )
        target_plans.append((target, status))

    existing_ids = {
        int(row[0]) for row in conn.execute(
            "SELECT artifact_id FROM artifact_recovery_targets"
        )
    }
    created_count = sum(
        1 for target, status in target_plans
        if status is RecoveryTargetStatus.PENDING and target.artifact_id not in existing_ids
    )
    if not dry_run:
        with conn:
            if target_plans:
                planned_ids = tuple(target.artifact_id for target, _ in target_plans)
                placeholders = ",".join("?" for _ in planned_ids)
                conn.execute(
                    f"DELETE FROM artifact_recovery_quarantine WHERE artifact_id IN ({placeholders})",
                    planned_ids,
                )
            for artifact_id, reason, evidence in quarantines:
                conn.execute(
                    """INSERT INTO artifact_recovery_quarantine
                       (artifact_id, reason_code, evidence_json,
                        quarantined_at_utc, updated_at_utc)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(artifact_id) DO UPDATE SET
                         reason_code=excluded.reason_code,
                         evidence_json=excluded.evidence_json,
                         updated_at_utc=excluded.updated_at_utc""",
                    (artifact_id, reason, _json(evidence), now, now),
                )
            for target, status in target_plans:
                conn.execute(
                    """INSERT INTO artifact_recovery_targets
                       (artifact_id, company_id, artifact_type,
                        document_originator, blocked_source_id,
                        blocked_source_url, blocked_source_host,
                        company_canonical_name, company_aliases_json,
                        nse_symbols_json, bse_codes_json, isins_json,
                        event_date, fiscal_period, rating, outlook,
                        rating_action, instrument_or_facility, amount,
                        amount_unit, source_confidence,
                        field_provenance_json, contract_version, status,
                        basic_rating_coverage, created_at_utc, updated_at_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(artifact_id) DO UPDATE SET
                         company_canonical_name=excluded.company_canonical_name,
                         company_aliases_json=excluded.company_aliases_json,
                         nse_symbols_json=excluded.nse_symbols_json,
                         bse_codes_json=excluded.bse_codes_json,
                         isins_json=excluded.isins_json,
                         event_date=COALESCE(artifact_recovery_targets.event_date,
                                             excluded.event_date),
                         fiscal_period=COALESCE(artifact_recovery_targets.fiscal_period,
                                               excluded.fiscal_period),
                         rating=COALESCE(artifact_recovery_targets.rating,
                                         excluded.rating),
                         outlook=COALESCE(artifact_recovery_targets.outlook,
                                          excluded.outlook),
                         rating_action=COALESCE(artifact_recovery_targets.rating_action,
                                                excluded.rating_action),
                         instrument_or_facility=COALESCE(
                           artifact_recovery_targets.instrument_or_facility,
                           excluded.instrument_or_facility),
                         status=CASE
                           WHEN excluded.status='ALREADY_SATISFIED'
                             THEN 'ALREADY_SATISFIED'
                           ELSE artifact_recovery_targets.status END,
                         basic_rating_coverage=MAX(
                           artifact_recovery_targets.basic_rating_coverage,
                           excluded.basic_rating_coverage),
                         updated_at_utc=excluded.updated_at_utc""",
                    (
                        target.artifact_id, target.company_id, target.artifact_type,
                        target.document_originator.value, target.blocked_source_id,
                        target.blocked_source_url, target.blocked_source_host,
                        target.company_canonical_name, _json(target.company_aliases),
                        _json(target.nse_symbols), _json(target.bse_codes),
                        _json(target.isins), target.event_date, target.fiscal_period,
                        target.rating, target.outlook, target.rating_action,
                        target.instrument_or_facility, target.amount,
                        target.amount_unit, target.source_confidence,
                        _json(target.field_provenance), target.contract_version,
                        status.value, int(status is RecoveryTargetStatus.ALREADY_SATISFIED),
                        now, now,
                    ),
                )
                conn.execute(
                    """UPDATE artifact_sources
                          SET automation_status='BLOCKED_TERMS',
                              document_originator=?
                        WHERE artifact_source_id=?""",
                    (target.document_originator.value, target.blocked_source_id),
                )

    actionable = [target for target, status in target_plans if status is RecoveryTargetStatus.PENDING]
    satisfied = [target for target, status in target_plans if status is RecoveryTargetStatus.ALREADY_SATISFIED]
    originator_counts = Counter(target.document_originator.value for target, _ in target_plans)
    total = len(grouped)
    accounted = len(actionable) + len(satisfied) + len(quarantines)
    if accounted != total:
        raise RuntimeError(f"Recovery target accounting mismatch: {total} != {accounted}")
    completeness = {
        "identity": sum(bool(t.nse_symbols or t.bse_codes or t.isins) for t, _ in target_plans),
        "event_date": sum(t.event_date is not None for t, _ in target_plans),
        "rating": sum(t.rating is not None for t, _ in target_plans),
        "instrument": sum(t.instrument_or_facility is not None for t, _ in target_plans),
    }
    return {
        "schema_version": RECOVERY_CONTRACT_VERSION,
        "mode": "dry_run" if dry_run else "execute",
        "total_blocked_artifacts": total,
        "already_satisfied": len(satisfied),
        "recovery_target_count": len(actionable),
        "targets_created": created_count,
        "quarantined_unresolvable": len(quarantines),
        "crisil_count": originator_counts["CRISIL"],
        "icra_count": originator_counts["ICRA"],
        "identity_completeness": completeness["identity"],
        "event_date_completeness": completeness["event_date"],
        "rating_completeness": completeness["rating"],
        "instrument_completeness": completeness["instrument"],
        "accounted_total": accounted,
    }


def load_recovery_target(conn: sqlite3.Connection, recovery_target_id: int) -> RecoveryTarget:
    row = conn.execute(
        "SELECT * FROM artifact_recovery_targets WHERE recovery_target_id=?",
        (recovery_target_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown recovery target: {recovery_target_id}")
    return RecoveryTarget(
        recovery_target_id=int(row["recovery_target_id"]),
        artifact_id=int(row["artifact_id"]),
        company_id=int(row["company_id"]),
        artifact_type=str(row["artifact_type"]),
        document_originator=DocumentOriginator(row["document_originator"]),
        blocked_source_id=int(row["blocked_source_id"]),
        blocked_source_url=str(row["blocked_source_url"]),
        blocked_source_host=str(row["blocked_source_host"]),
        company_canonical_name=str(row["company_canonical_name"]),
        company_aliases=tuple(json.loads(row["company_aliases_json"])),
        nse_symbols=tuple(json.loads(row["nse_symbols_json"])),
        bse_codes=tuple(json.loads(row["bse_codes_json"])),
        isins=tuple(json.loads(row["isins_json"])),
        event_date=row["event_date"],
        fiscal_period=row["fiscal_period"],
        rating=row["rating"],
        outlook=row["outlook"],
        rating_action=row["rating_action"],
        instrument_or_facility=row["instrument_or_facility"],
        amount=row["amount"],
        amount_unit=row["amount_unit"],
        source_confidence=row["source_confidence"],
        field_provenance={
            key: tuple(value)
            for key, value in json.loads(row["field_provenance_json"]).items()
        },
        contract_version=str(row["contract_version"]),
    )


def claim_recovery_target(
    conn: sqlite3.Connection,
    *,
    source: str,
    worker: str,
    now_utc: str,
    lease_seconds: int = 600,
) -> tuple[RecoveryTarget, int] | None:
    """Lease one target not yet settled for this official source group."""

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
) -> None:
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


def store_recovery_discovery_blob(
    conn: sqlite3.Connection,
    *,
    content: bytes,
    source: str,
    requested_url: str,
    content_type: str,
    observed_at_utc: str,
    snapshot_root: Path = DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS,
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
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO response_blobs
               (content_sha256,content_type,encoding,byte_size,local_path,
                first_observed_at_utc,created_at_utc)
               VALUES (?,?,NULL,?,?,?,?)""",
            (
                digest,
                content_type,
                len(content),
                str(destination.resolve()),
                observed_at_utc,
                observed_at_utc,
            ),
        )
    row = conn.execute(
        "SELECT blob_id FROM response_blobs WHERE content_sha256=?", (digest,)
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"Failed to retain recovery discovery response for {requested_url}"
        )
    return int(row["blob_id"])


def persist_recovery_candidates(
    conn: sqlite3.Connection,
    *,
    recovery_target_id: int,
    candidates: tuple[RecoveryCandidate, ...],
    discovery_blob_id: int,
    persisted_at_utc: str | None = None,
) -> dict[str, Any]:
    """Upsert one search response's candidates and arbitrate repeated plausibility."""

    now = persisted_at_utc or utc_now()
    target = load_recovery_target(conn, recovery_target_id)
    before = int(
        conn.execute(
            "SELECT COUNT(*) FROM artifact_recovery_candidates WHERE recovery_target_id=?",
            (recovery_target_id,),
        ).fetchone()[0]
    )
    with conn:
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
            conn.execute(
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
        plausible = conn.execute(
            """SELECT candidate_id FROM artifact_recovery_candidates
                WHERE recovery_target_id=? AND confidence IN ('EXACT','HIGH')
                  AND attached_source_id IS NULL""",
            (recovery_target_id,),
        ).fetchall()
        if len(plausible) > 1:
            conn.execute(
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
            for row in conn.execute(
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
        conn.execute(
            """UPDATE artifact_recovery_targets SET status=?,updated_at_utc=?
                WHERE recovery_target_id=?
                  AND status NOT IN ('RECOVERED','ALREADY_SATISFIED')""",
            (status, now, recovery_target_id),
        )
    after = int(
        conn.execute(
            "SELECT COUNT(*) FROM artifact_recovery_candidates WHERE recovery_target_id=?",
            (recovery_target_id,),
        ).fetchone()[0]
    )
    return {
        "created_candidate_count": after - before,
        "confidence_counts": counts,
        "target_status": status,
    }


def attach_recovery_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_id: int,
    attached_at_utc: str | None = None,
    queue_priority: int = 50,
) -> dict[str, Any]:
    """Attach one accepted official retrieval URL to the existing artifact."""

    now = attached_at_utc or utc_now()
    candidate = conn.execute(
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
        conn.execute(
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
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO artifact_discovery_pages
               (company_id,discovered_from_source,discovery_page_url,publisher_claim,
                blob_id,parser_name,parser_version,first_observed_at_utc)
               VALUES (?,?,?,?,?,'da06-official-source','1.0.0',?)""",
            (
                candidate["company_id"],
                discovered_from,
                discovery_page_url,
                candidate["target_originator"],
                candidate["discovery_blob_id"],
                now,
            ),
        )
        page = conn.execute(
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
            conn.execute("SELECT COUNT(*) FROM document_artifacts").fetchone()[0]
        )
        conn.execute(
            """INSERT OR IGNORE INTO artifact_sources
               (artifact_id,discovery_page_id,discovered_from_source,
                discovery_page_url,document_url,publisher_claim,
                exchange_announcement_id,source_position,source_title_raw,
                classification_evidence_json,is_primary_source,
                first_observed_at_utc,last_checked_at_utc,availability_status,
                automation_status,document_originator,retrieval_source_type,
                retrieval_host,published_at_utc,available_at_utc,
                availability_basis,recovery_target_id,recovery_candidate_id,
                matching_confidence,recovery_evidence_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,'available','AVAILABLE',?,?,?,?,?,?,?,?,?,?)""",
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
        source = conn.execute(
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
            conn.execute(
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
            source = conn.execute(
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
        conn.execute(
            """UPDATE artifact_recovery_candidates
                  SET attached_source_id=?,updated_at_utc=? WHERE candidate_id=?""",
            (source["artifact_source_id"], now, candidate_id),
        )
        conn.execute(
            """UPDATE artifact_recovery_targets
                  SET status='CANDIDATE_FOUND',resolved_candidate_id=?,updated_at_utc=?
                WHERE recovery_target_id=?""",
            (candidate_id, now, candidate["recovery_target_id"]),
        )
        blocked = conn.execute(
            """SELECT automation_status FROM artifact_sources
                WHERE artifact_source_id=?""",
            (candidate["blocked_source_id"],),
        ).fetchone()
        if blocked is None or blocked["automation_status"] != "BLOCKED_TERMS":
            raise RuntimeError(
                "Blocked publisher source policy changed during recovery"
            )
        artifact_count_after = int(
            conn.execute("SELECT COUNT(*) FROM document_artifacts").fetchone()[0]
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


def reconcile_recovery_for_source(
    conn: sqlite3.Connection,
    *,
    artifact_source_id: int,
    content_id: int,
    reconciled_at_utc: str | None = None,
) -> dict[str, Any] | None:
    """Finalize recovery only after D03 rating-evidence classification."""

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
) -> list[dict[str, Any]]:
    """Classify extracted direct rating content against its exact retrieval sources.

    Alternate recovery sources are finalized by ``reconcile_recovery_for_source``;
    this path covers already-acquired publisher/exchange documents without
    changing acquisition or recovery state.
    """

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
) -> dict[str, Any] | None:
    """Release a failed selected source so untried source groups remain claimable."""

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
