"""Offline shared Screener pacing, accounting, ownership and entry contracts."""
import ast
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import requests
from scrapper.db.database import connect
from scrapper.tests.database_fixtures import prepare_database
from scrapper.db import host_permits
from scrapper.db.screener_host_defer import renew, defer, epoch, stamp
from scrapper.db.document_work_claims import OwnershipLost
from scrapper.core.host_request_contracts import AdministrativeDefer
from scrapper.operations.workers.screener_requests import SharedScreenerClient
from scrapper.documents.download.contracts import DownloadPolicy
from scrapper.pipeline.screener_discovery import discover_screener_results
from scrapper.tests.test_screener_discovery import screen_html

NOW='2030-01-01T00:00:00+00:00'
URL='https://www.screener.in/company/ALPHA/'
BODY=b'<div id="company-info" data-company-id="1"></div><div id="top"><a>NSE: ALPHA</a></div><ul id="top-ratios"><li><span class="name">Market Cap</span><span class="value">Rs. 579.5 Cr.</span></li></ul>'

def response(url,body=BODY,status=200,headers=None):
    r=requests.Response();r.url=url;r.status_code=status;r.headers=requests.structures.CaseInsensitiveDict({'Content-Type':'text/html; charset=utf-8',**(headers or {})});r.encoding='utf-8';r._content=body;r._content_consumed=True
    return r

class Clock:
    def __init__(self):self.value=epoch(NOW)
    def __call__(self):return self.value
    def utc(self):return stamp(self.value)
    def sleep(self,seconds):self.value+=seconds

class SharedScreenerTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);self.db=self.root/'fixture.db';prepare_database(self.db)
        self.conn=connect(self.db);self.addCleanup(self.conn.close)
        self.cookie=self.root/'cookie';self.cookie.write_text('sessionid=synthetic-secret')
        self.clock=Clock();self.calls=[]
    def fake(self,url,**kwargs):
        self.assertFalse(self.conn.in_transaction);self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM acquisition_host_permits WHERE status='ACTIVE'").fetchone()[0],1)
        self.calls.append((url,kwargs));return response(url)
    def client(self,**kwargs):
        c=SharedScreenerClient(self.conn,owner='fixture',cookie_path=self.cookie,now=self.clock,sleep=self.clock.sleep,offline_session=SimpleNamespace(get=self.fake),**kwargs);self.addCleanup(c.close);return c
    def seed_queue(self):
        fixture=self.root/'screen';fixture.write_bytes(screen_html().replace(b'/consolidated/',b'/'))
        discover_screener_results(query_role='fixture',query='x',fixture=fixture,db_path=self.db,snapshot_root=self.root/'snapshots',now_utc=self.clock.utc)
    def seed_scope(self):
        from scrapper.db.collection_scope_repo import record_scope
        with self.conn:record_scope(self.conn,path='/company/ALPHA/',company_id=None,value='100',observed_at=NOW,evidence={},next_check=NOW)
    def claim_scope(self):
        self.seed_scope()
        with self.conn:self.conn.execute("UPDATE screener_collection_scope SET claim_token='scope-token',claim_expires_at_utc=?",(stamp(self.clock()+60),))
        return ('scope','/company/ALPHA/','scope-token')
    def hold(self,mode='generic'):
        return host_permits.acquire(self.conn,URL,mode=mode,owner='other-worker',now=self.clock(),lease_seconds=300).permit
    def test_company_and_all_fragments_hold_permits(self):
        c=self.client();self.assertEqual(c.fetch_company(URL).fetch_status,'ok')
        c.fetch_peers('https://www.screener.in/api/company/1/peers/',URL)
        c.fetch_quick_ratios('https://www.screener.in/api/company/1/quick_ratios/',URL)
        c.fetch_commentary('https://www.screener.in/company/1/wiki/',URL)
        rows=self.conn.execute('SELECT acquisition_mode,status,acquired_at FROM acquisition_host_permits ORDER BY acquired_at').fetchall()
        self.assertEqual(len(rows),4);self.assertTrue(all(r[0]=='screener' and r[1]=='RELEASED' for r in rows));self.assertEqual([r[2] for r in rows],[epoch(NOW)+4*i for i in range(4)])
        self.assertEqual(self.calls[1][1]['headers']['Referer'],URL)
    def test_authenticated_cookies_are_jar_not_explicit_header(self):
        c=self.client();self.assertEqual(c.transport.cookies.get('sessionid'),'synthetic-secret');c.fetch_company(URL)
        self.assertNotIn('Cookie',self.calls[0][1]['headers']);self.assertEqual(c.fetch_company(URL).auth_mode,'authenticated')
    def test_alias_limit_includes_generic_rating_browser_lanes(self):
        for mode in ('generic','rating','screener'):
            with self.subTest(mode=mode):
                p=self.hold(mode);c=self.client(wait_seconds=0)
                with self.assertRaises(AdministrativeDefer):c.fetch_company(URL)
                self.assertEqual(c.transport.dispatched,0);host_permits.finish(self.conn,p,now=self.clock(),outcome='released');self.clock.sleep(4)
        self.assertEqual(self.calls,[])
    def test_blocked_screener_does_not_block_unrelated_host(self):
        self.hold();self.assertIsNotNone(host_permits.acquire(self.conn,'https://other.test/',mode='generic',owner='other',now=self.clock()).permit)
    def test_per_hop_redirect_reservation(self):
        c=self.client();c.transport.offline_session=SimpleNamespace(get=lambda url,**kw:response(url,status=302,headers={'Location':'/company/BETA/'}) if url==URL else response(url))
        self.assertEqual(c.fetch_company(URL).final_url,'https://www.screener.in/company/BETA/')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM acquisition_host_permits').fetchone()[0],2)
    def test_foreign_redirect_never_receives_cookie_or_request(self):
        c=self.client();c.transport.offline_session=SimpleNamespace(get=lambda url,**kw:response(url,status=302,headers={'Location':'https://foreign.test/'}))
        with self.assertRaisesRegex(ValueError,'first-party'):c.fetch_company(URL)
        self.assertEqual(c.transport.dispatched,1)
    def test_login_redirect_is_rejected_without_following(self):
        c=self.client();c.transport.offline_session=SimpleNamespace(get=lambda url,**kw:response(url,status=302,headers={'Location':'/login/'}))
        self.assertEqual(c.fetch_company(URL).fetch_status,'rejected');self.assertEqual(c.transport.dispatched,1)
    def test_invalid_targets_never_reserve(self):
        c=self.client()
        for url in ['http://www.screener.in/company/A/','https://www.screener.in:443/company/A/','https://user@www.screener.in/','https://screener.in/','https://www.screener.in/#x']:
            with self.assertRaises(ValueError):c.fetch_company(url)
        self.assertEqual(c.transport.dispatched,0)
    def test_discovery_query_only_and_classification(self):
        c=self.client();c.transport.offline_session=SimpleNamespace(get=lambda url,**kw:response(url,screen_html()))
        self.assertEqual(c.fetch_result_screen('https://www.screener.in/screen/raw/?query=x').fetch_status,'ok')
        with self.assertRaises(ValueError):c.fetch_result_screen('https://www.screener.in/screen/raw/?query=x&page=2')
    def test_missing_migration_refuses_before_claim_or_io(self):
        self.seed_queue()
        with self.conn:self.conn.execute("DELETE FROM schema_migrations WHERE version='071_shared_host_permits.sql'")
        with self.assertRaisesRegex(RuntimeError,'071'):self.client()
        self.assertEqual(self.conn.execute("SELECT count(*) FROM screener_company_queue WHERE claim_token IS NOT NULL").fetchone()[0],0)
    def test_missing_cookie_refuses_without_fallback(self):
        self.cookie.unlink()
        with self.assertRaisesRegex(ValueError,'session cookie'):self.client()
    def test_byte_limit_releases_capacity_and_records_failure(self):
        c=self.client(policy=DownloadPolicy(max_bytes=1))
        with self.assertRaises(requests.RequestException):c.fetch_company(URL)
        self.assertEqual(self.conn.execute('SELECT last_outcome FROM acquisition_host_state').fetchone()[0],'transport_failure')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM acquisition_host_permits WHERE status='ACTIVE'").fetchone()[0],0)
    def test_retry_after_and_restart_preserve_deadline(self):
        c=self.client();c.transport.offline_session=SimpleNamespace(get=lambda url,**kw:response(url,status=429,headers={'Retry-After':'99999'}))
        self.assertEqual(c.fetch_company(URL).fetch_status,'rejected');c.close();self.clock.sleep(10)
        restarted=self.client(wait_seconds=0)
        with self.assertRaises(AdministrativeDefer) as ctx:restarted.fetch_company(URL)
        self.assertEqual(ctx.exception.retry_at,epoch(NOW)+99999)
    def test_three_transport_failures_open_durable_circuit(self):
        c=self.client()
        def fail(*a,**k):raise requests.Timeout('offline timeout')
        c.transport.offline_session=SimpleNamespace(get=fail)
        for _ in range(3):
            with self.assertRaises(requests.Timeout):c.fetch_company(URL)
            self.clock.sleep(4)
        with self.assertRaises(AdministrativeDefer):self.client(wait_seconds=0).fetch_company(URL)
    def test_expired_scope_claim_cannot_renew_or_defer(self):
        kind,key,token=self.claim_scope();self.clock.sleep(61)
        for fn in [lambda:renew(self.conn,kind,key,token,now=self.clock.utc()),lambda:defer(self.conn,kind,key,token,now=self.clock.utc(),retry_at=self.clock()+50,dispatched=False,reason='HOST_BUSY')]:
            with self.assertRaises(OwnershipLost):fn()
    def test_stale_scope_token_cannot_clear_new_owner(self):
        kind,key,token=self.claim_scope()
        with self.conn:self.conn.execute("UPDATE screener_collection_scope SET claim_token='new-token'")
        c=self.client();c.bind([(kind,key,token)],self.clock.utc)
        with self.assertRaises(OwnershipLost):c.fetch_company(URL)
        self.assertEqual(c.transport.dispatched,0);self.assertEqual(self.conn.execute('SELECT claim_token FROM screener_collection_scope').fetchone()[0],'new-token')
    def test_short_wait_renews_claim(self):
        claim=self.claim_scope();c=self.client();c.bind([claim],self.clock.utc);c.fetch_company(URL);c.fetch_company(URL)
        self.assertGreaterEqual(epoch(self.conn.execute('SELECT claim_expires_at_utc FROM screener_collection_scope').fetchone()[0]),self.clock()+599)
    def test_scope_defer_preserves_eligibility_errors_and_unspent_budget(self):
        kind,key,token=self.claim_scope();defer(self.conn,kind,key,token,now=NOW,retry_at=self.clock()+100,dispatched=False,reason='HOST_BUSY')
        row=self.conn.execute('SELECT * FROM screener_collection_scope').fetchone();self.assertEqual(row['eligibility'],'below_floor');self.assertEqual(row['check_failures'],0);self.assertIsNone(row['claim_token']);self.assertEqual(epoch(row['next_check_at_utc']),self.clock()+100)
    def test_company_worker_defers_without_failure_or_spent_attempt(self):
        from scrapper.workflows.screener.company_worker import run_screener_company_worker
        self.seed_queue();self.hold()
        report=run_screener_company_worker(due=True,limit=1,db_path=self.db,now_utc=self.clock.utc,shared_permits=True,shared_client_factory=lambda conn,**kw:self.client(wait_seconds=0),snapshot_root=self.root/'snapshots')
        self.assertEqual(report['outcomes'][0]['status'],'administrative_defer');self.assertEqual(self.conn.execute('SELECT max(attempt_count) FROM screener_company_queue').fetchone()[0],0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fetch_observations WHERE resource_kind!='result_screen'").fetchone()[0],0)
    def test_company_partial_defer_preserves_original_without_acceptance(self):
        from scrapper.workflows.screener import company_worker as w
        self.seed_queue()
        def scrape(path,scope,**kw):
            kw['host_client'].fetch_company(URL)
            raise AdministrativeDefer(host_permits.Decision('HOST_BUSY',retry_at=self.clock()+100))
        with patch.object(w,'scrape_screener_company_path',scrape):
            report=w.run_screener_company_worker(due=True,limit=1,db_path=self.db,now_utc=self.clock.utc,scrape=scrape,scrape_screener_company_path=scrape,shared_permits=True,shared_client_factory=lambda conn,**kw:self.client(),snapshot_root=self.root/'snapshots')
        self.assertEqual(report['outcomes'][0]['status'],'administrative_defer');self.assertEqual(self.conn.execute('SELECT max(attempt_count) FROM screener_company_queue').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM parse_artifacts').fetchone()[0],0)
        blob=self.conn.execute("SELECT local_path FROM response_blobs JOIN fetch_observations USING(blob_id) WHERE resource_kind='shared_partial'").fetchone();self.assertEqual(Path(blob[0]).read_bytes(),BODY)
    def test_scope_monitor_shared_defer_records_no_observation_or_failure(self):
        from scrapper.commands.collection_scope import monitor
        self.seed_scope();self.hold()
        with patch('scrapper.operations.workers.screener_requests.SharedScreenerClient',side_effect=lambda *a,**k:self.client(wait_seconds=0)):
            result=monitor(self.conn,limit=1,now_fn=self.clock.utc,shared_permits=True,snapshot_root=self.root/'snapshots')
        self.assertEqual(result['outcomes'][0]['status'],'administrative_defer');self.assertEqual(self.conn.execute('SELECT check_failures FROM screener_collection_scope').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM scrape_runs').fetchone()[0],0)
    def test_reconciler_shared_defer_spends_no_check_and_leaves_availability(self):
        from scrapper.tests.test_d09_artifact_reconciliation import ArtifactReconciliationD09Tests
        from scrapper.workflows.documents.reconciliation import reconcile_artifacts
        from scrapper.domain.artifact_reconciliation import ScreenerRefreshTarget
        case=ArtifactReconciliationD09Tests();case.setUp();self.addCleanup(case.temp.cleanup)
        conn=connect(case.db_path);self.addCleanup(conn.close)
        token_now='2026-08-05T02:00:00+00:00';clock=lambda:epoch(token_now)
        cookie=case.root/'cookie';cookie.write_text('sessionid=fixture')
        p=host_permits.acquire(conn,URL,mode='generic',owner='other',now=clock(),lease_seconds=300).permit
        target=ScreenerRefreshTarget(case.company_id,1,'/company/ALPHA/','standalone','2026-06',(case.transcript_expectation_id,))
        def targets(c,rows):return [target] if rows else []
        def factory(c,**kwargs):return SharedScreenerClient(c,owner='reconciler',cookie_path=cookie,now=clock,wait_seconds=0,offline_session=SimpleNamespace(get=self.fake))
        result=reconcile_artifacts(due=True,limit=10,db_path=case.db_path,output=case.root/'out.json',now_utc=lambda:token_now,shared_permits=True,shared_client_factory=factory,_source_refresh_targets=targets)
        self.assertTrue(any(r['status']=='administrative_defer' for r in result['source_refreshes']))
        row=conn.execute('SELECT attempt_count,claim_token FROM artifact_expectations WHERE expectation_id=?',(case.transcript_expectation_id,)).fetchone();self.assertEqual(row[0],0);self.assertIsNone(row[1])
        self.assertEqual(conn.execute('SELECT count(*) FROM artifact_check_events WHERE expectation_id=?',(case.transcript_expectation_id,)).fetchone()[0],0)
    def test_new_owners_inert_imports_and_workflow_layer(self):
        import subprocess,sys
        code="import sys;sys.dont_write_bytecode=True\ndef audit(e,a):\n if e in ('sqlite3.connect','socket.connect','subprocess.Popen','os.mkdir','os.remove','os.rename'):raise AssertionError(e)\n if e=='open' and (a[2]&3):raise AssertionError('write')\nsys.addaudithook(audit)\nimport scrapper.db.screener_host_defer;import scrapper.operations.workers.screener_requests\n"
        run=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True);self.assertEqual(run.returncode,0,run.stderr)
        for name in ['workflows/screener/company_worker.py','workflows/documents/reconciliation.py']:
            tree=ast.parse((Path(__file__).parents[1]/name).read_text())
            self.assertFalse(any(isinstance(n,ast.ImportFrom) and any(x in (n.module or '') for x in ('operations','pipeline','cli','view')) for n in ast.walk(tree)))
    def test_default_worker_and_reconciler_remain_legacy(self):
        import inspect
        from scrapper.pipeline.screener_discovery import run_screener_company_worker
        from scrapper.pipeline.reconcile_artifacts import reconcile_artifacts
        from scrapper.commands.collection_scope import monitor
        for fn in [run_screener_company_worker,reconcile_artifacts,monitor,discover_screener_results]:self.assertIs(inspect.signature(fn).parameters['shared_permits'].default,False)

if __name__=='__main__':unittest.main()
