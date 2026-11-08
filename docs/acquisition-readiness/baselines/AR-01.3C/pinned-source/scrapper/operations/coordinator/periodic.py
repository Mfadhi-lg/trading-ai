"""Enqueue-only timer and separate bounded delayed/recovery/housekeeping executor.

Local per-DB locks, rather than an expiring task lease, prevent overlap while a
source request runs. Underlying expectation/recovery claims recover after crash.
Admissions commit before work; interrupted slots remain consumed, with original
work still durable. Imports never start threads, schedulers or writers.
"""
import json
import threading
from ...config import DB_PATH
from ...db.database import connect, init_db
from ...db.repo import utc_now
from ...db import periodic_tasks as tasks
from ...db.documents.due import preview_due_expectations
from ...db.documents.expectations import ensure_result_artifact_expectations
from ...db.documents.recovery.targets import build_recovery_targets
from ...documents.recovery.alternate_sources import run_document_recovery_worker
from ...workflows.documents.reconciliation import reconcile_artifacts
from ..workers.screener_requests import SharedScreenerClient
from ..workers.lifecycle import WorkerLifecycle
from .collection import priority_ready
from .exchange import ownership


def schedule_tick(*, db_path=DB_PATH, now_utc=utc_now, startup=False):
    conn=connect(db_path)
    try:
        init_db(conn)
        # Preserve the reconciler's startup30s wait; restart keeps pending work.
        return [tasks.enqueue(conn,kind=kind,now=now_utc()) for kind in tasks.KINDS
                if not startup or kind=='recovery']
    finally:
        conn.close()


def execute_unit(conn, task, *, db_path, now_utc):
    if task['kind']=='delayed':
        if not preview_due_expectations(conn,now_utc=now_utc(),limit=1):
            return {'idle':True}
        result=reconcile_artifacts(due=True,limit=1,db_path=db_path,now_utc=now_utc,
            shared_permits=True,shared_client_factory=SharedScreenerClient)
        return {'idle':result['summary']['claimed_count']==0,'payload':result}
    if task['kind']=='recovery':
        # Each admitted slot starts at a different source, preserving fair
        # rotation even though the legacy worker is invoked one unit at a time.
        sources=('bse','nse','issuer-ir')
        pos=(task['admitted']-1)%len(sources)
        result=run_document_recovery_worker(conn,sources=sources[pos:]+sources[:pos],
            limit=1,throttle_seconds=2,now=now_utc)
        return {'idle':result['processed_target_source_count']==0,'payload':result}
    accepted,artifact=tasks.housekeeping_rows(conn,task)
    if accepted is not None:
        ensure_result_artifact_expectations(conn,company_id=accepted['company_id'],
            filing_id=accepted['filing_id'],exchange=accepted['exchange'],exchange_key=accepted['exchange_key'],
            result_period=accepted['result_period'],period_end=accepted['period_end_date'],
            broadcast_at_utc=accepted['broadcast_at_utc'],created_at_utc=now_utc())
    if artifact is not None:
        build_recovery_targets(conn,artifact_ids=(artifact['artifact_id'],),observed_at_utc=now_utc())
    # Advance only after both idempotent operations. Crash repeats safely.
    tasks.cursors(conn,task['task_id'],accepted['queue_item_id'] if accepted else 0,
                  artifact['artifact_id'] if artifact else 0)
    return {'idle':accepted is None and artifact is None,
            'accepted_queue_id':accepted['queue_item_id'] if accepted else None,
            'artifact_id':artifact['artifact_id'] if artifact else None}


def run_pending(*, db_path=DB_PATH, now_utc=utc_now, stop=None, execute=execute_unit):
    conn=connect(db_path)
    try:
        init_db(conn)
        with ownership(db_path,'periodic-executor') as acquired:
            if not acquired:
                return {'status':'busy','processed':0}
            completed=0
            # Round robin each unit protects delayed expectations from recovery.
            # Each call admits at most one unit per kind, keeping dispatch bounded.
            for kind in tasks.KINDS:
                if stop is not None and stop.is_set():
                    return {'status':'draining','processed':completed}
                if priority_ready(conn,now_utc()):
                    return {'status':'priority_wait','processed':completed}
                task=conn.execute("SELECT * FROM periodic_dispatch_tasks WHERE kind=? AND state='PENDING'",(kind,)).fetchone()
                if task is None:
                    continue
                task=dict(task)
                if not tasks.admit(conn,task['task_id']):
                    tasks.finish(conn,task,now=now_utc(),result={'budget_exhausted':True})
                    continue
                task['admitted']+=1
                try:
                    result=execute(conn,task,db_path=db_path,now_utc=now_utc)
                except Exception as exc:
                    tasks.finish(conn,task,now=now_utc(),error=f'{type(exc).__name__}: {exc}')
                    raise
                if result.get('idle') or task['admitted']==task['budget']:
                    tasks.finish(conn,task,now=now_utc(),result=result)
                completed+=1
            return {'status':'ok' if completed else 'idle','processed':completed}
    finally:
        conn.close()


def serve(*, db_path=DB_PATH, now_utc=utc_now):
    from apscheduler.schedulers.background import BackgroundScheduler
    conn=connect(db_path)
    try:
        init_db(conn)
    finally:
        conn.close()
    with ownership(db_path,'periodic-scheduler') as acquired:
        if not acquired:
            raise RuntimeError('periodic coordinator already owns this database')
        stop=threading.Event()
        scheduler=BackgroundScheduler(timezone='UTC')
        def tick():
            if not stop.is_set():
                schedule_tick(db_path=db_path,now_utc=now_utc)
        scheduler.add_job(tick,'interval',seconds=1,id='periodic-admission',coalesce=True,
                          misfire_grace_time=300,max_instances=1)
        with WorkerLifecycle(event=stop).signals():
            schedule_tick(db_path=db_path,now_utc=now_utc,startup=True)
            # Start full admission after30s without sleeping in a callback.
            from datetime import datetime,timedelta
            scheduler.get_job('periodic-admission').modify(next_run_time=datetime.fromisoformat(now_utc())+timedelta(seconds=30))
            scheduler.start()
            try:
                while not stop.is_set():
                    try:
                        result=run_pending(db_path=db_path,now_utc=now_utc,stop=stop)
                        if result['status'] not in ('idle','priority_wait'):
                            print(json.dumps(result,sort_keys=True))
                    except Exception as exc:
                        print(json.dumps({'status':'failed','error':str(exc)},sort_keys=True))
                    stop.wait(1)
            finally:
                stop.set()
                scheduler.shutdown(wait=True)
        return {'status':'ok'}
