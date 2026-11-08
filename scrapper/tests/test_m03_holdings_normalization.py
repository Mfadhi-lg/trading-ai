from __future__ import annotations

from scrapper.tests.database_fixtures import prepare_database

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from scrapper.db.database import connect
from scrapper.exports.mf_workbook_json_export import build_mf_workbook_export
from scrapper.pipeline.import_mf import execute_import, plan_import
from scrapper.sources.amc_workbook.ppfas import isin_status


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "mf_workbook" / "PPFAS_Monthly_Portfolio_Report_June_30_2026.xls"
GOLDEN = ROOT / "golden" / "ppfas_2026_06_m03_expected.json"
SHEET1 = "xl/worksheets/sheet1.xml"


def mutated_workbook(source: Path, destination: Path, mutate) -> Path:
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED
    ) as outgoing:
        for info in incoming.infolist():
            payload = incoming.read(info.filename)
            if info.filename == SHEET1:
                payload = mutate(payload)
            outgoing.writestr(info.filename, payload)
    return destination


def change_cells(*, remove: tuple[str, ...] = (), inline: dict[str, str] | None = None):
    inline = inline or {}

    def mutate(payload: bytes) -> bytes:
        root = ElementTree.fromstring(payload)
        found: set[str] = set()
        for row in root.iter():
            if not row.tag.endswith("}row"):
                continue
            for cell in list(row):
                if not cell.tag.endswith("}c"):
                    continue
                coordinate = cell.attrib.get("r")
                if coordinate in remove:
                    row.remove(cell)
                    found.add(coordinate)
                elif coordinate in inline:
                    namespace = cell.tag.split("}", 1)[0] + "}"
                    cell.attrib.clear()
                    cell.attrib.update({"r": coordinate, "t": "inlineStr"})
                    for child in list(cell):
                        cell.remove(child)
                    inline_string = ElementTree.SubElement(cell, namespace + "is")
                    text = ElementTree.SubElement(inline_string, namespace + "t")
                    text.text = inline[coordinate]
                    found.add(coordinate)
        expected = set(remove) | set(inline)
        if found != expected:
            raise AssertionError(f"Fixture cells not found: {sorted(expected - found)}")
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    return mutate


class PPFASHoldingsM03Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def import_workbook(self, workbook: Path = FIXTURE, *, suffix: str = "main"):
        db_path = self.root / f"{suffix}.sqlite"
        proposals = plan_import(
            publisher="ppfas", disclosure_month="2026-06", path=workbook,
            db_path=db_path, snapshot_root=self.root / suffix / "snapshots",
            import_root=self.root / suffix / "imports",
        )
        prepare_database(db_path)
        result = execute_import(
            proposals, db_path=db_path, observed_at_utc="2026-08-05T01:00:00+00:00"
        )
        return db_path, result

    def test_official_fixture_matches_golden_normalization_and_exact_values(self) -> None:
        db_path, result = self.import_workbook()
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(result["files"][0]["normalization_summary"], expected["summary"])
        conn = connect(db_path)
        try:
            scheme_counts = {
                row["scheme_code"]: row["count"]
                for row in conn.execute(
                    """SELECT scheme_code, COUNT(*) AS count FROM mf_holdings
                       GROUP BY scheme_code ORDER BY scheme_code"""
                )
            }
            type_counts = {
                row["instrument_type_code"]: row["count"]
                for row in conn.execute(
                    """SELECT instrument_type_code, COUNT(*) AS count FROM mf_holdings
                       GROUP BY instrument_type_code ORDER BY instrument_type_code"""
                )
            }
            self.assertEqual(scheme_counts, expected["scheme_counts"])
            self.assertEqual(type_counts, expected["instrument_type_counts"])
            source_rows = conn.execute(
                """SELECT COUNT(*) FROM mf_workbook_cells AS c
                   JOIN mf_workbook_sheets AS s ON s.sheet_id=c.sheet_id
                   WHERE s.import_file_id=1
                     AND c.normalized_field_code='source_security_code'
                     AND COALESCE(c.display_value, '')!=''
                     AND c.table_position IN (0, 1)"""
            ).fetchone()[0]
            self.assertEqual(source_rows, 727)

            hdfc = conn.execute(
                """SELECT * FROM mf_holdings
                   WHERE scheme_code='PPFCF' AND source_table_kind='primary_portfolio'
                     AND source_row=7"""
            ).fetchone()
            self.assertEqual(hdfc["security_name_raw"], "HDFC Bank Limited")
            self.assertEqual(hdfc["isin_normalized"], "INE040A01034")
            self.assertEqual(hdfc["quantity_decimal"], "149727224")
            self.assertEqual(hdfc["market_value_decimal"], "1194748.38")
            self.assertEqual(hdfc["percentage_of_nav_decimal"], "8.33")
            self.assertEqual(hdfc["market_value_scale_multiplier"], "100000")
            self.assertEqual(hdfc["instrument_type_code"], "equity")

            derivative = conn.execute(
                """SELECT * FROM mf_holdings
                   WHERE scheme_code='PPFCF' AND source_table_kind='derivatives'
                     AND source_row=156"""
            ).fetchone()
            self.assertEqual(derivative["quantity_decimal"], "-5340000.00")
            self.assertEqual(derivative["market_value_decimal"], "-53928.66")
            self.assertEqual(derivative["percentage_of_aum_decimal"], "-0.3800")
            self.assertEqual(derivative["position_direction_raw"], "(Short)")
            self.assertEqual(derivative["security_match_status"], "unmatched")

            footnoted_zero = conn.execute(
                """SELECT percentage_of_nav_decimal, percentage_qualifier
                   FROM mf_holdings WHERE scheme_code='PPFCF' AND source_row=67"""
            ).fetchone()
            self.assertEqual(tuple(footnoted_zero), ("0.00", "less_than_0_01"))
            treps = conn.execute(
                """SELECT quantity_decimal, quantity_missing_kind, security_id,
                          security_match_status
                   FROM mf_holdings WHERE scheme_code='PPFCF' AND source_row=147"""
            ).fetchone()
            self.assertEqual(
                tuple(treps), (None, "blank", None, "unmatched")
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_security_resolved_holdings").fetchone()[0],
                586,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mf_stock_feature_eligible_holdings").fetchone()[0],
                586,
            )
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(DISTINCT security_id) FROM mf_holdings
                       WHERE isin_normalized='INE040A01034'"""
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM mf_holdings
                       WHERE market_value_decimal IS NOT NULL
                         AND typeof(market_value_decimal)!='text'"""
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM mf_workbook_validation_results
                       WHERE import_file_id=1 AND rule_code LIKE 'm03_%' AND passed=0"""
                ).fetchone()[0],
                0,
            )
        finally:
            conn.close()

    def test_export_keeps_source_cells_normalized_values_and_match_audit(self) -> None:
        db_path, _ = self.import_workbook(suffix="export")
        conn = connect(db_path)
        try:
            payload = build_mf_workbook_export(conn, 1)
        finally:
            conn.close()
        self.assertEqual(payload["provenance"]["workbook_manifest_version"], "0.4.0")
        self.assertEqual(len(payload["source_faithful"]["holding_row_cells"]), 7002)
        self.assertEqual(len(payload["normalized"]["holdings"]), 727)
        self.assertEqual(len(payload["quality"]["validations"]), 22)
        first = payload["normalized"]["holdings"][0]
        self.assertEqual(first["isin_normalized"], "INE040A01034")
        self.assertEqual(first["match_method"], "isin")
        self.assertEqual(first["match_rule_version"], "iso6166_luhn_v1")
        self.assertEqual(first["match_evidence"], {"basis": "structurally_valid_isin"})
        json.dumps(payload, sort_keys=True)

    def test_missing_isin_uses_only_unambiguous_versioned_name_evidence(self) -> None:
        workbook = mutated_workbook(
            FIXTURE, self.root / "missing-isin.xls", change_cells(remove=("C7",))
        )
        db_path, result = self.import_workbook(workbook, suffix="fallback")
        summary = result["files"][0]["normalization_summary"]
        self.assertEqual(summary["name_fallback_match_count"], 1)
        self.assertEqual(summary["unmatched_count"], 141)
        conn = connect(db_path)
        try:
            row = conn.execute(
                """SELECT h.security_id, h.security_match_status, d.match_method,
                          d.rule_version, d.candidate_security_ids_json
                   FROM mf_holdings AS h
                   JOIN mf_security_match_decisions AS d
                     ON d.holding_id=h.holding_id AND d.is_current=1
                   WHERE h.scheme_code='PPFCF' AND h.source_row=7"""
            ).fetchone()
            self.assertIsNotNone(row["security_id"])
            self.assertEqual(row["security_match_status"], "matched")
            self.assertEqual(row["match_method"], "exact_normalized_name")
            self.assertEqual(row["rule_version"], "exact_normalized_name_v1")
            self.assertEqual(json.loads(row["candidate_security_ids_json"]), [row["security_id"]])
        finally:
            conn.close()

    def test_ambiguous_name_requires_manual_review_and_stays_unmatched(self) -> None:
        workbook = mutated_workbook(
            FIXTURE,
            self.root / "ambiguous-name.xls",
            change_cells(remove=("C7",), inline={"B8": "HDFC Bank Limited"}),
        )
        db_path, result = self.import_workbook(workbook, suffix="ambiguous")
        self.assertEqual(result["files"][0]["normalization_summary"]["manual_review_count"], 1)
        conn = connect(db_path)
        try:
            row = conn.execute(
                """SELECT h.security_id, h.security_match_status,
                          d.candidate_security_ids_json
                   FROM mf_holdings AS h
                   JOIN mf_security_match_decisions AS d
                     ON d.holding_id=h.holding_id AND d.is_current=1
                   WHERE h.scheme_code='PPFCF' AND h.source_row=7"""
            ).fetchone()
            self.assertIsNone(row["security_id"])
            self.assertEqual(row["security_match_status"], "manual_review")
            self.assertEqual(len(json.loads(row["candidate_security_ids_json"])), 2)
            warning = conn.execute(
                """SELECT severity, passed FROM mf_workbook_validation_results
                   WHERE rule_code='m03_name_fallback_unambiguous'"""
            ).fetchone()
            self.assertEqual(tuple(warning), ("warning", 0))
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM mf_security_resolved_holdings
                       WHERE holding_id=(SELECT holding_id FROM mf_holdings
                         WHERE scheme_code='PPFCF' AND source_row=7)"""
                ).fetchone()[0],
                0,
            )
        finally:
            conn.close()

    def test_invalid_isin_is_retained_but_cannot_match_or_pass_validation(self) -> None:
        self.assertEqual(isin_status("INE040A01035"), ("INE040A01035", "invalid_checksum"))
        workbook = mutated_workbook(
            FIXTURE,
            self.root / "invalid-isin.xls",
            change_cells(inline={"C7": "INE040A01035"}),
        )
        db_path, result = self.import_workbook(workbook, suffix="invalid")
        self.assertEqual(result["files"][0]["normalization_summary"]["invalid_isin_count"], 1)
        conn = connect(db_path)
        try:
            row = conn.execute(
                """SELECT isin_raw, isin_validation_status, security_id,
                          security_match_status
                   FROM mf_holdings WHERE scheme_code='PPFCF' AND source_row=7"""
            ).fetchone()
            self.assertEqual(
                tuple(row), ("INE040A01035", "invalid_checksum", None, "invalid_isin")
            )
            validation = conn.execute(
                """SELECT severity, passed FROM mf_workbook_validation_results
                   WHERE rule_code='m03_isin_structural_validity'"""
            ).fetchone()
            self.assertEqual(tuple(validation), ("error", 0))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mf_holdings").fetchone()[0], 727)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
