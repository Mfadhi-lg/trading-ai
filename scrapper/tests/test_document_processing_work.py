"""C21.1 crash boundaries, exact cache identity, and shared extraction fences."""
import sqlite3
import unittest
from dataclasses import replace
from scrapper.db.database import connect
from scrapper.db.documents.processing import ProcessingVersion, reconcile_processing, claim_processing
from scrapper.db.document_work_claims import ExtractionBusy, OwnershipLost, assert_extraction_owned
from scrapper.db.document_extraction_repo import persist_extraction
from scrapper.tests import test_d03_document_extraction as fixtures
OBSERVED = fixtures.OBSERVED
from scrapper.documents.extraction import DOCUMENT_PARSER_NAME, DOCUMENT_PARSER_VERSION

NOW = '2026-10-05T10:00:00+00:00'
LATER = '2026-10-05T10:00:02+00:00'

class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DocumentExtractionD03Tests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.conn = connect(self.fixture.db_path)
        self.addCleanup(self.conn.close)
        self.version = ProcessingVersion.create(parser_name=DOCUMENT_PARSER_NAME,
            parser_version=DOCUMENT_PARSER_VERSION, ocr_engine='fixture',
            ocr_engine_version='1', options={'language':'eng'})

    def content(self, name='saved.pdf', media='application/pdf', kind='presentation'):
        return self.fixture._content(name, media_type=media, artifact_type=kind, body=name.encode())

    def reconcile(self, ids, version=None):
        return reconcile_processing(self.conn, content_ids=ids, version=version or self.version, now=NOW)

    def test_saved_content_commits_inbox_before_enqueue_and_restart(self):
        _, cid = self.content()
        self.assertEqual(self.conn.execute('SELECT content_id FROM document_processing_inbox').fetchone()[0],cid)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_work').fetchone()[0],0)
        other = connect(self.fixture.db_path)
        try:
            jobs = reconcile_processing(other,content_ids=[cid],version=self.version,now=NOW)
        finally:
            other.close()
        self.assertIsNone(jobs[0]['extraction_id'])
        self.assertEqual(self.reconcile([cid,cid]),jobs)

    def test_registration_rollback_also_rolls_back_inbox(self):
        _, cid = self.content()
        before = self.conn.execute('SELECT count(*) FROM document_processing_inbox').fetchone()[0]
        self.conn.execute('''INSERT INTO response_blobs
            (content_sha256,content_type,byte_size,local_path,first_observed_at_utc,created_at_utc)
            VALUES ('different','application/pdf',1,'/tmp/unused',?,?)''',(OBSERVED,OBSERVED))
        blob = self.conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        self.conn.execute('''INSERT INTO artifact_contents
            (artifact_id,blob_id,media_type,content_sha256,byte_size,extraction_status,
             is_official,content_origin,created_at_utc)
            SELECT artifact_id,?,media_type,'different',1,'not_started',
            0,'generated_derivative',created_at_utc FROM artifact_contents WHERE content_id=?''',(blob,cid))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_inbox').fetchone()[0],before+1)
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_inbox').fetchone()[0],before)

    def test_historic_selected_content_recovers_missing_inbox_no_full_scan(self):
        _, cid = self.content(); _, other = self.content('other.pdf')
        with self.conn:
            self.conn.execute('DELETE FROM document_processing_inbox')
        self.reconcile([cid])
        self.assertEqual([r[0] for r in self.conn.execute('SELECT content_id FROM document_processing_inbox')],[cid])
        self.assertEqual([r[0] for r in self.conn.execute('SELECT content_id FROM document_processing_work')],[cid])

    def test_exact_version_options_key_and_canonical_order(self):
        _, cid = self.content()
        first = self.reconcile([cid])[0]
        for field in ['parser_name','parser_version','ocr_engine','ocr_engine_version','options_json']:
            changed = replace(self.version, **{field: 'different' if field!='options_json' else '{"language":"hin"}'})
            self.assertNotEqual(self.reconcile([cid],changed)[0]['work_id'],first['work_id'])
        v = ProcessingVersion.create(parser_name='p',parser_version='1',ocr_engine='o',ocr_engine_version='1',options={'b':2,'a':1})
        w = ProcessingVersion.create(parser_name='p',parser_version='1',ocr_engine='o',ocr_engine_version='1',options={'a':1,'b':2})
        self.assertEqual(v,w)

    def test_expiry_restart_stale_finish_and_legacy_exclusion(self):
        _, cid = self.content(); job=self.reconcile([cid])[0]
        first=claim_processing(self.conn,work_id=job['work_id'],seconds=1,now=NOW)
        other=connect(self.fixture.db_path)
        try:
            with self.assertRaises(ExtractionBusy):
                claim_processing(other,work_id=job['work_id'],now=NOW)
            newer=claim_processing(other,work_id=job['work_id'],now=LATER)
            with self.assertRaises(OwnershipLost):
                assert_extraction_owned(self.conn,first,now=LATER)
            assert_extraction_owned(other,newer,now=LATER)
            from scrapper.db.document_work_claims import claim_extraction
            with self.assertRaises(ExtractionBusy):
                claim_extraction(self.conn,job['resource_key'],now=LATER)
            self.assertFalse(other.in_transaction)
        finally:
            other.close()

    def test_crash_after_failed_extraction_persist_recovers_actual_outcome(self):
        _, cid=self.content(); job=self.reconcile([cid])[0]
        claim=claim_processing(self.conn,work_id=job['work_id'])
        result=persist_extraction(self.conn,content_id=cid,extracted=None,ocr_engine='fixture',
            ocr_engine_version='1',options={'language':'eng'},started_at_utc=OBSERVED,
            completed_at_utc=OBSERVED,error={'message':'fixture failure'},claim=claim)
        self.assertIsNone(self.conn.execute('SELECT extraction_id FROM document_processing_work').fetchone()[0])
        recovered=self.reconcile([cid])[0]
        self.assertEqual(recovered['extraction_id'],result.extraction_id)
        self.assertEqual(result.extraction_status,'failed')
        with self.assertRaises(ExtractionBusy):claim_processing(self.conn,work_id=job['work_id'])
        changed=self.reconcile([cid],replace(self.version,parser_version='next'))[0]
        self.assertIsNone(changed['extraction_id'])

    def test_media_and_origin_are_not_fabricated_success(self):
        ids=[]
        for name,media,kind in [('audio','audio/mpeg','recording'),('html','text/html','presentation'),('rating','text/html','credit_rating')]:
            _,cid=self.content(name,media,kind);ids.append(cid)
        jobs=self.reconcile(ids)
        self.assertEqual([j['content_id'] for j in jobs],[ids[-1]])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_inbox').fetchone()[0],3)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],0)

    def test_invalid_scope_rolls_back_and_missing_schema_refuses(self):
        _,cid=self.content()
        for ids in [[],list(range(1,102)),[0],[True]]:
            with self.assertRaises(ValueError):self.reconcile(ids)
        with self.assertRaises(ValueError):self.reconcile([cid,99999])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_work').fetchone()[0],0)
        with self.conn:
            self.conn.execute('DROP TABLE document_processing_work')
        with self.assertRaises(sqlite3.OperationalError):self.reconcile([cid])
        self.assertFalse(self.conn.in_transaction)

    def test_process_death_after_durable_save_before_enqueue(self):
        import subprocess, sys
        _,cid=self.content()
        with self.conn:
            self.conn.execute('DELETE FROM document_processing_inbox')
        code="""import sqlite3,sys,os
c=sqlite3.connect(sys.argv[1])
c.execute('INSERT INTO document_processing_inbox SELECT content_id,created_at_utc FROM artifact_contents WHERE content_id=?',(int(sys.argv[2]),))
c.commit()
os._exit(9)
"""
        result=subprocess.run([sys.executable,'-c',code,str(self.fixture.db_path),str(cid)])
        self.assertEqual(result.returncode,9)
        self.assertEqual(self.reconcile([cid])[0]['content_id'],cid)

    def test_import_is_inert(self):
        import subprocess, sys
        code="""from unittest.mock import patch
with patch('sqlite3.connect',side_effect=AssertionError('DB write')), patch('pathlib.Path.mkdir',side_effect=AssertionError('directory creation')):
 import scrapper.db.documents.processing
"""
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
