"""Strict FG-00 contracts and deterministic P0 governance rules.

This module deliberately contains no model training and no document fetching.
It separates source facts, deterministic derivations, proposals, approvals and
unknown values so a missing historical universe can never manufacture a
negative label.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence


class DecisionUniverseState(str, Enum):
    IN_UNIVERSE = "IN_UNIVERSE"
    OUT_OF_UNIVERSE = "OUT_OF_UNIVERSE"
    UNKNOWN = "UNKNOWN"
    QUARANTINED = "QUARANTINED"


class IdentityResolutionState(str, Enum):
    RESOLVED = "RESOLVED"
    RESOLVED_HISTORICAL_ALIAS = "RESOLVED_HISTORICAL_ALIAS"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


class ArchetypeApprovalState(str, Enum):
    APPROVED = "APPROVED"
    PROVISIONAL = "PROVISIONAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNKNOWN = "UNKNOWN"


class AllowedArchetype(str, Enum):
    OPERATING_COMPANY = "OPERATING_COMPANY"
    PROJECT_EPC = "PROJECT_EPC"
    MANUFACTURING = "MANUFACTURING"
    BANK = "BANK"
    NBFC = "NBFC"
    INSURER = "INSURER"
    IT_SERVICES = "IT_SERVICES"
    PHARMA = "PHARMA"
    CONSUMER = "CONSUMER"
    OTHER = "OTHER"


class EligibilityState(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    EXCLUDED_NOT_YET_LISTED = "EXCLUDED_NOT_YET_LISTED"
    EXCLUDED_INACTIVE = "EXCLUDED_INACTIVE"
    EXCLUDED_INSTRUMENT_SCOPE = "EXCLUDED_INSTRUMENT_SCOPE"
    EXCLUDED_IDENTITY_UNRESOLVED = "EXCLUDED_IDENTITY_UNRESOLVED"
    EXCLUDED_RESULT_NOT_AVAILABLE = "EXCLUDED_RESULT_NOT_AVAILABLE"
    EXCLUDED_SOURCE_DATA_FAILURE = "EXCLUDED_SOURCE_DATA_FAILURE"
    EXCLUDED_NOT_IN_DECISION_UNIVERSE = "EXCLUDED_NOT_IN_DECISION_UNIVERSE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class P0SelectionState(str, Enum):
    SELECTED = "SELECTED"
    CANDIDATE = "CANDIDATE"
    NOT_SELECTED = "NOT_SELECTED"
    BLOCKED_APPROVAL = "BLOCKED_APPROVAL"


def derive_eligibility(
    *,
    cutoff_date: str,
    listing_date: str | None,
    company_status: str | None,
    instrument_in_scope: bool | None,
    identity_state: IdentityResolutionState,
    result_available: bool | None,
    technical_source_failure: bool = False,
    universe_state: DecisionUniverseState = DecisionUniverseState.UNKNOWN,
) -> tuple[EligibilityState, tuple[str, ...]]:
    """Classify eligibility while keeping technical missingness explicit."""

    if identity_state not in {
        IdentityResolutionState.RESOLVED,
        IdentityResolutionState.RESOLVED_HISTORICAL_ALIAS,
    }:
        return (
            EligibilityState.EXCLUDED_IDENTITY_UNRESOLVED,
            ("IDENTITY_NOT_RESOLVED",),
        )
    if listing_date and listing_date > cutoff_date:
        return EligibilityState.EXCLUDED_NOT_YET_LISTED, ("LISTING_AFTER_CUTOFF",)
    if company_status and company_status.lower() in {"inactive", "delisted"}:
        return EligibilityState.EXCLUDED_INACTIVE, ("INACTIVE_AT_CUTOFF",)
    if instrument_in_scope is False:
        return EligibilityState.EXCLUDED_INSTRUMENT_SCOPE, ("INSTRUMENT_OUT_OF_SCOPE",)
    if technical_source_failure:
        return EligibilityState.EXCLUDED_SOURCE_DATA_FAILURE, ("TECHNICAL_SOURCE_FAILURE",)
    if result_available is False:
        return EligibilityState.EXCLUDED_RESULT_NOT_AVAILABLE, ("RESULT_NOT_AVAILABLE",)
    if universe_state == DecisionUniverseState.OUT_OF_UNIVERSE:
        return (
            EligibilityState.EXCLUDED_NOT_IN_DECISION_UNIVERSE,
            ("NOT_IN_DECISION_UNIVERSE",),
        )
    if universe_state != DecisionUniverseState.IN_UNIVERSE:
        return EligibilityState.REVIEW_REQUIRED, ("UNIVERSE_MEMBERSHIP_NOT_GOVERNED",)
    return EligibilityState.ELIGIBLE, ()


@dataclass(frozen=True)
class IdentityResolution:
    company_id: int | None
    state: IdentityResolutionState
    method: str
    resolved_symbol: str | None = None


@dataclass(frozen=True)
class ArchetypeProposal:
    archetype: AllowedArchetype | None
    state: ArchetypeApprovalState
    mapping_rule_id: str
    mapping_basis: str
    review_reason: str | None = None


@dataclass(frozen=True)
class CompanyQuarterGovernance:
    company_id: int | None
    quarter: str
    historical_symbol: str | None
    isin: str | None
    bse_code: str | None
    decision_universe_state: DecisionUniverseState
    outcome_label: int | None
    analysis_archetype: AllowedArchetype | None
    identity_resolution_state: IdentityResolutionState
    label_source_id: str | None
    label_source_version: str | None
    universe_source_id: str | None
    universe_source_version: str | None
    archetype_mapping_version: str | None
    archetype_approval_state: ArchetypeApprovalState
    eligibility_state: EligibilityState
    eligibility_reasons: tuple[str, ...]
    p0_selection_state: P0SelectionState
    created_at_utc: str
    governance_version: str

    def __post_init__(self) -> None:
        if self.outcome_label not in (None, 0, 1):
            raise ValueError("outcome_label must be 0, 1, or NULL")
        if (
            self.outcome_label == 0
            and self.decision_universe_state != DecisionUniverseState.IN_UNIVERSE
        ):
            raise ValueError(
                "outcome_label=0 requires decision_universe_state=IN_UNIVERSE"
            )
        if self.identity_resolution_state in {
            IdentityResolutionState.RESOLVED,
            IdentityResolutionState.RESOLVED_HISTORICAL_ALIAS,
        } and self.company_id is None:
            raise ValueError("A resolved identity requires company_id")
        if (
            self.archetype_approval_state == ArchetypeApprovalState.APPROVED
            and self.analysis_archetype is None
        ):
            raise ValueError("An approved archetype cannot be NULL")
        if not self.governance_version.strip():
            raise ValueError("governance_version is required")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "decision_universe_state",
            "analysis_archetype",
            "identity_resolution_state",
            "archetype_approval_state",
            "eligibility_state",
            "p0_selection_state",
        ):
            value = payload[key]
            payload[key] = value.value if isinstance(value, Enum) else value
        payload["eligibility_reasons"] = list(self.eligibility_reasons)
        return payload


def assign_outcome_label(
    *,
    is_focus_member: bool,
    universe_state: DecisionUniverseState,
    technical_source_failure: bool = False,
) -> int | None:
    """Derive a label without turning missingness into a negative."""

    if is_focus_member:
        return 1
    if technical_source_failure:
        return None
    if universe_state == DecisionUniverseState.IN_UNIVERSE:
        return 0
    return None


def _effective_identifier_clause(as_of_date: str | None) -> tuple[str, tuple[str, ...]]:
    if as_of_date is None:
        return "", ()
    return (
        " AND (valid_from IS NULL OR valid_from<=?)"
        " AND (valid_to IS NULL OR valid_to>=?)",
        (as_of_date, as_of_date),
    )


def _normalize_company_name(value: str) -> str:
    """Normalize only legal-form differences when joining verified exchange facts."""

    normalized = re.sub(r"[^A-Z0-9]+", " ", value.upper())
    suffixes = {"LIMITED", "LTD", "PRIVATE", "PVT", "INDIA", "CO", "COMPANY"}
    return " ".join(token for token in normalized.split() if token not in suffixes)


def resolve_verified_exchange_identifier(
    conn: sqlite3.Connection,
    *,
    exchange: str,
    identifier: str,
) -> IdentityResolution:
    """Resolve one explicitly verified NSE symbol or BSE scrip to one company.

    The exchange master is evidence for the symbol/scrip and ISIN, while the
    existing Screener-origin company record remains the durable target.  A
    name match is accepted only when it yields exactly one company; otherwise
    it stays quarantined rather than creating or merging identities.
    """

    normalized_exchange = exchange.strip().upper()
    normalized_identifier = identifier.strip().upper()
    if normalized_exchange not in {"NSE", "BSE"} or not normalized_identifier:
        return IdentityResolution(None, IdentityResolutionState.UNRESOLVED, "invalid_exchange_identifier")
    column = "nse_symbol" if normalized_exchange == "NSE" else "bse_scrip_code"
    rows = conn.execute(
        f"""SELECT DISTINCT current_name_raw FROM exchange_securities
             WHERE exchange=? AND UPPER({column})=? AND is_present=1
               AND current_isin IS NOT NULL""",
        (normalized_exchange, normalized_identifier),
    ).fetchall()
    names = {
        _normalize_company_name(str(row[0]))
        for row in rows
        if row[0] and _normalize_company_name(str(row[0]))
    }
    if len(names) != 1:
        return IdentityResolution(None, IdentityResolutionState.UNRESOLVED, "exchange_identifier_not_unique")
    expected_name = next(iter(names))
    company_ids = {
        int(row[0])
        for row in conn.execute("SELECT company_id,current_legal_name FROM companies")
        if _normalize_company_name(str(row[1])) == expected_name
    }
    if len(company_ids) != 1:
        state = IdentityResolutionState.AMBIGUOUS if len(company_ids) > 1 else IdentityResolutionState.UNRESOLVED
        return IdentityResolution(None, state, "exchange_master_company_name_not_unique")
    return IdentityResolution(
        company_ids.pop(),
        IdentityResolutionState.RESOLVED_HISTORICAL_ALIAS,
        "verified_exchange_master_identifier",
        normalized_identifier,
    )


def resolve_historical_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    as_of_date: str | None = None,
) -> IdentityResolution:
    """Resolve an NSE source symbol, including a recorded historical alias.

    Exact source facts win. A historical alias is followed only through the
    official symbol-change table and must resolve to exactly one durable
    company. Multiple candidates are quarantined as ambiguous.
    """

    source_symbol = symbol.strip().upper()
    if not source_symbol:
        return IdentityResolution(None, IdentityResolutionState.UNRESOLVED, "empty")
    clause, params = _effective_identifier_clause(as_of_date)
    rows = conn.execute(
        "SELECT DISTINCT company_id FROM security_identifiers "
        "WHERE identifier_type='NSE' AND UPPER(identifier_value)=?" + clause,
        (source_symbol, *params),
    ).fetchall()
    exact_ids = {int(row[0]) for row in rows}
    if len(exact_ids) == 1:
        return IdentityResolution(
            exact_ids.pop(), IdentityResolutionState.RESOLVED, "exact_nse_symbol", source_symbol
        )
    if len(exact_ids) > 1:
        return IdentityResolution(
            None, IdentityResolutionState.AMBIGUOUS, "duplicate_exact_nse_symbol"
        )

    alias_rows = conn.execute(
        """SELECT DISTINCT UPPER(new_value) AS new_symbol
             FROM exchange_symbol_changes
            WHERE exchange='NSE' AND change_kind='symbol'
              AND UPPER(old_value)=?
              AND (? IS NULL OR effective_date<=?)""",
        (source_symbol, as_of_date, as_of_date),
    ).fetchall()
    alias_ids: set[int] = set()
    alias_symbols: set[str] = set()
    for row in alias_rows:
        new_symbol = str(row[0] or "").strip().upper()
        if not new_symbol:
            continue
        alias_symbols.add(new_symbol)
        matches = conn.execute(
            """SELECT DISTINCT company_id FROM security_identifiers
                WHERE identifier_type='NSE' AND UPPER(identifier_value)=?""",
            (new_symbol,),
        ).fetchall()
        alias_ids.update(int(match[0]) for match in matches)
    if len(alias_ids) == 1:
        return IdentityResolution(
            alias_ids.pop(),
            IdentityResolutionState.RESOLVED_HISTORICAL_ALIAS,
            "official_nse_symbol_change",
            next(iter(alias_symbols)) if len(alias_symbols) == 1 else None,
        )
    if alias_ids or len(alias_symbols) > 1:
        return IdentityResolution(
            None, IdentityResolutionState.AMBIGUOUS, "ambiguous_official_symbol_change"
        )
    return IdentityResolution(
        None, IdentityResolutionState.UNRESOLVED, "no_verified_identity_match"
    )


def _classification_tokens(*values: str | None) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        normalized = re.sub(r"[^A-Z0-9]+", " ", (value or "").upper()).strip()
        if normalized:
            tokens.add(normalized)
    return tokens


def propose_archetype(
    *,
    sector: str | None = None,
    industry: str | None = None,
    subsector: str | None = None,
    approved_source: bool = False,
) -> ArchetypeProposal:
    """Apply versioned classification rules; proposals are never self-approved."""

    values = _classification_tokens(sector, industry, subsector)
    joined = " | ".join(sorted(values))
    if not values:
        return ArchetypeProposal(
            None,
            ArchetypeApprovalState.UNKNOWN,
            "FG00-ARCH-000",
            "no governed business classification",
            "No sector, industry, or subsector source fact is available",
        )

    rules: tuple[tuple[str, tuple[str, ...], AllowedArchetype], ...] = (
        ("FG00-ARCH-010", ("BANK", "BANKS", "BANKING"), AllowedArchetype.BANK),
        (
            "FG00-ARCH-020",
            ("NBFC", "NON BANKING FINANCE", "ASSET MTG", "HOUSING FINANCE"),
            AllowedArchetype.NBFC,
        ),
        (
            "FG00-ARCH-030",
            ("INSURANCE", "LIFE INSURANCE", "GENERAL INSURANCE"),
            AllowedArchetype.INSURER,
        ),
        (
            "FG00-ARCH-040",
            ("IT", "IT SERVICES", "INFORMATION TECHNOLOGY SERVICES"),
            AllowedArchetype.IT_SERVICES,
        ),
        (
            "FG00-ARCH-050",
            ("PHARMA", "PHARMACEUTICALS", "PHARMACEUTICAL"),
            AllowedArchetype.PHARMA,
        ),
        (
            "FG00-ARCH-060",
            ("EPC", "PROJECT EPC", "CONSTRUCTION EPC", "PROJECT ENGINEERING"),
            AllowedArchetype.PROJECT_EPC,
        ),
        (
            "FG00-ARCH-070",
            (
                "MANUFACTURING",
                "AUTO ANCILLIARY",
                "AUTO ANCILLARY",
                "CHEMICALS",
                "EMS",
                "INDUSTRIAL PRODUCTS",
                "METALS",
                "PACKAGING",
                "PLYWOOD",
                "TEXTILES",
                "WIRES",
            ),
            AllowedArchetype.MANUFACTURING,
        ),
        (
            "FG00-ARCH-080",
            ("CONSUMER", "FMCG", "RETAIL", "ALCOHOL"),
            AllowedArchetype.CONSUMER,
        ),
    )
    def term_matches(value: str, term: str) -> bool:
        return bool(re.search(rf"(?:^| ){re.escape(term)}(?: |$)", value))

    matches = [
        (rule_id, archetype)
        for rule_id, terms, archetype in rules
        if any(term_matches(value, term) for value in values for term in terms)
    ]
    distinct = {match[1] for match in matches}
    if len(distinct) != 1:
        return ArchetypeProposal(
            None,
            ArchetypeApprovalState.REVIEW_REQUIRED,
            "FG00-ARCH-900",
            joined,
            "Classification is ambiguous or has no deterministic governed rule",
        )
    rule_id = next(rule for rule, archetype in matches if archetype in distinct)
    archetype = distinct.pop()
    return ArchetypeProposal(
        archetype,
        (
            ArchetypeApprovalState.APPROVED
            if approved_source
            else ArchetypeApprovalState.PROVISIONAL
        ),
        rule_id,
        joined,
        None if approved_source else "Deterministic proposal awaits owner approval",
    )


def stable_payload_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _selection_key(row: Mapping[str, Any], seed: int) -> tuple[Any, ...]:
    artifact_shape = sum(
        int(bool(row.get(key)))
        for key in (
            "transcript_artifact_count",
            "presentation_artifact_count",
            "credit_rating_artifact_count",
        )
    )
    diversity = (
        int(bool(row.get("alternate_rating_recovery_needed"))),
        int(bool(row.get("ocr_likelihood"))),
        int(bool(row.get("recording_available"))),
    )
    tie = hashlib.sha256(
        f"{seed}:{row.get('company_id')}:{row.get('quarter')}".encode("utf-8")
    ).hexdigest()
    return (-artifact_shape, *(-item for item in diversity), tie)


def _eligible_for_p0(row: Mapping[str, Any]) -> bool:
    return (
        row.get("identity_resolution_state")
        in {
            IdentityResolutionState.RESOLVED.value,
            IdentityResolutionState.RESOLVED_HISTORICAL_ALIAS.value,
        }
        and row.get("decision_universe_state") == DecisionUniverseState.IN_UNIVERSE.value
        and row.get("outcome_label") in (0, 1)
        and row.get("analysis_archetype") in {item.value for item in AllowedArchetype}
        and row.get("archetype_approval_state") == ArchetypeApprovalState.APPROVED.value
        and row.get("quarter") in {"FY2026-Q4", "FY2027-Q1"}
    )


def select_p0_companies(
    rows: Iterable[Mapping[str, Any]],
    *,
    seed: int,
    target_count: int = 40,
    positive_target: int = 20,
) -> list[dict[str, Any]]:
    """Select a stable, label-balanced P0 from already governed rows."""

    if not 30 <= target_count <= 50:
        raise ValueError("P0 target_count must be between 30 and 50")
    candidates = [dict(row) for row in rows if _eligible_for_p0(row)]
    positives = sorted(
        (row for row in candidates if row["outcome_label"] == 1),
        key=lambda row: _selection_key(row, seed),
    )
    negatives = sorted(
        (row for row in candidates if row["outcome_label"] == 0),
        key=lambda row: _selection_key(row, seed),
    )
    positive_count = min(positive_target, len(positives))
    negative_count = target_count - positive_count
    if positive_count < 1 or len(negatives) < negative_count:
        raise ValueError("Insufficient governed positive/negative rows for P0")
    selected = positives[:positive_count] + negatives[:negative_count]
    if len({int(row["company_id"]) for row in selected}) != len(selected):
        raise ValueError("P0 selection must contain unique companies")
    return sorted(selected, key=lambda row: (int(row["company_id"]), row["quarter"]))


def validate_p0_manifest(
    companies: Sequence[Mapping[str, Any]],
    *,
    artifact_company: Mapping[int, int],
    focus_registry_keys: set[tuple[int, str]],
) -> dict[str, bool]:
    """Return every FG-00 invariant; callers decide whether to fail closed."""

    pairs = [(row.get("company_id"), row.get("quarter")) for row in companies]
    labels = [row.get("outcome_label") for row in companies]
    artifact_ids: list[int] = []
    recordings: list[int] = []
    ownership_ok = True
    for row in companies:
        grouped = row.get("artifact_ids") or {}
        for kind in ("transcripts", "presentations", "credit_ratings"):
            for raw_id in grouped.get(kind, []):
                artifact_id = int(raw_id)
                artifact_ids.append(artifact_id)
                ownership_ok = ownership_ok and artifact_company.get(artifact_id) == int(
                    row["company_id"]
                )
        recordings.extend(int(value) for value in grouped.get("recordings", []))
    archetypes = {
        row.get("analysis_archetype") for row in companies if row.get("analysis_archetype")
    }
    source_refs = all(
        row.get("label_source")
        and row.get("universe_source")
        and row.get("archetype_mapping_version")
        for row in companies
    )
    return {
        "no_duplicate_company_quarter": len(pairs) == len(set(pairs)),
        "negatives_in_universe": all(
            row.get("outcome_label") != 0
            or row.get("decision_universe_state") == DecisionUniverseState.IN_UNIVERSE.value
            for row in companies
        ),
        "positives_in_focus_registry": all(
            row.get("outcome_label") != 1
            or (int(row["company_id"]), str(row["quarter"])) in focus_registry_keys
            for row in companies
        ),
        "unknown_universe_has_null_label": all(
            row.get("decision_universe_state") != DecisionUniverseState.UNKNOWN.value
            or row.get("outcome_label") is None
            for row in companies
        ),
        "identities_resolved": all(
            row.get("identity_resolution_state")
            in {
                IdentityResolutionState.RESOLVED.value,
                IdentityResolutionState.RESOLVED_HISTORICAL_ALIAS.value,
            }
            for row in companies
        ),
        "archetypes_approved": all(
            row.get("archetype_approval_state") == ArchetypeApprovalState.APPROVED.value
            for row in companies
        ),
        "company_count_30_50": 30 <= len({row.get("company_id") for row in companies}) <= 50,
        "positive_and_negative": 0 in labels and 1 in labels,
        "at_least_10_each_label": labels.count(0) >= 10 and labels.count(1) >= 10,
        "at_least_3_archetypes": len(archetypes) >= 3,
        "artifact_ownership": ownership_ok,
        "recordings_excluded": not recordings,
        "sources_versioned": source_refs,
        "artifact_ids_frozen": bool(artifact_ids),
    }
