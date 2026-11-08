"""Derived evidence overlay on existing artifact/content/extraction identities."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import hashlib
import json
import re

from ...documents.availability import parse_utc

PARSER_VERSION = "phase5-deterministic-2.0.2"
STATUSES = {"VERIFIED", "EVIDENCED_STATEMENT", "UNKNOWN", "NOT_FOUND", "AMBIGUOUS", "UNSUPPORTED"}


@dataclass(frozen=True)
class DocumentContext:
    document_id: str
    content_sha256: str
    document_type: str
    company_id: int | None = None
    company_name: str | None = None
    security_id: str | None = None
    symbol: str | None = None
    isin: str | None = None
    content_id: int | None = None
    version: str | None = None
    source_url: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    reporting_period: str | None = None
    filing_date: str | None = None
    report_date: str | None = None
    publication_date: str | None = None
    publication_at: str | None = None
    ingested_at: str | None = None
    captured_at: str | None = None
    supersedes_version: str | None = None

    def __post_init__(self):
        if self.document_type not in {"presentation", "transcript", "credit_rating"}:
            raise ValueError("Unsupported document type")
        if not self.document_id or not re.fullmatch(r"[a-f0-9]{64}", self.content_sha256):
            raise ValueError("Original identity and SHA256 are required")
        for value in (self.publication_at, self.ingested_at, self.captured_at):
            if value is not None:
                parse_utc(value)

    def to_dict(self):
        result = asdict(self)
        result["version"] = self.version or self.content_sha256
        result["metadata_status"] = {
            key: "KNOWN" if value is not None else "UNKNOWN"
            for key, value in result.items() if key not in {"provenance", "metadata_status"}
        }
        return result


@dataclass
class ResearchEvidence:
    source: dict[str, Any]
    metric: str
    statement_kind: str
    status: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    original_value: str | None = None
    normalized_value: Any = None
    currency: str | None = None
    unit: str | None = None
    scale: str | None = None
    value_basis: str = "UNKNOWN"
    reporting_period: str | None = None
    time_horizon: str | None = None
    dimensions: dict[str, Any] = field(default_factory=dict)
    speaker: dict[str, Any] | None = None
    section: str | None = None
    effective_date: str | None = None
    as_of_date: str | None = None
    claim_text: str | None = None
    reasons: list[str] = field(default_factory=list)
    related_records: list[str] = field(default_factory=list)
    parser_version: str = PARSER_VERSION
    model_version: str | None = None
    record_id: str = ""

    @property
    def disclosure_status(self):
        if self.metric == 'evidence_rejection':
            return 'REJECTED'
        return {'VERIFIED': 'ACCEPTED', 'EVIDENCED_STATEMENT': 'ACCEPTED',
                'AMBIGUOUS': 'AMBIGUOUS', 'UNSUPPORTED': 'UNSUPPORTED'}.get(self.status)

    def seal(self):
        if self.status not in STATUSES:
            raise ValueError(self.status)
        # Validation status and relationships can evolve in a derived export;
        # identity remains pinned to the original content, passage and parser.
        payload = {k: v for k, v in asdict(self).items()
                   if k not in {"record_id", "status", "reasons", "related_records"}}
        self.record_id = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return self

    def to_dict(self):
        if not self.record_id:
            self.seal()
        result = asdict(self)
        if self.dimensions.get('disclosure_categories') or self.parser_version == 'phase5-disclosures-1.0.0':
            result['disclosure_status'] = self.disclosure_status
        return result
