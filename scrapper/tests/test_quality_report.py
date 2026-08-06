from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scrapper.db.database import connect
from scrapper.exports.quality_report import (
    build_quality_report,
    write_quality_report,
)
from scrapper.pipeline.screener_vertical import ingest_screener_response
from scrapper.pipeline.trendlyne_vertical import ingest_trendlyne_response
from scrapper.tests.test_screener_vertical import (
    FIRST_FETCH as SCREENER_FETCH,
    FIXTURE as SCREENER_FIXTURE,
    fixture_peer_response,
    fixture_response as screener_response,
)
from scrapper.tests.test_trendlyne_t01 import (
    FIRST_FETCH as TRENDLYNE_FETCH,
    FIXTURE as TRENDLYNE_FIXTURE,
    fixture_response as trendlyne_response,
)


TEST_ROOT = Path(__file__).resolve().parent
GOLDEN = TEST_ROOT / "golden" / "p01_quality_report_expected.json"
GENERATED_AT = "2026-08-04T18:00:00+00:00"


class QualityReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db_path = root / "scraper.db"
        self.snapshots = root / "snapshots"
        self.exports = root / "exports"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _screener(self, content: bytes, fetched_at: str) -> None:
        ingest_screener_response(
            "LAURUSLABS",
            "consolidated",
            screener_response(content),
            fixture_peer_response(),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=fetched_at,
        )

    def _trendlyne(self, content: bytes, fetched_at: str) -> None:
        ingest_trendlyne_response(
            "RBA",
            trendlyne_response(content),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=fetched_at,
        )

    def test_report_explains_accepted_rejected_and_manual_review_results(self) -> None:
        self.maxDiff = None
        self._screener(SCREENER_FIXTURE.read_bytes(), SCREENER_FETCH)

        unknown_document = SCREENER_FIXTURE.read_bytes().replace(
            b">REC</a>", b">Slides</a>", 1
        )
        with self.assertRaisesRegex(ValueError, "automatic acceptance rules"):
            self._screener(unknown_document, "2026-08-04T10:03:00+00:00")

        self._trendlyne(TRENDLYNE_FIXTURE.read_bytes(), TRENDLYNE_FETCH)
        wrong_identity = TRENDLYNE_FIXTURE.read_bytes().replace(
            b'data-myid="357598"', b'data-myid="999999"', 1
        )
        with self.assertRaisesRegex(ValueError, "automatic acceptance rules"):
            self._trendlyne(wrong_identity, "2026-08-04T15:22:12+00:00")

        conn = connect(self.db_path)
        try:
            payload = build_quality_report(conn, generated_at_utc=GENERATED_AT)
            output = write_quality_report(
                conn,
                self.exports / "quality-report.json",
                generated_at_utc=GENERATED_AT,
            )
        finally:
            conn.close()

        self.assertEqual(
            payload,
            json.loads(output.read_text(encoding="utf-8")),
        )
        expected_hash = payload.pop("report_sha256")
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.assertEqual(expected_hash, hashlib.sha256(canonical).hexdigest())

        results = payload["results"]
        projection = {
            "summary": payload["summary"],
            "accepted_versions": [
                {
                    "source": row["source"],
                    "scope": row["reporting_scope"],
                    "period": row["resolved_source_period"],
                    "parser_version": row["parser_version"],
                    "manifest_version": row["manifest_version"],
                    "sha256": row["content_sha256"],
                }
                for row in payload["accepted_versions"]
            ],
            "results": [
                {
                    "result": row["result"],
                    "action": row["acceptance_action"],
                    "source": row["source"],
                    "current": row["is_current_accepted_version"],
                    "parse_status": row["artifact"]["parse_status"],
                    "validation_status": row["artifact"]["validation_status"],
                    "parser_version": row["artifact"]["parser_version"],
                    "manifest_version": row["artifact"]["manifest_version"],
                    "section_statuses": {
                        section["section_code"]: section["parse_status"]
                        for section in row["sections"]
                    },
                    "normalized_counts": {
                        name: count
                        for name, count in row["row_counts"]["normalized"].items()
                        if count
                    },
                    "failure_rules": [
                        rule["rule_code"] for rule in row["validation_failures"]
                    ],
                    "warning_rules": [
                        rule["rule_code"] for rule in row["validation_warnings"]
                    ],
                    "unknown_labels": row["unknown_labels"],
                    "supplements": [
                        {
                            "component": supplement["component_code"],
                            "parser_version": supplement["artifact"][
                                "parser_version"
                            ],
                            "sha256": supplement["artifact"]["hashes"][
                                "source_content_sha256"
                            ],
                            "normalized_peer_rows": supplement["row_counts"][
                                "normalized"
                            ]["peer_observations"],
                        }
                        for supplement in row["supplemental_artifacts"]
                    ],
                }
                for row in results
            ],
        }
        self.assertEqual(
            projection,
            json.loads(GOLDEN.read_text(encoding="utf-8")),
        )

        manual = next(row for row in results if row["result"] == "manual_review")
        self.assertEqual(
            manual["unknown_labels"],
            [{"category": "document_type", "labels": ["Slides"]}],
        )
        accepted_screener = next(
            row
            for row in results
            if row["result"] == "accepted" and row["source"] == "screener"
        )
        quarterly = next(
            section
            for section in accepted_screener["sections"]
            if section["section_code"] == "quarterly_results"
        )
        self.assertEqual(
            quarterly["row_counts"],
            {
                "source_fields": 0,
                "source_cells": 182,
                "source_tables": 1,
                "source_rows": 13,
                "source_columns": 14,
            },
        )
        accepted_trendlyne = next(
            row
            for row in results
            if row["result"] == "accepted" and row["source"] == "trendlyne"
        )
        fund_table = next(
            section
            for section in accepted_trendlyne["sections"]
            if section["section_code"] == "trendlyne_fund_table"
        )
        self.assertEqual(
            fund_table["row_counts"],
            {
                "source_fields": 0,
                "source_cells": 234,
                "source_tables": 1,
                "source_rows": 30,
                "source_columns": 8,
            },
        )
        rejected = next(row for row in results if row["result"] == "rejected")
        self.assertIn(
            "trendlyne_identity_company_id",
            [rule["rule_code"] for rule in rejected["validation_failures"]],
        )


if __name__ == "__main__":
    unittest.main()
