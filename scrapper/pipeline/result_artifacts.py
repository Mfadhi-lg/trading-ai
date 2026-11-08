"""Event-backed document requirements for accepted exchange results."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..config import DB_PATH
from ..db.artifact_reconciliation_repo import backfill_result_artifact_expectations
from ..db.database import connect, init_db
from ..db.repo import utc_now


def backfill_accepted_result_artifacts(
    *,
    db_path: Path = DB_PATH,
    limit: int | None = None,
    now_utc: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    """Create idempotent document expectations for accepted official results."""
    created_at = now_utc()
    conn = connect(db_path)
    try:
        init_db(conn)
        summary = backfill_result_artifact_expectations(
            conn, created_at_utc=created_at, limit=limit
        )
    finally:
        conn.close()
    return {
        "schema_version": "1.0.0",
        "created_at_utc": created_at,
        "summary": summary,
    }
