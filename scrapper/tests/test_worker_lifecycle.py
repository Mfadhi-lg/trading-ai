"""Offline signal, drain, waiting and real queue ownership contracts."""
import importlib
import os
import signal
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from scrapper.operations.workers.lifecycle import WorkerLifecycle, run_bounded
from scrapper.tests import test_document_download_queue as queue_fixture
from scrapper.tests.test_document_download_queue import FakeHealth, FakeSession, FakeResponse, PDF_BYTES, NOW
from scrapper.db.document_download_queue_repo import materialize_download_queue
from scrapper.pipeline import credit_rating_downloads as rating
worker = importlib.import_module('scrapper.pipeline.run_document_download_worker')


class LifecycleTests(unittest.TestCase):
    def test_import_is_inert_and_dependencies_are_bounded(self):
        import ast
        from pathlib import Path
        path=Path(__file__).parents[1]/'operations/workers/lifecycle.py'
        tree=ast.parse(path.read_text())
        imports=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
        self.assertFalse(any(name and any(x in name for x in ('db','cli','documents','view','requests')) for name in imports))
        code="""from unittest.mock import patch
import signal, socket, sqlite3, subprocess, threading
with patch.object(signal,'signal',side_effect=AssertionError), patch.object(socket,'socket',side_effect=AssertionError), patch.object(sqlite3,'connect',side_effect=AssertionError), patch.object(subprocess,'Popen',side_effect=AssertionError), patch.object(threading.Thread,'start',side_effect=AssertionError), patch('builtins.open',side_effect=AssertionError):
 import scrapper.operations.workers.lifecycle
"""
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_claimed_job_pacing_is_preserved_after_drain(self):
        clock=[0.0];life=WorkerLifecycle(monotonic=lambda:clock[0],heartbeat_seconds=2)
        life.claimed(1);life.set()
        with patch('scrapper.operations.workers.lifecycle.time.sleep',side_effect=lambda n:clock.__setitem__(0,clock[0]+n)) as sleep:
            self.assertTrue(life.wait(5,state='THROTTLED',interruptible=False))
        self.assertEqual(clock[0],5);self.assertEqual(sleep.call_count,3)
        self.assertEqual(life.active_job,1)

    def test_handlers_restore_on_success_and_failure(self):
        previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        for fail in (False, True):
            lifecycle = WorkerLifecycle()
            try:
                with lifecycle.signals():
                    os.kill(os.getpid(), signal.SIGTERM)
                    self.assertTrue(lifecycle.is_set())
                    self.assertEqual(lifecycle.snapshot()['state'], 'DRAINING')
                    if fail: raise ValueError('setup failed')
            except ValueError: pass
            self.assertEqual(lifecycle.state, 'STOPPED')
            self.assertEqual({s: signal.getsignal(s) for s in previous}, previous)

    def test_wait_heartbeat_is_not_progress_and_wait_is_bounded(self):
        clock = [0.0]; reports = []
        event = SimpleNamespace(is_set=lambda: False, wait=lambda n:clock.__setitem__(0,clock[0]+n))
        lifecycle = WorkerLifecycle(event=event, monotonic=lambda:clock[0], heartbeat_seconds=2, observer=reports.append)
        lifecycle.wait(5, state='COOLDOWN')
        self.assertEqual(clock[0],5)
        self.assertEqual([x['heartbeat_at_monotonic'] for x in reports], [0,2,4,5])
        self.assertTrue(all(x['completed']==0 and x['progress_at_monotonic']==0 and x['state']=='COOLDOWN' for x in reports))
        lifecycle.claimed(7); lifecycle.finished()
        self.assertEqual(lifecycle.snapshot()['progress_at_monotonic'],5)
        self.assertEqual(lifecycle.completed,1)
        for n in (-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError): lifecycle.wait(n)
            with self.assertRaises(ValueError): WorkerLifecycle(heartbeat_seconds=n)

    def test_observer_failure_cannot_retry_completed_work(self):
        lifecycle=WorkerLifecycle(observer=Mock(side_effect=OSError('full')))
        lifecycle.claimed(1); lifecycle.finished()
        self.assertEqual(lifecycle.completed,1)
        self.assertEqual(lifecycle.observer_error,'OSError')

    def test_real_process_signal_interrupts_idle_without_starting_work(self):
        code='''import os
from scrapper.operations.workers.lifecycle import WorkerLifecycle
with WorkerLifecycle().signals() as life:
 print('READY',flush=True)
 life.wait(60,state='IDLE')
 print(life.snapshot()['state'],flush=True)
print('STOPPED',flush=True)
'''
        process=subprocess.Popen([sys.executable,'-B','-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'READY')
            started=time.monotonic();process.send_signal(signal.SIGTERM)
            out,err=process.communicate(timeout=3)
            self.assertEqual(process.returncode,0,err)
            self.assertEqual(out.splitlines(),['DRAINING','STOPPED'])
            self.assertLess(time.monotonic()-started,3)
        finally:
            if process.poll() is None:process.kill();process.communicate()

    def test_real_process_drains_active_work_before_next_claim(self):
        code='''import signal, time
from scrapper.operations.workers.lifecycle import WorkerLifecycle
with WorkerLifecycle().signals() as life:
 life.claimed(7)
 print('CLAIMED',flush=True)
 time.sleep(.2)
 life.finished()
 print('FINISHED',flush=True)
 if not life.is_set(): print('NEW_CLAIM',flush=True)
'''
        process=subprocess.Popen([sys.executable,'-B','-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'CLAIMED')
            process.send_signal(signal.SIGINT);out,err=process.communicate(timeout=3)
            self.assertEqual(process.returncode,0,err);self.assertEqual(out.splitlines(),['FINISHED'])
        finally:
            if process.poll() is None:process.kill();process.communicate()

    def test_browser_setup_failure_restores_signal_handlers(self):
        from ops.host.browser_rating_runner import main
        previous={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
        hooks=SimpleNamespace(threading=__import__('threading'),signal=signal,argparse=Mock(side_effect=RuntimeError('setup')))
        hooks.argparse.ArgumentParser.side_effect=RuntimeError('setup')
        with self.assertRaisesRegex(RuntimeError,'setup'):main(hooks=hooks)
        self.assertEqual({s:signal.getsignal(s) for s in previous},previous)

    def test_direct_rating_drain_finishes_only_active_job(self):
        import sqlite3
        life=WorkerLifecycle();conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
        self.addCleanup(conn.close)
        from scrapper.db.database import apply_migrations
        apply_migrations(conn)
        job={'download_job_id':7,'requested_url':'https://example.test/a',
             'status':'LEASED','lease_owner':'mock-owner','claim_token':'mock-claim',
             'attempt_count':1,'lease_expires_at_utc':'2099-01-01T00:00:00+00:00'}
        # Renewal validates the captured claim in its own unit tests; this fixture
        # isolates the drain boundary and supplies a full schema for role health.
        def resolve(*a,**kw):
            life.set();return SimpleNamespace(status='OK',error=None)
        with patch.object(rating,'queue_renewal') as renewal,patch.object(rating,'assert_document_store_ready',return_value=SimpleNamespace(to_dict=lambda:{})),patch.object(rating,'_claim_job',return_value=job) as claim,patch.object(rating,'resolve_credit_rating',side_effect=resolve),patch.object(rating,'_persist_resolution',return_value=9),patch.object(rating,'_host_block_status',return_value=None),patch.object(rating,'_finish_job') as finish,patch.object(rating.requests,'Session'):
            result=rating.run_credit_rating_download_worker(conn,limit=3,throttle_seconds=0,lifecycle=life)
        self.assertEqual(result['processed_job_count'],1);self.assertEqual(claim.call_count,1)
        self.assertEqual(finish.call_args.kwargs['job_id'],7);self.assertEqual(finish.call_args.kwargs['status'],'SUCCEEDED')
        self.assertEqual(life.completed,1)

    def test_generic_drain_preserves_real_db_claim_and_no_network_transaction(self):
        fixture=queue_fixture.DocumentDownloadQueueDA05Tests();fixture.setUp()
        try:
            fixture.register('life',('presentation',))
            materialize_download_queue(fixture.conn,scope_id='life',created_at_utc=NOW)
            row=fixture.conn.execute("SELECT requested_url FROM artifact_download_queue LIMIT 1").fetchone()
            life=WorkerLifecycle()
            class Session(FakeSession):
                def get(self,url,**kwargs):
                    self_test.assertFalse(fixture.conn.in_transaction)
                    life.set()
                    return super().get(url,**kwargs)
            self_test=self
            session=Session({row['requested_url']:[FakeResponse(PDF_BYTES,url=row['requested_url'])]})
            result=worker.run_document_download_worker(fixture.conn,scope_id='life',limit=3,lifecycle=life,worker='owner',throttle_seconds=0,now=lambda:NOW,snapshot_root=fixture.root/'bytes',document_store=fixture.root,health_check=FakeHealth,session_factory=lambda:session)
            self.assertEqual(result['processed_job_count'],1)
            self.assertEqual(life.completed,1)
            rows=fixture.conn.execute("SELECT status,attempt_count,lease_owner FROM artifact_download_queue").fetchall()
            self.assertEqual(rows[0]['status'],'SUCCEEDED');self.assertEqual(rows[0]['attempt_count'],1)
            self.assertIsNone(rows[0]['lease_owner'])
        finally:fixture.tearDown()

    def test_signal_during_empty_claim_prevents_round_robin_fallback(self):
        fixture=queue_fixture.DocumentDownloadQueueDA05Tests();fixture.setUp()
        try:
            fixture.register('life',('presentation',))
            materialize_download_queue(fixture.conn,scope_id='life',created_at_utc=NOW)
            row=fixture.conn.execute("SELECT requested_url FROM artifact_download_queue LIMIT 1").fetchone()
            life=WorkerLifecycle()
            class Session(FakeSession):
                def get(self,url,**kwargs):
                    self_test.assertFalse(fixture.conn.in_transaction)
                    return super().get(url,**kwargs)
            self_test=self
            session=Session({row['requested_url']:[FakeResponse(PDF_BYTES,url=row['requested_url'])]})
            original_claim=worker.claim_download_job
            calls=[]
            def claim(*args,**kwargs):
                calls.append(kwargs)
                if len(calls)>1:
                    life.set();return None
                return original_claim(*args,**kwargs)
            with patch.object(worker,'claim_download_job',side_effect=claim):
                result=worker.run_document_download_worker(fixture.conn,scope_id='life',limit=3,lifecycle=life,worker='owner',throttle_seconds=0,now=lambda:NOW,snapshot_root=fixture.root/'bytes',document_store=fixture.root,health_check=FakeHealth,session_factory=lambda:session)
            self.assertEqual(len(calls),2)
            self.assertEqual(result['processed_job_count'],1)
            self.assertEqual(life.completed,1)
            rows=fixture.conn.execute("SELECT status,attempt_count,lease_owner FROM artifact_download_queue").fetchall()
            self.assertEqual(rows[0]['status'],'SUCCEEDED');self.assertEqual(rows[0]['attempt_count'],1)
            self.assertIsNone(rows[0]['lease_owner'])
        finally:fixture.tearDown()

    def test_killed_claim_restarts_after_expiry_with_history_preserved(self):
        from scrapper.db.document_download_queue_repo import claim_download_job
        fixture=queue_fixture.DocumentDownloadQueueDA05Tests();fixture.setUp()
        process=None
        try:
            fixture.register('crash',('presentation',))
            artifact_id=fixture.conn.execute("SELECT artifact_id FROM document_artifacts WHERE artifact_type='presentation' ORDER BY artifact_id LIMIT 1").fetchone()[0]
            materialize_download_queue(fixture.conn,scope_id='crash',created_at_utc=NOW,artifact_ids=(artifact_id,))
            # This artifact has multiple source URLs; freeze the unrelated jobs
            # as policy skips so this fixture has one eligible crash target.
            with fixture.conn:
                fixture.conn.execute("UPDATE artifact_download_queue SET status='SKIPPED_POLICY' WHERE download_job_id != (SELECT min(download_job_id) FROM artifact_download_queue)")
            code="""import sqlite3,sys,time
from scrapper.db.document_download_queue_repo import claim_download_job
conn=sqlite3.connect(sys.argv[1]);conn.row_factory=sqlite3.Row
job=claim_download_job(conn,worker='crashed',scope_id='crash',now_utc='2026-08-14T10:00:00+00:00',lease_seconds=1)
print(job['download_job_id'],flush=True)
time.sleep(60)
"""
            process=subprocess.Popen([sys.executable,'-B','-c',code,str(fixture.db_path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            job_id=int(process.stdout.readline().strip());process.kill();process.communicate(timeout=3)
            old=fixture.conn.execute('SELECT * FROM artifact_download_queue WHERE download_job_id=?',(job_id,)).fetchone()
            self.assertEqual(old['status'],'LEASED');self.assertEqual(old['attempt_count'],1)
            job=claim_download_job(fixture.conn,worker='restarted',scope_id='crash',now_utc='2026-08-14T10:00:02+00:00')
            self.assertEqual(job['download_job_id'],job_id);self.assertEqual(job['attempt_count'],2)
            self.assertEqual(job['lease_owner'],'restarted')
            self.assertEqual(fixture.conn.execute('SELECT count(*) FROM artifact_download_attempts').fetchone()[0],0)
        finally:
            if process is not None and process.poll() is None:process.kill();process.communicate()
            fixture.tearDown()

    def test_stopped_worker_never_claims_and_idle_is_healthy(self):
        import sqlite3
        from scrapper.db.database import apply_migrations
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
        self.addCleanup(conn.close);apply_migrations(conn)
        for stopped in (False,True):
            life=WorkerLifecycle()
            if stopped:life.set()
            with patch.object(worker,'claim_download_job',return_value=None) as claim,patch.object(worker,'queue_counts',return_value={}):
                worker.run_document_download_worker(conn,limit=1,lifecycle=life,health_check=lambda p:SimpleNamespace(to_dict=lambda:{}),throttle_seconds=0)
            self.assertEqual(claim.call_count,0 if stopped else 1)
            self.assertEqual(life.snapshot()['state'],'DRAINING' if stopped else 'IDLE')


if __name__=='__main__':unittest.main()
