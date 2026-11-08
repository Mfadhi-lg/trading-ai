"""Read-only comparison of full scan and enforced write-boundary admission."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time
sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot
from phase4_documents_runtime import fingerprint
from phase4_routine_runtime import routine_authorizer


def benchmark():
    db=Path('/data/scraper.db');before=assert_safe_empty_wal(db)
    result={'source_requests':0,'production_writes':0,'scope':'preservation guard only; same immutable production snapshot'}
    with closing(sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True)) as c:
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON');c.execute('PRAGMA temp_store=MEMORY')
        start=time.monotonic()
        result['full_scan_fingerprints']={t:fingerprint(c,t) for t in ('accepted_page_versions','financial_observations','exchange_source_access_events')}
        result['one_full_scan_seconds']=time.monotonic()-start
        start=time.monotonic();c.set_authorizer(routine_authorizer)
        # The authorizer must reject the statement before any row can change.
        try:c.execute('UPDATE financial_observations SET parse_id=parse_id WHERE 0')
        except sqlite3.DatabaseError as exc:
            if 'not authorized' not in str(exc):raise
        else:raise RuntimeError('Protected financial write was not denied')
        result['write_fence_install_and_reject_seconds']=time.monotonic()-start
    if snapshot(db)!=before['files']:raise RuntimeError('Production DB changed during benchmark')
    return result


if __name__=='__main__':print(json.dumps(benchmark()))
