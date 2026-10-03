"""First-class NSE/BSE issue/debt-document candidate adapter."""

from __future__ import annotations

from dataclasses import replace

from ...documents.recovery import RecoverySourceType
from .recovery_common import (
    ISSUE_DOCUMENT_TERMS,
    OfficialCandidateObservation,
    relevant_title,
)


class DebtIssueDocumentResolver:
    """Select issue documents from an exchange filing observation stream.

    The adapter does not claim that an issue document satisfies the target.
    Existing PDF extraction plus DA-06 content validation must prove that CRA
    evidence is actually present before attachment.
    """

    def select(
        self,
        observations: tuple[OfficialCandidateObservation, ...],
        *,
        exchange: str,
    ) -> tuple[OfficialCandidateObservation, ...]:
        source_type = (
            RecoverySourceType.NSE_DEBT_DOCUMENT
            if exchange.upper() == "NSE"
            else RecoverySourceType.BSE_DEBT_DOCUMENT
        )
        selected = []
        for observation in observations:
            category = str(observation.source_evidence.get("filing_category") or "")
            if relevant_title(
                " ".join(filter(None, (observation.document_title, category))),
                ISSUE_DOCUMENT_TERMS,
            ):
                selected.append(replace(observation, source_type=source_type))
        return tuple(selected)

