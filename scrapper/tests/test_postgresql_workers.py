"""Native ownership races and bounded processing with synthetic PostgreSQL data."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import os
from threading import Barrier
import unittest
import uuid

from scrapper.db import postgresql_workers as workers
from scrapper.db.document_work_claims import ExtractionBusy, OwnershipLost
from scrapper.db.documents.processing import ProcessingVersion

NOW = '2026-11-08T10:00:00+00:00'
LATER = '2026-11-08T10:00:02+00:00'


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'),
                     'requires explicitly disposable PostgreSQL 16 DSN')
class WorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        from psycopg import sql
        from scrapper.db.postgresql import PostgreSQLSettings, connect
        from scrapper.db.postgresql_schema import migrate
        self.settings = PostgreSQLSettings(os.environ['TRADING_AI_POSTGRESQL_TEST_DSN'])
        self.connect = lambda: connect(self.settings)
        self.sql = sql
        self.schema = 'workers_test_' + uuid.uuid4().hex
        with self.connect() as conn:
            migrate(conn, self.schema)
        self.addCleanup(self.drop_schema)
        self.version = ProcessingVersion.create(parser_name='synthetic',parser_version='1',
            ocr_engine='none',ocr_engine_version='1',options={'language':'eng'})
        with self.session() as conn:
            conn.execute("INSERT INTO companies(company_id,current_legal_name) VALUES (1,'Synthetic')")

    def drop_schema(self):
        with self.connect() as conn:
            conn.execute(self.sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(
                self.sql.Identifier(self.schema)))

    @contextmanager
    def session(self):
        with self.connect() as conn:
            conn.execute(self.sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(
                self.sql.Identifier(self.schema)))
            yield conn

    def content(self, index=1, *, media='application/pdf', kind='presentation'):
        with self.session() as conn:
            conn.execute('''INSERT INTO response_blobs
                (blob_id,content_sha256,byte_size,local_path,first_observed_at_utc,created_at_utc)
                VALUES (%s,%s,1,'synthetic-unused',%s,%s)''', (index, str(index), NOW, NOW))
            conn.execute('''INSERT INTO document_artifacts
                (artifact_id,company_id,artifact_key,artifact_type,title_raw,first_observed_at_utc)
                VALUES (%s,1,%s,%s,'synthetic',%s)''', (index, str(index), kind, NOW))
            conn.execute('''INSERT INTO artifact_contents
                (content_id,artifact_id,blob_id,media_type,content_sha256,byte_size,
                 is_official,content_origin,created_at_utc)
                VALUES (%s,%s,%s,%s,%s,1,1,'publisher_original',%s)''',
                (index,index,index,media,str(index),NOW))
        return index

    def test_concurrent_absent_key_has_exactly_one_owner(self):
        barrier = Barrier(2)
        def attempt():
            try:
                with self.session() as conn:
                    barrier.wait(timeout=5)
                    return workers.claim_extraction(conn, 'same', now=NOW)
            except ExtractionBusy:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sum(claim is not None for claim in claims), 1)

    def test_expiry_reclaim_stale_renew_assert_and_release(self):
        with self.session() as conn:
            first = workers.claim_extraction(conn, 'same', seconds=1, now=NOW)
        with self.session() as conn:
            second = workers.claim_extraction(conn, 'same', now=LATER)
        self.assertNotEqual(first['claim_token'], second['claim_token'])
        for operation in (workers.assert_extraction_owned, workers.renew_extraction):
            with self.subTest(operation=operation.__name__), self.assertRaises(OwnershipLost):
                with self.session() as conn:
                    operation(conn, first, now=LATER)
        with self.session() as conn:
            self.assertEqual(workers.release_extraction(conn, first), 0)
            workers.assert_extraction_owned(conn, second, now=LATER)
            self.assertEqual(workers.release_extraction(conn, second), 1)
        with self.session() as conn:
            third = workers.claim_extraction(conn, 'same', now=LATER)
            self.assertNotEqual(third['claim_token'], second['claim_token'])

    def test_renew_never_shortens_and_timezone_comparisons_are_instant_based(self):
        with self.session() as conn:
            first = workers.claim_extraction(conn, 'same', seconds=600, now=NOW)
        with self.session() as conn:
            workers.renew_extraction(conn, first, seconds=1, now='2026-11-08T15:30:00+05:30')
            row = conn.execute('SELECT * FROM document_extraction_claims').fetchone()
            self.assertEqual(row['lease_expires_at_utc'], first['lease_expires_at_utc'])
        with self.assertRaises(ExtractionBusy):
            with self.session() as conn:
                workers.claim_extraction(conn, 'same', now='2026-11-08T15:30:01+05:30')

    def test_rollback_releases_claim_and_assertion_locks_until_persistence_commit(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic rollback'):
            with self.session() as conn:
                workers.claim_extraction(conn, 'rollback', now=NOW)
                raise RuntimeError('synthetic rollback')
        with self.session() as conn:
            self.assertIsNotNone(workers.claim_extraction(conn, 'rollback', now=NOW))
            claim = workers.claim_extraction(conn, 'locked', seconds=1, now=NOW)
        from psycopg.errors import LockNotAvailable
        with self.session() as writer:
            workers.assert_extraction_owned(writer, claim, now=NOW)
            # A newer worker cannot overwrite the fence while persistence holds it.
            with self.assertRaises(LockNotAvailable):
                with self.session() as contender:
                    contender.execute("SET LOCAL lock_timeout='100ms'")
                    workers.claim_extraction(contender, 'locked', now=LATER)
            workers.release_extraction(writer, claim)
        with self.session() as conn:
            self.assertIsNotNone(workers.claim_extraction(conn, 'locked', now=LATER))

    def test_processing_exact_identity_unsupported_evidence_and_shared_claim(self):
        self.content()
        self.content(2,media='audio/mpeg',kind='recording')
        self.content(3,media='text/html',kind='credit_rating')
        self.content(4,media='text/html',kind='presentation')
        with self.session() as conn:
            jobs = workers.reconcile_processing(conn,content_ids=[1,2,3,4,1],version=self.version,now=NOW)
            self.assertEqual([j['content_id'] for j in jobs], [1,3])
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM document_processing_inbox').fetchone()['n'],4)
            self.assertEqual(workers.reconcile_processing(conn,content_ids=[1,3],version=self.version,now=NOW),jobs)
            changed = workers.reconcile_processing(conn,content_ids=[1],
                version=replace(self.version,parser_version='2'),now=NOW)[0]
            self.assertNotEqual(changed['resource_key'],jobs[0]['resource_key'])
            claim = workers.claim_processing(conn,work_id=jobs[0]['work_id'],now=NOW)
        with self.assertRaises(ExtractionBusy):
            with self.session() as conn:
                workers.claim_extraction(conn, claim['resource_key'], now=NOW)

    def test_processing_unknown_id_savepoint_rolls_back_entire_batch(self):
        self.content()
        with self.session() as conn:
            conn.execute('DELETE FROM document_processing_inbox')
            with self.assertRaises(ValueError):
                workers.reconcile_processing(conn,content_ids=[1,999],version=self.version,now=NOW)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM document_processing_work').fetchone()['n'],0)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM document_processing_inbox').fetchone()['n'],0)

    def test_failed_exact_cache_is_recovered_and_never_claimed(self):
        self.content()
        with self.session() as conn:
            jobs = workers.reconcile_processing(conn,content_ids=[1],version=self.version,now=NOW)
            extraction = conn.execute('''INSERT INTO document_extractions
                (content_id,parser_name,parser_version,ocr_engine,ocr_engine_version,options_json,
                 started_at_utc,completed_at_utc,extraction_status,page_or_slide_count,native_page_count,
                 ocr_page_count,unusable_page_count,created_at_utc)
                VALUES (1,%s,%s,%s,%s,%s,%s,%s,'failed',0,0,0,0,%s) RETURNING extraction_id''',
                (*self.version.values(),NOW,NOW,NOW)).fetchone()['extraction_id']
            recovered = workers.reconcile_processing(conn,content_ids=[1],version=self.version,now=NOW)
            self.assertEqual(recovered[0]['extraction_id'],extraction)
            with self.assertRaises(ExtractionBusy):
                workers.claim_processing(conn,work_id=jobs[0]['work_id'],now=NOW)

    def test_idle_connection_and_invalid_claim_inputs_are_rejected(self):
        import psycopg
        with psycopg.connect(self.settings.dsn,autocommit=True) as idle:
            with self.assertRaisesRegex(ValueError,'managed'):
                workers.claim_extraction(idle,'same',now=NOW)
        with self.session() as conn:
            for seconds in (0,-1,True,float('nan'),float('inf')):
                with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                    workers.claim_extraction(conn,'same',seconds=seconds,now=NOW)
            with self.assertRaises(ValueError):
                workers.claim_extraction(conn,'same',now='2026-11-08T00:00:00')
            for ids in ([],[True],[0],list(range(1,102))):
                with self.subTest(ids=ids), self.assertRaises(ValueError):
                    workers.reconcile_processing(conn,content_ids=ids,version=self.version,now=NOW)
