"""Offline integration, cancellation, defer accounting and physical-I/O fencing."""
import ast
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import requests
from scrapper.db.database import connect, init_db
from scrapper.db import host_permits as permits
from scrapper.db.host_request_defer import defer_job, require_shared_schema
from scrapper.db.document_work_claims import OwnershipLost
from scrapper.operations.workers.host_requests import HostSession, AdministrativeDefer
from scrapper.documents.download.isolated_http import http_hop
from scrapper.documents.downloader import download_url, DownloadPolicy
from scrapper.documents.credit_rating_resolver import resolve_credit_rating
from scrapper.tests.test_credit_rating_acquisition import FakeResponse, FakeSession, PDF_BYTES


class Clock:
    def __init__(self): self.value = time.time()
    def __call__(self): return self.value
    def sleep(self, seconds): self.value += seconds


class StallResponse:
    status_code = 200
    url = 'https://issuer.example/stall'
    headers = {'Content-Type': 'application/pdf'}
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def iter_content(self, chunk_size):
        time.sleep(60)
        yield b'never'


class ChildSession:
    def __init__(self): self.cookies = requests.cookies.RequestsCookieJar()
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def request(self, method, url, **kwargs):
        if url.endswith('/pdf'):
            return ChildPDFResponse(PDF_BYTES,url=url,content_type='application/pdf')
        return StallResponse()


class ChildPDFResponse(FakeResponse):
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def offline_child(*args):
    with patch('requests.Session', ChildSession): http_hop(*args)


class StallHop:
    def __init__(self, method, url, kwargs, cookies, chunk_bytes, deadline):
        context = multiprocessing.get_context('spawn')
        self.pipe, child = context.Pipe(False)
        self.process = context.Process(target=offline_child,
            args=(child, os.getpid(), method, url, kwargs, cookies, chunk_bytes, deadline))
        self.process.start(); child.close()
    def close(self):
        if self.process.is_alive(): self.process.kill()
        self.process.join(); self.pipe.close()


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.conn = connect(self.root/'db.sqlite'); init_db(self.conn, apply_migrations=True)
        self.clock = Clock()
    def tearDown(self): self.conn.close(); self.temp.cleanup()
    def adapter(self, fake=None, **kwargs):
        return HostSession(self.conn, mode=kwargs.pop('mode','generic'), owner='test',
            policy=kwargs.pop('policy',DownloadPolicy()), check=kwargs.pop('check',lambda:None),
            now=self.clock, sleep=self.clock.sleep, offline_session=fake, **kwargs)
    def response(self, url, body=PDF_BYTES):
        return FakeResponse(body, url=url, content_type='application/pdf')
    def test_get_post_redirect_each_owns_a_permit(self):
        first='https://issuer.example/first'; last='https://other.example/final'
        redirect=self.response(first,b''); redirect.status_code=302; redirect.headers['Location']=last
        fake=FakeSession({first:redirect,last:self.response(last)})
        session=self.adapter(fake)
        result=download_url(first,artifact_type='presentation',session=session)
        self.assertTrue(result.is_validated)
        rows=self.conn.execute('SELECT host,status FROM acquisition_host_permits').fetchall()
        self.assertEqual([tuple(r) for r in rows],[('issuer.example','RELEASED'),('other.example','RELEASED')])
        post='https://post.example/form'; fake.responses[post]=self.response(post)
        session.post(post,allow_redirects=False,data={'id':'123'}).close()
        self.assertEqual(fake.calls[-1],('POST',post))
    def test_alias_pacing_waits_without_retry_or_transaction(self):
        urls=['https://careratings.com/a','https://www.careratings.com/b']
        fake=FakeSession({u:self.response(u) for u in urls})
        session=self.adapter(fake,mode='rating'); start=self.clock()
        for url in urls:
            self.assertTrue(download_url(url,artifact_type='credit_rating',session=session).is_validated)
        grants=[r[0] for r in self.conn.execute('SELECT acquired_at FROM acquisition_host_permits ORDER BY acquired_at')]
        self.assertGreaterEqual(grants[1]-grants[0],4); self.assertFalse(self.conn.in_transaction)
    def test_one_busy_publisher_does_not_block_other_host(self):
        permits.acquire(self.conn,'https://careratings.com/a',mode='rating',owner='other',now=self.clock(),lease_seconds=100)
        session=self.adapter(FakeSession({'https://free.example/a':self.response('https://free.example/a')}))
        with self.assertRaises(AdministrativeDefer): session.get('https://www.careratings.com/a',allow_redirects=False)
        self.assertEqual(session.dispatched,0)
        download_url('https://free.example/a',artifact_type='presentation',session=session)
        self.assertEqual(session.dispatched,1)
    def test_retry_after_and_default_blocks_are_shared(self):
        for mode,status,raw,expected in [('generic',429,'20',20),('rating',429,'20',21600),('generic',403,None,86400)]:
            url=f'https://{mode}-{status}.example/a'; response=self.response(url); response.status_code=status
            if raw: response.headers['Retry-After']=raw
            session=self.adapter(FakeSession({url:response}),mode=mode)
            download_url(url,artifact_type='presentation',session=session)
            row=self.conn.execute('SELECT blocked_until FROM acquisition_host_state WHERE publisher_group=?',(permits.policy_for(url).group,)).fetchone()
            self.assertAlmostEqual(row[0],self.clock()+expected)
            other=self.adapter(mode='rating' if mode=='generic' else 'generic')
            with self.assertRaises(AdministrativeDefer): other.get(url,allow_redirects=False)
    def test_three_transport_failures_open_circuit_for_other_queue(self):
        class Broken:
            def get(self,*a,**k): raise requests.ConnectionError('offline fault')
        url='https://broken.example/a'
        for _ in range(3):
            result=download_url(url,artifact_type='presentation',session=self.adapter(Broken()))
            self.assertEqual(result.result_status,'network_error'); self.clock.sleep(4)
        with self.assertRaises(AdministrativeDefer): self.adapter(mode='rating').get(url,allow_redirects=False)
    def test_policy_blocks_aliases_before_dispatch(self):
        fake=FakeSession({}); session=self.adapter(fake)
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO credit_rating_host_policy(host,automation_status,max_concurrency,min_delay_seconds,policy_basis,reviewed_at_utc) VALUES('careratings.com','BLOCKED_TERMS',1,4,'offline test','2026-10-05T00:00:00+00:00')")
        with self.assertRaises(AdministrativeDefer): session.get('https://www.careratings.com/a',allow_redirects=False)
        self.assertEqual(fake.calls,[]); self.assertEqual(session.dispatched,0)
    def test_outer_transaction_refused(self):
        self.conn.execute('BEGIN')
        with self.assertRaises(ValueError): self.adapter().get('https://issuer.example/a',allow_redirects=False)
        self.conn.rollback()
    def test_ownership_loss_during_stream_refuses_success(self):
        url='https://issuer.example/a'; count=[0]
        def check():
            count[0]+=1
            if count[0]>=3: raise OwnershipLost('reclaimed')
        session=self.adapter(FakeSession({url:self.response(url)}),check=check)
        with self.assertRaises(OwnershipLost): download_url(url,artifact_type='presentation',session=session)
        self.assertEqual(self.conn.execute("SELECT status FROM acquisition_host_permits").fetchone()[0],'RELEASED')
    def test_permit_expiry_cannot_release_newer_reservation(self):
        url='https://issuer.example/a'; session=self.adapter(FakeSession({url:self.response(url)}))
        response=session.get(url,allow_redirects=False)
        self.clock.sleep(1000)
        newer=permits.acquire(self.conn,url,mode='rating',owner='new',now=self.clock()).permit
        with self.assertRaises(OwnershipLost): response.close()
        self.assertEqual(self.conn.execute('SELECT status FROM acquisition_host_permits WHERE permit_token=?',(newer.token,)).fetchone()[0],'ACTIVE')
    def test_resolver_keeps_completed_steps_on_later_defer(self):
        first='https://careratings.com/page'; last='https://careratings.com/report.pdf'
        html=b'<html><a href="/report.pdf">Download rating report PDF</a></html>'
        response=FakeResponse(html,url=first,content_type='text/html')
        session=self.adapter(FakeSession({first:response,last:self.response(last)}),mode='rating',wait_seconds=0)
        with self.assertRaises(AdministrativeDefer) as context:
            resolve_credit_rating(first,session=session,browser_fallback=False)
        self.assertEqual(len(context.exception.steps),1); self.assertEqual(session.dispatched,1)
    def test_resolver_form_and_landing_use_shared_pacing(self):
        first='https://careratings.com/page'; last='https://careratings.com/report.pdf'
        html=b'<html><form method="post" action="/report.pdf"><input name="id" value="123">Download rating report PDF</form></html>'
        fake=FakeSession({first:FakeResponse(html,url=first,content_type='text/html'),last:self.response(last)})
        result=resolve_credit_rating(first,session=self.adapter(fake,mode='rating'),browser_fallback=False)
        self.assertEqual(result.status,'LANDING_PAGE_RESOLVED'); self.assertEqual(fake.calls[-1],('POST',last))
    def test_stalled_real_child_is_killed_before_permit_release(self):
        session=self.adapter(policy=DownloadPolicy(total_timeout_seconds=2),hop_factory=StallHop)
        started=time.monotonic()
        result=download_url('https://issuer.example/stall',artifact_type='presentation',session=session)
        self.assertIn(result.result_status,{'timed_out','network_error'})
        self.assertLess(time.monotonic()-started,5)
        self.assertFalse(session.responses[0].hop.process.is_alive())
        self.assertEqual(self.conn.execute('SELECT status FROM acquisition_host_permits').fetchone()[0],'RELEASED')
    def test_queue_loss_kills_live_child(self):
        count=[0]
        def check():
            count[0]+=1
            if count[0]>5: raise OwnershipLost('queue reclaimed')
        session=self.adapter(hop_factory=StallHop,check=check)
        with self.assertRaises(OwnershipLost): download_url('https://issuer.example/stall',artifact_type='presentation',session=session)
        self.assertFalse(session.responses[0].hop.process.is_alive())
    def test_killed_parent_cannot_leave_child_running(self):
        # Independent parent/child processes exercise actual Linux PDEATHSIG.
        script='''import os,time
from pathlib import Path
from scrapper.tests.test_host_request_executors import StallHop
import requests
hop=StallHop('GET','https://issuer.example/stall',{},requests.cookies.RequestsCookieJar(),1024,time.monotonic()+60)
Path(os.environ['CHILD_PID_FILE']).write_text(str(hop.process.pid))
time.sleep(60)
'''
        file=self.root/'pid'; parent=subprocess.Popen([sys.executable,'-B','-c',script],env={**os.environ,'CHILD_PID_FILE':str(file)})
        try:
            until=time.monotonic()+5
            while not file.exists() and time.monotonic()<until: time.sleep(.05)
            self.assertTrue(file.exists()); pid=int(file.read_text())
            # Wait for prctl registration/headers; child registration race also fails closed.
            time.sleep(.8); parent.kill(); parent.wait()
            until=time.monotonic()+3
            while time.monotonic()<until:
                state=Path(f'/proc/{pid}/stat')
                if not state.exists() or state.read_text().split()[2]=='Z': break
                time.sleep(.05)
            else: self.fail('HTTP child survived SIGKILL of permit owner')
        finally:
            if parent.poll() is None: parent.kill(); parent.wait()
    def test_imports_are_inert_and_document_owner_has_no_upward_dependency(self):
        script="""import sys,socket,sqlite3,threading,subprocess
sys.dont_write_bytecode=True
def deny(*a,**k): raise AssertionError('side effect')
socket.socket.connect=sqlite3.connect=threading.Thread.start=subprocess.Popen=deny
import scrapper.operations.workers.host_requests
import scrapper.documents.download.isolated_http
import scrapper.db.host_request_defer
"""
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        root=Path(__file__).parents[1]
        for name in ['documents/credit_rating_resolver.py','documents/download/isolated_http.py','db/host_request_defer.py']:
            for node in ast.walk(ast.parse((root/name).read_text())):
                if isinstance(node,ast.ImportFrom): self.assertFalse(set((node.module or '').split('.'))&{'operations','workflows','pipeline','view','commands'})

    def test_real_child_stream_protocol_delivers_validated_bytes(self):
        session=self.adapter(hop_factory=StallHop)
        result=download_url('https://issuer.example/pdf',artifact_type='presentation',session=session)
        self.assertTrue(result.is_validated);self.assertEqual(result.body,PDF_BYTES)
        self.assertFalse(session.responses[0].hop.process.is_alive())
    def test_child_has_its_own_deadline_without_parent_polling(self):
        hop=StallHop('GET','https://issuer.example/stall',{},requests.cookies.RequestsCookieJar(),1024,time.monotonic()+2)
        try:
            hop.process.join(4)
            self.assertFalse(hop.process.is_alive())
            self.assertEqual(hop.process.exitcode,-signal.SIGKILL)
        finally:hop.close()
    def test_restart_reads_shared_cooldown_without_dispatch(self):
        url='https://www.careratings.com/a';response=self.response(url);response.status_code=429
        download_url(url,artifact_type='credit_rating',session=self.adapter(FakeSession({url:response}),mode='rating'))
        self.conn.close();self.conn=connect(self.root/'db.sqlite')
        session=self.adapter(FakeSession({}),mode='generic')
        with self.assertRaises(AdministrativeDefer):session.get('https://careratings.com/b',allow_redirects=False)
        self.assertEqual(session.dispatched,0)

    def test_queue_loss_before_dispatch_releases_without_transport_failure(self):
        calls=[0]
        def check():
            calls[0]+=1
            if calls[0]==2:raise OwnershipLost('lost after reservation before HTTP')
        fake=FakeSession({});session=self.adapter(fake,check=check)
        with self.assertRaises(OwnershipLost):session.get('https://issuer.example/a',allow_redirects=False)
        self.assertEqual(fake.calls,[]);self.assertEqual(session.dispatched,0)
        self.assertEqual(tuple(self.conn.execute('SELECT consecutive_failures,last_outcome FROM acquisition_host_state').fetchone()),(0,'released'))
        self.assertEqual(self.conn.execute('SELECT status FROM acquisition_host_permits').fetchone()[0],'RELEASED')


class QueueIntegrationTests(unittest.TestCase):
    def setUp(self):
        from scrapper.tests.test_document_download_queue import DocumentDownloadQueueDA05Tests
        self.fixture=DocumentDownloadQueueDA05Tests(); self.fixture.setUp()
        self.conn=self.fixture.conn; self.root=self.fixture.root
    def tearDown(self): self.fixture.tearDown()
    def generic_job(self):
        from scrapper.db.document_download_queue_repo import materialize_download_queue,claim_download_job
        self.fixture.register('shared',('presentation',))
        materialize_download_queue(self.conn,scope_id='shared')
        from scrapper.db.repo import utc_now
        return claim_download_job(self.conn,worker='test',now_utc=utc_now(),scope_id='shared')
    def test_defer_without_dispatch_restores_request_budget(self):
        job=self.generic_job(); from scrapper.db.repo import utc_now
        defer_job(self.conn,'generic',job,now=utc_now(),retry_at=time.time()+60,dispatched=False,reason='HOST_BUSY')
        row=self.conn.execute('SELECT * FROM artifact_download_queue WHERE download_job_id=?',(job['download_job_id'],)).fetchone()
        self.assertEqual(row['attempt_count'],0); self.assertEqual(row['status'],'RETRYABLE_FAILURE'); self.assertIsNone(row['lease_owner'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM artifact_download_attempts').fetchone()[0],0)
    def test_defer_after_dispatch_retains_request_attempt(self):
        job=self.generic_job(); from scrapper.db.repo import utc_now
        defer_job(self.conn,'generic',job,now=utc_now(),retry_at=time.time()+60,dispatched=True,reason='HOST_DEFERRED')
        self.assertEqual(self.conn.execute('SELECT attempt_count FROM artifact_download_queue').fetchone()[0],1)
    def test_stale_defer_cannot_refund_or_release_newer_claim(self):
        job=self.generic_job(); from scrapper.db.repo import utc_now
        with self.conn: self.conn.execute("UPDATE artifact_download_queue SET claim_token='new'")
        with self.assertRaises(OwnershipLost): defer_job(self.conn,'generic',job,now=utc_now(),retry_at=time.time()+60,dispatched=False,reason='HOST_BUSY')
        self.assertEqual(self.conn.execute('SELECT attempt_count FROM artifact_download_queue').fetchone()[0],1)
    def test_enabled_worker_defers_without_network_or_download_attempt(self):
        from scrapper.db.document_download_queue_repo import materialize_download_queue
        from scrapper.workflows.documents.download import run_document_download_worker
        from scrapper.tests.test_document_download_queue import FakeHealth
        self.fixture.register('shared',('presentation',)); materialize_download_queue(self.conn,scope_id='shared')
        url=self.conn.execute('SELECT requested_url FROM artifact_download_queue').fetchone()[0]
        permits.acquire(self.conn,url,mode='rating',owner='other',now=time.time(),lease_seconds=100)
        result=run_document_download_worker(self.conn,scope_id='shared',limit=1,shared_permits=True,
            snapshot_root=self.root/'bytes',document_store=self.root,health_check=FakeHealth,throttle_seconds=0)
        self.assertEqual(result['outcome_counts'],{'HOST_BUSY':1})
        self.assertEqual(self.conn.execute('SELECT attempt_count FROM artifact_download_queue').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM artifact_download_attempts').fetchone()[0],0)
    def test_missing_schema_fails_before_claim_without_fallback(self):
        from scrapper.workflows.documents.download import run_document_download_worker
        self.fixture.register('shared',('presentation',))
        with self.conn: self.conn.execute('DROP TABLE acquisition_host_permits')
        before=self.conn.total_changes
        with self.assertRaises(Exception): run_document_download_worker(self.conn,scope_id='shared',shared_permits=True)
        self.assertEqual(self.conn.total_changes,before)
    def test_enabled_generic_preserves_content_and_artifact_identity(self):
        from scrapper.db.document_download_queue_repo import materialize_download_queue
        from scrapper.workflows.documents.download import run_document_download_worker
        from scrapper.tests.test_document_download_queue import FakeHealth
        self.fixture.register('shared',('presentation',)); materialize_download_queue(self.conn,scope_id='shared')
        job=self.conn.execute('SELECT * FROM artifact_download_queue').fetchone(); url=job['requested_url']
        fake=FakeSession({url:FakeResponse(PDF_BYTES,url=url,content_type='application/pdf')})
        real=HostSession
        def adapter(*a,**k): return real(*a,**k,offline_session=fake)
        with patch('scrapper.operations.workers.host_requests.HostSession',side_effect=adapter):
            result=run_document_download_worker(self.conn,scope_id='shared',limit=1,shared_permits=True,
                snapshot_root=self.root/'bytes',document_store=self.root,health_check=FakeHealth,throttle_seconds=0)
        self.assertEqual(result['outcome_counts'],{'validated':1})
        self.assertEqual(self.conn.execute('SELECT artifact_id FROM artifact_contents').fetchone()[0],job['artifact_id'])
        self.assertEqual(self.conn.execute('SELECT status FROM acquisition_host_permits').fetchone()[0],'RELEASED')

    def test_redirect_defer_records_dispatched_attempt_and_never_false_success(self):
        from scrapper.db.document_download_queue_repo import materialize_download_queue
        from scrapper.workflows.documents.download import run_document_download_worker
        from scrapper.tests.test_document_download_queue import FakeHealth
        self.fixture.register('shared',('presentation',)); materialize_download_queue(self.conn,scope_id='shared')
        url=self.conn.execute('SELECT requested_url FROM artifact_download_queue').fetchone()[0]
        target='https://held.example/report.pdf'
        permits.acquire(self.conn,target,mode='rating',owner='other',now=time.time(),lease_seconds=100)
        redirect=FakeResponse(b'',url=url,content_type='text/html');redirect.status_code=302;redirect.headers['Location']=target
        fake=FakeSession({url:redirect})
        def adapter(*a,**k):return HostSession(*a,**k,offline_session=fake)
        with patch('scrapper.operations.workers.host_requests.HostSession',side_effect=adapter):
            result=run_document_download_worker(self.conn,scope_id='shared',limit=1,shared_permits=True,
                snapshot_root=self.root/'bytes',document_store=self.root,health_check=FakeHealth,throttle_seconds=0)
        self.assertEqual(result['outcome_counts'],{'HOST_BUSY':1})
        self.assertEqual(self.conn.execute('SELECT attempt_count FROM artifact_download_queue').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT result_status FROM artifact_download_attempts').fetchone()[0],'rejected_http_status')
        self.assertEqual(json.loads(self.conn.execute('SELECT error_json FROM artifact_download_attempts').fetchone()[0])['code'],'HOST_BUSY')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM artifact_contents').fetchone()[0],0)
        self.assertEqual(fake.calls,[('GET',url)])
    def test_recording_stream_uses_permit_and_preserves_immutable_bytes(self):
        from scrapper.documents.downloader import download_recording_to_store
        from scrapper.tests.test_document_download_queue import MP3_BYTES
        url='https://recording.example/a'
        fake=FakeSession({url:FakeResponse(MP3_BYTES,url=url,content_type='audio/mpeg')})
        session=HostSession(self.conn,mode='generic',owner='recording',policy=DownloadPolicy(),check=lambda:None,offline_session=fake)
        result=download_recording_to_store(url,snapshot_root=self.root/'bytes',session=session)
        self.assertTrue(result.is_validated);self.assertEqual(Path(result.stored_path).read_bytes(),MP3_BYTES)
        self.assertEqual(self.conn.execute('SELECT status FROM acquisition_host_permits').fetchone()[0],'RELEASED')
    def test_policy_refusal_skips_and_refunds_without_http(self):
        from scrapper.db.document_download_queue_repo import materialize_download_queue
        from scrapper.workflows.documents.download import run_document_download_worker
        from scrapper.tests.test_document_download_queue import FakeHealth
        self.fixture.register('shared',('presentation',));materialize_download_queue(self.conn,scope_id='shared')
        url=self.conn.execute('SELECT requested_url FROM artifact_download_queue').fetchone()[0]
        host=permits.policy_for(url).host
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO credit_rating_host_policy VALUES(?,'BLOCKED_TERMS',0,4,'test',NULL,'2026-10-05T00:00:00+00:00')",(host,))
        result=run_document_download_worker(self.conn,scope_id='shared',limit=1,shared_permits=True,
            snapshot_root=self.root/'bytes',document_store=self.root,health_check=FakeHealth,throttle_seconds=0)
        row=self.conn.execute('SELECT status,attempt_count FROM artifact_download_queue').fetchone()
        self.assertEqual(tuple(row),('SKIPPED_POLICY',0))


class RatingWorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        from scrapper.tests.test_credit_rating_acquisition import CreditRatingAcquisitionTests
        self.fixture=CreditRatingAcquisitionTests(); self.fixture.setUp()
        self.conn=self.fixture.conn; self.root=self.fixture.root
        from scrapper.pipeline.credit_rating_downloads import enqueue_credit_ratings_for_accepted_parse
        enqueue_credit_ratings_for_accepted_parse(self.conn,parse_id=self.fixture.parse_id)
    def tearDown(self): self.fixture.tearDown()
    def run_worker(self):
        from scrapper.pipeline.credit_rating_downloads import run_credit_rating_download_worker
        from scrapper.tests.test_document_download_queue import FakeHealth
        with patch('scrapper.pipeline.credit_rating_downloads.assert_document_store_ready',FakeHealth):
            return run_credit_rating_download_worker(self.conn,limit=1,shared_permits=True,throttle_seconds=0,snapshot_root=self.root/'bytes',document_store=self.root)
    def test_rating_wait_refunds_claim_without_resolution_attempt(self):
        url=self.conn.execute('SELECT requested_url FROM credit_rating_download_queue').fetchone()[0]
        permits.acquire(self.conn,url,mode='generic',owner='other',now=time.time(),lease_seconds=100)
        result=self.run_worker(); self.assertEqual(result['outcome_counts'],{'HOST_BUSY':1})
        self.assertEqual(self.conn.execute('SELECT attempt_count FROM credit_rating_download_queue').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM credit_rating_resolution_attempts').fetchone()[0],0)
    def test_rating_shared_success_keeps_logical_artifact(self):
        job=self.conn.execute('SELECT * FROM credit_rating_download_queue').fetchone(); url=job['requested_url']
        fake=FakeSession({url:FakeResponse(PDF_BYTES,url=url,content_type='application/pdf')})
        def adapter(*a,**k): return HostSession(*a,**k,offline_session=fake)
        with patch('scrapper.operations.workers.host_requests.HostSession',side_effect=adapter),patch('scrapper.documents.credit_rating_resolver._browser_resolution',side_effect=AssertionError('browser bypass')):
            result=self.run_worker()
        self.assertEqual(result['outcome_counts'],{'DIRECT_REPORT_VALIDATED':1})
        self.assertEqual(self.conn.execute('SELECT artifact_id FROM artifact_contents').fetchone()[0],job['artifact_id'])
        self.assertEqual(self.conn.execute('SELECT status FROM acquisition_host_permits').fetchone()[0],'RELEASED')
    def test_enabled_rating_never_invokes_unpermitted_browser_fallback(self):
        url=self.conn.execute('SELECT requested_url FROM credit_rating_download_queue').fetchone()[0]
        fake=FakeSession({url:FakeResponse(b'<html>Landing page</html>',url=url,content_type='text/html')})
        def adapter(*a,**k): return HostSession(*a,**k,offline_session=fake)
        with patch('scrapper.operations.workers.host_requests.HostSession',side_effect=adapter),patch('scrapper.documents.credit_rating_resolver._browser_resolution',side_effect=AssertionError('browser bypass')) as browser:
            result=self.run_worker()
        browser.assert_not_called(); self.assertEqual(result['outcome_counts'],{'INTERACTION_REQUIRED':1})

    def test_partial_rating_resolution_keeps_original_html_and_dispatched_count(self):
        url=self.conn.execute('SELECT requested_url FROM credit_rating_download_queue').fetchone()[0]
        target='https://www.indiaratings.co.in/report.pdf'
        permits.acquire(self.conn,target,mode='generic',owner='held',now=time.time(),lease_seconds=100)
        html=('<html><a href="'+target+'">Download rating report PDF</a></html>').encode()
        fake=FakeSession({url:FakeResponse(html,url=url,content_type='text/html')})
        def adapter(*a,**k):return HostSession(*a,**k,offline_session=fake)
        with patch('scrapper.operations.workers.host_requests.HostSession',side_effect=adapter):result=self.run_worker()
        self.assertEqual(result['outcome_counts'],{'HOST_BUSY':1})
        self.assertEqual(self.conn.execute('SELECT attempt_count FROM credit_rating_download_queue').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT resolution_status FROM credit_rating_resolution_attempts').fetchone()[0],'SHARED_HOST_DEFERRED')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM credit_rating_resolution_steps').fetchone()[0],2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM artifact_contents').fetchone()[0],0)
        row=self.conn.execute("SELECT local_path FROM response_blobs WHERE content_sha256=?",(__import__('hashlib').sha256(html).hexdigest(),)).fetchone()
        self.assertEqual(Path(row[0]).read_bytes(),html)


def race_adapter(db_path, mode, url, start, release, results):
    conn=connect(Path(db_path))
    fake=FakeSession({url:FakeResponse(PDF_BYTES,url=url,content_type='application/pdf')})
    session=HostSession(conn,mode=mode,owner=mode,policy=DownloadPolicy(),check=lambda:None,
        offline_session=fake,wait_seconds=0)
    start.wait(5)
    try:
        response=session.get(url,allow_redirects=False)
        results.put((mode,'GRANTED'))
        release.wait(5)
        response.close()
    except AdministrativeDefer as exc:
        results.put((mode,exc.reason))
    finally:
        session.close(); conn.close()


class ExecutorRaceTests(unittest.TestCase):
    setUp = ExecutorTests.setUp
    tearDown = ExecutorTests.tearDown
    adapter = ExecutorTests.adapter
    response = ExecutorTests.response
    # Do not inherit the unrelated executor tests into this separate case.
    def test_generic_rating_processes_race_across_aliases(self):
        context=multiprocessing.get_context('spawn'); start=context.Event(); release=context.Event(); results=context.Queue()
        path=str(self.root/'db.sqlite')
        processes=[context.Process(target=race_adapter,args=(path,mode,url,start,release,results))
            for mode,url in [('generic','https://careratings.com/a'),('rating','https://www.careratings.com/b')]]
        try:
            for p in processes:p.start()
            start.set(); outcomes=[results.get(timeout=5) for _ in processes]
            self.assertEqual(sum(status=='GRANTED' for _,status in outcomes),1)
            self.assertEqual(sum(status in {'HOST_BUSY','HOST_DEFERRED'} for _,status in outcomes),1)
            # A held publisher never occupies an unrelated publisher's capacity.
            session=self.adapter(FakeSession({'https://free.example/a':self.response('https://free.example/a')}))
            download_url('https://free.example/a',artifact_type='presentation',session=session)
        finally:
            release.set()
            for p in processes:
                p.join(5)
                if p.is_alive():p.kill();p.join()
        self.assertTrue(all(p.exitcode==0 for p in processes))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM acquisition_host_permits WHERE status='ACTIVE'").fetchone()[0],0)


class DifferentialIntegrationTests(unittest.TestCase):
    setUp = QueueIntegrationTests.setUp
    tearDown = QueueIntegrationTests.tearDown

    # Attach bounded differential integration cases to the established queue fixture.
    def test_enabled_generic_matches_every_legacy_row_and_sampled_byte(self):
        import hashlib
        import sqlite3
        from datetime import datetime,timezone
        from scrapper.db import repo,document_work_claims
        from scrapper.db.document_download_queue_repo import materialize_download_queue
        from scrapper.workflows.documents.download import run_document_download_worker
        from scrapper.tests.test_document_download_queue import FakeHealth
        self.fixture.register('shared',('presentation',)); materialize_download_queue(self.conn,scope_id='shared')
        original=sqlite3.connect(':memory:'); self.conn.backup(original)
        url=self.conn.execute('SELECT requested_url FROM artifact_download_queue').fetchone()[0]
        fixed='2026-10-05T00:00:00+00:00'; epoch=datetime.fromisoformat(fixed).timestamp()
        class FixedDatetime(datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,5,tzinfo=tz)
        def snapshot():
            return {name:sorted([list(r) for r in self.conn.execute('SELECT * FROM "'+name+'"')],key=lambda r:json.dumps(r,sort_keys=True))
                for (name,) in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def bytes_snapshot():
            return {str(p.relative_to(self.root/'bytes')):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (self.root/'bytes').rglob('*') if p.is_file()}
        runs=[]
        for enabled in [False,True]:
            if enabled:original.backup(self.conn)
            fake=FakeSession({url:FakeResponse(PDF_BYTES,url=url,content_type='application/pdf')})
            def adapter(*a,**k):return HostSession(*a,**k,offline_session=fake)
            with patch('scrapper.operations.workers.host_requests.HostSession',side_effect=adapter),patch.object(repo,'datetime',FixedDatetime),patch.object(document_work_claims,'datetime',FixedDatetime),patch('secrets.token_hex',return_value='fixed-token'):
                result=run_document_download_worker(self.conn,scope_id='shared',limit=1,worker='frozen',shared_permits=enabled,
                    snapshot_root=self.root/'bytes',document_store=self.root,health_check=FakeHealth,throttle_seconds=0,
                    now=lambda:fixed,session_factory=lambda:fake,monotonic=lambda:0)
            runs.append((result,snapshot(),bytes_snapshot()))
        original.close()
        self.assertEqual(runs[0][0],runs[1][0]); self.assertEqual(runs[0][2],runs[1][2])
        left,right=runs[0][1],runs[1][1]
        self.assertEqual(set(left),set(right))
        for name in left:
            if name in {'acquisition_host_state','acquisition_host_permits'}:continue
            self.assertEqual(left[name],right[name],name)
        group=permits.policy_for(url).group; host=permits.policy_for(url).host
        self.assertEqual(left['acquisition_host_state'],[]);self.assertEqual(left['acquisition_host_permits'],[])
        self.assertEqual(right['acquisition_host_state'],[[group,epoch+4,epoch,0.0,epoch,0,None,'success','c20.1-v1']])
        self.assertEqual(right['acquisition_host_permits'],[['fixed-token',group,host,'generic','frozen','RELEASED',epoch,epoch+DownloadPolicy().total_timeout_seconds+30,epoch,'c20.1-v1','{}']])
