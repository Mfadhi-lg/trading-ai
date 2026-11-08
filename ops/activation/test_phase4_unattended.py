"""Synthetic offline regression data; never production acceptance evidence."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from scrapper.tests.test_phase4_routine import RoutineTests, NOW, EPOCH, FIXTURES

CANDIDATE=Path(__file__).resolve().parent/'verify_phase4_unattended.py'
spec=importlib.util.spec_from_file_location('verifier',CANDIDATE)
verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)

class UnattendedTests(unittest.TestCase):
    def setUp(self):
        self.f=RoutineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.c=self.f.c
        from scrapper.pipeline.accepted_document_downloads import enqueue_discovered_documents
        result=self.f.page();self.aid=result['artifact_ids'][0];self.sid=result['artifact_source_ids'][0]
        enqueue_discovered_documents(self.c,epoch=EPOCH,now=NOW)
        self.f.download((FIXTURES/'d03_text.pdf').read_bytes());self.processing=self.f.process()
        self.cid=self.c.execute('SELECT content_id FROM artifact_contents').fetchone()[0]
        self.job=self.c.execute('SELECT download_job_id FROM artifact_download_queue').fetchone()[0]
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.receipt('discovery', 'DOCUMENT_DISCOVERY', {'official':{'observations':[{'company_id':self.f.f.company_id,**result}]}})
        self.receipt('download','DOCUMENT_DOWNLOAD',{'selected':[{'download_job_id':self.job}]})
        self.receipt('processing','DOCUMENT_PROCESSING',self.processing)
    def receipt(self,name,stage,result,origin='launchd',timestamp=NOW,status='PASS'):
        folder=self.root/('fixture-document-'+name);folder.mkdir(exist_ok=True)
        (folder/'admission.json').write_text(json.dumps({'dispatch_origin':origin,'started_at_utc':timestamp}))
        (folder/'outcome.json').write_text(json.dumps({'status':status}))
        (folder/'runtime.log').write_text(json.dumps({'stage':'document_stage_outcome','document_stage':stage,'protected_unchanged':True,'finished_at_utc':NOW,'result':result}))
    def verify(self,**kw):return verifier.verify(self.c,receipts=self.root,epoch=EPOCH,**kw)
    def mutate(self,sql,args=()):
        with self.c:self.c.execute(sql,args)
    def test_issuer_d01_lineage_passes_without_exchange_event(self):
        self.mutate("DELETE FROM artifact_expectations WHERE event_id IN (SELECT event_id FROM artifact_events WHERE evidence_source='exchange_result_filing')")
        self.assertEqual(self.verify()['status'],'PASS')
        self.assertEqual(self.verify()['lifecycle'][0]['evidence_source'],'d01_artifact_discovery')
    def test_exchange_lineage_still_passes(self):
        self.mutate("DELETE FROM artifact_expectations WHERE event_id IN (SELECT event_id FROM artifact_events WHERE evidence_source='d01_artifact_discovery')")
        self.assertEqual(self.verify()['status'],'PASS')
    def test_space_timestamp_epoch_day_is_compared_as_time(self):
        p=self.root/'fixture-document-download/admission.json';v=json.loads(p.read_text());v['started_at_utc']=NOW.replace('T',' ');p.write_text(json.dumps(v))
        self.assertEqual(self.verify()['status'],'PASS')
    def test_manual_or_stopped_download_cannot_pass(self):
        for origin,status in [('manual','PASS'),('launchd','STOP')]:
            self.receipt('download','DOCUMENT_DOWNLOAD',{'selected':[{'download_job_id':self.job}]},origin=origin,status=status)
            self.assertNotEqual(self.verify()['status'],'PASS')
    def test_discovery_receipt_required(self):
        self.receipt('discovery','DOCUMENT_DISCOVERY',{'selected':[]})
        self.assertNotEqual(self.verify()['status'],'PASS')
    def test_old_artifact_is_excluded(self):
        self.mutate("UPDATE document_artifacts SET first_observed_at_utc='2026-10-11T00:00:00+00:00'")
        self.assertNotEqual(self.verify()['status'],'PASS')
    def test_period_and_company_cannot_be_borrowed(self):
        original=[tuple(r) for r in self.c.execute('SELECT event_id,company_id,period_end FROM artifact_events')]
        for sql in ["UPDATE artifact_events SET period_end='2026-03-31'", "UPDATE artifact_events SET company_id=company_id+10000"]:
            with self.c:
                self.c.execute('PRAGMA foreign_keys=OFF')
                self.c.execute(sql)
            self.assertNotEqual(self.verify()['status'],'PASS')
            with self.c:
                self.c.executemany('UPDATE artifact_events SET company_id=?,period_end=? WHERE event_id=?', [(company,period,event) for event,company,period in original])
    def test_d01_event_must_reference_exact_artifact(self):
        self.mutate("DELETE FROM artifact_expectations WHERE event_id IN (SELECT event_id FROM artifact_events WHERE evidence_source='exchange_result_filing')")
        self.mutate("UPDATE artifact_events SET evidence_json='{}'")
        self.assertNotEqual(self.verify()['status'],'PASS')
    def test_unknown_event_source_excluded(self):
        self.mutate("UPDATE artifact_events SET evidence_source='manual_fixture'")
        self.assertNotEqual(self.verify()['status'],'PASS')
    def test_missing_period_excluded(self):
        self.mutate('UPDATE document_artifacts SET period_end=NULL')
        self.assertNotEqual(self.verify()['status'],'PASS')
    def test_checksum_is_verified(self):
        self.mutate("UPDATE artifact_contents SET content_sha256=?",('0'*64,))
        with self.assertRaisesRegex(RuntimeError,'original failed'):self.verify()
    def test_manual_cached_extraction_before_processing_is_excluded(self):
        self.mutate("UPDATE document_extractions SET created_at_utc='2026-10-12T04:59:00+00:00'")
        self.assertNotEqual(self.verify()['status'],'PASS')
    def test_successful_processing_outcome_required(self):
        self.receipt('processing','DOCUMENT_PROCESSING',{'selected':[{'content_id':self.cid}], 'outcomes':[]})
        self.assertNotEqual(self.verify()['status'],'PASS')
    def test_dedicated_rating_queue_uses_actual_launchd_attempt(self):
        # Offline routing fixture with real archived content and extraction.
        self.mutate("DELETE FROM artifact_expectations WHERE event_id IN (SELECT event_id FROM artifact_events WHERE evidence_source='exchange_result_filing')")
        self.mutate("UPDATE document_artifacts SET artifact_type='credit_rating'")
        self.mutate("UPDATE artifact_expectations SET artifact_type='credit_rating' WHERE artifact_type='transcript'")
        attempt=self.c.execute('SELECT * FROM artifact_download_attempts').fetchone()
        self.mutate("INSERT INTO credit_rating_download_queue(download_job_id,artifact_id,requested_url,status,created_at_utc,updated_at_utc,target_host) VALUES(100,?,?,'SUCCEEDED',?,?,'fixture.example')",(self.aid,attempt['requested_url'],NOW,NOW))
        self.mutate("INSERT INTO credit_rating_resolution_attempts(download_job_id,artifact_id,original_url,started_at_utc,completed_at_utc,resolution_status,resolver_version,final_url) VALUES(100,?,?,?,?,'DIRECT_REPORT_VALIDATED','fixture',?)",(self.aid,attempt['requested_url'],NOW,NOW,attempt['final_url']))
        self.receipt('download','DOCUMENT_DOWNLOAD',{'selected':[]})
        self.assertNotEqual(self.verify()['status'],'PASS')
        self.receipt('rating','DOCUMENT_RATING',{'processed_job_count':1})
        result=self.verify()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['lifecycle'][0]['download_queue_kind'],'rating')
    def test_expiry_bounds_admission_but_allows_completion(self):
        self.assertNotEqual(self.verify(expires=NOW)['status'],'PASS')
        self.assertEqual(self.verify(expires='2026-10-12T05:01:00+00:00')['status'],'PASS')

if __name__=='__main__':unittest.main()
