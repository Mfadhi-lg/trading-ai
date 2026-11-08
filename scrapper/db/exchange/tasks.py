"""Durable bounded exchange dispatch; legacy attempt ledgers remain authoritative."""
from datetime import date, datetime, timedelta, timezone
import json


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
    with conn:
        cursor = conn.execute('''UPDATE exchange_coordinator_tasks
            SET state=?, owner=NULL, lease_until_utc=NULL, finished_at_utc=?, result_json=?, error=?
            WHERE task_id=? AND state='RUNNING' AND owner=? AND generation=?''',
            ('FAILED' if error is not None else 'DONE', stamp, payload, str(error or ''),
             task['task_id'], task['owner'], task['generation']))
        if cursor.rowcount != 1:
            raise TaskOwnershipLost('stale exchange task cannot complete another generation')


def pending_count(conn):
    return conn.execute("SELECT COUNT(*) FROM exchange_coordinator_tasks WHERE state IN ('PENDING','RUNNING')").fetchone()[0]
