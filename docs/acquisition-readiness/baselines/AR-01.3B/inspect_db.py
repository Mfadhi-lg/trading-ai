"""Guarded production read-only precondition, post-state and 075 canary checks."""
import hashlib
import json
import os
import signal
import sqlite3
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, '/app')
from scrapper.db import database
from scrapper import config

DB = Path('/data/scraper.db')


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def state():
    s = DB.lstat()
    require(stat.S_ISREG(s.st_mode), 'Main DB is not a regular file')
    sidecars = {suffix: os.path.lexists(str(DB) + suffix) for suffix in ('-wal', '-shm', '-journal')}
    require(not any(sidecars.values()), 'Unexpected WAL/SHM/journal; no immutable read or recovery allowed')
    return {'bytes': s.st_size, 'mtime_ns': s.st_mtime_ns, 'ctime_ns': s.st_ctime_ns,
            'device': s.st_dev, 'inode': s.st_ino, 'sidecars': sidecars}


def schema(conn):
    return [dict(r) for r in conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]


def main():
    phase = sys.argv[1]
    gate = json.loads(Path('/proof/host-gate.json').read_text())
    require(gate['writer_gate_passed'] and 0 <= time.time() - gate['observed_at_epoch'] <= 10, 'Writer gate missing/stale')
    require(sha(Path(__file__)) == gate['harness_sha256'], 'Inspector bytes mismatch')
    mount_rows = [s.split(' - ', 1)[0].split() for s in Path('/proc/self/mountinfo').read_text().splitlines()]
    require(any(r[4] == '/data' and 'ro' in r[5].split(',') for r in mount_rows), 'Production volume must be read-only')
    before = state()
    manifest = json.loads(Path('/proof/migration-manifest.json').read_text())
    pending_expected = [r['filename'] for r in manifest['pending']]
    sql_hashes = {r['filename']: sha(database.MIGRATIONS_PATH / r['filename']) for r in manifest['pending']}
    require(sql_hashes == {r['filename']: r['sha256'] for r in manifest['pending']}, 'Pinned SQL hashes differ')
    expected_schema = json.loads(Path('/proof/schema-before.json' if phase == 'pre' else '/proof/schema-after.json').read_text())
    expected_ledger = json.loads(Path('/proof/ledger-before.json').read_text())
    if phase == 'pre':
        expected_identity = json.loads(Path('/proof/source-baseline.json').read_text())['source_before']
        require(before == expected_identity, 'Production DB identity differs from AR-01.3A')
        source_hash = sha(DB)
        require(source_hash == 'f237daa6f4696f75e2081e93d69a57be6f8d9c097769aaa169a819789c9e4811', 'Production SHA256 differs from authorized baseline')
        require(state() == before, 'Source identity changed during pre-write hash')
    conn = sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    require(conn.execute('PRAGMA query_only').fetchone()[0] == 1, 'Query-only not set')
    actual_schema = schema(conn)
    require(actual_schema == expected_schema, 'Unexpected complete DDL or partial migration objects')
    ledger = [dict(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]
    applied = {r['version'] for r in ledger}
    available = {p.name for p in database.MIGRATIONS_PATH.glob('*.sql')}
    unknown = sorted(applied - available - database.HISTORICAL_MIGRATION_IDENTITIES)
    pending = sorted(available - applied)
    require(not unknown, 'Unknown migration identities')
    result = {'phase': phase, 'db_before': before, 'ledger': ledger, 'schema': actual_schema,
              'sql_hashes': sql_hashes, 'pending': pending, 'unknown': unknown,
              'readonly_mount': True, 'query_only': True}
    if phase == 'pre':
        require(ledger == expected_ledger and pending == pending_expected, 'Expected exact 068 ledger and only 069–075 pending')
        # Require all reviewed init-db directories and bind targets to exist:
        # this prevents incidental directory/placeholder creation in production.
        names = ['STORE','FILES','SNAPSHOTS','EXPORTS','AMC_WORKBOOK_SNAPSHOTS','MF_WORKBOOK_EXPORTS',
                 'MF_FEATURE_EXPORTS','MF_COVERAGE_EXPORTS','MFDATA_SNAPSHOTS','MFDATA_EXPORTS',
                 'DOCUMENT_DISCOVERY_SNAPSHOTS','DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS',
                 'DOCUMENT_DOWNLOAD_EXPORTS','DOCUMENT_EXTRACTION_EXPORTS','DOCUMENT_CREDIT_RATING_EXPORTS',
                 'DOCUMENT_TRANSCRIPT_EXPORTS','DOCUMENT_RECORDING_EXPORTS','DOCUMENT_MACHINE_TRANSCRIPT_EXPORTS',
                 'DOCUMENT_QUALITY_EXPORTS','DOCUMENT_RECONCILIATION_EXPORTS','DOCUMENT_ARTIFACT_COVERAGE_EXPORTS',
                 'EXCHANGE_SCHEDULE_EXPORTS','MF_IMPORT_INBOX','MF_IMPORT_ACCEPTED','MF_IMPORT_REJECTED','MF_IMPORT_QUARANTINE']
        result['required_directories'] = {str(getattr(config, n)): Path(getattr(config, n)).is_dir() for n in names}
        require(all(result['required_directories'].values()), 'Reviewed init-db would create missing operational directories; stop for scope review')
        markers = {}
        for p in (Path('/data/.trading_ai_runtime_identity.json'), Path('/data/snapshots/.trading_ai_runtime_identity.json')):
            require(p.is_file(), 'Existing runtime sentinel bind target missing')
            markers[str(p)] = {'sha256': sha(p), 'bytes': p.stat().st_size, 'text': p.read_text()}
        result['original_markers'] = markers
        result['source_sha256'] = source_hash
        result['schema_latest'] = '068'
    else:
        require(len(ledger) == 76 and ledger[-1]['version'] == '075_periodic_dispatch.sql' and not pending, 'Expected exact 075 ledger')
        old_names = {r['version'] for r in expected_ledger}
        require([r for r in ledger if r['version'] in old_names] == expected_ledger, 'Original migration records changed')
        require(sorted(applied - old_names) == pending_expected, 'Unexpected new ledger identities')
        database.check_schema_compatibility(conn)
        result['schema_compatibility'] = 'PASS'
        new_tables = ['document_extraction_claims','acquisition_host_state','acquisition_host_permits',
                      'document_processing_inbox','document_processing_work','exchange_coordinator_tasks',
                      'collection_scope_tasks','periodic_dispatch_tasks']
        result['new_table_counts'] = {t: conn.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in new_tables}
        require(not any(result['new_table_counts'].values()), 'New operational table is not empty')
        result['nonnull_claim_tokens'] = {t: conn.execute('SELECT COUNT(*) FROM '+t+' WHERE claim_token IS NOT NULL').fetchone()[0]
            for t in ('artifact_download_queue','credit_rating_download_queue','browser_rating_recovery_jobs')}
        require(not any(result['nonnull_claim_tokens'].values()), 'Historical claim token is not NULL')
        result['schema_latest'] = '075'
        result['original_markers'] = {str(p): {'sha256': sha(p), 'bytes': p.stat().st_size, 'text': p.read_text()}
            for p in (Path('/data/.trading_ai_runtime_identity.json'), Path('/data/snapshots/.trading_ai_runtime_identity.json'))}
    conn.close()
    result['source_sha256'] = sha(DB)
    require(state() == before, 'DB identity/sidecars changed during read-only inspection')
    result['db_after'] = state()
    result['status'] = 'PASS'
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(RuntimeError('Read-only inspector deadline exceeded')))
    signal.alarm(30 if sys.argv[1] == 'canary' else 60)
    main()
