"""Cold, immutable census inside a networkless container with a read-only volume."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot
from production_ownership import assert_no_conflicting_leases


def census(db, include_summary=False, *, live=False):
    from postgresql_runtime import enabled, read_connection, table_names, table_columns
    native=enabled()
    before = None if live or native else assert_safe_empty_wal(db)
    conn = read_connection(db, immutable=not live)
    if not native:
        conn.execute('PRAGMA temp_store=MEMORY')
    if live and not native:conn.execute('BEGIN')
    try:
        tables = table_names(conn)
        counts = {}
        for name in tables:
            if not any(word in name for word in ('queue', 'inbox', 'filing', 'processing_work')):
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            cols = table_columns(conn,name)
            state = next((c for c in ('status', 'state') if c in cols), None)
            sql = ('SELECT "' + state + '" state,count(*) n FROM ' + quoted + ' GROUP BY 1'
                   if state else 'SELECT count(*) n FROM ' + quoted)
            try:
                counts[name] = [dict(r) for r in conn.execute(sql)]
            except sqlite3.Error as exc:
                raise RuntimeError('Read-only census failed in ' + name + ': ' + str(exc)) from exc
        try:
            if live:
                leases = {'status':'NOT_CHECKED','reason':'Live monitoring snapshot does not establish shutdown safety'}
            else:
                leases = {'status': 'PASS', 'checked': assert_no_conflicting_leases(
                conn, now=datetime.now(timezone.utc).isoformat())}
        except RuntimeError as exc:
            leases = {'status': 'BLOCKED', 'error': str(exc)}
        result = {'queues': counts, 'leases': leases, 'database': str(db), 'guard': before}
        if include_summary:
            from acquisition_queries import query
            try:
                result['summary'] = query(conn, 'summary')
            except sqlite3.Error as exc:
                result['summary_error'] = str(exc)
    finally:
        conn.close()
    if not live and not native and snapshot(db) != before['files']:
        raise RuntimeError('Database changed during immutable inspection; discard census')
    return result


if __name__ == '__main__':
    print(json.dumps(census(Path('/data/scraper.db'), include_summary='--summary' in sys.argv, live='--live' in sys.argv)))
