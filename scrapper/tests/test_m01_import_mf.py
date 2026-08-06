from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scrapper.cli import main
from scrapper.db.database import connect, init_db
from scrapper.pipeline.import_mf import (
    execute_import,
    import_plan_payload,
    plan_directory_import,
    plan_import,
)
from scrapper.sources.amc_workbook.detector import XLS_MAGIC, inspect_workbook


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
WORKBOOK = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets>
  <sheet name="Portfolio" sheetId="1" r:id="rId1"/>
  <sheet name="Notes" sheetId="2" state="hidden" r:id="rId2"/>
 </sheets>
</workbook>"""
WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
 <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
</Relationships>"""
EMPTY_SHEET = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>"""


def make_xlsx(
    path: Path,
    *,
    macro: bool = False,
    external: bool = False,
    zip_bomb: bool = False,
) -> Path:
    relationships = WORKBOOK_RELS
    if external:
        relationships = relationships.replace(
            "</Relationships>",
            '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLink" Target="https://example.invalid/book.xlsx" TargetMode="External"/></Relationships>',
        )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("xl/workbook.xml", WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", EMPTY_SHEET)
        archive.writestr("xl/worksheets/sheet2.xml", EMPTY_SHEET)
        if macro:
            archive.writestr("xl/vbaProject.bin", b"not executed")
        if zip_bomb:
            archive.writestr("xl/media/repetitive.bin", b"0" * 200_000)
    return path


class MutualFundM01ImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.db"
        self.snapshot_root = self.root / "snapshots" / "amc_workbook"
        self.import_root = self.root / "mf_import"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_synthetic_xlsx_is_hashed_inventoried_and_idempotent(self) -> None:
        workbook = make_xlsx(self.root / "portfolio.xlsx")
        inspection = inspect_workbook(workbook)
        self.assertEqual(inspection.workbook_type, "xlsx")
        self.assertEqual(inspection.proposed_state, "inbox")
        self.assertEqual(inspection.inventory_status, "sheet_metadata_complete")
        self.assertEqual(
            [(sheet.position, sheet.name, sheet.visibility_state) for sheet in inspection.sheets],
            [(0, "Portfolio", "visible"), (1, "Notes", "hidden")],
        )

        proposals = plan_import(
            publisher="example_amc", disclosure_month="2026-06", path=workbook,
            db_path=self.db_path, snapshot_root=self.snapshot_root,
            import_root=self.import_root,
        )
        first = execute_import(
            proposals, db_path=self.db_path,
            observed_at_utc="2026-08-05T10:00:00+00:00",
        )
        database_before_dry_run = self.db_path.read_bytes()
        second_proposals = plan_import(
            publisher="example_amc", disclosure_month="2026-07", path=workbook,
            db_path=self.db_path, snapshot_root=self.snapshot_root,
            import_root=self.import_root,
        )
        self.assertEqual(self.db_path.read_bytes(), database_before_dry_run)
        self.assertEqual(second_proposals[0].proposed_action, "duplicate_observation")
        second = execute_import(
            second_proposals, db_path=self.db_path,
            observed_at_utc="2026-08-05T10:01:00+00:00",
        )
        self.assertEqual(first["files"][0]["import_file_id"], second["files"][0]["import_file_id"])
        self.assertEqual(
            first["files"][0]["proposed_destination"],
            second["files"][0]["proposed_destination"],
        )
        self.assertTrue(second["files"][0]["duplicate"])

        destination = Path(first["files"][0]["proposed_destination"])
        self.assertEqual(destination.read_bytes(), workbook.read_bytes())
        self.assertEqual(destination.name, f"{inspection.content_sha256}.xlsx")
        conn = connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_workbook_blobs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_import_files").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_import_observations").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_import_state_events").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_workbook_sheets").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_workbook_cells").fetchone()[0], 0)
            results = [
                row[0] for row in conn.execute(
                    "SELECT observation_result FROM mf_import_observations ORDER BY import_observation_id"
                )
            ]
            self.assertEqual(results, ["inventoried", "duplicate"])
            requested_months = [
                row[0] for row in conn.execute(
                    """SELECT requested_disclosure_month
                       FROM mf_import_observations ORDER BY import_observation_id"""
                )
            ]
            self.assertEqual(requested_months, ["2026-06", "2026-07"])
        finally:
            conn.close()

    def test_invalid_and_unsafe_packages_are_quarantined_without_partial_parse(self) -> None:
        invalid = self.root / "invalid.xlsx"
        invalid.write_bytes(b"this is not a workbook")
        macro = make_xlsx(self.root / "macro.xlsx", macro=True)
        external = make_xlsx(self.root / "external.xlsx", external=True)
        zip_bomb = make_xlsx(self.root / "zip-bomb.xlsx", zip_bomb=True)
        self.assertEqual(inspect_workbook(macro).decision_code, "macro_content_forbidden")
        self.assertEqual(
            inspect_workbook(external).decision_code,
            "external_relationship_forbidden",
        )
        self.assertEqual(
            inspect_workbook(zip_bomb).decision_code,
            "zip_compression_ratio_limit",
        )

        dry_run_output = io.StringIO()
        with contextlib.redirect_stdout(dry_run_output):
            dry_run_status = main(
                [
                    "import-mf", "--publisher", "ppfas", "--month", "2026-06",
                    "--path", str(invalid), "--db", str(self.db_path), "--dry-run",
                ]
            )
        self.assertEqual(dry_run_status, 1)
        self.assertEqual(
            json.loads(dry_run_output.getvalue())["files"][0]["proposed_action"],
            "quarantine",
        )
        self.assertFalse(self.db_path.exists())

        proposals = plan_import(
            publisher="ppfas", disclosure_month="2026-06", path=invalid,
            db_path=self.db_path, snapshot_root=self.snapshot_root,
            import_root=self.import_root,
        )
        result = execute_import(
            proposals, db_path=self.db_path,
            observed_at_utc="2026-08-05T11:00:00+00:00",
        )
        row = result["files"][0]
        self.assertEqual(row["import_state"], "quarantine")
        self.assertEqual(Path(row["proposed_destination"]).read_bytes(), invalid.read_bytes())
        conn = connect(self.db_path)
        try:
            stored = conn.execute("SELECT * FROM mf_import_files").fetchone()
            self.assertEqual(stored["import_state"], "quarantine")
            self.assertEqual(stored["workbook_type"], "unknown")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_workbook_sheets").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_workbook_cells").fetchone()[0], 0)
        finally:
            conn.close()

    def test_signature_extension_size_and_legacy_xls_safety(self) -> None:
        wrong_extension = make_xlsx(self.root / "wrong.xls")
        self.assertEqual(
            inspect_workbook(wrong_extension).decision_code,
            "extension_signature_mismatch",
        )
        oversized = self.root / "large.xlsx"
        oversized.write_bytes(b"PK\x03\x04" + b"x" * 64)
        self.assertEqual(
            inspect_workbook(oversized, max_file_bytes=16).decision_code,
            "file_size_limit",
        )
        legacy = self.root / "legacy.xls"
        header = bytearray(512)
        header[:8] = XLS_MAGIC
        header[26:28] = (3).to_bytes(2, "little")
        header[28:30] = b"\xfe\xff"
        header[30:32] = (9).to_bytes(2, "little")
        legacy.write_bytes(header)
        inspection = inspect_workbook(legacy)
        self.assertEqual(inspection.workbook_type, "xls")
        self.assertEqual(inspection.proposed_state, "inbox")
        self.assertEqual(inspection.inventory_status, "deferred_legacy_binary")
        self.assertIn("macros_not_executed", inspection.safety_checks)
        self.assertEqual(inspection.sheets, ())

    def test_directory_dry_run_is_exact_sorted_and_write_free(self) -> None:
        inbox = self.root / "incoming"
        month = inbox / "example_amc" / "2026-06"
        month.mkdir(parents=True)
        make_xlsx(month / "b.xlsx")
        make_xlsx(month / "a.xlsx")
        proposals = plan_directory_import(
            path=inbox, db_path=self.db_path,
            snapshot_root=self.snapshot_root, import_root=self.import_root,
        )
        payload = import_plan_payload(proposals, dry_run=True)
        self.assertEqual([item["original_filename"] for item in payload["files"]], ["a.xlsx", "b.xlsx"])
        self.assertEqual(
            [item["proposed_action"] for item in payload["files"]],
            ["inventory", "duplicate_observation"],
        )
        self.assertFalse(self.db_path.exists())
        self.assertFalse(self.snapshot_root.exists())
        self.assertFalse(self.import_root.exists())

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_status = main(
                [
                    "import-mf-directory", "--path", str(inbox),
                    "--db", str(self.db_path), "--dry-run",
                ]
            )
        emitted = json.loads(stdout.getvalue())
        self.assertEqual(exit_status, 0)
        self.assertTrue(emitted["dry_run"])
        self.assertEqual(emitted["file_count"], 2)
        self.assertFalse(self.db_path.exists())

    def test_empty_database_has_all_m01_tables_and_pointer(self) -> None:
        conn = connect(self.db_path)
        try:
            init_db(conn)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mf_%'"
                )
            }
            foreign_key_failures = conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()
        self.assertTrue(
            {
                "mf_workbook_blobs", "mf_import_files", "mf_import_observations",
                "mf_workbook_sheets", "mf_workbook_cells", "mf_import_state_events",
                "mf_schemes", "mf_accepted_versions",
            }.issubset(tables)
        )
        self.assertEqual(foreign_key_failures, [])


if __name__ == "__main__":
    unittest.main()
