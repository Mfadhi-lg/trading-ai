"""Durable admission budgets. Caller holds the single collection executor lock."""
from ..repository_backend import (
    atomic_repository, begin_immediate, commit, execute, executemany, generated_id,
    is_postgresql, rollback, statement, transaction,
)
import json
from ..collection_scope_repo import after


@atomic_repository('financial-repositories')
def enqueue(conn, *, now, budget=40):
    if type(budget) is not int or not 1 <= budget <= 100:
        raise ValueError('collection budget must be 1–100')
    after(now)  # require aware clock
    begin_immediate(conn, lock='financial-repositories')
    try:
        row=execute(conn, "SELECT * FROM collection_scope_tasks WHERE state='PENDING'").fetchone()
        if row is None:
            last=execute(conn, 'SELECT finished_at_utc FROM collection_scope_tasks ORDER BY task_id DESC LIMIT 1').fetchone()
            if last is not None and after(now) < after(last[0],seconds=3600):
                commit(conn)
                return None
            execute(conn, 'INSERT INTO collection_scope_tasks(created_at_utc,budget) VALUES (?,?)',(after(now),budget))
            row=execute(conn, 'SELECT * FROM collection_scope_tasks ORDER BY task_id DESC LIMIT 1').fetchone()
        commit(conn)
        return dict(row)
    except BaseException:
        rollback(conn)
        raise


@atomic_repository('financial-repositories')
def admit(conn, task_id):
    with transaction(conn):
        return execute(conn, "UPDATE collection_scope_tasks SET admitted=admitted+1 WHERE task_id=? AND state='PENDING' AND admitted<budget",(task_id,)).rowcount==1


@atomic_repository('financial-repositories')
def finish(conn, task_id, *, now, result=None, error=None):
    with transaction(conn):
        changed=execute(conn, "UPDATE collection_scope_tasks SET state=?,finished_at_utc=?,result_json=?,error=? WHERE task_id=? AND state='PENDING'",
            ('FAILED' if error is not None else 'DONE',after(now),json.dumps(result,sort_keys=True),str(error or ''),task_id)).rowcount
        if changed!=1:
            raise RuntimeError('collection task already settled')
