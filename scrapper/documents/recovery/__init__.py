"""Versioned contracts for official alternate-source document recovery.

DA-06 discovers and evaluates retrieval alternatives for an existing logical
artifact.  These contracts deliberately contain no downloader or persistence
behaviour: accepted sources still enter the existing D02 pipeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit


RECOVERY_CONTRACT_VERSION = "1.0.0"


class DocumentOriginator(str, Enum):
    CRISIL = "CRISIL"
    ICRA = "ICRA"


class RecoveryConfidence(str, Enum):
    EXACT = "EXACT"
    HIGH = "HIGH"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"
    REJECTED_POLICY = "REJECTED_POLICY"
    REJECTED_INVALID = "REJECTED_INVALID"

    @property
    def may_auto_attach(self) -> bool:
        return self in {self.EXACT, self.HIGH}


class RecoveryTargetStatus(str, Enum):
    PENDING = "PENDING"
    SEARCHING = "SEARCHING"
    CANDIDATE_FOUND = "CANDIDATE_FOUND"
    RECOVERED = "RECOVERED"
    ALREADY_SATISFIED = "ALREADY_SATISFIED"
    AMBIGUOUS = "AMBIGUOUS"
    NO_OFFICIAL_SOURCE_FOUND = "NO_OFFICIAL_SOURCE_FOUND"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    LICENSE_OR_MANUAL_REQUIRED = "LICENSE_OR_MANUAL_REQUIRED"


class RecoverySourceType(str, Enum):
    NSE_EXCHANGE_ATTACHMENT = "NSE_EXCHANGE_ATTACHMENT"
    BSE_EXCHANGE_ATTACHMENT = "BSE_EXCHANGE_ATTACHMENT"
    NSE_DEBT_DOCUMENT = "NSE_DEBT_DOCUMENT"
    BSE_DEBT_DOCUMENT = "BSE_DEBT_DOCUMENT"
    ISSUER_IR_ATTACHMENT = "ISSUER_IR_ATTACHMENT"
    REGULATORY_METADATA = "REGULATORY_METADATA"


class RecoveredContentClass(str, Enum):
    FULL_RATING_RATIONALE = "FULL_RATING_RATIONALE"
    RATING_RATIONALE_EXCERPT = "RATING_RATIONALE_EXCERPT"
    RATING_LETTER = "RATING_LETTER"
    RATING_ACTION_INTIMATION = "RATING_ACTION_INTIMATION"
    ISSUE_DOCUMENT_EMBEDDED_RATIONALE = "ISSUE_DOCUMENT_EMBEDDED_RATIONALE"
    ANNUAL_REPORT_RATING_SUMMARY = "ANNUAL_REPORT_RATING_SUMMARY"
    REGULATORY_RATING_METADATA = "REGULATORY_RATING_METADATA"
    UNKNOWN_RATING_DOCUMENT = "UNKNOWN_RATING_DOCUMENT"

    @property
    def provides_basic_rating(self) -> bool:
        """Only positively classified rating evidence satisfies recovery."""

        return self is not self.UNKNOWN_RATING_DOCUMENT

    @property
    def provides_full_rationale(self) -> bool:
        return self in {
            self.FULL_RATING_RATIONALE,
            self.ISSUE_DOCUMENT_EMBEDDED_RATIONALE,
        }


def _plain_https_url(value: str, *, field_name: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError(f"{field_name} must be a plain absolute HTTPS URL")
    return value


def _nonempty(value: str, *, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} is required")
    return value


@dataclass(frozen=True)
class RecoveryTarget:
    recovery_target_id: int | None
    artifact_id: int
    company_id: int
    artifact_type: str
    document_originator: DocumentOriginator
    blocked_source_id: int
    blocked_source_url: str
    blocked_source_host: str
    company_canonical_name: str
    company_aliases: tuple[str, ...] = ()
    nse_symbols: tuple[str, ...] = ()
    bse_codes: tuple[str, ...] = ()
    isins: tuple[str, ...] = ()
    event_date: str | None = None
    fiscal_period: str | None = None
    rating: str | None = None
    outlook: str | None = None
    rating_action: str | None = None
    instrument_or_facility: str | None = None
    amount: str | None = None
    amount_unit: str | None = None
    source_confidence: str | None = None
    field_provenance: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    contract_version: str = RECOVERY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.artifact_id < 1 or self.company_id < 1 or self.blocked_source_id < 1:
            raise ValueError("artifact, company, and blocked source IDs must be positive")
        if self.artifact_type != "credit_rating":
            raise ValueError("DA-06 recovery targets must be credit_rating artifacts")
        _plain_https_url(self.blocked_source_url, field_name="blocked_source_url")
        _nonempty(self.blocked_source_host, field_name="blocked_source_host")
        _nonempty(self.company_canonical_name, field_name="company_canonical_name")
        if self.contract_version != RECOVERY_CONTRACT_VERSION:
            raise ValueError(f"Unsupported recovery contract: {self.contract_version}")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["document_originator"] = self.document_originator.value
        return payload


@dataclass(frozen=True)
class RecoveryCandidate:
    candidate_id: int | None
    recovery_target_id: int
    artifact_id: int
    candidate_url: str
    retrieval_host: str
    source_type: RecoverySourceType
    discovered_at_utc: str
    document_title: str | None = None
    publication_date: str | None = None
    company_identifiers: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    document_originator: DocumentOriginator | None = None
    rating: str | None = None
    outlook: str | None = None
    instrument_or_facility: str | None = None
    source_evidence: Mapping[str, Any] = field(default_factory=dict)
    matching_features: Mapping[str, Any] = field(default_factory=dict)
    confidence: RecoveryConfidence = RecoveryConfidence.AMBIGUOUS
    rejection_reason: str | None = None
    content_class: RecoveredContentClass = RecoveredContentClass.UNKNOWN_RATING_DOCUMENT
    contract_version: str = RECOVERY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.recovery_target_id < 1 or self.artifact_id < 1:
            raise ValueError("recovery target and artifact IDs must be positive")
        _plain_https_url(self.candidate_url, field_name="candidate_url")
        _nonempty(self.retrieval_host, field_name="retrieval_host")
        parsed_host = (urlsplit(self.candidate_url).hostname or "").lower().rstrip(".")
        if parsed_host != self.retrieval_host.lower().rstrip("."):
            raise ValueError("retrieval_host must match candidate_url")
        if self.confidence in {
            RecoveryConfidence.REJECTED_POLICY,
            RecoveryConfidence.REJECTED_INVALID,
        } and not self.rejection_reason:
            raise ValueError("Rejected candidates require a rejection reason")
        if self.contract_version != RECOVERY_CONTRACT_VERSION:
            raise ValueError(f"Unsupported recovery contract: {self.contract_version}")

    @property
    def may_auto_attach(self) -> bool:
        return self.confidence.may_auto_attach

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_type"] = self.source_type.value
        payload["confidence"] = self.confidence.value
        payload["content_class"] = self.content_class.value
        payload["document_originator"] = (
            self.document_originator.value if self.document_originator else None
        )
        return payload
