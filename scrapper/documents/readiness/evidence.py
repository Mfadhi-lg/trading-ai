"""Versioned projection of recorded D09 readiness observations at a cutoff."""
from dataclasses import dataclass
from typing import Any

from ...db.documents.evidence import document_check_rows
from ..availability import ArtifactCheckResult, parse_utc

DOCUMENT_EVIDENCE_VERSION = "document-evidence-v1"


@dataclass(frozen=True)
class DocumentEvidence:
    version: str
    as_of_utc: str
    expectation_id: int
    identity: dict[str, Any]
    check_id: int | None
    observed_at_utc: str | None
    available_at_utc: str | None
    result: ArtifactCheckResult | None
    # Original serialized evidence is retained, including units/nulls/hash/origin
    # references; consumers must not reinterpret it as normalized feature values.
    evidence_json: str | None
    error_json: str | None
    availability_basis: str = "recorded_check_timestamp_not_historical_PIT_certification"

    @property
    def recorded_ready(self) -> bool | None:
        if self.result is None or self.result.result_status != "available":
            return None
        return True


def document_evidence(conn, *, expectation_id: int, as_of_utc: str) -> DocumentEvidence:
    """Project one explicit expectation from recorded checks, without writes.

    Unknown expectations raise KeyError. Expectations/events first recorded after
    the cutoff also raise KeyError. An existing expectation without an eligible
    check returns a NULL result, never a negative or today's mutable readiness.
    """
    if isinstance(expectation_id, bool) or not isinstance(expectation_id, int) or expectation_id < 1:
        raise ValueError("A positive expectation id is required")
    cutoff = parse_utc(as_of_utc)
    rows = document_check_rows(conn, expectation_id=expectation_id)
    if not rows:
        raise KeyError(expectation_id)
    identity_row = rows[0]
    earliest = max(parse_utc(identity_row["created_at_utc"]), parse_utc(identity_row["evidenced_at_utc"]))
    if earliest > cutoff:
        raise KeyError(expectation_id)
    eligible = []
    for row in rows:
        if row["check_id"] is None:
            continue
        checked = parse_utc(row["checked_at_utc"])
        if checked < earliest:
            raise ValueError("Check timestamp precedes recorded expectation/event")
        if checked <= cutoff:
            eligible.append((checked, row["check_id"], row))
    selected = max(eligible, key=lambda item: item[:2])[2] if eligible else None
    identity = {key: identity_row[key] for key in (
        "company_id", "artifact_type", "event_id", "event_key", "event_type", "period_end",
        "expectation_basis", "expected_after_utc",
        "event_date", "fiscal_year", "fiscal_quarter", "created_at_utc",
        "evidenced_at_utc", "evidence_source", "event_evidence_json",
    )}
    result = None
    if selected is not None:
        import json
        result = ArtifactCheckResult(
            selected["result_status"], selected["source"],
            requested_url=selected["requested_url"],
            evidence=json.loads(selected["evidence_json"]),
            discovered_artifact_id=selected["discovered_artifact_id"],
            retryable=bool(selected["retryable"]), error=json.loads(selected["error_json"]),
        )
    observed = selected["checked_at_utc"] if selected else None
    return DocumentEvidence(
        DOCUMENT_EVIDENCE_VERSION, as_of_utc, expectation_id, identity,
        selected["check_id"] if selected else None, observed,
        observed if result is not None and result.result_status == "available" else None,
        result, selected["evidence_json"] if selected else None,
        selected["error_json"] if selected else None,
    )
