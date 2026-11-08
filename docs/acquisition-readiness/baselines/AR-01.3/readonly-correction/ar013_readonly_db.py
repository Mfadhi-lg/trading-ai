"""One-shot AR-01.3 diagnostic; never a normal application connection path."""
import hashlib
import json
import os
import signal
import sqlite3
import stat
import sys
import time
from pathlib import Path

EXPECTED_SOURCE = 'c3ddbbb9c298d07b8b46a362996251417282476a'
EXPECTED_PENDING = [
    '069_worker_claim_fencing.sql', '070_document_extraction_claims.sql',
    '071_shared_host_permits.sql', '072_document_processing_work.sql',
    '073_exchange_coordinator_tasks.sql', '074_collection_scope_tasks.sql',
    '075_periodic_dispatch.sql',
]
PROCESSING_TABLES = (
    'document_extraction_claims', 'document_processing_inbox',
    'document_processing_work',
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def identity(db):
    s = db.lstat()
    require(stat.S_ISREG(s.st_mode), 'DB must be an existing regular, non-symlink file')
    with db.open('rb') as f:
        header = f.read(100)
    require(header[:16] == b'SQLite format 3\x00', 'Invalid SQLite main-file header')
    return {'path': str(db), 'device': s.st_dev, 'inode': s.st_ino,
            'bytes': s.st_size, 'mtime_ns': s.st_mtime_ns, 'ctime_ns': s.st_ctime_ns,
            'header_sha256': hashlib.sha256(header).hexdigest(),
            'sqlite_header_write_read_versions': list(header[18:20])}


def sidecar_gate(db):
    wal, shm = Path(str(db) + '-wal'), Path(str(db) + '-shm')
    # Even a zero-byte WAL fails the requested absence gate. Reject broken
    # symlinks too. A nonempty SHM is ambiguous: never parse/ignore it here.
    require(not os.path.lexists(wal), 'WAL exists: immutable read forbidden')
    shm_exists = os.path.lexists(shm)
    if shm_exists:
        require(stat.S_ISREG(shm.lstat().st_mode) and shm.stat().st_size == 0,
                'Nonempty/nonregular SHM: immutable read forbidden')
    return {'wal_exists': False, 'shm_exists': shm_exists,
            'shm_bytes': shm.stat().st_size if shm_exists else None}


def require_readonly_mount(path, mountinfo):
    matches = [line.split(' - ', 1)[0].split() for line in mountinfo.splitlines()
               if ' - ' in line]
    matches = [f for f in matches if f[4] == path]
    require(len(matches) == 1 and 'ro' in matches[0][5].split(','),
            'Required exact read-only mount missing: ' + path)
    return {'mountpoint': path, 'options': matches[0][5]}


def main():
    result = {'CANARY_HARNESS': 'FAIL', 'READ_ONLY_DB_ACCESS': 'FAIL',
              'production_mutation': 'none'}
    conn = None
    db = Path('/data/scraper.db')
    try:
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(RuntimeError('20-second probe deadline exceeded')))
        signal.alarm(20)
        gate = json.loads(Path('/proof/host-gate.json').read_text())
        require(gate['writer_gate_passed'] is True, 'Host writer gate not proven')
        require(0 <= time.time() - gate['observed_at_epoch'] <= 10,
                'Host writer inventory older than 10 seconds')
        require(gate['source_commit'] == EXPECTED_SOURCE, 'Source identity mismatch')
        require(hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == gate['harness_sha256'],
                'Harness bytes differ from host inventory')
        result['host_gate_age_seconds'] = time.time() - gate['observed_at_epoch']
        mounts = Path('/proc/self/mountinfo').read_text()
        result['readonly_mounts'] = [require_readonly_mount(p, mounts) for p in ('/data', '/proof', '/')]
        result['db_before'] = identity(db)
        result['sidecars_before'] = sidecar_gate(db)
        # The host gate and file/sidecar checks above are prerequisites, never
        # a global change to scrapper.db.database.connect.
        conn = sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1', uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        require(conn.execute('PRAGMA query_only').fetchone()[0] == 1, 'Query-only did not activate')
        ledger = [dict(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]
        result['READ_ONLY_DB_ACCESS'] = 'PASS'
        result['schema_ledger'] = ledger
        latest = max(int(r['version'].split('_', 1)[0]) for r in ledger)
        result['PRODUCTION_SCHEMA_IDENTIFIED'] = f'{latest:03d}'
        require(latest == 68, 'Unexpected production schema; expected 068')
        # Python starts with /proof for this script; import only the pinned
        # image's /app tree, never a host application-source overlay.
        require(Path('/app/scrapper/db/database.py').is_file(), 'Pinned image application source missing')
        sys.path.insert(0, '/app')
        from scrapper.db import database
        applied = {r['version'] for r in ledger}
        available = {p.name for p in database.MIGRATIONS_PATH.glob('*.sql')}
        pending = sorted(available - applied)
        unknown = sorted(applied - available - database.HISTORICAL_MIGRATION_IDENTITIES)
        result['pending_migrations'] = pending
        result['unknown_applied_migrations'] = unknown
        require(pending == EXPECTED_PENDING and not unknown, 'Unexpected schema delta')
        require(len(ledger) == 69, 'Unexpected baseline ledger count')
        result['EXPECTED_PENDING_MIGRATIONS'] = '069-075'
        result['processing_tables'] = {
            table: bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())
            for table in PROCESSING_TABLES}
        require(not any(result['processing_tables'].values()), 'Unexpected pending processing schema already present')
        # The exact pinned implementation joins one details item, so its
        # RuntimeError starts with "pending migrations:" (no preamble).
        # The independently computed pending/unknown sets above are the gate.
        expected = ('pending migrations: '
                    + ', '.join(EXPECTED_PENDING) + '. Run explicit init-db only after review.')
        try:
            database.check_schema_compatibility(conn)
        except RuntimeError as exc:
            result['application_compatibility_message'] = str(exc)
            require(str(exc) == expected, 'Unexpected application compatibility failure')
            result['C28_APPLICATION_SCHEMA_COMPATIBILITY'] = 'EXPECTED_BLOCKED_PENDING_MIGRATIONS'
        else:
            raise RuntimeError('Application unexpectedly accepted pending schema')
        conn.close()
        conn = None
        result['sidecars_after'] = sidecar_gate(db)
        result['db_after'] = identity(db)
        require(result['sidecars_after'] == result['sidecars_before'], 'Sidecar state changed during read')
        require(result['db_after'] == result['db_before'], 'DB identity/size/mtime changed during read')
        result['db_identity_unchanged'] = True
        result['CANARY_HARNESS'] = 'PASS'
        result['exit_classification'] = 'PASS_READ_ONLY_ACCESS_WITH_EXPECTED_SCHEMA_BLOCK'
        return 0
    except Exception as exc:
        result['error'] = str(exc)
        result['exit_classification'] = 'FAIL_CLOSED_SAFETY_OR_UNEXPECTED_CHECK'
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()
        if 'db_before' in result and 'db_after' not in result:
            try:
                result['sidecars_after'] = sidecar_gate(db)
                result['db_after'] = identity(db)
                result['db_identity_unchanged'] = result['db_before'] == result['db_after']
            except Exception as exc:
                result['post_read_safety_error'] = str(exc)
        signal.alarm(0)
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    sys.exit(main())
