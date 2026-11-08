"""Cold, immutable census inside a networkless container with a read-only volume."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, '/activation')
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot
from production_ownership import assert_no_conflicting_leases


def census(db, include_summary=False, *, live=False):
    before = None if live else assert_safe_empty_wal(db)
    conn = sqlite3.connect('file:' + str(db) + '?mode=ro' + ('' if live else '&immutable=1'), uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    # Large grouped censuses must not attempt temporary files on the read-only
    # container root. This changes connection state only, never the database.
    conn.execute('PRAGMA temp_store=MEMORY')
    if live:conn.execute('BEGIN')
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        counts = {}
        for name in tables:
            if not any(word in name for word in ('queue', 'inbox', 'filing', 'processing_work')):
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            cols = [r['name'] for r in conn.execute('PRAGMA table_info(' + quoted + ')')]
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
    if not live and snapshot(db) != before['files']:
        raise RuntimeError('Database changed during immutable inspection; discard census')
    return result


if __name__ == '__main__':
    print(json.dumps(census(Path('/data/scraper.db'), include_summary='--summary' in sys.argv, live='--live' in sys.argv)))
