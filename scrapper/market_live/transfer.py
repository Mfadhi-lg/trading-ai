"""Explicit bounded transfer of a quiesced live snapshot; never a startup action."""
from contextlib import closing, ExitStack, nullcontext
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from scrapper.db.postgresql_transfer import read_snapshot, file_sha256, _catalog, _catalog_tokens
from scrapper.db.postgresql_schema import validate_schema_name
from experiments.historical_candle_archive_v1.storage import dump
from .repository import contract, MARKER

LEGACY_DDL = '''CREATE TABLE frames(
    id INTEGER PRIMARY KEY, provider_ms INTEGER, received_at TEXT, sha256 TEXT, raw_path TEXT);
CREATE TABLE observations(
    frame_id INTEGER, instrument TEXT, start TEXT, provider_ms INTEGER,
    received_at TEXT, completed INTEGER, payload TEXT,
    PRIMARY KEY(frame_id,instrument,start));
CREATE INDEX observation_lookup ON observations(instrument,start,provider_ms);'''
TABLES = {'frames':'id','observations':'frame_id,instrument COLLATE "C",start COLLATE "C"'}


def batch(value):
    if type(value) is not int or not 1 <= value <= 100000:
        raise ValueError('Batch size must be 1..100000')


def rows_digest(cursor, size):
    digest, count = hashlib.sha256(), 0
    while rows := cursor.fetchmany(size):
        for row in rows:
            digest.update((json.dumps(list(row),ensure_ascii=True,separators=(',',':'))+'\n').encode())
        count += len(rows)
    return dict(rows=count,sha256=digest.hexdigest())


def inspect(source, root, size=1000):
    with closing(sqlite3.connect(':memory:')) as baseline:
        baseline.executescript(LEGACY_DDL)
        if _catalog_tokens(_catalog(source)) != _catalog_tokens(_catalog(baseline)):
            raise RuntimeError('Unreviewed live source schema')
    if source.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise RuntimeError('Live source integrity failure')
    if source.execute('SELECT 1 FROM observations o LEFT JOIN frames f ON f.id=o.frame_id WHERE f.id IS NULL OR o.instrument IS NULL OR o.start IS NULL OR o.completed NOT IN (0,1) OR o.completed IS NULL LIMIT 1').fetchone():
        raise RuntimeError('Invalid live observation identity')
    files = {}
    root = Path(root).resolve(strict=True)
    for ident, provider, received, checksum, name in source.execute('SELECT * FROM frames ORDER BY id'):
        path = root / (name or '')
        if not name or Path(name).is_absolute() or '..' in Path(name).parts or any(p.is_symlink() for p in [path,*path.parents]) or not path.is_file():
            raise RuntimeError('Unsafe/missing raw frame reference')
        actual = file_sha256(path)
        if actual != checksum:
            raise RuntimeError('Raw frame checksum mismatch')
        files[name] = dict(sha256=actual,bytes=path.stat().st_size)
    for payload, checksum in source.execute('SELECT o.payload,f.sha256 FROM observations o JOIN frames f ON f.id=o.frame_id'):
        if json.loads(payload).get('source_sha256') != checksum:
            raise RuntimeError('Observation raw provenance mismatch')
    # SQLite BINARY and PostgreSQL C use the same ordering for these UTF-8 keys.
    tables = {name:rows_digest(source.execute('SELECT * FROM '+name+' ORDER BY '+order.replace('"C"','BINARY')),size)
              for name,order in TABLES.items()}
    return dict(tables=tables,files=files)


def export_snapshot(source, target, root, *, batch_size=1000):
    batch(batch_size)
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    with read_snapshot(source) as old, closing(sqlite3.connect(target)) as copy:
        old.backup(copy,pages=256)
        copy.execute('PRAGMA journal_mode=DELETE')
    with read_snapshot(target) as copy:
        return dict(inspect(copy,root,batch_size),snapshot_sha256=file_sha256(target))


def transfer(settings, snapshot, schema, root, *, expected_sha256, target, batch_size=1000, progress=None, _destination=None):
    batch(batch_size)
    validate_schema_name(schema)
    if not expected_sha256 or file_sha256(snapshot) != expected_sha256:
        raise RuntimeError('Unauthorized snapshot checksum')
    with read_snapshot(snapshot) as source:
        if source.execute('PRAGMA journal_mode').fetchone()[0] != 'delete' or any(Path(str(snapshot)+s).exists() for s in ('-wal','-shm','-journal')):
            raise RuntimeError('Standalone live snapshot required')
        before = inspect(source,root,batch_size)
        if file_sha256(snapshot) != expected_sha256:
            raise RuntimeError('Snapshot changed')
        import psycopg
        from psycopg import sql
        from scrapper.db.postgresql import _configure, _connection_kwargs
        with (nullcontext(_destination) if _destination is not None else psycopg.connect(settings.dsn, **_connection_kwargs(settings))) as dest:
            _configure(dest)
            with dest.transaction():
                for name in ('statement_timeout','lock_timeout'):
                    dest.execute('SELECT set_config(%s,%s,true)',(name,'30000' if name=='statement_timeout' else '5000'))
                for lane in ('stream','recovery'):
                    if not dest.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',('live:'+schema+':'+lane,)).fetchone()[0]:
                        raise RuntimeError('Live writer busy')
                dest.execute(sql.SQL('LOCK TABLE {}.frames, {}.observations IN ACCESS EXCLUSIVE MODE').format(sql.Identifier(schema),sql.Identifier(schema)))
                if contract(dest,schema,root) != target:
                    raise RuntimeError('Live transfer target mismatch')
                dest.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(schema)))
                populated = any(dest.execute('SELECT EXISTS(SELECT 1 FROM '+name+')').fetchone()[0] for name in TABLES)
                for name, order in TABLES.items():
                    if not populated:
                        reader=source.execute('SELECT * FROM '+name+' ORDER BY '+order.replace('"C"','BINARY'))
                        with dest.cursor() as writer:
                            while rows := reader.fetchmany(batch_size):
                                writer.executemany('INSERT INTO '+name+' VALUES ('+','.join(['%s']*len(rows[0]))+')',rows)
                                if progress:progress(name,len(rows))
                    with dest.cursor(name='verify_'+name) as reader:
                        reader.execute('SELECT * FROM '+name+' ORDER BY '+order)
                        if rows_digest(reader,batch_size) != before['tables'][name]:
                            raise RuntimeError('Live destination content mismatch')
                if not populated:
                    next_id=dest.execute('SELECT COALESCE(max(id),0)+1 FROM frames').fetchone()[0]
                    dest.execute(sql.SQL('ALTER TABLE frames ALTER COLUMN id RESTART WITH {}').format(sql.Literal(max(1,next_id))))
                if inspect(source,root,batch_size) != before or file_sha256(snapshot) != expected_sha256:
                    raise RuntimeError('Live source/raw evidence changed during transfer')
    return dict(before,snapshot_sha256=expected_sha256,status='verified')


def select_target(settings, snapshot, schema, root, *, expected_sha256, target, backend):
    """Explicit cutover/rollback rehearsal. Refuses rollback after native writes.

    Never rewrites SQLite or raw bytes. A later native write needs a separately
    reviewed reverse transfer; it cannot be discarded by this API.
    """
    if backend not in ('sqlite','postgresql'):
        raise ValueError('Unknown live target')
    root=Path(root)
    with ExitStack() as stack:
        for name in ('.stream.lock','.recovery.lock','.evidence-stream.lock','.evidence-recovery.lock'):
            lock=stack.enter_context((root/name).open('a+b'))
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with read_snapshot(snapshot) as frozen, read_snapshot(root/'live.sqlite3') as old:
            if inspect(frozen,root) != inspect(old,root):
                raise RuntimeError('Live SQLite changed since snapshot')
            import psycopg
            from scrapper.db.postgresql import _connection_kwargs
            with psycopg.connect(settings.dsn, **_connection_kwargs(settings)) as guard:
                for lane in ('stream','recovery'):
                    if not guard.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',('live:'+schema+':'+lane,)).fetchone()[0]:
                        raise RuntimeError('Live writer busy')
                result=transfer(settings,snapshot,schema,root,expected_sha256=expected_sha256,target=target,_destination=guard)
                if backend == 'postgresql':dump(root/MARKER,target)
                else:(root/MARKER).unlink(missing_ok=True)
                return result
