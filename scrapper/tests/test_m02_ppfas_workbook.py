from __future__ import annotations

from scrapper.tests.database_fixtures import prepare_database

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from scrapper.cli import main
from scrapper.db.database import connect
from scrapper.pipeline.import_mf import execute_import, plan_import
from scrapper.sources.amc_workbook.ppfas import (
    normalize_ppfas_holdings,
    parse_ppfas_workbook,
)


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "mf_workbook" / "PPFAS_Monthly_Portfolio_Report_June_30_2026.xls"
GOLDEN = ROOT / "golden" / "ppfas_2026_06_m02_expected.json"
LEGACY_FIXTURE = (
    ROOT.parent / "store" / "mf_import" / "inbox" / "ppfas" / "2025-07"
    / "PPFAS_Monthly_Portfolio_Report_July_31_2025.xls"
)


def compact_inventory(parsed) -> dict:
    inventory = parsed.inventory_dict()
    result = {
        key: inventory[key]
        for key in (
            "manifest_version", "detected_publisher", "detected_disclosure_month",
            "workbook_format", "date_system", "sheet_count", "portfolio_sheet_count",
            "non_portfolio_sheet_count", "cell_count", "nonempty_cell_count",
            "formula_count", "merged_range_count", "table_count",
        )
    }
    result["sheets"] = [
        {
            key: sheet[key]
            for key in (
                "sheet_position", "sheet_name_raw", "classification", "scheme_code",
                "cell_count", "nonempty_cell_count", "formula_count", "merged_range_count",
            )
        }
        | {
            "tables": [
                ({
                    key: table[key]
                    for key in ("table_kind", "start_row", "end_row")
                } | {"source_total_rows": list(table["source_total_rows"])})
                for table in sheet["tables"]
            ]
        }
        for sheet in inventory["sheets"]
    ]
    return result


def mutated_workbook(source: Path, destination: Path, member: str, mutate) -> Path:
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED
    ) as outgoing:
        for info in incoming.infolist():
            payload = incoming.read(info.filename)
            if info.filename == member:
                payload = mutate(payload)
            outgoing.writestr(info.filename, payload)
    return destination


class PPFASWorkbookM02Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def plan(self, workbook: Path = FIXTURE, *, publisher: str = "ppfas", month: str = "2026-06"):
        return plan_import(
            publisher=publisher,
            disclosure_month=month,
            path=workbook,
            db_path=self.root / f"{publisher}-{month}.sqlite",
            snapshot_root=self.root / "snapshots",
            import_root=self.root / "imports",
        )

    def test_official_fixture_matches_golden_structure_and_source_displays(self) -> None:
        self.assertEqual(
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            "b0e6b22a4eb71dd9db192f7e29b3f8ea83198ee14920defb0519a1b80642d24e",
        )
        parsed = parse_ppfas_workbook(
            FIXTURE,
            requested_publisher="ppfas",
            requested_disclosure_month="2026-06",
        )
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(compact_inventory(parsed), expected)

        flexi = parsed.sheets[0]
        cells = {cell.source.coordinate: cell for cell in flexi.cells}
        self.assertEqual(cells["E7"].source.raw_value, "149727224")
        self.assertEqual(cells["E7"].source.display_value, "149,727,224")
        self.assertEqual(cells["E7"].source.number_format, "#,##0")
        self.assertEqual(cells["E7"].normalized_field_code, "quantity")
        self.assertEqual(cells["G7"].source.raw_value, "8.3299999999999999E-2")
        self.assertEqual(cells["G7"].source.display_value, "8.33%")
        self.assertEqual(cells["F150"].source.formula, "375469.9+E185-174.12")
        self.assertEqual(cells["F150"].source.display_value, "66,757.12")
        self.assertEqual(cells["F150"].cell_role, "formula_cached_value")
        self.assertIn("C340:D340", [item.reference for item in flexi.source.merged_ranges])

        large_cap = parsed.sheets[-1]
        date_cell = next(cell for cell in large_cap.cells if cell.source.coordinate == "C147")
        self.assertEqual(date_cell.source.raw_value, "46171")
        self.assertEqual(date_cell.source.display_value, "29 May 2026")

    @unittest.skipUnless(LEGACY_FIXTURE.is_file(), "official legacy PPFAS fixture absent")
    def test_official_legacy_biff_variant_preserves_index_formulas_and_holdings(self) -> None:
        parsed = parse_ppfas_workbook(
            LEGACY_FIXTURE,
            requested_publisher="ppfas",
            requested_disclosure_month="2025-07",
        )
        self.assertEqual(parsed.workbook_format, "legacy_biff8_xls")
        self.assertEqual(parsed.detected_disclosure_month, "2025-07")
        self.assertEqual(parsed.sheets[0].classification, "non_portfolio")
        self.assertEqual(parsed.sheets[0].source.name, "Index")
        self.assertEqual(len(parsed.sheets), 7)
        self.assertEqual(
            sum(cell.source.formula is not None for sheet in parsed.sheets for cell in sheet.cells),
            19,
        )
        self.assertEqual(len(normalize_ppfas_holdings(parsed)), 998)

    def test_persistence_is_complete_ordered_and_idempotent(self) -> None:
        db_path = self.root / "persist.sqlite"
        proposals = plan_import(
            publisher="ppfas", disclosure_month="2026-06", path=FIXTURE,
            db_path=db_path, snapshot_root=self.root / "snapshots",
            import_root=self.root / "imports",
        )
        self.assertEqual(proposals[0].proposed_action, "inventory")
        self.assertEqual(proposals[0].inspection.extension, ".xls")
        self.assertEqual(proposals[0].inspection.workbook_type, "xlsx")
        self.assertIn(
            "publisher_manifest_allows_ooxml_xls_extension",
            proposals[0].inspection.safety_checks,
        )
        prepare_database(db_path)
        first = execute_import(
            proposals, db_path=db_path, observed_at_utc="2026-08-05T01:00:00+00:00"
        )
        duplicate = plan_import(
            publisher="ppfas", disclosure_month="2026-06", path=FIXTURE,
            db_path=db_path, snapshot_root=self.root / "snapshots",
            import_root=self.root / "imports",
        )
        self.assertEqual(duplicate[0].proposed_action, "duplicate_observation")
        prepare_database(db_path)
        execute_import(
            duplicate, db_path=db_path, observed_at_utc="2026-08-05T01:01:00+00:00"
        )

        conn = connect(db_path)
        try:
            imported = conn.execute("SELECT * FROM mf_import_files").fetchone()
            self.assertEqual(imported["detected_publisher"], "ppfas")
            self.assertEqual(imported["detected_disclosure_month"], "2026-06")
            self.assertEqual(imported["workbook_manifest_version"], "0.4.0")
            self.assertEqual(imported["import_state"], "accepted")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_workbook_cells").fetchone()[0], 16467)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_workbook_tables").fetchone()[0], 19)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_workbook_merged_ranges").fetchone()[0], 70)
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM mf_workbook_validation_results
                       WHERE passed=1 AND rule_code NOT LIKE 'm03_%'
                         AND rule_code NOT LIKE 'm04_%'"""
                ).fetchone()[0],
                9,
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_import_observations").fetchone()[0], 2)
            source_cell = conn.execute(
                """SELECT c.* FROM mf_workbook_cells AS c
                   JOIN mf_workbook_sheets AS s ON s.sheet_id=c.sheet_id
                   WHERE s.sheet_name_raw='PPFCF' AND c.coordinate='F150'"""
            ).fetchone()
            self.assertEqual(source_cell["raw_value"], "66757.120000000054")
            self.assertEqual(source_cell["display_value"], "66,757.12")
            self.assertEqual(source_cell["formula"], "375469.9+E185-174.12")
            self.assertEqual(source_cell["number_format"], "#,##0.00;\\(#,##0.00\\)")
            positions = [
                row[0] for row in conn.execute(
                    """SELECT c.cell_position FROM mf_workbook_cells AS c
                       JOIN mf_workbook_sheets AS s ON s.sheet_id=c.sheet_id
                       WHERE s.sheet_name_raw='PPFCF' ORDER BY c.row_position, c.column_position"""
                )
            ]
            self.assertEqual(positions, list(range(2946)))
        finally:
            conn.close()
        self.assertTrue(Path(first["files"][0]["proposed_destination"]).is_file())

    def test_wrong_month_and_publisher_are_quarantined_without_cells(self) -> None:
        cases = (("ppfas", "2026-05", "disclosure_month_mismatch"),
                 ("wrong_amc", "2026-06", "publisher_mismatch"))
        for publisher, month, error_code in cases:
            with self.subTest(error_code=error_code):
                db_path = self.root / f"{error_code}.sqlite"
                proposals = plan_import(
                    publisher=publisher, disclosure_month=month, path=FIXTURE,
                    db_path=db_path, snapshot_root=self.root / error_code / "snapshots",
                    import_root=self.root / error_code / "imports",
                )
                self.assertEqual(proposals[0].proposed_action, "quarantine")
                self.assertEqual(proposals[0].structure_error.code, error_code)
                prepare_database(db_path)
                result = execute_import(proposals, db_path=db_path)
                self.assertEqual(result["files"][0]["import_state"], "quarantine")
                conn = connect(db_path)
                try:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_workbook_sheets").fetchone()[0], 0)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_workbook_cells").fetchone()[0], 0)
                    failure = conn.execute(
                        "SELECT passed, details_json FROM mf_workbook_validation_results"
                    ).fetchone()
                    self.assertEqual(failure["passed"], 0)
                    self.assertEqual(json.loads(failure["details_json"])["error_code"], error_code)
                finally:
                    conn.close()

    def test_sheet_drift_and_partial_required_columns_are_quarantined(self) -> None:
        def rename_sheet(payload: bytes) -> bytes:
            return payload.replace(b'name="PPFCF"', b'name="NOTES"', 1)

        drift = mutated_workbook(FIXTURE, self.root / "drift.xls", "xl/workbook.xml", rename_sheet)

        def remove_isin_header(payload: bytes) -> bytes:
            root = ElementTree.fromstring(payload)
            for row in root.iter():
                if row.tag.endswith("}row") and row.attrib.get("r") == "4":
                    for cell in list(row):
                        if cell.tag.endswith("}c") and cell.attrib.get("r") == "C4":
                            row.remove(cell)
                            return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            raise AssertionError("Fixture C4 cell was not found")

        partial = mutated_workbook(
            FIXTURE, self.root / "partial.xls", "xl/worksheets/sheet1.xml", remove_isin_header
        )
        for workbook, error_code in (
            (drift, "sheet_inventory_drift"),
            (partial, "required_column_missing"),
        ):
            with self.subTest(error_code=error_code):
                db_path = self.root / f"{error_code}.sqlite"
                proposals = plan_import(
                    publisher="ppfas", disclosure_month="2026-06", path=workbook,
                    db_path=db_path, snapshot_root=self.root / error_code / "snapshots",
                    import_root=self.root / error_code / "imports",
                )
                self.assertEqual(proposals[0].structure_error.code, error_code)
                prepare_database(db_path)
                execute_import(proposals, db_path=db_path)
                conn = connect(db_path)
                try:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_workbook_cells").fetchone()[0], 0)
                    self.assertEqual(conn.execute("SELECT import_state FROM mf_import_files").fetchone()[0], "quarantine")
                finally:
                    conn.close()

    def test_cli_dry_run_reports_structure_and_remains_write_free(self) -> None:
        db_path = self.root / "dry-run.sqlite"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = main(
                [
                    "import-mf", "--publisher", "ppfas", "--month", "2026-06",
                    "--path", str(FIXTURE), "--db", str(db_path), "--dry-run",
                ]
            )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["files"][0]["structure_inventory"]["cell_count"], 16467)
        self.assertEqual(payload["files"][0]["structure_inventory"]["table_count"], 19)
        self.assertFalse(db_path.exists())


if __name__ == "__main__":
    unittest.main()
