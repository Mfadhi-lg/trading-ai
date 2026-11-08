"""Durable admission budgets. Caller holds the single collection executor lock."""
import json
from ..collection_scope_repo import after


def enqueue(conn, *, now, budget=40):
    if type(budget) is not int or not 1 <= budget <= 100:
        raise ValueError('collection budget must be 1–100')
    after(now)  # require aware clock
    conn.execute('BEGIN IMMEDIATE')
    try:
        row=conn.execute("SELECT * FROM collection_scope_tasks WHERE state='PENDING'").fetchone()
        if row is None:
            last=conn.execute('SELECT finished_at_utc FROM collection_scope_tasks ORDER BY task_id DESC LIMIT 1').fetchone()
            if last is not None and after(now) < after(last[0],seconds=3600):
                conn.commit()
                return None
            conn.execute('INSERT INTO collection_scope_tasks(created_at_utc,budget) VALUES (?,?)',(after(now),budget))
            row=conn.execute('SELECT * FROM collection_scope_tasks ORDER BY task_id DESC LIMIT 1').fetchone()
        conn.commit()
        return dict(row)
    except BaseException:
        conn.rollback()
        raise


def admit(conn, task_id):
    with conn:
        return conn.execute("UPDATE collection_scope_tasks SET admitted=admitted+1 WHERE task_id=? AND state='PENDING' AND admitted<budget",(task_id,)).rowcount==1


def finish(conn, task_id, *, now, result=None, error=None):
    with conn:
        changed=conn.execute("UPDATE collection_scope_tasks SET state=?,finished_at_utc=?,result_json=?,error=? WHERE task_id=? AND state='PENDING'",
            ('FAILED' if error is not None else 'DONE',after(now),json.dumps(result,sort_keys=True),str(error or ''),task_id)).rowcount
        if changed!=1:
            raise RuntimeError('collection task already settled')
