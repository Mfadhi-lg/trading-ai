"""Small durable periodic inbox; source/task retries are distinct from admissions."""
from datetime import timedelta
import json
from .exchange.tasks import _instant

KINDS = {'delayed': (50, 300), 'recovery': (50, 3), 'housekeeping': (40, 300)}

def enqueue(conn, *, kind, now):
    if kind not in KINDS:
        raise ValueError('unknown periodic task')
    stamp = _instant(now).isoformat()
    conn.execute('BEGIN IMMEDIATE')
    try:
        pending = conn.execute("SELECT * FROM periodic_dispatch_tasks WHERE kind=? AND state='PENDING'", (kind,)).fetchone()
        latest = conn.execute('SELECT * FROM periodic_dispatch_tasks WHERE kind=? ORDER BY task_id DESC LIMIT 1', (kind,)).fetchone()
        if pending is not None or (latest is not None and latest['next_due_at_utc'] > stamp):
            conn.commit()
            return dict(pending) if pending is not None else None
        conn.execute('INSERT INTO periodic_dispatch_tasks(kind,created_at_utc,budget,result_cursor,artifact_cursor) VALUES (?,?,?,?,?)',
            (kind,stamp,KINDS[kind][0],latest['result_cursor'] if latest else 0,latest['artifact_cursor'] if latest else 0))
        row = dict(conn.execute('SELECT * FROM periodic_dispatch_tasks WHERE task_id=last_insert_rowid()').fetchone())
        conn.commit()
        return row
    except BaseException:
        conn.rollback()
        raise

def admit(conn, task_id):
    with conn:
        return conn.execute("UPDATE periodic_dispatch_tasks SET admitted=admitted+1 WHERE task_id=? AND state='PENDING' AND admitted<budget", (task_id,)).rowcount == 1

def cursors(conn, task_id, result_cursor, artifact_cursor):
    with conn:
        conn.execute("UPDATE periodic_dispatch_tasks SET result_cursor=?,artifact_cursor=? WHERE task_id=? AND state='PENDING'", (result_cursor,artifact_cursor,task_id))

def finish(conn, task, *, now, result=None, error=None):
    # Recovery preserves the old work/sleep3 vs idle/sleep120 cadence.
    delay = 120 if task['kind']=='recovery' and result and result.get('idle') else KINDS[task['kind']][1]
    if error is not None:
        delay = max(delay,120)
    stamp = _instant(now)
    with conn:
        conn.execute("UPDATE periodic_dispatch_tasks SET state=?,finished_at_utc=?,next_due_at_utc=?,result_json=?,error=? WHERE task_id=? AND state='PENDING'",
            ('FAILED' if error is not None else 'DONE',stamp.isoformat(),(stamp+timedelta(seconds=delay)).isoformat(),json.dumps(result,sort_keys=True) if result is not None else None,str(error or ''),task['task_id']))

def housekeeping_rows(conn, task):
    accepted = conn.execute("""SELECT queue.queue_item_id,accepted.company_id,filing.*
        FROM screener_company_queue queue JOIN exchange_result_filings filing ON filing.filing_id=queue.exchange_filing_id
        JOIN accepted_page_versions accepted ON accepted.parse_id=queue.accepted_parse_id AND accepted.source='screener'
        WHERE queue.origin='exchange_filing' AND queue.status='accepted' AND queue.accepted_parse_id IS NOT NULL AND queue.queue_item_id>?
        ORDER BY queue.queue_item_id LIMIT 1""", (task['result_cursor'],)).fetchone()
    artifact = conn.execute("SELECT artifact_id FROM document_artifacts WHERE artifact_type='credit_rating' AND artifact_id>? ORDER BY artifact_id LIMIT 1", (task['artifact_cursor'],)).fetchone()
    return accepted, artifact
