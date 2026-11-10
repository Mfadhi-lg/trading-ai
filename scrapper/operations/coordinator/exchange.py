"""IST scheduling enqueues durable work; one separate executor runs exchange workflows.

Imports/help are inert. Filesystem locks enforce one schedule and one executor
per resolved DB, including manual calls to this dispatch API. A task lease is
restart recovery state, not permission to overlap an executor still holding its
OS lock. No long workflow runs on an APScheduler callback or DB transaction.
"""
from contextlib import contextmanager
from datetime import date, timedelta
import fcntl
import json
import sqlite3
import time
from pathlib import Path
import threading
import uuid

from ...config import DB_PATH, SNAPSHOTS
from ...core.http import fetch_exchange_dataset
from ...db.database import connect, init_db, selected_backend
from ...db.exchange import tasks
from ...db.repo import utc_now
from ...operations.workers.lifecycle import WorkerLifecycle
from ...workflows.exchange import schedule


@contextmanager
def ownership(db_path, role):
    # DB identity/schema must be checked by the caller before creating the lock.
    if selected_backend() == 'postgresql':
        conn = connect(db_path)
        try:
            key = ':coordinator:' + role
            acquired = conn.execute('SELECT pg_try_advisory_lock(hashtextextended(current_schema() || %s,0))', (key,)).fetchone()[0]
            try:
                yield bool(acquired)
            finally:
                if acquired:
                    conn.execute('SELECT pg_advisory_unlock(hashtextextended(current_schema() || %s,0))', (key,))
        finally:
            conn.close()
        return
    path = Path(str(Path(db_path).resolve()) + '.exchange-' + role + '.lock')
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def enqueue_attempt(conn, rung, *, now, workflow=schedule):
    day = (date.fromisoformat(workflow.ist_day(now)) - timedelta(days=rung.day_offset)).isoformat()
    return tasks.enqueue(conn, kind='attempt', scheduled_for=day,
                         attempt=rung.attempt, priority=0, now=now)


def enqueue_report(conn, *, now, workflow=schedule):
    return tasks.enqueue(conn, kind='weekly_report', scheduled_for=workflow.ist_day(now),
                         attempt=0, priority=20, now=now)


def enqueue_catchup(conn, *, now, max_days=schedule.DEFAULT_CATCHUP_DAYS, workflow=schedule):
    if not isinstance(max_days, int) or not 1 <= max_days <= 31:
        raise ValueError('catch-up must be bounded to 1–31 days')
    today = date.fromisoformat(workflow.ist_day(now))
    latest = conn.execute("SELECT MAX(scheduled_for) FROM exchange_schedule_runs WHERE role='primary'").fetchone()[0]
    seen = {row[0] for row in conn.execute("SELECT DISTINCT scheduled_for FROM exchange_schedule_runs WHERE role='primary'")}
    days = sorted((today-timedelta(days=offset)).isoformat() for offset in range(1,max_days+1)
                  if (today-timedelta(days=offset)).weekday()!=6)
    missed = [day for day in days if day not in seen and (latest is None or day>latest)]
    ids = [tasks.enqueue(conn, kind='attempt', scheduled_for=day,
                         attempt=workflow.LADDER[-1].attempt, priority=10, now=now)['task_id'] for day in missed]
    return {'schema_version': workflow.SCHEDULER_SCHEMA_VERSION, 'job':'catch_up',
            'started_at_utc':now, 'max_days':max_days, 'latest_recorded_day':latest,
            'missed_days':missed, 'attempts':[], 'queued_task_ids':ids, 'status':'ok'}


def execute_task(task, *, db_path, snapshot_root, now_utc, fetcher, workflow=schedule):
    if task['kind']=='weekly_report':
        return workflow.run_weekly_report(db_path=db_path,now_utc=now_utc)
    # Preserve the existing bounded DB-lock retry ladder for startup catch-up.
    # Current cron attempts have their existing later publication rungs instead.
    tries=4 if task['priority']==10 else 1
    for lock_attempt in range(tries):
        try:
            return workflow.run_attempt(attempt=task['attempt'],scheduled_for=task['scheduled_for'],
                db_path=db_path,snapshot_root=snapshot_root,now_utc=now_utc,fetcher=fetcher)
        except sqlite3.OperationalError as exc:
            if tries==1:
                raise
            if 'locked' not in str(exc).lower() or lock_attempt==tries-1:
                return {'scheduled_for':task['scheduled_for'],'status':'incomplete',
                        'error':f'catch-up database lock: {exc}'}
            time.sleep(lock_attempt+1)


def run_pending(*, db_path=DB_PATH, snapshot_root=SNAPSHOTS, now_utc=utc_now,
                fetcher=fetch_exchange_dataset, limit=10, stop=None, workflow=schedule,
                publish=None, lease_seconds=600):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError('dispatch budget must be 1–100 tasks')
    conn = connect(db_path)
    try:
        init_db(conn)  # explicit migration required, never implicit application
        with ownership(db_path, 'executor') as acquired:
            if not acquired:
                return {'processed':0, 'status':'busy'}
            completed=0
            while completed<limit and not (stop and stop.is_set()):
                task = tasks.claim(conn, owner=uuid.uuid4().hex, now=now_utc(), seconds=lease_seconds)
                if task is None:
                    break
                # Transaction committed before source/network/report work.
                try:
                    payload=execute_task(task,db_path=db_path,snapshot_root=snapshot_root,
                                         now_utc=now_utc,fetcher=fetcher,workflow=workflow)
                except Exception as exc:
                    tasks.finish(conn,task,now=now_utc(),error=f'{type(exc).__name__}: {exc}')
                    workflow.alert({'job':'exchange_dispatch','task_id':task['task_id'],'status':'failed','error':str(exc)})
                else:
                    # The per-DB executor lock remains held even beyond lease
                    # expiry. Another dispatcher cannot repeat an active workflow.
                    tasks.finish(conn,task,now=now_utc(),result=payload)
                    if publish is not None:
                        publish(payload)
                    if payload.get('status')!='ok':
                        workflow.alert(payload)
                completed+=1
            return {'processed':completed,'pending':tasks.pending_count(conn),'status':'ok'}
    finally:
        conn.close()


def register_jobs(scheduler, *, db_path, now_utc, workflow=schedule):
    from apscheduler.triggers.cron import CronTrigger
    def attempt_job(rung):
        conn=connect(db_path)
        try:
            init_db(conn)
            enqueue_attempt(conn,rung,now=now_utc(),workflow=workflow)
        finally:
            conn.close()
    for rung in workflow.LADDER:
        hour,minute=map(int,rung.ist_time.split(':'))
        scheduler.add_job(attempt_job,
            CronTrigger(day_of_week='mon-sat',hour=hour,minute=minute,timezone=schedule.IST),
            args=[rung],id=f'exchange-attempt-{rung.attempt}',
            misfire_grace_time=workflow.MISFIRE_GRACE_SECONDS,coalesce=True)
    def report_job():
        conn=connect(db_path)
        try:
            init_db(conn)
            enqueue_report(conn,now=now_utc(),workflow=workflow)
        finally:
            conn.close()
    hour,minute=map(int,workflow.EXCHANGE_REPORT_TIME.split(':'))
    scheduler.add_job(report_job,
        CronTrigger(day_of_week=workflow.EXCHANGE_REPORT_DAY,hour=hour,minute=minute,timezone=schedule.IST),
        id='exchange-weekly-report',misfire_grace_time=workflow.MISFIRE_GRACE_SECONDS,coalesce=True)


def serve(*, db_path, snapshot_root, now_utc, fetcher, workflow=schedule):
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError as exc:
        raise ImportError('exchange-scheduler needs APScheduler to serve the schedule: '
                          'pip install -r scrapper/requirements.txt. Use --once to run a single attempt without it.') from exc
    conn=connect(db_path)
    try:
        init_db(conn)
    finally:
        conn.close()
    with ownership(db_path,'scheduler') as acquired:
        if not acquired:
            raise RuntimeError('exchange coordinator already owns this database; stop the other scheduler first')
        conn=connect(db_path)
        try:
            catchup=enqueue_catchup(conn,now=now_utc(),workflow=workflow)
        finally:
            conn.close()
        print(json.dumps(catchup,sort_keys=True))
        scheduler=BlockingScheduler(timezone=schedule.IST)
        register_jobs(scheduler,db_path=db_path,now_utc=now_utc,workflow=workflow)
        started=now_utc()
        print(json.dumps({'schema_version':workflow.SCHEDULER_SCHEMA_VERSION,'job':'serve',
            'started_at_utc':started,'ladder':[r.as_dict() for r in workflow.LADDER],
            'weekly_report':f'{workflow.EXCHANGE_REPORT_DAY} {workflow.EXCHANGE_REPORT_TIME} IST',
            'datasets':list(workflow.scheduled_datasets())},sort_keys=True))
        stop=threading.Event()
        def consume():
            try:
                while not stop.is_set():
                    try:
                        run_pending(db_path=db_path,snapshot_root=snapshot_root,now_utc=now_utc,fetcher=fetcher,
                            limit=1,stop=stop,workflow=workflow,publish=lambda p:print(json.dumps(p,sort_keys=True)))
                    except Exception as exc:
                        workflow.alert({'job':'exchange_dispatch','status':'failed','error':str(exc)})
                    stop.wait(1)
            finally:
                if stop.is_set() and scheduler.running:
                    try:
                        scheduler.shutdown(wait=False)
                    except Exception:
                        pass
        worker=threading.Thread(target=consume,name='exchange-dispatch',daemon=False)
        with WorkerLifecycle(event=stop).signals():
            worker.start()
            try:
                if not stop.is_set():
                    scheduler.start()
            except (KeyboardInterrupt,SystemExit):
                if scheduler.running:
                    try:
                        scheduler.shutdown(wait=False)
                    except Exception:
                        pass
            finally:
                stop.set()
                worker.join()  # finish current bounded workflow before return
        return {'schema_version':workflow.SCHEDULER_SCHEMA_VERSION,'job':'serve',
                'started_at_utc':started,'finished_at_utc':now_utc(),'status':'ok'}


def run_scheduler(*, once=False, attempt=None, db_path=DB_PATH, snapshot_root=SNAPSHOTS,
                  now_utc=utc_now, fetcher=fetch_exchange_dataset):
    if once:
        payload=schedule.run_attempt(attempt=attempt or 1,db_path=db_path,snapshot_root=snapshot_root,
                                     now_utc=now_utc,fetcher=fetcher)
        if payload['status']!='ok':
            schedule.alert(payload)
        return payload
    return serve(db_path=db_path,snapshot_root=snapshot_root,now_utc=now_utc,fetcher=fetcher)
