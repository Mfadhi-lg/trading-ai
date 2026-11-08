from __future__ import annotations

import unittest

from scrapper.documents.recovery import (
    DocumentOriginator,
    RecoveredContentClass,
    RecoveryCandidate,
    RecoveryConfidence,
    RecoverySourceType,
    RecoveryTarget,
)


class RecoveryContractTests(unittest.TestCase):
    def target(self) -> RecoveryTarget:
        return RecoveryTarget(
            recovery_target_id=1,
            artifact_id=10,
            company_id=20,
            artifact_type="credit_rating",
            document_originator=DocumentOriginator.CRISIL,
            blocked_source_id=30,
            blocked_source_url="https://www.crisilratings.com/report.pdf",
            blocked_source_host="www.crisilratings.com",
            company_canonical_name="Fixture Limited",
            nse_symbols=("FIXTURE",),
            isins=("INE000A01000",),
            event_date=None,
            field_provenance={"nse_symbols": ("security_identifiers",)},
        )

    def test_target_preserves_unknowns_and_durable_identity(self) -> None:
        target = self.target()
        self.assertIsNone(target.event_date)
        self.assertIsNone(target.rating)
        self.assertEqual(target.artifact_id, 10)
        self.assertEqual(target.to_dict()["document_originator"], "CRISIL")

    def test_target_cannot_depend_on_ticker_alone(self) -> None:
        with self.assertRaisesRegex(ValueError, "company_canonical_name"):
            RecoveryTarget(
                recovery_target_id=None,
                artifact_id=10,
                company_id=20,
                artifact_type="credit_rating",
                document_originator=DocumentOriginator.ICRA,
                blocked_source_id=30,
                blocked_source_url="https://www.icra.in/report.pdf",
                blocked_source_host="www.icra.in",
                company_canonical_name="",
                nse_symbols=("FIXTURE",),
            )

    def test_only_exact_and_high_may_auto_attach(self) -> None:
        for confidence, accepted in (
            (RecoveryConfidence.EXACT, True),
            (RecoveryConfidence.HIGH, True),
            (RecoveryConfidence.AMBIGUOUS, False),
            (RecoveryConfidence.NO_MATCH, False),
            (RecoveryConfidence.REJECTED_POLICY, False),
            (RecoveryConfidence.REJECTED_INVALID, False),
        ):
            candidate = RecoveryCandidate(
                candidate_id=None,
                recovery_target_id=1,
                artifact_id=10,
                candidate_url="https://nsearchives.nseindia.com/corporate/a.pdf",
                retrieval_host="nsearchives.nseindia.com",
                source_type=RecoverySourceType.NSE_EXCHANGE_ATTACHMENT,
                discovered_at_utc="2026-08-16T00:00:00+00:00",
                confidence=confidence,
                rejection_reason=(
                    "fixture rejection" if confidence.value.startswith("REJECTED") else None
                ),
            )
            self.assertEqual(candidate.may_auto_attach, accepted)

    def test_full_rationale_coverage_is_explicit(self) -> None:
        self.assertTrue(RecoveredContentClass.FULL_RATING_RATIONALE.provides_full_rationale)
        self.assertTrue(
            RecoveredContentClass.ISSUE_DOCUMENT_EMBEDDED_RATIONALE.provides_full_rationale
        )
        self.assertFalse(
            RecoveredContentClass.RATING_ACTION_INTIMATION.provides_full_rationale
        )

    def test_candidate_host_must_match_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "retrieval_host"):
            RecoveryCandidate(
                candidate_id=None,
                recovery_target_id=1,
                artifact_id=10,
                candidate_url="https://www.bseindia.com/corporates/a.pdf",
                retrieval_host="nsearchives.nseindia.com",
                source_type=RecoverySourceType.BSE_EXCHANGE_ATTACHMENT,
                discovered_at_utc="2026-08-16T00:00:00+00:00",
            )


if __name__ == "__main__":
    unittest.main()
