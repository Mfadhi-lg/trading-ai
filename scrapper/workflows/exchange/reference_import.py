"""Explicit, atomic import of a retained exchange-security reference export."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ...db.database import check_schema_compatibility
from ...db.repository_backend import is_postgresql, transaction
from ...sources.exchange.reference import COLUMNS, read_reference_rows


def import_exchange_reference(conn: sqlite3.Connection, path: Path) -> dict:
    """Bootstrap an empty table without replacing existing rows or inventing lineage."""
    rows = read_reference_rows(path)
    check_schema_compatibility(conn)
    if conn.in_transaction:
        raise ValueError("reference import requires a connection without an active transaction")
    if is_postgresql(conn):
        with transaction(conn):
            return _import_rows(conn, rows)
    if not conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise ValueError("reference import requires foreign-key enforcement")
    if conn.execute("PRAGMA query_only").fetchone()[0]:
        raise ValueError("reference import requires an explicit writable connection")
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = _import_rows(conn, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result


def _import_rows(conn, rows):
    if conn.execute("SELECT COUNT(*) FROM exchange_securities").fetchone()[0]:
        raise ValueError("exchange_securities is nonempty; reference import refuses to replace existing rows")
    snapshots = {r[0] for r in conn.execute("SELECT snapshot_id FROM exchange_source_snapshots")}
    for line, row in enumerate(rows, 2):
        values = dict(zip(COLUMNS, row))
        for key in ("first_observed_snapshot_id", "last_observed_snapshot_id"):
            if values[key] not in snapshots:
                raise ValueError(f"reference CSV line {line}: missing source snapshot {values[key]}")
        version = values["current_version_id"]
        if version is not None:
            match = conn.execute("SELECT exchange_security_id FROM exchange_security_versions WHERE version_id=?", (version,)).fetchone()
            if match is None or match[0] != values["exchange_security_id"]:
                raise ValueError(f"reference CSV line {line}: current_version_id does not resolve to this security")
    sql = f"INSERT INTO exchange_securities ({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})"
    conn.executemany(sql, rows)
    return {"status": "imported", "imported_rows": len(rows)}
