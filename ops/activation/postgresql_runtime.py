"""Explicit native operational connections; importing never activates a target."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3


def enabled():
    return os.environ.get('TRADING_AI_DB_BACKEND') == 'postgresql'


def read_connection(db, *, immutable=False):
    if enabled():
        from scrapper.db.database import connect
        connection=connect(db, read_only=True)
        connection.raw.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return connection
    connection = sqlite3.connect('file:' + str(db) + '?mode=ro' + ('&immutable=1' if immutable else ''), uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    return connection


def table_columns(conn, table):
    from scrapper.db.repository_backend import is_postgresql
    if is_postgresql(conn):
        return [row[0] for row in conn.execute('''SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=? ORDER BY ordinal_position''', (table,))]
    return [row['name'] for row in conn.execute('PRAGMA table_info("' + table.replace('"', '""') + '")')]


def table_names(conn):
    from scrapper.db.repository_backend import is_postgresql
    if is_postgresql(conn):
        return [row[0] for row in conn.execute('''SELECT tablename FROM pg_catalog.pg_tables
            WHERE schemaname=current_schema() ORDER BY tablename''')]
    return [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]


def query_deadline(conn, deadline, steps=10000):
    import time
    from scrapper.db.repository_backend import is_postgresql
    if is_postgresql(conn):
        milliseconds = max(1, int((deadline - time.monotonic()) * 1000))
        # Connection setting only; never grants rights or rewrites application SQL.
        conn.raw.execute("SELECT set_config('statement_timeout',%s,false)", (str(milliseconds),))
    else:
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), steps)



PHYSICAL_ROOT=Path('/physical')
MOUNTINFO=Path('/proc/self/mountinfo')


def verify_physical_storage(baseline):
    """Keep the existing underlying storage seal independent of SQLite's inode."""
    import hashlib
    rows=[line.split(' - ',1)[0].split() for line in MOUNTINFO.read_text().splitlines()]
    if not any(len(row)>5 and row[4]==str(PHYSICAL_ROOT) and 'ro' in row[5].split(',') for row in rows):
        raise RuntimeError('Read-only physical storage mount required')
    markers=baseline.get('physical_markers')
    if not isinstance(markers,dict) or not markers:raise RuntimeError('Physical storage identity evidence missing')
    for name,expected in markers.items():
        if not name.startswith('/data/') or '..' in Path(name).parts:
            raise RuntimeError('Unexpected physical marker target')
        actual=PHYSICAL_ROOT/name.removeprefix('/data/')
        if actual.is_symlink() or hashlib.sha256(actual.read_bytes()).hexdigest()!=expected['sha256']:
            raise RuntimeError('Underlying production storage marker changed')


def preflight(db, baseline):
    """Check exact server identity/role/schema and retained control/hold evidence."""
    if not enabled():
        raise RuntimeError('Native operational preflight requires explicit PostgreSQL configuration')
    from scrapper.config import assert_configured_writable_targets
    assert_configured_writable_targets(db_path=Path(db))
    verify_physical_storage(baseline)
    from scrapper.db.database import check_schema_compatibility
    from production_ownership import assert_no_conflicting_leases
    from datetime import datetime, timezone
    with closing(read_connection(db)) as reader:
        check_schema_compatibility(reader)
        assert_no_conflicting_leases(reader, now=datetime.now(timezone.utc).isoformat())
        receipt = reader.execute('SELECT * FROM document_extraction_claims WHERE resource_key=?',
                                 (baseline['control_row']['resource_key'],)).fetchone()
        if receipt is None or dict(receipt) != baseline['control_row']:
            raise RuntimeError('Native production control receipt changed')
        holds = [dict(row) for row in reader.execute('SELECT * FROM exchange_source_access_events ORDER BY event_id')]
        queue_count = reader.execute('SELECT COUNT(*) FROM screener_company_queue').fetchone()[0]
        filings = [dict(row) for row in reader.execute('SELECT exchange,COUNT(*) AS count FROM exchange_result_filings GROUP BY exchange')]
        return {'holds': holds, 'queue_count': queue_count, 'filing_counts': filings}


def fingerprint(conn,table):
    """Bounded native preservation digest over exact typed values in PK order."""
    import hashlib
    import uuid
    from psycopg import sql
    from psycopg.rows import tuple_row
    from scrapper.db.postgresql_transfer import encode_row
    from scrapper.db.postgresql_source_text import SOURCE_TEXT_COLUMNS,transfer_row
    metadata=conn.execute('''SELECT a.attname,format_type(a.atttypid,a.atttypmod),
        a.attnotnull,coalesce(array_position(i.indkey::smallint[],a.attnum),-1) AS key_position
        FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        LEFT JOIN pg_index i ON i.indrelid=t.oid AND i.indisprimary
        WHERE n.nspname=current_schema() AND t.relname=? AND a.attnum>0
        AND NOT a.attisdropped ORDER BY a.attname''',(table,)).fetchall()
    if not metadata:raise ValueError('Unknown native preservation table')
    kinds={'bigint':'INTEGER','double precision':'REAL','text':'TEXT'}
    columns=[]
    for position,row in enumerate(metadata):
        kind=row[1]
        if kind=='bytea' and row[0] in SOURCE_TEXT_COLUMNS.get(table,()):kind='text'
        if kind not in kinds:raise ValueError('Unsupported native preservation column type')
        columns.append((position,row[0],kinds[kind],row[2],None,row[3]+1 if row[3]>=0 else 0))
    keys=sorted((col for col in columns if col[5]),key=lambda col:col[5])
    if not keys:raise ValueError('Native preservation table requires a primary key')
    order=sql.SQL(',').join(sql.SQL('{} COLLATE "C"').format(sql.Identifier(col[1]))
        if col[2]=='TEXT' else sql.Identifier(col[1]) for col in keys)
    digest=hashlib.sha256()
    with conn.raw.transaction(),conn.raw.cursor(name='preserve_'+uuid.uuid4().hex,row_factory=tuple_row) as reader:
        reader.execute(sql.SQL('SELECT {} FROM {} ORDER BY {}').format(
            sql.SQL(',').join(sql.Identifier(col[1]) for col in columns),sql.Identifier(table),order))
        while rows:=reader.fetchmany(1000):
            for row in rows:digest.update(encode_row(transfer_row(table,columns,row,to_postgresql=False),columns))
    return digest.hexdigest()
