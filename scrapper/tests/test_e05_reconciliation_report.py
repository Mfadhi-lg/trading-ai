"""Offline tests for the first-run reconciliation report (C6).

The report exists to answer one question before automation is turned on: when an
official file met a hand-built registry, what matched, what is new, what went
unconfirmed, and where do they disagree. It is a gate as much as a document —
non-zero while any identity conflict is open.
"""

from __future__ import annotations

from scrapper.tests.database_fixtures import prepare_database

import json
import unittest

from scrapper.db.database import connect, init_db
from scrapper.db.exchange_repo import resolve_identity_conflict
from scrapper.exports.exchange_reconciliation_report import (
    build_exchange_reconciliation_report,
    write_exchange_reconciliation_report,
)

from .test_e02_snapshot_guard import (
    ALPHA_ISIN,
    BETA_ISIN,
    DELTA_ISIN,
    GAMMA_ISIN,
    AutomationCase,
    nse_master,
)
from .test_e04_mii_universe import MII_DATASET, MII_BSE_ROW, MII_NSE_ROW, mii_file


ALPHA_ROW = f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{ALPHA_ISIN},10"
BETA_ROW = f"BETA,Beta Services Limited,EQ,04-JUL-2004,1,1,{BETA_ISIN},1"
GAMMA_ROW = f"GAMMA,Gamma Textiles Limited,EQ,01-JAN-2010,10,1,{GAMMA_ISIN},10"
# The same exchange key an operator imported, but a different instrument.
ALPHA_DISPUTED_ROW = f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{DELTA_ISIN},10"


class ReconciliationReportCase(AutomationCase):
    GUARD_OVERRIDES = {
        "row_floor": 1, "row_count_drop_ratio": None, "rejected_row_rate": 1.0,
    }

    def imported(self, dataset: str, content: bytes, effective_date: str) -> dict:
        """Ingest as an operator-supplied file: how the registry was seeded."""
        path = self.snapshots.parent / f"{dataset}-{effective_date}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        from scrapper.pipeline.exchange_universe import refresh_exchange_universe

        prepare_database(self.db)
        return refresh_exchange_universe(
            dataset=dataset, file=path, effective_date=effective_date,
            db_path=self.db, snapshot_root=self.snapshots, now_utc=self.now,
            thresholds=self.thresholds(dataset),
        )

    def report(self, snapshot_id: int | None = None) -> dict:
        conn = connect(self.db)
        try:
            init_db(conn, apply_migrations=True)
            return build_exchange_reconciliation_report(conn, snapshot_id)
        finally:
            conn.close()


class ReportSectionTests(ReconciliationReportCase):
    def test_a_matched_row_is_matched_not_counted_as_new(self) -> None:
        self.imported("nse_equity_master", nse_master((ALPHA_ROW,)), "2026-08-01")
        self.refresh("nse_equity_master", nse_master((ALPHA_ROW,)), "2026-08-02")

        report = self.report()
        self.assertEqual(report["summary"]["matched_existing_count"], 1)
        self.assertEqual(report["summary"]["new_nse_security_count"], 0)
        self.assertEqual(report["matched_existing"][0]["exchange_key"], "ALPHA")

    def test_new_securities_are_split_by_exchange(self) -> None:
        self.refresh(MII_DATASET, mii_file((MII_NSE_ROW, MII_BSE_ROW)), "2026-08-01")

        report = self.report()
        self.assertEqual(report["summary"]["new_nse_security_count"], 1)
        self.assertEqual(report["summary"]["new_bse_exclusive_security_count"], 1)
        self.assertEqual(
            report["new_bse_exclusive_securities"][0]["exchange_key"], "500002"
        )

    def test_an_unconfirmed_manual_row_is_listed_with_its_absence_streak(self) -> None:
        self.imported(
            "nse_equity_master", nse_master((ALPHA_ROW, BETA_ROW)), "2026-08-01"
        )
        self.refresh("nse_equity_master", nse_master((ALPHA_ROW,)), "2026-08-02")

        report = self.report()
        self.assertEqual(report["summary"]["unconfirmed_manual_count"], 1)
        unconfirmed = report["unconfirmed_manual_records"][0]
        self.assertEqual(unconfirmed["exchange_key"], "BETA")
        self.assertEqual(unconfirmed["absence_streak"], 1)
        self.assertEqual(unconfirmed["first_absent_date"], "2026-08-02")

    def test_an_open_isin_conflict_is_reported_by_type(self) -> None:
        self.imported("nse_equity_master", nse_master((ALPHA_ROW,)), "2026-08-01")
        self.refresh(
            "nse_equity_master", nse_master((ALPHA_DISPUTED_ROW,)), "2026-08-02"
        )

        report = self.report()
        self.assertEqual(report["summary"]["open_conflict_count"], 1)
        self.assertEqual(
            report["summary"]["open_conflict_counts_by_type"]["isin_mismatch"], 1
        )
        conflict = report["open_conflicts"]["isin_mismatch"][0]
        self.assertEqual(conflict["existing_value"], ALPHA_ISIN)
        self.assertEqual(conflict["incoming_value"], DELTA_ISIN)
        self.assertEqual(conflict["existing_source_type"], "manual_import")

    def test_rejected_rows_are_grouped_by_reason_code(self) -> None:
        broken = nse_master((ALPHA_ROW, "BROKEN,Broken Limited,EQ,,,,INE000000000,1"))
        self.refresh("nse_equity_master", broken, "2026-08-01")

        self.assertEqual(
            self.report()["summary"]["rejected_rows_by_reason"], {"invalid_isin": 1}
        )

    def test_excluded_rows_are_grouped_by_instrument_class(self) -> None:
        from .test_e04_mii_universe import FULL_MII

        self.refresh(MII_DATASET, FULL_MII, "2026-08-01")

        self.assertEqual(
            self.report()["summary"]["excluded_rows_by_instrument_class"], {"etf": 1}
        )

    def test_guard_findings_for_the_snapshot_are_carried_through(self) -> None:
        self.refresh("nse_equity_master", nse_master((ALPHA_ROW, BETA_ROW)), "2026-08-01")
        rejected = self.refresh(
            "nse_equity_master", nse_master((ALPHA_ROW,)), "2026-08-02",
            thresholds=self.thresholds("nse_equity_master").__class__(
                row_count_drop_ratio=0.10, row_floor=1, rejected_row_rate=1.0,
                duplicate_isin_rate=None,
            ),
        )
        self.assertEqual(rejected["validation"]["status"], "failed")

        report = self.report(rejected["snapshot_id"])
        codes = [
            item["code"]
            for entry in report["guard_findings"]
            for item in entry["findings"]
        ]
        self.assertIn("row_count_collapse", codes)

    def test_provenance_is_summarised_across_the_whole_registry(self) -> None:
        self.imported("nse_equity_master", nse_master((ALPHA_ROW,)), "2026-08-01")

        self.assertEqual(
            self.report()["summary"]["securities_by_provenance"],
            {"manual_import_unverified": 1},
        )


class ReportScopeTests(ReconciliationReportCase):
    def test_without_a_snapshot_it_reports_the_latest_accepted_master(self) -> None:
        self.refresh("nse_equity_master", nse_master((ALPHA_ROW,)), "2026-08-01")
        second = self.refresh(
            "nse_equity_master", nse_master((ALPHA_ROW, BETA_ROW)), "2026-08-02"
        )

        self.assertEqual(self.report()["scope_snapshot_ids"], [second["snapshot_id"]])

    def test_a_rejected_snapshot_is_never_the_reported_state(self) -> None:
        good = self.refresh("nse_equity_master", nse_master((ALPHA_ROW, BETA_ROW)), "2026-08-01")
        self.refresh(
            "nse_equity_master", nse_master((ALPHA_ROW,)), "2026-08-02",
            thresholds=self.thresholds("nse_equity_master").__class__(
                row_count_drop_ratio=0.10, row_floor=1, rejected_row_rate=1.0,
                duplicate_isin_rate=None,
            ),
        )

        self.assertEqual(self.report()["scope_snapshot_ids"], [good["snapshot_id"]])

    def test_an_explicit_snapshot_id_is_honoured(self) -> None:
        first = self.refresh("nse_equity_master", nse_master((ALPHA_ROW,)), "2026-08-01")
        self.refresh("nse_equity_master", nse_master((ALPHA_ROW, BETA_ROW)), "2026-08-02")

        report = self.report(first["snapshot_id"])
        self.assertEqual(report["scope_snapshot_ids"], [first["snapshot_id"]])
        self.assertEqual(report["summary"]["new_nse_security_count"], 1)

    def test_an_empty_registry_reports_cleanly_rather_than_failing(self) -> None:
        report = self.report()
        self.assertEqual(report["scope_snapshot_ids"], [])
        self.assertEqual(report["reconciliation_status"], "clear")


class ReportGateTests(ReconciliationReportCase):
    def _conflicted(self) -> None:
        self.imported("nse_equity_master", nse_master((ALPHA_ROW,)), "2026-08-01")
        self.refresh(
            "nse_equity_master", nse_master((ALPHA_DISPUTED_ROW,)), "2026-08-02"
        )

    def test_an_open_conflict_blocks(self) -> None:
        self._conflicted()
        self.assertEqual(self.report()["reconciliation_status"], "blocked")

    def test_resolving_the_conflict_clears_the_block(self) -> None:
        self._conflicted()
        conflict_id = self.report()["open_conflicts"]["isin_mismatch"][0]["conflict_id"]

        conn = connect(self.db)
        try:
            init_db(conn, apply_migrations=True)
            resolve_identity_conflict(
                conn, conflict_id=conflict_id, resolution="kept_existing",
                now_utc=self.now(),
            )
        finally:
            conn.close()

        report = self.report()
        self.assertEqual(report["reconciliation_status"], "clear")
        self.assertEqual(report["summary"]["open_conflict_count"], 0)

    def test_the_written_document_is_deterministic_json(self) -> None:
        self._conflicted()
        output = self.snapshots.parent / "reconciliation.json"

        conn = connect(self.db)
        try:
            init_db(conn, apply_migrations=True)
            first = write_exchange_reconciliation_report(conn, output).read_text()
            second = write_exchange_reconciliation_report(conn, output).read_text()
        finally:
            conn.close()

        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["reconciliation_status"], "blocked")


if __name__ == "__main__":
    unittest.main()
