from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scrapper.db.database import connect, init_db
from scrapper.db.mf_change_repo import generate_scheme_month_changes
from scrapper.exports.mf_workbook_json_export import build_mf_workbook_export


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "mf_workbook" / "m05_two_month_changes.json"
GOLDEN = ROOT / "golden" / "ppfas_m05_changes_expected.json"


class MFConsecutiveMonthChangesM05Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "scraper.sqlite"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.scheme_id = self._seed_scheme()
        self.security_cases: dict[str, int] = {}

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def _seed_scheme(self) -> int:
        cursor = self.conn.execute(
            """INSERT INTO mf_schemes(publisher, scheme_code, current_name)
               VALUES (?, ?, 'M05 Test Active Equity Scheme')""",
            (self.fixture["publisher"], self.fixture["scheme_code"]),
        )
        scheme_id = int(cursor.lastrowid)
        return scheme_id

    def _ensure_security(self, case: str, isin: str) -> int:
        found = self.conn.execute(
            """SELECT security_id FROM mf_security_identifiers
               WHERE identifier_type='isin' AND identifier_value=?""",
            (isin,),
        ).fetchone()
        if found is not None:
            security_id = int(found["security_id"])
        else:
            cursor = self.conn.execute(
                """INSERT INTO mf_securities(canonical_name, created_at_utc)
                   VALUES (?, '2026-08-05T00:00:00+00:00')""",
                (case,),
            )
            security_id = int(cursor.lastrowid)
            self.conn.execute(
                """INSERT INTO mf_security_identifiers
                   (security_id, identifier_type, identifier_value,
                    structurally_valid, first_seen_import_file_id,
                    first_seen_disclosure_month, created_at_utc)
                   VALUES (?, 'isin', ?, 1, 1, '2026-05',
                           '2026-08-05T00:00:00+00:00')""",
                (security_id, isin),
            )
        self.security_cases[case] = security_id
        return security_id

    def _seed_import(
        self,
        month: str,
        state: str,
        holdings: list[dict] | None = None,
        *,
        suffix: str = "",
    ) -> int:
        marker = f"{month}-{state}-{suffix}"
        blob = self.conn.execute(
            """INSERT INTO mf_workbook_blobs
               (content_sha256, workbook_type, byte_size, local_path,
                first_observed_at_utc, created_at_utc, safety_status,
                safety_report_json)
               VALUES (?, 'xlsx', 1, ?, '2026-08-05T00:00:00+00:00',
                       '2026-08-05T00:00:00+00:00', 'passed', '{}')""",
            ((marker.encode().hex() + "0" * 64)[:64], f"/{marker}.xlsx"),
        )
        imported = self.conn.execute(
            """INSERT INTO mf_import_files
               (publisher, requested_disclosure_month,
                detected_disclosure_month, original_filename, submitted_path,
                content_sha256, workbook_blob_id, workbook_type, byte_size,
                import_state, first_observed_at_utc, accepted_at_utc,
                state_reason_json, detected_publisher,
                workbook_manifest_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'xlsx', 1, ?,
                       '2026-08-05T00:00:00+00:00', ?, '{}', ?, 'm05_fixture')""",
            (
                self.fixture["publisher"], month, month, f"{marker}.xlsx",
                f"/{marker}.xlsx", (marker.encode().hex() + "0" * 64)[:64],
                int(blob.lastrowid), state,
                "2026-08-05T00:00:00+00:00" if state == "accepted" else None,
                self.fixture["publisher"],
            ),
        )
        import_id = int(imported.lastrowid)
        self.conn.execute(
            """INSERT OR IGNORE INTO mf_scheme_classifications
               (scheme_id, scheme_management_type, is_equity_scheme,
                scheme_category, valid_from_month, valid_to_month,
                classification_source, classification_rule_version,
                first_seen_import_file_id, created_at_utc)
               VALUES (?, 'active', 1, 'test_active_equity', '2025-01', NULL,
                       'm05_fixture', 'm05_fixture_v1', ?,
                       '2026-08-05T00:00:00+00:00')""",
            (self.scheme_id, import_id),
        )
        sheet = self.conn.execute(
            """INSERT INTO mf_workbook_sheets
               (import_file_id, sheet_position, sheet_name_raw,
                visibility_state, detection_evidence_json, parse_status,
                detected_scheme_code, detected_scheme_name_raw,
                sheet_classification, manifest_version)
               VALUES (?, 0, 'M05TEST', 'visible', '{}', 'ok', ?,
                       'M05 Test Active Equity Scheme', 'portfolio', 'm05_fixture')""",
            (import_id, self.fixture["scheme_code"]),
        )
        if holdings:
            for source_row, item in enumerate(holdings, start=1):
                security_id = self._ensure_security(item["case"], item["isin"])
                holding = self.conn.execute(
                    """INSERT INTO mf_holdings
                       (import_file_id, sheet_id, source_table_position,
                        source_table_kind, source_row, publisher, source,
                        source_quality, scheme_name_raw, scheme_code,
                        disclosure_month, security_name_raw, isin_raw,
                        isin_normalized, isin_validation_status,
                        quantity_decimal, market_value_decimal,
                        percentage_of_nav_decimal, percentage_basis,
                        instrument_type_raw, instrument_type_code, currency,
                        quantity_unit_code, market_value_unit_code,
                        market_value_scale_multiplier, percentage_unit_code,
                        normalization_rule_version, first_observed_at_utc,
                        accepted_at_utc, security_id, security_match_status,
                        scheme_id, scheme_category)
                       VALUES (?, ?, 0, 'primary_portfolio', ?, ?,
                               'amc_workbook', 'official',
                               'M05 Test Active Equity Scheme', ?, ?, ?, ?, ?,
                               'valid', ?, '1', ?, 'net_assets', 'Equity',
                               'equity', 'INR', 'COUNT', 'INR_LAKH', '100000',
                               'PERCENT_POINT', 'm05_fixture_v1',
                               '2026-08-05T00:00:00+00:00', ?, ?, 'matched',
                               ?, 'test_active_equity')""",
                    (
                        import_id, int(sheet.lastrowid), source_row,
                        self.fixture["publisher"], self.fixture["scheme_code"],
                        month, item["case"], item["isin"], item["isin"],
                        item["quantity"], item["weight"],
                        "2026-08-05T00:00:00+00:00"
                        if state == "accepted" else None,
                        security_id, self.scheme_id,
                    ),
                )
                self.conn.execute(
                    """INSERT INTO mf_security_match_decisions
                       (holding_id, decision_version, is_current, security_id,
                        match_method, match_status, match_key, rule_version,
                        candidate_security_ids_json, evidence_json,
                        decided_at_utc, decided_by)
                       VALUES (?, 1, 1, ?, 'isin', 'matched', ?,
                               'm05_fixture_v1', '[]', '{}',
                               '2026-08-05T00:00:00+00:00', 'm05_fixture')""",
                    (int(holding.lastrowid), security_id, item["isin"]),
                )
        if state == "accepted":
            self.conn.execute(
                """INSERT INTO mf_workbook_acceptance_events
                   (import_file_id, decided_at_utc, decided_by, acceptance_mode,
                    validation_status, scheme_count,
                    previous_import_file_ids_json, decision_json)
                   VALUES (?, '2026-08-05T00:00:00+00:00', 'm05_fixture',
                           'fixture', 'passed', 1, '[]', '{}')""",
                (import_id,),
            )
            self.conn.execute(
                """INSERT INTO mf_accepted_versions
                   (publisher, scheme_id, disclosure_month, import_file_id,
                    accepted_at_utc, accepted_by, acceptance_mode)
                   VALUES (?, ?, ?, ?, '2026-08-05T00:00:00+00:00',
                           'm05_fixture', 'fixture')
                   ON CONFLICT(publisher, scheme_id, disclosure_month) DO UPDATE SET
                     import_file_id=excluded.import_file_id,
                     accepted_at_utc=excluded.accepted_at_utc,
                     accepted_by=excluded.accepted_by,
                     acceptance_mode=excluded.acceptance_mode""",
                (self.fixture["publisher"], self.scheme_id, month, import_id),
            )
        self.conn.commit()
        return import_id

    def _seed_complete_pair(self) -> tuple[int, int]:
        previous = [
            {"case": row["case"], "isin": row["isin"], **row["previous"]}
            for row in self.fixture["securities"]
            if row["previous"] is not None
        ]
        current = [
            {"case": row["case"], "isin": row["isin"], **row["current"]}
            for row in self.fixture["securities"]
            if row["current"] is not None
        ]
        return (
            self._seed_import(self.fixture["previous_month"], "accepted", previous),
            self._seed_import(self.fixture["current_month"], "accepted", current),
        )

    def test_all_change_classes_exact_values_presence_and_export_match_golden(self) -> None:
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        previous_import, current_import = self._seed_complete_pair()
        result = generate_scheme_month_changes(
            self.conn, publisher=self.fixture["publisher"],
            current_month=self.fixture["current_month"],
            generated_at_utc="2026-08-05T01:00:00+00:00",
        )
        self.assertEqual(result.change_count, expected["change_count"])
        self.assertEqual(result.change_class_counts, expected["change_class_counts"])
        rows = self.conn.execute(
            """SELECT changes.* FROM mf_current_scheme_security_month_changes AS changes
               ORDER BY changes.isin_normalized"""
        ).fetchall()
        self.assertEqual(len(rows), expected["change_count"])
        for row in rows:
            case = next(
                key for key, security_id in self.security_cases.items()
                if security_id == row["security_id"]
            )
            projection = {
                key: row[key]
                for key in expected["cases"][case]
            }
            self.assertEqual(projection, expected["cases"][case])
            self.assertEqual(row["previous_import_file_id"], previous_import)
            self.assertEqual(row["current_import_file_id"], current_import)
            evidence = json.loads(row["completeness_evidence_json"])
            self.assertTrue(evidence["months_consecutive"])
            self.assertTrue(evidence["absent_row_as_zero_authorized"])
            self.assertTrue(
                evidence["previous"]["complete_accepted_disclosure"]
            )
            self.assertTrue(
                evidence["current"]["complete_accepted_disclosure"]
            )

        # Idempotence reuses the content/pointer fingerprint and rows.
        generate_scheme_month_changes(
            self.conn, publisher=self.fixture["publisher"],
            current_month=self.fixture["current_month"],
            generated_at_utc="2026-08-05T01:01:00+00:00",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM mf_scheme_month_change_sets"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM mf_scheme_security_month_changes"
            ).fetchone()[0],
            7,
        )
        exported = build_mf_workbook_export(self.conn, current_import)
        self.assertEqual(exported["schema_version"], "1.3.0")
        self.assertEqual(
            len(exported["normalized"]["scheme_security_month_changes"]), 7
        )
        self.assertEqual(
            exported["quality"]["month_change_sets"][0]["comparison_status"],
            "generated",
        )
        self.assertEqual(
            {
                item["rule_code"]: item["validation_status"]
                for item in exported["quality"]["month_change_sets"][0][
                    "validations"
                ]
            },
            {
                "m05_comparable_holding_rows": "passed",
                "m05_complete_accepted_disclosures": "passed",
                "m05_consecutive_calendar_months": "passed",
                "m05_exact_change_rows_complete": "passed",
            },
        )

    def test_missing_rejected_or_quarantined_middle_month_never_implies_trade(self) -> None:
        for middle_state in (None, "rejected", "quarantine"):
            with self.subTest(middle_state=middle_state or "missing"):
                self.conn.close()
                self.db_path = (
                    Path(self.temp.name)
                    / f"scraper-{middle_state or 'missing'}.sqlite"
                )
                self.conn = connect(self.db_path)
                init_db(self.conn)
                self.scheme_id = self._seed_scheme()
                self.security_cases = {}
                prior = [{
                    "case": "prior_only", "isin": "IN002026X107",
                    "quantity": "10", "weight": "1.00",
                }]
                later = [{
                    "case": "later_only", "isin": "IN002026X115",
                    "quantity": "12", "weight": "1.20",
                }]
                self._seed_import("2026-05", "accepted", prior)
                if middle_state is not None:
                    self._seed_import("2026-06", middle_state)
                self._seed_import("2026-07", "accepted", later)

                june = generate_scheme_month_changes(
                    self.conn, publisher=self.fixture["publisher"],
                    current_month="2026-06",
                    generated_at_utc="2026-08-05T02:00:00+00:00",
                )
                july = generate_scheme_month_changes(
                    self.conn, publisher=self.fixture["publisher"],
                    current_month="2026-07",
                    generated_at_utc="2026-08-05T02:01:00+00:00",
                )
                self.assertEqual(june.generated_change_sets, 0)
                self.assertEqual(july.generated_change_sets, 0)
                self.assertEqual(june.change_count + july.change_count, 0)
                self.assertEqual(
                    self.conn.execute(
                        "SELECT COUNT(*) FROM mf_scheme_security_month_changes"
                    ).fetchone()[0],
                    0,
                )
                july_set = self.conn.execute(
                    """SELECT * FROM mf_scheme_month_change_sets
                       WHERE current_month='2026-07'"""
                ).fetchone()
                self.assertEqual(
                    july_set["reason_code"],
                    "previous_accepted_disclosure_missing",
                )
                evidence = json.loads(july_set["completeness_evidence_json"])
                self.assertFalse(evidence["absent_row_as_zero_authorized"])
                states = {
                    item["import_state"]
                    for item in evidence["previous"]["candidate_imports"]
                }
                self.assertEqual(states, {middle_state} if middle_state else set())
                validations = {
                    row["rule_code"]: row["validation_status"]
                    for row in self.conn.execute(
                        """SELECT rule_code, validation_status
                           FROM mf_scheme_month_change_validations
                           WHERE change_set_id=?""",
                        (july_set["change_set_id"],),
                    )
                }
                self.assertEqual(
                    validations["m05_complete_accepted_disclosures"], "failed"
                )
                self.assertEqual(
                    validations["m05_exact_change_rows_complete"], "not_evaluated"
                )

    def test_superseded_pointer_keeps_history_but_current_view_uses_new_pair(self) -> None:
        self._seed_complete_pair()
        generate_scheme_month_changes(
            self.conn, publisher=self.fixture["publisher"],
            current_month="2026-06",
            generated_at_utc="2026-08-05T03:00:00+00:00",
        )
        revised = [{
            "case": "quantity_increase", "isin": "IN002025Z419",
            "quantity": "20", "weight": "3.00",
        }]
        revised_import = self._seed_import(
            "2026-06", "accepted", revised, suffix="revised"
        )
        generate_scheme_month_changes(
            self.conn, publisher=self.fixture["publisher"],
            current_month="2026-06",
            generated_at_utc="2026-08-05T03:01:00+00:00",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM mf_scheme_month_change_sets"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM mf_current_scheme_month_change_sets"
            ).fetchone()[0],
            1,
        )
        current_import_ids = {
            row[0]
            for row in self.conn.execute(
                """SELECT DISTINCT current_import_file_id
                   FROM mf_current_scheme_security_month_changes"""
            )
        }
        self.assertEqual(current_import_ids, {revised_import})

    def test_same_security_rows_are_exactly_aggregated_within_scheme_month(self) -> None:
        isin = "IN002025Z419"
        self._seed_import(
            "2026-05",
            "accepted",
            [
                {"case": "split_position", "isin": isin, "quantity": "10", "weight": "1.0"},
                {"case": "split_position", "isin": isin, "quantity": "5", "weight": "0.5"},
            ],
        )
        self._seed_import(
            "2026-06",
            "accepted",
            [
                {"case": "split_position", "isin": isin, "quantity": "12", "weight": "1.2"},
                {"case": "split_position", "isin": isin, "quantity": "6", "weight": "0.6"},
            ],
        )
        summary = generate_scheme_month_changes(
            self.conn,
            publisher=self.fixture["publisher"],
            current_month="2026-06",
            generated_at_utc="2026-08-05T04:00:00+00:00",
        )
        self.assertEqual(summary.generated_change_sets, 1)
        self.assertEqual(summary.skipped_change_sets, 0)
        change = self.conn.execute(
            "SELECT * FROM mf_current_scheme_security_month_changes"
        ).fetchone()
        self.assertEqual(change["previous_quantity_decimal"], "15")
        self.assertEqual(change["current_quantity_decimal"], "18")
        self.assertEqual(change["quantity_change_decimal"], "3")
        self.assertEqual(change["previous_weight_decimal"], "1.5")
        self.assertEqual(change["current_weight_decimal"], "1.8")
        self.assertEqual(change["portfolio_weight_change_decimal"], "0.3")


if __name__ == "__main__":
    unittest.main()
