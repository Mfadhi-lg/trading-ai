"""Offline AR-02.1 regression cases with the real parser and snapshot store."""
import hashlib

from scrapper.pipeline.exchange_universe import discover_exchange_results
from scrapper.tests.database_fixtures import prepare_database
from scrapper.tests.test_e02_snapshot_guard import AutomationCase


HEADER = (
    "SYMBOL,COMPANY NAME,INDUSTRY,RELATING TO,PERIOD ENDED,AUDITED,"
    "CUMULATIVE,CONSOLIDATED,BROADCAST DATE/TIME\n"
)
VALID = (HEADER +
    "ALPHA,Alpha Industries Limited,Capital Goods,Quarterly,30-JUN-2026,"
    "Un-Audited,Non-cumulative,Consolidated,01-Oct-2026 18:30:00\n"
).encode()


class ExchangeObservationDateTests(AutomationCase):
    def discover(self, body, observed, effective):
        prepare_database(self.db)
        return discover_exchange_results(
            dataset="nse_financial_results",
            url="https://nsearchives.nseindia.com/content/equities/results.csv",
            effective_date=effective, db_path=self.db,
            snapshot_root=self.snapshots, now_utc=lambda: observed,
            fetcher=self.fetched(body),
        )

    def codes(self, payload):
        return {item["code"] for item in payload["validation"]["findings"]}

    def test_sparse_valid_result_passes_after_ist_midnight(self):
        payload = self.discover(VALID, "2026-10-03T18:44:56+00:00", "2026-10-04")
        self.assertEqual(payload["validation"]["status"], "passed")
        self.assertEqual(payload["validation"]["metrics"]["accepted_row_count"], 1)
        self.assertEqual(payload["validation"]["thresholds"]["row_floor"], 1)

    def test_next_day_remains_future_before_ist_midnight(self):
        payload = self.discover(VALID, "2026-10-03T18:29:59+00:00", "2026-10-04")
        self.assertIn("future_effective_date", self.codes(payload))
        self.assertEqual(self.rows("SELECT * FROM exchange_result_watermarks"), [])
        self.assertEqual(self.rows("SELECT * FROM screener_company_queue"), [])

    def test_exact_ist_midnight_passes(self):
        payload = self.discover(VALID, "2026-10-03T18:30:00+00:00", "2026-10-04")
        self.assertEqual(payload["validation"]["status"], "passed")

    def test_future_day_remains_rejected_after_ist_midnight(self):
        payload = self.discover(VALID, "2026-10-03T18:44:56+00:00", "2026-10-05")
        self.assertIn("future_effective_date", self.codes(payload))

    def test_header_only_remains_held_and_bytes_retained(self):
        body = HEADER.encode()
        payload = self.discover(body, "2026-10-03T18:44:56+00:00", "2026-10-04")
        self.assertEqual(self.codes(payload), {"row_count_floor"})
        self.assertEqual(self.rows("SELECT * FROM exchange_result_watermarks"), [])
        self.assertEqual(self.rows("SELECT * FROM screener_company_queue"), [])
        blobs = self.rows("SELECT * FROM response_blobs")
        self.assertEqual(blobs[0]["content_sha256"], hashlib.sha256(body).hexdigest())
        from pathlib import Path
        self.assertEqual(Path(blobs[0]["local_path"]).read_bytes(), body)

    def test_malformed_row_remains_rejected(self):
        with self.assertRaisesRegex(ValueError, "cells; expected"):
            self.discover(
                (HEADER + "ALPHA,broken\n").encode(),
                "2026-10-03T18:44:56+00:00", "2026-10-04",
            )
        self.assertEqual(self.rows("SELECT * FROM exchange_result_watermarks"), [])
        self.assertEqual(self.rows("SELECT * FROM screener_company_queue"), [])

    def test_invalid_period_remains_guard_rejected(self):
        payload = self.discover(
            VALID.replace(b"30-JUN-2026", b"not-a-date"),
            "2026-10-03T18:44:56+00:00", "2026-10-04",
        )
        self.assertIn("row_count_floor", self.codes(payload))
        self.assertIn("rejected_row_rate", self.codes(payload))

    def test_naive_observation_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "must include a timezone"):
            self.discover(VALID, "2026-10-03T18:44:56", "2026-10-04")
