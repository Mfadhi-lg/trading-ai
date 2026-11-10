"""Native application parity for official workbooks and exact MF derivations."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile

from scrapper.db import mfdata_repo
from scrapper.db.mutual_funds import changes, features, holdings, scheme_acceptance, workbook_inventory
from scrapper.db.postgresql_transfer import file_sha256, transfer
from scrapper.sources.amc_workbook.detector import inspect_workbook
from scrapper.sources.amc_workbook.ppfas import (
    PPFASStructureError, normalize_ppfas_holdings, parse_ppfas_workbook,
)
from scrapper.sources.mfdata.adapter import parse_mfdata_holdings
from scrapper.tests import test_m06_stock_features as m06
from scrapper.tests.postgresql_fixtures import DisposablePostgreSQLTestCase
from scrapper.tests.test_postgresql_schema import synthetic_sqlite

ROOT = Path(__file__).parent
WORKBOOK = ROOT / 'fixtures/mf_workbook/PPFAS_Monthly_Portfolio_Report_June_30_2026.xls'
NOW = '2026-11-09T00:00:00+00:00'


class MutualFundRepositoryTests(DisposablePostgreSQLTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = synthetic_sqlite()
        self.source.row_factory = sqlite3.Row
        self.addCleanup(self.source.close)

    def records(self, conn, table):
        return sorted((dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')),
                      key=lambda row: json.dumps(row, sort_keys=True))

    def import_workbook(self, conn):
        inspection = inspect_workbook(WORKBOOK, allow_ooxml_xls_extension=True)
        parsed = parse_ppfas_workbook(WORKBOOK, requested_publisher='ppfas',
                                     requested_disclosure_month='2026-06')
        inventory = workbook_inventory.persist_inventory(conn, inspection, publisher='ppfas',
            disclosure_month='2026-06', immutable_path=str(WORKBOOK), observed_at_utc=NOW)
        workbook_inventory.persist_ppfas_structure(conn, import_file_id=inventory.import_file_id, parsed=parsed)
        normalized = holdings.persist_ppfas_holdings(conn, import_file_id=inventory.import_file_id,
                                                     holdings=normalize_ppfas_holdings(parsed))
        finalized = scheme_acceptance.finalize_ppfas_import(conn, import_file_id=inventory.import_file_id,
                                                            parsed=parsed, decided_at_utc=NOW)
        return inventory, normalized, finalized

    def test_official_workbook_cells_normalization_and_acceptance_match_sqlite(self):
        expected = self.import_workbook(self.source)
        with self.session() as conn:
            actual = self.import_workbook(conn)
            self.assertEqual(actual, expected)
            self.assertEqual(actual[-1].import_state, 'accepted')
            for table in ('mf_workbook_blobs', 'mf_import_files', 'mf_import_observations',
                          'mf_workbook_sheets', 'mf_workbook_tables', 'mf_workbook_cells',
                          'mf_workbook_validation_results', 'mf_securities', 'mf_security_identifiers',
                          'mf_security_name_evidence', 'mf_holdings', 'mf_security_match_decisions',
                          'mf_schemes', 'mf_scheme_name_versions', 'mf_scheme_classifications',
                          'mf_accepted_versions', 'mf_accepted_version_events', 'mf_workbook_acceptance_events'):
                with self.subTest(table=table):
                    self.assertEqual(self.records(conn, table), self.records(self.source, table))
            inspection = inspect_workbook(WORKBOOK, allow_ooxml_xls_extension=True)
            duplicate = workbook_inventory.persist_inventory(conn, inspection, publisher='ppfas',
                disclosure_month='2026-06', immutable_path=str(WORKBOOK), observed_at_utc=NOW)
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(duplicate.import_file_id, actual[0].import_file_id)

    def test_structure_failure_updates_existing_validation_identity(self):
        inspection = inspect_workbook(WORKBOOK, allow_ooxml_xls_extension=True)
        with self.session() as conn:
            imported = workbook_inventory.persist_inventory(conn, inspection, publisher='ppfas',
                disclosure_month='2026-06', immutable_path=str(WORKBOOK), observed_at_utc=NOW)
            for message in ('first controlled failure', 'revised controlled failure'):
                workbook_inventory.persist_ppfas_structure_failure(conn, import_file_id=imported.import_file_id,
                    error=PPFASStructureError('synthetic', message))
                row = conn.execute("SELECT * FROM mf_workbook_validation_results WHERE rule_code='ppfas_structure_complete'").fetchone()
                if message.startswith('first'):
                    identity = row['validation_id']
                self.assertEqual(row['validation_id'], identity)
                self.assertEqual(json.loads(row['details_json'])['message'], message)
                self.assertEqual(row['passed'], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM mf_workbook_validation_results WHERE rule_code='ppfas_structure_complete'").fetchone()[0], 1)

    def seed_feature_inputs(self):
        # Reuse the existing synthetic source fixture; transfer is schema- and hash-checked.
        fixture = m06.MFStockMonthlyFeaturesM06Tests('test_exact_scopes_counts_decimal_values_and_lineage_match_golden')
        fixture.conn = self.source
        fixture.fixture = json.loads(m06.FIXTURE.read_text())
        fixture.scheme_ids = {}
        fixture.security_ids = {}
        fixture._seed_import('2026-05')
        fixture._seed_import('2026-06')
        snapshot = Path(self.temp.name) / 'synthetic-mf.db'
        with sqlite3.connect(snapshot) as target:
            self.source.backup(target)
        transfer(self.settings, snapshot, self.schema, expected_sha256=file_sha256(snapshot))
        return fixture.fixture

    def test_exact_changes_features_and_third_party_crosscheck_match_sqlite(self):
        fixture = self.seed_feature_inputs()
        expected_changes = changes.generate_scheme_month_changes(self.source, publisher='ppfas',
            current_month='2026-06', generated_at_utc=NOW)
        expected_features = features.generate_stock_month_features(self.source, publisher='ppfas',
            current_month='2026-06', generated_at_utc=NOW)
        mfdata_path = ROOT / 'fixtures/mfdata/ppfas_active_a_2026_06_conflict.json'
        raw = mfdata_path.read_bytes()
        parsed = parse_mfdata_holdings(raw, requested_family_id=8292, requested_disclosure_month='2026-06')
        expected_crosscheck = mfdata_repo.persist_and_crosscheck_mfdata(self.source, parsed=parsed,
            raw_bytes=raw, publisher='ppfas', scheme_code='ACTIVE_A', submitted_path=str(mfdata_path),
            immutable_path='/synthetic/mfdata.json', observed_at_utc=NOW)
        with self.session() as conn:
            actual_changes = changes.generate_scheme_month_changes(conn, publisher='ppfas',
                current_month='2026-06', generated_at_utc=NOW)
            actual_features = features.generate_stock_month_features(conn, publisher='ppfas',
                current_month='2026-06', generated_at_utc=NOW)
            actual_crosscheck = mfdata_repo.persist_and_crosscheck_mfdata(conn, parsed=parsed,
                raw_bytes=raw, publisher='ppfas', scheme_code='ACTIVE_A', submitted_path=str(mfdata_path),
                immutable_path='/synthetic/mfdata.json', observed_at_utc=NOW)
            self.assertEqual(actual_changes, expected_changes)
            self.assertEqual(actual_features, expected_features)
            self.assertEqual(actual_crosscheck, expected_crosscheck)
            for table in ('mf_scheme_month_change_sets', 'mf_scheme_security_month_changes',
                          'mf_scheme_month_change_validations', 'mf_stock_month_feature_sets',
                          'mf_stock_month_feature_inputs', 'mf_stock_monthly_features',
                          'mf_stock_month_feature_lineage', 'mf_stock_month_feature_validations',
                          'mfdata_snapshots', 'mfdata_snapshot_observations', 'mfdata_holdings',
                          'mfdata_accepted_versions', 'mfdata_accepted_version_events', 'mfdata_crosscheck_runs'):
                with self.subTest(table=table):
                    self.assertEqual(self.records(conn, table), self.records(self.source, table))
            again = features.generate_stock_month_features(conn, publisher='ppfas', current_month='2026-06',
                                                           generated_at_utc=NOW)
            self.assertEqual(again.feature_set_id, actual_features.feature_set_id)
            golden = json.loads(m06.GOLDEN.read_text())
            self.assertEqual(actual_features.lineage_count, golden['lineage_count'])
            self.assertEqual(actual_features.all_scheme_feature_count, 2)
            self.assertEqual(actual_features.active_equity_feature_count, 1)

    def test_concurrent_inventory_cache_preserves_one_import_and_both_observations(self):
        inspection = inspect_workbook(WORKBOOK, allow_ooxml_xls_extension=True)
        def record(_):
            with self.session() as conn:
                return workbook_inventory.persist_inventory(conn, inspection, publisher='ppfas',
                    disclosure_month='2026-06', immutable_path=str(WORKBOOK), observed_at_utc=NOW)
        with ThreadPoolExecutor(max_workers=2) as workers:
            result = list(workers.map(record, range(2)))
        self.assertEqual(len({item.import_file_id for item in result}), 1)
        self.assertCountEqual([item.duplicate for item in result], [False, True])
        with self.session() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM mf_import_files').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM mf_import_observations').fetchone()[0], 2)
