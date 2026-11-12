"""DA-06 target construction, identity enrichment and durable target loading.

Transaction boundaries and target accounting retain the original registry contract.
The optional hook object preserves old facade patch points per invocation.
"""
from __future__ import annotations
import json
import sqlite3
from collections import Counter
from typing import Any, Iterable
from urllib.parse import urlsplit
from ....core.access_policy import BLOCKED_TERMS_HOSTS, host_matches_policy
from ....documents.recovery import RECOVERY_CONTRACT_VERSION, DocumentOriginator, RecoveryTarget, RecoveryTargetStatus
from ...repo import utc_now
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)

def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _originator(host: str, *, _hooks: Any = None) -> DocumentOriginator | None:
    DocumentOriginator = getattr(_hooks, "DocumentOriginator", globals()["DocumentOriginator"])
    normalized = host.lower().rstrip(".")
    if normalized == "crisil.com" or normalized.endswith(".crisil.com"):
        return DocumentOriginator.CRISIL
    if normalized == "crisilratings.com" or normalized.endswith(".crisilratings.com"):
        return DocumentOriginator.CRISIL
    if normalized == "icra.in" or normalized.endswith(".icra.in"):
        return DocumentOriginator.ICRA
    return None


@atomic_repository('documents')
def _blocked_artifacts(
    conn: sqlite3.Connection,
    artifact_ids: tuple[int, ...] | None = None,
    *, _hooks: Any = None,
) -> tuple[sqlite3.Row, ...]:
    BLOCKED_TERMS_HOSTS = getattr(_hooks, "BLOCKED_TERMS_HOSTS", globals()["BLOCKED_TERMS_HOSTS"])
    host_matches_policy = getattr(_hooks, "host_matches_policy", globals()["host_matches_policy"])
    urlsplit = getattr(_hooks, "urlsplit", globals()["urlsplit"])
    artifact_filter = ""
    parameters: tuple[Any, ...] = ()
    if artifact_ids is not None:
        placeholders = ",".join("?" for _ in artifact_ids)
        artifact_filter = f" AND artifact.artifact_id IN ({placeholders})"
        parameters = artifact_ids
    rows = execute(conn,
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


@atomic_repository('documents')
def _identities(conn: sqlite3.Connection, company_id: int, *, _hooks: Any = None) -> dict[str, tuple[str, ...]]:
    sqlite3 = getattr(_hooks, "sqlite3", globals()["sqlite3"])
    identifiers = execute(conn,
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
        rows = execute(conn,
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


@atomic_repository('documents')
def _rating_context(conn: sqlite3.Connection, artifact_id: int, *, _hooks: Any = None) -> dict[str, str | None]:
    row = execute(conn,
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


@atomic_repository('documents')
def _already_satisfied(conn: sqlite3.Connection, artifact_id: int, *, _hooks: Any = None) -> bool:
    return execute(conn,
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


@atomic_repository('documents')
def _target_from_row(
    conn: sqlite3.Connection, row: sqlite3.Row, originator: DocumentOriginator,
    *, _hooks: Any = None,
) -> RecoveryTarget:
    RecoveryTarget = getattr(_hooks, "RecoveryTarget", globals()["RecoveryTarget"])
    _identities = getattr(_hooks, "_identities", globals()["_identities"])
    _rating_context = getattr(_hooks, "_rating_context", globals()["_rating_context"])
    urlsplit = getattr(_hooks, "urlsplit", globals()["urlsplit"])
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


@atomic_repository('documents')
def build_recovery_targets(
    conn: sqlite3.Connection,
    *,
    artifact_type: str = "credit_rating",
    dry_run: bool = False,
    observed_at_utc: str | None = None,
    artifact_ids: Iterable[int] | None = None,
    _hooks: Any = None,
) -> dict[str, Any]:
    """Build one durable target per blocked logical artifact, idempotently."""
    Counter = getattr(_hooks, "Counter", globals()["Counter"])
    RECOVERY_CONTRACT_VERSION = getattr(_hooks, "RECOVERY_CONTRACT_VERSION", globals()["RECOVERY_CONTRACT_VERSION"])
    RecoveryTarget = getattr(_hooks, "RecoveryTarget", globals()["RecoveryTarget"])
    RecoveryTargetStatus = getattr(_hooks, "RecoveryTargetStatus", globals()["RecoveryTargetStatus"])
    _already_satisfied = getattr(_hooks, "_already_satisfied", globals()["_already_satisfied"])
    _blocked_artifacts = getattr(_hooks, "_blocked_artifacts", globals()["_blocked_artifacts"])
    _json = getattr(_hooks, "_json", globals()["_json"])
    _originator = getattr(_hooks, "_originator", globals()["_originator"])
    _target_from_row = getattr(_hooks, "_target_from_row", globals()["_target_from_row"])
    sqlite3 = getattr(_hooks, "sqlite3", globals()["sqlite3"])
    urlsplit = getattr(_hooks, "urlsplit", globals()["urlsplit"])
    utc_now = getattr(_hooks, "utc_now", globals()["utc_now"])

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
        int(row[0]) for row in execute(conn,
            "SELECT artifact_id FROM artifact_recovery_targets"
        )
    }
    created_count = sum(
        1 for target, status in target_plans
        if status is RecoveryTargetStatus.PENDING and target.artifact_id not in existing_ids
    )
    if not dry_run:
        with repository_transaction(conn):
            if target_plans:
                planned_ids = tuple(target.artifact_id for target, _ in target_plans)
                placeholders = ",".join("?" for _ in planned_ids)
                execute(conn,
                    f"DELETE FROM artifact_recovery_quarantine WHERE artifact_id IN ({placeholders})",
                    planned_ids,
                )
            for artifact_id, reason, evidence in quarantines:
                execute(conn,
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
                execute(conn,
                    statement(conn, """INSERT INTO artifact_recovery_targets
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
                         updated_at_utc=excluded.updated_at_utc""", """INSERT INTO artifact_recovery_targets
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
                         basic_rating_coverage=CASE WHEN artifact_recovery_targets.basic_rating_coverage IS NULL THEN NULL ELSE GREATEST(artifact_recovery_targets.basic_rating_coverage,excluded.basic_rating_coverage) END,
                         updated_at_utc=excluded.updated_at_utc"""),
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
                execute(conn,
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


@atomic_repository('documents')
def load_recovery_target(conn: sqlite3.Connection, recovery_target_id: int, *, _hooks: Any = None) -> RecoveryTarget:
    DocumentOriginator = getattr(_hooks, "DocumentOriginator", globals()["DocumentOriginator"])
    RecoveryTarget = getattr(_hooks, "RecoveryTarget", globals()["RecoveryTarget"])
    json = getattr(_hooks, "json", globals()["json"])
    row = execute(conn,
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
