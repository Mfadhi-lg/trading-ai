"""Bounded collection batches, separate scheduling and executor ownership.

Existing due scope rows are durable company work. A persisted admission budget
survives crash/restart without expanding a batch; consumed interrupted slots
remain consumed. No network work runs in the scheduling callback.
"""
import json
import threading
from ...config import DB_PATH, SNAPSHOTS
from ...db.database import connect, init_db
from ...db.repo import utc_now
from ...db.screener import scope_tasks
from ...db.screener_discovery_repo import preview_due_companies
from ...commands.collection_scope import monitor
from ..workers.lifecycle import WorkerLifecycle
from .exchange import ownership


def validate_limit(limit):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('collection limit must be 1–100')


def schedule_tick(*, db_path=DB_PATH, now_utc=utc_now, limit=40):
    validate_limit(limit)
    conn=connect(db_path)
    try:
        init_db(conn)
        return scope_tasks.enqueue(conn,now=now_utc(),budget=limit)
    finally:
        conn.close()


def priority_ready(conn, now):
    # Preserve the existing company queue's eligibility/due policy. Active
    # company work also yields the Screener host lane until it settles.
    if preview_due_companies(conn,now_utc=now,limit=1):
        return True
    if conn.execute("SELECT 1 FROM screener_company_queue WHERE status IN ('claimed','running') AND collection_disposition IS NULL LIMIT 1").fetchone():
        return True
    return conn.execute("SELECT 1 FROM exchange_coordinator_tasks WHERE priority=0 AND state IN ('PENDING','RUNNING') LIMIT 1").fetchone() is not None


def run_pending(*, db_path=DB_PATH, snapshot_root=SNAPSHOTS, now_utc=utc_now,
                stop=None, monitor_fn=monitor):
    conn=connect(db_path)
    try:
        init_db(conn)
        with ownership(db_path,'collection-executor') as acquired:
            if not acquired:
                return {'status':'busy','checked':0}
            task=conn.execute("SELECT * FROM collection_scope_tasks WHERE state='PENDING'").fetchone()
            if task is None:
                return {'status':'idle','checked':0}
            reason=[None]
            def admit():
                if stop is not None and stop.is_set():
                    reason[0]='draining'
                    return False
                now=now_utc()
                if priority_ready(conn,now):
                    reason[0]='priority_wait'
                    return False
                if conn.execute("SELECT 1 FROM screener_collection_scope WHERE next_check_at_utc<=? AND (claim_token IS NULL OR claim_expires_at_utc<=?) LIMIT 1",(now,now)).fetchone() is None:
                    # Active interrupted scope claims remain recoverable. Keep
                    # the batch pending until they expire, without spending slots.
                    if conn.execute("SELECT 1 FROM screener_collection_scope WHERE next_check_at_utc<=? AND claim_token IS NOT NULL LIMIT 1",(now,)).fetchone():
                        reason[0]='lease_wait'
                    return False
                return scope_tasks.admit(conn,task['task_id'])
            remaining=task['budget']-task['admitted']
            try:
                result=monitor_fn(conn,limit=remaining,now_fn=now_utc,snapshot_root=snapshot_root,
                    shared_permits=True,before_claim=admit) if remaining else {'checked':0,'outcomes':[]}
            except Exception as exc:
                scope_tasks.finish(conn,task['task_id'],now=now_utc(),error=f'{type(exc).__name__}: {exc}')
                raise
            if reason[0] is None:
                scope_tasks.finish(conn,task['task_id'],now=now_utc(),result=result)
            return {**result,'task_id':task['task_id'],'status':reason[0] or 'done'}
    finally:
        conn.close()


def serve(*, db_path=DB_PATH, snapshot_root=SNAPSHOTS, now_utc=utc_now, limit=40):
    validate_limit(limit)
    from apscheduler.schedulers.background import BackgroundScheduler
    # Guard identity/schema before lock, directory creation or worker startup.
    conn=connect(db_path)
    try:
        init_db(conn)
    finally:
        conn.close()
    with ownership(db_path,'collection-scheduler') as acquired:
        if not acquired:
            raise RuntimeError('collection coordinator already owns this database')
        stop=threading.Event()
        scheduler=BackgroundScheduler(timezone='UTC')
        def tick():
            if not stop.is_set():
                schedule_tick(db_path=db_path,now_utc=now_utc,limit=limit)
        scheduler.add_job(tick,'interval',seconds=60,id='collection-scope-admission',coalesce=True,misfire_grace_time=3600,max_instances=1)
        with WorkerLifecycle(event=stop).signals():
            tick()  # one bounded catch-up batch, never replay every missed hour
            scheduler.start()
            try:
                while not stop.is_set():
                    result=run_pending(db_path=db_path,snapshot_root=snapshot_root,now_utc=now_utc,stop=stop)
                    if result['status'] not in ('idle','priority_wait','lease_wait','busy'):
                        print(json.dumps(result,sort_keys=True))
                    stop.wait(1)
            finally:
                stop.set()
                scheduler.shutdown(wait=True)
        return {'status':'ok'}
