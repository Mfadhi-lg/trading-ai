"""Offline bounded inbox, restart, priority, fault and workflow contracts."""
import ast
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from scrapper.db.database import connect,init_db
from scrapper.tests.database_fixtures import prepare_database
from scrapper.db import periodic_tasks as t
from scrapper.db.exchange import tasks as exchange_tasks
from scrapper.operations.coordinator import periodic as c

NOW='2026-10-06T00:00:00+00:00'
LATER='2026-10-06T00:05:00+00:00'

def interrupted(db,ready):
    conn=connect(Path(db));init_db(conn)
    with c.ownership(Path(db),'periodic-executor') as acquired:
        assert acquired
        row=conn.execute("SELECT task_id FROM periodic_dispatch_tasks WHERE state='PENDING' LIMIT 1").fetchone()
        t.admit(conn,row[0]);ready.put(True);time.sleep(30)

class PeriodicTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=self.root/'test.db'
        prepare_database(self.db);self.conn=connect(self.db);self.addCleanup(self.conn.close)
    def tick(self,now=NOW,startup=False):return c.schedule_tick(db_path=self.db,now_utc=lambda:now,startup=startup)
    def rows(self):return [dict(x) for x in self.conn.execute('SELECT * FROM periodic_dispatch_tasks ORDER BY task_id')]
    def run_dispatch(self,execute=None,stop=None):
        return c.run_pending(db_path=self.db,now_utc=lambda:NOW,execute=execute or (lambda *a,**k:{'idle':True}),stop=stop)
    def test_timer_enqueue_only_idempotent(self):
        with patch.object(c,'execute_unit',side_effect=AssertionError('work')):
            self.assertEqual(self.tick(),self.tick())
        self.assertEqual(len(self.rows()),3)
    def test_unknown_kind_refuses(self):
        with self.assertRaises(ValueError):t.enqueue(self.conn,kind='news',now=NOW)
    def test_startup_recovery_only(self):
        self.tick(startup=True);self.assertEqual([r['kind'] for r in self.rows()],['recovery'])
    def test_single_unit_per_kind_and_round_robin(self):
        self.tick();seen=[]
        self.run_dispatch(lambda conn,task,**k:seen.append(task['kind']) or {'idle':False})
        self.assertEqual(seen,['delayed','recovery','housekeeping'])
        self.assertEqual([x['admitted'] for x in self.rows()],[1,1,1])
    def test_budget_hard_bound(self):
        self.tick();seen=[]
        for _ in range(55):self.run_dispatch(lambda conn,task,**k:seen.append(task['kind']) or {'idle':False})
        self.assertEqual(seen.count('delayed'),50);self.assertEqual(seen.count('recovery'),50);self.assertEqual(seen.count('housekeeping'),40)
        self.assertTrue(all(r['state']=='DONE' for r in self.rows()))
    def test_idle_finishes_and_cadence(self):
        self.tick();self.run_dispatch();self.assertEqual(self.tick(),[None]*3)
        self.assertEqual(len([x for x in self.tick(LATER) if x]),3)
    def test_recovery_idle_120_seconds(self):
        self.tick();self.run_dispatch();row=self.rows()[1]
        self.assertEqual(row['next_due_at_utc'],'2026-10-06T00:02:00+00:00')
    def test_recovery_work_sleep3(self):
        self.tick()
        with self.conn:self.conn.execute("UPDATE periodic_dispatch_tasks SET budget=1 WHERE kind='recovery'")
        self.run_dispatch(lambda *a,**k:{'idle':False})
        self.assertEqual(self.rows()[1]['next_due_at_utc'],'2026-10-06T00:00:03+00:00')
    def test_priority_preserves_every_byte_of_inbox(self):
        self.tick();exchange_tasks.enqueue(self.conn,kind='attempt',scheduled_for='2026-10-06',attempt=1,priority=0,now=NOW)
        before=self.rows();self.assertEqual(self.run_dispatch()['status'],'priority_wait');self.assertEqual(before,self.rows())
    def test_priority_rechecked_between_units(self):
        self.tick();calls=[]
        def work(conn,task,**kw):
            calls.append(task['kind']);exchange_tasks.enqueue(conn,kind='attempt',scheduled_for='2026-10-06',attempt=1,priority=0,now=NOW)
            return {'idle':False}
        self.assertEqual(self.run_dispatch(work)['status'],'priority_wait');self.assertEqual(calls,['delayed'])
    def test_old_catchup_does_not_block(self):
        self.tick();exchange_tasks.enqueue(self.conn,kind='attempt',scheduled_for='2026-10-05',attempt=3,priority=10,now=NOW)
        self.assertEqual(self.run_dispatch()['processed'],3)
    def test_drain_admits_nothing(self):
        self.tick();event=threading.Event();event.set();before=self.rows()
        self.assertEqual(self.run_dispatch(stop=event)['status'],'draining');self.assertEqual(before,self.rows())
    def test_duplicate_executor_refused(self):
        self.tick()
        with c.ownership(self.db,'periodic-executor') as acquired:
            self.assertTrue(acquired);self.assertEqual(self.run_dispatch()['status'],'busy')
    def test_duplicate_scheduler_refused(self):
        with c.ownership(self.db,'periodic-scheduler'):
            with self.assertRaises(RuntimeError):c.serve(db_path=self.db)
    def test_error_accounting_and_restart(self):
        self.tick()
        def fail(*a,**k):raise RuntimeError('fault')
        with self.assertRaisesRegex(RuntimeError,'fault'):self.run_dispatch(fail)
        self.assertEqual(self.rows()[0]['state'],'FAILED');self.assertEqual(self.rows()[0]['admitted'],1)
        self.assertIn('fault',self.rows()[0]['error']);self.assertEqual(self.run_dispatch()['processed'],2)
    def test_process_crash_durable_consumed_admission(self):
        self.tick(startup=True);ctx=multiprocessing.get_context('fork');ready=ctx.Queue();p=ctx.Process(target=interrupted,args=(str(self.db),ready));p.start()
        try:
            self.assertTrue(ready.get(timeout=10));self.assertEqual(self.run_dispatch()['status'],'busy')
        finally:p.terminate();p.join(10)
        self.assertEqual(self.rows()[0]['admitted'],1);self.run_dispatch();self.assertEqual(self.rows()[0]['admitted'],2)
    def test_no_db_transaction_during_executor(self):
        self.tick()
        def work(conn,task,**kw):
            self.assertFalse(conn.in_transaction)
            other=connect(self.db)
            try:
                with other:other.execute('CREATE TABLE IF NOT EXISTS writer_test(id INTEGER)')
            finally:other.close()
            return {'idle':True}
        self.run_dispatch(work)
    def test_callback_runs_during_slow_work(self):
        self.tick();ready=threading.Event();release=threading.Event();errors=[]
        def work(*a,**k):ready.set();release.wait(10);return {'idle':True}
        def worker():
            try:self.run_dispatch(work)
            except BaseException as e:errors.append(e)
        thread=threading.Thread(target=worker);thread.start()
        try:self.assertTrue(ready.wait(5));self.assertEqual(len(self.tick()),3)
        finally:release.set();thread.join(10)
        self.assertFalse(errors);self.assertFalse(thread.is_alive())
    def test_missing_migration_refuses_before_lock(self):
        with self.conn:
            self.conn.execute('DROP TABLE periodic_dispatch_tasks');self.conn.execute("DELETE FROM schema_migrations WHERE version='075_periodic_dispatch.sql'")
        with patch.object(c,'ownership',side_effect=AssertionError('lock')):
            with self.assertRaises(RuntimeError):self.run_dispatch()
    def test_delayed_uses_canonical_shared_transport(self):
        with patch.object(c,'preview_due_expectations',return_value=[object()]), patch.object(c,'reconcile_artifacts',return_value={'summary':{'claimed_count':1}}) as fn:
            result=c.execute_unit(self.conn,{'kind':'delayed'},db_path=self.db,now_utc=lambda:NOW)
        self.assertFalse(result['idle']);self.assertTrue(fn.call_args.kwargs['shared_permits']);self.assertIs(fn.call_args.kwargs['shared_client_factory'],c.SharedScreenerClient);self.assertEqual(fn.call_args.kwargs['limit'],1)
    def test_recovery_explicit_rotation_throttle(self):
        with patch.object(c,'run_document_recovery_worker',return_value={'processed_target_source_count':0}) as fn:
            for i in range(1,4):
                self.assertTrue(c.execute_unit(self.conn,{'kind':'recovery','admitted':i},db_path=self.db,now_utc=lambda:NOW)['idle'])
        self.assertEqual([x.kwargs['sources'][0] for x in fn.call_args_list],['bse','nse','issuer-ir']);self.assertTrue(all(x.kwargs['limit']==1 and x.kwargs['throttle_seconds']==2 for x in fn.call_args_list))
    def test_real_empty_workflows_idle_and_cursors(self):
        self.tick();result=self.run_dispatch(c.execute_unit);self.assertEqual(result['processed'],3)
        self.assertTrue(all(r['state']=='DONE' for r in self.rows()))
    def test_cursor_restart_retained_and_idempotent_unit(self):
        self.tick();row=self.rows()[2];t.cursors(self.conn,row['task_id'],123,456);self.run_dispatch()
        self.tick(LATER);new=self.rows()[-1];self.assertEqual((new['result_cursor'],new['artifact_cursor']),(123,456))
    def test_help_import_inert(self):
        code="import sys;sys.dont_write_bytecode=True\ndef audit(e,a):\n if e in ('sqlite3.connect','socket.connect','subprocess.Popen','os.mkdir','os.remove','os.rename'):raise AssertionError(e)\n if e=='open' and (a[2]&3):raise AssertionError('write')\nsys.addaudithook(audit)\nimport scrapper.operations.coordinator.periodic\nfrom scrapper.commands.periodic_dispatch import main\nmain(['--help'])\n"
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_cli_apply_required_before_writer(self):
        from scrapper.commands.periodic_dispatch import main
        with patch.object(c,'connect',side_effect=AssertionError('writer')):
            with self.assertRaises(SystemExit):main(['--once'])
    def test_new_db_owner_no_operations_dependency(self):
        tree=ast.parse(Path(t.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):self.assertNotIn('operations',node.module or '')
    def test_housekeeping_real_blocked_artifact_sweep_and_replay(self):
        from scrapper.tests.test_da06_recovery_registry import RecoveryRegistryTests
        case=RecoveryRegistryTests();case.setUp();self.addCleanup(case.tearDown)
        ids=[case._blocked_artifact('Example '+str(i),'EX'+str(i),'https://www.crisilratings.com/report'+str(i)+'.pdf')[0] for i in range(3)]
        task=t.enqueue(case.conn,kind='housekeeping',now=NOW)
        for expected in ids:
            task=dict(case.conn.execute('SELECT * FROM periodic_dispatch_tasks WHERE task_id=?',(task['task_id'],)).fetchone())
            result=c.execute_unit(case.conn,task,db_path=case.root/'recovery.db',now_utc=lambda:NOW)
            self.assertEqual(result['artifact_id'],expected)
            # Replay the same stale cursor after an interrupted unit is idempotent.
            c.execute_unit(case.conn,task,db_path=case.root/'recovery.db',now_utc=lambda:NOW)
        self.assertEqual(case.conn.execute('SELECT COUNT(*) FROM artifact_recovery_targets').fetchone()[0],3)
        task=dict(case.conn.execute('SELECT * FROM periodic_dispatch_tasks').fetchone())
        self.assertTrue(c.execute_unit(case.conn,task,db_path=case.root/'recovery.db',now_utc=lambda:NOW)['idle'])
        self.assertEqual(case.conn.execute('SELECT artifact_cursor FROM periodic_dispatch_tasks').fetchone()[0],0)
    def test_runtime_identity_refuses_before_lock(self):
        with patch.object(c,'connect',side_effect=RuntimeError('production target')),patch.object(c,'ownership',side_effect=AssertionError('lock')):
            with self.assertRaisesRegex(RuntimeError,'production target'):c.serve(db_path=self.db)
    def test_real_process_sigterm_drains_current_unit(self):
        ctx=multiprocessing.get_context('fork');ready=ctx.Queue();release=ctx.Event()
        def worker():
            original=c.run_pending
            def slow(conn,task,**kw):
                ready.put(True)
                if not release.wait(10):raise RuntimeError('timed out')
                return {'idle':True}
            with patch.object(c,'run_pending',side_effect=lambda **kw:original(**kw,execute=slow)):
                c.serve(db_path=self.db)
        p=ctx.Process(target=worker);p.start()
        try:
            self.assertTrue(ready.get(timeout=10))
            import os,signal
            os.kill(p.pid,signal.SIGTERM);time.sleep(.1);self.assertTrue(p.is_alive());release.set();p.join(10)
            self.assertEqual(p.exitcode,0);self.assertEqual(self.rows()[0]['state'],'DONE',repr(self.rows()));self.assertEqual(len(self.rows()),1)
        finally:
            if p.is_alive():p.terminate();p.join(10)
