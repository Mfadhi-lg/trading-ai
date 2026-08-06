"""Pure, saved-page document discovery for D01.

The adapter deliberately does not fetch. A small JSON manifest identifies saved
primary-source/Screener pages and their original page URLs. Every candidate keeps
the source page hash, exact link text, URL and classification evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit


DISCOVERY_MANIFEST_VERSION = "1.0.0"
DISCOVERY_PARSER_NAME = "document-link-discovery"
DISCOVERY_PARSER_VERSION = "1.0.0"
DISCOVERY_SOURCES = (
    "nse",
    "bse",
    "company_ir",
    "rating_agency",
    "screener",
)
ARTIFACT_TYPES = ("presentation", "credit_rating", "transcript", "recording")
PRIMARY_SOURCES = frozenset({"nse", "bse", "company_ir", "rating_agency"})
SOURCE_PRIORITY = {source: position for position, source in enumerate(DISCOVERY_SOURCES)}

_SPACE = re.compile(r"\s+")
_NON_KEY = re.compile(r"[^a-z0-9]+")
_TYPE_PATTERNS = (
    (
        "credit_rating",
        re.compile(r"\b(credit[ -]?rating|rating rationale|rating letter|surveillance|reaffirmation)\b", re.I),
    ),
    (
        "transcript",
        re.compile(r"\b(transcript|conference call transcript|earnings call transcript)\b", re.I),
    ),
    (
        "presentation",
        re.compile(r"\b(investor|earnings|results?)?\s*presentation\b", re.I),
    ),
    (
        "recording",
        re.compile(r"\b(recording|audio|video|webcast|earnings call)\b", re.I),
    ),
)
_RECORDING_SUFFIXES = (".mp3", ".m4a", ".wav", ".mp4", ".webm", ".m3u8")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = _SPACE.sub(" ", str(value)).strip()
    return normalized or None


def _iso_date(value: Any, field: str) -> str | None:
    normalized = _text(value)
    if normalized is None:
        return None
    try:
        date.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date: {normalized!r}") from exc
    return normalized


def _iso_datetime(value: Any, field: str) -> str | None:
    normalized = _text(value)
    if normalized is None:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO datetime: {normalized!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a UTC offset: {normalized!r}")
    return parsed.astimezone(timezone.utc).isoformat()


def _http_url(value: str, field: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"{field} must be an absolute HTTP(S) URL: {value!r}")
    if parts.username or parts.password:
        raise ValueError(f"{field} must not contain credentials")
    return value


@dataclass(frozen=True)
class DiscoveryPage:
    page_position: int
    source: str
    page_url: str
    publisher_claim: str | None
    content_path: Path
    content_sha256: str
    content: bytes
    inspected_link_count: int
    ignored_link_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_position": self.page_position,
            "source": self.source,
            "page_url": self.page_url,
            "publisher_claim": self.publisher_claim,
            "content_path": str(self.content_path),
            "content_sha256": self.content_sha256,
            "byte_size": len(self.content),
            "inspected_link_count": self.inspected_link_count,
            "ignored_link_count": self.ignored_link_count,
        }


@dataclass(frozen=True)
class DiscoveredArtifactCandidate:
    page_position: int
    source_position: int
    discovered_from_source: str
    discovery_page_url: str
    discovery_page_sha256: str
    artifact_key: str
    artifact_type: str
    title_raw: str
    document_url: str
    publisher_claim: str | None
    exchange_announcement_id: str | None
    event_date: str | None
    period_end: str | None
    fiscal_year: int | None
    fiscal_quarter: int | None
    published_at_utc: str | None
    identity_basis: str
    classification_basis: str
    is_primary_source: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_position": self.page_position,
            "source_position": self.source_position,
            "discovered_from_source": self.discovered_from_source,
            "discovery_page_url": self.discovery_page_url,
            "discovery_page_sha256": self.discovery_page_sha256,
            "artifact_key": self.artifact_key,
            "artifact_type": self.artifact_type,
            "title_raw": self.title_raw,
            "document_url": self.document_url,
            "publisher_claim": self.publisher_claim,
            "exchange_announcement_id": self.exchange_announcement_id,
            "event_date": self.event_date,
            "period_end": self.period_end,
            "fiscal_year": self.fiscal_year,
            "fiscal_quarter": self.fiscal_quarter,
            "published_at_utc": self.published_at_utc,
            "identity_basis": self.identity_basis,
            "classification_basis": self.classification_basis,
            "is_primary_source": self.is_primary_source,
        }


@dataclass(frozen=True)
class DiscoveryPlan:
    symbol: str
    company_name: str
    manifest_path: Path
    pages: tuple[DiscoveryPage, ...]
    candidates: tuple[DiscoveredArtifactCandidate, ...]

    @property
    def logical_artifact_count(self) -> int:
        return len({candidate.artifact_key for candidate in self.candidates})

    def to_dict(self, *, dry_run: bool) -> dict[str, Any]:
        source_counts = {
            source: sum(c.discovered_from_source == source for c in self.candidates)
            for source in DISCOVERY_SOURCES
            if any(c.discovered_from_source == source for c in self.candidates)
        }
        artifact_type_counts = {
            artifact_type: sum(c.artifact_type == artifact_type for c in self.candidates)
            for artifact_type in ARTIFACT_TYPES
            if any(c.artifact_type == artifact_type for c in self.candidates)
        }
        return {
            "schema_version": "1.0.0",
            "parser_name": DISCOVERY_PARSER_NAME,
            "parser_version": DISCOVERY_PARSER_VERSION,
            "dry_run": dry_run,
            "symbol": self.symbol,
            "company_name": self.company_name,
            "manifest_path": str(self.manifest_path),
            "summary": {
                "page_count": len(self.pages),
                "candidate_url_count": len(self.candidates),
                "logical_artifact_count": self.logical_artifact_count,
                "primary_source_candidate_count": sum(
                    candidate.is_primary_source for candidate in self.candidates
                ),
                "source_counts": source_counts,
                "artifact_type_counts": artifact_type_counts,
            },
            "pages": [page.to_dict() for page in self.pages],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass
class _Anchor:
    attrs: dict[str, str]
    text_parts: list[str]


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self._current: _Anchor | None = None
        self._anchor_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a" and self._current is None:
            self._current = _Anchor(
                attrs={key.lower(): value or "" for key, value in attrs},
                text_parts=[],
            )
            self._anchor_depth = 1
        elif self._current is not None:
            self._anchor_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        self._anchor_depth -= 1
        if tag.lower() == "a" or self._anchor_depth <= 0:
            self.anchors.append(self._current)
            self._current = None
            self._anchor_depth = 0

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.text_parts.append(data)


def _classify(anchor: _Anchor, title: str, document_url: str) -> tuple[str | None, str]:
    declared = _text(anchor.attrs.get("data-artifact-type"))
    if declared is not None:
        if declared not in ARTIFACT_TYPES:
            raise ValueError(f"Unknown data-artifact-type: {declared!r}")
        return declared, "source_declared_data_attribute"
    haystack = f"{title} {document_url}"
    for artifact_type, pattern in _TYPE_PATTERNS:
        if pattern.search(haystack):
            return artifact_type, f"versioned_keyword:{artifact_type}"
    if urlsplit(document_url).path.lower().endswith(_RECORDING_SUFFIXES):
        return "recording", "validated_url_suffix:recording"
    return None, "no_in_scope_artifact_rule"


def _artifact_key(
    *,
    symbol: str,
    artifact_type: str,
    title: str,
    anchor: _Anchor,
    event_date: str | None,
    period_end: str | None,
) -> tuple[str, str]:
    explicit = _text(anchor.attrs.get("data-artifact-key"))
    if explicit:
        identity = f"explicit:{_NON_KEY.sub('-', explicit.lower()).strip('-')}"
        basis = "source_manifest_artifact_key"
    else:
        normalized_title = _NON_KEY.sub(" ", title.lower()).strip()
        identity = "|".join(
            (normalized_title, event_date or "", period_end or "")
        )
        basis = "normalized_title_event_period"
    digest = hashlib.sha256(
        f"{symbol.upper()}|{artifact_type}|{identity}".encode("utf-8")
    ).hexdigest()
    return digest, basis


def _parse_page(
    *,
    symbol: str,
    page_position: int,
    source: str,
    page_url: str,
    publisher_claim: str | None,
    content_path: Path,
    content: bytes,
) -> tuple[DiscoveryPage, list[DiscoveredArtifactCandidate]]:
    sha256 = hashlib.sha256(content).hexdigest()
    parser = _AnchorParser()
    try:
        parser.feed(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"Discovery page is not UTF-8: {content_path}") from exc
    candidates: list[DiscoveredArtifactCandidate] = []
    ignored = 0
    for source_position, anchor in enumerate(parser.anchors):
        href = _text(anchor.attrs.get("href"))
        if href is None or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            ignored += 1
            continue
        document_url = _http_url(urljoin(page_url, href), "document_url")
        title = _text(" ".join(anchor.text_parts)) or _text(anchor.attrs.get("title"))
        title = title or _text(anchor.attrs.get("aria-label"))
        if title is None:
            ignored += 1
            continue
        artifact_type, classification_basis = _classify(anchor, title, document_url)
        if artifact_type is None:
            ignored += 1
            continue
        if source == "rating_agency" and artifact_type != "credit_rating":
            ignored += 1
            continue
        event_date = _iso_date(anchor.attrs.get("data-event-date"), "event_date")
        period_end = _iso_date(anchor.attrs.get("data-period-end"), "period_end")
        published_at = _iso_datetime(
            anchor.attrs.get("data-published-at-utc"), "published_at_utc"
        )
        fiscal_year_text = _text(anchor.attrs.get("data-fiscal-year"))
        fiscal_quarter_text = _text(anchor.attrs.get("data-fiscal-quarter"))
        fiscal_year = int(fiscal_year_text) if fiscal_year_text else None
        fiscal_quarter = int(fiscal_quarter_text) if fiscal_quarter_text else None
        if fiscal_quarter is not None and fiscal_quarter not in range(1, 5):
            raise ValueError("fiscal_quarter must be between 1 and 4")
        key, identity_basis = _artifact_key(
            symbol=symbol,
            artifact_type=artifact_type,
            title=title,
            anchor=anchor,
            event_date=event_date,
            period_end=period_end,
        )
        candidate_publisher = _text(anchor.attrs.get("data-publisher")) or publisher_claim
        if source == "screener":
            candidate_publisher = None
        candidates.append(
            DiscoveredArtifactCandidate(
                page_position=page_position,
                source_position=source_position,
                discovered_from_source=source,
                discovery_page_url=page_url,
                discovery_page_sha256=sha256,
                artifact_key=key,
                artifact_type=artifact_type,
                title_raw=title,
                document_url=document_url,
                publisher_claim=candidate_publisher,
                exchange_announcement_id=_text(
                    anchor.attrs.get("data-exchange-announcement-id")
                ),
                event_date=event_date,
                period_end=period_end,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                published_at_utc=published_at,
                identity_basis=identity_basis,
                classification_basis=classification_basis,
                is_primary_source=source in PRIMARY_SOURCES,
            )
        )
    page = DiscoveryPage(
        page_position=page_position,
        source=source,
        page_url=page_url,
        publisher_claim=publisher_claim,
        content_path=content_path,
        content_sha256=sha256,
        content=content,
        inspected_link_count=len(parser.anchors),
        ignored_link_count=ignored,
    )
    return page, candidates


def build_discovery_plan(manifest_path: Path, *, requested_symbol: str) -> DiscoveryPlan:
    """Parse saved discovery pages without touching the database or network."""

    manifest_path = manifest_path.resolve()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read discovery manifest: {manifest_path}") from exc
    if payload.get("schema_version") != DISCOVERY_MANIFEST_VERSION:
        raise ValueError(
            f"Unsupported discovery manifest schema: {payload.get('schema_version')!r}"
        )
    symbol = _text(payload.get("symbol"))
    company_name = _text(payload.get("company_name"))
    if symbol is None or symbol.upper() != requested_symbol.upper():
        raise ValueError(
            f"Discovery manifest symbol {symbol!r} does not match {requested_symbol!r}"
        )
    if company_name is None:
        raise ValueError("Discovery manifest company_name is required")
    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise ValueError("Discovery manifest pages must be a non-empty list")
    pages: list[DiscoveryPage] = []
    candidates: list[DiscoveredArtifactCandidate] = []
    seen_page_keys: set[tuple[str, str, str]] = set()
    for page_position, raw in enumerate(raw_pages):
        if not isinstance(raw, dict):
            raise ValueError("Each discovery page must be an object")
        source = _text(raw.get("source"))
        if source not in DISCOVERY_SOURCES:
            raise ValueError(f"Unknown discovery source: {source!r}")
        page_url = _http_url(str(raw.get("page_url", "")), "page_url")
        relative_content_path = _text(raw.get("content_path"))
        if relative_content_path is None:
            raise ValueError("Discovery page content_path is required")
        content_path = (manifest_path.parent / relative_content_path).resolve()
        try:
            content = content_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"Cannot read saved discovery page: {content_path}") from exc
        actual_sha256 = hashlib.sha256(content).hexdigest()
        expected_sha256 = _text(raw.get("content_sha256"))
        if expected_sha256 and expected_sha256.lower() != actual_sha256:
            raise ValueError(
                f"Discovery page hash mismatch for {content_path}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        page_key = (source, page_url, actual_sha256)
        if page_key in seen_page_keys:
            raise ValueError(f"Duplicate discovery page entry: {page_key!r}")
        seen_page_keys.add(page_key)
        page, page_candidates = _parse_page(
            symbol=symbol,
            page_position=page_position,
            source=source,
            page_url=page_url,
            publisher_claim=_text(raw.get("publisher_claim")),
            content_path=content_path,
            content=content,
        )
        pages.append(page)
        candidates.extend(page_candidates)
    candidates.sort(
        key=lambda candidate: (
            SOURCE_PRIORITY[candidate.discovered_from_source],
            candidate.page_position,
            candidate.source_position,
            candidate.document_url,
        )
    )
    if not candidates:
        raise ValueError("No in-scope document candidates were found")
    # A conservative identity may have many URL paths, but those paths cannot
    # disagree on typed event identity. Conflicts require manifest/source review
    # instead of allowing source order to choose a canonical value silently.
    identity_groups: dict[str, list[DiscoveredArtifactCandidate]] = {}
    url_groups: dict[str, list[DiscoveredArtifactCandidate]] = {}
    for candidate in candidates:
        identity_groups.setdefault(candidate.artifact_key, []).append(candidate)
        url_groups.setdefault(candidate.document_url, []).append(candidate)
    for group_name, groups in (("artifact_key", identity_groups), ("document_url", url_groups)):
        for group_value, group in groups.items():
            for field in (
                "artifact_type",
                "event_date",
                "period_end",
                "fiscal_year",
                "fiscal_quarter",
            ):
                values = {
                    getattr(candidate, field)
                    for candidate in group
                    if getattr(candidate, field) is not None
                }
                if len(values) > 1:
                    raise ValueError(
                        f"Conflicting {field} for {group_name} {group_value!r}: "
                        f"{sorted(values)!r}"
                    )
    return DiscoveryPlan(
        symbol=symbol.upper(),
        company_name=company_name,
        manifest_path=manifest_path,
        pages=tuple(pages),
        candidates=tuple(candidates),
    )
