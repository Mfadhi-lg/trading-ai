from __future__ import annotations

import unittest
from dataclasses import replace

from scrapper.documents.rating_evidence import classify_rating_evidence
from scrapper.documents.recovery import (
    DocumentOriginator,
    RecoveredContentClass,
    RecoveryConfidence,
    RecoverySourceType,
    RecoveryTarget,
)
from scrapper.documents.recovery_matching import arbitrate_candidates, match_candidate
from scrapper.sources.documents.recovery_common import OfficialCandidateObservation


NOW = "2026-08-16T00:00:00+00:00"


def target() -> RecoveryTarget:
    return RecoveryTarget(
        recovery_target_id=1, artifact_id=2, company_id=3,
        artifact_type="credit_rating", document_originator=DocumentOriginator.CRISIL,
        blocked_source_id=4,
        blocked_source_url="https://www.crisilratings.com/report.pdf",
        blocked_source_host="www.crisilratings.com",
        company_canonical_name="Fixture Limited", nse_symbols=("FIXTURE",),
        bse_codes=("500001",), isins=("INE000A01000",), event_date="2026-08-01",
        rating="AA+", outlook="Stable", instrument_or_facility="Term Loan",
    )


def observation(**overrides: object) -> OfficialCandidateObservation:
    values = dict(
        candidate_url="https://nsearchives.nseindia.com/corporate/rating.pdf",
        retrieval_host="nsearchives.nseindia.com",
        source_type=RecoverySourceType.NSE_EXCHANGE_ATTACHMENT,
        discovery_page_url="https://www.nseindia.com/api/corporate-announcements",
        document_title="CRISIL Credit Rating Reaffirmation",
        publication_date="2026-08-01",
        published_at_utc="2026-08-01T08:00:00+00:00",
        available_at_utc="2026-08-01T08:00:00+00:00",
        availability_basis="exchange_filing_timestamp",
        company_identifiers={"ISIN": ("INE000A01000",), "NSE": ("FIXTURE",)},
        document_originator="CRISIL", rating="AA+", outlook="Stable",
        instrument_or_facility="Term Loan",
        source_evidence={"issuer": "Fixture Limited", "announcement_id": "A1"},
    )
    values.update(overrides)
    return OfficialCandidateObservation(**values)


class CandidateMatchingTests(unittest.TestCase):
    def test_same_company_cra_date_rating_is_exact(self) -> None:
        candidate = match_candidate(target(), observation(), discovered_at_utc=NOW)
        self.assertEqual(candidate.confidence, RecoveryConfidence.EXACT)
        self.assertTrue(candidate.matching_features["company_identity_match"])
        self.assertTrue(candidate.matching_features["agency_match"])
        self.assertEqual(candidate.matching_features["date_distance_days"], 0)
        self.assertTrue(candidate.matching_features["rating_match"])

    def test_wrong_company_is_rejected(self) -> None:
        candidate = match_candidate(
            target(), observation(company_identifiers={"ISIN": ("INE999Z01000",)}),
            discovered_at_utc=NOW,
        )
        self.assertEqual(candidate.confidence, RecoveryConfidence.NO_MATCH)
        self.assertIn("company", candidate.rejection_reason or "")

    def test_query_identifier_echo_is_not_independent_identity_evidence(self) -> None:
        candidate = match_candidate(
            target(),
            observation(
                company_identifiers={"NSE": ("FIXTURE",)},
                source_evidence={
                    "issuer": "Fixture Limited",
                    "query_identifier_kind": "NSE",
                    "query_identifier_value": "FIXTURE",
                },
            ),
            discovered_at_utc=NOW,
        )
        self.assertIsNone(candidate.matching_features["company_identity_match"])
        self.assertEqual(candidate.matching_features["company_identity_basis"], "unproven")
        self.assertEqual(candidate.confidence, RecoveryConfidence.AMBIGUOUS)

    def test_wrong_cra_is_rejected(self) -> None:
        candidate = match_candidate(
            target(), observation(document_originator="ICRA", document_title="ICRA Credit Rating"),
            discovered_at_utc=NOW,
        )
        self.assertEqual(candidate.confidence, RecoveryConfidence.NO_MATCH)
        self.assertIn("CRA", candidate.rejection_reason or "")

    def test_clearly_different_rating_event_is_rejected(self) -> None:
        candidate = match_candidate(
            target(), observation(publication_date="2026-03-01", rating="A"),
            discovered_at_utc=NOW,
        )
        self.assertEqual(candidate.confidence, RecoveryConfidence.NO_MATCH)

    def test_multiple_plausible_candidates_are_ambiguous(self) -> None:
        first = match_candidate(target(), observation(), discovered_at_utc=NOW)
        second = match_candidate(
            target(), observation(candidate_url="https://nsearchives.nseindia.com/corporate/rating-2.pdf"),
            discovered_at_utc=NOW,
        )
        decided = arbitrate_candidates((first, second))
        self.assertEqual({item.confidence for item in decided}, {RecoveryConfidence.AMBIGUOUS})
        self.assertFalse(any(item.may_auto_attach for item in decided))

    def test_blocked_candidate_is_policy_rejected_before_matching(self) -> None:
        candidate = match_candidate(
            target(),
            observation(
                candidate_url="https://www.crisilratings.com/redirected.pdf",
                retrieval_host="www.crisilratings.com",
                source_type=RecoverySourceType.ISSUER_IR_ATTACHMENT,
            ),
            discovered_at_utc=NOW,
        )
        self.assertEqual(candidate.confidence, RecoveryConfidence.REJECTED_POLICY)


class ContentClassificationTests(unittest.TestCase):
    def test_full_rationale_and_intimation_are_distinct(self) -> None:
        full = classify_rating_evidence([{
            "page_number": 1,
            "text": "CRISIL Rating Rationale. Key Rating Drivers. Liquidity: Strong. "
                    "Rating Sensitivities. Factors that could lead to a downgrade.",
        }], source_type=RecoverySourceType.NSE_EXCHANGE_ATTACHMENT)
        intimation = classify_rating_evidence([{
            "page_number": 1,
            "text": "Intimation regarding credit rating. CRISIL has reaffirmed AA+ Stable.",
        }], source_type=RecoverySourceType.NSE_EXCHANGE_ATTACHMENT)
        self.assertEqual(full.content_class, RecoveredContentClass.FULL_RATING_RATIONALE)
        self.assertEqual(intimation.content_class,
                         RecoveredContentClass.RATING_ACTION_INTIMATION)
        self.assertFalse(intimation.content_class.provides_full_rationale)

    def test_issue_document_requires_actual_embedded_rationale_structure(self) -> None:
        metadata_only = classify_rating_evidence([{
            "page_number": 1, "text": "The bonds are rated CRISIL AA+."
        }], source_type=RecoverySourceType.BSE_DEBT_DOCUMENT)
        embedded = classify_rating_evidence([{
            "page_number": 42,
            "text": "CRISIL Rating Rationale. Key Rating Drivers. Liquidity. "
                    "Rating Sensitivities and downgrade factors.",
        }], source_type=RecoverySourceType.BSE_DEBT_DOCUMENT)
        self.assertEqual(metadata_only.content_class,
                         RecoveredContentClass.UNKNOWN_RATING_DOCUMENT)
        self.assertEqual(embedded.content_class,
                         RecoveredContentClass.ISSUE_DOCUMENT_EMBEDDED_RATIONALE)
        self.assertEqual(embedded.annexure_page_range, (42, 42))

    def test_non_crisil_official_agency_rationale_is_classified(self) -> None:
        care = classify_rating_evidence([{
            "page_number": 1,
            "text": "CARE Ratings rating rationale. Key Rating Drivers. Liquidity: "
                    "Adequate. Rating Sensitivities and downgrade factors.",
        }], source_type=None)
        self.assertEqual(
            care.content_class,
            RecoveredContentClass.FULL_RATING_RATIONALE,
        )


if __name__ == "__main__":
    unittest.main()
