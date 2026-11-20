"""Real restricted exchange/document logins; synthetic offline result capture only."""
from contextlib import closing, contextmanager
from functools import partial
import json
import os
import sqlite3
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, quote

from scrapper.tests import test_postgresql_configuration as fixtures

NOW = '2026-11-17T12:30:32+00:00'
URL = 'https://api.bseindia.com/BseIndiaAPI/api/Corp_FinanceResult_ng_new/w?SCRIP_CD=&FlagDur=1&HFQ=&ISUBGROUP_CODE=&segment=C'


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'), 'requires disposable PostgreSQL')
class NativeExchangeHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ConfigurationIntegrationTests.setUpClass.__func__(cls)
        from ops.activation.nse_results_runtime import exchange_authorizer
        cls.policy = staticmethod(exchange_authorizer)

    drop_database = classmethod(fixtures.ConfigurationIntegrationTests.drop_database.__func__)
    admin_session = classmethod(fixtures.ConfigurationIntegrationTests.admin_session.__func__)
    cleanup = fixtures.ConfigurationIntegrationTests.cleanup

    def setUp(self):
        fixtures.ConfigurationIntegrationTests.setUp(self)
        environment = patch.dict(os.environ, {'TRADING_AI_ENV':'test',
            'TRADING_AI_POSTGRESQL_TARGET_ID':self.target_id})
        environment.start()
        self.addCleanup(environment.stop)
        from scrapper.core.http import FetchResponse
        # Same BSE response shape as the retained incident, no private source rows.
        raw = json.dumps({'Table': [dict(Scrip_cd='500001', scrip_name='Synthetic',
            quarter_code='SQ2026-2027', audited='Unaudited', Qtr='September 2026',
            Fld_CreateDate='2026-11-17T18:00:32', DT_TM='2026-11-17T18:00:32',
            Industry_name='Synthetic', company_name='Synthetic', XMLName='synthetic.xml',
            Fld_NatureOfReport='Standalone', URL='', Consol_XMLName='', Resultpageurl='')]}).encode()
        self.response = FetchResponse(URL, URL, 200, raw, 'application/json', 'utf-8', None, None, 'public', 'ok')
        from scrapper.db.exchange import snapshots
        with self.admin_app() as conn:
            blob = snapshots.store_exchange_blob(conn, self.response, exchange='BSE',
                dataset='bse_security_master', effective_date='2026-11-16',
                observed_at_utc=NOW, snapshot_root=self.root/'seed')
            sid, _ = snapshots.persist_snapshot(conn, exchange='BSE', dataset='bse_security_master',
                effective_date='2026-11-16', source_label='synthetic', requested_url=URL,
                acquisition_mode='fetched', fetch_id=None, blob_id=blob, parser_name='synthetic',
                parser_version='1', source_encoding='utf-8', observed_at_utc=NOW,
                headers=(), accepted_row_count=1, rejected=())
            with conn:
                conn.execute("""INSERT INTO exchange_securities(exchange,segment,exchange_key,bse_scrip_code,
                    current_name_raw,current_status,first_observed_date,first_observed_snapshot_id,
                    last_observed_date,last_observed_snapshot_id,created_at_utc,updated_at_utc)
                    VALUES ('BSE','equity','500001','500001','Synthetic','active','2026-11-16',?,
                        '2026-11-16',?,?,?)""", (sid, sid, NOW, NOW))
                cid = conn.execute("INSERT INTO companies(current_legal_name,status) VALUES ('Synthetic','active') RETURNING company_id").fetchone()[0]
                conn.execute("INSERT INTO security_identifiers(company_id,identifier_type,identifier_value,is_current) VALUES (?,'BSE','500001',1)", (cid,))
                conn.execute("""INSERT INTO screener_collection_scope(company_path,company_id,eligibility,
                    floor_crore,observed_at_utc,evidence_json,next_check_at_utc)
                    VALUES ('/company/500001/',?,'eligible','580',?,'{}',?)""", (cid, NOW, NOW))

    def url(self, *, role=None):
        fields = self.admin
        user = role or fields.get('user', '')
        auth = quote(user, safe='')
        if role:
            auth += ':' + quote(self.password, safe='')
        elif fields.get('password'):
            auth += ':' + quote(fields['password'], safe='')
        return 'postgresql://' + auth + '@/' + quote(self.database, safe='') + '?' + urlencode(
            {k: fields[k] for k in ('host', 'port') if k in fields})

    @contextmanager
    def admin_app(self):
        from scrapper.db.postgresql import PostgreSQLSettings
        from scrapper.db.postgresql_application import PostgreSQLApplicationConnection
        raw = self.psycopg.connect(**self.admin, autocommit=True, row_factory=self.dict_row)
        raw.execute(self.sql.SQL('SET search_path TO {},pg_catalog').format(self.sql.Identifier(self.schema)))
        conn = PostgreSQLApplicationConnection(raw, PostgreSQLSettings(self.url()), self.schema)
        try:
            yield conn
        finally:
            conn.close()

    def exchange(self, *_args, **_kwargs):
        from scrapper.db.postgresql import PostgreSQLSettings
        from scrapper.db.postgresql_application import open_application
        settings = PostgreSQLSettings(self.url(role=self.role))
        return open_application(settings, schema=self.schema, authorizer=self.policy)

    def ingest(self, **options):
        from scrapper.workflows.exchange import universe
        return universe.discover_exchange_results(dataset='bse_financial_results', url=URL,
            result_period='2026-09', effective_date='2026-11-17', now_utc=lambda: NOW,
            snapshot_root=self.root/'snapshots', fetcher=lambda *_: self.response,
            _ingest=partial(universe._ingest, connect=self.exchange), **options)

    def test_exchange_preserves_denial_and_document_owner_completes_handoff_once(self):
        # Reproduce the original code defect without weakening the selected role.
        with self.assertRaisesRegex(sqlite3.OperationalError, '42501'):
            self.ingest()
        with closing(self.exchange()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM artifact_events').fetchone()[0], 0)
        first = self.ingest(register_document_expectations=False)
        self.assertEqual(first['accepted_row_count'], 1)
        self.assertEqual(first['enqueue']['queued'], 1)
        self.ingest(register_document_expectations=False)
        with closing(self.exchange()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0], 1)
            self.assertFalse(conn.execute("SELECT has_table_privilege(current_user,'artifact_events','INSERT')").fetchone()[0])
            self.assertFalse(conn.execute("SELECT has_table_privilege(current_user,'artifact_expectations','INSERT')").fetchone()[0])
        from scrapper.tests.test_postgresql_permissions import routine_authorizer
        from scrapper.db.postgresql_permissions import provision_policy
        from scrapper.db.postgresql import PostgreSQLSettings
        from scrapper.db.postgresql_application import open_application
        role = self.role + '_doc'
        policy = routine_authorizer()
        with self.admin_session() as conn:
            provision_policy(conn, self.schema, role, policy)
            conn.execute(self.sql.SQL('ALTER ROLE {} PASSWORD {}').format(self.sql.Identifier(role), self.sql.Literal(self.password)))
        def cleanup_role():
            with self.admin_session() as conn:
                conn.execute(self.sql.SQL('DROP OWNED BY {}').format(self.sql.Identifier(role)))
                conn.execute(self.sql.SQL('DROP ROLE {}').format(self.sql.Identifier(role)))
        self.addCleanup(cleanup_role)
        def documents(*_):
            return open_application(PostgreSQLSettings(self.url(role=role)), schema=self.schema, authorizer=policy)
        from scrapper.operations.workers.automatic_documents import run_automatic_discovery
        # Future first-check time exercises real handoff and native due SQL without HTTP.
        with patch('sqlite3.connect', side_effect=AssertionError('SQLite fallback')), \
             patch('scrapper.sources.documents.routine.refresh_official_pages',
                   side_effect=AssertionError('Unexpected official request')) as official, \
             patch('scrapper.workflows.documents.reconciliation.reconcile_artifacts',
                   side_effect=AssertionError('Unexpected Screener request')) as reconcile:
            for _ in range(2):
                result = run_automatic_discovery(db_path=self.root/'unused.db', epoch=NOW,
                    limit=1, historical_limit=0, now_utc=lambda: NOW, connection_factory=documents)
                self.assertEqual(result['status'], 'idle')
            official.assert_not_called()
            reconcile.assert_not_called()
            with closing(documents()) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM artifact_events').fetchone()[0], 1)
                self.assertEqual(conn.execute('SELECT count(*) FROM artifact_expectations').fetchone()[0], 4)
                self.assertFalse(conn.execute("SELECT has_table_privilege(current_user,'exchange_result_filings','INSERT')").fetchone()[0])
