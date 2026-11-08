"""Pure contracts and normalization shared by official recovery adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

from ...config import (
    DOCUMENT_RECOVERY_DATE_WINDOW_DAYS,
    DOCUMENT_RECOVERY_MAX_DATE_WINDOW_DAYS,
)
from ...documents.recovery import RecoverySourceType, RecoveryTarget


RATING_TERMS = (
    "credit rating",
    "rating rationale",
    "rating revision",
    "rating reaffirmation",
    "rating upgrade",
    "rating downgrade",
)
ISSUE_DOCUMENT_TERMS = (
    "key information document",
    "kid",
    "general information document",
    "gid",
    "placement memorandum",
    "information memorandum",
    "prospectus",
    "debenture issue",
    "bond issue",
)


@dataclass(frozen=True)
class RecoveryQuery:
    source: str
    url: str
    identifier_kind: str
    identifier_value: str
    date_from: str
    date_to: str


@dataclass(frozen=True)
class OfficialCandidateObservation:
    candidate_url: str
    retrieval_host: str
    source_type: RecoverySourceType
    discovery_page_url: str
    document_title: str | None
    publication_date: str | None
    published_at_utc: str | None
    available_at_utc: str
    availability_basis: str
    company_identifiers: Mapping[str, tuple[str, ...]]
    document_originator: str | None
    rating: str | None
    outlook: str | None
    instrument_or_facility: str | None
    source_evidence: Mapping[str, Any]


def date_window(
    target: RecoveryTarget, *, days: int = DOCUMENT_RECOVERY_DATE_WINDOW_DAYS
) -> tuple[str, str]:
    if days < 1 or days > DOCUMENT_RECOVERY_MAX_DATE_WINDOW_DAYS:
        raise ValueError(
            f"date window must be between 1 and {DOCUMENT_RECOVERY_MAX_DATE_WINDOW_DAYS} days"
        )
    if not target.event_date:
        raise ValueError("A bounded exchange recovery search requires an event date")
    center = date.fromisoformat(target.event_date)
    return ((center - timedelta(days=days)).isoformat(),
            (center + timedelta(days=days)).isoformat())


def normalize_attachment_url(base_url: str, value: str) -> str:
    url = urljoin(base_url, value.strip())
    parts = urlsplit(url)
    if (
        parts.scheme != "https" or not parts.hostname or parts.username
        or parts.password or parts.fragment
    ):
        raise ValueError("Official attachment must be a plain HTTPS URL")
    return url


def parse_publication_time(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    text = value.strip()
    patterns = (
        "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
        "%d-%b-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
    )
    parsed: datetime | None = None
    for pattern in patterns:
        try:
            parsed = datetime.strptime(text, pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        return None, None
    if parsed.tzinfo is None:
        # Exchange filing surfaces publish in IST unless they carry an offset.
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    return parsed.date().isoformat(), parsed.astimezone(timezone.utc).isoformat()


def explicit_document_originator(*values: str | None) -> str | None:
    """Return a CRA only when an official response field names it explicitly."""

    text = " ".join(value for value in values if value)
    if (
        re.search(r"\bcrisil\b", text, re.IGNORECASE)
        or re.search(r"CRISIL(?:RATINGS|Ratings)", text)
    ):
        return "CRISIL"
    if (
        re.search(r"\bicra\b", text, re.IGNORECASE)
        or re.search(r"ICRA(?:LIMITED|Limited)", text)
    ):
        return "ICRA"
    return None


def first_value(row: Mapping[str, Any], *names: str) -> str | None:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def json_rows(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, list):
        return tuple(row for row in payload if isinstance(row, Mapping))
    if not isinstance(payload, Mapping):
        return ()
    for key in ("data", "Data", "Table", "rows", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return tuple(row for row in value if isinstance(row, Mapping))
    return (payload,)


def relevant_title(title: str | None, terms: tuple[str, ...]) -> bool:
    lowered = (title or "").lower()
    return any(term in lowered for term in terms)
