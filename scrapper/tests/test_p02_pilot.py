from __future__ import annotations

from scrapper.tests.database_fixtures import prepare_database

import hashlib
import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from contextlib import closing
from pathlib import Path

from scrapper.core.http import FetchResponse
from scrapper.pipeline.screener_vertical import ingest_screener_response
from scrapper.sources.screener.company import parse_company_page
from scrapper.sources.screener.manifest import audit_screener_fixture_coverage
from scrapper.sources.screener.peers import parse_peer_table
from scrapper.sources.screener.urls import company_url, peer_table_url


TEST_ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = TEST_ROOT / "fixtures" / "screener" / "p02"
GOLDEN = TEST_ROOT / "golden" / "p02_five_company_expected.json"
FIRST_OBSERVED = "2026-08-04T17:03:53+00:00"


def response(url: str, content: bytes) -> FetchResponse:
    return FetchResponse(
        requested_url=url,
        final_url=url,
        status_code=200,
        content=content,
        content_type="text/html; charset=utf-8",
        encoding="utf-8",
        etag=None,
        last_modified=None,
        auth_mode="public",
        fetch_status="ok",
    )


class FiveCompanyPilotTests(unittest.TestCase):
    def test_pilot_fixtures_cover_varied_structures_without_identity_drift(self) -> None:
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))["companies"]
        self.assertEqual(len(expected), 5)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = root / "scraper.db"
            accepted_symbols: list[str] = []

            for company in expected:
                symbol = company["symbol"]
                main = (FIXTURE_ROOT / f"{symbol}_consolidated.html").read_bytes()
                peer = (FIXTURE_ROOT / f"{symbol}_peers.html").read_bytes()
                self.assertEqual(hashlib.sha256(main).hexdigest(), company["main_sha256"])
                self.assertEqual(hashlib.sha256(peer).hexdigest(), company["peer_sha256"])
                self.assertEqual(audit_screener_fixture_coverage(main), [], symbol)

                parsed = parse_company_page(main, FIRST_OBSERVED)
                parsed_peer = parse_peer_table(peer, parsed.source_company_id)
                financials = (
                    parsed.financials
                    + parsed.pnl_financials
                    + parsed.balance_financials
                    + parsed.cash_financials
                    + parsed.ratios_financials
                )
                shareholding = (
                    parsed.shareholding_q_observations
                    + parsed.shareholding_y_observations
                )
                self.assertEqual(parsed.nse_symbol, symbol)
                self.assertEqual(parsed.company_name, company["company_name"])
                self.assertEqual(parsed.source_company_id, company["source_company_id"])
                self.assertEqual(parsed.source_warehouse_id, company["warehouse_id"])
                self.assertEqual(len(parsed.metrics), company["metrics"])
                self.assertEqual(
                    dict(sorted(Counter(row.section_code for row in financials).items())),
                    company["financials"],
                )
                self.assertEqual(len(parsed.growth_observations), company["growth"])
                self.assertEqual(
                    dict(sorted(Counter(row.frequency for row in shareholding).items())),
                    company["shareholding"],
                )
                self.assertEqual(
                    dict(sorted(Counter(row.document_type for row in parsed.document_links).items())),
                    company["documents"],
                )
                self.assertEqual(len(parsed_peer.observations), company["peers"])

                prepare_database(db_path)
                with self.assertRaisesRegex(ValueError, "standalone page"):
                    ingest_screener_response(
                        symbol, "consolidated",
                        response(f"https://www.screener.in/company/{symbol}/consolidated/", main),
                        response(peer_table_url(company["warehouse_id"]), peer),
                        db_path=db_path, snapshot_root=root / "snapshots",
                        export_root=root / "exports", fetched_at_utc=FIRST_OBSERVED,
                    )
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM accepted_page_versions").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM financial_observations").fetchone()[0], 0)



if __name__ == "__main__":
    unittest.main()
