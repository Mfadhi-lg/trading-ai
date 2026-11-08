"""Offline scheduling, durable restart, fencing and separate-executor contracts."""
import ast
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import inspect
import io
import json
import multiprocessing
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scrapper.db.database import connect, init_db
from scrapper.db.exchange import tasks
from scrapper.operations.coordinator import exchange as coordinator
from scrapper.pipeline import scheduler as facade
from scrapper.workflows.exchange import schedule as workflow
from scrapper.tests.database_fixtures import prepare_database
from scrapper.tests.test_e06_scheduler import SchedulerCase, DAY_ONE, RESULT_FEED, REJECTED

NOW='2026-08-10T14:00:00+00:00'
LATER='2026-08-10T14:20:00+00:00'


def claimed_process(db, ready):
    conn=connect(Path(db));init_db(conn)
    with coordinator.ownership(Path(db),'executor') as held:
        assert held
        task=tasks.claim(conn,owner='crashed',now=NOW,seconds=1)
        ready.put(task)
        time.sleep(30)


def locked_process(db, ready):
    with coordinator.ownership(Path(db),'executor') as held:
        ready.put(held)
        time.sleep(30)


def serving_process(db, ready, release):
    api=SimpleNamespace(**{name:getattr(workflow,name) for name in (
        'LADDER','MISFIRE_GRACE_SECONDS','DEFAULT_CATCHUP_DAYS','SCHEDULER_SCHEMA_VERSION',
        'EXCHANGE_REPORT_DAY','EXCHANGE_REPORT_TIME','scheduled_datasets','ist_day')})
    def run(**kw):
        ready.put(kw['scheduled_for'])
        if not release.wait(10):raise RuntimeError('fixture timeout')
        return {'status':'ok'}
    api.run_attempt=run;api.run_weekly_report=lambda **kw:{'status':'ok'};api.alert=lambda p:None
    coordinator.serve(db_path=Path(db),snapshot_root=Path(db).parent/'snapshots',now_utc=lambda:NOW,fetcher=lambda *a:None,workflow=api)


class CoordinatorCase(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.db=self.root/'tasks.db'
        prepare_database(self.db);self.conn=connect(self.db);init_db(self.conn)
        self.addCleanup(self.conn.close)
    def enqueue(self, day='2026-08-10', attempt=1, priority=0, kind='attempt'):
        return tasks.enqueue(self.conn,kind=kind,scheduled_for=day,attempt=attempt,priority=priority,now=NOW)
    def stub(self, run=None):
        return SimpleNamespace(run_attempt=run or (lambda **kw:{'status':'ok','day':kw['scheduled_for']}),
                               run_weekly_report=lambda **kw:{'status':'ok'},alert=lambda p:None)


class DispatchContracts(CoordinatorCase):
    def test_callbacks_preserve_cron_policy_and_only_enqueue(self):
        from apscheduler.schedulers.blocking import BlockingScheduler
        scheduler=BlockingScheduler(timezone=workflow.IST)
        with patch.object(workflow,'run_attempt',side_effect=AssertionError('fetch in cron')), patch.object(workflow,'run_weekly_report',side_effect=AssertionError('report in cron')):
            coordinator.register_jobs(scheduler,db_path=self.db,now_utc=lambda:NOW)
            jobs=scheduler.get_jobs()
            self.assertEqual([j.id for j in jobs],[f'exchange-attempt-{r.attempt}' for r in workflow.LADDER]+['exchange-weekly-report'])
            for job in jobs:
                self.assertEqual(job.misfire_grace_time,604800);self.assertTrue(job.coalesce)
                job.func(*job.args)
            # A coalesced/double callback is a single durable task.
            jobs[0].func(*jobs[0].args)
        rows=self.conn.execute('SELECT * FROM exchange_coordinator_tasks ORDER BY task_id').fetchall()
        self.assertEqual(len(rows),4)
        self.assertEqual([r['scheduled_for'] for r in rows[:3]],['2026-08-10','2026-08-10','2026-08-09'])
        for job,rung in zip(jobs,workflow.LADDER):
            fields={f.name:str(f) for f in job.trigger.fields}
            self.assertEqual((fields['day_of_week'],fields['hour'],fields['minute']),('mon-sat',str(int(rung.ist_time[:2])),str(int(rung.ist_time[3:]))))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM exchange_schedule_runs').fetchone()[0],0)

    def test_pending_work_is_prioritized_budgeted_and_restart_idempotent(self):
        self.enqueue('2026-08-07',3,10);self.enqueue('2026-08-09',0,20,'weekly_report');self.enqueue()
        seen=[]
        api=self.stub(lambda **kw:seen.append(kw['scheduled_for']) or {'status':'ok'})
        first=coordinator.run_pending(db_path=self.db,workflow=api,now_utc=lambda:NOW,limit=1)
        self.assertEqual(seen,['2026-08-10']);self.assertEqual(first['pending'],2)
        self.enqueue() # DONE isn't reset or duplicated
        coordinator.run_pending(db_path=self.db,workflow=api,now_utc=lambda:NOW,limit=1)
        self.assertEqual(seen,['2026-08-10','2026-08-07'])
        coordinator.run_pending(db_path=self.db,workflow=api,now_utc=lambda:NOW,limit=1)
        self.assertEqual(tasks.pending_count(self.conn),0)
        self.assertEqual(coordinator.run_pending(db_path=self.db,workflow=api,now_utc=lambda:NOW)['processed'],0)

    def test_catchup_matches_legacy_days_and_is_enqueue_only(self):
        payload=coordinator.enqueue_catchup(self.conn,now=NOW,max_days=7)
        with patch.object(facade,'run_attempt',side_effect=lambda **kw:{'status':'ok'}):
            old=facade.catch_up_missed_days(db_path=self.db,max_days=7,now_utc=lambda:NOW)
        self.assertEqual(payload['missed_days'],old['missed_days'])
        again=coordinator.enqueue_catchup(self.conn,now=NOW,max_days=7)
        self.assertEqual(payload['queued_task_ids'],again['queued_task_ids'])
        self.assertEqual(len(payload['missed_days']),6)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM exchange_schedule_runs').fetchone()[0],0)
        self.assertTrue(all(r[0]==3 for r in self.conn.execute('SELECT attempt FROM exchange_coordinator_tasks')))

    def test_catchup_keeps_primary_latest_day_filter_and_revision_does_not_satisfy(self):
        from scrapper.db.exchange.schedule import record_schedule_run
        def record(day,role):
            record_schedule_run(self.conn,dataset='nse_equity_master',scheduled_for=day,attempt=1,
                role=role,outcome='not_published',snapshot_id=None,error='',started_at_utc=NOW,finished_at_utc=NOW)
        record('2026-08-07','primary');record('2026-08-08','revision')
        result=coordinator.enqueue_catchup(self.conn,now=NOW)
        self.assertEqual(result['missed_days'],['2026-08-08'])
        for value in [0,32,None]:
            with self.assertRaises(ValueError):coordinator.enqueue_catchup(self.conn,now=NOW,max_days=value)

    def test_exception_is_visible_and_does_not_spend_request_retry(self):
        self.enqueue()
        with patch.object(workflow,'alert') as alert:
            coordinator.run_pending(db_path=self.db,now_utc=lambda:NOW,workflow=SimpleNamespace(run_attempt=lambda **kw:(_ for _ in ()).throw(ValueError('bad fixture')),alert=alert),limit=1)
        row=dict(self.conn.execute('SELECT * FROM exchange_coordinator_tasks').fetchone())
        self.assertEqual(row['state'],'FAILED');self.assertIn('bad fixture',row['error'])
        self.assertIsNone(row['owner']);alert.assert_called_once()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM exchange_schedule_runs').fetchone()[0],0)
        self.enqueue();self.assertEqual(tasks.pending_count(self.conn),0)

    def test_queued_catchup_preserves_four_lock_retries_and_terminal_accounting(self):
        self.enqueue('2026-08-07',3,10)
        api=self.stub()
        with patch.object(api,'run_attempt',side_effect=[sqlite3.OperationalError('database locked'),{'status':'ok'}]) as run,patch.object(coordinator.time,'sleep') as sleep:
            coordinator.run_pending(db_path=self.db,now_utc=lambda:NOW,workflow=api)
        self.assertEqual(run.call_count,2);sleep.assert_called_once_with(1)
        self.enqueue('2026-08-08',3,10)
        with patch.object(api,'run_attempt',side_effect=sqlite3.OperationalError('database locked')) as run,patch.object(coordinator.time,'sleep') as sleep:
            coordinator.run_pending(db_path=self.db,now_utc=lambda:NOW,workflow=api)
        self.assertEqual(run.call_count,4);self.assertEqual([c.args for c in sleep.call_args_list],[(1,),(2,),(3,)])
        row=self.conn.execute("SELECT state,result_json FROM exchange_coordinator_tasks WHERE scheduled_for='2026-08-08'").fetchone()
        self.assertEqual(row[0],'DONE');self.assertEqual(json.loads(row[1])['status'],'incomplete')
        self.enqueue('2026-08-08',1,0)
        with patch.object(api,'run_attempt',side_effect=sqlite3.OperationalError('database locked')) as run,patch.object(coordinator.time,'sleep') as sleep:
            coordinator.run_pending(db_path=self.db,now_utc=lambda:NOW,workflow=api)
        self.assertEqual(run.call_count,1);sleep.assert_not_called()

    def test_incomplete_result_is_accounted_without_new_network_retry(self):
        self.enqueue();api=self.stub(lambda **kw:{'status':'incomplete','outstanding':['nse_equity_master']})
        coordinator.run_pending(db_path=self.db,now_utc=lambda:NOW,workflow=api)
        row=self.conn.execute('SELECT state,result_json FROM exchange_coordinator_tasks').fetchone()
        self.assertEqual(row[0],'DONE');self.assertEqual(json.loads(row[1])['status'],'incomplete')
        self.assertEqual(tasks.pending_count(self.conn),0)

    def test_expired_worker_is_fenced_and_claim_has_no_open_transaction(self):
        self.enqueue();a=tasks.claim(self.conn,owner='old',now=NOW,seconds=1)
        self.assertFalse(self.conn.in_transaction)
        self.assertIsNone(tasks.claim(self.conn,owner='other',now=NOW))
        b=tasks.claim(self.conn,owner='new',now=LATER)
        self.assertGreater(b['generation'],a['generation'])
        with self.assertRaises(tasks.TaskOwnershipLost):tasks.finish(self.conn,a,now=LATER,result={})
        tasks.finish(self.conn,b,now=LATER,result={})
        with self.assertRaises(tasks.TaskOwnershipLost):tasks.finish(self.conn,b,now=LATER,result={})

    def test_second_executor_cannot_run_work_even_after_lease_expiry(self):
        self.enqueue();ctx=multiprocessing.get_context('spawn');ready=ctx.Queue()
        proc=ctx.Process(target=claimed_process,args=(str(self.db),ready));proc.start()
        try:
            ready.get(timeout=10)
            api=self.stub(lambda **kw:(_ for _ in ()).throw(AssertionError('overlap')))
            self.assertEqual(coordinator.run_pending(db_path=self.db,now_utc=lambda:LATER,workflow=api)['status'],'busy')
        finally:
            proc.kill();proc.join(10)
        recovered=coordinator.run_pending(db_path=self.db,now_utc=lambda:LATER,workflow=self.stub())
        self.assertEqual(recovered['processed'],1)
        self.assertEqual(self.conn.execute('SELECT generation FROM exchange_coordinator_tasks').fetchone()[0],2)

    def test_process_death_after_enqueue_before_claim_retains_work(self):
        self.enqueue();ctx=multiprocessing.get_context('spawn');ready=ctx.Queue()
        proc=ctx.Process(target=locked_process,args=(str(self.db),ready));proc.start()
        try:self.assertTrue(ready.get(timeout=10))
        finally:proc.kill();proc.join(10)
        self.assertEqual(coordinator.run_pending(db_path=self.db,now_utc=lambda:NOW,workflow=self.stub())['processed'],1)

    def test_stop_drains_current_work_and_leaves_remaining_pending(self):
        self.enqueue();self.enqueue('2026-08-10',2)
        stop=threading.Event()
        def run(**kw):
            self.assertFalse(self.conn.in_transaction)
            # Independent writer remains available during the callback.
            c=connect(self.db)
            try:
                c.execute('BEGIN IMMEDIATE');c.rollback()
            finally:c.close()
            stop.set();return {'status':'ok'}
        result=coordinator.run_pending(db_path=self.db,workflow=self.stub(run),now_utc=lambda:NOW,stop=stop)
        self.assertEqual((result['processed'],result['pending']),(1,1))
        self.assertEqual(coordinator.run_pending(db_path=self.db,workflow=self.stub(),now_utc=lambda:NOW,stop=stop)['processed'],0)

    def test_slow_executor_does_not_block_callback_or_current_priority(self):
        self.enqueue('2026-08-07',3,10)
        entered=threading.Event();release=threading.Event();errors=[]
        def slow(**kw):
            entered.set()
            if not release.wait(10):raise AssertionError('test did not release worker')
            return {'status':'ok'}
        def consume():
            try:coordinator.run_pending(db_path=self.db,now_utc=lambda:NOW,workflow=self.stub(slow),limit=1)
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=consume);thread.start()
        try:
            self.assertTrue(entered.wait(10))
            coordinator.enqueue_attempt(self.conn,workflow.LADDER[0],now=NOW)
            self.assertEqual(tasks.pending_count(self.conn),2)
        finally:release.set();thread.join(10)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        seen=[]
        coordinator.run_pending(db_path=self.db,now_utc=lambda:NOW,workflow=self.stub(lambda **kw:seen.append(kw['scheduled_for']) or {'status':'ok'}))
        self.assertEqual(seen,['2026-08-10'])

    def test_scheduler_lock_refuses_duplicate_owner_and_releases(self):
        with coordinator.ownership(self.db,'scheduler') as held:
            self.assertTrue(held)
            with self.assertRaisesRegex(RuntimeError,'already owns'):
                coordinator.serve(db_path=self.db,snapshot_root=self.root/'snap',now_utc=lambda:NOW,fetcher=lambda *a:None)
        with coordinator.ownership(self.db,'scheduler') as held:self.assertTrue(held)

    def test_sigterm_finishes_current_task_and_stops_before_next(self):
        import os
        ctx=multiprocessing.get_context('spawn');ready=ctx.Queue();release=ctx.Event()
        proc=ctx.Process(target=serving_process,args=(str(self.db),ready,release));proc.start()
        try:
            ready.get(timeout=10)
            os.kill(proc.pid,signal.SIGTERM)
            release.set();proc.join(10)
            self.assertFalse(proc.is_alive());self.assertEqual(proc.exitcode,0)
        finally:
            if proc.is_alive():proc.kill();proc.join(10)
        states=dict(self.conn.execute('SELECT state,COUNT(*) FROM exchange_coordinator_tasks GROUP BY state').fetchall())
        self.assertEqual(states.get('DONE'),1);self.assertEqual(states.get('RUNNING',0),0)
        self.assertGreater(states.get('PENDING',0),0)

    def test_rollback_keeps_inbox_and_requires_explicit_073_compatibility(self):
        from scrapper.db import database
        self.enqueue()
        before=[dict(r) for r in self.conn.execute('SELECT * FROM exchange_coordinator_tasks')]
        old_names=[p.name for p in database.MIGRATIONS_PATH.glob('*.sql') if p.name!='073_exchange_coordinator_tasks.sql']
        with patch.object(database,'_migration_names',return_value=old_names):
            with self.assertRaisesRegex(RuntimeError,'unknown applied identities: 073'):
                init_db(self.conn)
        # A role rollback retaining the additive schema identities can read the
        # post-change DB without dropping or consuming pending dispatch history.
        init_db(self.conn)
        facade.day_status('2026-08-10',db_path=self.db,now_utc=lambda:NOW)
        self.assertEqual([dict(r) for r in self.conn.execute('SELECT * FROM exchange_coordinator_tasks')],before)

    def test_missing_migration_and_identity_guard_fail_before_lock_or_work(self):
        conn=self.conn
        with conn:conn.execute("DELETE FROM schema_migrations WHERE version='073_exchange_coordinator_tasks.sql'")
        with self.assertRaisesRegex(RuntimeError,'pending migrations'):
            coordinator.run_pending(db_path=self.db,now_utc=lambda:NOW)
        self.assertFalse(Path(str(self.db)+'.exchange-executor.lock').exists())
        with patch.object(coordinator,'connect',side_effect=RuntimeError('identity refused')):
            with self.assertRaisesRegex(RuntimeError,'identity refused'):
                coordinator.run_pending(db_path=self.db,now_utc=lambda:NOW)


class WorkflowParity(SchedulerCase):
    def test_real_dispatch_preserves_publication_and_revision_outputs(self):
        prepare_database(self.db)
        clock=lambda:'2026-08-06T14:00:00+00:00'
        conn=connect(self.db)
        try:coordinator.enqueue_attempt(conn,workflow.LADDER[0],now=clock())
        finally:conn.close()
        saved=[]
        def bounded(**kw):
            return workflow.run_attempt(**kw,datasets=('nse_financial_results',),thresholds=self.thresholds)
        coordinator.run_pending(db_path=self.db,snapshot_root=self.snapshots,now_utc=clock,
            fetcher=self.fetcher(RESULT_FEED),workflow=SimpleNamespace(run_attempt=bounded,alert=lambda p:None),publish=saved.append)
        self.assertEqual(len(saved),1);self.assertEqual(saved[0]['revision_period'],'2026-03')
        self.assertEqual(len(self.issued),2)
        # Enqueue repeat, including restart, does not refetch a completed rung.
        conn=connect(self.db)
        try:coordinator.enqueue_attempt(conn,workflow.LADDER[0],now=clock())
        finally:conn.close()
        coordinator.run_pending(db_path=self.db,now_utc=clock,workflow=SimpleNamespace(run_attempt=bounded,alert=lambda p:None))
        self.assertEqual(len(self.issued),2)

    def test_access_hold_is_shared_with_old_paths_and_future_rungs(self):
        prepare_database(self.db);clock=lambda:'2026-08-06T14:00:00+00:00'
        def bounded(**kw):return workflow.run_attempt(**kw,datasets=('bse_security_master',),thresholds=self.thresholds)
        api=SimpleNamespace(run_attempt=bounded,alert=lambda p:None)
        conn=connect(self.db)
        try:coordinator.enqueue_attempt(conn,workflow.LADDER[0],now=clock())
        finally:conn.close()
        coordinator.run_pending(db_path=self.db,snapshot_root=self.snapshots,now_utc=clock,fetcher=self.fetcher(REJECTED),workflow=api)
        count=len(self.issued)
        conn=connect(self.db)
        try:coordinator.enqueue_attempt(conn,workflow.LADDER[1],now=clock())
        finally:conn.close()
        coordinator.run_pending(db_path=self.db,snapshot_root=self.snapshots,now_utc=clock,fetcher=self.fetcher(REJECTED),workflow=api)
        self.assertEqual(len(self.issued),count)
        self.assertIn('bse_security_master',facade.day_status('2026-08-06',db_path=self.db,now_utc=clock)['access_blocked_datasets'])

    def test_death_after_publication_before_dispatch_finish_reuses_legacy_ledger(self):
        prepare_database(self.db);clock=lambda:'2026-08-06T14:00:00+00:00'
        def bounded(**kw):return workflow.run_attempt(**kw,datasets=('nse_equity_master',),thresholds=self.thresholds)
        api=SimpleNamespace(run_attempt=bounded,alert=lambda p:None)
        conn=connect(self.db)
        try:coordinator.enqueue_attempt(conn,workflow.LADDER[0],now=clock())
        finally:conn.close()
        with patch.object(tasks,'finish',side_effect=SystemExit('after durable workflow')):
            with self.assertRaises(SystemExit):
                coordinator.run_pending(db_path=self.db,snapshot_root=self.snapshots,now_utc=clock,fetcher=self.fetcher(DAY_ONE),workflow=api,lease_seconds=1)
        snapshots=self.rows('SELECT * FROM exchange_source_snapshots')
        ledger=self.rows('SELECT * FROM exchange_schedule_runs')
        self.assertEqual(len(self.issued),1)
        coordinator.run_pending(db_path=self.db,snapshot_root=self.snapshots,now_utc=lambda:'2026-08-06T14:20:00+00:00',fetcher=self.fetcher(DAY_ONE),workflow=api)
        self.assertEqual(len(self.issued),1)
        self.assertEqual(self.rows('SELECT * FROM exchange_source_snapshots'),snapshots)
        self.assertEqual(self.rows('SELECT * FROM exchange_schedule_runs'),ledger)
        self.assertEqual(self.rows('SELECT state,generation FROM exchange_coordinator_tasks')[0],{'state':'DONE','generation':2})

    def test_crash_after_snapshot_before_schedule_ledger_retains_evidence_and_restarts(self):
        prepare_database(self.db);clock=lambda:'2026-08-06T14:00:00+00:00'
        def bounded(**kw):return workflow.run_attempt(**kw,datasets=('nse_equity_master',),thresholds=self.thresholds)
        api=SimpleNamespace(run_attempt=bounded,alert=lambda p:None)
        conn=connect(self.db)
        try:coordinator.enqueue_attempt(conn,workflow.LADDER[0],now=clock())
        finally:conn.close()
        attempt=workflow._attempt_dataset
        def crash(dataset,**kw):
            return attempt(dataset,**kw,record_schedule_run=lambda *a,**k:(_ for _ in ()).throw(SystemExit('after snapshot')))
        patched=lambda **kw:workflow.run_attempt(**kw,datasets=('nse_equity_master',),thresholds=self.thresholds,_attempt_dataset=crash)
        with self.assertRaises(SystemExit):
            coordinator.run_pending(db_path=self.db,snapshot_root=self.snapshots,now_utc=clock,fetcher=self.fetcher(DAY_ONE),workflow=SimpleNamespace(run_attempt=patched,alert=lambda p:None),lease_seconds=1)
        snapshots=self.rows('SELECT * FROM exchange_source_snapshots')
        self.assertTrue(snapshots);self.assertEqual(self.rows('SELECT * FROM exchange_schedule_runs'),[])
        coordinator.run_pending(db_path=self.db,snapshot_root=self.snapshots,now_utc=lambda:'2026-08-06T14:20:00+00:00',fetcher=self.fetcher(DAY_ONE),workflow=api)
        retained=self.rows('SELECT * FROM exchange_source_snapshots')
        self.assertTrue(all(row in retained for row in snapshots))
        self.assertEqual(len(self.rows('SELECT * FROM exchange_schedule_runs')),1)
        self.assertEqual(self.rows('SELECT state FROM exchange_coordinator_tasks')[0]['state'],'DONE')

    def test_existing_catchup_lock_retry_is_now_reachable(self):
        prepare_database(self.db)
        # Original scheduler referenced sqlite3 in this exception path without importing it.
        with patch.object(facade,'run_attempt',side_effect=[sqlite3.OperationalError('database is locked'),{'status':'ok'}]),patch.object(facade.time,'sleep') as sleep:
            result=facade.catch_up_missed_days(db_path=self.db,max_days=1,now_utc=lambda:'2026-08-07T14:00:00+00:00')
        sleep.assert_called_once_with(1);self.assertEqual(result['status'],'ok')


class ArchitectureContracts(unittest.TestCase):
    def test_legacy_signatures_hooks_and_cli_targets(self):
        source=(Path(__file__).parents[1]/'pipeline/scheduler.py').read_text()
        self.assertNotIn('BlockingScheduler',source)
        self.assertNotIn('SELECT ',source)
        with patch.object(facade,'scheduled_datasets',return_value=('nse_equity_master',)):
            self.assertEqual(facade.scheduled_datasets(),('nse_equity_master',))
        cli=(Path(__file__).parents[1]/'cli/__init__.py').read_text()
        self.assertIn("lazy_handler('.operations.coordinator.exchange', 'run_scheduler')",cli)
        tree=ast.parse((Path(__file__).parents[1]/'operations/coordinator/exchange.py').read_text())
        self.assertFalse(any(isinstance(n,ast.ImportFrom) and any(x in (n.module or '') for x in ('pipeline','cli','view','sources')) for n in ast.walk(tree)))

    def test_import_and_help_have_no_writes_network_db_or_threads(self):
        code="""import sys,runpy,threading
sys.dont_write_bytecode=True
def deny(*a,**k):raise AssertionError('thread start')
threading.Thread.start=deny
def audit(e,a):
 if e in ('sqlite3.connect','socket.connect','subprocess.Popen','os.mkdir','os.remove','os.rename'):raise AssertionError(e)
 if e=='open' and (a[2]&3):raise AssertionError('write')
sys.addaudithook(audit)
import scrapper.operations.coordinator.exchange
import scrapper.workflows.exchange.schedule
import scrapper.db.exchange.tasks
import scrapper.pipeline.scheduler
sys.argv=['scrapper.cli','exchange-scheduler','--help']
runpy.run_module('scrapper.cli',run_name='__main__')
"""
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('--once',result.stdout)
