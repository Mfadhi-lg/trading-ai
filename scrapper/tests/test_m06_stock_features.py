from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scrapper.db.database import connect, init_db
from scrapper.db.mf_change_repo import generate_scheme_month_changes
from scrapper.db.mf_feature_repo import generate_stock_month_features
from scrapper.exports.mf_feature_json_export import build_mf_feature_export
from scrapper.exports.mf_workbook_json_export import build_mf_workbook_export


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "mf_workbook" / "m06_multischeme_features.json"
GOLDEN = ROOT / "golden" / "ppfas_m06_features_expected.json"


class MFStockMonthlyFeaturesM06Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "scraper.sqlite")
        init_db(self.conn)
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.scheme_ids: dict[str, int] = {}
        self.security_ids: dict[str, int] = {}

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def _ensure_scheme(self, scheme: dict) -> int:
        code = scheme["code"]
        if code not in self.scheme_ids:
            cursor = self.conn.execute(
                """INSERT INTO mf_schemes(publisher, scheme_code, current_name)
                   VALUES (?, ?, ?)""",
                (self.fixture["publisher"], code, f"M06 {code} Scheme"),
            )
            self.scheme_ids[code] = int(cursor.lastrowid)
        return self.scheme_ids[code]

    def _ensure_security(self, case: str, import_id: int) -> int:
        if case in self.security_ids:
            return self.security_ids[case]
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
               VALUES (?, 'isin', ?, 1, ?, '2026-05',
                       '2026-08-05T00:00:00+00:00')""",
            (security_id, self.fixture["securities"][case], import_id),
        )
        self.security_ids[case] = security_id
        return security_id

    def _seed_import(
        self, month: str, *, scheme_codes: set[str] | None = None,
        suffix: str = "", alpha_active_a_current: tuple[str, str] | None = None,
    ) -> int:
        marker = f"m06-{month}-{suffix or 'original'}"
        content_hash = hashlib.sha256(marker.encode()).hexdigest()
        blob = self.conn.execute(
            """INSERT INTO mf_workbook_blobs
               (content_sha256, workbook_type, byte_size, local_path,
                first_observed_at_utc, created_at_utc, safety_status,
                safety_report_json)
               VALUES (?, 'xlsx', 1, ?, '2026-08-05T00:00:00+00:00',
                       '2026-08-05T00:00:00+00:00', 'passed', '{}')""",
            (content_hash, f"/{marker}.xlsx"),
        )
        imported = self.conn.execute(
            """INSERT INTO mf_import_files
               (publisher, requested_disclosure_month,
                detected_disclosure_month, original_filename, submitted_path,
                content_sha256, workbook_blob_id, workbook_type, byte_size,
                import_state, first_observed_at_utc, accepted_at_utc,
                state_reason_json, detected_publisher,
                workbook_manifest_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'xlsx', 1, 'accepted',
                       '2026-08-05T00:00:00+00:00',
                       '2026-08-05T00:00:00+00:00', '{}', ?, 'm06_fixture')""",
            (
                self.fixture["publisher"], month, month, f"{marker}.xlsx",
                f"/{marker}.xlsx", content_hash, int(blob.lastrowid),
                self.fixture["publisher"],
            ),
        )
        import_id = int(imported.lastrowid)
        selected = [
            scheme for scheme in self.fixture["schemes"]
            if scheme_codes is None or scheme["code"] in scheme_codes
        ]
        for position, scheme in enumerate(selected):
            scheme_id = self._ensure_scheme(scheme)
            self.conn.execute(
                """INSERT OR IGNORE INTO mf_scheme_classifications
                   (scheme_id, scheme_management_type, is_equity_scheme,
                    scheme_category, valid_from_month, valid_to_month,
                    classification_source, classification_rule_version,
                    first_seen_import_file_id, created_at_utc)
                   VALUES (?, ?, ?, ?, '2025-01', NULL, 'm06_fixture',
                           'm06_fixture_v1', ?, '2026-08-05T00:00:00+00:00')""",
                (
                    scheme_id, scheme["management_type"], int(scheme["is_equity"]),
                    f"test_{scheme['management_type']}", import_id,
                ),
            )
            sheet = self.conn.execute(
                """INSERT INTO mf_workbook_sheets
                   (import_file_id, sheet_position, sheet_name_raw,
                    visibility_state, detection_evidence_json, parse_status,
                    detected_scheme_code, detected_scheme_name_raw,
                    sheet_classification, manifest_version)
                   VALUES (?, ?, ?, 'visible', '{}', 'ok', ?, ?,
                           'portfolio', 'm06_fixture')""",
                (
                    import_id, position, scheme["code"], scheme["code"],
                    f"M06 {scheme['code']} Scheme",
                ),
            )
            source_row = 0
            for case, periods in scheme["holdings"].items():
                side = "previous" if month == self.fixture["previous_month"] else "current"
                values = periods[side]
                if (
                    case == "alpha" and scheme["code"] == "ACTIVE_A"
                    and side == "current" and alpha_active_a_current is not None
                ):
                    values = list(alpha_active_a_current)
                if values is None:
                    continue
                source_row += 1
                security_id = self._ensure_security(case, import_id)
                isin = self.fixture["securities"][case]
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
                               'amc_workbook', 'official', ?, ?, ?, ?, ?, ?,
                               'valid', ?, '1', ?, 'net_assets', 'Equity',
                               'equity', 'INR', 'COUNT', 'INR_LAKH', '100000',
                               'PERCENT_POINT', 'm06_fixture_v1',
                               '2026-08-05T00:00:00+00:00',
                               '2026-08-05T00:00:00+00:00', ?, 'matched', ?, ?)""",
                    (
                        import_id, int(sheet.lastrowid), source_row,
                        self.fixture["publisher"], f"M06 {scheme['code']} Scheme",
                        scheme["code"], month, case, isin, isin,
                        values[0], values[1], security_id, scheme_id,
                        f"test_{scheme['management_type']}",
                    ),
                )
                self.conn.execute(
                    """INSERT INTO mf_security_match_decisions
                       (holding_id, decision_version, is_current, security_id,
                        match_method, match_status, match_key, rule_version,
                        candidate_security_ids_json, evidence_json,
                        decided_at_utc, decided_by)
                       VALUES (?, 1, 1, ?, 'isin', 'matched', ?,
                               'm06_fixture_v1', '[]', '{}',
                               '2026-08-05T00:00:00+00:00', 'm06_fixture')""",
                    (int(holding.lastrowid), security_id, isin),
                )
            self.conn.execute(
                """INSERT INTO mf_accepted_versions
                   (publisher, scheme_id, disclosure_month, import_file_id,
                    accepted_at_utc, accepted_by, acceptance_mode)
                   VALUES (?, ?, ?, ?, '2026-08-05T00:00:00+00:00',
                           'm06_fixture', 'fixture')
                   ON CONFLICT(publisher, scheme_id, disclosure_month) DO UPDATE SET
                     import_file_id=excluded.import_file_id,
                     accepted_at_utc=excluded.accepted_at_utc""",
                (self.fixture["publisher"], scheme_id, month, import_id),
            )
        self.conn.execute(
            """INSERT INTO mf_workbook_acceptance_events
               (import_file_id, decided_at_utc, decided_by, acceptance_mode,
                validation_status, scheme_count,
                previous_import_file_ids_json, decision_json)
               VALUES (?, '2026-08-05T00:00:00+00:00', 'm06_fixture',
                       'fixture', 'passed', ?, '[]', '{}')""",
            (import_id, len(selected)),
        )
        self.conn.commit()
        return import_id

    def _derive_pair(self) -> tuple[int, int]:
        previous_import = self._seed_import(self.fixture["previous_month"])
        current_import = self._seed_import(self.fixture["current_month"])
        changes = generate_scheme_month_changes(
            self.conn, publisher=self.fixture["publisher"],
            current_month=self.fixture["current_month"],
            generated_at_utc="2026-08-05T01:00:00+00:00",
        )
        self.assertEqual(changes.generated_change_sets, 7)
        self.assertEqual(changes.change_count, 9)
        return previous_import, current_import

    def test_exact_scopes_counts_decimal_values_and_lineage_match_golden(self) -> None:
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        previous_import, current_import = self._derive_pair()
        result = generate_stock_month_features(
            self.conn, publisher=self.fixture["publisher"],
            current_month=self.fixture["current_month"],
            generated_at_utc="2026-08-05T01:01:00+00:00",
        )
        self.assertEqual(result.aggregation_status, "generated")
        self.assertEqual(result.all_scheme_feature_count, 2)
        self.assertEqual(result.active_equity_feature_count, 1)
        self.assertEqual(result.lineage_count, expected["lineage_count"])
        rows = self.conn.execute(
            """SELECT * FROM mf_current_stock_monthly_features
               ORDER BY feature_scope, isin_normalized"""
        ).fetchall()
        self.assertEqual(len(rows), 3)
        case_by_isin = {value: key for key, value in self.fixture["securities"].items()}
        for row in rows:
            key = f"{row['feature_scope']}:{case_by_isin[row['isin_normalized']]}"
            self.assertEqual(
                {field: row[field] for field in expected["features"][key]},
                expected["features"][key],
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM mf_scheme_security_month_changes"
            ).fetchone()[0],
            9,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM mf_active_equity_stock_monthly_features"
            ).fetchone()[0],
            1,
        )
        validations = {
            row["rule_code"]: row["validation_status"]
            for row in self.conn.execute(
                """SELECT * FROM mf_stock_month_feature_validations
                   WHERE feature_set_id=?""", (result.feature_set_id,)
            )
        }
        self.assertEqual(set(validations.values()), {"passed"})

        # A rerun reuses the exact input fingerprint.
        rerun = generate_stock_month_features(
            self.conn, publisher=self.fixture["publisher"],
            current_month=self.fixture["current_month"],
            generated_at_utc="2026-08-05T01:02:00+00:00",
        )
        self.assertEqual(rerun.feature_set_id, result.feature_set_id)
        active_export = build_mf_feature_export(
            self.conn, scope="active_equity", through_month="2026-06",
            publisher="ppfas",
        )
        self.assertEqual(active_export["schema_version"], "1.0.0")
        self.assertEqual(len(active_export["features"]), 1)
        self.assertEqual(len(active_export["features"][0]["lineage"]), 2)
        self.assertEqual(
            active_export["features"][0]["number_of_funds_increasing"],
            active_export["features"][0]["funds_increasing_quantity"],
        )
        workbook_ids = {
            item["import_file_id"]
            for item in active_export["provenance"]["official_workbooks"]
        }
        self.assertEqual(workbook_ids, {previous_import, current_import})
        workbook_export = build_mf_workbook_export(self.conn, current_import)
        self.assertEqual(workbook_export["schema_version"], "1.3.0")
        self.assertEqual(
            len(workbook_export["normalized"]["stock_monthly_features"]), 3
        )

    def test_incomplete_scheme_pair_is_audited_without_partial_features(self) -> None:
        all_codes = {scheme["code"] for scheme in self.fixture["schemes"]}
        self._seed_import(
            self.fixture["previous_month"], scheme_codes=all_codes - {"UNKNOWN"}
        )
        self._seed_import(self.fixture["current_month"])
        changes = generate_scheme_month_changes(
            self.conn, publisher="ppfas", current_month="2026-06",
            generated_at_utc="2026-08-05T02:00:00+00:00",
        )
        self.assertEqual(changes.generated_change_sets, 6)
        self.assertEqual(changes.skipped_change_sets, 1)
        features = generate_stock_month_features(
            self.conn, publisher="ppfas", current_month="2026-06",
            generated_at_utc="2026-08-05T02:01:00+00:00",
        )
        self.assertEqual(features.aggregation_status, "skipped")
        self.assertEqual(features.all_scheme_feature_count, 0)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM mf_stock_monthly_features"
            ).fetchone()[0],
            0,
        )
        statuses = {
            row["rule_code"]: row["validation_status"]
            for row in self.conn.execute(
                """SELECT * FROM mf_stock_month_feature_validations
                   WHERE feature_set_id=?""", (features.feature_set_id,)
            )
        }
        self.assertEqual(statuses["m06_complete_current_change_sets"], "failed")
        self.assertEqual(
            statuses["m06_exact_feature_reconciliation"], "not_evaluated"
        )

    def test_superseded_workbook_preserves_history_and_moves_current_view(self) -> None:
        self._derive_pair()
        first = generate_stock_month_features(
            self.conn, publisher="ppfas", current_month="2026-06",
            generated_at_utc="2026-08-05T03:00:00+00:00",
        )
        revised_import = self._seed_import(
            "2026-06", suffix="revised", alpha_active_a_current=("20", "2.00")
        )
        generate_scheme_month_changes(
            self.conn, publisher="ppfas", current_month="2026-06",
            generated_at_utc="2026-08-05T03:01:00+00:00",
        )
        second = generate_stock_month_features(
            self.conn, publisher="ppfas", current_month="2026-06",
            generated_at_utc="2026-08-05T03:02:00+00:00",
        )
        self.assertNotEqual(first.feature_set_id, second.feature_set_id)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM mf_stock_monthly_features"
            ).fetchone()[0],
            6,
        )
        current_set_ids = {
            int(row[0]) for row in self.conn.execute(
                "SELECT DISTINCT feature_set_id FROM mf_current_stock_monthly_features"
            )
        }
        self.assertEqual(current_set_ids, {second.feature_set_id})
        current_import_ids = {
            int(row[0]) for row in self.conn.execute(
                """SELECT DISTINCT current_import_file_id
                   FROM mf_stock_month_feature_inputs WHERE feature_set_id=?""",
                (second.feature_set_id,),
            )
        }
        self.assertEqual(current_import_ids, {revised_import})


if __name__ == "__main__":
    unittest.main()
