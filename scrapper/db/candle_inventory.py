"""Explicit archive inventory sessions; never migrate or fall back at runtime.

The filesystem writer fence is shared with pre-port writers. Native writers also
hold a session advisory lock, surviving request-boundary commits. Selection is
pinned in the archive root by a separately authorized, reconciled cutover.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .postgresql import PostgreSQLSettings, _configure, _connection_kwargs
from .postgresql_application import repository_row
from .repository_backend import _parameters
from .postgresql_schema import validate_schema_name
from . import candle_inventory_transfer as transfer

MARKER = '.inventory-target.json'


def backend():
    value = os.environ.get('TRADING_AI_CANDLE_BACKEND', 'sqlite')
    if value not in ('sqlite', 'postgresql'):
        raise ValueError('Explicit candle backend must be sqlite or postgresql')
    return value


def native_settings():
    return PostgreSQLSettings(os.environ.get('TRADING_AI_CANDLE_DATABASE_URL', ''))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def identity(conn, schema):
    row = conn.execute('''SELECT (SELECT system_identifier::text FROM pg_control_system()) AS cluster,
        (SELECT oid::text FROM pg_database WHERE datname=current_database()) AS database,
        (SELECT oid::text FROM pg_namespace WHERE nspname=%s) AS schema''', (schema,)).fetchone()
    return dict(row)


def target_contract(conn, schema):
    validate_schema_name(schema)
    return dict(identity=identity(conn, schema), schema=schema,
                catalog_sha256=fingerprint(transfer.catalog(conn, schema)), version=transfer.VERSION,
                version_rows=conn.execute('SELECT * FROM "' + schema + '"._candle_inventory_version').fetchall())


def select_target(root, snapshot, *, expected_sha256, settings, schema, target):
    """Operator-only cutover/rollback rehearsal API; never called by runtime.

    Requires a quiesced archive and the exact original snapshot. Rollback refuses
    any native metadata or retained-file divergence; new native writes require a
    separately designed reverse transfer, never silently discarded.
    """
    import fcntl
    from .postgresql import connect
    from experiments.historical_candle_archive_v1.storage import dump
    if target not in ('sqlite', 'postgresql'):
        raise ValueError('unknown archive target')
    if not expected_sha256 or transfer.file_sha256(snapshot) != expected_sha256:
        raise RuntimeError('Unauthorized snapshot checksum')
    root = Path(root)
    with (root / '.writer.lock').open('a+b') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Refuse stale snapshots in either direction, including changed requests
        # that have not yet produced any raw/Parquet file.
        with transfer.read_snapshot(snapshot) as frozen, transfer.read_snapshot(root / 'inventory.sqlite3') as old:
            if transfer.inspect(frozen, root, 1000) != transfer.inspect(old, root, 1000):
                raise RuntimeError('STOP_ARCHIVE_SQLITE_CHANGED_SINCE_SNAPSHOT')
        with connect(settings) as conn:
            locked = conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked',
                                  ('candle-runtime:' + schema,)).fetchone()['locked']
            if not locked:
                raise RuntimeError('STOP_ARCHIVE_WRITER_BUSY')
            report = transfer.transfer(settings, snapshot, schema, root, expected_sha256=expected_sha256)
            transfer.check_schema(conn, schema)
            contract = target_contract(conn, schema)
            dump(root / MARKER, dict(backend=target, contract=contract,
                 snapshot_sha256=expected_sha256, reconciliation_sha256=fingerprint(report)))
        return report


class InventoryError(RuntimeError):
    pass


class NativeInventory:
    """Archive DB-API surface with portable statements and explicit commits."""
    def __init__(self, root, *, read_only, writer, settings=None, schema=None):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row, tuple_row
        self.raw = None
        self.writer_lock = None
        if read_only == writer:
            raise ValueError("Writable inventory sessions require a writer fence")
        schema = schema or os.environ.get('TRADING_AI_CANDLE_SCHEMA', '')
        validate_schema_name(schema)
        settings = settings or native_settings()
        try:
            if writer:
                import fcntl
                self.writer_lock = (Path(root) / '.writer.lock').open('a+b')
                try:
                    fcntl.flock(self.writer_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError('STOP_ARCHIVE_WRITER_BUSY') from None
            marker = json.loads((Path(root) / MARKER).read_text())
            if marker['backend'] != 'postgresql':
                raise RuntimeError('STOP_ARCHIVE_TARGET_MISMATCH')
            self.raw = psycopg.connect(settings.dsn, row_factory=dict_row, **_connection_kwargs(settings))
            _configure(self.raw)
            self.raw.execute('SET default_transaction_read_only TO on' if read_only else 'SET default_transaction_read_only TO off')
            for name, value in (('statement_timeout', settings.statement_timeout_ms),
                                ('lock_timeout', settings.lock_timeout_ms)):
                self.raw.execute('SELECT set_config(%s,%s,false)', (name, str(value)))
            self.raw.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
            if target_contract(self.raw, schema) != marker['contract']:
                raise RuntimeError('STOP_ARCHIVE_TARGET_IDENTITY_OR_SCHEMA_CHANGED')
            if writer:
                runtime_locked = self.raw.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked',
                    ('candle-runtime:' + schema,)).fetchone()['locked']
                if not runtime_locked:
                    raise RuntimeError('STOP_ARCHIVE_WRITER_BUSY')
                locked = self.raw.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked',
                                          ('candle-inventory:' + schema,)).fetchone()['locked']
                if not locked:
                    raise RuntimeError('STOP_ARCHIVE_WRITER_BUSY')
            self.raw.row_factory = repository_row if read_only else tuple_row
            self.raw.autocommit = False
            self._row_factory = sqlite3.Row if read_only else None
        except psycopg.Error:
            self.close()
            raise InventoryError('STOP_ARCHIVE_DATABASE_UNAVAILABLE') from None
        except BaseException:
            self.close()
            raise

    @property
    def row_factory(self):
        return self._row_factory

    @row_factory.setter
    def row_factory(self, value):
        from psycopg.rows import tuple_row
        if value not in (None, sqlite3.Row):
            raise ValueError('Unsupported archive row factory')
        self._row_factory = value
        self.raw.row_factory = repository_row if value else tuple_row

    def execute(self, query, params=()):
        import psycopg
        try:
            return self.raw.execute(_parameters(query), params)
        except psycopg.Error:
            raise InventoryError('STOP_ARCHIVE_DATABASE_ERROR') from None

    def executemany(self, query, rows):
        import psycopg
        try:
            with self.raw.cursor() as cursor:
                cursor.executemany(_parameters(query), rows)
        except psycopg.Error:
            raise InventoryError('STOP_ARCHIVE_DATABASE_ERROR') from None

    def commit(self):
        import psycopg
        try:
            self.raw.commit()
        except psycopg.Error:
            raise InventoryError('STOP_ARCHIVE_DATABASE_ERROR') from None

    def rollback(self):
        import psycopg
        try:
            self.raw.rollback()
        except psycopg.Error:
            raise InventoryError('STOP_ARCHIVE_DATABASE_ERROR') from None

    def close(self):
        if self.raw is not None:
            self.raw.close()
        if self.writer_lock is not None:
            self.writer_lock.close()


def open_inventory(root, *, read_only=True, writer=False):
    root = Path(root)
    if read_only == writer:
        raise ValueError("Writable inventory sessions require a writer fence")
    if backend() == 'postgresql':
        return NativeInventory(root, read_only=read_only, writer=writer)
    marker = root / MARKER
    if marker.exists() and json.loads(marker.read_text())['backend'] != 'sqlite':
        raise RuntimeError('STOP_ARCHIVE_TARGET_MISMATCH; no SQLite fallback')
    if not read_only:
        raise ValueError('SQLite archive writers must use Archive')
    mode = 'ro'
    conn = sqlite3.connect((root / 'inventory.sqlite3').as_uri() + '?mode=' + mode, uri=True, timeout=30)
    if read_only:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
    return conn


def integrity_check(conn, *, quick=False):
    if isinstance(conn, NativeInventory):
        # PostgreSQL validates pages when read; do not mislabel this as a SQLite
        # integrity scan. Schema/identity were checked before the session opened.
        conn.execute('SELECT count(*) FROM requests').fetchone()
        return 'ok'
    if quick:
        return conn.execute('PRAGMA quick_check').fetchone()[0]
    return conn.execute('PRAGMA integrity_check').fetchall()[0][0]


def completed_requests_sql(conn):
    hint = '' if isinstance(conn, NativeInventory) else ' NOT INDEXED'
    return "SELECT request_id FROM requests" + hint + " WHERE status='SUCCESS'"


def integrity_scope(conn):
    return ('postgresql: target identity/schema verified and requests readable; no full physical integrity scan'
            if isinstance(conn, NativeInventory) else 'sqlite: integrity_check/quick_check')


def writer_busy(root):
    """Probe both fences without changing archive metadata."""
    try:
        probe = NativeInventory(root, read_only=False, writer=True)
    except RuntimeError as error:
        if 'WRITER_BUSY' in str(error):
            return True
        raise
    probe.close()
    return False
