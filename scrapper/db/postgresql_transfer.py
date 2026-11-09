"""Bounded, verified transfer from an unchanged SQLite source into PostgreSQL.

No implicit production access, schema application, destructive destination reset,
upsert, type coercion, source mutation or application-runtime activation.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote
import uuid

from .postgresql_schema import check_compatibility, manifest, validate_schema_name


def sqlite_identifier(value):
    return '"' + value.replace('"', '""') + '"'


@contextmanager
def read_snapshot(path):
    path = Path(path).resolve(strict=True)
    conn = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        yield conn
    finally:
        conn.close()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_tables(conn):
    catalog = list(conn.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY name"))
    checksum = hashlib.sha256(json.dumps(catalog, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    if checksum != manifest()["sqlite_schema_sha256"]:
        raise RuntimeError("SQLite schema differs from the reviewed PostgreSQL baseline")
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise RuntimeError("SQLite integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("SQLite foreign key check failed")
    tables = {}
    for name in manifest()["objects"]["table"]:
        columns = list(conn.execute(f"PRAGMA table_info({sqlite_identifier(name)})"))
        keys = sorted((col for col in columns if col[5]), key=lambda col: col[5])
        if not keys:
            raise RuntimeError(f"Source table requires a primary key: {name}")
        nulls = " OR ".join(f"{sqlite_identifier(col[1])} IS NULL" for col in keys)
        if conn.execute(f"SELECT 1 FROM {sqlite_identifier(name)} WHERE {nulls} LIMIT 1").fetchone():
            raise RuntimeError(f"Source contains NULL primary key: {name}")
        tables[name] = columns
    return tables


def encode_row(row, columns):
    values = []
    for value, col in zip(row, columns):
        expected = {"TEXT": str, "INTEGER": int, "REAL": float}[col[2]]
        if value is None:
            values.append(["null"])
        elif type(value) is expected:
            values.append([col[2], value.hex() if type(value) is float else value])
        else:
            raise RuntimeError("Stored SQLite value has incompatible declared type")
    return json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"


def source_rows(conn, table, columns, batch_size):
    keys = sorted((col for col in columns if col[5]), key=lambda col: col[5])
    order = ", ".join(sqlite_identifier(col[1]) + (" COLLATE BINARY" if col[2] == "TEXT" else "") for col in keys)
    cursor = conn.execute(f"SELECT * FROM {sqlite_identifier(table)} ORDER BY {order}")
    while rows := cursor.fetchmany(batch_size):
        yield rows


def table_digest(conn, table, columns, batch_size):
    digest, count = hashlib.sha256(), 0
    for rows in source_rows(conn, table, columns, batch_size):
        for row in rows:
            digest.update(encode_row(row, columns))
        count += len(rows)
    return {"rows": count, "sha256": digest.hexdigest()}


def export_snapshot(source, target, *, batch_size=1000, progress=None):
    """Verified SQLite backup using the backup API, including committed WAL.

    The target must not exist. Failure preserves the partial target for operator
    diagnosis and never deletes or overwrites retained evidence.
    """
    if not 1 <= batch_size <= 100_000:
        raise ValueError("Batch size must be between 1 and 100000")
    target = Path(target)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    # Backup owns its own consistent source snapshot. Do not use immutable=1:
    # that would ignore a live source WAL.
    with read_snapshot(source) as original:
        backup = sqlite3.connect(target)
        try:
            original.backup(backup, pages=256)
            backup.execute("PRAGMA journal_mode=DELETE")
        finally:
            backup.close()
    with read_snapshot(target) as snapshot:
        tables = source_tables(snapshot)
        summary = {}
        for name, columns in tables.items():
            summary[name] = table_digest(snapshot, name, columns, batch_size)
            if progress:
                progress({"phase": "export", "table": name, "rows": summary[name]["rows"]})
    return {"format": 1, "snapshot_sha256": file_sha256(target), "tables": summary, "status": "verified_snapshot"}


def transfer(settings, snapshot, schema, *, expected_sha256, batch_size=1000, progress=None):
    """Atomically load an empty reviewed schema, or verify an exact rerun.

    All destination tables are exclusively locked until commit. User triggers
    are transactionally suspended while copying retained history, then restored;
    PK/UNIQUE/CHECK constraints remain active and deferred FKs are forced before
    commit. Any exception rolls back the entire load, including trigger/sequence
    changes. Restart uses the verified snapshot; no partial rows are committed.
    """
    from psycopg import sql
    from psycopg.rows import tuple_row
    from .postgresql import connect
    validate_schema_name(schema)
    if not 1 <= batch_size <= 100_000:
        raise ValueError("Batch size must be between 1 and 100000")
    if not expected_sha256 or file_sha256(snapshot) != expected_sha256:
        raise RuntimeError("Snapshot checksum differs from the authorized snapshot")
    with read_snapshot(snapshot) as source:
        if source.execute("PRAGMA journal_mode").fetchone()[0] != "delete" or any(Path(str(snapshot) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise RuntimeError("Import requires a standalone verified snapshot without journal sidecars")
        tables = source_tables(source)
        # source_tables pins a shared rollback-journal read lock. Rehash the
        # authorized file while that lock prevents committed source changes.
        if file_sha256(snapshot) != expected_sha256:
            raise RuntimeError("Snapshot file changed before its read transaction was pinned")
        report = {"format": 1, "snapshot_sha256": expected_sha256, "tables": {}, "status": "pending"}
        with connect(settings) as dest:
            dest.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("trading-ai-schema:" + schema,))
            dest.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog, pg_temp").format(sql.Identifier(schema)))
            actual = {row["tablename"] for row in dest.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s", (schema,))}
            if actual != set(tables) | {"_postgresql_migrations"}:
                raise RuntimeError("Destination table inventory differs from reviewed schema")
            dest.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(sql.SQL(",").join(sql.Identifier(schema, name) for name in tables)))
            check_compatibility(dest, schema)
            # A previous successful load is verify-only; partial/foreign data is
            # never merged, skipped, deleted or overwritten.
            populated = any(dest.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM {} LIMIT 1) AS present").format(sql.Identifier(name))).fetchone()["present"] for name in tables)
            if not populated:
                for name in tables:
                    dest.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER USER").format(sql.Identifier(name)))
                dest.execute("SET CONSTRAINTS ALL DEFERRED")
                for name, columns in tables.items():
                    if progress:
                        progress({"phase": "import", "table": name, "rows": 0})
                    insert = sql.SQL("INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE VALUES ({})").format(sql.Identifier(name), sql.SQL(",").join(sql.Identifier(col[1]) for col in columns), sql.SQL(",").join(sql.Placeholder() for _ in columns))
                    count = 0
                    with dest.cursor() as writer:
                        for rows in source_rows(source, name, columns, batch_size):
                            for row in rows:
                                encode_row(row, columns)  # Fail before driver coercion.
                            writer.executemany(insert, rows)
                            count += len(rows)
                            if progress:
                                progress({"phase": "import", "table": name, "rows": count})
                dest.execute("SET CONSTRAINTS ALL IMMEDIATE")
                for name in tables:
                    dest.execute(sql.SQL("ALTER TABLE {} ENABLE TRIGGER USER").format(sql.Identifier(name)))
            # Re-read every source/destination row through bounded server cursors.
            for name, columns in tables.items():
                if progress:
                    progress({"phase": "verify", "table": name, "rows": 0})
                source_summary = table_digest(source, name, columns, batch_size)
                keys = sorted((col for col in columns if col[5]), key=lambda col: col[5])
                order = sql.SQL(",").join(sql.SQL("{} COLLATE \"C\"").format(sql.Identifier(col[1])) if col[2] == "TEXT" else sql.Identifier(col[1]) for col in keys)
                digest, count = hashlib.sha256(), 0
                with dest.cursor(name="verify_" + uuid.uuid4().hex, row_factory=tuple_row) as reader:
                    reader.execute(sql.SQL("SELECT {} FROM {} ORDER BY {}").format(sql.SQL(",").join(sql.Identifier(col[1]) for col in columns), sql.Identifier(name), order))
                    while rows := reader.fetchmany(batch_size):
                        for row in rows:
                            digest.update(encode_row(row, columns))
                        count += len(rows)
                target_summary = {"rows": count, "sha256": digest.hexdigest()}
                if source_summary != target_summary:
                    raise RuntimeError(f"Source/destination verification failed: {name}")
                report["tables"][name] = {"source": source_summary, "destination": target_summary, "verified": True}
                if progress:
                    progress({"phase": "verify", "table": name, "rows": count})
            sequences = dest.execute("""SELECT t.relname AS table_name, a.attname AS column_name,
                    sn.nspname AS sequence_schema, s.relname AS sequence_name
                    FROM pg_class t JOIN pg_namespace tn ON tn.oid=t.relnamespace
                    JOIN pg_attribute a ON a.attrelid=t.oid AND a.attidentity<>''
                    JOIN pg_depend d ON d.refobjid=t.oid AND d.refobjsubid=a.attnum AND d.deptype='i'
                    JOIN pg_class s ON s.oid=d.objid AND s.relkind='S'
                    JOIN pg_namespace sn ON sn.oid=s.relnamespace WHERE tn.nspname=%s""", (schema,)).fetchall()
            for seq in sequences:
                maximum = dest.execute(sql.SQL("SELECT max({}) AS n FROM {}").format(sql.Identifier(seq["column_name"]), sql.Identifier(seq["table_name"]))).fetchone()["n"]
                if not populated:
                    next_id = max(1, (maximum or 0) + 1)
                    # ALTER SEQUENCE RESTART is transactional; setval is not.
                    dest.execute(sql.SQL("ALTER SEQUENCE {} RESTART WITH {}").format(sql.Identifier(seq["sequence_schema"], seq["sequence_name"]), sql.Literal(next_id)))
                position = dest.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(sql.Identifier(seq["sequence_schema"], seq["sequence_name"]))).fetchone()
                next_value = position["last_value"] + int(position["is_called"])
                if maximum is not None and next_value <= maximum:
                    raise RuntimeError(f"Destination identity sequence is behind imported keys: {seq['table_name']}")
            if file_sha256(snapshot) != expected_sha256:
                raise RuntimeError("Snapshot file changed during transfer")
            check_compatibility(dest, schema)
        report["status"] = "verified_existing" if populated else "committed"
        return report
