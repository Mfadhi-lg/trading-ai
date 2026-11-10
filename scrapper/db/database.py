"""SQLite connections, read-only schema checks, and explicit migration control."""

import fcntl
import sqlite3
import threading
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from ..config import DB_PATH, MIGRATIONS_PATH, assert_configured_writable_targets, ensure_dirs


_IN_PROCESS_MIGRATION_LOCK = threading.Lock()
# Applied in the preserved A2 review stream and present in the retained schema
# copy, but intentionally absent from the current canonical migration directory.
# Identity is the full filename; this does not alias or replace browser 065.
HISTORICAL_MIGRATION_IDENTITIES = frozenset({"065_exchange_source_access.sql"})


def selected_backend(environ=None):
    env = os.environ if environ is None else environ
    backend = env.get("TRADING_AI_DB_BACKEND", "sqlite")
    if backend not in {"sqlite", "postgresql"}:
        raise ValueError("TRADING_AI_DB_BACKEND must be sqlite or postgresql")
    return backend


@contextmanager
def _migration_lock(conn: sqlite3.Connection):
    """Serialize explicit migration application across worker processes."""

    database_path = str(conn.execute("PRAGMA database_list").fetchone()[2] or "")
    if not database_path:
        with _IN_PROCESS_MIGRATION_LOCK:
            yield
        return
    lock_path = Path(database_path + ".migrate.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def connect(db_path: Path = DB_PATH, *, read_only: bool = False) -> sqlite3.Connection:
    """Open SQLite with explicit write intent.

    Read-only connections require an existing file, use SQLite's ``mode=ro``,
    and never create configured store directories or switch journal mode.
    """

    if selected_backend() == "postgresql":
        from .postgresql import PostgreSQLSettings
        from .postgresql_schema import validate_schema_name
        from .postgresql_application import open_application
        settings = PostgreSQLSettings.from_env()
        schema = os.environ.get("TRADING_AI_POSTGRESQL_SCHEMA", "")
        validate_schema_name(schema)
        if not read_only:
            assert_configured_writable_targets(db_path=Path(db_path))
            ensure_dirs()
        return open_application(settings, schema=schema, read_only=read_only)
    db_path = Path(db_path)
    if read_only:
        if not db_path.is_file():
            raise FileNotFoundError(f"read-only database does not exist: {db_path}")
        uri = f"file:{quote(str(db_path.resolve()), safe='/')}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA query_only=ON;")
    else:
        assert_configured_writable_targets(db_path=db_path)
        ensure_dirs()
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    return conn


def _migration_names() -> list[str]:
    return [path.name for path in sorted(Path(MIGRATIONS_PATH).glob("*.sql"))]


def check_schema_compatibility(conn: sqlite3.Connection) -> None:
    """Read the migration ledger and fail if this code cannot use the schema.

    This function is safe for a read-only connection. It never creates the
    ledger, applies SQL, takes a filesystem lock, or writes database state.
    """

    from .repository_backend import is_postgresql
    if is_postgresql(conn):
        from .postgresql_schema import check_compatibility
        raw = getattr(conn, "raw", conn)
        schema = getattr(conn, "schema", os.environ.get("TRADING_AI_POSTGRESQL_SCHEMA", ""))
        with raw.transaction():
            check_compatibility(raw, schema)
        return
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not exists:
        raise RuntimeError(
            "Database schema is not initialized; run the explicit init-db command"
        )
    applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
    available = set(_migration_names())
    unknown = sorted(applied - available - HISTORICAL_MIGRATION_IDENTITIES)
    pending = sorted(available - applied)
    if unknown or pending:
        details = []
        if unknown:
            details.append("unknown applied identities: " + ", ".join(unknown))
        if pending:
            details.append("pending migrations: " + ", ".join(pending))
        raise RuntimeError(
            "Database schema is incompatible with this code; "
            "; ".join(details) + ". Run explicit init-db only after review."
        )


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply visible migrations only when called through an explicit action."""

    from .repository_backend import is_postgresql
    if is_postgresql(conn):
        raise RuntimeError("Use the explicit PostgreSQL schema/admin setup command; runtime roles cannot migrate")
    if conn.execute("PRAGMA query_only").fetchone()[0]:
        raise RuntimeError("Cannot apply migrations through a read-only connection")
    with _migration_lock(conn):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at_utc TEXT NOT NULL)"
        )
        applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
        unknown = sorted(applied - set(_migration_names()) - HISTORICAL_MIGRATION_IDENTITIES)
        if unknown:
            raise RuntimeError(
                "Cannot migrate with unknown applied identities: " + ", ".join(unknown)
            )
        for migration in sorted(Path(MIGRATIONS_PATH).glob("*.sql")):
            if migration.name in applied:
                continue
            with conn:
                conn.executescript(migration.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (?, ?)",
                    (migration.name, datetime.now(timezone.utc).isoformat()),
                )


def init_db(conn: sqlite3.Connection, *, apply_migrations: bool = False) -> None:
    """Compatibility facade: check by default, migrate only with explicit opt-in."""

    if apply_migrations:
        apply_migrations_to_db(conn)
    else:
        check_schema_compatibility(conn)


def apply_migrations_to_db(conn: sqlite3.Connection) -> None:
    """Named internal target to avoid shadowing the public keyword argument."""

    apply_migrations(conn)
