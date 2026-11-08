from __future__ import annotations

import csv
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scrapper.commands.financial_coverage_audit import (
    build_financial_coverage_report,
    run_financial_coverage_audit,
)
from scrapper.db.database import connect
from scrapper.tests.database_fixtures import prepare_database
from scrapper.tests.test_screener_vertical import (
    FIRST_FETCH,
    fixture_quick_ratios_response,
    fixture_standalone_peer_response,
    fixture_standalone_response,
)
from scrapper.pipeline.screener_vertical import ingest_screener_response


class FinancialCoverageAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "audit.sqlite3"
        prepare_database(self.db)
        self.ingested = ingest_screener_response(
            "LAURUSLABS", "standalone", fixture_standalone_response(),
            fixture_standalone_peer_response(), fixture_quick_ratios_response(),
            db_path=self.db, snapshot_root=self.root / "snapshots",
            export_root=self.root / "exports", fetched_at_utc=FIRST_FETCH,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_zero_missing_value_and_missing_observation_are_distinct(self):
        with closing(connect(self.db)) as conn:
            with conn:
                conn.execute(
                    """UPDATE financial_observations
                          SET display_value='0', value_decimal='0', missing_kind=NULL
                        WHERE parse_id=? AND section_code='quarters'
                          AND line_item_code='net_profit' AND period_end='2026-06-30'""",
                    (self.ingested.parse_id,),
                )
                conn.execute(
                    """UPDATE financial_observations
                          SET display_value='-', value_decimal=NULL, missing_kind='not_reported'
                        WHERE parse_id=? AND section_code='quarters'
                          AND line_item_code='sales' AND period_end='2026-06-30'""",
                    (self.ingested.parse_id,),
                )
                conn.execute(
                    """DELETE FROM financial_observations
                        WHERE parse_id=? AND section_code='quarters'
                          AND line_item_code='expenses' AND period_end='2026-06-30'""",
                    (self.ingested.parse_id,),
                )
            report = build_financial_coverage_report(conn)
        rows = {
            (row["metric_code"], row["period_end"]): row
            for row in report["rows"]
            if row["section_code"] == "quarters"
        }
        self.assertEqual(rows[("net_profit", "2026-06-30")]["status"], "PRESENT_ZERO")
        self.assertEqual(rows[("sales", "2026-06-30")]["status"], "MISSING_VALUE")
        self.assertEqual(rows[("expenses", "2026-06-30")]["status"], "MISSING_OBSERVATION")

    def test_report_is_deterministic_and_select_only(self):
        with closing(connect(self.db, read_only=True)) as conn:
            before = conn.total_changes
            first = build_financial_coverage_report(conn)
            second = build_financial_coverage_report(conn)
            self.assertEqual(first, second)
            self.assertEqual(conn.total_changes, before)
            self.assertTrue(first["read_only"])
            self.assertGreater(first["summary"]["metric_period_cells"], 0)

    def test_json_and_csv_exports_are_repeatable(self):
        with closing(connect(self.db, read_only=True)) as conn:
            report = build_financial_coverage_report(conn)
            json_path = self.root / "reports" / "coverage.json"
            csv_path = self.root / "reports" / "coverage.csv"
            run_financial_coverage_audit(
                conn=conn, json_path=json_path, csv_path=csv_path,
            )
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload, report)
        with csv_path.open(encoding="utf-8", newline="") as stream:
            csv_rows = list(csv.DictReader(stream))
        self.assertEqual(len(csv_rows), len(report["rows"]))


if __name__ == "__main__":
    unittest.main()
