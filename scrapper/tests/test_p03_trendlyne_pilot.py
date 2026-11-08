from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scrapper.db.database import connect, init_db
from scrapper.exports.quality_report import build_quality_report
from scrapper.pipeline.trendlyne_pilot import (
    build_trendlyne_pilot_plan,
    run_trendlyne_pilot_batch,
)
from scrapper.pipeline.trendlyne_vertical import ingest_trendlyne_response
from scrapper.tests.test_trendlyne_t01 import (
    FIXTURE,
    OLDER_FIXTURE,
    URL,
    fixture_response,
)


class TrendlyneP03PilotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db_path = root / "scraper.db"
        self.snapshots = root / "snapshots"
        self.exports = root / "exports"
        conn = connect(self.db_path)
        try:
            init_db(conn, apply_migrations=True)
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _plan(self, *, months: int = 6, batch_size: int = 3):
        conn = connect(self.db_path)
        try:
            return build_trendlyne_pilot_plan(
                conn,
                end_source_period="2026-06",
                month_count=months,
                batch_size=batch_size,
            )
        finally:
            conn.close()

    def _ingest_fixture(self, symbol: str, period: str):
        self.assertEqual(symbol, "RBA")
        fixtures = {
            "2026-06": (FIXTURE, URL.replace("latest", "Jun-2026")),
            "2026-05": (OLDER_FIXTURE, URL.replace("latest", "May-2026")),
        }
        fixture, url = fixtures[period]
        return ingest_trendlyne_response(
            symbol,
            fixture_response(fixture.read_bytes(), url),
            requested_source_period=period,
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=f"2026-08-04T1{6 if period == '2026-06' else 7}:00:00+00:00",
        )

    def test_exact_plan_is_read_only_bounded_and_allowlisted(self) -> None:
        plan = self._plan(months=6, batch_size=2)
        self.assertEqual(plan.request_count, 6)
        self.assertEqual(plan.pending_request_count, 6)
        self.assertEqual(plan.next_batch_request_count, 2)
        self.assertEqual(
            [request.requested_source_period for request in plan.requests],
            ["2026-06", "2026-05", "2026-04", "2026-03", "2026-02", "2026-01"],
        )
        self.assertEqual(
            plan.requests[0].requested_url,
            URL.replace("latest", "Jun-2026"),
        )
        conn = connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM trendlyne_backfill_plans").fetchone()[0],
                0,
            )
        finally:
            conn.close()

        with self.assertRaisesRegex(ValueError, "between 6 and 12"):
            self._plan(months=5)
        with self.assertRaisesRegex(ValueError, "between 1 and 3"):
            self._plan(batch_size=4)
        conn = connect(self.db_path)
        try:
            with self.assertRaisesRegex(ValueError, "allowlist"):
                build_trendlyne_pilot_plan(
                    conn,
                    end_source_period="2026-06",
                    month_count=6,
                    symbols=("TCS",),
                )
            with self.assertRaisesRegex(ValueError, "never latest"):
                build_trendlyne_pilot_plan(
                    conn,
                    end_source_period="latest",
                    month_count=6,
                )
        finally:
            conn.close()

    def test_bounded_run_prints_plan_first_and_resumes_running_item(self) -> None:
        events: list[tuple[str, str | None]] = []

        first = run_trendlyne_pilot_batch(
            end_source_period="2026-06",
            month_count=6,
            batch_size=1,
            db_path=self.db_path,
            on_plan=lambda plan: events.append(("plan", plan.plan_key)),
            execute_request=lambda symbol, period: (
                events.append(("request", period)) or self._ingest_fixture(symbol, period)
            ),
            apply_live_throttle=False,
        )
        self.assertEqual([event[0] for event in events], ["plan", "request"])
        self.assertEqual(first.attempted_request_count, 1)
        self.assertEqual(first.accepted_request_count, 1)
        self.assertEqual(first.remaining_request_count, 5)

        interrupted_requests: list[str] = []

        def interrupt(_symbol: str, period: str):
            interrupted_requests.append(period)
            raise KeyboardInterrupt("simulated process interruption")

        with self.assertRaisesRegex(KeyboardInterrupt, "simulated"):
            run_trendlyne_pilot_batch(
                end_source_period="2026-06",
                month_count=6,
                batch_size=1,
                db_path=self.db_path,
                execute_request=interrupt,
                apply_live_throttle=False,
            )
        self.assertEqual(interrupted_requests, ["2026-05"])

        resumed_requests: list[str] = []
        resumed = run_trendlyne_pilot_batch(
            end_source_period="2026-06",
            month_count=6,
            batch_size=1,
            db_path=self.db_path,
            execute_request=lambda symbol, period: (
                resumed_requests.append(period) or self._ingest_fixture(symbol, period)
            ),
            apply_live_throttle=False,
        )
        self.assertEqual(resumed_requests, ["2026-05"])
        self.assertEqual(resumed.accepted_request_count, 1)
        self.assertEqual(resumed.remaining_request_count, 4)
        plan = self._plan(months=6, batch_size=1)
        self.assertEqual(plan.accepted_request_count, 2)
        self.assertEqual(
            [request.requested_source_period for request in plan.requests if request.status == "accepted"],
            ["2026-06", "2026-05"],
        )

    def test_quality_report_matches_request_coverage_and_anomalies(self) -> None:
        run_trendlyne_pilot_batch(
            end_source_period="2026-06",
            month_count=6,
            batch_size=1,
            db_path=self.db_path,
            execute_request=self._ingest_fixture,
            apply_live_throttle=False,
        )
        with self.assertRaises(KeyboardInterrupt):
            run_trendlyne_pilot_batch(
                end_source_period="2026-06",
                month_count=6,
                batch_size=1,
                db_path=self.db_path,
                execute_request=lambda _symbol, _period: (_ for _ in ()).throw(
                    KeyboardInterrupt()
                ),
                apply_live_throttle=False,
            )
        run_trendlyne_pilot_batch(
            end_source_period="2026-06",
            month_count=6,
            batch_size=1,
            db_path=self.db_path,
            execute_request=self._ingest_fixture,
            apply_live_throttle=False,
        )

        conn = connect(self.db_path)
        try:
            report = build_quality_report(
                conn, generated_at_utc="2026-08-04T19:00:00+00:00"
            )
        finally:
            conn.close()
        self.assertEqual(report["report_schema_version"], "1.1.0")
        pilot = report["trendlyne_pilot"][0]
        self.assertEqual(
            pilot["request_accounting"],
            {
                "planned_request_count": 6,
                "attempt_count": 3,
                "successful_item_count": 2,
                "failed_item_count": 0,
                "running_item_count": 0,
                "pending_item_count": 4,
            },
        )
        self.assertEqual(pilot["month_coverage"]["accepted_count"], 2)
        self.assertEqual(pilot["month_coverage"]["missing_count"], 4)
        self.assertEqual(
            pilot["month_coverage"]["accepted_periods"], ["2026-06", "2026-05"]
        )
        self.assertEqual(pilot["anomaly_count"], 2)
        self.assertEqual(
            {anomaly["rule_code"] for anomaly in pilot["anomalies"]},
            {"trendlyne_headline_net_change_matches_computed"},
        )
        self.assertEqual(pilot["coverage_pointer_mismatches"], [])
        json.dumps(report)


if __name__ == "__main__":
    unittest.main()
