from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from scrapper.db.mfdata_repo import persist_and_crosscheck_mfdata
from scrapper.exports.mfdata_crosscheck_json_export import (
    build_mfdata_crosscheck_export,
)
from scrapper.sources.mfdata.adapter import MFDataParseError, parse_mfdata_holdings
from scrapper.tests import test_m06_stock_features as m06_fixture


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "mfdata" / "ppfas_active_a_2026_06_conflict.json"


class MFDataCrosscheckM09Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.official = m06_fixture.MFStockMonthlyFeaturesM06Tests(
            "test_exact_scopes_counts_decimal_values_and_lineage_match_golden"
        )
        self.official.setUp()
        self.official._seed_import("2026-06")
        self.conn = self.official.conn
        self.raw_bytes = FIXTURE.read_bytes()
        self.parsed = parse_mfdata_holdings(
            self.raw_bytes,
            requested_family_id=8292,
            requested_disclosure_month="2026-06",
        )

    def tearDown(self) -> None:
        self.official.tearDown()

    def _persist(self, observed_at: str = "2026-08-05T04:00:00+00:00"):
        return persist_and_crosscheck_mfdata(
            self.conn,
            parsed=self.parsed,
            raw_bytes=self.raw_bytes,
            publisher="ppfas",
            scheme_code="ACTIVE_A",
            submitted_path=str(FIXTURE),
            immutable_path="/immutable/mfdata-fixture.json",
            observed_at_utc=observed_at,
        )

    def test_saved_response_parser_is_complete_exact_and_identity_checked(self) -> None:
        self.assertEqual(self.parsed.source, "mfdata")
        self.assertEqual(self.parsed.source_quality, "third_party_unverified")
        self.assertEqual(self.parsed.provider_family_id, 8292)
        self.assertEqual(self.parsed.disclosure_month, "2026-06")
        self.assertEqual(len(self.parsed.holdings), 2)
        alpha, beta = self.parsed.holdings
        self.assertEqual(alpha.quantity_decimal, "999")
        self.assertEqual(alpha.market_value_cr_decimal, "0.01")
        self.assertEqual(alpha.market_value_lakh_decimal, "1.00")
        self.assertEqual(beta.percentage_of_nav_decimal, "0.80")
        self.assertEqual(beta.market_value_lakh_decimal, "2.00")
        self.assertEqual(
            json.loads(alpha.source_row_json)["sector"], "Test Sector"
        )

        with self.assertRaises(MFDataParseError) as family_error:
            parse_mfdata_holdings(
                self.raw_bytes,
                requested_family_id=9999,
                requested_disclosure_month="2026-06",
            )
        self.assertEqual(family_error.exception.code, "family_id_mismatch")
        with self.assertRaises(MFDataParseError) as month_error:
            parse_mfdata_holdings(
                self.raw_bytes,
                requested_family_id=8292,
                requested_disclosure_month="2026-05",
            )
        self.assertEqual(month_error.exception.code, "disclosure_month_mismatch")

    def test_conflict_is_retained_but_official_wins_and_gap_uses_fallback(self) -> None:
        official_count = self.conn.execute(
            "SELECT COUNT(*) FROM mf_holdings"
        ).fetchone()[0]
        result = self._persist()
        self.assertEqual(result.comparison_status, "passed_with_warnings")
        self.assertEqual(result.official_observation_count, 1)
        self.assertEqual(result.third_party_observation_count, 2)
        self.assertEqual(result.conflict_count, 1)
        self.assertEqual(result.third_party_only_count, 1)
        self.assertTrue(result.official_precedence_enforced)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM mf_holdings").fetchone()[0],
            official_count,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM mfdata_holdings").fetchone()[0],
            2,
        )
        alpha_evidence = self.conn.execute(
            """SELECT source, source_quality, quantity_decimal,
                      source_precedence_rank, is_preferred
               FROM mf_holding_source_evidence
               WHERE publisher='ppfas' AND scheme_code='ACTIVE_A'
                 AND disclosure_month='2026-06' AND isin_normalized='IN0020240076'
               ORDER BY source_precedence_rank"""
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in alpha_evidence],
            [
                ("amc_workbook", "official", "12", 1, 1),
                ("mfdata", "third_party_unverified", "999", 2, 0),
            ],
        )
        preferred = self.conn.execute(
            """SELECT isin_normalized, source, source_quality, quantity_decimal
               FROM mf_preferred_holdings
               WHERE publisher='ppfas' AND scheme_code='ACTIVE_A'
                 AND disclosure_month='2026-06'
               ORDER BY isin_normalized"""
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in preferred],
            [
                ("IN0020240076", "amc_workbook", "official", "12"),
                ("IN002025Z187", "mfdata", "third_party_unverified", "10"),
            ],
        )

        report = build_mfdata_crosscheck_export(
            self.conn, result.mfdata_crosscheck_run_id
        )
        self.assertEqual(report["schema_version"], "1.0.0")
        self.assertEqual(report["summary"]["conflict_count"], 1)
        self.assertEqual(report["summary"]["gap_count"], 1)
        conflict = next(
            item for item in report["reconciliation"]
            if item["result_status"] == "conflict"
        )
        self.assertEqual(conflict["preferred_source"], "amc_workbook")
        self.assertEqual(
            set(conflict["conflict_fields"]), {"quantity", "percentage_of_nav"}
        )
        fallback = next(
            item for item in report["reconciliation"]
            if item["result_status"] == "third_party_only"
        )
        self.assertEqual(fallback["preferred_source"], "mfdata")

    def test_duplicate_is_idempotent_and_official_revision_still_wins(self) -> None:
        first = self._persist()
        second = self._persist("2026-08-05T04:01:00+00:00")
        self.assertTrue(second.duplicate)
        self.assertEqual(second.mfdata_snapshot_id, first.mfdata_snapshot_id)
        self.assertEqual(
            second.mfdata_crosscheck_run_id, first.mfdata_crosscheck_run_id
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM mfdata_snapshots").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM mfdata_snapshot_observations"
            ).fetchone()[0],
            2,
        )

        self.official._seed_import(
            "2026-06", suffix="m09-revised", alpha_active_a_current=("20", "2.00")
        )
        revised = self._persist("2026-08-05T04:02:00+00:00")
        self.assertTrue(revised.duplicate)
        self.assertNotEqual(
            revised.mfdata_crosscheck_run_id, first.mfdata_crosscheck_run_id
        )
        preferred_alpha = self.conn.execute(
            """SELECT source, quantity_decimal FROM mf_preferred_holdings
               WHERE publisher='ppfas' AND scheme_code='ACTIVE_A'
                 AND disclosure_month='2026-06' AND isin_normalized='IN0020240076'"""
        ).fetchone()
        self.assertEqual(tuple(preferred_alpha), ("amc_workbook", "20"))
        original_report = build_mfdata_crosscheck_export(
            self.conn, first.mfdata_crosscheck_run_id
        )
        original_official = next(
            item
            for item in original_report["normalized"]["preferred_holding_evidence"]
            if item["source"] == "amc_workbook"
            and item["isin_normalized"] == "IN0020240076"
        )
        self.assertEqual(original_official["quantity_decimal"], "12")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM mfdata_holdings").fetchone()[0],
            2,
        )

    def test_schema_rejects_false_source_quality(self) -> None:
        result = self._persist()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute(
                    """UPDATE mfdata_snapshots SET source_quality='official'
                       WHERE mfdata_snapshot_id=?""",
                    (result.mfdata_snapshot_id,),
                )


if __name__ == "__main__":
    unittest.main()
