"""C21.2 real worker boundaries using disposable D03 fixtures."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import subprocess
import sys
import unittest
from scrapper.tests import test_d03_document_extraction as fixtures
from scrapper.db.database import connect
from scrapper.db.documents.processing import reconcile_processing, claim_processing
from scrapper.db.document_work_claims import ExtractionBusy, OwnershipLost, claim_extraction
from scrapper.pipeline.extract_document import processing_version, extract_document
from scrapper.operations.workers.document_processing import run_processing
from scrapper.operations.workers.lifecycle import WorkerLifecycle

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DocumentExtractionD03Tests();self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.conn=connect(self.fixture.db_path);self.addCleanup(self.conn.close)
        self.engine=fixtures.FakeOcr()
        self.version=processing_version(self.engine)

    def job(self, name='native.pdf', body=None, media='application/pdf', kind='presentation'):
        aid,cid=self.fixture._content(name,media_type=media,artifact_type=kind,body=body)
        job=reconcile_processing(self.conn,content_ids=[cid],version=self.version,now=fixtures.OBSERVED)[0]
        return aid,cid,job['work_id']

    def run_job(self, ids, **kw):
        return run_processing(db_path=self.fixture.db_path,work_ids=ids,
            ocr_engine=self.engine,output_dir=self.fixture.root/'output',
            now_utc=lambda:fixtures.OBSERVED,**kw)

    def test_real_parser_normalization_and_exact_cache(self):
        aid,cid,wid=self.job('d03_text.pdf')
        first=self.run_job([wid]); second=self.run_job([wid,wid])
        self.assertEqual(first,second)
        self.assertEqual(first[0]['extraction_status'],'ok')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT status FROM document_extraction_claims').fetchone()[0],'RELEASED')
        legacy=extract_document(artifact_id=aid,content_id=cid,db_path=self.fixture.db_path,
            ocr_engine=self.engine,output=self.fixture.root/'legacy.json',now_utc=lambda:fixtures.OBSERVED)
        self.assertTrue(legacy['reused'])
        self.assertEqual(legacy['extraction']['extraction_id'],first[0]['extraction_id'])

    def test_pdf_ocr_and_pptx_use_existing_normalization(self):
        cases=[('d03_scanned.pdf','application/pdf','transcript',
                ('SCANNED EBITDA MARGIN 24 PERCENT','0.9825')),
               ('d03_presentation.pptx','application/vnd.openxmlformats-officedocument.presentationml.presentation','presentation',
                ('IMAGE ONLY GUIDANCE RAISED','0.9700'))]
        for name,media,kind,response in cases:
            self.engine=fixtures.FakeOcr((response,))
            aid,cid,wid=self.job(name,media=media,kind=kind)
            result=self.run_job([wid])
            self.assertEqual(result[0]['extraction_status'],'ok')
            self.assertEqual(self.engine.calls,1)
            page=self.conn.execute("SELECT selected_text,selected_text_origin FROM document_pages WHERE content_id=? AND selected_text_origin='ocr'",(cid,)).fetchone()
            self.assertEqual(page['selected_text'],response[0])
            self.assertEqual(page['selected_text_origin'],'ocr')

    def test_preflight_version_mismatch_and_invalid_selection_are_inert(self):
        _,cid,wid=self.job('bad.pdf',b'bad')
        bad=reconcile_processing(self.conn,content_ids=[cid],version=replace(self.version,parser_version='future'),now=fixtures.OBSERVED)[0]['work_id']
        for ids in [[],[True],list(range(1,102)),[wid,99999],[wid,bad]]:
            with self.assertRaises(ValueError):self.run_job(ids)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extraction_claims').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],0)

    def test_busy_legacy_owner_skipped_without_double_parser(self):
        _,cid,wid=self.job('bad.pdf',b'bad')
        claim_extraction(self.conn,self.version.key(cid))
        with patch('scrapper.pipeline.extract_document.extract_pdf',side_effect=AssertionError('duplicate')):
            self.assertEqual(self.run_job([wid]),[{'work_id':wid,'state':'busy'}])

    def test_failure_recorded_and_not_retried_as_success(self):
        _,_,wid=self.job('bad.pdf',b'bad')
        with patch('scrapper.pipeline.extract_document.extract_pdf',side_effect=RuntimeError('parser failure')) as parser:
            first=self.run_job([wid]);second=self.run_job([wid])
        self.assertEqual(parser.call_count,1);self.assertEqual(first,second)
        self.assertEqual(first[0]['extraction_status'],'failed')

    def test_crash_after_persist_before_export_restart_no_duplicate(self):
        _,_,wid=self.job('bad.pdf',b'bad')
        original=Path.write_text
        def fail(path,*a,**k):
            if path.name==f'work-{wid}.json':raise OSError('export crash')
            return original(path,*a,**k)
        with patch.object(Path,'write_text',fail):
            with self.assertRaises(OSError):self.run_job([wid])
        self.assertIsNone(self.conn.execute('SELECT extraction_id FROM document_processing_work').fetchone()[0])
        result=self.run_job([wid])
        self.assertEqual(result[0]['extraction_status'],'failed')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],1)

    def test_missing_bytes_release_owned_work_pending(self):
        _,_,wid=self.job('bad.pdf',b'bad')
        path=self.conn.execute('SELECT local_path FROM response_blobs').fetchone()[0]
        Path(path).unlink()
        with self.assertRaises(RuntimeError):self.run_job([wid])
        self.assertIsNone(self.conn.execute('SELECT extraction_id FROM document_processing_work').fetchone()[0])
        self.assertEqual(self.conn.execute('SELECT status FROM document_extraction_claims').fetchone()[0],'RELEASED')

    def test_expired_worker_cannot_persist_or_release_new_owner(self):
        _,cid,wid=self.job('bad.pdf',b'bad')
        newer=[]
        def parser(*a,**k):
            other=connect(self.fixture.db_path)
            try:
                with other:other.execute("UPDATE document_extraction_claims SET lease_expires_at_utc='2000-01-01T00:00:00+00:00'")
                newer.append(claim_processing(other,work_id=wid))
            finally:other.close()
            raise RuntimeError('parse interrupted')
        with patch('scrapper.pipeline.extract_document.extract_pdf',parser):
            with self.assertRaises(OwnershipLost):self.run_job([wid])
        row=self.conn.execute('SELECT * FROM document_extraction_claims').fetchone()
        self.assertEqual(row['claim_token'],newer[0]['claim_token']);self.assertEqual(row['status'],'ACTIVE')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],0)

    def test_drain_finishes_owned_job_then_stops(self):
        _,_,a=self.job('a.pdf',b'a');_,_,b=self.job('b.pdf',b'b')
        life=WorkerLifecycle()
        def parser(*args,**kw):
            life.set();raise RuntimeError('fixture failed extraction')
        with patch('scrapper.pipeline.extract_document.extract_pdf',parser):result=self.run_job([a,b],lifecycle=life)
        self.assertEqual(len(result),1);self.assertEqual(life.completed,1)
        self.assertIsNone(life.active_job)
        self.assertIsNone(self.conn.execute('SELECT extraction_id FROM document_processing_work WHERE work_id=?',(b,)).fetchone()[0])

    def test_no_write_transaction_during_parser_and_no_schema_application(self):
        _,_,wid=self.job('bad.pdf',b'bad')
        def parser(*args,**kw):
            other=connect(self.fixture.db_path)
            try:other.execute('BEGIN IMMEDIATE');other.rollback()
            finally:other.close()
            raise RuntimeError('fixture')
        with patch('scrapper.pipeline.extract_document.init_db',side_effect=AssertionError('migration')),patch('scrapper.pipeline.extract_document.extract_pdf',parser):
            self.run_job([wid])

    def test_html_origin_and_unsupported_audio_stay_honest(self):
        _,_,wid=self.job('rating.html',b'<html><body>Fixture rating text</body></html>',media='text/html',kind='credit_rating')
        self.run_job([wid])
        aid,cid=self.fixture._content('audio.mp3',media_type='audio/mpeg',artifact_type='recording',body=b'audio')
        self.assertEqual(reconcile_processing(self.conn,content_ids=[cid],version=self.version,now=fixtures.OBSERVED),[])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_work').fetchone()[0],1)

    def test_process_death_after_claim_leaves_recoverable_work(self):
        _,_,wid=self.job('bad.pdf',b'bad')
        code="""import sys,os
from pathlib import Path
from scrapper.db.database import connect
from scrapper.db.documents.processing import claim_processing
c=connect(Path(sys.argv[1]));claim_processing(c,work_id=int(sys.argv[2]),seconds=1,now='2000-01-01T00:00:00+00:00');os._exit(9)
"""
        result=subprocess.run([sys.executable,'-c',code,str(self.fixture.db_path),str(wid)])
        self.assertEqual(result.returncode,9)
        self.assertEqual(self.run_job([wid])[0]['extraction_status'],'failed')

    def test_import_inert(self):
        code="""from unittest.mock import patch
with patch('sqlite3.connect',side_effect=AssertionError('DB')),patch('threading.Thread.start',side_effect=AssertionError('thread')),patch('pathlib.Path.mkdir',side_effect=AssertionError('FS')),patch('socket.socket',side_effect=AssertionError('network')):
 import scrapper.operations.workers.document_processing
"""
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
