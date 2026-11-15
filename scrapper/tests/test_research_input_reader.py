"""Current input failures never select historical evidence; replay preserves bytes."""
from contextlib import redirect_stdout, closing
import gzip
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scrapper.research.input_reader import observation, file_sha256
from experiments.historical_candle_archive_v1 import prepare
from scrapper.research.focus import build_p0_governance as focus


class InputTests(unittest.TestCase):
    def test_current_entrypoints_refuse_retained_inputs_before_writing(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'TRADING_AI_DB_BACKEND':'sqlite'}), \
             patch('sqlite3.connect', side_effect=AssertionError('fallback')), \
             patch.object(prepare, 'OUT', Path(folder)/'out'):
            with self.assertRaisesRegex(RuntimeError, 'no retained SQLite fallback'):
                prepare.main([])
            self.assertFalse(prepare.OUT.exists())
            with patch('sys.argv', ['governance']), patch.object(focus, 'assert_configured_writable_targets'):
                with self.assertRaisesRegex(RuntimeError, 'no retained SQLite fallback'):
                    focus._main()

    def test_planner_refuses_oversized_input_without_writing_outputs(self):
        from contextlib import contextmanager
        from unittest.mock import Mock
        conn=Mock()
        conn.execute.return_value.fetchall.return_value=[{}]*100001
        @contextmanager
        def source(*args, **kwargs):
            yield conn, {'mode':'synthetic'}
        with tempfile.TemporaryDirectory() as folder, patch.object(prepare, 'observation', source), \
             patch.object(prepare, 'OUT', Path(folder)/'out'):
            with self.assertRaisesRegex(ValueError, 'read bound'):
                prepare.main([])
            self.assertFalse(prepare.OUT.exists())

    def test_native_connection_error_has_no_sqlite_fallback(self):
        with patch.dict(os.environ, {'TRADING_AI_DB_BACKEND':'postgresql'}), \
             patch('scrapper.db.database.connect', side_effect=RuntimeError('unavailable')), \
             patch('sqlite3.connect', side_effect=AssertionError('fallback')):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'), observation():
                pass

    def test_frozen_input_requires_provenance_and_preserves_schema_labels_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'frozen.db'
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript("CREATE TABLE schema_migrations(version TEXT); INSERT INTO schema_migrations VALUES('historical-v1'); CREATE TABLE labels(value TEXT); INSERT INTO labels VALUES('UNKNOWN');")
            before=path.read_bytes()
            checksum=file_sha256(path)
            with observation(path, frozen_sha256=checksum, observed_at='2026-10-08T00:00:00Z') as (conn, source):
                self.assertEqual(source['schema_versions'], ['historical-v1'])
                self.assertEqual(source['mode'], 'frozen_sqlite_snapshot')
                self.assertEqual(conn.execute('SELECT value FROM labels').fetchone()[0], 'UNKNOWN')
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("UPDATE labels SET value='0'")
            self.assertEqual(before, path.read_bytes())
            for kwargs in ({}, {'frozen_sha256':checksum}, {'frozen_sha256':'bad','observed_at':'2026-10-08T00:00:00Z'}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError), observation(path, **kwargs):
                    pass
            Path(str(path)+'-wal').touch()
            with self.assertRaisesRegex(ValueError, 'sidecar'), observation(path, frozen_sha256=checksum, observed_at='2026-10-08T00:00:00Z'):
                pass

    def test_modified_frozen_file_is_rejected_after_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'frozen.db'
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('CREATE TABLE schema_migrations(version TEXT)')
                conn.commit()
            with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                with observation(path, frozen_sha256=file_sha256(path), observed_at='2026-10-08T00:00:00Z'):
                    with path.open('ab') as stream:
                        stream.write(b'changed')


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'), 'requires disposable PostgreSQL')
class NativeInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scrapper.tests.test_postgresql_configuration import ConfigurationIntegrationTests as fixture
        fixture.setUpClass.__func__(cls)
        cls.policy=staticmethod(lambda *_: sqlite3.SQLITE_DENY)

    from scrapper.tests.test_postgresql_configuration import ConfigurationIntegrationTests as _fixture
    drop_database=classmethod(_fixture.drop_database.__func__)
    admin_session=classmethod(_fixture.admin_session.__func__)
    cleanup=_fixture.cleanup
    dsn=_fixture.dsn
    setUp=_fixture.setUp

    def test_public_planner_and_focus_queries_on_restricted_native_input(self):
        metadata=self.root/'provider.json.gz'
        metadata.write_bytes(gzip.compress(json.dumps([dict(segment='NSE_EQ', exchange='NSE',
            isin='INE000A00001', trading_symbol='ALPHA', instrument_key='NSE_EQ|INE000A00001', instrument_type='EQ')]).encode()))
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('SET search_path TO {},pg_catalog').format(self.sql.Identifier(self.schema)))
            conn.execute("INSERT INTO companies(company_id,current_legal_name) VALUES(1,'Alpha synthetic')")
            conn.execute("INSERT INTO security_identifiers(company_id,identifier_type,identifier_value,is_current) VALUES(1,'NSE','ALPHA',1)")
            conn.execute("INSERT INTO screener_collection_scope(company_path,company_id,eligibility,market_cap_crore,floor_crore,observed_at_utc,evidence_json,next_check_at_utc) VALUES('/company/ALPHA/',1,'eligible','1000','580','2026-11-15T00:00:00Z','{}','2026-11-16T00:00:00Z')")
        environment={'TRADING_AI_ENV':'test','TRADING_AI_DB_BACKEND':'postgresql',
                     'DATABASE_URL':self.dsn(),'TRADING_AI_POSTGRESQL_SCHEMA':self.schema,
                     'TRADING_AI_POSTGRESQL_TARGET_ID':self.target_id}
        with patch.dict(os.environ, environment), patch('sqlite3.connect', side_effect=AssertionError('fallback')), \
             patch.object(prepare, 'OUT', self.root/'plan'), patch.object(prepare, 'PILOT', ()), \
             patch.object(prepare, 'METADATA', metadata), patch.object(prepare, 'ROOT', self.root), redirect_stdout(io.StringIO()):
            prepare.main([])
            summary=json.loads((prepare.OUT/'universe-summary.json').read_text())
            self.assertEqual(summary['database_source']['mode'], 'native_current_observation')
            self.assertTrue(summary['database_source']['observed_at_utc'])
            universe=json.loads((prepare.OUT/'eligible-universe.json').read_text())
            self.assertEqual(universe[0]['instrument_key'], 'NSE_EQ|INE000A00001')
            self.assertEqual(universe[0]['market_cap_observed_at'], '2026-11-15T00:00:00Z')
            retained=prepare.OUT/'pilot-companies.json'
            retained.write_text(json.dumps([dict(universe[0],instrument_key='NSE_EQ|STALE')]))
            with self.assertRaisesRegex(ValueError, 'Retained pilot identity'):
                prepare.main([])
            retained.write_text(json.dumps(universe))
            prepare.main([])
            pilot=json.loads(retained.read_text())
            self.assertEqual(pilot[0]['instrument_key'], universe[0]['instrument_key'])
            with observation() as (conn, source):
                self.assertEqual(conn.execute('SHOW transaction_isolation').fetchone()[0], 'repeatable read')
                self.assertEqual(conn.execute('SHOW transaction_read_only').fetchone()[0], 'on')
                identity=focus._identity_details(conn, 1)
                self.assertEqual(identity['nse_symbol'], 'ALPHA')
                self.assertEqual(focus._result_availability(conn, metadata=identity, period_end='2026-06-30', cutoff='2026-08-02')['result_availability'], 'NOT_FOUND_IN_CAPTURED_FEEDS')
                with patch.object(focus, 'ingest_q1_focus', return_value=[]), \
                     patch.object(focus, 'ingest_q4_focus', return_value=[]), \
                     patch.object(focus, 'build_negative_approval_pool', return_value=[]):
                    result=focus.build_p0_governance(conn, repository_root=self.root,
                        output_dir=self.root/'focus', created_at_utc='2026-11-15T00:00:00Z',
                        require_production=False, database_source=source)
                    self.assertEqual(result['gate'], 'YELLOW')
                    manifest=json.loads((self.root/'focus/P0_GOVERNANCE_MANIFEST_v001.json').read_text())
                    self.assertFalse(manifest['queueable'])
                    self.assertEqual(manifest['database_source']['target_id'], self.target_id)
                for quarter in focus.QUARTERS:
                    self.assertEqual(focus._artifact_shape(conn, company_id=1, quarter=quarter)['credit_rating_artifact_count'], 0)
                    self.assertEqual(focus._freeze_artifact_ids(conn, company_id=1, quarter=quarter)['credit_ratings'], [])
            with patch('sys.argv', ['governance']), patch.object(focus, 'assert_configured_writable_targets'), \
                 patch.object(focus, 'build_p0_governance', return_value={}) as build:
                self.assertEqual(focus._main(), 0)
                self.assertEqual(build.call_args.kwargs['database_source']['mode'], 'native_current_observation')

    def test_bad_native_target_never_opens_retained_sqlite(self):
        with patch.dict(os.environ, {'TRADING_AI_ENV':'test','TRADING_AI_DB_BACKEND':'postgresql',
             'DATABASE_URL':self.dsn(),'TRADING_AI_POSTGRESQL_SCHEMA':self.schema,
             'TRADING_AI_POSTGRESQL_TARGET_ID':'00000000-0000-4000-8000-000000000001'}), \
             patch('sqlite3.connect', side_effect=AssertionError('fallback')):
            with self.assertRaisesRegex(RuntimeError, 'identity'), observation():
                pass
