from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from pathlib import Path

from scrapper.core.access_policy import BlockedTermsError
from scrapper.core.http import fetch_exchange_recovery_surface

from scrapper.documents.recovery import (
    DocumentOriginator,
    RecoverySourceType,
    RecoveryTarget,
)
from scrapper.sources.documents.bse_recovery import BSECorporateRatingResolver
from scrapper.sources.documents.debt_recovery import DebtIssueDocumentResolver
from scrapper.sources.documents.issuer_ir_recovery import IssuerIRResolver
from scrapper.sources.documents.nse_recovery import NSECorporateAnnouncementResolver
from scrapper.sources.documents.recovery_common import ISSUE_DOCUMENT_TERMS


NOW = "2026-08-16T00:00:00+00:00"
FIXTURES = Path(__file__).parent / "fixtures" / "documents"


def target() -> RecoveryTarget:
    return RecoveryTarget(
        recovery_target_id=1,
        artifact_id=2,
        company_id=3,
        artifact_type="credit_rating",
        document_originator=DocumentOriginator.CRISIL,
        blocked_source_id=4,
        blocked_source_url="https://www.crisilratings.com/report.pdf",
        blocked_source_host="www.crisilratings.com",
        company_canonical_name="Fixture Limited",
        nse_symbols=("FIXTURE",),
        bse_codes=("500001",),
        isins=("INE000A01000",),
        event_date="2026-08-01",
    )


class NSERecoveryAdapterTests(unittest.TestCase):
    def test_live_shape_recovers_explicit_cra_and_sm_isin(self) -> None:
        resolver = NSECorporateAnnouncementResolver()
        query = resolver.build_queries(target())[0]
        payload = (FIXTURES / "da06_nse_live_rating.json").read_bytes()
        candidate = resolver.parse_response(
            payload, query=query, discovered_at_utc=NOW
        )[0]
        self.assertEqual(candidate.document_originator, "CRISIL")
        self.assertEqual(candidate.company_identifiers["ISIN"], ("INE158B01016",))
        self.assertEqual(candidate.publication_date, "2025-05-22")

    def test_finds_actual_attachment_and_preserves_announcement_provenance(self) -> None:
        resolver = NSECorporateAnnouncementResolver()
        query = resolver.build_queries(target(), window_days=7)[0]
        payload = [{
            "symbol": "FIXTURE",
            "sm_name": "Fixture Limited",
            "desc": "Credit Rating Reaffirmation",
            "attchmntText": "Credit Rating",
            "attchmntFile": "https://nsearchives.nseindia.com/corporate/FIXTURE_01082026.pdf",
            "an_dt": "01-Aug-2026 13:30:00",
            "seq_id": "NSE-123",
            "isin": "INE000A01000",
        }]
        candidates = resolver.parse_response(
            json.dumps(payload).encode(), query=query, discovered_at_utc=NOW
        )
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.source_type, RecoverySourceType.NSE_EXCHANGE_ATTACHMENT)
        self.assertEqual(candidate.source_evidence["announcement_id"], "NSE-123")
        self.assertEqual(candidate.publication_date, "2026-08-01")
        self.assertEqual(candidate.availability_basis, "exchange_filing_timestamp")
        self.assertTrue(candidate.candidate_url.endswith(".pdf"))

    def test_unrelated_announcement_is_not_a_candidate(self) -> None:
        resolver = NSECorporateAnnouncementResolver()
        query = resolver.build_queries(target())[0]
        payload = [{
            "symbol": "FIXTURE", "desc": "Board meeting outcome",
            "attchmntFile": "https://nsearchives.nseindia.com/corporate/board.pdf",
        }]
        self.assertEqual(
            resolver.parse_response(json.dumps(payload).encode(), query=query,
                                    discovered_at_utc=NOW),
            (),
        )

    def test_query_symbol_is_not_promoted_to_returned_identifier_evidence(self) -> None:
        resolver = NSECorporateAnnouncementResolver()
        query = resolver.build_queries(target())[0]
        payload = [{
            "sm_name": "Fixture Limited",
            "desc": "Credit Rating Reaffirmation",
            "attchmntFile": "https://nsearchives.nseindia.com/corporate/rating.pdf",
            "an_dt": "01-Aug-2026 13:30:00",
        }]
        candidate = resolver.parse_response(
            json.dumps(payload).encode(), query=query, discovered_at_utc=NOW
        )[0]
        self.assertNotIn("NSE", candidate.company_identifiers)
        self.assertEqual(candidate.source_evidence["query_identifier_value"], "FIXTURE")
        self.assertEqual(candidate.source_evidence["returned_identifier_kinds"], [])

    def test_search_without_event_date_is_refused_not_broadened(self) -> None:
        missing = RecoveryTarget(**{**target().__dict__, "event_date": None})
        with self.assertRaisesRegex(ValueError, "bounded"):
            NSECorporateAnnouncementResolver().build_queries(missing)


class BSERecoveryAdapterTests(unittest.TestCase):
    def test_live_fractional_iso_timestamp_is_preserved(self) -> None:
        resolver = BSECorporateRatingResolver()
        query = resolver.build_queries(target())[0]
        payload = (FIXTURES / "da06_bse_live_fractional.json").read_bytes()
        candidate = resolver.parse_response(
            payload, query=query, discovered_at_utc=NOW
        )[0]
        self.assertEqual(candidate.publication_date, "2024-04-03")
        self.assertEqual(candidate.published_at_utc, "2024-04-03T11:14:01.250000+00:00")
        self.assertIsNone(candidate.document_originator)
        self.assertIn("/xml-data/corpfiling/AttachHis/", candidate.candidate_url)
        self.assertEqual(candidate.source_evidence["attachment_archive"], "historical")

    def test_finds_rating_attachment_and_preserves_filing_provenance(self) -> None:
        resolver = BSECorporateRatingResolver()
        query = resolver.build_queries(target(), window_days=10)[0]
        payload = {"Table": [{
            "SCRIP_CD": "500001",
            "SLONGNAME": "Fixture Limited",
            "NEWSSUB": "Revision in Credit Rating",
            "SUBCATNAME": "Credit Rating",
            "ATTACHMENTNAME": "fixture-rating.pdf",
            "DT_TM": "01/08/2026 13:30:00",
            "NEWSID": "BSE-456",
            "ISIN": "INE000A01000",
        }]}
        candidates = resolver.parse_response(
            json.dumps(payload).encode(), query=query, discovered_at_utc=NOW
        )
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.source_evidence["filing_id"], "BSE-456")
        self.assertEqual(candidate.retrieval_host, "www.bseindia.com")
        self.assertIn("/xml-data/corpfiling/AttachLive/", candidate.candidate_url)

    def test_issue_document_is_a_separate_first_class_path(self) -> None:
        resolver = BSECorporateRatingResolver()
        query = resolver.build_queries(target())[0]
        payload = {"Table": [{
            "SCRIP_CD": "500001", "SLONGNAME": "Fixture Limited",
            "NEWSSUB": "Placement Memorandum for bond issue",
            "SUBCATNAME": "Debt Listing", "ATTACHMENTNAME": "pm.pdf",
            "DT_TM": "01/08/2026 13:30:00", "NEWSID": "BSE-DEBT-1",
        }]}
        observations = resolver.parse_response(
            json.dumps(payload).encode(), query=query, discovered_at_utc=NOW,
            terms=ISSUE_DOCUMENT_TERMS,
        )
        selected = DebtIssueDocumentResolver().select(observations, exchange="BSE")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].source_type, RecoverySourceType.BSE_DEBT_DOCUMENT)

    def test_query_scrip_is_not_promoted_to_returned_identifier_evidence(self) -> None:
        resolver = BSECorporateRatingResolver()
        query = resolver.build_queries(target())[0]
        payload = {"Table": [{
            "SLONGNAME": "Fixture Limited", "NEWSSUB": "Credit Rating",
            "ATTACHMENTNAME": "rating.pdf", "DT_TM": "01/08/2026 13:30:00",
        }]}
        candidate = resolver.parse_response(
            json.dumps(payload).encode(), query=query, discovered_at_utc=NOW
        )[0]
        self.assertNotIn("BSE", candidate.company_identifiers)
        self.assertEqual(candidate.source_evidence["returned_identifier_kinds"], [])


class IssuerIRRecoveryAdapterTests(unittest.TestCase):
    def test_discovery_is_one_page_same_host_and_never_follows_blocked_link(self) -> None:
        html = b"""
          <a href='/investors/rating-rationale.pdf'>Credit Rating Rationale</a>
          <a href='https://www.crisilratings.com/forbidden.pdf'>Rating report</a>
          <a href='https://other.example/rating.pdf'>Credit rating</a>
        """
        candidates = IssuerIRResolver().parse_saved_page(
            html,
            discovery_page_url="https://issuer.example/investor-relations/ratings",
            company_id=3,
            discovered_at_utc=NOW,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].candidate_url,
                         "https://issuer.example/investors/rating-rationale.pdf")
        self.assertEqual(candidates[0].source_evidence["bounded_depth"], 0)


class _RedirectResponse:
    def __init__(self, url: str, location: str) -> None:
        self.url = url
        self.status_code = 302
        self.headers = {"Location": location, "Content-Type": "text/plain"}
        self.content = b""
        self.encoding = "utf-8"


class _RedirectSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, **_: object) -> _RedirectResponse:
        self.calls.append(url)
        return _RedirectResponse(
            url, "https://blocked.example/forbidden.pdf"
        )


class RecoveryHTTPPolicyTests(unittest.TestCase):
    @patch("scrapper.core.access_policy.BLOCKED_TERMS_HOSTS", frozenset({"blocked.example"}))
    def test_exchange_redirect_to_blocked_publisher_stops_before_second_request(self) -> None:
        session = _RedirectSession()
        url = "https://www.nseindia.com/api/corporate-announcements?symbol=FIXTURE"
        with self.assertRaises(BlockedTermsError):
            fetch_exchange_recovery_surface(url, "NSE", session=session)
        self.assertEqual(session.calls, [url])


if __name__ == "__main__":
    unittest.main()
