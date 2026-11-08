"""Offline tests for the exchange automation safety layer.

C1 judges a whole dated file before it may rewrite current state, C2 requires a
streak of absences before a security stops being present, and C3 keeps an
operator-imported row from being silently overwritten by an official one.
"""

from __future__ import annotations

from scrapper.tests.database_fixtures import prepare_database

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from scrapper.core.http import FetchResponse
from scrapper.db.database import connect, init_db
from scrapper.db.exchange_repo import (
    open_identity_conflicts,
    resolve_identity_conflict,
    resolve_screener_path,
)
from scrapper.pipeline.exchange_universe import (
    discover_exchange_results,
    refresh_exchange_universe,
    thresholds_for_dataset,
)
from scrapper.sources.exchange.snapshot_guard import (
    GuardThresholds,
    ScopeMetrics,
    SnapshotBaseline,
    SnapshotMetrics,
    evaluate_snapshot,
    isin_metrics,
    override,
)


ALPHA_ISIN = "INE002A01018"
BETA_ISIN = "INE467B01029"
GAMMA_ISIN = "INE040A01034"
DELTA_ISIN = "INE009A01021"

NSE_HEADER = (
    "SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE,"
    " MARKET LOT, ISIN NUMBER, FACE VALUE"
)
ALPHA_ROW = f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{ALPHA_ISIN},10"
BETA_ROW = f"BETA,Beta Services Limited,EQ,04-JUL-2004,1,1,{BETA_ISIN},1"
GAMMA_ROW = f"GAMMA,Gamma Textiles Limited,EQ,01-JAN-2010,10,1,{GAMMA_ISIN},10"


def nse_master(rows: tuple[str, ...], *, header: str = NSE_HEADER) -> bytes:
    return ("\n".join((header, *rows)) + "\n").encode()


FULL_MASTER = nse_master((ALPHA_ROW, BETA_ROW, GAMMA_ROW))
TRUNCATED_MASTER = nse_master((ALPHA_ROW,))


def metrics(**overrides: object) -> SnapshotMetrics:
    base = dict(
        accepted_row_count=100,
        rejected_row_count=0,
        headers=("SYMBOL", "ISIN NUMBER"),
        distinct_isin_count=100,
        duplicate_isin_row_count=0,
        non_null_isin_count=100,
        effective_date="2026-08-05",
        would_absent_count=0,
        present_count=100,
    )
    return SnapshotMetrics(**{**base, **overrides})  # type: ignore[arg-type]


def baseline(**overrides: object) -> SnapshotBaseline:
    base = dict(
        snapshot_id=1,
        accepted_row_count=100,
        headers=("SYMBOL", "ISIN NUMBER"),
        distinct_isin_count=100,
        effective_date="2026-08-01",
    )
    return SnapshotBaseline(**{**base, **overrides})  # type: ignore[arg-type]


TODAY = "2026-08-06"
DEFAULTS = GuardThresholds(row_floor=10)


class GuardRuleTests(unittest.TestCase):
    """One table test per reason code, over the pure evaluator."""

    def evaluate(self, candidate, prior=None, thresholds=DEFAULTS):
        return evaluate_snapshot(candidate, prior, thresholds, today=TODAY)

    def test_a_clean_snapshot_passes_with_no_findings(self) -> None:
        verdict = self.evaluate(metrics(), baseline())
        self.assertEqual(verdict.status, "passed")
        self.assertEqual(verdict.findings, ())
        self.assertTrue(verdict.accepted)

    def test_row_count_collapse(self) -> None:
        verdict = self.evaluate(metrics(accepted_row_count=80), baseline())
        self.assertEqual(verdict.status, "failed")
        self.assertIn("row_count_collapse", verdict.codes)
        # A drop inside the tolerance is ordinary movement.
        self.assertEqual(
            self.evaluate(metrics(accepted_row_count=95), baseline()).status, "passed"
        )

    def test_row_count_floor(self) -> None:
        verdict = self.evaluate(
            metrics(accepted_row_count=5, distinct_isin_count=5, non_null_isin_count=5),
            None,
        )
        self.assertIn("row_count_floor", verdict.codes)

    def test_header_drift(self) -> None:
        verdict = self.evaluate(metrics(headers=("SYMBOL",)), baseline())
        self.assertIn("header_drift", verdict.codes)
        # A new column alongside the declared ones is not drift.
        self.assertEqual(
            self.evaluate(
                metrics(headers=("SYMBOL", "ISIN NUMBER", "NEW COLUMN")), baseline()
            ).status,
            "passed",
        )

    def test_header_drift_ignores_spacing_and_case(self) -> None:
        verdict = self.evaluate(
            metrics(headers=(" symbol ", "ISIN_NUMBER")),
            baseline(headers=("SYMBOL", "ISIN NUMBER")),
        )
        self.assertEqual(verdict.status, "passed")

    def test_rejected_row_rate(self) -> None:
        verdict = self.evaluate(metrics(rejected_row_count=20), baseline())
        self.assertIn("rejected_row_rate", verdict.codes)
        self.assertEqual(
            self.evaluate(metrics(rejected_row_count=2), baseline()).status, "passed"
        )

    def test_duplicate_isin_rate(self) -> None:
        verdict = self.evaluate(
            metrics(duplicate_isin_row_count=5, distinct_isin_count=95), baseline()
        )
        self.assertIn("duplicate_isin_rate", verdict.codes)

    def test_duplicate_isin_rule_is_skipped_when_disabled(self) -> None:
        verdict = self.evaluate(
            metrics(duplicate_isin_row_count=50),
            baseline(),
            replace(DEFAULTS, duplicate_isin_rate=None),
        )
        self.assertEqual(verdict.status, "passed")

    def test_absence_volume_needs_both_bounds_crossed(self) -> None:
        verdict = self.evaluate(
            metrics(would_absent_count=500, present_count=1000), baseline()
        )
        self.assertIn("absence_volume", verdict.codes)
        # A large count of a much larger universe is normal churn, and so is a
        # large share of a tiny one.
        self.assertNotIn(
            "absence_volume",
            self.evaluate(
                metrics(would_absent_count=150, present_count=10_000), baseline()
            ).codes,
        )
        self.assertNotIn(
            "absence_volume",
            self.evaluate(
                metrics(would_absent_count=8, present_count=20), baseline()
            ).codes,
        )

    def test_scope_row_count_collapse_catches_what_the_total_absorbs(self) -> None:
        # A combined file keeping its NSE rows and losing a third of its
        # BSE-exclusive ones: 3000 rows to 2800, a 6.7% fall that every
        # whole-file rule passes, while a third of one swept scope vanishes.
        candidate = metrics(
            accepted_row_count=2800,
            distinct_isin_count=2800,
            non_null_isin_count=2800,
            scope_metrics=(
                ScopeMetrics("NSE", "equity", 2400),
                ScopeMetrics("BSE", "equity", 400),
            ),
        )
        prior = baseline(
            accepted_row_count=3000,
            scope_row_counts=(("NSE", "equity", 2400), ("BSE", "equity", 600)),
        )
        verdict = self.evaluate(candidate, prior)

        self.assertNotIn("row_count_collapse", verdict.codes)
        self.assertIn("scope_row_count_collapse", verdict.codes)
        self.assertEqual(verdict.status, "failed")
        self.assertIn("BSE/equity", verdict.findings[0].detail)

    def test_ordinary_movement_in_every_scope_still_passes(self) -> None:
        verdict = self.evaluate(
            metrics(
                accepted_row_count=2950,
                distinct_isin_count=2950,
                non_null_isin_count=2950,
                scope_metrics=(
                    ScopeMetrics("NSE", "equity", 2390),
                    ScopeMetrics("BSE", "equity", 560),
                ),
            ),
            baseline(
                accepted_row_count=3000,
                scope_row_counts=(("NSE", "equity", 2400), ("BSE", "equity", 600)),
            ),
        )
        self.assertEqual(verdict.status, "passed")

    def test_a_baseline_without_scope_counts_sits_the_rule_out(self) -> None:
        # The snapshot before scopes were measured is a legitimate baseline for
        # every other rule; it simply cannot answer this one.
        verdict = self.evaluate(
            metrics(scope_metrics=(ScopeMetrics("NSE", "equity", 1),)), baseline()
        )
        self.assertEqual(verdict.status, "passed")

    def test_scope_absence_volume_is_bounded_per_scope(self) -> None:
        verdict = self.evaluate(
            metrics(
                would_absent_count=200,
                present_count=6600,
                scope_metrics=(
                    ScopeMetrics("NSE", "equity", 6000, 0, 6000),
                    ScopeMetrics("BSE", "equity", 400, 200, 600),
                ),
            ),
            baseline(),
        )
        # 200 of 6600 is 3% and passes the whole-file bound; 200 of the 600 the
        # sweep would actually run over is a third of that scope.
        self.assertNotIn("absence_volume", verdict.codes)
        self.assertIn("scope_absence_volume", verdict.codes)

    def test_a_scope_with_no_rows_at_all_is_still_judged(self) -> None:
        # The declared scope is swept whether or not the file carries rows for
        # it, so a file that dropped one entirely has to fail here.
        verdict = self.evaluate(
            metrics(
                scope_metrics=(
                    ScopeMetrics("NSE", "equity", 100, 0, 100),
                    ScopeMetrics("BSE", "equity", 0, 600, 600),
                ),
            ),
            baseline(scope_row_counts=(("NSE", "equity", 100), ("BSE", "equity", 600))),
        )
        self.assertEqual(
            {"scope_row_count_collapse", "scope_absence_volume"},
            set(verdict.codes),
        )

    def test_stale_effective_date(self) -> None:
        verdict = self.evaluate(metrics(effective_date="2026-07-30"), baseline())
        self.assertIn("stale_effective_date", verdict.codes)

    def test_future_effective_date(self) -> None:
        verdict = self.evaluate(metrics(effective_date="2026-09-01"), baseline())
        self.assertIn("future_effective_date", verdict.codes)

    def test_every_broken_rule_is_reported_not_just_the_first(self) -> None:
        verdict = self.evaluate(
            metrics(
                accepted_row_count=5,
                rejected_row_count=40,
                headers=("SYMBOL",),
                effective_date="2026-07-01",
                non_null_isin_count=5,
            ),
            baseline(),
        )
        self.assertEqual(
            set(verdict.codes),
            {
                "row_count_floor", "rejected_row_rate", "row_count_collapse",
                "header_drift", "stale_effective_date",
            },
        )

    def test_first_snapshot_of_a_dataset_bootstraps_without_a_baseline(self) -> None:
        verdict = self.evaluate(metrics(accepted_row_count=20), None)
        self.assertEqual(verdict.status, "passed")
        self.assertIsNone(verdict.baseline)

    def test_override_keeps_the_findings_it_overrode(self) -> None:
        verdict = self.evaluate(metrics(accepted_row_count=1), None)
        forced = override(verdict)
        self.assertEqual(forced.status, "overridden")
        self.assertEqual(forced.findings, verdict.findings)
        self.assertTrue(forced.accepted)
        # A passing verdict is never relabelled.
        self.assertEqual(override(self.evaluate(metrics(), None)).status, "passed")

    def test_isin_metrics_counts_repeats_not_distinct_values(self) -> None:
        distinct, duplicates, non_null = isin_metrics(
            [ALPHA_ISIN, ALPHA_ISIN, ALPHA_ISIN, BETA_ISIN, None, ""]
        )
        self.assertEqual((distinct, duplicates, non_null), (2, 2, 4))


class AutomationCase(unittest.TestCase):
    """End-to-end cases over a real database and the real pipeline."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.db = root / "scraper.db"
        self.snapshots = root / "snapshots"
        self.files = root / "files"
        self.files.mkdir()
        self.clock = iter(
            f"2026-11-{day:02d}T00:00:0{index}+00:00"
            for day in range(6, 31)
            for index in range(9)
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def now(self) -> str:
        return next(self.clock)

    # These fixtures are three rows wide, so the size-relative rules have to be
    # relaxed for the cases that are not about them. Each subclass relaxes only
    # the rules its own subject does not depend on.
    GUARD_OVERRIDES: dict = {"row_floor": 1}

    def thresholds(self, dataset: str) -> GuardThresholds:
        return replace(thresholds_for_dataset(dataset), **self.GUARD_OVERRIDES)

    def fetched(self, content: bytes):
        """A stub fetcher standing in for one allowlisted official download."""

        def fetcher(url: str, exchange: str) -> FetchResponse:
            return FetchResponse(
                requested_url=url, final_url=url, status_code=200, content=content,
                content_type="text/csv", encoding=None, etag=None, last_modified=None,
                auth_mode="none", fetch_status="ok",
            )

        return fetcher

    def refresh(
        self, dataset: str, content: bytes, effective_date: str, **kwargs
    ) -> dict:
        """Ingest as a fetched official file, which is what automation does."""
        if not kwargs.get("dry_run", False):
            prepare_database(self.db)
        return refresh_exchange_universe(
            dataset=dataset,
            url=f"https://nsearchives.nseindia.com/content/equities/{dataset}.csv",
            effective_date=effective_date,
            db_path=self.db,
            snapshot_root=self.snapshots,
            now_utc=self.now,
            fetcher=self.fetched(content),
            **{"thresholds": self.thresholds(dataset), **kwargs},
        )

    def rows(self, sql: str, *parameters) -> list[dict]:
        conn = connect(self.db)
        try:
            init_db(conn, apply_migrations=True)
            return [dict(row) for row in conn.execute(sql, parameters)]
        finally:
            conn.close()

    def security(self, exchange_key: str) -> dict:
        return self.rows(
            "SELECT * FROM exchange_securities WHERE exchange_key=?", exchange_key
        )[0]

    def event_types(self, exchange_key: str) -> list[str]:
        return [
            row["event_type"]
            for row in self.rows(
                """SELECT event.event_type FROM exchange_security_events AS event
                   JOIN exchange_securities AS security USING (exchange_security_id)
                   WHERE security.exchange_key=?
                   ORDER BY event.security_event_id""",
                exchange_key,
            )
        ]


class SnapshotGuardPipelineTests(AutomationCase):
    def test_truncated_day_two_master_leaves_day_one_state_untouched(self) -> None:
        self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        payload = self.refresh("nse_equity_master", TRUNCATED_MASTER, "2026-08-02")

        self.assertEqual(payload["validation"]["status"], "failed")
        self.assertIn("row_count_collapse", [
            item["code"] for item in payload["validation"]["findings"]
        ])
        self.assertIsNone(payload["registry"])
        present = self.rows(
            "SELECT exchange_key, is_present, absence_streak FROM exchange_securities"
            " ORDER BY exchange_key"
        )
        self.assertEqual([item["exchange_key"] for item in present],
                         ["ALPHA", "BETA", "GAMMA"])
        self.assertTrue(all(item["is_present"] == 1 for item in present))
        self.assertTrue(all(item["absence_streak"] == 0 for item in present))

    def test_the_rejected_file_is_still_kept_as_evidence(self) -> None:
        self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        payload = self.refresh("nse_equity_master", TRUNCATED_MASTER, "2026-08-02")

        snapshot = self.rows(
            "SELECT * FROM exchange_source_snapshots WHERE snapshot_id=?",
            payload["snapshot_id"],
        )[0]
        self.assertEqual(snapshot["validation_status"], "failed")
        blob = self.rows(
            "SELECT local_path FROM response_blobs WHERE blob_id=?", payload["blob_id"]
        )[0]
        self.assertTrue(Path(blob["local_path"]).exists())
        validation = self.rows(
            "SELECT * FROM exchange_snapshot_validations WHERE snapshot_id=?",
            payload["snapshot_id"],
        )[0]
        self.assertEqual(validation["status"], "failed")
        self.assertIn("row_count_collapse", validation["findings_json"])
        # The thresholds it was judged against are stored with the verdict.
        self.assertIn("row_count_drop_ratio", validation["thresholds_json"])

    def test_override_applies_the_snapshot_the_guard_rejected(self) -> None:
        self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        payload = self.refresh(
            "nse_equity_master", TRUNCATED_MASTER, "2026-08-02",
            override_validation=True,
        )
        self.assertEqual(payload["validation"]["status"], "overridden")
        self.assertTrue(payload["validation"]["findings"])
        self.assertEqual(payload["registry"]["absence_observed"], 2)
        self.assertEqual(self.security("BETA")["absence_streak"], 1)

    def test_a_failed_snapshot_is_not_a_baseline_for_the_next_one(self) -> None:
        self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        self.refresh("nse_equity_master", TRUNCATED_MASTER, "2026-08-02")
        recovered = self.refresh("nse_equity_master", FULL_MASTER, "2026-08-03")
        self.assertEqual(recovered["validation"]["status"], "passed")
        self.assertEqual(
            recovered["validation"]["baseline"]["effective_date"], "2026-08-01"
        )

    def test_first_snapshot_of_a_dataset_needs_no_override(self) -> None:
        payload = self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        self.assertEqual(payload["validation"]["status"], "passed")
        self.assertIsNone(payload["validation"]["baseline"])
        self.assertEqual(payload["registry"]["added"], 3)

    def test_a_rejected_result_feed_queues_nothing(self) -> None:
        self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        feed = (
            "SYMBOL,COMPANY NAME,INDUSTRY,RELATING TO,PERIOD ENDED,AUDITED,"
            "CUMULATIVE,CONSOLIDATED,BROADCAST DATE/TIME\n"
            "ALPHA,Alpha Industries Limited,Capital Goods,Quarterly,30-JUN-2026,"
            "Un-Audited,Non-cumulative,Consolidated,05-Aug-2026 18:30:00\n"
        ).encode()
        prepare_database(self.db)
        payload = discover_exchange_results(
            dataset="nse_financial_results",
            url="https://nsearchives.nseindia.com/content/equities/results.csv",
            effective_date="2026-12-01",
            db_path=self.db,
            snapshot_root=self.snapshots,
            now_utc=self.now,
            fetcher=self.fetched(feed),
            thresholds=GuardThresholds(row_floor=50),
        )
        self.assertEqual(payload["validation"]["status"], "failed")
        self.assertEqual(payload["enqueue"], {})
        self.assertEqual(self.rows("SELECT * FROM screener_company_queue"), [])
        # The watermark did not move, so the feed is retried, not skipped.
        self.assertEqual(self.rows("SELECT * FROM exchange_result_watermarks"), [])


class AbsenceStreakTests(AutomationCase):
    GUARD_OVERRIDES = {"row_floor": 1, "row_count_drop_ratio": None}

    def test_one_absence_leaves_the_security_present(self) -> None:
        self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        payload = self.refresh(
            "nse_equity_master", nse_master((ALPHA_ROW, GAMMA_ROW)), "2026-08-02"
        )
        self.assertEqual(payload["registry"]["absence_observed"], 1)
        self.assertEqual(payload["registry"]["removed"], 0)
        beta = self.security("BETA")
        self.assertEqual(beta["is_present"], 1)
        self.assertEqual(beta["absence_streak"], 1)
        self.assertEqual(beta["first_absent_date"], "2026-08-02")
        self.assertEqual(beta["official_presence"], "not_confirmed")
        self.assertEqual(beta["current_status"], "active")

    def test_three_absent_dates_confirm_the_removal(self) -> None:
        self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        without_beta = nse_master((ALPHA_ROW, GAMMA_ROW))
        for day in ("2026-08-02", "2026-08-03"):
            payload = self.refresh("nse_equity_master", without_beta, day)
            self.assertEqual(payload["registry"]["removed"], 0)
        final = self.refresh("nse_equity_master", without_beta, "2026-08-04")

        self.assertEqual(final["registry"]["removed"], 1)
        beta = self.security("BETA")
        self.assertEqual(beta["is_present"], 0)
        self.assertEqual(beta["current_status"], "absent_from_master")
        self.assertEqual(beta["absence_streak"], 3)
        self.assertEqual(beta["first_absent_date"], "2026-08-02")
        events = self.event_types("BETA")
        self.assertEqual(events.count("absence_observed"), 3)
        # `removed` is retained alongside the new event so existing consumers of
        # the registry keep working unchanged.
        self.assertEqual(events.count("absence_confirmed"), 1)
        self.assertEqual(events.count("removed"), 1)

    def test_a_confirmed_absence_stops_advancing_the_streak(self) -> None:
        self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        without_beta = nse_master((ALPHA_ROW, GAMMA_ROW))
        for day in ("2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"):
            self.refresh("nse_equity_master", without_beta, day)
        self.assertEqual(self.security("BETA")["absence_streak"], 3)
        self.assertEqual(self.event_types("BETA").count("removed"), 1)

    def test_returning_to_the_master_resets_the_streak(self) -> None:
        self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        without_beta = nse_master((ALPHA_ROW, GAMMA_ROW))
        self.refresh("nse_equity_master", without_beta, "2026-08-02")
        self.refresh("nse_equity_master", without_beta, "2026-08-03")
        self.assertEqual(self.security("BETA")["absence_streak"], 2)

        self.refresh("nse_equity_master", FULL_MASTER, "2026-08-04")
        beta = self.security("BETA")
        self.assertEqual(beta["absence_streak"], 0)
        self.assertIsNone(beta["first_absent_date"])
        self.assertEqual(beta["is_present"], 1)
        self.assertEqual(beta["official_presence"], "confirmed")
        self.assertNotIn("removed", self.event_types("BETA"))
        self.assertNotIn("reinstated", self.event_types("BETA"))

    def test_reingesting_the_same_dated_file_cannot_advance_the_streak(self) -> None:
        self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        without_beta = nse_master((ALPHA_ROW, GAMMA_ROW))
        self.refresh("nse_equity_master", without_beta, "2026-08-02")
        again = self.refresh("nse_equity_master", without_beta, "2026-08-02")

        self.assertEqual(again["registry"]["absence_repeated"], 1)
        self.assertEqual(again["registry"]["absence_observed"], 0)
        self.assertEqual(self.security("BETA")["absence_streak"], 1)
        self.assertEqual(self.event_types("BETA").count("absence_observed"), 1)

    def test_an_explicit_delisting_still_takes_effect_immediately(self) -> None:
        # A BSE master saying "Delisted" is a positive statement from the
        # source, not an absence, so the streak rule does not apply to it.
        header = (
            "Security Code,Issuer Name,Security Id,Security Name,Status,Group,"
            "Face Value,ISIN No,Industry,Instrument\n"
        )
        active = (
            header
            + f"500001,Alpha Industries Limited,ALPHAIND,ALPHA IND,Active,A,10,"
              f"{ALPHA_ISIN},Capital Goods,Equity\n"
        ).encode()
        delisted = (
            header
            + f"500001,Alpha Industries Limited,ALPHAIND,ALPHA IND,Delisted,A,10,"
              f"{ALPHA_ISIN},Capital Goods,Equity\n"
        ).encode()
        self.refresh("bse_security_master", active, "2026-08-01")
        self.refresh("bse_security_master", delisted, "2026-08-02")

        security = self.security("500001")
        self.assertEqual(security["current_status"], "delisted")
        self.assertEqual(security["absence_streak"], 0)
        self.assertIn("status_changed", self.event_types("500001"))


class ProvenanceTests(AutomationCase):
    # A file where half the ISINs repeat would be rejected outright at
    # production thresholds; the registry-level duplicate rule exists for the
    # single stray duplicate inside an otherwise sound file.
    GUARD_OVERRIDES = {
        "row_floor": 1, "row_count_drop_ratio": None, "duplicate_isin_rate": None,
    }

    def operator_import(self, content: bytes, effective_date: str) -> dict:
        """Ingest as a hand-carried file, which is how the registry was seeded."""
        prepare_database(self.db)
        return refresh_exchange_universe(
            dataset="nse_equity_master",
            file=self.write(f"manual-{effective_date}.csv", content),
            effective_date=effective_date,
            db_path=self.db,
            snapshot_root=self.snapshots,
            now_utc=self.now,
            thresholds=self.thresholds("nse_equity_master"),
        )

    def write(self, name: str, content: bytes) -> Path:
        path = self.files / name
        path.write_bytes(content)
        return path

    def test_an_operator_import_is_not_marked_official(self) -> None:
        self.operator_import(FULL_MASTER, "2026-08-01")
        alpha = self.security("ALPHA")
        self.assertEqual(alpha["source_type"], "manual_import")
        self.assertEqual(alpha["verification_status"], "unverified")
        self.assertEqual(alpha["official_presence"], "confirmed")

    def test_a_matching_official_file_verifies_the_manual_row_in_place(self) -> None:
        self.operator_import(FULL_MASTER, "2026-08-01")
        payload = self.refresh("nse_equity_master", FULL_MASTER, "2026-08-02")

        self.assertEqual(payload["registry"]["added"], 0)
        self.assertEqual(payload["registry"]["verified"], 3)
        self.assertEqual(len(self.rows("SELECT * FROM exchange_securities")), 3)
        alpha = self.security("ALPHA")
        self.assertEqual(alpha["source_type"], "official_automated")
        self.assertEqual(alpha["verification_status"], "verified")
        self.assertEqual(alpha["official_presence"], "confirmed")
        self.assertIn("provenance_verified", self.event_types("ALPHA"))
        # One ISIN, one registry company, one current link: no duplication.
        self.assertEqual(
            len(self.rows(
                "SELECT * FROM exchange_company_registry WHERE isin=?", ALPHA_ISIN
            )),
            1,
        )
        self.assertEqual(
            len(self.rows(
                "SELECT * FROM exchange_company_links WHERE is_current=1"
            )),
            3,
        )
        # A second official pass has nothing left to promote.
        again = self.refresh("nse_equity_master", FULL_MASTER, "2026-08-03")
        self.assertEqual(again["registry"]["verified"], 0)

    def test_a_disagreeing_official_file_opens_a_conflict_instead_of_overwriting(
        self,
    ) -> None:
        self.operator_import(FULL_MASTER, "2026-08-01")
        # The official file gives ALPHA's symbol a different instrument.
        restated = nse_master(
            (
                f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{DELTA_ISIN},10",
                BETA_ROW,
                GAMMA_ROW,
            )
        )
        payload = self.refresh("nse_equity_master", restated, "2026-08-02")

        self.assertEqual(payload["registry"]["conflicts"], 1)
        alpha = self.security("ALPHA")
        self.assertEqual(alpha["current_isin"], ALPHA_ISIN)  # unchanged
        self.assertEqual(alpha["verification_status"], "conflict")
        self.assertEqual(alpha["source_type"], "manual_import")
        conflict = self.rows("SELECT * FROM exchange_identity_conflicts")[0]
        self.assertEqual(conflict["conflict_type"], "isin_mismatch")
        self.assertEqual(conflict["existing_value"], ALPHA_ISIN)
        self.assertEqual(conflict["incoming_value"], DELTA_ISIN)
        self.assertEqual(conflict["existing_source_type"], "manual_import")
        self.assertEqual(conflict["resolution"], "open")
        # No new version was written and the registry link did not move.
        self.assertEqual(
            len(self.rows(
                """SELECT version.version_id FROM exchange_security_versions AS version
                   JOIN exchange_securities AS security USING (exchange_security_id)
                   WHERE security.exchange_key='ALPHA'"""
            )),
            1,
        )
        self.assertIn("conflict_detected", self.event_types("ALPHA"))

    def test_the_same_disagreement_seen_again_is_not_a_second_conflict(self) -> None:
        self.operator_import(FULL_MASTER, "2026-08-01")
        restated = nse_master(
            (
                f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{DELTA_ISIN},10",
                BETA_ROW,
                GAMMA_ROW,
            )
        )
        self.refresh("nse_equity_master", restated, "2026-08-02")
        again = self.refresh("nse_equity_master", restated, "2026-08-03")
        self.assertEqual(again["registry"]["conflicts"], 0)
        self.assertEqual(len(self.rows("SELECT * FROM exchange_identity_conflicts")), 1)

    def test_a_conflicted_company_never_reaches_the_screener_worker(self) -> None:
        self.operator_import(FULL_MASTER, "2026-08-01")
        restated = nse_master(
            (
                f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{DELTA_ISIN},10",
                BETA_ROW,
                GAMMA_ROW,
            )
        )
        self.refresh("nse_equity_master", restated, "2026-08-02")

        conn = connect(self.db)
        try:
            init_db(conn, apply_migrations=True)
            blocked = resolve_screener_path(
                conn, exchange="NSE", exchange_key="ALPHA",
                reporting_scope="consolidated",
            )
            allowed = resolve_screener_path(
                conn, exchange="NSE", exchange_key="BETA",
                reporting_scope="consolidated",
            )
        finally:
            conn.close()
        self.assertEqual(blocked["decision"], "manual_review")
        self.assertEqual(blocked["reason"], "identity_conflict")
        self.assertEqual(allowed["decision"], "resolved")

    def test_keeping_the_existing_value_unblocks_the_security(self) -> None:
        self.operator_import(FULL_MASTER, "2026-08-01")
        restated = nse_master(
            (
                f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{DELTA_ISIN},10",
                BETA_ROW,
                GAMMA_ROW,
            )
        )
        self.refresh("nse_equity_master", restated, "2026-08-02")

        conn = connect(self.db)
        try:
            init_db(conn, apply_migrations=True)
            conflict_id = open_identity_conflicts(conn)[0]["conflict_id"]
            outcome = resolve_identity_conflict(
                conn, conflict_id=conflict_id, resolution="kept_existing",
                now_utc="2026-12-01T00:00:00+00:00",
            )
            self.assertFalse(outcome["applied_incoming_value"])
            self.assertEqual(open_identity_conflicts(conn), [])
            unblocked = resolve_screener_path(
                conn, exchange="NSE", exchange_key="ALPHA",
                reporting_scope="consolidated",
            )
        finally:
            conn.close()
        self.assertEqual(unblocked["decision"], "resolved")
        alpha = self.security("ALPHA")
        self.assertEqual(alpha["verification_status"], "verified")
        self.assertEqual(alpha["current_isin"], ALPHA_ISIN)
        self.assertIn("conflict_resolved", self.event_types("ALPHA"))

    def test_accepting_the_official_value_relinks_the_registry(self) -> None:
        self.operator_import(FULL_MASTER, "2026-08-01")
        restated = nse_master(
            (
                f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{DELTA_ISIN},10",
                BETA_ROW,
                GAMMA_ROW,
            )
        )
        self.refresh("nse_equity_master", restated, "2026-08-02")

        conn = connect(self.db)
        try:
            init_db(conn, apply_migrations=True)
            conflict_id = open_identity_conflicts(conn)[0]["conflict_id"]
            outcome = resolve_identity_conflict(
                conn, conflict_id=conflict_id, resolution="accepted_official",
                now_utc="2026-12-01T00:00:00+00:00",
            )
        finally:
            conn.close()
        self.assertTrue(outcome["applied_incoming_value"])
        alpha = self.security("ALPHA")
        self.assertEqual(alpha["current_isin"], DELTA_ISIN)
        self.assertEqual(alpha["verification_status"], "verified")
        self.assertIn("isin_changed", self.event_types("ALPHA"))
        current = self.rows(
            """SELECT registry.isin FROM exchange_company_links AS link
               JOIN exchange_company_registry AS registry USING (registry_company_id)
               JOIN exchange_securities AS security USING (exchange_security_id)
               WHERE security.exchange_key='ALPHA' AND link.is_current=1"""
        )
        self.assertEqual([item["isin"] for item in current], [DELTA_ISIN])

    def test_resolving_a_conflict_twice_is_refused(self) -> None:
        self.operator_import(FULL_MASTER, "2026-08-01")
        restated = nse_master(
            (
                f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{DELTA_ISIN},10",
                BETA_ROW,
                GAMMA_ROW,
            )
        )
        self.refresh("nse_equity_master", restated, "2026-08-02")
        conn = connect(self.db)
        try:
            init_db(conn, apply_migrations=True)
            conflict_id = open_identity_conflicts(conn)[0]["conflict_id"]
            resolve_identity_conflict(
                conn, conflict_id=conflict_id, resolution="dismissed",
                now_utc="2026-12-01T00:00:00+00:00",
            )
            with self.assertRaises(ValueError):
                resolve_identity_conflict(
                    conn, conflict_id=conflict_id, resolution="kept_existing",
                    now_utc="2026-12-01T00:00:00+00:00",
                )
            with self.assertRaises(ValueError):
                resolve_identity_conflict(
                    conn, conflict_id=999, resolution="kept_existing",
                    now_utc="2026-12-01T00:00:00+00:00",
                )
        finally:
            conn.close()

    def test_two_rows_of_one_file_claiming_one_isin_conflict_and_neither_links(
        self,
    ) -> None:
        duplicated = nse_master(
            (
                ALPHA_ROW,
                f"ALPHATWO,Alpha Industries Two Limited,EQ,20-JAN-1995,10,1,"
                f"{ALPHA_ISIN},10",
            )
        )
        payload = self.refresh("nse_equity_master", duplicated, "2026-08-01")

        self.assertEqual(payload["registry"]["added"], 2)
        self.assertEqual(payload["registry"]["conflicts"], 1)
        self.assertEqual(payload["registry"]["linked"], 1)
        conflict = self.rows("SELECT * FROM exchange_identity_conflicts")[0]
        self.assertEqual(conflict["conflict_type"], "duplicate_isin")
        self.assertEqual(conflict["exchange_key"], "ALPHATWO")
        self.assertEqual(self.security("ALPHATWO")["verification_status"], "conflict")
        self.assertEqual(
            len(self.rows("SELECT * FROM exchange_company_links WHERE is_current=1")), 1
        )

    def test_a_symbol_rename_is_not_a_duplicate_isin_conflict(self) -> None:
        # BETA leaves the master and BETANEW arrives holding its ISIN. BETA is
        # still present because its absence is unconfirmed, but it is not in
        # this file, so the new symbol is superseding it, not colliding.
        self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        renamed = nse_master(
            (
                ALPHA_ROW,
                f"BETANEW,Beta Global Limited,EQ,04-JUL-2004,1,1,{BETA_ISIN},1",
                GAMMA_ROW,
            )
        )
        payload = self.refresh("nse_equity_master", renamed, "2026-08-02")

        self.assertEqual(payload["registry"]["conflicts"], 0)
        self.assertEqual(self.rows("SELECT * FROM exchange_identity_conflicts"), [])
        self.assertEqual(self.security("BETANEW")["verification_status"], "verified")


if __name__ == "__main__":
    unittest.main()
