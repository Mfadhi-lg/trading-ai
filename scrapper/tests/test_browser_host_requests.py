"""Offline browser interception, trusted bridge, retry and physical-I/O contracts."""
import ast
import inspect
import json
import sqlite3
import time
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from scrapper.core.host_policy import OperatorAuthorization
from scrapper.core.host_request_contracts import AdministrativeDefer
from scrapper.db import host_permits
from scrapper.db.browser_host_defer import defer, assert_unexpired
from scrapper.db.document_work_claims import OwnershipLost
from scrapper.db.documents.recovery import browser_rating as ledger
from scrapper.documents.recovery.browser_rating.host_permit_bridge import Broker
from scrapper.documents.recovery.browser_rating import bridge
from scrapper.tests.test_host_request_executors import Clock, StallHop
from scrapper.tests.test_credit_rating_acquisition import FakeSession, FakeResponse, PDF_BYTES
from ops.host.browser_host_requests import BrowserRequests, handshake


class BrowserHostTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:'); self.conn.row_factory=sqlite3.Row
        self.clock=Clock()
        root=Path(ledger.__file__).parents[2]/'migrations'
        for name in ['065_operator_browser_rating_recovery.sql','066_browser_rating_recovery_backoff.sql','071_shared_host_permits.sql']:
            self.conn.executescript((root/name).read_text())
        self.conn.executescript("""ALTER TABLE browser_rating_recovery_jobs ADD COLUMN claim_token TEXT;
          CREATE TABLE schema_migrations(version TEXT);
          INSERT INTO schema_migrations VALUES('071_shared_host_permits.sql');
          CREATE TABLE credit_rating_host_policy(host TEXT,automation_status TEXT,max_concurrency INTEGER,min_delay_seconds REAL);
          INSERT INTO credit_rating_host_policy VALUES('www.icra.in','BLOCKED_TERMS',0,4);""")
        self.auth=OperatorAuthorization('run','reviewed-offline-fixture',('www.icra.in','icra.in','free.example'),self.clock()+7200,('www.icra.in','icra.in'))
        self.broker=Broker(self.conn,'run',self.auth,now=self.clock)
        self.stamp=lambda:datetime.fromtimestamp(self.clock(),timezone.utc).isoformat()
        with self.conn:
            self.conn.execute("""INSERT INTO browser_rating_recovery_jobs(run_id,artifact_id,artifact_source_id,requested_url,target_host,company_name,created_at_utc,updated_at_utc)
              VALUES('run',1,1,'https://www.icra.in/report','www.icra.in','Example',?,?)""",(self.stamp(),self.stamp()))
        self.job=ledger.claim(self.conn,'run',['www.icra.in'],'browser',utc_now=self.stamp)
        self.stopping=Mock();self.stopping.is_set.return_value=False
    def tearDown(self): self.conn.close()
    def exchange(self, request):
        current=self.conn.execute('SELECT * FROM browser_rating_recovery_jobs WHERE browser_job_id=1').fetchone()
        if current['status']!='RUNNING' or request['claim_token']!=current['claim_token'] or request['claim_attempt_count']!=current['attempt_count']:
            raise ValueError('Browser claim replaced')
        assert_unexpired(current,self.stamp())
        self.assertFalse(self.conn.in_transaction)
        return self.broker.handle(request,self.job)
    def adapter(self,responses=None,**kwargs):
        return BrowserRequests(self.exchange,self.job,self.stopping,authorization=self.auth,
            offline_session=FakeSession(responses or {}),now=self.clock,sleep=self.clock.sleep,**kwargs)
    def response(self,url,body=PDF_BYTES,status=200,headers=None):
        r=FakeResponse(body,url=url,content_type='application/pdf');r.status_code=status
        if headers:r.headers.update(headers)
        return r
    def route(self,url,method='GET',resource_type='document',data=None):
        request=SimpleNamespace(url=url,method=method,resource_type=resource_type,post_data_buffer=data,all_headers=lambda:{'User-Agent':'offline-browser'})
        return SimpleNamespace(request=request,abort=Mock(),fulfill=Mock(),continue_=Mock(),fetch=Mock())
    def test_navigation_subrequest_form_and_download_each_metered(self):
        urls=['https://www.icra.in/report','https://www.icra.in/js','https://icra.in/form','https://www.icra.in/download']
        a=self.adapter({u:self.response(u) for u in urls})
        for i,u in enumerate(urls):
            r=self.route(u,method='POST' if i==2 else 'GET',resource_type='script' if i==1 else 'document',data=b'id=123' if i==2 else None)
            a.route(r);r.fulfill.assert_called_once();r.abort.assert_not_called();r.continue_.assert_not_called();r.fetch.assert_not_called()
        rows=self.conn.execute('SELECT host,acquisition_mode,status,acquired_at,released_at,authorization_json FROM acquisition_host_permits ORDER BY acquired_at').fetchall()
        self.assertEqual(len(rows),4);self.assertTrue(all(r[1:3]==('browser','RELEASED') for r in map(tuple,rows)))
        self.assertTrue(all(json.loads(r[5])['reference']==self.auth.reference for r in rows))
        self.assertTrue(all(rows[i+1][3]>=rows[i][3]+4 and rows[i+1][3]>=rows[i][4] for i in range(3)))
        a.close()
    def test_redirect_is_fulfilled_and_followup_reintercepted(self):
        first='https://www.icra.in/report';last='https://free.example/pdf'
        a=self.adapter({first:self.response(first,b'',302,{'Location':last}),last:self.response(last)})
        route=self.route(first);a.route(route);self.assertEqual(route.fulfill.call_args.kwargs['status'],302)
        route=self.route(last);a.route(route);self.assertEqual(route.fulfill.call_args.kwargs['body'],PDF_BYTES)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM acquisition_host_permits').fetchone()[0],2)
    def test_viewer_fallback_manual_redirects_never_api_request_context(self):
        first='https://www.icra.in/report';last='https://free.example/pdf'
        a=self.adapter({first:self.response(first,b'',302,{'Location':last}),last:self.response(last)})
        self.assertEqual(a.viewer(first)[3],PDF_BYTES)
        self.assertEqual(a.session.dispatched,2)
    def test_denied_redirect_and_external_subrequest_abort_without_dispatch(self):
        url='https://unapproved.example/pdf';a=self.adapter({url:self.response(url)})
        r=self.route(url);a.route(r);r.abort.assert_called_once();r.fulfill.assert_not_called()
        self.assertEqual(a.session.dispatched,0)
        with self.assertRaises(AdministrativeDefer):a.check()
        result=a.defer(a.failure);self.assertEqual(result['status'],'PENDING')
        self.assertEqual(self.conn.execute('SELECT attempt_count FROM browser_rating_recovery_jobs').fetchone()[0],0)
    def test_busy_publisher_defers_and_refunds_only_before_dispatch(self):
        permit=host_permits.acquire(self.conn,'https://icra.in/other',mode='browser',owner='other',now=self.clock(),lease_seconds=100,authorization=self.auth).permit
        a=self.adapter();r=self.route(self.job['requested_url']);a.route(r)
        self.assertIsInstance(a.failure,AdministrativeDefer);a.defer(a.failure)
        row=self.conn.execute('SELECT status,attempt_count FROM browser_rating_recovery_jobs').fetchone();self.assertEqual(tuple(row),('PENDING',0))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM acquisition_host_permits').fetchone()[0],1)
        self.assertIsNone(ledger.claim(self.conn,'run',['www.icra.in'],'another',utc_now=self.stamp))
        self.clock.sleep(101)
        new=ledger.claim(self.conn,'run',['www.icra.in'],'another',utc_now=self.stamp);self.assertIsNotNone(new)
        self.assertNotEqual(new['claim_token'],self.job['claim_token'])
    def test_dispatched_then_unapproved_redirect_retains_request_budget(self):
        first=self.job['requested_url'];a=self.adapter({first:self.response(first,b'',302,{'Location':'https://unapproved.example/a'})})
        a.route(self.route(first));a.route(self.route('https://unapproved.example/a'));a.defer(a.failure)
        self.assertEqual(self.conn.execute('SELECT attempt_count FROM browser_rating_recovery_jobs').fetchone()[0],1)
        evidence=json.loads(self.conn.execute('SELECT evidence_json FROM browser_rating_recovery_jobs').fetchone()[0]);self.assertEqual(evidence['dispatched_requests'],1)
    def test_429_retry_after_is_persisted_and_not_cleared_by_other_host(self):
        first=self.job['requested_url'];free='https://free.example/a'
        a=self.adapter({first:self.response(first,b'',429,{'Retry-After':'90000'}),free:self.response(free)})
        start=self.clock();a.fetch('GET',first);a.fetch('GET',free)
        row=self.conn.execute("SELECT blocked_until,retry_after_raw FROM acquisition_host_state WHERE publisher_group='icra'").fetchone()
        self.assertGreaterEqual(row[0],start+90000);self.assertEqual(row[1],'90000')
        with self.assertRaises(AdministrativeDefer):a.fetch('GET',first)
    def test_403_and_circuit_defaults_persist_across_new_broker(self):
        first=self.job['requested_url'];a=self.adapter({first:self.response(first,b'',403)})
        start=self.clock();a.fetch('GET',first)
        self.assertGreaterEqual(self.conn.execute("SELECT blocked_until FROM acquisition_host_state WHERE publisher_group='icra'").fetchone()[0],start+86400)
        broker=Broker(self.conn,'run',self.auth,now=self.clock)
        self.assertEqual(broker.handle({'action':'host_acquire','url':first},self.job)['reason'],'HOST_DEFERRED')
    def test_expired_or_mismatched_receipt_refused_before_claim(self):
        for auth in [None,replace(self.auth,run_id='wrong'),replace(self.auth,expires_at=self.clock()-1)]:
            with self.assertRaises(ValueError):Broker(self.conn,'run',auth,now=self.clock)
        before=self.conn.execute('SELECT count(*) FROM acquisition_host_permits').fetchone()[0]
        with patch('sys.platform','linux'):
            wrong=json.loads(json.dumps(self.broker.setup()));wrong['authorization']['reference']='wrong'
            with self.assertRaises(ValueError):handshake(lambda r:wrong,self.auth,now=self.clock)
            with self.assertRaises(ValueError):handshake(lambda r:dict(self.broker.setup(),server_now=self.clock()+10),self.auth,now=self.clock)
        self.assertEqual(before,self.conn.execute('SELECT count(*) FROM acquisition_host_permits').fetchone()[0])
    def test_missing_migration_and_nonlinux_fail_closed(self):
        self.conn.execute('DELETE FROM schema_migrations');self.conn.commit()
        with self.assertRaises(RuntimeError):Broker(self.conn,'run',self.auth,now=self.clock)
        with patch('sys.platform','darwin'):
            with self.assertRaises(RuntimeError):handshake(Mock(),self.auth,now=self.clock)
    def test_stale_claim_cannot_dispatch_finish_or_refund(self):
        with self.conn:self.conn.execute("UPDATE browser_rating_recovery_jobs SET claim_token='new'")
        a=self.adapter();r=self.route(self.job['requested_url']);a.route(r)
        self.assertIsInstance(a.failure,ValueError);self.assertEqual(a.session.dispatched,0)
        before=tuple(self.conn.execute('SELECT * FROM browser_rating_recovery_jobs').fetchone())
        with self.assertRaises(OwnershipLost):defer(self.conn,self.job,now=self.clock(),retry_at=self.clock()+60,dispatched=0,reason='HOST_BUSY')
        self.assertEqual(before,tuple(self.conn.execute('SELECT * FROM browser_rating_recovery_jobs').fetchone()))
    def test_old_permit_cannot_be_used_for_another_claim(self):
        result=self.broker.handle({'action':'host_acquire','url':self.job['requested_url']},self.job)
        with self.assertRaises(ValueError):self.broker.handle({'action':'host_dispatch','permit_token':result['permit']['token']},dict(self.job,claim_token='new'))
    def test_transaction_is_never_held_during_fake_io(self):
        first=self.job['requested_url'];a=self.adapter({first:self.response(first)})
        original=a.session.offline_session.get
        def fake(*args,**kwargs):self.assertFalse(self.conn.in_transaction);return original(*args,**kwargs)
        a.session.offline_session.get=fake;a.fetch('GET',first);self.assertFalse(self.conn.in_transaction)
    def test_service_workers_websockets_and_unmetered_routes_blocked(self):
        a=self.adapter();context=SimpleNamespace(route=Mock(),route_web_socket=Mock());a.install(context)
        socket=Mock();context.route_web_socket.call_args.args[1](socket);socket.close.assert_called_once();socket.connect_to_server.assert_not_called()
        with self.assertRaises(RuntimeError):a.install(SimpleNamespace(route=Mock()))
        route=self.route(self.job['requested_url'],resource_type='image');a.route(route);route.abort.assert_called_once();self.assertEqual(a.session.dispatched,0)
        route=self.route(self.job['requested_url'],method='PUT');a.route(route);route.abort.assert_called_once();self.assertEqual(a.session.dispatched,0)
    def test_shutdown_aborts_before_dispatch(self):
        self.stopping.is_set.return_value=True;a=self.adapter();r=self.route(self.job['requested_url']);a.route(r)
        self.assertIsInstance(a.failure,RuntimeError);self.assertEqual(a.session.dispatched,0)
    def test_response_transport_headers_are_removed_after_decompression(self):
        url=self.job['requested_url'];a=self.adapter({url:self.response(url,headers={'Content-Encoding':'gzip','Content-Length':'2','Content-Type':'application/pdf'})})
        r=self.route(url);a.route(r);headers=r.fulfill.call_args.kwargs['headers'];self.assertNotIn('Content-Encoding',headers);self.assertNotIn('Content-Length',headers)
    def test_permit_expiry_bounds_independent_http_child_deadline(self):
        from scrapper.operations.workers.host_requests import _Response
        url=self.job['requested_url'];a=self.adapter();permit=host_permits.Permit('t','icra','www.icra.in','browser','browser',self.clock()+20)
        response=_Response(a.session,permit);self.assertLessEqual(response.deadline,time.monotonic()+15.1)
    def test_actual_killable_browser_request_cancels_before_release(self):
        url='https://free.example/stall';a=BrowserRequests(self.exchange,self.job,self.stopping,authorization=self.auth,now=self.clock,sleep=self.clock.sleep,hop_factory=StallHop)
        a.session.policy=replace(a.session.policy,total_timeout_seconds=.7)
        started=time.monotonic()
        with self.assertRaises(Exception):a.fetch('GET',url)
        self.assertLess(time.monotonic()-started,5)
        self.assertTrue(all(not r.hop.process.is_alive() for r in a.session.responses))
        self.assertEqual(self.conn.execute('SELECT status FROM acquisition_host_permits').fetchone()[0],'RELEASED')
    def test_bridge_shared_disabled_rejects_host_messages(self):
        hooks={n:Mock() for n in ['worker_state','release_owned_jobs','claim','cool_down','finish','validate_report','company_names','import_report','DownloadPolicy','DownloadResult','utc_now']}
        replies=[];bridge.serve(self.conn,SimpleNamespace(run_id='run'),lines=[json.dumps({'action':'shared_setup'}),json.dumps({'action':'host_acquire'})],emit=replies.append,**hooks)
        self.assertTrue(all('not enabled' in r['error'] for r in replies));hooks['claim'].assert_not_called();hooks['finish'].assert_not_called()
    def test_new_modules_import_without_io(self):
        import subprocess,sys
        code="""
from unittest.mock import patch
import socket,sqlite3,subprocess,threading
with patch.object(socket.socket,'connect',side_effect=AssertionError('network')),patch.object(sqlite3,'connect',side_effect=AssertionError('db')),patch.object(subprocess,'Popen',side_effect=AssertionError('process')),patch.object(threading.Thread,'start',side_effect=AssertionError('thread')):
 import ops.host.browser_host_requests
 import scrapper.documents.recovery.browser_rating.host_permit_bridge
 import scrapper.db.browser_host_defer
"""
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stderr)

    def test_duplicate_dispatch_and_completion_with_active_request_refused(self):
        result=self.broker.handle({'action':'host_acquire','url':self.job['requested_url']},self.job)
        token=result['permit']['token']
        self.broker.handle({'action':'host_dispatch','permit_token':token},self.job)
        with self.assertRaises(ValueError):self.broker.handle({'action':'host_dispatch','permit_token':token},self.job)
        with self.assertRaises(ValueError):self.broker.assert_drained(self.job)
        with self.assertRaises(ValueError):self.broker.handle({'action':'host_defer','reason':'HOST_BUSY','retry_at':self.clock()+60},self.job)
        self.broker.handle({'action':'host_finish','permit_token':token,'outcome':'success'},self.job)
        self.broker.assert_drained(self.job)
    def test_preexisting_generic_request_blocks_browser_and_free_host_progresses(self):
        host_permits.acquire(self.conn,'https://free.example/other',mode='generic',owner='generic',now=self.clock(),lease_seconds=100)
        a=self.adapter({self.job['requested_url']:self.response(self.job['requested_url'])})
        with self.assertRaises(AdministrativeDefer):a.fetch('GET','https://free.example/report')
        a.fetch('GET',self.job['requested_url'])
        self.assertEqual(a.session.dispatched,1)
    def test_transport_failure_circuit_and_restart_are_shared(self):
        url='https://free.example/a'
        for _ in range(3):
            a=self.adapter()
            def fail(*args,**kwargs):raise __import__('requests').RequestException('offline transport fault')
            a.session.offline_session.get=fail
            with self.assertRaises(Exception):a.fetch('GET',url)
            self.clock.sleep(4)
        state=self.conn.execute("SELECT consecutive_failures,circuit_until FROM acquisition_host_state WHERE publisher_group='host:free.example'").fetchone()
        self.assertEqual(state[0],3);self.assertGreater(state[1],self.clock())
        newer=Broker(self.conn,'run',self.auth,now=self.clock)
        self.assertEqual(newer.handle({'action':'host_acquire','url':url},self.job)['reason'],'HOST_DEFERRED')
    def test_cookies_from_http_child_update_browser_and_viewer_uses_browser_cookies(self):
        import requests
        a=self.adapter();a.session.cookies.set_cookie(requests.cookies.create_cookie('session','abc',domain='.icra.in',path='/',secure=True,rest={'HttpOnly':None,'SameSite':'Lax'}))
        a.context=SimpleNamespace(add_cookies=Mock(),cookies=Mock(return_value=[{'name':'javascript','value':'def'}]))
        a.sync_cookies();cookie=a.context.add_cookies.call_args.args[0][0]
        self.assertEqual(cookie['name'],'session');self.assertTrue(cookie['httpOnly']);self.assertEqual(cookie['sameSite'],'Lax')
        with patch.object(a,'fetch',return_value=(200,self.job['requested_url'],{},PDF_BYTES)) as fetch:
            a.viewer(self.job['requested_url'])
        self.assertEqual(fetch.call_args.kwargs['headers']['Cookie'],'javascript=def')
    def test_expired_receipt_does_not_claim_again(self):
        self.clock.sleep(7201)
        with self.assertRaises(ValueError):self.broker.setup()
    def test_actual_shared_bridge_claim_permit_import_and_stale_message(self):
        # Drive the real line dispatcher, reading its emitted claim/token between
        # messages, with persistence/validation hooks at their existing boundary.
        with self.conn:
            self.conn.execute("UPDATE browser_rating_recovery_jobs SET status='PENDING',attempt_count=0,lease_owner=NULL,lease_expires_at_utc=NULL")
        replies=[];claim={};token={}
        hooks={n:Mock() for n in ['worker_state','release_owned_jobs','cool_down','finish','validate_report','company_names','import_report','DownloadPolicy','DownloadResult']}
        hooks.update(claim=lambda c,r,h,o,ids:ledger.claim(c,r,h,o,ids,utc_now=self.stamp),utc_now=self.stamp)
        hooks['release_owned_jobs'].return_value=0
        hooks['DownloadPolicy'].return_value=SimpleNamespace(max_bytes=1024)
        hooks['validate_report'].return_value={'company_name_match':True}
        hooks['company_names'].return_value=['Example']
        hooks['import_report'].return_value=('SUCCEEDED',9,{})
        def finish(conn,job,status,*args,**kwargs):ledger.finish(conn,job,status,utc_now=self.stamp)
        hooks['finish']=Mock(side_effect=finish)
        def identity(action,**kw):return dict(action=action,browser_job_id=claim['browser_job_id'],claim_token=claim['claim_token'],claim_attempt_count=claim['attempt_count']+1,**kw)
        def lines():
            yield json.dumps({'action':'shared_setup','protocol_version':1})
            yield json.dumps({'action':'claim','protocol_version':1})
            claim.update(replies[-1]['job'])
            yield json.dumps(identity('host_acquire',url=claim['requested_url']))
            token.update(replies[-1]['permit'])
            yield json.dumps(identity('host_dispatch',permit_token=token['token']))
            yield json.dumps(identity('import',body_base64='JVBERi0=',final_url=claim['requested_url']))
            yield json.dumps(identity('host_finish',permit_token=token['token'],outcome='success'))
            yield json.dumps(identity('import',body_base64='JVBERi0=',final_url=claim['requested_url']))
            yield json.dumps(identity('host_dispatch',permit_token=token['token']))
        with patch('scrapper.documents.recovery.browser_rating.host_permit_bridge.Broker',side_effect=lambda c,r,a:Broker(c,r,a,now=self.clock)):
            bridge.serve(self.conn,SimpleNamespace(run_id='run'),lines=lines(),emit=replies.append,shared_permits=True,authorization=self.auth,**hooks)
        self.assertTrue(replies[0]['shared_permits']);self.assertEqual(replies[0]['protocol_version'],1)
        self.assertIn('Finish all browser requests',replies[4]['error']);self.assertEqual(replies[6]['status'],'SUCCEEDED')
        self.assertIn('not owned',replies[7]['error']);hooks['import_report'].assert_called_once();hooks['finish'].assert_called_once()
    def test_actual_shared_bridge_defer_refund_and_lease_cleanup(self):
        with self.conn:self.conn.execute("UPDATE browser_rating_recovery_jobs SET status='PENDING',attempt_count=0,lease_owner=NULL,lease_expires_at_utc=NULL")
        replies=[];job={};renewal=Mock()
        hooks={n:Mock() for n in ['worker_state','release_owned_jobs','cool_down','finish','validate_report','company_names','import_report','DownloadPolicy','DownloadResult']}
        hooks.update(claim=lambda c,r,h,o,ids:ledger.claim(c,r,h,o,ids,utc_now=self.stamp),utc_now=self.stamp,renewal_factory=lambda c,j:renewal)
        renewal.__enter__=Mock(return_value=renewal);renewal.__exit__=Mock(return_value=False)
        def lines():
            yield json.dumps({'action':'claim'})
            job.update(replies[-1]['job'])
            yield json.dumps(dict(action='host_defer',browser_job_id=job['browser_job_id'],claim_token=job['claim_token'],claim_attempt_count=1,reason='HOST_DEFERRED',retry_at=self.clock()+3600))
            yield json.dumps({'action':'claim'})
        with patch('scrapper.documents.recovery.browser_rating.host_permit_bridge.Broker',side_effect=lambda c,r,a:Broker(c,r,a,now=self.clock)):
            bridge.serve(self.conn,SimpleNamespace(run_id='run'),lines=lines(),emit=replies.append,shared_permits=True,authorization=self.auth,**hooks)
        self.assertEqual(replies[1]['status'],'PENDING');self.assertIsNone(replies[2]['job']);renewal.__exit__.assert_called_once()
        self.assertEqual(self.conn.execute('SELECT attempt_count FROM browser_rating_recovery_jobs').fetchone()[0],0)
        hooks['finish'].assert_not_called();hooks['import_report'].assert_not_called()

    def test_shared_runner_uses_fresh_closed_context_and_metered_download(self):
        import argparse,base64,hashlib,io,sys,tempfile
        from contextlib import redirect_stdout
        from ops.host import browser_rating_runner as runner
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name)/'report.pdf';path.write_bytes(PDF_BYTES)
        navigation=self.job['requested_url'];pdf='https://www.icra.in/pdf'
        events=[];messages=[];contexts=[]
        test=self
        class Context:
            request=SimpleNamespace(get=Mock(side_effect=AssertionError('Unmetered API request')))
            def route(self,pattern,handler):self.handler=handler
            def route_web_socket(self,pattern,handler):events.append('websocket_block')
            def new_page(self):return Page(self)
            def close(self):events.append('context.close')
            def add_cookies(self,cookies):pass
        class Element:
            def __init__(self,context):self.first=self;self.context=context
            def inner_text(self,**kwargs):return 'Rating rationale'
            def filter(self,**kwargs):return self
            def wait_for(self,**kwargs):pass
            def focus(self):pass
            def press(self,key):self.context.handler(test.route(pdf))
        class Download:
            url=pdf;suggested_filename='report.pdf'
            def failure(self):return None
            def path(self):return path
            def delete(self):events.append('download.delete')
        class Expect:
            value=Download()
            def __enter__(self):return self
            def __exit__(self,*args):pass
        class Page:
            url=navigation;mouse=SimpleNamespace(move=lambda *a:None)
            def __init__(self,context):self.context=context
            def goto(self,url,**kwargs):
                route=test.route(url);self.context.handler(route);route.fulfill.assert_called_once()
                return SimpleNamespace(status=200,headers={})
            def locator(self,*args):return Element(self.context)
            def get_by_text(self,*args,**kwargs):return Element(self.context)
            def expect_download(self,**kwargs):return Expect()
        class Browser:
            def new_context(self,**kwargs):
                test.assertEqual(kwargs['service_workers'],'block');contexts.append(kwargs);return Context()
            def close(self):events.append('browser.close')
        class Playwright:
            chromium=SimpleNamespace(launch=lambda **kw:Browser())
            def __enter__(self):return self
            def __exit__(self,*args):pass
        process=SimpleNamespace(stdin=SimpleNamespace(close=lambda:events.append('bridge.close')),wait=lambda **kw:events.append('bridge.wait'))
        def exchange(channel,request):
            messages.append(request)
            if request['action']=='shared_setup':return json.loads(json.dumps(self.broker.setup()))
            if request['action']=='claim':return {'job':self.job}
            if request['action']=='import':
                self.assertEqual(base64.b64decode(request['body_base64']),PDF_BYTES)
                self.broker.assert_drained(self.job)
                ledger.finish(self.conn,self.job,'SUCCEEDED',utc_now=self.stamp)
                return {'status':'SUCCEEDED'}
            if request['action']=='finish':raise AssertionError(request)
            return self.exchange(request)
        hooks=SimpleNamespace(argparse=argparse,subprocess=SimpleNamespace(Popen=lambda *a,**kw:process,PIPE=1),
            bridge_command=lambda:[],bridge_exchange=exchange,sync_playwright=lambda:Playwright(),
            assert_configured_writable_targets=Mock(),assert_host_targets=Mock(),time=time,sys=sys,
            datetime=datetime,timezone=timezone,json=json,base64=base64,hashlib=hashlib,
            TimeoutError=TimeoutError,PublisherRateLimit=runner.PublisherRateLimit)
        fake=FakeSession({navigation:self.response(navigation,b'<html>Rating</html>'),pdf:self.response(pdf)})
        actual=BrowserRequests
        def adapter(*args,**kwargs):return actual(*args,**kwargs,offline_session=fake,now=self.clock,sleep=self.clock.sleep)
        with patch.object(sys,'argv',['runner','--limit','1','--background']),patch('ops.host.browser_host_requests.BrowserRequests',side_effect=adapter),redirect_stdout(io.StringIO()):
            runner._run(hooks=hooks,stopping=self.stopping,shared_permits=True,authorization=self.auth)
        self.assertEqual(len(contexts),1);self.assertIn('import',[m['action'] for m in messages])
        self.assertLess(events.index('context.close'),events.index('browser.close'));self.assertLess(events.index('browser.close'),events.index('bridge.close'))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM acquisition_host_permits WHERE status=\'RELEASED\'').fetchone()[0],2)

    def test_shared_publisher_direct_pdf_never_uses_api_context(self):
        from scrapper.sources.documents import browser_rating as publisher
        context=Mock();context.route_web_socket=Mock();context.cookies.return_value=[]
        browser=Mock();browser.new_context.return_value=context
        url='https://www.icra.in/report.pdf';a=self.adapter({url:self.response(url)})
        context.request.get.side_effect=AssertionError('API context bypass')
        validate=Mock(return_value={'company_name_match':True})
        result,evidence=publisher.fetch_report(browser,url,'Example',shared_requests=a,validate_report=validate)
        self.assertEqual(result.body,PDF_BYTES);self.assertTrue(evidence['direct_pdf_request'])
        context.request.get.assert_not_called();context.close.assert_called_once()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM acquisition_host_permits WHERE status=\'RELEASED\'').fetchone()[0],1)
    def test_container_campaign_uses_shared_adapter_and_ordinary_finish(self):
        import sys
        from contextlib import ExitStack
        from scrapper.commands import browser_rating_recovery as command
        from scrapper.operations.workers import browser_requests as worker
        with self.conn:self.conn.execute('CREATE TABLE artifact_contents(content_id INTEGER,artifact_id INTEGER)')
        conn=self.conn;seen=[];lifecycle=Mock();renewal=Mock();renewal.__enter__=Mock(return_value=renewal);renewal.__exit__=Mock(return_value=False)
        playwright=Mock();playwright.__enter__=Mock(return_value=playwright);playwright.__exit__=Mock(return_value=False)
        playwright.chromium.launch.return_value.is_connected.return_value=True
        stop=Mock();stop.is_set.return_value=False
        url=self.job['requested_url'];actual=BrowserRequests
        def adapter(*args,**kwargs):return actual(*args,**kwargs,offline_session=FakeSession({url:self.response(url)}))
        def fetch(browser,url,name,**kwargs):
            shared=kwargs['shared_requests'];status,final,headers,body=shared.fetch('GET',url);shared.close()
            from scrapper.documents.downloader import DownloadResult
            return DownloadResult(url,final,status,headers,(),'application/pdf','application/pdf',body,len(body),'validated',0),{}
        def importer(c,j,r,e,run):
            self.assertEqual(r.body,PDF_BYTES);seen.append('import');return 'SUCCEEDED',9,e
        def finish(c,j,status,*args):
            self.assertEqual(status,'SUCCEEDED');seen.append('finish');ledger.finish(c,j,status)
            seen.append([tuple(r) for r in c.execute('SELECT acquisition_mode,status FROM acquisition_host_permits')])
        def claim(c,r,h,owner,ids):
            self.job['lease_owner']=owner
            with c:c.execute('UPDATE browser_rating_recovery_jobs SET lease_owner=?',(owner,))
            return self.job
        replacements={'connect':lambda path:conn,'init_db':Mock(),'assert_document_store_ready':Mock(),
            'release_owned_jobs':Mock(),'report':Mock(return_value=[]),'worker_state':Mock(),'emit':Mock(),
            'STOP':stop,'claim':claim,'sync_playwright':lambda:playwright,
            'queue_renewal':lambda *a,**k:renewal,'company_names':Mock(return_value=['Example']),
            '_shared_fetch_report':fetch,'import_report':importer,'finish':finish}
        with ExitStack() as stack:
            for key,value in replacements.items():stack.enter_context(patch.object(command,key,value))
            stack.enter_context(patch.object(sys,'argv',['browser','--run-id','run','--apply','--operator-authorized','--limit','1','--host','www.icra.in']))
            stack.enter_context(patch('scrapper.operations.workers.health.bind_role_health'))
            stack.enter_context(patch.object(worker,'BrowserRequests',side_effect=adapter))
            command._main(lifecycle,shared_permits=True,authorization=self.auth)
        self.assertEqual(seen,['import','finish',[('browser','RELEASED')]])
        renewal.check.assert_called();playwright.chromium.launch.return_value.close.assert_called_once()
    def test_headless_receipt_cannot_expand_cli_host_scope(self):
        import sys
        from scrapper.commands import browser_rating_recovery as command
        conn=self.conn
        with patch.object(command,'connect',return_value=conn),patch.object(command,'init_db'),patch.object(sys,'argv',['browser','--run-id','run','--apply','--operator-authorized','--host','www.crisil.com']),patch.object(command,'sync_playwright') as launch:
            with self.assertRaisesRegex(ValueError,'exceed'):command._main(Mock(),shared_permits=True,authorization=self.auth)
        launch.assert_not_called()

    def test_shared_static_report_retains_document_only_resource_filter(self):
        a=self.adapter();context=Mock();a.install(context,documents_only=True)
        route=self.route('https://www.icra.in/script',resource_type='script');a.route(route)
        route.abort.assert_called_once();self.assertEqual(a.session.dispatched,0)
