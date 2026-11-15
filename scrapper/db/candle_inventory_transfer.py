"""Offline archive metadata contract. Never imported by archive runtime writers.

Explicit paths/settings only; callers must supply a quiesced disposable inventory
copy and unchanged archive file root. No provider requests or file rewrites.
"""
from collections import Counter
from contextlib import closing
import gzip
import hashlib
import os
from pathlib import Path
import sqlite3

from .postgresql import connect
from .postgresql_schema import validate_schema_name
from .postgresql_transfer import (
    _catalog, _catalog_tokens, encode_row, file_sha256, read_snapshot,
    source_rows, sqlite_identifier, table_digest,
)

VERSION = 'candle_inventory_v1'
DDL = Path(__file__).with_name('candle_inventory_v1.sql').read_text()


def source_tables(conn):
    with closing(sqlite3.connect(':memory:')) as baseline:
        baseline.executescript(DDL)
        if _catalog_tokens(_catalog(conn)) != _catalog_tokens(_catalog(baseline)):
            raise RuntimeError('Unreviewed candle inventory schema')
    if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise RuntimeError('Inventory integrity failure')
    tables = {}
    for kind, name, _ in _catalog(conn):
        if kind != 'table':
            continue
        columns = list(conn.execute(f'PRAGMA table_info({sqlite_identifier(name)})'))
        keys = [c for c in columns if c[5]]
        nulls = ' OR '.join(sqlite_identifier(c[1]) + ' IS NULL' for c in keys)
        if conn.execute(f'SELECT 1 FROM {sqlite_identifier(name)} WHERE {nulls} LIMIT 1').fetchone():
            raise RuntimeError('NULL inventory key requires explicit remediation')
        tables[name] = columns
    return tables


def native_ddl():
    # Preserve all source fields and indexes. No generated IDs or sequences exist.
    return DDL.replace('IF NOT EXISTS ', '').replace('INTEGER', 'BIGINT').replace('REAL', 'DOUBLE PRECISION')


def migrate(conn, schema):
    from psycopg import sql
    validate_schema_name(schema)
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('candle-inventory:' + schema,))
    # Deliberately requires a new namespace; never adopts an existing schema.
    conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    conn.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
    conn.execute(native_ddl())
    conn.execute('CREATE TABLE _candle_inventory_version(version TEXT PRIMARY KEY, ddl_sha256 TEXT NOT NULL)')
    conn.execute('INSERT INTO _candle_inventory_version VALUES (%s,%s)',
                 (VERSION, hashlib.sha256(native_ddl().encode()).hexdigest()))


def catalog(conn, schema):
    """Pin actual native tables, columns, defaults, indexes and constraints."""
    queries = (
        "SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind NOT IN ('i') ORDER BY 1,2",
        "SELECT table_name,column_name,data_type,is_nullable,column_default,ordinal_position FROM information_schema.columns WHERE table_schema=%s ORDER BY table_name,ordinal_position",
        "SELECT tablename,indexname,replace(indexdef,quote_ident(schemaname)||'.','') AS definition FROM pg_indexes WHERE schemaname=%s ORDER BY tablename,indexname",
        "SELECT c.relname,con.conname,pg_get_constraintdef(con.oid) AS definition FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY 1,2",
        "SELECT event_object_table,trigger_name,action_statement FROM information_schema.triggers WHERE trigger_schema=%s ORDER BY 1,2",
        "SELECT tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname=%s ORDER BY 1,2",
    )
    return [conn.execute(query, (schema,)).fetchall() for query in queries]


def check_schema(conn, schema):
    from psycopg import sql
    import uuid
    # Compare against a fresh transaction-local reference; rollback removes it.
    reference = 'candle_check_' + uuid.uuid4().hex
    conn.execute('SAVEPOINT candle_contract')
    try:
        migrate(conn, reference)
        if catalog(conn, schema) != catalog(conn, reference):
            raise RuntimeError('Native candle schema drift')
        rows = conn.execute(sql.SQL('SELECT * FROM {}._candle_inventory_version').format(sql.Identifier(schema))).fetchall()
        if rows != [{'version': VERSION, 'ddl_sha256': hashlib.sha256(native_ddl().encode()).hexdigest()}]:
            raise RuntimeError('Native candle version drift')
    finally:
        conn.execute('ROLLBACK TO SAVEPOINT candle_contract')
        conn.execute('RELEASE SAVEPOINT candle_contract')


def file_inventory(root):
    """Hash every retained file including receipts; reject links and special files."""
    root = Path(root).resolve(strict=True)
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise RuntimeError('Archive symlink refused')
        if path.is_dir():
            continue
        if not path.is_file():
            raise RuntimeError('Archive special file refused')
        # Inventory snapshots/locks are separate mutable operational state.
        if path.name.startswith('inventory.sqlite3') or path.name in ('.writer.lock', '.inventory-target.json'):
            continue
        result[str(path.relative_to(root))] = {'bytes': path.stat().st_size, 'sha256': file_sha256(path)}
    return result


def reference_check(conn, root, files):
    for table, path_col, hash_col, compressed in (
        ('requests', 'raw_path', 'raw_sha256', True),
        ('datasets', 'parquet_path', 'parquet_sha256', False),
    ):
        for path, checksum in conn.execute(f'SELECT {path_col},{hash_col} FROM {table}'):
            if path is None and checksum is None:
                continue
            if path not in files or not checksum:
                raise RuntimeError('Missing archive reference')
            actual = files[path]['sha256']
            if compressed:
                digest = hashlib.sha256()
                with gzip.open(Path(root) / path, 'rb') as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b''):
                        digest.update(block)
                actual = digest.hexdigest()
            if actual != checksum:
                raise RuntimeError('Archive reference checksum failure')
    for path, size in conn.execute('SELECT parquet_path,bytes FROM datasets'):
        if size is not None and files[path]['bytes'] != size:
            raise RuntimeError('Parquet size mismatch')


def inspect(conn, root, batch_size):
    tables = source_tables(conn)
    files = file_inventory(root)
    reference_check(conn, root, files)
    summaries = {name: table_digest(conn, name, cols, batch_size) for name, cols in tables.items()}
    statuses = {name: sorted(Counter(conn.execute(f'SELECT {column} FROM {name}')).items(), key=repr)
                for name, column in (('requests', 'status'), ('datasets', 'observation_status'), ('candle_observations', 'status'))}
    # Source has no FKs. Preserve existing gaps and make them reviewable instead
    # of silently repairing or dropping historical lineage.
    lineage = {}
    for label, query in {
        'datasets_without_request': 'SELECT count(*) FROM datasets d WHERE d.request_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM requests r WHERE r.request_id=d.request_id)',
        'observations_without_dataset': 'SELECT count(*) FROM candle_observations c WHERE c.dataset_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM datasets d WHERE d.dataset_id=c.dataset_id)',
        'revisions_without_request': 'SELECT count(*) FROM revisions v WHERE v.request_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM requests r WHERE r.request_id=v.request_id)',
        'dataset_raw_not_in_requests': 'SELECT count(*) FROM datasets d WHERE d.raw_sha256 IS NOT NULL AND d.raw_sha256 NOT IN (SELECT r.raw_sha256 FROM requests r WHERE r.raw_sha256 IS NOT NULL)',
        'revision_previous_not_in_requests': 'SELECT count(*) FROM revisions v WHERE v.previous_sha256 IS NOT NULL AND v.previous_sha256 NOT IN (SELECT r.raw_sha256 FROM requests r WHERE r.raw_sha256 IS NOT NULL)',
        'revision_new_not_in_requests': 'SELECT count(*) FROM revisions v WHERE v.new_sha256 IS NOT NULL AND v.new_sha256 NOT IN (SELECT r.raw_sha256 FROM requests r WHERE r.raw_sha256 IS NOT NULL)',
    }.items():
        lineage[label] = conn.execute(query).fetchone()[0]
    return {'tables': summaries, 'statuses': statuses, 'files': files, 'unresolved_lineage': lineage}


def export_snapshot(source, target, root, *, batch_size=1000):
    _batch(batch_size)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with read_snapshot(source) as original, closing(sqlite3.connect(target)) as backup:
        original.backup(backup, pages=256)
        backup.execute('PRAGMA journal_mode=DELETE')
    with read_snapshot(target) as conn:
        report = inspect(conn, root, batch_size)
    return dict(report, snapshot_sha256=file_sha256(target), version=VERSION)


def _batch(value):
    if type(value) is not int or not 1 <= value <= 100000:
        raise ValueError('Batch size must be between 1 and 100000')


def transfer(settings, snapshot, schema, root, *, expected_sha256, batch_size=1000, progress=None):
    """Atomic bounded load or exact verify-only retry. Never merges partial data."""
    _batch(batch_size)
    validate_schema_name(schema)
    if not expected_sha256 or file_sha256(snapshot) != expected_sha256:
        raise RuntimeError('Unauthorized snapshot checksum')
    with read_snapshot(snapshot) as source:
        tables = source_tables(source)  # pins rollback-journal read lock
        if source.execute('PRAGMA journal_mode').fetchone()[0] != 'delete' or any(Path(str(snapshot) + s).exists() for s in ('-wal', '-shm', '-journal')):
            raise RuntimeError('Standalone snapshot required')
        if file_sha256(snapshot) != expected_sha256:
            raise RuntimeError('Snapshot changed before read lock')
        before = inspect(source, root, batch_size)
        # Optional native dependencies are needed only after offline validation.
        from psycopg import sql
        from psycopg.rows import tuple_row
        with connect(settings) as dest:
            dest.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('candle-inventory:' + schema,))
            dest.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(sql.SQL(',').join(sql.Identifier(schema, name) for name in [*tables, '_candle_inventory_version'])))
            check_schema(dest, schema)
            dest.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
            populated = any(dest.execute(sql.SQL('SELECT EXISTS(SELECT 1 FROM {}) AS present').format(sql.Identifier(name))).fetchone()['present'] for name in tables)
            for name, columns in tables.items():
                if not populated:
                    insert = sql.SQL('INSERT INTO {} VALUES ({})').format(sql.Identifier(name), sql.SQL(',').join(sql.Placeholder() for _ in columns))
                    with dest.cursor() as writer:
                        for rows in source_rows(source, name, columns, batch_size):
                            for row in rows:
                                encode_row(row, columns)
                            writer.executemany(insert, rows)
                            if progress:
                                progress(name, len(rows))
                keys = sorted((c for c in columns if c[5]), key=lambda c: c[5])
                order = sql.SQL(',').join(sql.SQL('{} COLLATE "C"').format(sql.Identifier(c[1])) if c[2] == 'TEXT' else sql.Identifier(c[1]) for c in keys)
                digest, count = hashlib.sha256(), 0
                with dest.cursor(name='verify_' + name, row_factory=tuple_row) as reader:
                    reader.execute(sql.SQL('SELECT * FROM {} ORDER BY {}').format(sql.Identifier(name), order))
                    while rows := reader.fetchmany(batch_size):
                        for row in rows:
                            digest.update(encode_row(row, columns))
                        count += len(rows)
                if {'rows': count, 'sha256': digest.hexdigest()} != before['tables'][name]:
                    raise RuntimeError('Destination content mismatch: ' + name)
            if inspect(source, root, batch_size) != before or file_sha256(snapshot) != expected_sha256:
                raise RuntimeError('Source or archive changed during transfer')
    return dict(before, snapshot_sha256=expected_sha256, version=VERSION, status='verified')
