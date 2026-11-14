"""Issue #98: public diagnostics on verified disposable restricted native targets."""
from contextlib import contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from ops.host import diagnose_financial_pending as pending
from ops.host import financial_queue_eligibility as eligibility
from ops.host import runtime_status as status
from scrapper.tests import test_postgresql_configuration as fixtures


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'),
                     'requires an explicitly disposable PostgreSQL 16 cluster')
class NativeFinancialDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ConfigurationIntegrationTests.setUpClass.__func__(cls)
        cls.policy = staticmethod(lambda *_: sqlite3.SQLITE_DENY)

    drop_database = classmethod(fixtures.ConfigurationIntegrationTests.drop_database.__func__)
    admin_session = classmethod(fixtures.ConfigurationIntegrationTests.admin_session.__func__)
    cleanup = fixtures.ConfigurationIntegrationTests.cleanup
    dsn = fixtures.ConfigurationIntegrationTests.dsn
    setUp = fixtures.ConfigurationIntegrationTests.setUp

    @contextmanager
    def native(self, **changes):
        environment = {'TRADING_AI_ENV':'test', 'TRADING_AI_DB_BACKEND':'postgresql',
                       'DATABASE_URL':self.dsn(), 'TRADING_AI_POSTGRESQL_SCHEMA':self.schema,
                       'TRADING_AI_POSTGRESQL_TARGET_ID':self.target_id, **changes}
        with patch.dict(os.environ, environment), patch('sqlite3.connect', side_effect=AssertionError('SQLite fallback')) as sqlite_open:
            try:
                yield
            finally:
                sqlite_open.assert_not_called()

    def output(self, main, argv=None, expected=0):
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(main(argv or []), expected, stream.getvalue())
        return json.loads(stream.getvalue())

    def test_public_commands_and_native_schema_use_actual_reader_no_sqlite(self):
        with self.native():
            for main in (pending.main, eligibility.main):
                report = self.output(main)
                self.assertEqual(report['database_source']['backend'], 'postgresql')
                self.assertEqual(report['status'], 'verified')
            self.assertEqual(self.output(eligibility.main, ['--accepted-versions'])['rows'], [])
            report = status.native_migrations()
            self.assertEqual(report['status'], 'verified', report)
            self.assertEqual(report['value']['backend'], 'postgresql')
            self.assertTrue(report['value']['latest_known'].startswith('005_'))
            # Even a legacy --database selection must obey authoritative backend.
            self.assertEqual(status.migrations(Path('/unused'), Path('/stale.db'))['status'], 'verified')

    def test_reader_transaction_and_server_privileges_are_read_only(self):
        from financial_diagnostic_reader import observation
        with self.native(), observation(Path('/stale.db')) as (conn, source):
            self.assertEqual(conn.execute('SHOW transaction_isolation').fetchone()[0], 'repeatable read')
            self.assertEqual(conn.execute('SHOW transaction_read_only').fetchone()[0], 'on')
            self.assertFalse(conn.execute("SELECT has_table_privilege(current_user,'companies','INSERT')").fetchone()[0])
            self.assertEqual(source['backend'], 'postgresql')
            with self.assertRaises(self.psycopg.errors.ReadOnlySqlTransaction):
                conn.raw.execute("INSERT INTO companies(current_legal_name) VALUES('denied')")

    def test_target_mismatch_or_connection_failure_never_falls_back(self):
        for changes in ({'TRADING_AI_POSTGRESQL_TARGET_ID':'00000000-0000-4000-8000-000000000001'},
                        {'DATABASE_URL':'postgresql://test@127.0.0.1:1/unavailable'}):
            with self.subTest(changes=tuple(changes)), self.native(**changes):
                for main in (pending.main, eligibility.main):
                    self.assertEqual(self.output(main, expected=1)['status'], 'unknown')
                self.assertEqual(status.native_migrations()['status'], 'unknown')

    def test_native_lineage_mismatch_remains_unknown(self):
        with self.admin_session() as admin:
            admin.execute(self.sql.SQL("UPDATE {}._postgresql_migrations SET sha256='bad' WHERE version LIKE '001_%'").format(self.sql.Identifier(self.schema)))
        with self.native():
            self.assertEqual(status.native_migrations()['status'], 'unknown')
            self.assertEqual(self.output(pending.main, expected=1)['status'], 'unknown')
            self.assertEqual(self.output(eligibility.main, expected=1)['status'], 'unknown')

    def test_structure_drift_remains_unknown(self):
        with self.admin_session() as admin:
            admin.execute(self.sql.SQL('ALTER TABLE {}.companies ADD COLUMN unreviewed text').format(self.sql.Identifier(self.schema)))
        with self.native():
            self.assertEqual(status.native_migrations()['status'], 'unknown')
            self.assertEqual(self.output(eligibility.main, expected=1)['status'], 'unknown')

    def test_synthetic_financial_reasons_match_sqlite_including_filing_discovery(self):
        from contextlib import closing
        from scrapper.tests.test_e01_exchange_universe import ExchangeCase, nse_results
        from scrapper.db.postgresql import PostgreSQLSettings
        from scrapper.db.postgresql_transfer import export_snapshot, transfer
        fixture = ExchangeCase(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        fixture.build_universe()
        line = 'ALPHA,Alpha Industries Limited,Capital Goods,Quarterly,30-SEP-2026,Un-Audited,Non-cumulative,Standalone,05-Oct-2026 18:30:00'
        fixture.discover('nse_financial_results', nse_results((line,)), '2026-10-10', result_period='2026-09')
        now = '2026-11-14T12:00:00+00:00'
        with closing(sqlite3.connect(fixture.db)) as source:
            source.row_factory = sqlite3.Row
            base = dict(source.execute('SELECT * FROM screener_company_queue LIMIT 1').fetchone())
            source.execute("UPDATE screener_company_queue SET next_attempt_at_utc=NULL,result_period='2025-06'")
            for i, changes in enumerate((
                {'next_attempt_at_utc':'2999-01-01T00:00:00+00:00'},
                {'collection_disposition':'below_floor'},
                {'status':'running', 'claim_token':'active', 'claimed_at_utc':now, 'claim_expires_at_utc':'2999-01-01T00:00:00+00:00'},
                {'status':'claimed', 'claim_token':'expired', 'claimed_at_utc':'2020-01-01T00:00:00+00:00', 'claim_expires_at_utc':'2020-01-02T00:00:00+00:00'},
                {'result_period':'2026-12'}, {'result_period':'2025-03'},
                {'reporting_scope':'consolidated'}, {'status':'accepted'}, {'collection_disposition':None},
            ), start=2):
                row = {**base, 'queue_item_id':i, 'company_path_normalized':f'/company/CASE{i}/', 'next_attempt_at_utc':None, **changes}
                source.execute('INSERT INTO screener_company_queue ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', tuple(row.values()))
            source.execute("INSERT INTO screener_collection_scope(company_path,eligibility,floor_crore,observed_at_utc,evidence_json,next_check_at_utc) VALUES('/company/CANONICAL/','below_floor','580',?,'{}',?)", (now, now))
            source.execute("INSERT INTO screener_scope_aliases VALUES('/company/CASE10/','/company/CANONICAL/','synthetic')")
            source.commit()
            expected = eligibility.inspect(source, now)
            expected_pending = pending.inspect(source, now)
            receipt = export_snapshot(fixture.db, self.root/'source.db')
            transfer(PostgreSQLSettings(self.dsn(admin=True)), self.root/'source.db', self.schema, expected_sha256=receipt['snapshot_sha256'])
        from financial_diagnostic_reader import observation
        with self.native(), observation(Path('/stale.db')) as (conn, _):
            actual = eligibility.inspect(conn, now)
            self.assertEqual(actual, expected)
            self.assertEqual(pending.inspect(conn, now), expected_pending)
            for key in ('current_due','deferred_until_due','active_or_unknown_lease','historical_lane','reporting_scope','future_period','accepted','below_market_cap','recent_filing_due'):
                self.assertIn(key, actual['all_jobs'])
            self.assertTrue(actual['pending_examples'][0]['discovered'])
