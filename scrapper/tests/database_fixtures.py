"""Explicit schema preparation for disposable workflow test databases."""

from pathlib import Path
from scrapper.db.database import connect, init_db

def prepare_database(path: Path) -> None:
    """Prepare an absent fixture DB before running a workflow; retain existing DBs."""
    if Path(path).exists():
        return
    conn = connect(path)
    try:
        init_db(conn, apply_migrations=True)
    finally:
        conn.close()
