#!/usr/bin/env python3
"""AR-01.1 offline proof. Never opens production; writes only fresh temp clones/evidence.

Run from repository root: python3 docs/acquisition-readiness/baselines/AR-01.1/rehearse.py
Retains clones for inspection. A second invocation creates new disposable paths.
"""
import hashlib
import json
import pickle
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
SOURCE = Path('/private/tmp/trading-ai-audit-2026-10-07/scraper.db')
EXPECTED = 'f237daa6f4696f75e2081e93d69a57be6f8d9c097769aaa169a819789c9e4811'
sys.path.insert(0, str(ROOT))
from scrapper.db import database


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def log(message):
    line = f'{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())} {message}'
    print(line, flush=True)
    with (OUT / 'rehearsal.log').open('a') as stream:
        stream.write(line + '\n')


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def ro(path):
    conn = sqlite3.connect(f'file:{path}?mode=ro&immutable=1', uri=True)
    conn.execute('PRAGMA query_only=ON')
    conn.row_factory = sqlite3.Row
    return conn


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def schema(conn):
    return [dict(row) for row in conn.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]


def tables(conn):
    return {row[0]: [col[1] for col in conn.execute(f'PRAGMA table_info({quoted(row[0])})')]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}


def fingerprint(conn, projection):
    result = {}
    for name, columns in projection.items():
        # Rowid order is stable across these additive migrations. WITHOUT ROWID
        # tables use their declared primary-key order instead.
        ddl = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
        order = 'rowid'
        if 'WITHOUT ROWID' in (ddl or '').upper():
            info = list(conn.execute(f'PRAGMA table_info({quoted(name)})'))
            order = ','.join(quoted(col[1]) for col in sorted(info, key=lambda c: c[5]) if col[5])
        digest, count = hashlib.sha256(), 0
        for row in conn.execute(f'SELECT {",".join(map(quoted, columns))} FROM {quoted(name)} ORDER BY {order}'):
            data = pickle.dumps(tuple(row), protocol=4)
            digest.update(len(data).to_bytes(8, 'big'))
            digest.update(data)
            count += 1
        result[name] = {'columns': columns, 'rows': count, 'sha256': digest.hexdigest()}
        log(f'fingerprint {name}: {count} rows')
    return result


def main():
    log('START clone-only rehearsal; no production access')
    status = subprocess.check_output(['git', 'status', '--porcelain=v1', '-uall'], cwd=ROOT, text=True)
    changed = []
    for line in status.splitlines():
        path = line[3:]
        file = ROOT / path
        changed.append({'status': line[:2], 'path': path, 'sha256': sha(file) if file.is_file() else None})
    save('working-tree-before.json', {
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=ROOT, text=True).strip(),
        'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
        'changed_paths': changed, 'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version})
    index = json.loads((ROOT / 'docs/acquisition-readiness/baselines/AR-00/evidence-index.json').read_text())
    for item in index['artifacts']:
        assert sha(ROOT / item['path']) == item['sha256'], item['path']
    log('AR-00 six evidence hashes verified')
    sidecars = {suffix: {'exists': Path(str(SOURCE) + suffix).exists(),
                        'bytes': Path(str(SOURCE) + suffix).stat().st_size if Path(str(SOURCE) + suffix).exists() else None}
                for suffix in ('-wal', '-shm')}
    assert not sidecars['-wal']['bytes'], 'nonempty WAL: stop rather than ignore recovery state'
    assert sha(SOURCE) == EXPECTED, 'retained main file differs from audit'
    log('retained baseline hash matches audit; WAL empty')
    target = Path(tempfile.mkdtemp(prefix='trading-ai-ar011-', dir='/private/tmp'))
    clone = target / 'rehearsal.db'
    shutil.copyfile(SOURCE, clone)
    assert sha(clone) == EXPECTED
    save('clone-identity.json', {'source': str(SOURCE), 'source_sha256': EXPECTED,
         'source_bytes': SOURCE.stat().st_size, 'source_sidecars': sidecars,
         'clone': str(clone), 'clone_before_sha256': EXPECTED, 'production_access': 'none'})
    log(f'fresh verified clone: {clone}')
    source = ro(SOURCE)
    before_schema = schema(source)
    save('schema-before.json', before_schema)
    projection = tables(source)
    before_ledger = [list(row) for row in source.execute('SELECT * FROM schema_migrations ORDER BY version')]
    assert before_ledger == sorted(index['retained_database']['migration_rows'])
    available = sorted(database.MIGRATIONS_PATH.glob('*.sql'))
    applied = {row[0] for row in before_ledger}
    pending = [file.name for file in available if file.name not in applied]
    assert [int(name[:3]) for name in pending] == list(range(69, 76)), pending
    assert applied - {p.name for p in available} == database.HISTORICAL_MIGRATION_IDENTITIES
    manifest = {'canonical': [{'filename': p.name, 'sha256': sha(p)} for p in available],
                'historical_allowlist': sorted(database.HISTORICAL_MIGRATION_IDENTITIES),
                'applied_before': before_ledger, 'pending': pending}
    save('migration-checksums.json', manifest)
    try:
        database.check_schema_compatibility(source)
        raise AssertionError('baseline unexpectedly compatible')
    except RuntimeError as exc:
        assert 'unknown applied' not in str(exc)
        manifest['before_compatibility_error'] = str(exc)
    before = fingerprint(source, projection)
    save('rows-before.json', before)
    source.close()
    conn = sqlite3.connect(clone)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    assert conn.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    log('clone quick/integrity checks before migration')
    checks = {'before_quick': [row[0] for row in conn.execute('PRAGMA quick_check')],
              'before_integrity': [row[0] for row in conn.execute('PRAGMA integrity_check')]}
    assert checks['before_quick'] == checks['before_integrity'] == ['ok']
    fk_before = [list(row) for row in conn.execute('PRAGMA foreign_key_check')]
    save('foreign-keys-before.json', fk_before)
    log('apply actual application migration runner to clone only')
    database.apply_migrations(conn)
    database.check_schema_compatibility(conn)
    after_ledger = [list(row) for row in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]
    assert [row for row in after_ledger if row[0] in applied] == before_ledger
    assert {row[0] for row in after_ledger} - applied == set(pending)
    manifest['applied_after'] = after_ledger
    save('migration-checksums.json', manifest)
    save('schema-after.json', schema(conn))
    after = fingerprint(conn, projection)
    save('rows-after.json', after)
    assert {k: v for k, v in before.items() if k != 'schema_migrations'} == {
        k: v for k, v in after.items() if k != 'schema_migrations'}, 'existing row changes'
    new_tables = set(tables(conn)) - set(projection)
    new_counts = {t: conn.execute(f'SELECT COUNT(*) FROM {quoted(t)}').fetchone()[0] for t in new_tables}
    assert all(n == 0 for n in new_counts.values()), new_counts
    for table in ('artifact_download_queue', 'credit_rating_download_queue', 'browser_rating_recovery_jobs'):
        assert conn.execute(f'SELECT COUNT(*) FROM {table} WHERE claim_token IS NOT NULL').fetchone()[0] == 0
    # Controlled trigger proof only: synthetic metadata, no document bytes or
    # application acceptance claimed. Entire fixture is rolled back.
    conn.execute('BEGIN')
    row = conn.execute('SELECT * FROM artifact_contents ORDER BY content_id LIMIT 1').fetchone()
    assert conn.execute('SELECT COUNT(*) FROM artifact_contents WHERE content_id=-100001').fetchone()[0] == 0
    # Reuse an existing artifact with a blob not yet attached to it.
    pair = conn.execute('SELECT a.artifact_id, b.blob_id FROM document_artifacts a CROSS JOIN response_blobs b '
        'WHERE NOT EXISTS (SELECT 1 FROM artifact_contents c WHERE c.artifact_id=a.artifact_id AND c.blob_id=b.blob_id) LIMIT 1').fetchone()
    cols, values = list(row.keys()), list(row)
    values[cols.index('content_id')] = -100001
    values[cols.index('artifact_id')] = pair[0]
    values[cols.index('blob_id')] = pair[1]
    conn.execute(f'INSERT INTO artifact_contents ({",".join(map(quoted, cols))}) VALUES ({",".join("?" for _ in cols)})', values)
    inbox = conn.execute('SELECT * FROM document_processing_inbox WHERE content_id=-100001').fetchone()
    assert list(inbox) == [-100001, row['created_at_utc']]
    conn.rollback()
    assert conn.execute('SELECT COUNT(*) FROM document_processing_inbox').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM artifact_contents WHERE content_id=-100001').fetchone()[0] == 0
    log('C28 inbox/work empty; new INSERT trigger proven with rolled-back fixture')
    checks['after_quick'] = [row[0] for row in conn.execute('PRAGMA quick_check')]
    checks['after_integrity'] = [row[0] for row in conn.execute('PRAGMA integrity_check')]
    assert checks['after_quick'] == checks['after_integrity'] == ['ok']
    fk_after = [list(row) for row in conn.execute('PRAGMA foreign_key_check')]
    save('foreign-keys-after.json', fk_after)
    assert fk_after == fk_before, 'new foreign-key violations'
    # Second explicit apply must be inert, including original ledger timestamps.
    database.apply_migrations(conn)
    assert [list(row) for row in conn.execute('SELECT * FROM schema_migrations ORDER BY version')] == after_ledger
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    conn.close()
    migrated_hash = sha(clone)
    log('rollback proof: recreate separate disposable state from verified baseline')
    restored = target / 'restored-068.db'
    shutil.copyfile(SOURCE, restored)
    assert sha(restored) == EXPECTED
    check = ro(restored)
    assert schema(check) == before_schema
    assert [list(row) for row in check.execute('SELECT * FROM schema_migrations ORDER BY version')] == before_ledger
    check.close()
    assert sha(SOURCE) == EXPECTED
    save('verification.json', {'checks': checks, 'new_table_counts': new_counts,
         'unchanged_table_count': len(before) - 1, 'old_ledger_rows_preserved': len(before_ledger),
         'new_ledger_rows': len(pending), 'foreign_key_violations_before': len(fk_before),
         'foreign_key_violations_after': len(fk_after), 'trigger_fixture': 'PASS; rolled back',
         'second_apply': 'PASS; ledger unchanged', 'clone_after_sha256': migrated_hash,
         'rollback_path': str(restored), 'rollback_sha256': EXPECTED,
         'rollback_schema_and_ledger': 'exact baseline', 'source_after_sha256': EXPECTED,
         'row_digest_format': 'length-prefixed pickle protocol 4 tuples in rowid/primary-key order',
         'claim_type': 'CLONE_REHEARSAL', 'production_activation': 'none'})
    log('PASS: rehearsal and disposable baseline recreation; source unchanged')


if __name__ == '__main__':
    main()
