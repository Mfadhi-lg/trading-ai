"""SQLite connection and ordered migration application."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..config import DB_PATH, MIGRATIONS_PATH, ensure_dirs


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at_utc TEXT NOT NULL)"
    )
    applied = {
        row["version"]
        for row in conn.execute("SELECT version FROM schema_migrations")
    }
    for migration in sorted(Path(MIGRATIONS_PATH).glob("*.sql")):
        if migration.name in applied:
            continue
        with conn:
            conn.executescript(migration.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (?, ?)",
                (migration.name, datetime.now(timezone.utc).isoformat()),
            )
