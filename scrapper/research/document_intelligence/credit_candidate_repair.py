"""Versioned, offline repair proposals for retained credit-section responses.

Repairs are candidates, never admissions.  They retain the original provider
DTO, exact source regions and a field-by-field transformation record.  A new
whole-candidate source review is required even when the change is structural.
"""
from __future__ import annotations

from copy import deepcopy
import re

from .credit_atomic import derive_claims
from .credit_candidate_provider import ATOM_KEYS, validate_atom
from .credit_v1 import FIELD_NAMES, attach_evidence
from .evidence import canonical_hash


VERSION = "credit-correction-proposals/1"
FINANCIAL_SCOPE_FIELDS = {
    "assessment.revenue_and_profitability",
    "assessment.debt_and_leverage",
    "assessment.interest_coverage_and_debt_protection",
}


def _region_text(region):
    if region["kind"] == "table":
        return "\n".join(" | ".join(str(cell) for cell in row) for row in region.get("rows", []))
    return region.get("text", "")


def explicit_accounting_scope(atom, regions):
    """Return scope only when the claim's applicable heading/header says it."""
    wanted = (atom.get("accounting_scope") or "").lower()
    if wanted not in {"standalone", "consolidated"}:
        return None
    if atom.get("field") == "assessment.accounting_approach":
        own_text = (atom.get("statement", "") + " " + " ".join(_region_text(r) for r in regions)).lower()
        return wanted if re.search(rf"\b{wanted}(?:ation)?\b", own_text) else None
    if atom.get("field") not in FINANCIAL_SCOPE_FIELDS:
        return None
    for region in regions:
        heading = region.get("heading", "")
        text = _region_text(region)
        if region.get("kind") != "table" and not re.match(r"^key financial indicators?\b", heading, re.I):
            continue
        # Only the heading/header area applies deterministically.  A later
        # mention cannot leak scope to another statement in a long passage.
        header = (heading + "\n" + "\n".join(text.splitlines()[:3])).lower()
        if re.search(rf"\b{wanted}\b", header):
            return wanted
    return None


def classification_family(field):
    mapping = {
        "capex": {"assessment.capex_and_execution_risks"},
        "debt_and_leverage": {"assessment.debt_and_leverage", "assessment.interest_coverage_and_debt_protection"},
        "working_capital": {"assessment.working_capital_intensity"},
        "liquidity": {name for name in FIELD_NAMES if name.startswith("liquidity.")},
        "financial_performance": {
            "assessment.revenue_and_profitability", "assessment.revenue_explanations",
            "assessment.profitability_explanations",
        },
        "business_strengths_and_risks": {
            "assessment.operational_strengths", "assessment.business_weaknesses",
            "assessment.concentration_risks", "assessment.raw_material_and_forex_risk",
            "assessment.pricing_and_cost_pass_through", "assessment.order_book_and_execution",
            "assessment.group_support",
        },
    }
    return next((family for family, fields in mapping.items() if field in fields), None)


def _period_changes(atom):
    changes = {}
    period, kind = atom.get("period"), atom.get("period_kind")
    if period is None and kind == "as_of":
        changes["period_kind"] = "unknown"
    elif period is None and kind == "historical_period":
        match = re.search(r"\bFY\s?20\d{2}\b", atom.get("statement", ""), re.I)
        if match:
            changes["period"] = match.group(0).replace(" ", "").upper()
        else:
            changes["period_kind"] = "unknown"
    if kind == "expectation_horizon" and atom.get("statement_kind") == "assessment":
        changes["statement_kind"] = "expectation"
    return changes


def propose_correction(original, regions, *, assignment_id, response_sha256,
                       json_pointer, parent_claim_id, original_atomic_key=None):
    """Create a safe proposal or return None when no deterministic repair exists."""
    corrected = deepcopy(original)
    changes = _period_changes(corrected)
    corrected.update(changes)

    scope = corrected.get("accounting_scope")
    if scope is not None and explicit_accounting_scope(corrected, regions) != scope.lower():
        changes["accounting_scope"] = None
        corrected["accounting_scope"] = None

    statement = corrected.get("statement", "")
    if corrected.get("field") == "assessment.debt_and_leverage" and re.search(r"\bcapex\b|capital expenditure", statement, re.I):
        changes["field"] = "assessment.capex_and_execution_risks"
        corrected["field"] = changes["field"]
        corrected["disclosure_category"] = "assessment"
        if re.search(r"\bincurred capex\b", statement, re.I) and re.search(r"\blined up\b", statement, re.I):
            actual = statement.split(", while another", 1)[0].rstrip(". ") + "."
            changes["statement"] = actual
            corrected["statement"] = actual
    if (corrected.get("field") == "assessment.business_weaknesses"
            and re.search(r"\b(?:inventory|receivable|debtor|working[ -]capital)\s+(?:days|cycle|levels?)\b", statement, re.I)):
        changes["field"] = "assessment.working_capital_intensity"
        corrected["field"] = changes["field"]
        corrected["disclosure_category"] = "assessment"

    # An unchanged DTO can still need a new identity when the old validator
    # incorrectly rejected an explicitly future but undated expectation.
    contract_migration = (
        original.get("period") is None
        and original.get("period_kind") == "expectation_horizon"
        and not changes
    )
    if not changes and not contract_migration:
        return None
    validate_atom(corrected)
    if corrected.get("accounting_scope") is not None and explicit_accounting_scope(corrected, regions) is None:
        raise ValueError("Correction retains unsupported accounting scope")

    support = [deepcopy(ev) for region in regions for ev in region.get("evidence", [])]
    semantic = {key: deepcopy(corrected[key]) for key in ATOM_KEYS if key not in {"id", "support_region_ids"}}
    identity = {
        "version": VERSION,
        "assignment_id": assignment_id,
        "original_id": original.get("id"),
        "original_candidate_sha256": canonical_hash(original),
        "corrected_semantic": semantic,
    }
    key = f"correction:v1:{canonical_hash(identity)[:20]}"
    return {
        **semantic,
        "atomic_key": key,
        "parent_claim_id": parent_claim_id,
        "parent_candidate_sha256": canonical_hash(original),
        "fields": [corrected["field"]],
        "derivation_basis": "offline_contract_correction_proposal",
        "support": support,
        "source_region_ids": list(original["support_region_ids"]),
        "transformation_provenance": {
            "schema_version": VERSION,
            "assignment_id": assignment_id,
            "response_sha256": response_sha256,
            "json_pointer": json_pointer,
            "original_id": original.get("id"),
            "original_candidate_sha256": canonical_hash(original),
            "original_atomic_key": original_atomic_key,
            "changes": {name: {"from": original.get(name), "to": corrected.get(name)} for name in sorted(changes)},
            "contract_migration_only": contract_migration,
            "automatic_admission": False,
        },
    }


def validate_review_packet(packet, reviews):
    if (reviews.get("schema_version") != "credit-correction-source-review/1"
            or reviews.get("source_sha256") != packet["source_sha256"]
            or reviews.get("canonical_sha256") != packet["canonical_sha256"]
            or reviews.get("proposal_set_sha256") != canonical_hash(packet["candidates"])):
        raise ValueError("Correction source-review identity changed")
    expected = {candidate["atomic_key"] for candidate in packet["candidates"]}
    if set(reviews.get("reviews", {})) != expected:
        raise ValueError("Every correction proposal requires an explicit source-review decision")
    return reviews["reviews"]


def derive_corrected_claims(packet, parents, context, extraction, *, reviews=None):
    candidate_packet = deepcopy(packet)
    candidate_packet["reviews"] = validate_review_packet(packet, reviews) if reviews else {}
    claims = derive_claims(candidate_packet, parents, context, extraction)
    for claim, proposal in zip(claims, packet["candidates"]):
        claim["origin"]["mode"] = "offline_contract_correction_proposal"
        claim["transformation_provenance"] = deepcopy(proposal["transformation_provenance"])
    return claims
