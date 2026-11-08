"""Deterministic, explainable matching for DA-06 recovery candidates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Mapping
from urllib.parse import urlsplit

from ..core.access_policy import is_blocked_terms_target
from ..sources.documents.recovery_common import OfficialCandidateObservation
from .recovery import (
    DocumentOriginator,
    RecoveredContentClass,
    RecoveryCandidate,
    RecoveryConfidence,
    RecoverySourceType,
    RecoveryTarget,
)


MATCHER_VERSION = "1.0.0"
_COMPANY_SUFFIXES = re.compile(
    r"\b(?:limited|ltd|private|pvt|plc|company|co)\b", re.IGNORECASE
)
_TOKEN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class CandidateDecision:
    confidence: RecoveryConfidence
    features: Mapping[str, Any]
    rejection_reason: str | None


def _normalized_name(value: str | None) -> str:
    text = _COMPANY_SUFFIXES.sub(" ", value or "")
    return " ".join(_TOKEN.sub(" ", text.lower()).split())


def _normalized_value(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return "".join(character for character in value.upper() if character.isalnum())


def _candidate_agency(observation: OfficialCandidateObservation) -> str | None:
    if observation.document_originator:
        return observation.document_originator.upper()
    haystack = " ".join(
        [observation.document_title or "", json.dumps(observation.source_evidence)]
    ).upper()
    if "CRISIL" in haystack:
        return "CRISIL"
    if re.search(r"\bICRA\b", haystack):
        return "ICRA"
    for agency in ("CARE", "INDIA RATINGS", "ACUITE", "BRICKWORK", "INFOMERICS"):
        if agency in haystack:
            return agency
    return None


def _identifier_features(
    target: RecoveryTarget, observation: OfficialCandidateObservation
) -> tuple[bool | None, str, tuple[str, ...]]:
    target_values = {
        "ISIN": {_normalized_value(item) for item in target.isins},
        "NSE": {_normalized_value(item) for item in target.nse_symbols},
        "BSE": {_normalized_value(item) for item in target.bse_codes},
        "company_id": {str(target.company_id)},
    }
    candidate_values = {
        kind: {_normalized_value(str(item)) for item in values}
        for kind, values in observation.company_identifiers.items()
    }
    # A value used to scope an exchange query is not evidence that the returned
    # row identified the same company. Current adapters explicitly record which
    # identifier kinds were actually present in the response.
    query_kind = str(observation.source_evidence.get("query_identifier_kind") or "")
    query_value = _normalized_value(
        str(observation.source_evidence.get("query_identifier_value") or "")
    )
    returned_kinds = {
        str(kind) for kind in observation.source_evidence.get(
            "returned_identifier_kinds", ()
        )
    }
    if query_kind and query_kind not in returned_kinds and query_value:
        values = candidate_values.get(query_kind, set())
        candidate_values[query_kind] = {value for value in values if value != query_value}
    conflicts: list[str] = []
    matches: list[str] = []
    for kind in ("ISIN", "NSE", "BSE", "company_id"):
        expected = {value for value in target_values.get(kind, set()) if value}
        actual = {value for value in candidate_values.get(kind, set()) if value}
        if not expected or not actual:
            continue
        if expected.intersection(actual):
            matches.append(kind)
        else:
            conflicts.append(kind)
    if conflicts:
        return False, "conflict", tuple(conflicts)
    if "ISIN" in matches or "company_id" in matches:
        return True, "durable_primary", ()
    if matches:
        return True, "exchange_identifier", ()
    return None, "unproven", ()


def _official_host_matches(source_type: RecoverySourceType, host: str) -> bool:
    if source_type in {
        RecoverySourceType.NSE_EXCHANGE_ATTACHMENT,
        RecoverySourceType.NSE_DEBT_DOCUMENT,
    }:
        return host == "nseindia.com" or host.endswith(".nseindia.com")
    if source_type in {
        RecoverySourceType.BSE_EXCHANGE_ATTACHMENT,
        RecoverySourceType.BSE_DEBT_DOCUMENT,
    }:
        return host == "bseindia.com" or host.endswith(".bseindia.com")
    return bool(host)


def match_candidate(
    target: RecoveryTarget,
    observation: OfficialCandidateObservation,
    *,
    discovered_at_utc: str,
) -> RecoveryCandidate:
    """Produce persisted matching features and a conservative confidence tier."""

    host = (urlsplit(observation.candidate_url).hostname or "").lower().rstrip(".")
    attachment_is_pdf = urlsplit(observation.candidate_url).path.lower().endswith(".pdf")
    issuer = str(observation.source_evidence.get("issuer") or "")
    target_names = {
        _normalized_name(target.company_canonical_name),
        *(_normalized_name(alias) for alias in target.company_aliases),
    }
    content_company_match = bool(_normalized_name(issuer)) and _normalized_name(issuer) in target_names
    company_match, identity_basis, identity_conflicts = _identifier_features(target, observation)
    agency = _candidate_agency(observation)
    agency_match = agency == target.document_originator.value if agency else None
    date_distance: int | None = None
    if target.event_date and observation.publication_date:
        date_distance = abs(
            (date.fromisoformat(observation.publication_date)
             - date.fromisoformat(target.event_date)).days
        )
    target_rating = _normalized_value(target.rating)
    candidate_rating = _normalized_value(observation.rating)
    rating_match = (
        target_rating == candidate_rating
        if target_rating is not None and candidate_rating is not None else None
    )
    target_outlook = _normalized_value(target.outlook)
    candidate_outlook = _normalized_value(observation.outlook)
    outlook_match = (
        target_outlook == candidate_outlook
        if target_outlook is not None and candidate_outlook is not None else None
    )
    target_instrument = _normalized_value(target.instrument_or_facility)
    candidate_instrument = _normalized_value(observation.instrument_or_facility)
    instrument_match = (
        target_instrument == candidate_instrument
        if target_instrument is not None and candidate_instrument is not None else None
    )
    title = (observation.document_title or "").lower()
    document_type_match = any(
        phrase in title for phrase in (
            "credit rating", "rating rationale", "rating revision",
            "rating reaffirmation", "rating upgrade", "rating downgrade",
        )
    )
    features: dict[str, Any] = {
        "matcher_version": MATCHER_VERSION,
        "company_identity_match": company_match,
        "company_identity_basis": identity_basis,
        "company_identity_conflicts": list(identity_conflicts),
        "agency_match": agency_match,
        "candidate_agency": agency,
        "date_distance_days": date_distance,
        "rating_match": rating_match,
        "outlook_match": outlook_match,
        "instrument_match": instrument_match,
        "amount_match": None,
        "document_type_match": document_type_match,
        "attachment_is_pdf": attachment_is_pdf,
        "content_company_match": content_company_match,
        "official_retrieval_host": _official_host_matches(observation.source_type, host),
    }

    confidence = RecoveryConfidence.AMBIGUOUS
    reason: str | None = None
    if is_blocked_terms_target(observation.candidate_url):
        confidence = RecoveryConfidence.REJECTED_POLICY
        reason = "candidate retrieval URL is covered by BLOCKED_TERMS"
    elif not features["official_retrieval_host"]:
        confidence = RecoveryConfidence.REJECTED_INVALID
        reason = "candidate host is not official for the declared source type"
    elif company_match is False:
        confidence = RecoveryConfidence.NO_MATCH
        reason = "durable company identifier conflicts"
    elif agency_match is False:
        confidence = RecoveryConfidence.NO_MATCH
        reason = "document originator/CRA conflicts"
    elif date_distance is not None and date_distance > 62:
        confidence = RecoveryConfidence.NO_MATCH
        reason = "candidate is outside the maximum rating-event window"
    elif rating_match is False:
        confidence = RecoveryConfidence.NO_MATCH
        reason = "rating conflicts with the target event"
    elif outlook_match is False:
        confidence = RecoveryConfidence.NO_MATCH
        reason = "outlook conflicts with the target event"
    elif (
        company_match is True and agency_match is True
        and date_distance is not None and date_distance <= 3
        and (rating_match is True or instrument_match is True)
    ):
        confidence = RecoveryConfidence.EXACT
    elif (
        company_match is True and agency_match is True
        and date_distance is not None and date_distance <= 21
        and (rating_match is True or instrument_match is True or document_type_match)
    ):
        confidence = RecoveryConfidence.HIGH
    else:
        reason = "insufficient independent evidence for automatic attachment"

    originator = None
    if agency in {"CRISIL", "ICRA"}:
        originator = DocumentOriginator(agency)
    return RecoveryCandidate(
        candidate_id=None,
        recovery_target_id=int(target.recovery_target_id or 0),
        artifact_id=target.artifact_id,
        candidate_url=observation.candidate_url,
        retrieval_host=host,
        source_type=observation.source_type,
        discovered_at_utc=discovered_at_utc,
        document_title=observation.document_title,
        publication_date=observation.publication_date,
        company_identifiers=observation.company_identifiers,
        document_originator=originator,
        rating=observation.rating,
        outlook=observation.outlook,
        instrument_or_facility=observation.instrument_or_facility,
        source_evidence={
            **observation.source_evidence,
            "discovery_page_url": observation.discovery_page_url,
            "published_at_utc": observation.published_at_utc,
            "available_at_utc": observation.available_at_utc,
            "availability_basis": observation.availability_basis,
        },
        matching_features=features,
        confidence=confidence,
        rejection_reason=reason,
        content_class=RecoveredContentClass.UNKNOWN_RATING_DOCUMENT,
    )


def arbitrate_candidates(
    candidates: tuple[RecoveryCandidate, ...],
) -> tuple[RecoveryCandidate, ...]:
    """Multiple auto-eligible filings are ambiguous until a human/content tie-break."""

    plausible = [candidate for candidate in candidates if candidate.may_auto_attach]
    if len(plausible) <= 1:
        return candidates
    plausible_keys = {(candidate.source_type, candidate.candidate_url) for candidate in plausible}
    return tuple(
        replace(
            candidate,
            confidence=RecoveryConfidence.AMBIGUOUS,
            rejection_reason="multiple plausible official filings",
        )
        if (candidate.source_type, candidate.candidate_url) in plausible_keys
        else candidate
        for candidate in candidates
    )
