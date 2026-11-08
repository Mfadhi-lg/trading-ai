"""Offline durable budget, priority, restart, fault and compatibility proof."""
import ast
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from scrapper.commands import collection_scope as legacy
from scrapper.db.database import connect, init_db
from scrapper.db.collection_scope_repo import record_scope
from scrapper.db.screener import scope_tasks
from scrapper.db.exchange import tasks
from scrapper.operations.coordinator import collection as c
from scrapper.tests.database_fixtures import prepare_database

NOW='2026-10-05T12:00:00+00:00'
LATER='2026-10-05T13:00:00+00:00'
BODY=b'<div id="company-info" data-company-id="1"></div><div id="top"><a>NSE: ALPHA</a></div><ul id="top-ratios"><li><span class="name">Market Cap</span><span class="value">Rs. 579.5 Cr.</span></li></ul>'

def crash_after_admission(db,ready):
    conn=connect(Path(db));init_db(conn)
    with c.ownership(Path(db),'collection-executor') as acquired:
        assert acquired
        task=conn.execute("SELECT * FROM collection_scope_tasks WHERE state='PENDING'").fetchone()
        scope_tasks.admit(conn,task['task_id']);ready.put(True)
        import time
        time.sleep(30)

def serving_process(db,ready,release):
    original=c.run_pending
    def slow_monitor(conn,**kw):
        if kw['before_claim']():
            ready.put(True)
            if not release.wait(10):raise RuntimeError('fixture timed out')
            if kw['before_claim']():raise AssertionError('signal admitted another company')
            return {'checked':1,'outcomes':[]}
        return {'checked':0,'outcomes':[]}
    with patch.object(c,'run_pending',side_effect=lambda **kw:original(**kw,monitor_fn=slow_monitor)):
        c.serve(db_path=Path(db),now_utc=lambda:NOW,limit=3)

class CollectionCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=self.root/'test.db'
        prepare_database(self.db);self.conn=connect(self.db);self.addCleanup(self.conn.close)
        with self.conn:
            record_scope(self.conn,path='/company/ALPHA/',company_id=None,value='100',observed_at='2026-09-01T00:00:00+00:00',evidence={},next_check=NOW)
    def tick(self,now=NOW,limit=40):return c.schedule_tick(db_path=self.db,now_utc=lambda:now,limit=limit)
    def row(self):return dict(self.conn.execute('SELECT * FROM collection_scope_tasks ORDER BY task_id DESC LIMIT 1').fetchone())
    def dispatch(self,stop=None,fn=None):
        def fake(conn,**kw):
            outcomes=[]
            for _ in range(kw['limit']):
                if not kw['before_claim']():break
                outcomes.append({'path':'/company/ALPHA/'})
            return {'checked':len(outcomes),'outcomes':outcomes}
        return c.run_pending(db_path=self.db,now_utc=lambda:NOW,snapshot_root=self.root/'snapshots',stop=stop,monitor_fn=fn or fake)
    def test_callback_inert_idempotent_frozen_budget(self):
        with patch.object(c,'monitor',side_effect=AssertionError('network')):
            a=self.tick(limit=3);b=self.tick(limit=99)
        self.assertEqual(a,b);self.assertEqual(b['budget'],3)
    def test_invalid_budgets_refuse_before_connect(self):
        for v in [0,101,True,1.5,None]:
            with patch.object(c,'connect',side_effect=AssertionError('writer')):
                with self.assertRaises(ValueError):self.tick(limit=v)
    def test_bounded_admission_and_sleep_cadence(self):
        self.tick(limit=3);self.assertEqual(self.dispatch()['checked'],3)
        self.assertEqual(self.row()['admitted'],3);self.assertEqual(self.row()['state'],'DONE')
        self.assertIsNone(self.tick(now='2026-10-05T12:59:59+00:00'))
        self.assertEqual(self.tick(now=LATER)['task_id'],2)
    def test_catchup_collapses_missed_hours(self):
        self.tick();self.dispatch();self.tick(now='2026-10-09T13:00:00+00:00');self.tick(now='2026-10-09T15:00:00+00:00')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM collection_scope_tasks').fetchone()[0],2)
    def test_restart_preserves_consumed_budget(self):
        self.tick(limit=3);scope_tasks.admit(self.conn,1)
        self.assertEqual(self.dispatch()['checked'],2);self.assertEqual(self.row()['admitted'],3)
    def test_process_death_after_admit_never_resets_budget(self):
        self.tick(limit=2);ctx=multiprocessing.get_context('fork');ready=ctx.Queue()
        proc=ctx.Process(target=crash_after_admission,args=(str(self.db),ready));proc.start()
        try:self.assertTrue(ready.get(timeout=10))
        finally:proc.terminate();proc.join(10)
        self.assertEqual(self.dispatch()['checked'],1);self.assertEqual(self.row()['admitted'],2)
    def test_duplicate_executor_refuses_even_without_lease(self):
        self.tick()
        with c.ownership(self.db,'collection-executor') as held:
            self.assertTrue(held);self.assertEqual(self.dispatch()['status'],'busy')
        self.assertEqual(self.row()['admitted'],0)
    def test_priority_exchange_wait_no_scope_mutation(self):
        self.tick();tasks.enqueue(self.conn,kind='attempt',scheduled_for='2026-10-05',attempt=1,priority=0,now=NOW)
        before=tuple(self.conn.execute('SELECT * FROM screener_collection_scope').fetchone())
        self.assertEqual(self.dispatch()['status'],'priority_wait');self.assertEqual(self.row()['admitted'],0)
        self.assertEqual(tuple(self.conn.execute('SELECT * FROM screener_collection_scope').fetchone()),before)
    def test_old_exchange_work_does_not_block(self):
        self.tick(limit=1);tasks.enqueue(self.conn,kind='attempt',scheduled_for='2026-10-04',attempt=3,priority=10,now=NOW)
        self.assertEqual(self.dispatch()['checked'],1)
    def test_priority_checked_between_each_company(self):
        self.tick(limit=3)
        def monitor(conn,**kw):
            self.assertTrue(kw['before_claim']())
            tasks.enqueue(conn,kind='attempt',scheduled_for='2026-10-05',attempt=1,priority=0,now=NOW)
            self.assertFalse(kw['before_claim']())
            return {'checked':1,'outcomes':[]}
        self.assertEqual(self.dispatch(fn=monitor)['status'],'priority_wait');self.assertEqual(self.row()['admitted'],1)
    def test_stop_retains_pending_and_unspent_slots(self):
        self.tick();stop=threading.Event();stop.set()
        self.assertEqual(self.dispatch(stop=stop)['status'],'draining');self.assertEqual(self.row()['state'],'PENDING');self.assertEqual(self.row()['admitted'],0)
    def test_signal_drain_checks_before_next_company(self):
        self.tick();stop=threading.Event()
        def monitor(conn,**kw):
            self.assertTrue(kw['before_claim']());stop.set();self.assertFalse(kw['before_claim']())
            return {'checked':1,'outcomes':[]}
        self.assertEqual(self.dispatch(stop=stop,fn=monitor)['status'],'draining');self.assertEqual(self.row()['admitted'],1)
    def test_active_scope_wait_then_expiry_recoverable(self):
        self.tick(limit=1)
        with self.conn:self.conn.execute("UPDATE screener_collection_scope SET claim_token='crashed',claim_expires_at_utc=?",(LATER,))
        self.assertEqual(self.dispatch()['status'],'lease_wait');self.assertEqual(self.row()['admitted'],0)
        with self.conn:self.conn.execute('UPDATE screener_collection_scope SET claim_expires_at_utc=?',(NOW,))
        self.assertEqual(self.dispatch()['checked'],1)
    def test_empty_due_set_settles_without_admission(self):
        self.tick()
        with self.conn:self.conn.execute('UPDATE screener_collection_scope SET next_check_at_utc=?',(LATER,))
        self.assertEqual(self.dispatch()['checked'],0);self.assertEqual(self.row()['state'],'DONE');self.assertEqual(self.row()['admitted'],0)
    def test_failure_retains_error_and_original_scope(self):
        self.tick()
        def fail(conn,**kw):self.assertTrue(kw['before_claim']());raise RuntimeError('offline fault')
        with self.assertRaisesRegex(RuntimeError,'offline fault'):self.dispatch(fn=fail)
        self.assertEqual(self.row()['state'],'FAILED');self.assertEqual(self.row()['admitted'],1);self.assertIn('offline fault',self.row()['error'])
        self.assertEqual(self.conn.execute('SELECT eligibility FROM screener_collection_scope').fetchone()[0],'below_floor')
    def test_real_monitor_shared_transport_and_weekly_state(self):
        self.tick(limit=2)
        from scrapper.tests.test_screener_host_requests import response
        from scrapper.operations.workers.screener_requests import SharedScreenerClient
        from datetime import datetime
        cookie=self.root/'cookie';cookie.write_text('sessionid=synthetic')
        def factory(conn,**kw):
            return SharedScreenerClient(conn,**kw,cookie_path=cookie,now=lambda:datetime.fromisoformat(NOW).timestamp(),sleep=lambda _:None,offline_session=SimpleNamespace(get=lambda url,**kw:response(url)))
        with patch('scrapper.operations.workers.screener_requests.SharedScreenerClient',side_effect=factory):
            result=self.dispatch(fn=legacy.monitor)
        self.assertEqual(result['checked'],1)
        row=self.conn.execute('SELECT * FROM screener_collection_scope').fetchone()
        self.assertEqual(row['next_check_at_utc'],'2026-10-12T12:00:00+00:00');self.assertEqual(row['eligibility'],'below_floor');self.assertIsNone(row['claim_token'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM screener_scope_observations').fetchone()[0],1)
    def test_missing_migration_refuses_before_lock_or_monitor(self):
        with self.conn:self.conn.execute("DELETE FROM schema_migrations WHERE version='074_collection_scope_tasks.sql'")
        with patch.object(c,'ownership',side_effect=AssertionError('lock')):
            with self.assertRaisesRegex(RuntimeError,'074'):self.dispatch()
    def test_double_settlement_refuses(self):
        self.tick();scope_tasks.finish(self.conn,1,now=NOW,result={})
        with self.assertRaises(RuntimeError):scope_tasks.finish(self.conn,1,now=NOW,result={})
    def test_pending_task_singleton_across_writers(self):
        self.tick();other=connect(self.db)
        try:self.assertEqual(scope_tasks.enqueue(other,now=NOW,budget=1)['task_id'],1)
        finally:other.close()
    def test_network_outside_transaction_allows_other_writer(self):
        self.tick(limit=1)
        def monitor(conn,**kw):
            self.assertTrue(kw['before_claim']());self.assertFalse(conn.in_transaction)
            other=connect(self.db)
            try:other.execute('BEGIN IMMEDIATE');other.rollback()
            finally:other.close()
            return {'checked':1,'outcomes':[]}
        self.assertEqual(self.dispatch(fn=monitor)['checked'],1)
    def test_import_and_help_inert(self):
        code="import sys;sys.dont_write_bytecode=True\ndef audit(e,a):\n if e in ('sqlite3.connect','socket.connect','subprocess.Popen','os.mkdir','os.remove','os.rename'):raise AssertionError(e)\n if e=='open' and (a[2]&3):raise AssertionError('write')\nsys.addaudithook(audit)\nimport scrapper.operations.coordinator.collection;import scrapper.db.screener.scope_tasks\nimport scrapper.commands.collection_scope as m;sys.argv=['collection_scope','--help'];m.main()"
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('--serve',result.stdout)
    def test_real_due_company_result_has_priority(self):
        from scrapper.pipeline.screener_discovery import discover_screener_results
        from scrapper.tests.test_screener_discovery import screen_html
        fixture=self.root/'screen.html';fixture.write_bytes(screen_html().replace(b'/consolidated/',b'/'))
        discover_screener_results(query_role='fixture',query='x',fixture=fixture,db_path=self.db,snapshot_root=self.root/'snapshots',now_utc=lambda:NOW)
        self.tick()
        self.assertEqual(self.dispatch()['status'],'priority_wait');self.assertEqual(self.row()['admitted'],0)
    def test_real_source_hold_stops_batch_after_one_request(self):
        from scrapper.core.http import FetchResponse
        self.tick(limit=3)
        client=SimpleNamespace(bind=lambda *a:None,check=lambda:None,close=lambda:None)
        calls=[]
        def fetch(url):
            calls.append(url)
            return FetchResponse(url,url,403,b'blocked','text/html','utf-8',None,None,'cookie','rejected')
        client.fetch_company=fetch
        with patch('scrapper.operations.workers.screener_requests.SharedScreenerClient',return_value=client):
            result=self.dispatch(fn=legacy.monitor)
        self.assertEqual(len(calls),1);self.assertEqual(result['checked'],1);self.assertEqual(self.row()['admitted'],1)
        row=self.conn.execute('SELECT * FROM screener_collection_scope').fetchone()
        self.assertEqual(row['check_failures'],1);self.assertEqual(row['eligibility'],'below_floor');self.assertEqual(row['next_check_at_utc'],'2026-10-06T12:00:00+00:00')
    def test_monitor_manual_signature_output_preserved(self):
        with self.conn:self.conn.execute('UPDATE screener_collection_scope SET next_check_at_utc=?',(LATER,))
        self.assertEqual(legacy.monitor(self.conn,limit=1,now_fn=lambda:NOW),{'checked':0,'outcomes':[]})
    def test_identity_refuses_before_scheduler_or_files(self):
        with patch.object(c,'connect',side_effect=RuntimeError('production target')):
            with patch.object(c,'ownership',side_effect=AssertionError('lock')):
                with self.assertRaisesRegex(RuntimeError,'production target'):c.serve(db_path=self.db)
    def test_real_sigterm_drains_current_then_stops_admission(self):
        import os,signal,time
        ctx=multiprocessing.get_context('fork');ready=ctx.Queue();release=ctx.Event()
        proc=ctx.Process(target=serving_process,args=(str(self.db),ready,release));proc.start()
        try:
            self.assertTrue(ready.get(timeout=10));os.kill(proc.pid,signal.SIGTERM)
            time.sleep(.1);self.assertTrue(proc.is_alive());release.set();proc.join(10)
            self.assertEqual(proc.exitcode,0);self.assertEqual(self.row()['admitted'],1);self.assertEqual(self.row()['state'],'PENDING')
        finally:
            if proc.is_alive():proc.terminate();release.set();proc.join(10)
    def test_pause_does_not_block_callback_or_another_writer(self):
        self.tick(limit=3);started=threading.Event();release=threading.Event();errors=[]
        def slow(conn,**kw):
            self.assertTrue(kw['before_claim']());started.set();release.wait(10)
            return {'checked':1,'outcomes':[]}
        def work():
            try:self.dispatch(fn=slow)
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=work);thread.start()
        try:
            self.assertTrue(started.wait(10));self.assertEqual(self.tick()['task_id'],1)
            other=connect(self.db)
            try:other.execute('BEGIN IMMEDIATE');other.rollback()
            finally:other.close()
        finally:release.set();thread.join(10)
        self.assertFalse(errors,errors)
    def test_db_layer_has_no_operations_import(self):
        tree=ast.parse(Path(scope_tasks.__file__).read_text())
        self.assertFalse(any(isinstance(n,ast.ImportFrom) and n.module and any(x in n.module.split('.') for x in ['operations','commands','view']) for n in ast.walk(tree)))

if __name__=='__main__':unittest.main()
