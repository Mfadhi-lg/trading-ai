"""Fenced Screener renew/defer operations; no fabricated acquisition result."""
from datetime import datetime, timezone, timedelta
import json
from .host_permits import transaction
from .document_work_claims import OwnershipLost

TABLES = {
    'company': ('screener_company_queue', 'queue_item_id', 'claim_expires_at_utc'),
    'expectation': ('artifact_expectations', 'expectation_id', 'claim_expires_at_utc'),
    'scope': ('screener_collection_scope', 'company_path', 'claim_expires_at_utc'),
}

def epoch(raw):
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError('Screener claim clock must include timezone')
    return dt.timestamp()

def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()

def renew(conn, kind, key, token, *, now, seconds=600):
    table, column, lease = TABLES[kind]
    with transaction(conn):
        cursor = conn.execute(f"UPDATE {table} SET {lease}=max({lease},?) WHERE {column}=? AND claim_token=? AND {lease}>?",
            (stamp(epoch(now)+seconds), key, token, now))
        if cursor.rowcount != 1:
            raise OwnershipLost('Screener claim expired or replaced')

def defer(conn, kind, key, token, *, now, retry_at, dispatched, reason):
    table, column, lease = TABLES[kind]
    deadline = stamp(max(epoch(now)+1, retry_at or epoch(now)+3600))
    with transaction(conn):
        row = conn.execute(f'SELECT * FROM {table} WHERE {column}=? AND claim_token=? AND {lease}>?', (key,token,now)).fetchone()
        if row is None:
            raise OwnershipLost('Cannot defer an expired or replaced Screener claim')
        common = 'claim_token=NULL,claim_expires_at_utc=NULL'
        if kind == 'company':
            conn.execute(f"UPDATE {table} SET {common},claimed_at_utc=NULL,status='failed_retryable',next_attempt_at_utc=?,attempt_count=attempt_count+?,updated_at_utc=? WHERE {column}=? AND claim_token=?", (deadline,int(bool(dispatched)),now,key,token))
            conn.execute("INSERT INTO screener_company_queue_events(queue_item_id,event_at_utc,from_status,to_status,evidence_json) VALUES(?,?,?,'failed_retryable',?)", (key,now,row['status'],json.dumps({'reason':reason,'administrative_defer':True,'dispatched':bool(dispatched),'retry_at':deadline})))
        elif kind == 'expectation':
            conn.execute(f'UPDATE {table} SET {common},claimed_at_utc=NULL,next_check_at_utc=?,attempt_count=attempt_count+?,updated_at_utc=? WHERE {column}=? AND claim_token=?', (deadline,int(bool(dispatched)),now,key,token))
        else:
            conn.execute(f'UPDATE {table} SET {common},next_check_at_utc=?,check_failures=check_failures+? WHERE {column}=? AND claim_token=?', (deadline,int(bool(dispatched)),key,token))


def owned(conn, kind, key, token, *, now):
    table, column, lease = TABLES[kind]
    if conn.execute(f'SELECT 1 FROM {table} WHERE {column}=? AND claim_token=? AND {lease}>?', (key,token,now)).fetchone() is None:
        raise OwnershipLost('Screener claim expired or replaced before persistence')
