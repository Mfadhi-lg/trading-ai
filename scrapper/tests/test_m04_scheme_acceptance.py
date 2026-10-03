from __future__ import annotations

from scrapper.tests.database_fixtures import prepare_database

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from scrapper.db.database import connect
from scrapper.exports.mf_workbook_json_export import build_mf_workbook_export
from scrapper.pipeline.import_mf import (
    execute_import,
    plan_directory_import,
    plan_import,
)


ROOT = Path(__file__).resolve().parent
FIXTURE = (
    ROOT / "fixtures" / "mf_workbook"
    / "PPFAS_Monthly_Portfolio_Report_June_30_2026.xls"
)
GOLDEN = ROOT / "golden" / "ppfas_2026_06_m04_expected.json"
SHEET1 = "xl/worksheets/sheet1.xml"


def workbook_with_inline_cell(
    source: Path, destination: Path, coordinate: str, value: str
) -> Path:
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED
    ) as outgoing:
        for info in incoming.infolist():
            payload = incoming.read(info.filename)
            if info.filename == SHEET1:
                root = ElementTree.fromstring(payload)
                found = False
                for cell in root.iter():
                    if not cell.tag.endswith("}c") or cell.attrib.get("r") != coordinate:
                        continue
                    namespace = cell.tag.split("}", 1)[0] + "}"
                    cell.attrib.clear()
                    cell.attrib.update({"r": coordinate, "t": "inlineStr"})
                    for child in list(cell):
                        cell.remove(child)
                    inline = ElementTree.SubElement(cell, namespace + "is")
                    text = ElementTree.SubElement(inline, namespace + "t")
                    text.text = value
                    found = True
                    break
                if not found:
                    raise AssertionError(f"Fixture cell not found: {coordinate}")
                payload = ElementTree.tostring(
                    root, encoding="utf-8", xml_declaration=True
                )
            outgoing.writestr(info.filename, payload)
    return destination


class PPFASSchemeAcceptanceM04Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.sqlite"
        self.snapshot_root = self.root / "snapshots"
        self.import_root = self.root / "mf_import"
        self.export_root = self.root / "exports"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def proposals(self, workbook: Path = FIXTURE):
        return plan_import(
            publisher="ppfas", disclosure_month="2026-06", path=workbook,
            db_path=self.db_path, snapshot_root=self.snapshot_root,
            import_root=self.import_root,
        )

    def execute(self, proposals, timestamp: str):
        prepare_database(self.db_path)
        return execute_import(
            proposals, db_path=self.db_path, observed_at_utc=timestamp,
            export_root=self.export_root,
        )

    def test_registry_acceptance_views_duplicate_and_export_match_golden(self) -> None:
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        first = self.execute(
            self.proposals(), "2026-08-05T03:00:00+00:00"
        )["files"][0]
        self.assertEqual(first["import_state"], expected["state"])
        self.assertEqual(
            first["finalization"]["validation_status"],
            expected["validation_status"],
        )
        self.assertTrue(Path(first["export_path"]).is_file())

        duplicate_plan = self.proposals()
        self.assertEqual(duplicate_plan[0].proposed_action, "duplicate_observation")
        duplicate = self.execute(
            duplicate_plan, "2026-08-05T03:01:00+00:00"
        )["files"][0]
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["import_state"], "accepted")

        conn = connect(self.db_path)
        try:
            classifications = {
                row["scheme_code"]: [
                    row["scheme_management_type"],
                    bool(row["is_equity_scheme"]),
                    row["scheme_category"],
                ]
                for row in conn.execute(
                    """SELECT s.scheme_code, c.scheme_management_type,
                              c.is_equity_scheme, c.scheme_category
                       FROM mf_schemes AS s
                       JOIN mf_scheme_classifications AS c USING(scheme_id)
                       ORDER BY s.scheme_code"""
                )
            }
            self.assertEqual(classifications, expected["classifications"])
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_schemes").fetchone()[0],
                expected["scheme_count"],
            )
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM mf_scheme_classifications
                       WHERE scheme_management_type='active' AND is_equity_scheme=1"""
                ).fetchone()[0],
                expected["active_equity_scheme_count"],
            )
            count_queries = {
                "all_holding_count": "SELECT COUNT(*) FROM mf_accepted_holdings",
                "active_equity_holding_count": (
                    "SELECT COUNT(*) FROM mf_active_equity_accepted_holdings"
                ),
                "all_feature_eligible_count": (
                    "SELECT COUNT(*) FROM mf_stock_feature_eligible_holdings"
                ),
                "active_equity_feature_eligible_count": (
                    "SELECT COUNT(*) FROM mf_active_equity_stock_feature_eligible_holdings"
                ),
            }
            for key, query in count_queries.items():
                self.assertEqual(conn.execute(query).fetchone()[0], expected[key])
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_import_files").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_holdings").fetchone()[0], 727
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM mf_import_observations"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM mf_import_state_events"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM mf_workbook_acceptance_events"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM mf_accepted_version_events"
                ).fetchone()[0],
                7,
            )
            payload = build_mf_workbook_export(conn, 1)
        finally:
            conn.close()
        self.assertEqual(payload["schema_version"], "1.3.0")
        self.assertEqual(payload["provenance"]["import_state"], "accepted")
        self.assertEqual(len(payload["normalized"]["holdings"]), 727)
        self.assertEqual(len(payload["normalized"]["scheme_registry"]), 7)
        self.assertEqual(
            payload["normalized"]["accepted_scope_counts"],
            {
                "all_scheme_accepted_holdings": 727,
                "active_equity_accepted_holdings": 298,
                "all_scheme_stock_feature_eligible_holdings": 586,
                "active_equity_stock_feature_eligible_holdings": 264,
            },
        )
        self.assertEqual([e["to_state"] for e in payload["quality"]["state_events"]], [
            "inbox", "accepted"
        ])

    def test_blocking_normalization_failure_is_rejected_without_partial_pointer(self) -> None:
        invalid = workbook_with_inline_cell(
            FIXTURE, self.root / "invalid-isin.xls", "C7", "INE040A01035"
        )
        result = self.execute(
            self.proposals(invalid), "2026-08-05T04:00:00+00:00"
        )["files"][0]
        self.assertEqual(result["import_state"], "rejected")
        self.assertIn(
            "m03_isin_structural_validity",
            result["finalization"]["failed_error_rules"],
        )
        conn = connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_holdings").fetchone()[0], 727
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_accepted_versions").fetchone()[0], 0
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_accepted_holdings").fetchone()[0], 0
            )
            self.assertEqual(
                [row[0] for row in conn.execute(
                    "SELECT to_state FROM mf_import_state_events ORDER BY state_event_id"
                )],
                ["inbox", "rejected"],
            )
        finally:
            conn.close()

    def test_conflicting_duplicate_observation_cannot_corrupt_accepted_import(self) -> None:
        self.execute(self.proposals(), "2026-08-05T04:30:00+00:00")
        conflicting = plan_import(
            publisher="ppfas", disclosure_month="2026-05", path=FIXTURE,
            db_path=self.db_path, snapshot_root=self.snapshot_root,
            import_root=self.import_root,
        )
        self.assertEqual(conflicting[0].proposed_action, "quarantine")
        result = self.execute(
            conflicting, "2026-08-05T04:31:00+00:00"
        )["files"][0]
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["import_state"], "accepted")
        self.assertNotIn("export_path", result)
        conn = connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM mf_workbook_validation_results
                       WHERE import_file_id=1 AND passed=0"""
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_import_state_events").fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_accepted_versions").fetchone()[0],
                7,
            )
        finally:
            conn.close()

    def test_superseding_version_preserves_history_but_views_follow_new_pointer(self) -> None:
        first = self.execute(
            self.proposals(), "2026-08-05T05:00:00+00:00"
        )["files"][0]
        revised = workbook_with_inline_cell(
            FIXTURE, self.root / "revised.xls", "B350", "Revision evidence"
        )
        second = self.execute(
            self.proposals(revised), "2026-08-05T05:01:00+00:00"
        )["files"][0]
        self.assertNotEqual(first["import_file_id"], second["import_file_id"])
        self.assertEqual(second["finalization"]["superseded_import_file_ids"], [1])

        conn = connect(self.db_path)
        try:
            self.assertEqual(
                [row[0] for row in conn.execute(
                    "SELECT import_state FROM mf_import_files ORDER BY import_file_id"
                )],
                ["accepted", "accepted"],
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_holdings").fetchone()[0], 1454
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM mf_accepted_versions WHERE import_file_id=2"
                ).fetchone()[0],
                7,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM mf_accepted_holdings WHERE import_file_id=1"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM mf_accepted_holdings WHERE import_file_id=2"
                ).fetchone()[0],
                727,
            )
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM mf_accepted_version_events
                       WHERE import_file_id=2 AND previous_import_file_id=1"""
                ).fetchone()[0],
                7,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM mf_workbook_acceptance_events"
                ).fetchone()[0],
                2,
            )
        finally:
            conn.close()

    def test_directory_execution_resumes_completed_files_idempotently(self) -> None:
        inbox = self.root / "inbox" / "ppfas" / "2026-06"
        inbox.mkdir(parents=True)
        shutil.copyfile(FIXTURE, inbox / "a-original.xls")
        workbook_with_inline_cell(
            FIXTURE, inbox / "b-revised.xls", "B350", "Directory revision"
        )
        planned = plan_directory_import(
            path=self.root / "inbox", db_path=self.db_path,
            snapshot_root=self.snapshot_root, import_root=self.import_root,
        )
        self.assertEqual(len(planned), 2)
        self.execute(planned[:1], "2026-08-05T06:00:00+00:00")

        resumed = plan_directory_import(
            path=self.root / "inbox", db_path=self.db_path,
            snapshot_root=self.snapshot_root, import_root=self.import_root,
        )
        self.assertEqual(
            [item.proposed_action for item in resumed],
            ["duplicate_observation", "inventory"],
        )
        result = self.execute(resumed, "2026-08-05T06:01:00+00:00")
        self.assertEqual(result["state_counts"]["accepted"], 2)
        conn = connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_import_files").fetchone()[0], 2
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_import_observations").fetchone()[0],
                3,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_workbook_acceptance_events").fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_accepted_holdings").fetchone()[0],
                727,
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
