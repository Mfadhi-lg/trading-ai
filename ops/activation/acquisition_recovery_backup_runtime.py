"""One new recovery copy; no checkpoint, schema application, or source writes."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot
from production_ownership import assert_no_conflicting_leases
from scrapper.db.database import check_schema_compatibility


def backup():
    started=time.monotonic()
    db=Path('/data/scraper.db')
    guard=assert_safe_empty_wal(db)
    baseline=json.loads(Path('/activation/production-baseline.json').read_text())
    if db.stat().st_ino!=baseline['db_after']['inode']:
        raise RuntimeError('Production DB identity mismatch')
    stats=os.statvfs('/recovery-backups')
    free=stats.f_bavail*stats.f_frsize
    if free<db.stat().st_size*2+1024**3:
        raise RuntimeError('Insufficient headroom for independent backup and database growth')
    target=Path('/recovery-backups')/datetime.now(timezone.utc).strftime('acquisition-recovery-20261026-%Y%m%dT%H%M%SZ.db')
    with closing(sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True)) as source:
        source.row_factory=sqlite3.Row
        check_schema_compatibility(source)
        assert_no_conflicting_leases(source,now=datetime.now(timezone.utc).isoformat())
        if [dict(r) for r in source.execute('SELECT * FROM schema_migrations ORDER BY version')]!=baseline['ledger']:
            raise RuntimeError('Migration ledger differs')
        if [dict(r) for r in source.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]!=baseline['schema']:
            raise RuntimeError('Production schema differs')
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with closing(sqlite3.connect(target)) as dest:
            source.backup(dest,pages=4096)
            checked=dest.execute('PRAGMA quick_check').fetchall()
            if checked!=[('ok',)]:raise RuntimeError('Recovery copy quick_check failed')
    with target.open('rb') as stream:os.fsync(stream.fileno())
    fd=os.open(target.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    if snapshot(db)!=guard['files']:raise RuntimeError('Source changed during backup')
    return {'status':'PASS','source':str(db),'source_guard':guard,'backup':str(target),
        'bytes':target.stat().st_size,'quick_check':'ok','source_unchanged':True,
        'free_before_bytes':free,'elapsed_seconds':time.monotonic()-started}


if __name__=='__main__':print(json.dumps(backup()))
