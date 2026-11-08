"""Offline recurring browser routing, canonical persistence and retry contracts."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import unittest
from unittest.mock import Mock, patch

from scrapper.core.host_request_contracts import AdministrativeDefer
from scrapper.db import host_permits
from scrapper.db.database import connect
from scrapper.documents.downloader import DownloadPolicy, DownloadResult
from scrapper.documents.rating_browser import (
    RatingBrowserRequests, resolve_rating_browser, uses_rating_browser,
)
from scrapper.operations.workers.automatic_documents import (
    handoff_browser_ratings, run_automatic_ratings, select_downloads,
)
from scrapper.operations.workers.host_requests import HostSession
from scrapper.pipeline.credit_rating_downloads import queue_credit_rating_downloads, run_credit_rating_download_worker
from scrapper.tests import test_credit_rating_acquisition as rating_fixture
from scrapper.tests.test_credit_rating_acquisition import FakeSession, FakeResponse, PDF_BYTES
from scrapper.tests.test_document_download_queue import FakeHealth
from scrapper.tests.test_host_request_executors import Clock


class AutomaticRatingBrowserTests(unittest.TestCase):
    def setUp(self):
        self.fixture = rating_fixture.CreditRatingAcquisitionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.doCleanups()
        self.conn = self.fixture.conn
        self.root = self.fixture.root
        self.conn.executescript((Path(__file__).parents[1] / 'db/migrations/076_retire_crisil_icra_terms_holds.sql').read_text())
        from scrapper.pipeline.promote_document_links import promote_document_links
        aid = promote_document_links(self.conn)['artifact_ids'][0]
        self.artifact_id = aid
        self.url = 'https://www.icra.in/Rationale/ShowRationaleReport?id=42'
        with self.conn:
            self.conn.execute('UPDATE artifact_sources SET document_url=? WHERE artifact_id=?', (self.url, aid))
            self.conn.execute("INSERT INTO screener_collection_scope(company_path,company_id,eligibility,floor_crore,observed_at_utc,evidence_json,next_check_at_utc) SELECT '/company/EXAMPLE/',company_id,'eligible','580','2026-10-30T00:00:00+00:00','{}','2026-11-01T00:00:00+00:00' FROM document_artifacts WHERE artifact_id=?", (aid,))
        queue_credit_rating_downloads(self.conn, artifact_ids=(aid,), created_at_utc='2026-10-30T00:00:00+00:00')
        self.clock = Clock()
        self.fake = FakeSession({self.url: FakeResponse(PDF_BYTES,url=self.url,content_type='application/pdf')})

    def session(self, **kwargs):
        return HostSession(self.conn, mode='rating',owner='fixture',
            policy=kwargs.pop('policy', DownloadPolicy(total_timeout_seconds=45)),
            check=kwargs.pop('check',lambda:None),offline_session=self.fake,
            now=self.clock,sleep=self.clock.sleep,**kwargs)

    def job(self):
        return self.conn.execute('SELECT * FROM credit_rating_download_queue').fetchone()

    def playwright(self):
        self.browser = Mock()
        factory = Mock()
        factory.return_value.__enter__ = Mock(return_value=SimpleNamespace(
            chromium=SimpleNamespace(launch=Mock(return_value=self.browser))))
        factory.return_value.__exit__ = Mock(return_value=False)
        return factory

    def fetch(self, browser, url, company, **kwargs):
        self.assertEqual(company, 'Example Limited')
        adapter = kwargs['shared_requests']
        status,final,headers,body = adapter.viewer(url)
        result = DownloadResult(url,final,status,headers,(), 'application/pdf',
            'application/pdf',body,len(body),'validated',1)
        return result, {'issuer_match':'fixture validated'}

    def resolve(self, conn, job, **kwargs):
        return resolve_rating_browser(conn,job,**kwargs,
            playwright_factory=self.playwright(),fetch_report=self.fetch)

    def run_worker(self):
        original = HostSession
        def offline(conn, **kwargs):
            return original(conn,**kwargs,offline_session=self.fake,now=self.clock,sleep=self.clock.sleep)
        with ExitStack() as stack:
            # The fake synchronous HTTP transport does not need Linux children.
            stack.enter_context(patch('scrapper.db.host_request_defer.require_shared_schema'))
            stack.enter_context(patch('scrapper.operations.workers.host_requests.HostSession',side_effect=offline))
            stack.enter_context(patch('scrapper.pipeline.credit_rating_downloads.assert_document_store_ready',FakeHealth))
            stack.enter_context(patch('scrapper.documents.rating_browser.resolve_rating_browser',side_effect=self.resolve))
            stack.enter_context(patch('scrapper.pipeline.credit_rating_downloads.resolve_credit_rating',side_effect=AssertionError('HTTP resolver used for ICRA')))
            return run_credit_rating_download_worker(self.conn,limit=1,shared_permits=True,
                allowed_job_ids=(self.job()['download_job_id'],),document_store=self.root,
                snapshot_root=self.root/'browser-bytes',throttle_seconds=0)

    def test_host_routing_is_exact_and_covers_aliases(self):
        for url in ('https://icra.in/a','https://www.crisil.com/a','https://cdn.crisilratings.com/a'):
            self.assertTrue(uses_rating_browser(url))
        for url in ('https://evilicra.in/a','https://icra.in.evil.example/a','https://careratings.com/a'):
            self.assertFalse(uses_rating_browser(url))

    def test_browser_success_persists_and_enters_normal_processing(self):
        result = self.run_worker()
        self.assertEqual(result['outcome_counts'], {'BROWSER_PDF_VALIDATED':1})
        self.assertEqual(self.job()['status'],'SUCCEEDED')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM artifact_contents WHERE artifact_id=?',(self.artifact_id,)).fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_inbox').fetchone()[0],1)
        step = self.conn.execute('SELECT step_type,evidence_json FROM credit_rating_resolution_steps').fetchone()
        self.assertEqual(step['step_type'],'automated_browser')
        self.assertEqual(json.loads(step['evidence_json'])['method'],'automated_credit_rating_browser')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM acquisition_host_permits WHERE acquisition_mode='rating' AND status='RELEASED'").fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM browser_rating_recovery_jobs').fetchone()[0],0)
        self.assertEqual(self.run_worker()['processed_job_count'],0)

    def test_browser_success_uses_existing_production_sql_write_boundary(self):
        with patch.object(sys,'path',list(sys.path)):
            sys.path.insert(0,str(Path(__file__).parents[2]/'ops/activation'))
            from phase4_routine_runtime import routine_authorizer
        self.conn.set_authorizer(routine_authorizer)
        try:
            self.assertEqual(self.run_worker()['outcome_counts'],{'BROWSER_PDF_VALIDATED':1})
        finally:
            self.conn.set_authorizer(None)

    def test_browser_failure_retries_without_manual_campaign(self):
        self.fetch = Mock(side_effect=TimeoutError('publisher viewer not ready'))
        self.assertEqual(self.run_worker()['outcome_counts'],{'RETRYABLE_BROWSER_FAILURE':1})
        self.assertEqual(self.job()['status'],'RETRYABLE_FAILURE')
        self.assertIsNotNone(self.job()['next_attempt_at_utc'])
        with self.conn:
            self.conn.execute('UPDATE credit_rating_download_queue SET next_attempt_at_utc=NULL')
        self.assertEqual(self.run_worker()['processed_job_count'],1)
        with self.conn:
            self.conn.execute('UPDATE credit_rating_download_queue SET next_attempt_at_utc=NULL')
        self.run_worker()
        self.assertEqual(self.job()['status'],'TERMINAL_FAILURE')
        self.assertEqual(self.job()['attempt_count'],3)

    def test_wrong_issuer_is_held_for_review_without_storing_content(self):
        self.fetch = Mock(side_effect=ValueError('REVIEW_REQUIRED: wrong issuer'))
        self.run_worker()
        self.assertEqual(self.job()['status'],'INTERACTION_REQUIRED')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM artifact_contents').fetchone()[0],0)

    def test_busy_publisher_refunds_claim_without_browser_network(self):
        host_permits.acquire(self.conn,self.url,mode='rating',owner='other',now=self.clock(),lease_seconds=300)
        result = self.run_worker()
        self.assertEqual(result['outcome_counts'],{'HOST_BUSY':1})
        self.assertEqual(self.job()['attempt_count'],0)
        self.assertEqual(self.fake.calls,[])

    def test_403_retains_cooldown_without_unmetered_fallback(self):
        self.fake.responses[self.url].status_code=403
        def denied(browser,url,company,**kwargs):
            kwargs['shared_requests'].viewer(url)
            raise ValueError('HTTP 403')
        self.fetch=denied
        self.run_worker()
        self.assertEqual(self.job()['status'],'RETRYABLE_FAILURE')
        self.assertEqual(self.conn.execute('SELECT last_http_status FROM credit_rating_host_health').fetchone()[0],403)
        self.assertEqual(self.fake.calls,[('GET',self.url)])

    def test_every_navigation_script_and_viewer_request_is_paced(self):
        urls=[self.url,'https://www.icra.in/script.js','https://icra.in/viewer.pdf']
        self.fake.responses.update({u:FakeResponse(PDF_BYTES,url=u,content_type='application/pdf') for u in urls})
        adapter=RatingBrowserRequests(self.session(),self.url)
        for u in urls:
            adapter.fetch('GET',u)
        rows=self.conn.execute('SELECT acquired_at FROM acquisition_host_permits ORDER BY acquired_at').fetchall()
        self.assertEqual(len(rows),3)
        self.assertTrue(all(rows[i+1][0]-rows[i][0]>=4 for i in range(2)))
        adapter.close()

    def test_budget_deadline_and_cross_publisher_navigation_are_bounded(self):
        adapter=RatingBrowserRequests(self.session(),self.url,request_limit=1)
        adapter.viewer(self.url)
        with self.assertRaisesRegex(RuntimeError,'budget'):adapter.viewer(self.url)
        self.assertEqual(len(self.fake.calls),1)
        adapter.close()
        adapter=RatingBrowserRequests(self.session(),self.url,monotonic=lambda:0)
        adapter.monotonic=lambda:46
        with self.assertRaises(TimeoutError):adapter.viewer(self.url)
        adapter=RatingBrowserRequests(self.session(),self.url)
        with self.assertRaisesRegex(ValueError,'left its publisher'):adapter.viewer('https://evilicra.in/a')

    def test_recurring_stage_preserves_eligibility_epoch_and_due_times(self):
        epoch='2026-10-29T00:00:00+00:00';now='2026-10-30T05:00:00+00:00'
        def factory(path):return connect(self.fixture.db_path)
        with patch('scrapper.pipeline.credit_rating_downloads.run_credit_rating_download_worker',return_value={'routed':True}) as worker:
            self.assertEqual(run_automatic_ratings(db_path=self.fixture.db_path,epoch=epoch,
                now_utc=lambda:now,connection_factory=factory),{'routed':True})
            self.assertEqual(worker.call_args.kwargs['allowed_job_ids'],(self.job()['download_job_id'],))
            with self.conn:self.conn.execute("UPDATE credit_rating_download_queue SET next_attempt_at_utc='2027-01-01T00:00:00+00:00'")
            worker.reset_mock()
            self.assertEqual(run_automatic_ratings(db_path=self.fixture.db_path,epoch=epoch,now_utc=lambda:now,connection_factory=factory)['status'],'idle')
            worker.assert_not_called()

    def test_generic_discoveries_are_handed_off_once_without_resetting_age(self):
        from scrapper.db.document_download_queue_repo import ensure_recovery_download_scope, claim_download_job
        company_id=self.conn.execute('SELECT company_id FROM document_artifacts WHERE artifact_id=?',(self.artifact_id,)).fetchone()[0]
        ensure_recovery_download_scope(self.conn,artifact_id=self.artifact_id,company_id=company_id,
                                      created_at_utc='2026-10-30T01:00:00+00:00')
        with self.conn:self.conn.execute('DELETE FROM credit_rating_download_queue')
        epoch='2026-10-29T00:00:00+00:00';now='2026-10-30T05:00:00+00:00'
        self.assertEqual(select_downloads(self.conn,now=now,epoch=epoch),[])
        self.assertEqual(handoff_browser_ratings(self.conn,epoch=epoch,now=now),[self.artifact_id])
        self.assertEqual(handoff_browser_ratings(self.conn,epoch=epoch,now=now),[])
        self.assertEqual(self.job()['created_at_utc'],'2026-10-30T01:00:00+00:00')
        self.assertIsNone(claim_download_job(self.conn,worker='generic',now_utc=now))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM credit_rating_download_queue').fetchone()[0],1)

    def test_handoff_leaves_old_and_ineligible_generic_discoveries_alone(self):
        from scrapper.db.document_download_queue_repo import ensure_recovery_download_scope
        company_id=self.conn.execute('SELECT company_id FROM document_artifacts WHERE artifact_id=?',(self.artifact_id,)).fetchone()[0]
        ensure_recovery_download_scope(self.conn,artifact_id=self.artifact_id,company_id=company_id,
                                      created_at_utc='2026-10-01T00:00:00+00:00')
        with self.conn:self.conn.execute('DELETE FROM credit_rating_download_queue')
        self.assertEqual(handoff_browser_ratings(self.conn,epoch='2026-10-29T00:00:00+00:00',now='2026-10-30T05:00:00+00:00'),[])
        with self.conn:self.conn.execute("UPDATE screener_collection_scope SET eligibility='below_floor'")
        self.assertEqual(handoff_browser_ratings(self.conn,epoch='2026-09-01T00:00:00+00:00',now='2026-10-30T05:00:00+00:00'),[])

    @unittest.skipUnless(sys.platform == 'linux', 'Uses the installed Linux Chromium runtime')
    def test_real_chromium_clicks_icra_download_with_network_disabled(self):
        import io
        from reportlab.pdfgen.canvas import Canvas
        pdf=io.BytesIO();canvas=Canvas(pdf)
        for i in range(8):
            canvas.drawString(40,780-i*20,'Example Limited credit rating rationale: stable liquidity and bank facilities.')
        canvas.save()
        target='https://www.icra.in/report.pdf'
        html=b'<html><body><button onclick="location.href=\'/report.pdf\'">Download</button></body></html>'
        self.fake.responses[self.url]=FakeResponse(html,url=self.url,content_type='text/html')
        self.fake.responses[target]=FakeResponse(pdf.getvalue(),url=target,content_type='application/pdf')
        self.fake.responses[target].headers['Content-Disposition']='attachment; filename="rating.pdf"'
        result=resolve_rating_browser(self.conn,self.job(),session=self.session(),
                                     policy=DownloadPolicy(total_timeout_seconds=45))
        self.assertEqual(result.status,'BROWSER_PDF_VALIDATED',result.error)
        self.assertEqual(result.final_result.body,pdf.getvalue())
        self.assertEqual(result.steps[0].evidence['clicked_labels'],['Download'])
        self.assertTrue(result.steps[0].evidence['company_name_match'])
        self.assertEqual(self.fake.calls,[('GET',self.url),('GET',target)])
