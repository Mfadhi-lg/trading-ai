"""Offline tests for the scheduler and its retry ladder (C7).

An injected clock and a stub fetcher stand in for the schedule and the network,
so the ladder is exercised without APScheduler, a network or a day passing. The
property under test throughout is that a failed attempt costs nothing: the last
accepted snapshot stays exactly where it was, and the run says so.
"""

from __future__ import annotations

from scrapper.tests.database_fixtures import prepare_database

import tempfile
import unittest
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from unittest import mock

import pytest

from scrapper.config import MIGRATIONS_PATH as MIGRATIONS
from scrapper.core.http import FetchResponse
from scrapper.pipeline import scheduler
from scrapper.db.database import connect, init_db
from scrapper.db.exchange_repo import record_schedule_run
from scrapper.pipeline.exchange_universe import thresholds_for_dataset
from scrapper.pipeline.scheduler import (
    LADDER,
    current_result_period,
    day_status,
    ist_day,
    previous_result_period,
    revision_datasets,
    run_attempt,
    run_weekly_report,
    resume_source_access,
    scheduled_datasets,
)
from scrapper.sources.exchange.datasets import RESULT_FILING, SECURITY_MASTER, DATASETS
from scrapper.sources.exchange.snapshot_guard import GuardThresholds

from .test_e01_exchange_universe import BSE_MASTER, nse_results
from .test_e02_snapshot_guard import ALPHA_ISIN, BETA_ISIN, nse_master


ALPHA_ROW = f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{ALPHA_ISIN},10"
BETA_ROW = f"BETA,Beta Services Limited,EQ,04-JUL-2004,1,1,{BETA_ISIN},1"
DAY_ONE = nse_master((ALPHA_ROW, BETA_ROW))
DAY_TWO = nse_master((ALPHA_ROW, BETA_ROW, f"GAMMA,Gamma Textiles Limited,EQ,01-JAN-2010,10,1,INE040A01034,10"))
TRUNCATED = nse_master((ALPHA_ROW,))

DATASET = "nse_equity_master"

# The period-addressed result feed, answered per requested quarter: what the
# revision sweep asks for is exactly what it gets back.
RESULT_FEED = {
    "30-Jun-2026": nse_results(
        ("ALPHA,Alpha Industries Limited,Capital Goods,Quarterly,30-JUN-2026,"
         "Un-Audited,Non-cumulative,Consolidated,05-Aug-2026 18:30:00",)
    ),
    "31-Mar-2026": nse_results(
        ("ALPHA,Alpha Industries Limited,Capital Goods,Quarterly,31-MAR-2026,"
         "Audited,Non-cumulative,Consolidated,20-May-2026 18:30:00",)
    ),
}


@dataclass(frozen=True)
class Served:
    """One canned HTTP answer, for the responses a fetch does not raise on."""

    status_code: int
    fetch_status: str
    content: bytes = b""


# The exchange saying the dated artifact does not exist, versus the endpoint
# turning the request away. Only the first is evidence of non-publication.
NOT_FOUND = Served(404, "not_found")
REJECTED = Served(403, "rejected")


class SchedulerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.db = root / "scraper.db"
        self.snapshots = root / "snapshots"
        self.exports = root / "exports"
        # 14:00 UTC is 19:30 IST, so the clock always sits inside the IST day the
        # attempt is scheduled for. It advances a second per reading, which is
        # what keeps `future_effective_date` out of these tests: a run must never
        # observe a file as of an instant before the day it is refreshing.
        self.today = "2026-08-06"
        self.tick = 0
        self.issued: list[str] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def now(self) -> str:
        self.tick += 1
        return f"{self.today}T14:{self.tick // 60:02d}:{self.tick % 60:02d}+00:00"

    def fetcher(self, content: bytes | Exception | Served | dict[str, bytes]):
        """A stub standing in for one allowlisted official download.

        A mapping answers per requested period, keyed by the period-ended value
        in the URL, so a sweep of an old quarter gets that quarter's feed and not
        a copy of this one's.
        """

        def fetch(url: str, exchange: str) -> FetchResponse:
            self.issued.append(url)
            if isinstance(content, Exception):
                raise content
            served = content
            if isinstance(served, dict):
                matched = [body for key, body in served.items() if key in url]
                if not matched:
                    raise AssertionError(f"no canned response for {url}")
                served = matched[0]
            if isinstance(served, Served):
                return FetchResponse(
                    requested_url=url, final_url=url,
                    status_code=served.status_code, content=served.content,
                    content_type="text/csv", encoding=None, etag=None,
                    last_modified=None, auth_mode="none",
                    fetch_status=served.fetch_status,
                )
            return FetchResponse(
                requested_url=url, final_url=url, status_code=200, content=served,
                content_type="text/csv", encoding=None, etag=None, last_modified=None,
                auth_mode="none", fetch_status="ok",
            )

        return fetch

    # These fixtures are three rows wide against a dataset whose real floor is
    # 1500, so the size rules are relaxed. The guard's own behaviour is the
    # subject of test_e02; here it only has to fire or not fire.
    def thresholds(self, dataset: str) -> GuardThresholds:
        return replace(thresholds_for_dataset(dataset), row_floor=1)

    def attempt(
        self, number: int, content: bytes | Exception, *, day: str = "2026-08-06", **kwargs
    ) -> dict:
        self.today, self.tick = day, 0
        prepare_database(self.db)
        return run_attempt(
            attempt=number,
            scheduled_for=day,
            datasets=kwargs.pop("datasets", (DATASET,)),
            db_path=self.db,
            snapshot_root=self.snapshots,
            now_utc=self.now,
            fetcher=self.fetcher(content),
            thresholds=self.thresholds,
            **kwargs,
        )

    def outcomes(self, payload: dict) -> dict[str, str]:
        return {item["dataset"]: item["outcome"] for item in payload["datasets"]}

    def rows(self, sql: str, *parameters) -> list[dict]:
        conn = connect(self.db)
        try:
            init_db(conn, apply_migrations=True)
            return [dict(row) for row in conn.execute(sql, parameters)]
        finally:
            conn.close()


class LadderShapeTests(unittest.TestCase):
    def test_the_ladder_is_three_rungs_ending_the_next_morning(self) -> None:
        self.assertEqual(
            [(rung.ist_time, rung.day_offset) for rung in LADDER],
            [("19:30", 0), ("21:00", 0), ("06:00", 1)],
        )
        self.assertTrue(LADDER[-1].final)
        self.assertFalse(any(rung.final for rung in LADDER[:-1]))

    def test_only_the_last_rung_is_final(self) -> None:
        self.assertEqual([rung.final for rung in LADDER], [False, False, True])

    def test_result_feeds_run_after_the_universe_datasets(self) -> None:
        order = scheduled_datasets()
        masters = [
            key for key in order if DATASETS[key].kind == SECURITY_MASTER
        ]
        feeds = [key for key in order if DATASETS[key].kind == RESULT_FILING]
        # A filing for a company that listed this morning only resolves once the
        # master listing it has been folded in.
        self.assertLess(max(order.index(key) for key in masters),
                        min(order.index(key) for key in feeds))

    def test_the_ist_day_is_the_trading_day_not_the_utc_one(self) -> None:
        # 19:30 IST on the 6th is 14:00 UTC on the 6th.
        self.assertEqual(ist_day("2026-08-06T14:00:00+00:00"), "2026-08-06")
        # 06:00 IST on the 7th is 00:30 UTC on the 7th, still the 7th in IST.
        self.assertEqual(ist_day("2026-08-07T00:30:00+00:00"), "2026-08-07")
        # 23:00 UTC on the 6th is already the 7th in IST.
        self.assertEqual(ist_day("2026-08-06T23:00:00+00:00"), "2026-08-07")

    def test_the_result_period_is_the_quarter_that_has_ended(self) -> None:
        for day, expected in (
            ("2026-08-06", "2026-06"),
            ("2026-04-01", "2026-03"),
            ("2026-01-15", "2025-12"),
            ("2026-03-31", "2025-12"),
        ):
            with self.subTest(day=day):
                self.assertEqual(current_result_period(day), expected)


class RetryLadderTests(SchedulerCase):
    def test_success_on_attempt_one_skips_the_later_rungs(self) -> None:
        first = self.attempt(1, DAY_ONE)
        self.assertEqual(self.outcomes(first), {DATASET: "published_changed"})
        self.assertEqual(first["status"], "ok")
        self.assertFalse(first["retry_scheduled"])

        issued_after_first = len(self.issued)
        second = self.attempt(2, DAY_ONE)
        third = self.attempt(3, DAY_ONE)

        self.assertEqual(self.outcomes(second), {DATASET: "already_refreshed"})
        self.assertEqual(self.outcomes(third), {DATASET: "already_refreshed"})
        # A dataset already refreshed is not re-fetched at all.
        self.assertEqual(len(self.issued), issued_after_first)

    def test_a_failed_fetch_retries_and_leaves_prior_state_alone(self) -> None:
        self.attempt(1, DAY_ONE)
        conn_state = self.rows("SELECT exchange_key, is_present FROM exchange_securities")

        broken = self.attempt(1, RuntimeError("Exchange dataset rejected before parsing"), day="2026-08-07")

        self.assertEqual(self.outcomes(broken), {DATASET: "fetch_failed"})
        self.assertTrue(broken["retry_scheduled"])
        self.assertEqual(broken["status"], "ok")
        self.assertEqual(
            self.rows("SELECT exchange_key, is_present FROM exchange_securities"),
            conn_state,
        )

    def test_failure_on_all_three_attempts_exits_non_zero(self) -> None:
        error = RuntimeError("Exchange dataset rejected before parsing")
        for number in (1, 2):
            payload = self.attempt(number, error)
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["retry_scheduled"])

        final = self.attempt(3, error)

        self.assertEqual(final["status"], "incomplete")
        self.assertEqual(final["outstanding"], [DATASET])
        self.assertFalse(final["retry_scheduled"])
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM exchange_securities")[0]["n"], 0)

    def test_the_final_failure_is_recorded_as_unrefreshed(self) -> None:
        error = RuntimeError("Exchange dataset rejected before parsing")
        for number in (1, 2, 3):
            self.attempt(number, error)

        ledger = self.rows(
            "SELECT attempt, outcome FROM exchange_schedule_runs ORDER BY attempt"
        )
        self.assertEqual(
            [(row["attempt"], row["outcome"]) for row in ledger],
            [(1, "fetch_failed"), (2, "fetch_failed"), (3, "fetch_failed"),
             (4, "unrefreshed")],
        )

    def test_a_later_attempt_can_still_succeed(self) -> None:
        self.attempt(1, RuntimeError("Exchange dataset rejected before parsing"))
        recovered = self.attempt(2, DAY_ONE)

        self.assertEqual(self.outcomes(recovered), {DATASET: "published_changed"})
        self.assertEqual(recovered["status"], "ok")
        self.assertEqual(
            self.rows("SELECT COUNT(*) AS n FROM exchange_securities")[0]["n"], 2
        )


class SourceAccessHoldTests(SchedulerCase):
    BSE = "bse_security_master"

    def test_bse_403_holds_source_and_preserves_accepted_snapshot(self) -> None:
        self.attempt(1, BSE_MASTER, datasets=(self.BSE,))
        before_snapshots = self.rows(
            "SELECT snapshot_id,validation_status FROM exchange_source_snapshots"
        )
        before_securities = self.rows(
            "SELECT exchange_key,is_present FROM exchange_securities ORDER BY exchange_key"
        )
        blocked = self.attempt(1, REJECTED, datasets=(self.BSE,), day="2026-08-07")
        self.assertEqual(self.outcomes(blocked), {self.BSE: "source_access_blocked"})
        self.assertEqual(blocked["status"], "access_blocked")
        self.assertFalse(blocked["retry_scheduled"])
        self.assertEqual(blocked["access_blocked_datasets"], [self.BSE])
        self.assertEqual(
            self.rows("SELECT snapshot_id,validation_status FROM exchange_source_snapshots"),
            before_snapshots,
        )
        self.assertEqual(
            self.rows("SELECT exchange_key,is_present FROM exchange_securities ORDER BY exchange_key"),
            before_securities,
        )
        issued = len(self.issued)
        for rung in (2, 3):
            held = self.attempt(rung, BSE_MASTER, datasets=(self.BSE,), day="2026-08-07")
            self.assertEqual(self.outcomes(held), {self.BSE: "source_access_blocked"})
            self.assertFalse(held["retry_scheduled"])
        next_day = self.attempt(1, BSE_MASTER, datasets=(self.BSE,), day="2026-08-08")
        self.assertEqual(next_day["status"], "access_blocked")
        self.assertEqual(len(self.issued), issued)
        historical = day_status("2026-08-06", db_path=self.db, now_utc=self.now)
        self.assertEqual(historical["dataset_states"][self.BSE], "REFRESHED")
        current = day_status("2026-08-08", db_path=self.db, now_utc=self.now)
        self.assertEqual(current["dataset_states"][self.BSE], "ACCESS_BLOCKED")
        self.assertEqual(
            self.rows("SELECT outcome FROM exchange_schedule_runs WHERE scheduled_for='2026-08-07'"),
            [{"outcome": "source_access_blocked"}],
        )

    def test_primary_403_skips_same_attempt_revision_without_blocking_nse(self) -> None:
        with mock.patch.object(scheduler, "revision_datasets",
                               side_effect=lambda datasets: tuple(d for d in datasets if d == self.BSE)):
            result = self.attempt(1, REJECTED, datasets=(self.BSE, DATASET))
        self.assertEqual(len(self.issued), 2)
        self.assertEqual(result["revisions"], [])
        self.assertEqual(result["access_blocked_datasets"], [self.BSE])
        self.assertTrue(result["retry_scheduled"])

    def test_resume_requires_evidence_and_releases_only_named_source(self) -> None:
        self.attempt(1, REJECTED, datasets=(self.BSE, "bse_financial_results"))
        with self.assertRaises(ValueError):
            resume_source_access(self.BSE, " ", db_path=self.db, now_utc=self.now)
        resumed = resume_source_access(
            self.BSE, "BSE approval reference A-1, effective 2026-08-07",
            db_path=self.db, now_utc=self.now,
        )
        self.assertEqual(resumed["access_state"], "RESUMED")
        pending = day_status("2026-08-06", db_path=self.db, now_utc=self.now)
        self.assertEqual(pending["dataset_states"][self.BSE], "ACCESS_RECHECK_PENDING")
        issued = len(self.issued)
        self.assertEqual(issued, 2)
        next_day = self.attempt(1, BSE_MASTER, datasets=(self.BSE,), day="2026-08-07")
        self.assertEqual(self.outcomes(next_day), {self.BSE: "published_changed"})
        self.assertEqual(len(self.issued), issued + 1)
        status = day_status("2026-08-07", db_path=self.db, now_utc=self.now)
        self.assertIn("bse_financial_results", status["access_blocked_datasets"])
        self.assertNotIn(self.BSE, status["access_blocked_datasets"])

    def test_404_and_transient_errors_remain_retryable_and_reported_separately(self) -> None:
        self.attempt(1, NOT_FOUND, datasets=(self.BSE,), day="2026-08-07")
        status = day_status("2026-08-07", db_path=self.db, now_utc=self.now)
        self.assertEqual(status["dataset_states"][self.BSE], "ARTIFACT_NOT_FOUND")
        self.assertIn(self.BSE, status["unpublished_datasets"])
        self.assertEqual(status["dataset_states"]["nse_equity_master"], "NEVER_REFRESHED")
        failure = self.attempt(2, Served(500, "error"), datasets=(self.BSE,), day="2026-08-07")
        self.assertTrue(failure["retry_scheduled"])
        status = day_status("2026-08-07", db_path=self.db, now_utc=self.now)
        self.assertEqual(status["dataset_states"][self.BSE], "FETCH_FAILED")
        self.assertNotIn(self.BSE, status["unpublished_datasets"])
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM exchange_source_access_events")[0]["n"], 0)

    def test_nse_403_is_not_misclassified_as_confirmed_bse_access_block(self) -> None:
        failure = self.attempt(1, REJECTED, datasets=(DATASET,))
        self.assertEqual(self.outcomes(failure), {DATASET: "fetch_failed"})
        self.assertTrue(failure["retry_scheduled"])

    def test_bse_result_403_keeps_watermark_and_recovery_queue(self) -> None:
        prepare_database(self.db)
        conn = connect(self.db)
        try:
            init_db(conn)
            with conn:
                conn.execute(
                    """INSERT INTO exchange_result_watermarks
                       (exchange,dataset,last_broadcast_at_utc,updated_at_utc)
                       VALUES ('BSE','bse_financial_results','2026-06-30T00:00:00+00:00',
                               '2026-07-01T00:00:00+00:00')"""
                )
            before = [dict(row) for row in conn.execute(
                "SELECT * FROM exchange_result_watermarks"
            )]
            queue_before = conn.execute(
                "SELECT COUNT(*) FROM screener_company_queue"
            ).fetchone()[0]
        finally:
            conn.close()
        blocked = self.attempt(1, REJECTED, datasets=("bse_financial_results",))
        self.assertEqual(blocked["status"], "access_blocked")
        self.assertEqual(self.rows("SELECT * FROM exchange_result_watermarks"), before)
        self.assertEqual(
            self.rows("SELECT COUNT(*) AS n FROM screener_company_queue")[0]["n"],
            queue_before,
        )

    def test_access_block_emits_existing_structured_alert(self) -> None:
        prepare_database(self.db)
        with mock.patch.object(scheduler, "scheduled_datasets", return_value=(self.BSE,)), \
             mock.patch.object(scheduler, "alert") as alert:
            result = scheduler.run_scheduler(
                once=True, db_path=self.db, snapshot_root=self.snapshots,
                now_utc=self.now, fetcher=self.fetcher(REJECTED),
            )
        self.assertEqual(result["status"], "access_blocked")
        alert.assert_called_once()
        self.assertEqual(alert.call_args.args[0]["access_blocked_datasets"], [self.BSE])

    def test_migration_backfills_only_latest_confirmed_bse_403(self) -> None:
        path = Path(self.temporary.name) / "legacy.db"
        conn = sqlite3.connect(path)
        try:
            conn.executescript("""CREATE TABLE exchange_source_snapshots(snapshot_id INTEGER PRIMARY KEY);
                CREATE TABLE exchange_schedule_runs (
                    schedule_run_id INTEGER PRIMARY KEY, dataset TEXT, scheduled_for TEXT,
                    attempt INTEGER, role TEXT, result_period TEXT, outcome TEXT,
                    snapshot_id INTEGER, error TEXT, started_at_utc TEXT,
                    finished_at_utc TEXT,
                    UNIQUE(dataset,scheduled_for,attempt,role));""")
            conn.executemany(
                """INSERT INTO exchange_schedule_runs
                   (dataset,scheduled_for,attempt,role,result_period,outcome,error,
                    started_at_utc,finished_at_utc)
                   VALUES (?, '2026-08-06', ?, 'primary', '', ?, ?, ?, ?)""",
                [
                    (self.BSE, 1, "fetch_failed", "status=403 classification=rejected", "a", "a"),
                    (self.BSE, 2, "unrefreshed", "no accepted publication", "b", "b"),
                    ("bse_financial_results", 1, "fetch_failed", "status=403", "a", "a"),
                    ("bse_financial_results", 2, "published_changed", "", "b", "b"),
                    (DATASET, 1, "fetch_failed", "status=403", "a", "a"),
                ],
            )
            conn.executescript(
                (MIGRATIONS / "068_exchange_source_access_compatibility.sql").read_text()
            )
            self.assertEqual(
                conn.execute(
                    "SELECT dataset FROM exchange_source_access_events"
                ).fetchall(),
                [(self.BSE,)],
            )
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM exchange_schedule_runs"
            ).fetchone()[0], 5)
        finally:
            conn.close()


class GuardInteractionTests(SchedulerCase):
    def test_a_guard_failure_is_a_failed_attempt_not_a_refresh(self) -> None:
        self.attempt(1, DAY_ONE)
        rejected = self.attempt(1, TRUNCATED, day="2026-08-07")

        self.assertEqual(self.outcomes(rejected), {DATASET: "guard_failed"})
        self.assertTrue(rejected["retry_scheduled"])

    def test_a_rejected_file_leaves_the_universe_exactly_as_it_was(self) -> None:
        self.attempt(1, DAY_ONE)
        before = self.rows(
            "SELECT exchange_key, is_present, absence_streak FROM exchange_securities"
            " ORDER BY exchange_key"
        )
        self.attempt(1, TRUNCATED, day="2026-08-07")

        self.assertEqual(
            self.rows(
                "SELECT exchange_key, is_present, absence_streak FROM exchange_securities"
                " ORDER BY exchange_key"
            ),
            before,
        )

    def test_the_rejected_file_is_still_kept_as_evidence(self) -> None:
        self.attempt(1, DAY_ONE)
        self.attempt(1, TRUNCATED, day="2026-08-07")

        self.assertEqual(
            [
                row["validation_status"]
                for row in self.rows(
                    "SELECT validation_status FROM exchange_source_snapshots"
                    " ORDER BY snapshot_id"
                )
            ],
            ["passed", "failed"],
        )


class PublicationTests(SchedulerCase):
    """What counts as a publication, and what counts as evidence of one.

    The distinction this class exists for: unchanged content is a published
    file, and only the absence of the dated artifact is a non-publication.
    """

    def test_identical_bytes_are_a_publication_that_did_not_change(self) -> None:
        self.attempt(1, DAY_ONE)
        repeated = self.attempt(1, DAY_ONE, day="2026-08-07")

        self.assertEqual(self.outcomes(repeated), {DATASET: "published_unchanged"})
        # A trading day with no listing, delisting or symbol change produces the
        # same file. The exchange published; the ladder has nothing left to look
        # for and must not climb.
        self.assertFalse(repeated["retry_scheduled"])
        self.assertEqual(repeated["outstanding"], [])
        self.assertEqual(repeated["status"], "ok")

    def test_an_unchanged_publication_satisfies_the_day(self) -> None:
        self.attempt(1, DAY_ONE)
        self.attempt(1, DAY_ONE, day="2026-08-07")

        issued_before = len(self.issued)
        later = self.attempt(2, DAY_ONE, day="2026-08-07")

        self.assertEqual(self.outcomes(later), {DATASET: "already_refreshed"})
        self.assertEqual(len(self.issued), issued_before)
        status = day_status("2026-08-07", db_path=self.db, now_utc=self.now)
        self.assertIn(DATASET, status["published_datasets"])
        self.assertNotIn(DATASET, status["unpublished_datasets"])

    def test_a_genuinely_new_file_is_a_changed_publication(self) -> None:
        self.attempt(1, DAY_ONE)
        published = self.attempt(1, DAY_TWO, day="2026-08-07")

        self.assertEqual(self.outcomes(published), {DATASET: "published_changed"})

    def test_an_absent_dated_artifact_is_the_non_publication(self) -> None:
        missing = self.attempt(1, NOT_FOUND)

        self.assertEqual(self.outcomes(missing), {DATASET: "not_published"})
        # Nothing was published, so the later rungs keep looking.
        self.assertTrue(missing["retry_scheduled"])
        self.assertEqual(missing["status"], "ok")

    def test_a_missing_file_is_told_apart_from_a_broken_endpoint(self) -> None:
        self.attempt(1, NOT_FOUND)
        self.attempt(2, REJECTED)

        self.assertEqual(
            [row["outcome"] for row in self.rows(
                "SELECT outcome FROM exchange_schedule_runs"
                " WHERE dataset=? ORDER BY attempt", DATASET
            )],
            ["not_published", "fetch_failed"],
        )

    def test_a_day_that_published_nothing_ends_unrefreshed(self) -> None:
        for number in (1, 2, 3):
            self.attempt(number, NOT_FOUND, day="2026-08-07")

        status = day_status("2026-08-07", db_path=self.db, now_utc=self.now)
        self.assertIn(DATASET, status["unpublished_datasets"])
        self.assertEqual(
            [row["outcome"] for row in status["attempts"]],
            ["not_published", "not_published", "not_published", "unrefreshed"],
        )


class RevisionSweepTests(SchedulerCase):
    """The period-addressed result feed keeps being asked about last quarter."""

    def test_the_previous_quarter_is_the_one_before_the_current_period(self) -> None:
        for period, expected in (
            ("2026-06", "2026-03"),
            ("2026-03", "2025-12"),
            ("2025-12", "2025-09"),
            ("2026-09", "2026-06"),
        ):
            with self.subTest(period=period):
                self.assertEqual(previous_result_period(period), expected)

    def test_only_a_period_addressed_feed_is_swept(self) -> None:
        # BSE returns a rolling window of quarters on every call, so its
        # revisions arrive with the primary run and re-fetching is duplication.
        self.assertEqual(
            revision_datasets(scheduled_datasets()), ("nse_financial_results",)
        )

    def test_the_sweep_asks_for_the_previous_quarter(self) -> None:
        payload = self.attempt(
            1, RESULT_FEED, datasets=("nse_financial_results",)
        )

        self.assertEqual(payload["revision_period"], "2026-03")
        self.assertEqual(
            [item["dataset"] for item in payload["revisions"]],
            ["nse_financial_results"],
        )
        # 30-Jun-2026 for the primary run, 31-Mar-2026 for the revision.
        self.assertEqual(len(self.issued), 2)
        self.assertIn("30-Jun-2026", self.issued[0])
        self.assertIn("31-Mar-2026", self.issued[1])

    def test_the_sweep_runs_on_one_rung_a_day_not_every_rung(self) -> None:
        first = self.attempt(1, RESULT_FEED, datasets=("nse_financial_results",))
        second = self.attempt(2, RESULT_FEED, datasets=("nse_financial_results",))

        self.assertEqual(len(first["revisions"]), 1)
        self.assertEqual(second["revisions"], [])

    def test_a_revision_is_recorded_with_its_period_and_never_satisfies_the_day(
        self,
    ) -> None:
        self.attempt(1, RESULT_FEED, datasets=("nse_financial_results",))

        ledger = self.rows(
            "SELECT role, result_period, outcome FROM exchange_schedule_runs"
            " ORDER BY role"
        )
        self.assertEqual(
            [(row["role"], row["result_period"]) for row in ledger],
            [("primary", "2026-06"), ("revision", "2026-03")],
        )
        status = day_status("2026-08-06", db_path=self.db, now_utc=self.now)
        # The revision row is a fact about last quarter, not about today.
        self.assertEqual(
            [row["role"] for row in status["attempts"]], ["primary", "revision"]
        )

    def test_a_failed_revision_does_not_make_the_day_incomplete(self) -> None:
        payload = self.attempt(
            3, RESULT_FEED, datasets=("nse_financial_results",)
        )
        revisions = self.attempt(
            1, NOT_FOUND, datasets=("nse_financial_results",), day="2026-08-07"
        )

        self.assertEqual(payload["revisions"], [])
        # The primary run found nothing either, but the revision's own failure
        # is not what put the dataset outstanding.
        self.assertEqual(
            [item["outcome"] for item in revisions["revisions"]], ["not_published"]
        )
        self.assertEqual(revisions["outstanding"], ["nse_financial_results"])


class UnwiredDatasetTests(SchedulerCase):
    def test_a_dataset_with_no_url_is_skipped_without_failing_the_day(self) -> None:
        payload = self.attempt(1, DAY_ONE, datasets=("nse_mii_security_file",))

        self.assertEqual(
            self.outcomes(payload), {"nse_mii_security_file": "skipped_no_url"}
        )
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["outstanding"], [])
        self.assertEqual(self.issued, [])

    def test_the_skip_is_recorded_with_its_reason(self) -> None:
        self.attempt(1, DAY_ONE, datasets=("nse_mii_security_file",))

        recorded = self.rows("SELECT outcome, error FROM exchange_schedule_runs")[0]
        self.assertEqual(recorded["outcome"], "skipped_no_url")
        self.assertIn("no registry default URL", recorded["error"])


class ServePathTests(SchedulerCase):
    """The APScheduler path, exercised without letting it block on a real clock."""

    def test_it_registers_a_job_per_rung_plus_the_weekly_report(self) -> None:
        blocking = pytest.importorskip("apscheduler.schedulers.blocking")
        registered: dict[str, dict] = {}

        def capture(self, *args, **kwargs):
            registered[str(kwargs.get("id"))] = kwargs

        with mock.patch.object(
            blocking.BlockingScheduler, "add_job", capture
        ), mock.patch.object(
            blocking.BlockingScheduler,
            "start", side_effect=KeyboardInterrupt,
        ):
            prepare_database(self.db)
            payload = scheduler._serve(
                db_path=self.db, snapshot_root=self.snapshots, now_utc=self.now,
                fetcher=self.fetcher(DAY_ONE),
            )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(
            list(registered),
            [f"exchange-attempt-{rung.attempt}" for rung in LADDER]
            + ["exchange-weekly-report"],
        )
        self.assertTrue(
            all(
                job["misfire_grace_time"] == scheduler.MISFIRE_GRACE_SECONDS
                for job in registered.values()
            )
        )

    def test_an_interrupt_before_startup_still_exits_cleanly(self) -> None:
        blocking = pytest.importorskip("apscheduler.schedulers.blocking")
        # shutdown() raises if the scheduler never started; a Ctrl-C must not
        # surface as a traceback on top of the operator's own interrupt.
        with mock.patch.object(
            blocking.BlockingScheduler,
            "start", side_effect=KeyboardInterrupt,
        ):
            prepare_database(self.db)
            payload = scheduler._serve(
                db_path=self.db, snapshot_root=self.snapshots, now_utc=self.now,
                fetcher=self.fetcher(DAY_ONE),
            )

        self.assertEqual(payload["status"], "ok")


class LedgerMigrationTests(SchedulerCase):
    """034 restates a ledger written under the old outcome vocabulary."""

    # The table as 033 created it, which is what a database that has already run
    # the scheduler is carrying.
    OLD_TABLE = """
        CREATE TABLE exchange_schedule_runs (
            schedule_run_id INTEGER PRIMARY KEY,
            dataset TEXT NOT NULL,
            scheduled_for TEXT NOT NULL,
            attempt INTEGER NOT NULL CHECK (attempt >= 1),
            outcome TEXT NOT NULL CHECK (outcome IN (
              'refreshed', 'already_refreshed', 'not_published', 'guard_failed',
              'fetch_failed', 'error', 'skipped_no_url', 'unrefreshed'
            )),
            snapshot_id INTEGER REFERENCES exchange_source_snapshots(snapshot_id),
            error TEXT NOT NULL DEFAULT '',
            started_at_utc TEXT NOT NULL,
            finished_at_utc TEXT NOT NULL,
            UNIQUE(dataset, scheduled_for, attempt)
        )
    """

    def migrated(self, rows: list[tuple[str, int, str]]) -> list[dict]:
        conn = connect(self.db)
        try:
            init_db(conn, apply_migrations=True)
            with conn:
                conn.execute("DROP TABLE exchange_schedule_runs")
                conn.execute(self.OLD_TABLE)
                conn.executemany(
                    """INSERT INTO exchange_schedule_runs
                       (dataset, scheduled_for, attempt, outcome, error,
                        started_at_utc, finished_at_utc)
                       VALUES (?, '2026-08-06', ?, ?, 'noted', 'a', 'b')""",
                    [(dataset, attempt, outcome) for dataset, attempt, outcome in rows],
                )
                conn.executescript(
                    (MIGRATIONS / "034_schedule_publication.sql").read_text(
                        encoding="utf-8"
                    )
                )
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM exchange_schedule_runs ORDER BY attempt"
                )
            ]
        finally:
            conn.close()

    def test_historic_rows_are_restated_not_discarded(self) -> None:
        migrated = self.migrated(
            [(DATASET, 1, "refreshed"), (DATASET, 2, "not_published"),
             (DATASET, 3, "guard_failed")]
        )

        self.assertEqual(
            [row["outcome"] for row in migrated],
            # A 'not_published' row was inferred from identical bytes, which is
            # what the ledger actually observed: an unchanged publication.
            ["published_changed", "published_unchanged", "guard_failed"],
        )
        self.assertEqual({row["error"] for row in migrated}, {"noted"})
        self.assertEqual({row["role"] for row in migrated}, {"primary"})
        self.assertEqual({row["result_period"] for row in migrated}, {""})

    def test_the_migrated_ledger_takes_the_new_outcomes_and_roles(self) -> None:
        self.migrated([(DATASET, 1, "refreshed")])

        conn = connect(self.db)
        try:
            record_schedule_run(
                conn,
                dataset=DATASET, scheduled_for="2026-08-06", attempt=1,
                role="revision", result_period="2026-03",
                outcome="published_unchanged", snapshot_id=None, error="",
                started_at_utc="a", finished_at_utc="b",
            )
            # The same rung under two roles is two rows, not a conflict.
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM exchange_schedule_runs WHERE attempt=1"
                ).fetchone()[0],
                2,
            )
        finally:
            conn.close()


class WeeklyReportTests(SchedulerCase):
    def test_it_writes_both_reports_and_reports_the_conflict_state(self) -> None:
        self.attempt(1, DAY_ONE)

        prepare_database(self.db)
        payload = run_weekly_report(
            db_path=self.db, output_root=self.exports, now_utc=self.now
        )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["reconciliation_status"], "clear")
        self.assertTrue(Path(payload["reconciliation_report"]).exists())
        self.assertTrue(Path(payload["coverage_report"]).exists())


if __name__ == "__main__":
    unittest.main()
