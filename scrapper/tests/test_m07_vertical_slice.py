from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scrapper.db.database import connect, init_db
from scrapper.db.mf_change_repo import generate_scheme_month_changes
from scrapper.db.mf_feature_repo import generate_stock_month_features
from scrapper.exports.mf_coverage_report import (
    build_mf_coverage_report,
    expected_months,
)
from scrapper.tests import test_m06_stock_features as m06_fixture


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_INVENTORY = (
    ROOT / "sources" / "manifests" / "ppfas_m07_official_workbooks.json"
)


class PPFASVerticalSliceM07Tests(unittest.TestCase):
    def test_official_local_inventory_has_all_pinned_workbook_hashes(self) -> None:
        inventory = json.loads(OFFICIAL_INVENTORY.read_text(encoding="utf-8"))
        self.assertEqual(inventory["publisher"], "ppfas")
        self.assertEqual(len(inventory["workbooks"]), 12)
        self.assertEqual(
            [item["month"] for item in inventory["workbooks"]],
            expected_months("2026-06", 12),
        )
        for item in inventory["workbooks"]:
            workbook = (
                ROOT / "store" / "mf_import" / "inbox" / "ppfas"
                / item["month"] / item["filename"]
            )
            self.assertTrue(workbook.is_file(), workbook)
            self.assertEqual(
                hashlib.sha256(workbook.read_bytes()).hexdigest(), item["sha256"]
            )

    def test_empty_slice_reports_exact_missing_months_and_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            conn = connect(Path(temporary) / "scraper.sqlite")
            try:
                init_db(conn)
                report = build_mf_coverage_report(
                    conn, publisher="ppfas", through_month="2026-06"
                )
            finally:
                conn.close()
        self.assertEqual(report["schema_version"], "1.0.0")
        self.assertEqual(report["selection"]["start_month"], "2025-07")
        self.assertEqual(report["selection"]["expected_month_count"], 12)
        self.assertEqual(report["selection"]["expected_pair_count"], 11)
        self.assertEqual(report["coverage_status"], "incomplete")
        self.assertEqual(report["summary"]["accepted_workbook_month_count"], 0)
        self.assertEqual(report["summary"]["complete_comparison_count"], 0)
        self.assertEqual(len(report["coverage"]["workbooks"]), 12)
        self.assertEqual(len(report["coverage"]["month_pairs"]), 11)
        self.assertEqual(set(report["rules"].values()), {"failed"})

    def test_twelve_complete_months_produce_eleven_covered_feature_pairs(self) -> None:
        # Reuse the focused M06 database builder: it creates accepted, classified,
        # security-resolved official-workbook rows without pretending that its
        # synthetic bytes are M07 operator-supplied workbooks.
        fixture = m06_fixture.MFStockMonthlyFeaturesM06Tests(
            "test_exact_scopes_counts_decimal_values_and_lineage_match_golden"
        )
        fixture.setUp()
        try:
            months = expected_months("2026-06", 12)
            for month in months:
                fixture._seed_import(month)
            for index, month in enumerate(months[1:], start=1):
                changes = generate_scheme_month_changes(
                    fixture.conn,
                    publisher="ppfas",
                    current_month=month,
                    generated_at_utc=f"2026-08-05T01:{index:02d}:00+00:00",
                )
                self.assertEqual(changes.generated_change_sets, 7)
                features = generate_stock_month_features(
                    fixture.conn,
                    publisher="ppfas",
                    current_month=month,
                    generated_at_utc=f"2026-08-05T02:{index:02d}:00+00:00",
                )
                self.assertEqual(features.aggregation_status, "generated")
            report = build_mf_coverage_report(
                fixture.conn, publisher="ppfas", through_month="2026-06"
            )
        finally:
            fixture.tearDown()

        self.assertEqual(report["coverage_status"], "complete")
        self.assertEqual(report["summary"]["accepted_workbook_month_count"], 12)
        self.assertEqual(report["summary"]["complete_scheme_month_count"], 12)
        self.assertEqual(
            report["summary"]["complete_security_accounting_month_count"], 12
        )
        self.assertEqual(report["summary"]["complete_comparison_count"], 11)
        self.assertEqual(report["summary"]["complete_feature_month_count"], 11)
        self.assertEqual(report["summary"]["unexplained_dropped_holding_count"], 0)
        self.assertEqual(set(report["rules"].values()), {"passed"})
        for pair in report["coverage"]["month_pairs"]:
            self.assertEqual(pair["generated_change_set_count"], 7)
            self.assertGreater(pair["all_scheme_feature_count"], 0)
            self.assertGreater(pair["active_equity_feature_count"], 0)


if __name__ == "__main__":
    unittest.main()
