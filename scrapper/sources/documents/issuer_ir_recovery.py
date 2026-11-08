"""One-page, same-host issuer-IR attachment resolver."""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ...core.access_policy import is_blocked_terms_target
from ...documents.recovery import RecoverySourceType
from .recovery_common import (
    OfficialCandidateObservation,
    RATING_TERMS,
    normalize_attachment_url,
    relevant_title,
)


class IssuerIRResolver:
    def parse_saved_page(
        self,
        content: bytes,
        *,
        discovery_page_url: str,
        company_id: int,
        discovered_at_utc: str,
    ) -> tuple[OfficialCandidateObservation, ...]:
        page_host = (urlsplit(discovery_page_url).hostname or "").lower().rstrip(".")
        if not page_host or is_blocked_terms_target(discovery_page_url):
            raise ValueError("Issuer IR discovery page is not an eligible issuer domain")
        soup = BeautifulSoup(content, "html.parser")
        observations: list[OfficialCandidateObservation] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            title = " ".join(anchor.stripped_strings).strip()
            href = str(anchor.get("href") or "").strip()
            combined = f"{title} {href}"
            if not relevant_title(combined, RATING_TERMS):
                continue
            url = normalize_attachment_url(discovery_page_url, href)
            host = (urlsplit(url).hostname or "").lower().rstrip(".")
            if host != page_host or is_blocked_terms_target(url) or url in seen:
                continue
            seen.add(url)
            observations.append(OfficialCandidateObservation(
                candidate_url=url,
                retrieval_host=host,
                source_type=RecoverySourceType.ISSUER_IR_ATTACHMENT,
                discovery_page_url=discovery_page_url,
                document_title=title or None,
                publication_date=None,
                published_at_utc=None,
                available_at_utc=discovered_at_utc,
                availability_basis="first_observation",
                company_identifiers={"company_id": (str(company_id),)},
                document_originator=None,
                rating=None,
                outlook=None,
                instrument_or_facility=None,
                source_evidence={
                    "issuer_domain": page_host,
                    "discovery_page": discovery_page_url,
                    "bounded_depth": 0,
                },
            ))
        return tuple(observations)

