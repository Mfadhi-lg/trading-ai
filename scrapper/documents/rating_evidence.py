"""Text-structure classification for recovered rating evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .recovery import RecoveredContentClass, RecoverySourceType


RATING_EVIDENCE_CLASSIFIER_NAME = "rating-evidence-structure"
RATING_EVIDENCE_CLASSIFIER_VERSION = "1.1.0"


_RATING_AGENCY_PATTERN = re.compile(
    r"\b(?:crisil|icra|care ratings?|india ratings(?: and research)?|"
    r"brickwork ratings?|acuit[eé] ratings?|infomerics(?: valuations? and ratings?)?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContentClassification:
    content_class: RecoveredContentClass
    features: Mapping[str, Any]
    annexure_page_range: tuple[int, int] | None = None


def _page_text(page: Mapping[str, Any]) -> str:
    return str(page.get("selected_text") or page.get("text") or "")


def classify_rating_evidence(
    pages: Sequence[Mapping[str, Any]],
    *,
    source_type: RecoverySourceType | None,
    filing_context: str | None = None,
) -> ContentClassification:
    """Classify from extracted structure/text, never from the filename."""

    texts = [_page_text(page) for page in pages]
    joined = "\n".join(texts).lower()
    context = (filing_context or "").lower()
    agency_present = bool(_RATING_AGENCY_PATTERN.search(joined))
    rationale_heading = bool(re.search(r"\brating rationale\b", joined))
    key_drivers = bool(re.search(r"\b(?:key rating drivers|key strengths|credit strengths)\b", joined))
    liquidity = bool(re.search(r"\bliquidity\b", joined))
    sensitivities = bool(re.search(
        r"\b(?:rating sensitivities|upgrade factors|downgrade factors|"
        r"factors that could lead to an upgrade|factors that could lead to a downgrade)\b",
        joined,
    ))
    rating_letter = bool(re.search(r"\brating letter\b", joined))
    intimation = bool(re.search(
        r"\b(?:intimation (?:of|regarding).{0,40}(?:credit )?rating|"
        r"rating action intimation|revision in credit rating)\b",
        joined,
    ))
    annual_summary = "annual report" in context and "credit rating" in joined
    issue_source = source_type in {
        RecoverySourceType.NSE_DEBT_DOCUMENT,
        RecoverySourceType.BSE_DEBT_DOCUMENT,
    }
    full_structure = agency_present and rationale_heading and key_drivers and liquidity and sensitivities
    features = {
        "classifier_name": RATING_EVIDENCE_CLASSIFIER_NAME,
        "classifier_version": RATING_EVIDENCE_CLASSIFIER_VERSION,
        "agency_present": agency_present,
        "rationale_heading": rationale_heading,
        "key_rating_drivers": key_drivers,
        "liquidity": liquidity,
        "rating_sensitivities": sensitivities,
        "rating_letter_heading": rating_letter,
        "rating_action_intimation": intimation,
        "issue_document_context": issue_source,
        "page_count": len(pages),
    }
    page_numbers = [
        int(page.get("page_or_slide_number") or page.get("page_number") or index + 1)
        for index, page in enumerate(pages)
        if _RATING_AGENCY_PATTERN.search(_page_text(page))
        or re.search(r"\b(?:rating rationale|key rating drivers)\b",
                     _page_text(page), re.IGNORECASE)
    ]
    annexure = (min(page_numbers), max(page_numbers)) if page_numbers else None
    if source_type is RecoverySourceType.REGULATORY_METADATA:
        result = RecoveredContentClass.REGULATORY_RATING_METADATA
    elif full_structure and issue_source:
        result = RecoveredContentClass.ISSUE_DOCUMENT_EMBEDDED_RATIONALE
    elif full_structure:
        result = RecoveredContentClass.FULL_RATING_RATIONALE
    elif rating_letter and agency_present:
        result = RecoveredContentClass.RATING_LETTER
    elif intimation and not (rationale_heading or key_drivers):
        result = RecoveredContentClass.RATING_ACTION_INTIMATION
    elif annual_summary:
        result = RecoveredContentClass.ANNUAL_REPORT_RATING_SUMMARY
    elif agency_present and (rationale_heading or key_drivers):
        result = RecoveredContentClass.RATING_RATIONALE_EXCERPT
    else:
        result = RecoveredContentClass.UNKNOWN_RATING_DOCUMENT
    return ContentClassification(result, features, annexure if issue_source else None)
