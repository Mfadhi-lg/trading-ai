"""Complete validation of a disposable snapshot/copy; no application writes."""
import hashlib
import json
import pickle
import sqlite3
import sys
import time
from pathlib import Path


def emit(kind, **values):
    print(json.dumps({'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                      'stage': kind, **values}), flush=True)


def save(name, value):
    (Path('/evidence') / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    db = Path('/copy/scraper.db')
    expected = sys.argv[1]
    if any(Path(str(db) + s).exists() for s in ('-wal', '-shm', '-journal')):
        raise RuntimeError('Disposable standalone copy unexpectedly has sidecars')
    before_hash = sha(db)
    if before_hash != expected:
        raise RuntimeError('Disposable input hash mismatch')
    conn = sqlite3.connect('file:/copy/scraper.db?mode=ro&immutable=1', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    emit('quick_check_start')
    quick = [r[0] for r in conn.execute('PRAGMA quick_check')]
    emit('quick_check_end', result=quick)
    if quick != ['ok']:
        raise RuntimeError('quick_check failed')
    emit('integrity_check_start')
    integrity = [r[0] for r in conn.execute('PRAGMA integrity_check')]
    emit('integrity_check_end', result=integrity)
    if integrity != ['ok']:
        raise RuntimeError('integrity_check failed')
    schema = [dict(r) for r in conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
    ledger = [dict(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]
    save('schema.json', schema)
    save('ledger.json', ledger)
    if len(ledger) != 69 or max(int(r['version'].split('_')[0]) for r in ledger) != 68:
        raise RuntimeError('Copy no longer has exact baseline 068 ledger')
    emit('foreign_key_check_start')
    fk = [list(r) for r in conn.execute('PRAGMA foreign_key_check')]
    # Canonical string ordering avoids NULL/int ordering ambiguity.
    fk.sort(key=lambda r: json.dumps(r, separators=(',', ':')))
    save('foreign-keys.json', fk)
    emit('foreign_key_check_end', violations=len(fk))
    rows = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        columns = [r[1] for r in conn.execute('PRAGMA table_info(' + quoted(name) + ')')]
        ddl = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
        order = 'rowid'
        if 'WITHOUT ROWID' in (ddl or '').upper():
            info = list(conn.execute('PRAGMA table_info(' + quoted(name) + ')'))
            order = ','.join(quoted(c[1]) for c in sorted(info, key=lambda c: c[5]) if c[5])
        h, count = hashlib.sha256(), 0
        for row in conn.execute('SELECT ' + ','.join(map(quoted, columns)) + ' FROM ' + quoted(name) + ' ORDER BY ' + order):
            data = pickle.dumps(tuple(row), protocol=4)
            h.update(len(data).to_bytes(8, 'big'))
            h.update(data)
            count += 1
        rows[name] = {'columns': columns, 'rows': count, 'sha256': h.hexdigest()}
        emit('fingerprint', table=name, rows=count)
        save('rows.json', rows)
    conn.close()
    after_hash = sha(db)
    if before_hash != after_hash:
        raise RuntimeError('Validation copy changed')
    result = {'status': 'PASS', 'db_sha256_before': before_hash, 'db_sha256_after': after_hash,
              'quick_check': quick, 'integrity_check': integrity, 'table_count': len(rows),
              'row_count': sum(r['rows'] for r in rows.values()), 'fk_count': len(fk),
              'fingerprint_format': 'length-prefixed pickle protocol 4 tuples; rowid/declared-PK order',
              'python': sys.version, 'sqlite': sqlite3.sqlite_version, 'production_mounts': []}
    save('validation.json', result)
    emit('PASS', **result)


if __name__ == '__main__':
    main()
