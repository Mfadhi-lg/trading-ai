"""Lossless historical-reference migration, fail-closed writes and ID allocation."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from scrapper.db.postgresql_transfer import export_snapshot, file_sha256, transfer
from scrapper.db.queue_identity_recovery import prepare, violation_sha256

ROOT = Path(__file__).resolve().parents[2]
NOW = '2026-08-07T00:00:00Z'


def old_source(path):
    c = sqlite3.connect(path)
    for p in sorted((ROOT / 'scrapper/db/migrations').glob('*.sql')):
        if int(p.name[:3]) <= 76:
            c.executescript(p.read_text())
    c.executescript("""
    INSERT INTO scrape_runs VALUES(1,'2026-08-07',NULL,'synthetic','{}','ok');
    INSERT INTO response_blobs VALUES(1,'synthetic','text/html',NULL,1,'synthetic','2026-08-07','2026-08-07');
    INSERT INTO fetch_observations(fetch_id,run_id,source,requested_url,final_url,fetched_at_utc,http_status,auth_mode,blob_id,fetch_status)
        VALUES(1,1,'synthetic','https://example.invalid','https://example.invalid','2026-08-07',200,'none',1,'ok');
    INSERT INTO screener_screen_runs VALUES(1,'synthetic','synthetic','https://example.invalid',1,1,'synthetic','1','2026-08-07',1,1,1,1,'complete','passed');
    INSERT INTO screener_result_candidates VALUES(1,1,1,'synthetic','Synthetic','/company/ALPHA/','/company/ALPHA/','standalone','202606','2026-06','2026-08-07');
    INSERT INTO screener_company_queue(queue_item_id,candidate_id,company_path_normalized,reporting_scope,result_period,queue_reason,status,created_at_utc,updated_at_utc)
        VALUES(1,1,'/company/ALPHA/','standalone','2026-06','new_company','pending','2026-08-07','2026-08-07');
    """)
    c.execute('ANALYZE')
    c.execute('PRAGMA foreign_keys=OFF')
    c.execute("INSERT INTO screener_company_queue_events(queue_event_id,queue_item_id,event_at_utc,to_status,evidence_json) VALUES(9,1000,?,'pending','{}')", (NOW,))
    c.commit()
    c.execute('PRAGMA journal_mode=DELETE')
    c.close()


def new_queue(conn, *, key=None, path='/company/BETA/'):
    fields = 'candidate_id,company_path_normalized,reporting_scope,result_period,queue_reason,status,created_at_utc,updated_at_utc'
    values = [1,path,'standalone','2026-06','new_company','pending',NOW,NOW]
    if key is not None:
        fields = 'queue_item_id,' + fields
        values.insert(0,key)
    return conn.execute('INSERT INTO screener_company_queue('+fields+') VALUES('+','.join('?' for _ in values)+')', values)


class QueueIdentityRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.target = self.root/'original.db', self.root/'candidate.db'
        old_source(self.source)
        with closing(sqlite3.connect(self.source)) as c:
            self.approval = violation_sha256(c.execute('PRAGMA foreign_key_check').fetchall())
        self.sha = file_sha256(self.source)

    def recover(self):
        return prepare(self.source,self.target,expected_source_sha256=self.sha,
                       approved_orphan_sha256=self.approval)

    def test_exact_history_preserved_without_recreating_jobs_and_source_unchanged(self):
        result = self.recover()
        self.assertEqual(result['historical_identities'],1)
        self.assertEqual(result['new_queue_jobs'],0)
        self.assertEqual(file_sha256(self.source),self.sha)
        with closing(sqlite3.connect(self.target)) as c:
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertIsNone(c.execute('SELECT 1 FROM screener_company_queue WHERE queue_item_id=1000').fetchone())
            self.assertEqual(c.execute('SELECT queue_item_id,evidence_json FROM screener_company_queue_events').fetchall(),[(1000,'{}')])
            self.assertEqual(c.execute('SELECT identity_basis,evidence_sha256 FROM screener_queue_identities WHERE queue_item_id=1000').fetchone(),('historical_reference',self.approval))
        with self.assertRaises(FileExistsError):
            self.recover()

    def test_new_or_changed_orphans_history_edits_and_identity_reuse_are_denied(self):
        self.recover()
        with closing(sqlite3.connect(self.target)) as c:
            c.execute('PRAGMA foreign_keys=ON')
            for statement in (
                "INSERT OR IGNORE INTO screener_company_queue_events(queue_item_id,event_at_utc,to_status) VALUES(1000,'now','pending')",
                "UPDATE screener_queue_identities SET identity_basis=identity_basis",
                "DELETE FROM screener_queue_identities",
                "INSERT INTO screener_queue_identities VALUES(2000,'historical_reference','"+'0'*64+"')",
                "UPDATE screener_company_queue SET queue_item_id=50 WHERE queue_item_id=1",
            ):
                with self.subTest(sql=statement), self.assertRaises(sqlite3.DatabaseError):
                    c.execute(statement)
            with self.assertRaises(sqlite3.DatabaseError):
                new_queue(c,key=1000)
            c.execute("INSERT INTO screener_company_queue_events(queue_item_id,event_at_utc,to_status) VALUES(1,'now','pending')")
            with self.assertRaises(sqlite3.DatabaseError):
                c.execute('DELETE FROM screener_company_queue WHERE queue_item_id=1')
            with self.assertRaises(sqlite3.DatabaseError):
                c.execute('UPDATE screener_company_queue_events SET queue_item_id=1000 WHERE queue_item_id=1')
            first = new_queue(c).lastrowid
            self.assertGreater(first,1000)
            with self.assertRaises(sqlite3.DatabaseError):
                c.execute('DELETE FROM screener_company_queue WHERE queue_item_id=?',(first,))
            second = new_queue(c,path='/company/GAMMA/').lastrowid
            self.assertGreater(second,first)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_sqlite_replace_cannot_remove_a_live_parent(self):
        self.recover()
        with closing(sqlite3.connect(self.target)) as c:
            c.execute('PRAGMA foreign_keys=ON')
            self.assertEqual(c.execute('PRAGMA recursive_triggers').fetchone()[0],0)
            c.execute("INSERT INTO screener_company_queue_events(queue_item_id,event_at_utc,to_status) VALUES(1,'now','pending')")
            c.execute("INSERT OR REPLACE INTO screener_company_queue(candidate_id,company_path_normalized,reporting_scope,result_period,queue_reason,status,created_at_utc,updated_at_utc) SELECT candidate_id,company_path_normalized,reporting_scope,result_period,queue_reason,status,created_at_utc,updated_at_utc FROM screener_company_queue WHERE queue_item_id=1")
            self.assertIsNotNone(c.execute('SELECT 1 FROM screener_company_queue WHERE queue_item_id=1').fetchone())
            second=new_queue(c).lastrowid
            with self.assertRaises(sqlite3.DatabaseError):
                c.execute("UPDATE OR REPLACE screener_company_queue SET company_path_normalized='/company/ALPHA/' WHERE queue_item_id=?",(second,))
            self.assertEqual(c.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0],2)

    def test_claim_token_replace_cannot_remove_live_parent(self):
        self.recover()
        with closing(sqlite3.connect(self.target)) as c:
            c.execute("UPDATE screener_company_queue SET claim_token='owned',claimed_at_utc='now',claim_expires_at_utc='later' WHERE queue_item_id=1")
            second=new_queue(c).lastrowid
            for statement in (
                "INSERT OR REPLACE INTO screener_company_queue(candidate_id,company_path_normalized,reporting_scope,result_period,queue_reason,status,created_at_utc,updated_at_utc,claim_token,claimed_at_utc,claim_expires_at_utc) VALUES(1,'/company/THIRD/','standalone','2026-06','new_company','pending','now','now','owned','now','later')",
                "UPDATE OR REPLACE screener_company_queue SET claim_token='owned',claimed_at_utc='now',claim_expires_at_utc='later' WHERE queue_item_id="+str(second),
            ):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.DatabaseError):
                    c.execute(statement)
            self.assertEqual(c.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0],2)
            self.assertIsNotNone(c.execute('SELECT 1 FROM screener_company_queue WHERE queue_item_id=1').fetchone())

    def test_unknown_source_or_violation_set_fail_before_target_creation(self):
        for sha,approval in (('0'*64,self.approval),(self.sha,'0'*64)):
            with self.subTest(sha=sha), self.assertRaises(RuntimeError):
                prepare(self.source,self.target,expected_source_sha256=sha,approved_orphan_sha256=approval)
            self.assertFalse(self.target.exists())

    def test_normal_migration_does_not_automatically_grandfather_orphans(self):
        with closing(sqlite3.connect(self.source)) as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.executescript((ROOT/'scrapper/db/migrations/077_screener_queue_identities.sql').read_text())
            c.rollback()
            self.assertEqual(c.execute('SELECT count(*) FROM screener_company_queue_events').fetchone()[0],1)
            self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='screener_queue_identities'").fetchone())


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'),'requires disposable PostgreSQL 16')
class QueueIdentityTransferTests(QueueIdentityRecoveryTests):
    def test_recovered_history_import_rerun_and_native_future_guards(self):
        from psycopg import sql
        from scrapper.db.postgresql import PostgreSQLSettings,connect
        from scrapper.db.postgresql_schema import migrate
        self.recover()
        snap=self.root/'verified.db'
        report=export_snapshot(self.target,snap)
        settings=PostgreSQLSettings(os.environ['TRADING_AI_POSTGRESQL_TEST_DSN'])
        schema='queue_history_'+uuid.uuid4().hex
        try:
            with connect(settings) as c:
                migrate(c,schema)
            result=transfer(settings,snap,schema,expected_sha256=report['snapshot_sha256'])
            self.assertEqual(result['status'],'committed')
            self.assertEqual(len(result['tables']),158)
            self.assertEqual(transfer(settings,snap,schema,expected_sha256=report['snapshot_sha256'])['status'],'verified_existing')
            with connect(settings) as c:
                c.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(schema)))
                self.assertIsNone(c.execute('SELECT 1 FROM screener_company_queue WHERE queue_item_id=1000').fetchone())
                for query in (
                    "INSERT INTO screener_company_queue_events(queue_item_id,event_at_utc,to_status) VALUES(1000,'now','pending')",
                    "INSERT INTO screener_queue_identities VALUES(2000,'historical_reference','"+'0'*64+"')",
                    "DELETE FROM screener_queue_identities",
                ):
                    with self.subTest(query=query), self.assertRaises(Exception):
                        with c.transaction():c.execute(query)
                row=c.execute("INSERT INTO screener_company_queue(candidate_id,company_path_normalized,reporting_scope,result_period,queue_reason,status,created_at_utc,updated_at_utc) VALUES(1,'/company/BETA/','standalone','2026-06','new_company','pending','now','now') RETURNING queue_item_id").fetchone()
                self.assertGreater(row['queue_item_id'],1000)
        finally:
            with connect(settings) as c:c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
