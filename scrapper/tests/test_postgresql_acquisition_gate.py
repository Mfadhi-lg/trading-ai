"""Issue #97: real reader logins and public backend selection, on disposable DBs."""
from contextlib import contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from scrapper.db.database import connect
from scrapper.pipeline import evaluate_document_acquisition_gate as gate
from scrapper.tests.test_final_acquisition_gate import _green_reports, NOW, SCOPE
from scrapper.tests import test_postgresql_configuration as fixtures


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'),
                     'requires an explicitly disposable PostgreSQL 16 cluster')
class PostgreSQLAcquisitionGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ConfigurationIntegrationTests.setUpClass.__func__(cls)
        cls.policy = staticmethod(lambda *_: sqlite3.SQLITE_DENY)  # SELECT-only role.

    drop_database = classmethod(fixtures.ConfigurationIntegrationTests.drop_database.__func__)
    admin_session = classmethod(fixtures.ConfigurationIntegrationTests.admin_session.__func__)
    cleanup = fixtures.ConfigurationIntegrationTests.cleanup
    dsn = fixtures.ConfigurationIntegrationTests.dsn

    def setUp(self):
        fixtures.ConfigurationIntegrationTests.setUp(self)
        self.output = self.root / 'reports'
        self.output.mkdir()
        for name, payload in _green_reports().items():
            (self.output / (name + '.json')).write_text(json.dumps(payload))
        (self.root / 'artifacts/focus_ml/governance').mkdir(parents=True)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(
                self.sql.Identifier(self.schema)))
            conn.execute("INSERT INTO companies(company_id,current_legal_name,status) VALUES(1,'Synthetic','active')")
            conn.execute("""INSERT INTO artifact_download_scopes
                (scope_id,scope_kind,description,artifact_types_json,policy_json,created_at_utc,updated_at_utc)
                VALUES(%s,'custom','test','["transcript"]','{}',%s,%s)""", (SCOPE, NOW, NOW))
            conn.execute("""INSERT INTO document_artifacts
                (artifact_id,company_id,artifact_key,artifact_type,publisher,title_raw,
                 first_observed_at_utc,acquisition_status,transcript_preference_status)
                VALUES(1,1,'synthetic','transcript','issuer','Synthetic',%s,'discovered','not_applicable')""", (NOW,))
            conn.execute('INSERT INTO artifact_download_scope_artifacts(scope_id,artifact_id) VALUES(%s,1)', (SCOPE,))
            conn.execute("""INSERT INTO document_acquisition_closure_states
                (scope_id,artifact_id,final_state,evidence_json,evaluated_at_utc)
                VALUES(%s,1,'NOT_OFFERED_CONFIRMED','{}',%s)""", (SCOPE, NOW))

    @contextmanager
    def reader(self):
        environment = {'TRADING_AI_ENV': 'test', 'TRADING_AI_DB_BACKEND': 'postgresql',
                       'DATABASE_URL': self.dsn(), 'TRADING_AI_POSTGRESQL_SCHEMA': self.schema,
                       'TRADING_AI_POSTGRESQL_TARGET_ID': self.target_id}
        with patch.dict(os.environ, environment), patch('sqlite3.connect', side_effect=AssertionError('SQLite fallback')):
            conn = connect(self.root / 'unused.db', read_only=True)
            try:
                yield conn
            finally:
                conn.close()
        self.assertFalse((self.root / 'unused.db').exists())

    def evaluate(self, conn):
        report = gate.evaluate(conn, output_dir=self.output, scope_id=SCOPE, db_path=Path('/data/scraper.db'))
        machine = gate.write_gate_json(report, self.output)
        self.assertFalse(conn.in_transaction)
        self.assertEqual(machine['database_source']['backend'], 'postgresql')
        self.assertTrue(machine['verified_at_utc'])
        return report

    def admin_sql(self, statement):
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(self.sql.Identifier(self.schema)))
            conn.execute(statement)

    def test_valid_corpus_through_public_read_only_connection(self):
        with self.reader() as conn:
            report = self.evaluate(conn)
            self.assertTrue(report['checks']['acquisition_foreign_keys'], report)
            self.assertEqual(report['counts']['acquisition_fk_violations'], 0)
            # A test schema cannot claim production closure via a SQLite path.
            self.assertFalse(report['checks']['production_db_path'])
            self.assertTrue(all(value for name, value in report['checks'].items() if name != 'production_db_path'), report)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO companies(current_legal_name) VALUES('denied')")

    def test_missing_constraint_fails_even_without_orphans(self):
        with self.reader() as conn:
            self.admin_sql('ALTER TABLE artifact_contents DROP CONSTRAINT artifact_contents_blob_id_fkey')
            report = self.evaluate(conn)
            self.assertFalse(report['checks']['acquisition_foreign_keys'])
            self.assertEqual(report['counts']['acquisition_fk_violations'], 0)

    def test_unvalidated_constraint_and_orphan_are_both_detected(self):
        with self.reader() as conn:
            self.admin_sql('ALTER TABLE document_artifacts DROP CONSTRAINT document_artifacts_company_id_fkey')
            self.admin_sql('UPDATE document_artifacts SET company_id=999 WHERE artifact_id=1')
            self.admin_sql('ALTER TABLE document_artifacts ADD FOREIGN KEY(company_id) REFERENCES companies(company_id) NOT VALID')
            report = self.evaluate(conn)
            self.assertFalse(report['checks']['acquisition_foreign_keys'])
            self.assertEqual(report['counts']['acquisition_fk_violations'], 1)
            self.assertIn('document_artifacts.company_id', report['gate_evidence']['acquisition_foreign_keys']['invalid_constraints'])

    def test_unvalidated_constraint_with_clean_rows_still_fails(self):
        with self.reader() as conn:
            self.admin_sql('ALTER TABLE artifact_contents DROP CONSTRAINT artifact_contents_blob_id_fkey')
            self.admin_sql('ALTER TABLE artifact_contents ADD FOREIGN KEY(blob_id) REFERENCES response_blobs(blob_id) NOT VALID')
            report = self.evaluate(conn)
            self.assertFalse(report['checks']['acquisition_foreign_keys'])
            self.assertEqual(report['counts']['acquisition_fk_violations'], 0)

    def test_disabled_fk_triggers_fail(self):
        with self.reader() as conn:
            self.admin_sql('ALTER TABLE artifact_contents DISABLE TRIGGER ALL')
            self.assertFalse(self.evaluate(conn)['checks']['acquisition_foreign_keys'])

    def test_orphan_is_counted_even_when_constraint_is_marked_valid(self):
        with self.reader() as conn:
            self.admin_sql('ALTER TABLE document_artifacts DISABLE TRIGGER ALL')
            self.admin_sql('UPDATE document_artifacts SET company_id=999 WHERE artifact_id=1')
            self.admin_sql('ALTER TABLE document_artifacts ENABLE TRIGGER ALL')
            report = self.evaluate(conn)
            self.assertFalse(report['checks']['acquisition_foreign_keys'])
            self.assertEqual(report['counts']['acquisition_fk_violations'], 1)
            self.assertEqual(report['gate_evidence']['acquisition_foreign_keys']['invalid_constraints'], [])

    def test_missing_table_yields_unknown_red(self):
        with self.reader() as conn:
            self.admin_sql('DROP TABLE artifact_recovery_targets CASCADE')
            report = self.evaluate(conn)
            self.assertFalse(report['checks']['database_queries_succeeded'])
            self.assertIsNone(report['counts']['acquisition_fk_violations'])
            self.assertFalse(report['ready_for_research_intelligence'])

    def test_native_permission_failure_yields_unknown_red(self):
        with self.reader() as conn:
            self.admin_sql(self.sql.SQL('REVOKE SELECT ON response_blobs FROM {}').format(self.sql.Identifier(self.role)))
            report = self.evaluate(conn)
            self.assertFalse(report['checks']['database_queries_succeeded'])
            self.assertFalse(report['checks']['acquisition_foreign_keys'])
            self.assertIsNone(report['counts']['acquisition_fk_violations'])
            self.assertEqual(report['document_acquisition'], 'RED')

    def test_missing_report_remains_red_with_native_integrity_passing(self):
        (self.output / '12_TRANSCRIPT_COVERAGE.json').unlink()
        with self.reader() as conn:
            report = self.evaluate(conn)
            self.assertTrue(report['checks']['acquisition_foreign_keys'])
            self.assertFalse(report['checks']['gate_inputs_present'])
            self.assertFalse(report['ready_for_research_intelligence'])

    def test_main_writes_red_without_migration_or_handoff_on_native_read_failure(self):
        with self.reader() as conn:
            self.admin_sql(self.sql.SQL('REVOKE SELECT ON response_blobs FROM {}').format(self.sql.Identifier(self.role)))
            with patch.object(gate, 'connect', return_value=conn) as factory, \
                 patch('sys.argv', ['gate', '--output-dir', str(self.output), '--db', str(self.root / 'unused.db')]), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(gate.main(), 2)
            self.assertTrue(factory.call_args.kwargs['read_only'])
        machine = json.loads((self.output / 'FINAL_ACQUISITION_GATE.json').read_text())
        self.assertEqual(machine['document_acquisition'], 'RED')
        self.assertFalse((self.output / 'DOCUMENT_ACQUISITION_HANDOFF_TO_RI.md').exists())

    def test_main_public_factory_schema_failure_writes_unknown_red(self):
        with self.reader():  # Keep real backend/environment selection active.
            self.admin_sql('ALTER TABLE artifact_contents DROP CONSTRAINT artifact_contents_blob_id_fkey')
            with patch.object(gate, 'connect', wraps=connect) as factory, \
                 patch('sys.argv', ['gate', '--output-dir', str(self.output), '--db', str(self.root / 'unused.db')]), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(gate.main(), 2)
            self.assertTrue(factory.call_args.kwargs['read_only'])
        machine = json.loads((self.output / 'FINAL_ACQUISITION_GATE.json').read_text())
        self.assertEqual(machine['document_acquisition'], 'RED')
        self.assertIsNone(machine['counts']['acquisition_fk_violations'])
        self.assertEqual(machine['database_source'], {'backend': 'postgresql', 'status': 'unverified'})
        self.assertFalse((self.output / 'DOCUMENT_ACQUISITION_HANDOFF_TO_RI.md').exists())
