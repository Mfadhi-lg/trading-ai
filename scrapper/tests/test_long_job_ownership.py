"""C19.3 real SQLite races, active renewal, stale writes and process death."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from scrapper.db import document_work_claims as claims
from scrapper.operations.workers.renewal import LeaseRenewal, extraction_renewal
from scrapper.tests import test_d03_document_extraction as fixtures
from scrapper.pipeline import extract_document as pipeline
from scrapper.db.document_extraction_repo import persist_extraction

NOW = '2026-10-05T10:00:00+00:00'
LATER = '2026-10-05T10:00:02+00:00'

class LongJobOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'claims.db'
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        root = Path(claims.__file__).parent/'migrations'
        self.conn.executescript((root/'070_document_extraction_claims.sql').read_text())

    def other(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def test_two_connections_exclusive_then_expiry_reclaim_fences_old(self):
        first = claims.claim_extraction(self.conn,'content-version-options',seconds=1,now=NOW)
        other = self.other()
        with self.assertRaises(claims.ExtractionBusy):
            claims.claim_extraction(other,first['resource_key'],now=NOW)
        newer = claims.claim_extraction(other,first['resource_key'],now=LATER)
        with self.assertRaises(claims.OwnershipLost):
            claims.assert_extraction_owned(self.conn,first,now=LATER)
        with self.assertRaises(claims.OwnershipLost):
            claims.renew_extraction(self.conn,first,now=LATER)
        with self.conn:
            self.assertEqual(claims.release_extraction(self.conn,first),0)
        claims.assert_extraction_owned(other,newer,now=LATER)
        self.assertFalse(self.conn.in_transaction)
        self.assertNotEqual(first['claim_token'],newer['claim_token'])

    def test_expired_extraction_cannot_resurrect_without_reclaim(self):
        first = claims.claim_extraction(self.conn,'key',seconds=1,now=NOW)
        with self.assertRaises(claims.OwnershipLost):
            claims.renew_extraction(self.conn,first,now=LATER)
        self.assertEqual(dict(self.conn.execute('SELECT * FROM document_extraction_claims').fetchone()),first)

    def test_queue_renewal_preserves_counts_and_refuses_all_stale_variants(self):
        for kind,(table,key,status) in claims.QUEUES.items():
            with self.subTest(kind=kind):
                self.conn.execute(f'''CREATE TABLE {table}({key} INTEGER,status TEXT,
                    lease_owner TEXT,claim_token TEXT,attempt_count INTEGER,lease_expires_at_utc TEXT)''')
                self.conn.execute(f"INSERT INTO {table} VALUES(1,?,'owner','token',2,?)",(status,LATER))
                self.conn.commit()
                job={key:1,'lease_owner':'owner','claim_token':'token','attempt_count':1 if kind=='browser' else 2}
                claims.renew_queue(self.conn,kind,job,seconds=10,now=NOW)
                row = tuple(self.conn.execute(f'SELECT * FROM {table}').fetchone())
                self.assertEqual(row[:5],(1,status,'owner','token',2))
                for field,value in [('lease_owner','other'),('claim_token','old'),('attempt_count',0)]:
                    with self.assertRaises(claims.OwnershipLost):
                        claims.renew_queue(self.conn,kind,{**job,field:value},seconds=10,now=NOW)
                    self.assertEqual(row,tuple(self.conn.execute(f'SELECT * FROM {table}').fetchone()))
                with self.assertRaises(claims.OwnershipLost):
                    claims.renew_queue(self.conn,kind,job,seconds=10,now='2026-10-05T10:00:11+00:00')

    def test_background_renewal_outlives_initial_lease_and_stops(self):
        first = claims.claim_extraction(self.conn,'long-ocr',seconds=1)
        with extraction_renewal(self.conn,first,seconds=1) as renewal:
            time.sleep(1.4)
            renewal.check()
            with self.assertRaises(claims.ExtractionBusy):
                claims.claim_extraction(self.other(),'long-ocr')
            claims.assert_extraction_owned(self.conn,first)
            self.assertFalse(self.conn.in_transaction)
            # The callback connection never belongs to the OCR/main thread.
            self.assertTrue(renewal.thread.is_alive())
        self.assertFalse(renewal.thread.is_alive())
        prior=tuple(self.conn.execute('SELECT * FROM document_extraction_claims').fetchone())
        time.sleep(.4)
        self.assertEqual(prior,tuple(self.conn.execute('SELECT * FROM document_extraction_claims').fetchone()))

    def test_renewal_failure_is_visible_and_thread_cleans_up(self):
        def fail(conn):
            self.assertFalse(conn.in_transaction)
            raise sqlite3.OperationalError('controlled storage fault')
        with LeaseRenewal(self.conn,fail,seconds=.15) as renewal:
            renewal.thread.join(2)
            with self.assertRaises(claims.OwnershipLost):renewal.check()
        self.assertFalse(renewal.thread.is_alive())

    def test_signal_drain_does_not_stop_active_job_renewal(self):
        from scrapper.operations.workers.lifecycle import WorkerLifecycle
        first=claims.claim_extraction(self.conn,'drain',seconds=1)
        life=WorkerLifecycle();life.claimed('drain')
        with extraction_renewal(self.conn,first,seconds=1) as renewal:
            life.set();time.sleep(1.1);renewal.check()
            claims.assert_extraction_owned(self.conn,first)
            self.assertTrue(life.is_set())
        life.finished()

    def test_killed_process_leaves_reclaimable_claim_without_output(self):
        code='''import sqlite3,sys,time,json
from scrapper.db.document_work_claims import claim_extraction
c=sqlite3.connect(sys.argv[1]);c.row_factory=sqlite3.Row
print(json.dumps(claim_extraction(c,'killed',seconds=1)),flush=True)
time.sleep(30)
'''
        proc=subprocess.Popen([sys.executable,'-B','-c',code,str(self.path)],stdout=subprocess.PIPE,text=True)
        try:
            first=json.loads(proc.stdout.readline())
            proc.kill();proc.wait(timeout=3)
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
            proc.stdout.close()
        expiry=first['lease_expires_at_utc']
        newer=claims.claim_extraction(self.conn,'killed',now=expiry)
        self.assertNotEqual(first['claim_token'],newer['claim_token'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extraction_claims').fetchone()[0],1)

    def fixture(self):
        f=fixtures.DocumentExtractionD03Tests();f.setUp();self.addCleanup(f.tearDown)
        artifact,content=f._content('d03_text.pdf',media_type='application/pdf',artifact_type='presentation')
        return f,artifact,content

    def test_extraction_race_and_no_write_transaction_during_parser(self):
        f,artifact,content=self.fixture()
        original=pipeline.extract_pdf
        def parser(*args,**kwargs):
            other=sqlite3.connect(f.db_path);other.row_factory=sqlite3.Row
            try:
                # A competing writer succeeds while parser/OCR runs.
                with other:other.execute("UPDATE companies SET status='active'")
                row=dict(other.execute('SELECT * FROM document_extraction_claims').fetchone())
                with self.assertRaises(claims.ExtractionBusy):
                    pipeline.extract_document(artifact_id=artifact,content_id=content,db_path=f.db_path,
                        ocr_engine=fixtures.FakeOcr(),output=f.root/'second.json')
                claims.assert_extraction_owned(other,row)
            finally:other.close()
            return original(*args,**kwargs)
        with patch.object(pipeline,'extract_pdf',side_effect=parser):
            result=pipeline.extract_document(artifact_id=artifact,content_id=content,db_path=f.db_path,
                ocr_engine=fixtures.FakeOcr(),output=f.root/'first.json')
        again=pipeline.extract_document(artifact_id=artifact,content_id=content,db_path=f.db_path,
            ocr_engine=fixtures.FakeOcr(),output=f.root/'cached.json')
        self.assertTrue(again['reused'])
        self.assertEqual(result['extraction'],again['extraction'])

    def test_stale_parser_output_refuses_persistence_and_preserves_new_owner(self):
        f,artifact,content=self.fixture()
        original=pipeline.extract_pdf
        captured={}
        def parser(*args,**kwargs):
            other=sqlite3.connect(f.db_path);other.row_factory=sqlite3.Row
            try:
                old=dict(other.execute('SELECT * FROM document_extraction_claims').fetchone())
                with other:other.execute("UPDATE document_extraction_claims SET lease_expires_at_utc='2000-01-01T00:00:00+00:00'")
                captured.update(claims.claim_extraction(other,old['resource_key']))
            finally:other.close()
            return original(*args,**kwargs)
        with patch.object(pipeline,'extract_pdf',side_effect=parser),self.assertRaises(claims.OwnershipLost):
            pipeline.extract_document(artifact_id=artifact,content_id=content,db_path=f.db_path,
                ocr_engine=fixtures.FakeOcr(),output=f.root/'stale.json')
        conn=sqlite3.connect(f.db_path);conn.row_factory=sqlite3.Row
        try:
            self.assertEqual(conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT count(*) FROM document_pages').fetchone()[0],0)
            claims.assert_extraction_owned(conn,captured)
            self.assertFalse((f.root/'stale.json').exists())
        finally:conn.close()

    def test_atomic_extraction_rollback_restores_claim_and_no_partial_output(self):
        f,artifact,content=self.fixture()
        conn=sqlite3.connect(f.db_path);conn.row_factory=sqlite3.Row
        try:
            claim=claims.claim_extraction(conn, pipeline.canonical_json([content, pipeline.DOCUMENT_PARSER_NAME, pipeline.DOCUMENT_PARSER_VERSION, 'fake', '1', pipeline.canonical_json({})]))
            conn.execute("CREATE TRIGGER extraction_fault BEFORE INSERT ON document_extraction_validations BEGIN SELECT RAISE(ABORT,'controlled fault'); END")
            conn.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                persist_extraction(conn,content_id=content,extracted=None,ocr_engine='fake',
                    ocr_engine_version='1',options={},started_at_utc=NOW,completed_at_utc=NOW,claim=claim)
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],0)
            claims.assert_extraction_owned(conn,claim)
        finally:conn.close()

    def test_browser_contention_keeps_processing_eligible(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from scrapper.documents.recovery.browser_rating import processing
        args=SimpleNamespace(run_id='run',limit=1,follow=False,db=self.path)
        job={'artifact_id':1,'content_id':1,'browser_job_id':1,'evidence_json':'{}'}
        for error in (claims.ExtractionBusy('owned'),claims.OwnershipLost('lost')):
            with self.subTest(error=type(error).__name__),patch.object(processing._repository,'next_processing_job',return_value=job),patch.object(processing._repository,'save_processing_evidence') as save,patch.object(pipeline,'extract_document',side_effect=error):
                processing.process_reports(self.conn,args,'owner',STOP=SimpleNamespace(is_set=lambda:False),worker_state=Mock(),emit=Mock())
                save.assert_not_called()


    def test_cache_contract_separates_real_ocr_versions_and_options(self):
        f,artifact,content=self.fixture()
        for version,language in [('1','eng'),('2','eng'),('1','hin')]:
            engine=fixtures.FakeOcr();engine.version=version;engine.language=language
            result=pipeline.extract_document(artifact_id=artifact,content_id=content,db_path=f.db_path,
                ocr_engine=engine,output=f.root/(version+language+'.json'))
            self.assertFalse(result['reused'])
        conn=sqlite3.connect(f.db_path)
        try:
            self.assertEqual(conn.execute('SELECT count(*) FROM document_extraction_claims').fetchone()[0],3)
            self.assertEqual(conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],3)
        finally:conn.close()

    def test_new_owners_import_without_threads_or_writes(self):
        code="""import sqlite3,socket,threading,subprocess,os
from unittest.mock import patch
def fail(*a,**k):raise AssertionError('import side effect')
with patch.object(sqlite3,'connect',fail),patch.object(threading.Thread,'start',fail),patch.object(socket.socket,'connect',fail),patch.object(subprocess,'Popen',fail),patch.object(os,'mkdir',fail):
 import scrapper.db.document_work_claims
 import scrapper.operations.workers.renewal
 import scrapper.pipeline.extract_document
"""
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
