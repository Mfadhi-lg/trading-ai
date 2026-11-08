"""Offline official intake, revised immutable bytes and routine handoff."""
import json
import unittest
from pathlib import Path
from scrapper.tests.test_d03_document_extraction import DocumentExtractionD03Tests,FakeOcr,FIXTURES
from scrapper.tests.test_document_download_queue import FakeSession,FakeResponse,FakeHealth
from scrapper.db.database import connect
from scrapper.db.documents.expectations import ensure_result_artifact_expectations
from scrapper.pipeline.accepted_document_downloads import enqueue_discovered_documents
from scrapper.pipeline.run_document_download_worker import run_document_download_worker
from scrapper.operations.workers.automatic_documents import select_downloads
from scrapper.operations.workers.document_processing import run_automatic_processing
from scrapper.sources.documents.routine import ingest_page,refresh_official_pages,revision_patrol
from scrapper.documents.late_publications import extend_exhausted_waits
from scrapper.documents.revisions import enqueue_revision

NOW='2026-10-12T05:00:00+00:00';EPOCH='2026-10-12T00:00:00+00:00'
PAGE='https://fixture.example/investors';URL='https://fixture.example/transcript.pdf'

class RoutineTests(unittest.TestCase):
    def setUp(self):
        self.f=DocumentExtractionD03Tests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.c=connect(self.f.db_path);self.addCleanup(self.c.close)
        with self.c:
            self.c.execute("INSERT INTO screener_collection_scope(company_path,company_id,eligibility,floor_crore,observed_at_utc,evidence_json,next_check_at_utc) VALUES (?,?,'eligible','580',?,'{}',?)",('/company/FIXTURE/',self.f.company_id,NOW,NOW))
        self.expectations=ensure_result_artifact_expectations(self.c,company_id=self.f.company_id,filing_id=1,
            exchange='NSE',exchange_key='fixture',result_period='2026-06',period_end='2026-06-30',
            broadcast_at_utc='2026-08-01T00:00:00+00:00',created_at_utc=NOW)

    def page(self,published='2026-10-12T04:00:00+00:00',period='2026-06-30'):
        body=f'<a href="{URL}" data-period-end="{period}" data-published-at-utc="{published}">Earnings transcript</a>'.encode()
        return ingest_page(self.c,company_id=self.f.company_id,source='company_ir',url=PAGE,body=body,now=NOW,snapshot_root=self.f.root/'pages')

    def test_exact_recovery_admits_old_job_without_changing_routine_policy(self):
        self.page();enqueue_discovered_documents(self.c,epoch=EPOCH,now=NOW)
        with self.c:self.c.execute("UPDATE artifact_download_queue SET created_at_utc='2026-08-01T00:00:00+00:00'")
        before=[tuple(r) for r in self.c.execute('SELECT * FROM artifact_download_queue')]
        jid=self.c.execute('SELECT download_job_id FROM artifact_download_queue').fetchone()[0]
        self.assertEqual(select_downloads(self.c,now=NOW,epoch=EPOCH),[])
        selected=select_downloads(self.c,now=NOW,epoch=EPOCH,recovery_job_ids=(jid,))
        self.assertEqual([r['download_job_id'] for r,_ in selected],[jid])
        self.assertEqual([tuple(r) for r in self.c.execute('SELECT * FROM artifact_download_queue')],before)
        with self.c:self.c.execute("UPDATE artifact_download_queue SET next_attempt_at_utc='2027-01-01T00:00:00+00:00'")
        self.assertEqual(select_downloads(self.c,now=NOW,epoch=EPOCH,recovery_job_ids=(jid,)),[])

    def test_recovery_preserves_publisher_and_already_stored_exclusions(self):
        self.page();enqueue_discovered_documents(self.c,epoch=EPOCH,now=NOW)
        jid=self.c.execute('SELECT download_job_id FROM artifact_download_queue').fetchone()[0]
        with self.c:
            self.c.execute("INSERT INTO credit_rating_host_policy(host,automation_status,policy_basis,reviewed_at_utc) VALUES ('blocked.fixture.example','BLOCKED_TERMS','Fixture policy',?)",(NOW,))
            self.c.execute("UPDATE artifact_download_queue SET target_host='blocked.fixture.example'")
        self.assertEqual(select_downloads(self.c,now=NOW,epoch=EPOCH,recovery_job_ids=(jid,)),[])
        with self.assertRaises(ValueError):
            select_downloads(self.c,now=NOW,epoch=EPOCH,recovery_job_ids=(jid,jid))

    def test_finite_campaign_pins_url_identity_and_expiry(self):
        from scrapper.documents.recovery.finite_downloads import validate_campaign
        self.page();enqueue_discovered_documents(self.c,epoch=EPOCH,now=NOW)
        job=dict(self.c.execute('SELECT q.*,a.company_id,a.period_end FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id)').fetchone())
        campaign={'version':1,'campaign_id':'fixture','jobs':[job],'expires_at_utc':'2026-11-01T00:00:00+00:00',
            'max_requests':1,'max_response_bytes':10485760,'max_redirects':0}
        self.assertEqual(validate_campaign(self.c,campaign,now=NOW),(job['download_job_id'],))
        with self.assertRaisesRegex(ValueError,'expired'):validate_campaign(self.c,campaign,now='2026-12-01T00:00:00+00:00')
        campaign['jobs']=[dict(job,requested_url='https://fixture.example/other.pdf')]
        with self.assertRaisesRegex(ValueError,'identity changed'):validate_campaign(self.c,campaign,now=NOW)

    def test_saved_link_repair_preserves_fetch_and_financial_history(self):
        from scrapper.tests.test_screener_vertical import fixture_standalone_response,fixture_standalone_peer_response
        from scrapper.workflows.screener.ingest import ingest_screener_response
        from scrapper.workflows.documents.saved_link_refresh import refresh_saved_links
        result=ingest_screener_response('LAURUSLABS','standalone',fixture_standalone_response(),
            fixture_standalone_peer_response(),db_path=self.f.db_path,snapshot_root=self.f.root/'pages',export_root=self.f.root/'exports')
        before={t:[tuple(r) for r in self.c.execute('SELECT * FROM '+t)] for t in
            ('accepted_page_versions','financial_observations','fetch_observations')}
        first=refresh_saved_links(self.c,company_id=result.company_id,accepted_parse_id=result.parse_id,period_end='2026-06-30')
        second=refresh_saved_links(self.c,company_id=result.company_id,accepted_parse_id=result.parse_id,period_end='2026-06-30')
        self.assertEqual(first['parse_id'],second['parse_id'])
        self.assertEqual(second['handoff']['queue']['created_job_count'],0)
        self.assertEqual(before,{t:[tuple(r) for r in self.c.execute('SELECT * FROM '+t)] for t in before})

    def download(self,body):
        selected=select_downloads(self.c,now=NOW,epoch=EPOCH,limit=2)
        fake=FakeSession({URL:[FakeResponse(body,url=URL)]})
        run_document_download_worker(self.c,limit=len(selected),artifact_types=('transcript',),
            allowed_job_ids=tuple(r['download_job_id'] for r,_ in selected),
            snapshot_root=self.f.root/'contents',document_store=self.f.root,
            session_factory=lambda:fake,health_check=FakeHealth,throttle_seconds=0,now=lambda:NOW)
        return selected

    def process(self):
        return run_automatic_processing(db_path=self.f.db_path,epoch=EPOCH,ocr_engine=FakeOcr(),
            output_dir=self.f.root/'extraction',limit=1,now_utc=lambda:NOW)

    def test_complete_late_document_lifecycle_and_same_url_versions(self):
        first=self.page();aid=first['artifact_ids'][0]
        enqueue_discovered_documents(self.c,epoch=EPOCH,now=NOW)
        self.assertEqual(self.download((FIXTURES/'d03_text.pdf').read_bytes())[0][1],2)
        self.assertEqual(self.process()['outcomes'][0]['extraction_status'],'ok')
        original=dict(self.c.execute('SELECT * FROM artifact_contents').fetchone())
        # Old artifact, newly revised content must retain P2 admission.
        with self.c:self.c.execute("UPDATE document_artifacts SET first_observed_at_utc='2026-08-01T00:00:00+00:00' WHERE artifact_id=?",(aid,))
        revised=self.page('2026-10-12T04:30:00+00:00')
        self.assertEqual(len(revised['revisions']),1)
        selected=self.download((FIXTURES/'d05_official_transcript.pdf').read_bytes())
        self.assertEqual(selected[0][1],2)
        self.assertEqual(self.process()['outcomes'][0]['extraction_status'],'ok')
        self.assertEqual(self.c.execute('SELECT count(*) FROM artifact_contents').fetchone()[0],2)
        self.assertEqual(dict(self.c.execute('SELECT * FROM artifact_contents WHERE content_id=?',(original['content_id'],)).fetchone()),original)
        self.assertEqual(self.c.execute('SELECT count(*) FROM document_processing_inbox').fetchone()[0],2)
        self.assertEqual(self.process()['status'],'idle')
        self.page('2026-10-12T04:30:00+00:00')
        self.assertEqual(select_downloads(self.c,now=NOW,epoch=EPOCH),[])

    def test_unchanged_revalidation_creates_no_content_or_extraction(self):
        first=self.page();enqueue_discovered_documents(self.c,epoch=EPOCH,now=NOW)
        body=(FIXTURES/'d03_text.pdf').read_bytes();self.download(body);self.process()
        self.page('2026-10-12T04:30:00+00:00');self.download(body)
        self.assertEqual(self.c.execute('SELECT count(*) FROM artifact_contents').fetchone()[0],1)
        self.assertEqual(self.process()['status'],'idle')
        old_job=self.c.execute('SELECT status FROM artifact_download_queue ORDER BY download_job_id').fetchall()
        self.assertEqual([r[0] for r in old_job],['SUCCEEDED','SUCCEEDED'])

    def test_verified_bse_only_identity_and_source_block(self):
        with self.c:self.c.execute("UPDATE security_identifiers SET identifier_type='BSE',identifier_value='123456'")
        result=self.page()
        self.assertEqual(result['company_id'],self.f.company_id)
        self.assertEqual(self.c.execute('SELECT count(*) FROM companies').fetchone()[0],1)
        with self.assertRaises(ValueError):
            ingest_page(self.c,company_id=self.f.company_id,source='company_ir',url='https://api.bseindia.com/x',body=b'',now=NOW,snapshot_root=self.f.root)
        self.assertEqual(enqueue_revision(self.c,artifact_id=1,url='https://api.bseindia.com/x',indicator={'etag':'x'},now=NOW)['status'],'source_access_blocked')

    def test_publication_absence_does_not_invent_period_or_unavailability(self):
        result=ingest_page(self.c,company_id=self.f.company_id,source='company_ir',url=PAGE,
            body=b'<p>No documents yet</p>',now=NOW,snapshot_root=self.f.root)
        self.assertEqual(result['status'],'not_yet_published')
        result=ingest_page(self.c,company_id=self.f.company_id,source='company_ir',url=PAGE,
            body=f'<a href="{URL}">Transcript</a>'.encode(),now=NOW,snapshot_root=self.f.root)
        artifact=self.c.execute('SELECT period_end,published_at_utc FROM document_artifacts WHERE artifact_id=?',(result['artifact_ids'][0],)).fetchone()
        self.assertEqual(tuple(artifact),(None,None))

    def test_http_block_and_gone_receipts_are_bounded(self):
        self.page()
        for code,state in [(403,'source_access_blocked'),(503,'temporarily_unavailable'),(410,'confirmed_unavailable')]:
            fake=FakeSession({PAGE:[FakeResponse(b'',url=PAGE,status_code=code,content_type='text/html')]})
            r=refresh_official_pages(self.c,company_ids=[self.f.company_id],now=NOW,snapshot_root=self.f.root,session=fake,request_limit=1)
            self.assertEqual(r['requests'],1);self.assertEqual(r['observations'][0]['status'],state)

    def test_late_policy_is_finite_and_does_not_rearm_blocks(self):
        eid=self.expectations[0]
        with self.c:self.c.execute("UPDATE artifact_expectations SET attempt_count=15,terminal_reason='retry_schedule_exhausted',next_check_at_utc=NULL WHERE expectation_id=?",(eid,))
        self.assertEqual(extend_exhausted_waits(self.c,now=NOW),[eid])
        self.assertEqual(extend_exhausted_waits(self.c,now=NOW),[])
        row=self.c.execute('SELECT * FROM artifact_expectations WHERE expectation_id=?',(eid,)).fetchone()
        self.assertEqual(json.loads(row['schedule_json'])['offsets_hours'][-2:],[2160,4320])
        self.assertIsNotNone(row['next_check_at_utc'])
        with self.c:self.c.execute("UPDATE artifact_expectations SET terminal_reason='inaccessible_not_actionable' WHERE expectation_id=?",(self.expectations[1],))
        self.assertEqual(extend_exhausted_waits(self.c,now=NOW),[])

    def test_document_refresh_does_not_touch_financial_history(self):
        from dataclasses import replace
        from scrapper.tests.test_screener_vertical import fixture_response,STANDALONE_FIXTURE,STANDALONE_URL
        from scrapper.sources.screener.company import parse_company_page
        from scrapper.workflows.documents.screener_refresh import ingest_document_response
        body=STANDALONE_FIXTURE.read_bytes()
        parsed=parse_company_page(body,NOW)
        with self.c:
            self.c.execute('UPDATE security_identifiers SET identifier_value=? WHERE company_id=?',(parsed.nse_symbol,self.f.company_id))
        response=replace(fixture_response(body),requested_url=STANDALONE_URL,final_url=STANDALONE_URL)
        before={t:[tuple(r) for r in self.c.execute('SELECT * FROM '+t)] for t in ('financial_observations','accepted_page_versions','company_metric_observations')}
        result=ingest_document_response(response=response,company_id=self.f.company_id,result_period='2026-06',db_path=self.f.db_path,snapshot_root=self.f.root/'screens')
        self.assertEqual(result.company_id,self.f.company_id)
        self.assertGreater(self.c.execute('SELECT count(*) FROM document_links').fetchone()[0],0)
        self.assertEqual(before,{t:[tuple(r) for r in self.c.execute('SELECT * FROM '+t)] for t in before})
        with self.assertRaises(ValueError):
            ingest_document_response(response=response,company_id=999,result_period='2026-06',db_path=self.f.db_path,snapshot_root=self.f.root)

    def test_explicit_quarter_label_supplies_period_without_current_quarter_guess(self):
        r=ingest_page(self.c,company_id=self.f.company_id,source='company_ir',url=PAGE,
            body=f'<a href="{URL}">Q1 FY26 Earnings Transcript</a>'.encode(),now=NOW,snapshot_root=self.f.root)
        period=self.c.execute('SELECT period_end FROM document_artifacts WHERE artifact_id=?',(r['artifact_ids'][0],)).fetchone()[0]
        self.assertEqual(period,'2025-06-30')

    def test_official_announcement_parser_keeps_company_period_and_raw_json(self):
        query='https://www.nseindia.com/api/corporate-announcements?symbol=FIXTURE'
        body=json.dumps([{'symbol':'FIXTURE','seq_id':'123','desc':'Q1 FY26 Earnings Transcript',
            'attchmntFile':'https://nsearchives.nseindia.com/corporate/x.pdf','an_dt':'12-Oct-2026 10:30:00'},
            {'symbol':'OTHER','desc':'Presentation','attchmntFile':'https://nsearchives.nseindia.com/corporate/other.pdf'}]).encode()
        r=ingest_page(self.c,company_id=self.f.company_id,source='nse',url=query,body=body,now=NOW,snapshot_root=self.f.root)
        self.assertEqual(len(r['artifact_ids']),1)
        a=self.c.execute('SELECT company_id,period_end FROM document_artifacts WHERE artifact_id=?',(r['artifact_ids'][0],)).fetchone()
        self.assertEqual(tuple(a),(self.f.company_id,'2025-06-30'))
        b=self.c.execute('SELECT b.local_path,b.content_type FROM artifact_discovery_pages p JOIN response_blobs b USING(blob_id) WHERE p.discovery_page_id=?',(r['discovery_page_ids'][0],)).fetchone()
        self.assertEqual(Path(b[0]).read_bytes(),body);self.assertEqual(b[1],'application/json')

    def test_missing_then_later_discovered_queued_and_downloaded(self):
        from scrapper.workflows.documents.reconciliation import reconcile_artifacts
        from scrapper.documents.readiness.persisted import PersistedArtifactAdapter
        later='2026-10-13T05:00:00+00:00'
        # Scope already established by this fixture; isolate availability from
        # financial acceptance while exercising canonical claims and persistence.
        with self.c:
            self.c.execute("UPDATE artifact_expectations SET expectation_basis='fixture' WHERE company_id=?",(self.f.company_id,))
        first=reconcile_artifacts(due=True,db_path=self.f.db_path,
            adapter=PersistedArtifactAdapter(),now_utc=lambda:NOW,output=self.f.root/'missing.json')
        transcript=next(c for c in first['checks'] if c['artifact_type']=='transcript')
        self.assertEqual(transcript['result_status'],'expected_not_yet_published')
        self.assertIsNotNone(transcript['next_check_at_utc'])
        self.page()
        enqueue_discovered_documents(self.c,epoch=EPOCH,now=NOW)
        second=reconcile_artifacts(due=True,db_path=self.f.db_path,
            adapter=PersistedArtifactAdapter(),now_utc=lambda:later,output=self.f.root/'found.json')
        transcript=next(c for c in second['checks'] if c['artifact_type']=='transcript')
        # Readiness remains pending until official normalization; discovery and
        # download have their own durable evidence before that acceptance.
        self.assertEqual(self.c.execute("SELECT count(*) FROM artifact_sources WHERE document_url=? AND availability_status='available'",(URL,)).fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT count(*) FROM artifact_download_queue').fetchone()[0],1)
        self.download((FIXTURES/'d03_text.pdf').read_bytes())
        self.assertEqual(self.c.execute('SELECT status FROM artifact_download_queue').fetchone()[0],'SUCCEEDED')
        self.assertEqual(self.c.execute('SELECT count(*) FROM artifact_contents').fetchone()[0],1)
