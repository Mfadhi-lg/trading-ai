"""Existing document APIs on isolated PostgreSQL schemas, with synthetic evidence."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier
import unittest

from scrapper.db import document_extraction_repo as repository
from scrapper.db import document_work_claims as claims
from scrapper.db.documents import processing
from scrapper.documents.extraction import (
    DOCUMENT_PARSER_NAME, DOCUMENT_PARSER_VERSION, DocumentElement,
    DocumentPage, ExtractedDocument,
)
from scrapper.tests.test_postgresql_schema import synthetic_sqlite
from scrapper.tests import test_postgresql_workers as worker_fixtures

NOW = worker_fixtures.NOW


def extracted():
    text = 'Synthetic ₹ exact amount 000123.00000000000000000001'
    return ExtractedDocument('application/pdf', (
        DocumentPage(1,'page',text,'',text,'native',True,None,'native',
            layout={'original': '₹'}, elements=(
                DocumentElement(0,'text_block','native',text_raw=text),
                DocumentElement(1,'table','native',rows=(('Value','000123.00000000000000000001'),)),
            )),
        DocumentPage(2,'page','','OCR source','OCR source','ocr',False,'absent','ocr',
            elements=(DocumentElement(0,'text_block','ocr',text_raw='OCR source'),)),
    ))


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'),
                     'requires explicitly disposable PostgreSQL 16 DSN')
class ExtractionIntegrationTests(unittest.TestCase):
    drop_schema = worker_fixtures.WorkerIntegrationTests.drop_schema
    session = worker_fixtures.WorkerIntegrationTests.session
    content = worker_fixtures.WorkerIntegrationTests.content

    def setUp(self):
        worker_fixtures.WorkerIntegrationTests.setUp(self)
        self.version = processing.ProcessingVersion.create(parser_name=DOCUMENT_PARSER_NAME,
            parser_version=DOCUMENT_PARSER_VERSION,ocr_engine='synthetic',
            ocr_engine_version='1',options={'language':'eng'})
        self.now = datetime.now(timezone.utc).isoformat()
        self.content()

    def persist(self, conn, *, claim=None, document=None, content_id=1):
        return repository.persist_extraction(conn, content_id=content_id,
            extracted=document or extracted(),ocr_engine='synthetic',ocr_engine_version='1',
            options={'language':'eng'},started_at_utc=NOW,completed_at_utc=NOW,claim=claim)

    def test_existing_claim_persist_payload_and_reconcile_match_sqlite(self):
        source = synthetic_sqlite()
        import sqlite3
        source.row_factory = sqlite3.Row
        self.addCleanup(source.close)
        with self.session() as conn:
            # Copy only synthetic fixture rows to an in-memory SQLite reference.
            for table in ('companies','response_blobs','document_artifacts','artifact_contents'):
                for row in conn.execute('SELECT * FROM ' + table):
                    columns = ','.join(row)
                    source.execute('INSERT INTO '+table+'('+columns+') VALUES ('+
                        ','.join('?' for _ in row)+')',tuple(row.values()))
            source.commit()
            jobs = processing.reconcile_processing(conn,content_ids=[1],version=self.version,now=NOW)
            claim = processing.claim_processing(conn,work_id=jobs[0]['work_id'],now=self.now)
        with self.session() as conn:
            claims.renew_extraction(conn,claim,now=self.now)
            result = self.persist(conn,claim=claim)
            payload = repository.extraction_payload(conn,result.extraction_id)
            self.assertEqual(conn.execute('SELECT status FROM document_extraction_claims').fetchone()['status'],'RELEASED')
            recovered = processing.reconcile_processing(conn,content_ids=[1],version=self.version,now=NOW)
            self.assertEqual(recovered[0]['extraction_id'],result.extraction_id)
            with self.assertRaises(claims.ExtractionBusy):
                processing.claim_processing(conn,work_id=jobs[0]['work_id'],now=self.now)
            self.assertTrue(self.persist(conn).reused)
        expected = self.persist(source)
        self.assertEqual(result,expected)
        self.assertEqual(payload,repository.extraction_payload(source,expected.extraction_id))
        self.assertEqual((result.native_page_count,result.ocr_page_count),(1,1))

    def test_stale_claim_cannot_persist_or_release_replacement(self):
        earlier = (datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat()
        with self.session() as conn:
            first = claims.claim_extraction(conn,self.version.key(1),seconds=1,now=earlier)
        with self.session() as conn:
            newer = claims.claim_extraction(conn,self.version.key(1),now=self.now)
        with self.session() as conn:
            with self.assertRaises(claims.OwnershipLost):
                self.persist(conn,claim=first)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM document_extractions').fetchone()['n'],0)
            claims.assert_extraction_owned(conn,newer,now=self.now)
            self.assertEqual(claims.release_extraction(conn,first),0)

    def test_constraint_failure_rolls_back_every_write_and_keeps_claim(self):
        from psycopg.errors import CheckViolation
        with self.session() as conn:
            claim = claims.claim_extraction(conn,self.version.key(1),now=self.now)
        bad = extracted()
        bad = replace(bad,pages=(replace(bad.pages[0],elements=(
            replace(bad.pages[0].elements[0],kind='not_a_supported_kind'),)),*bad.pages[1:]))
        with self.session() as conn:
            with self.assertRaises(CheckViolation):
                self.persist(conn,claim=claim,document=bad)
            for table in ('document_extractions','document_pages','document_page_elements','document_extraction_validations'):
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM '+table).fetchone()['n'],0)
            claims.assert_extraction_owned(conn,claim,now=self.now)
            self.assertEqual(conn.execute('SELECT extraction_status FROM artifact_contents').fetchone()['extraction_status'],'not_started')
            self.assertIsNone(conn.execute('SELECT preferred_content_id FROM document_artifacts').fetchone()['preferred_content_id'])
            self.assertEqual(self.persist(conn,claim=claim).extraction_status,'ok')

    def test_concurrent_unclaimed_persistence_reuses_one_complete_cache(self):
        ready = Barrier(2)
        def persist(_):
            with self.session() as conn:
                ready.wait(timeout=5)
                return self.persist(conn)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(persist,range(2)))
        self.assertEqual(len({result.extraction_id for result in results}),1)
        self.assertCountEqual([result.reused for result in results],[True,False])
        with self.session() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM document_extractions').fetchone()['n'],1)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM document_pages').fetchone()['n'],2)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM document_page_elements').fetchone()['n'],3)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM document_extraction_validations').fetchone()['n'],5)

    def test_preferred_content_lock_preserves_first_successful_content(self):
        from psycopg.errors import LockNotAvailable
        self.content(2)
        with self.session() as conn:
            conn.execute('UPDATE artifact_contents SET artifact_id=1 WHERE content_id=2')
        with self.session() as first:
            self.persist(first,content_id=1)
            with self.assertRaises(LockNotAvailable):
                with self.session() as second:
                    second.execute("SET LOCAL lock_timeout='100ms'")
                    self.persist(second,content_id=2)
        with self.session() as second:
            self.assertFalse(self.persist(second,content_id=2).reused)
            preferred = second.execute('SELECT preferred_content_id FROM document_artifacts WHERE artifact_id=1').fetchone()
            self.assertEqual(preferred['preferred_content_id'],1)
            self.assertEqual(second.execute('SELECT count(*) AS n FROM document_extractions').fetchone()['n'],2)

    def test_outer_rollback_undoes_success_and_claim_release(self):
        with self.session() as conn:
            claim = claims.claim_extraction(conn,self.version.key(1),now=self.now)
        with self.assertRaisesRegex(RuntimeError,'synthetic rollback'):
            with self.session() as conn:
                self.persist(conn,claim=claim)
                raise RuntimeError('synthetic rollback')
        with self.session() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM document_extractions').fetchone()['n'],0)
            claims.assert_extraction_owned(conn,claim,now=self.now)
