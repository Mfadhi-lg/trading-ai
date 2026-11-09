"""Durable bounded exchange dispatch; legacy attempt ledgers remain authoritative."""
from datetime import date, datetime, timedelta, timezone
import json
from ..repository_backend import is_postgresql, transaction


class TaskOwnershipLost(RuntimeError):
    pass


def _instant(now):
    moment = datetime.fromisoformat(now)
    if moment.tzinfo is None:
        raise ValueError('task clock requires an aware UTC instant')
    return moment.astimezone(timezone.utc)


def enqueue(conn, *, kind, scheduled_for, attempt, priority, now):
    date.fromisoformat(scheduled_for)
    stamp = _instant(now).isoformat()
    if (kind, priority) not in (('attempt', 0), ('attempt', 10), ('weekly_report', 20)):
        raise ValueError('invalid exchange task kind/priority')
    if (kind == 'attempt' and attempt not in (1, 2, 3)) or (kind == 'weekly_report' and attempt != 0):
        raise ValueError('invalid exchange task rung')
    if is_postgresql(conn):
        return _enqueue_postgresql(conn, kind, scheduled_for, attempt, priority, stamp)
    with conn:
        conn.execute('''INSERT INTO exchange_coordinator_tasks
            (kind, scheduled_for, attempt, priority, created_at_utc)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(kind, scheduled_for, attempt) DO UPDATE SET
            priority=MIN(exchange_coordinator_tasks.priority, excluded.priority)''',
            (kind, scheduled_for, attempt, priority, stamp))
    return dict(conn.execute('''SELECT * FROM exchange_coordinator_tasks
        WHERE kind=? AND scheduled_for=? AND attempt=?''', (kind, scheduled_for, attempt)).fetchone())


def claim(conn, *, owner, now, seconds=600):
    if not owner or not isinstance(seconds, (int, float)) or not 0 < seconds <= 86400:
        raise ValueError('task owner and bounded positive lease required')
    stamp = _instant(now).isoformat()
    until = (_instant(now) + timedelta(seconds=seconds)).isoformat()
    if is_postgresql(conn):
        return _claim_postgresql(conn, owner, stamp, until)
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = conn.execute('''SELECT task_id FROM exchange_coordinator_tasks
            WHERE state='PENDING' OR (state='RUNNING' AND lease_until_utc<=?)
            ORDER BY priority, scheduled_for, attempt, task_id LIMIT 1''', (stamp,)).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute('''UPDATE exchange_coordinator_tasks SET state='RUNNING',
            owner=?, generation=generation+1, lease_until_utc=?, error=''
            WHERE task_id=?''', (owner, until, row['task_id']))
        result = dict(conn.execute('SELECT * FROM exchange_coordinator_tasks WHERE task_id=?', (row['task_id'],)).fetchone())
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def finish(conn, task, *, now, result=None, error=None):
    stamp = _instant(now).isoformat()
    payload = json.dumps(result, sort_keys=True) if result is not None else None
    if is_postgresql(conn):
        with transaction(conn):
            cursor = conn.execute("""UPDATE exchange_coordinator_tasks
                SET state=%s, owner=NULL, lease_until_utc=NULL,
                    finished_at_utc=%s, result_json=%s, error=%s
                WHERE task_id=%s AND state='RUNNING' AND owner=%s AND generation=%s""",
                ('FAILED' if error is not None else 'DONE', stamp, payload, str(error or ''),
                 task['task_id'], task['owner'], task['generation']))
            if cursor.rowcount != 1:
                raise TaskOwnershipLost('stale exchange task cannot complete another generation')
        return
    with conn:
        cursor = conn.execute('''UPDATE exchange_coordinator_tasks
            SET state=?, owner=NULL, lease_until_utc=NULL, finished_at_utc=?, result_json=?, error=?
            WHERE task_id=? AND state='RUNNING' AND owner=? AND generation=?''',
            ('FAILED' if error is not None else 'DONE', stamp, payload, str(error or ''),
             task['task_id'], task['owner'], task['generation']))
        if cursor.rowcount != 1:
            raise TaskOwnershipLost('stale exchange task cannot complete another generation')


def pending_count(conn):
    if is_postgresql(conn):
        return conn.execute("SELECT COUNT(*) AS pending_count FROM exchange_coordinator_tasks WHERE state IN ('PENDING','RUNNING')").fetchone()['pending_count']
    return conn.execute("SELECT COUNT(*) FROM exchange_coordinator_tasks WHERE state IN ('PENDING','RUNNING')").fetchone()[0]


# Native transactions remain inside the caller's managed transaction. A savepoint
# makes a failed operation atomic without committing or closing its connection.
def _enqueue_postgresql(conn, kind, scheduled_for, attempt, priority, stamp):
    with transaction(conn):
        identity = json.dumps([kind, scheduled_for, attempt], separators=(',', ':'))
        conn.execute("""SELECT pg_advisory_xact_lock(hashtextextended(
            current_schema() || ':exchange-task:' || %s, 0))""", (identity,))
        return dict(conn.execute("""INSERT INTO exchange_coordinator_tasks
            (kind, scheduled_for, attempt, priority, created_at_utc)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT(kind, scheduled_for, attempt) DO UPDATE SET
                priority=LEAST(exchange_coordinator_tasks.priority, excluded.priority)
            RETURNING *""", (kind, scheduled_for, attempt, priority, stamp)).fetchone())


def _claim_postgresql(conn, owner, stamp, until):
    with transaction(conn):
        row = conn.execute("""SELECT task_id FROM exchange_coordinator_tasks
            WHERE state='PENDING' OR (state='RUNNING' AND lease_until_utc::timestamptz<=%s::timestamptz)
            ORDER BY priority, scheduled_for, attempt, task_id
            LIMIT 1 FOR UPDATE SKIP LOCKED""", (stamp,)).fetchone()
        if row is None:
            return None
        return dict(conn.execute("""UPDATE exchange_coordinator_tasks
            SET state='RUNNING', owner=%s, generation=generation+1,
                lease_until_utc=%s, error=''
            WHERE task_id=%s RETURNING *""", (owner, until, row['task_id'])).fetchone())
