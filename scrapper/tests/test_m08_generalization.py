from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from scrapper.db.database import connect
from scrapper.pipeline.import_mf import execute_import, plan_import
from scrapper.sources.amc_workbook.adapters import get_workbook_adapter
from scrapper.sources.amc_workbook.ppfas import PPFASStructureError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "mf_workbook" / "m08"
INVENTORY = ROOT / "sources" / "manifests" / "m08_official_workbooks.json"
GOLDEN = ROOT / "tests" / "golden" / "m08_generalization_expected.json"


def fixture_path(publisher: str, month: str, filename: str) -> Path:
    return FIXTURE_ROOT / publisher / month / filename


class MutualFundM08GeneralizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_four_official_files_are_pinned_and_parse_to_golden_contracts(self) -> None:
        self.assertEqual(len(self.inventory["publishers"]), 2)
        for publisher_entry in self.inventory["publishers"]:
            publisher = publisher_entry["publisher"]
            adapter = get_workbook_adapter(publisher)
            self.assertIsNotNone(adapter)
            self.assertEqual(len(publisher_entry["workbooks"]), 2)
            for workbook in publisher_entry["workbooks"]:
                month = workbook["month"]
                path = fixture_path(publisher, month, workbook["filename"])
                self.assertTrue(path.is_file(), path)
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(), workbook["sha256"]
                )
                parsed = adapter.parse(
                    path,
                    requested_publisher=publisher,
                    requested_disclosure_month=month,
                )
                holdings = adapter.normalize(parsed)
                inventory = parsed.inventory_dict()
                statuses = Counter(item.isin_validation_status for item in holdings)
                expected = self.golden[publisher][month]
                projection = {
                    "sheet_count": inventory["sheet_count"],
                    "portfolio_sheet_count": inventory["portfolio_sheet_count"],
                    "non_portfolio_sheet_count": inventory["non_portfolio_sheet_count"],
                    "cell_count": inventory["cell_count"],
                    "holding_count": len(holdings),
                    "valid_isin_count": statuses["valid"],
                    "missing_isin_count": statuses["missing"],
                }
                if publisher == "groww":
                    derivatives = tuple(
                        item for item in holdings if item.source_table_kind == "derivatives"
                    )
                    projection["derivative_holding_count"] = len(derivatives)
                    self.assertTrue(derivatives)
                    self.assertTrue(
                        all(
                            item.instrument_type_code == "derivative_future"
                            and item.isin_normalized is None
                            and item.isin_validation_status == "missing"
                            for item in derivatives
                        )
                    )
                self.assertEqual(projection, expected)
                self.assertFalse(any(key.startswith("invalid") for key in statuses))
                self.assertTrue(all(item.instrument_type_code != "unknown" for item in holdings))

                wrong_month = "2026-04" if month == "2026-05" else "2026-05"
                with self.assertRaises(PPFASStructureError) as raised:
                    adapter.parse(
                        path,
                        requested_publisher=publisher,
                        requested_disclosure_month=wrong_month,
                    )
                self.assertEqual(raised.exception.code, "disclosure_month_mismatch")

    def test_two_publishers_accept_and_produce_exact_consecutive_features(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            db_path = temporary_root / "m08.sqlite"
            import_results: dict[tuple[str, str], dict] = {}
            for publisher_entry in self.inventory["publishers"]:
                publisher = publisher_entry["publisher"]
                for position, workbook in enumerate(publisher_entry["workbooks"]):
                    month = workbook["month"]
                    path = fixture_path(publisher, month, workbook["filename"])
                    proposals = plan_import(
                        publisher=publisher,
                        disclosure_month=month,
                        path=path,
                        db_path=db_path,
                        snapshot_root=temporary_root / "snapshots",
                        import_root=temporary_root / "imports",
                    )
                    result = execute_import(
                        proposals,
                        db_path=db_path,
                        observed_at_utc=f"2026-08-05T0{position + 1}:00:00+00:00",
                        export_root=temporary_root / "exports",
                    )["files"][0]
                    import_results[(publisher, month)] = result
                    self.assertEqual(result["import_state"], "accepted")
                    self.assertEqual(result["finalization"]["validation_status"], "passed")

            conn = connect(db_path)
            try:
                for publisher in ("groww", "motilal_oswal"):
                    expected = self.golden[publisher]
                    june = import_results[(publisher, "2026-06")]
                    comparison = june["change_engine"]["comparisons"][0]
                    projection = {
                        "generated_change_sets": comparison["generated_change_sets"],
                        "skipped_change_sets": comparison["skipped_change_sets"],
                        "change_count": comparison["change_count"],
                        "change_class_counts": comparison["change_class_counts"],
                        "all_scheme_feature_count": june["stock_feature_engine"][0][
                            "all_scheme_feature_count"
                        ],
                        "active_equity_feature_count": june["stock_feature_engine"][0][
                            "active_equity_feature_count"
                        ],
                    }
                    self.assertEqual(projection, expected["comparison"])

                    feature_expected = expected["representative_feature"]
                    feature = conn.execute(
                        """SELECT * FROM mf_current_stock_monthly_features
                           WHERE publisher=? AND disclosure_month='2026-06'
                             AND feature_scope='all_scheme' AND isin_normalized=?""",
                        (publisher, feature_expected["isin_normalized"]),
                    ).fetchone()
                    self.assertIsNotNone(feature)
                    for key, value in feature_expected.items():
                        self.assertEqual(feature[key], value, (publisher, key))

                    rules = conn.execute(
                        """SELECT rule_code, passed FROM mf_workbook_validation_results
                           WHERE import_file_id=? AND rule_code LIKE ?""",
                        (
                            import_results[(publisher, "2026-06")]["import_file_id"],
                            f"{publisher}_%",
                        ),
                    ).fetchall()
                    self.assertEqual(len(rules), 9)
                    self.assertTrue(all(int(rule["passed"]) == 1 for rule in rules))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
