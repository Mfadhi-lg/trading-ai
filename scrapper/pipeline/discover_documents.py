"""D01 offline planning and persistence orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import DB_PATH, DOCUMENT_DISCOVERY_SNAPSHOTS
from ..db.database import connect, init_db
from ..db.document_artifact_repo import persist_discovery_plan
from ..sources.documents.discovery import build_discovery_plan


def discover_documents(
    *,
    symbol: str,
    manifest_path: Path,
    dry_run: bool,
    db_path: Path = DB_PATH,
    snapshot_root: Path = DOCUMENT_DISCOVERY_SNAPSHOTS.parent,
    observed_at_utc: str | None = None,
) -> dict[str, Any]:
    """Return every candidate; persist only when ``dry_run`` is false."""

    plan = build_discovery_plan(manifest_path, requested_symbol=symbol)
    payload = plan.to_dict(dry_run=dry_run)
    if dry_run:
        payload["persistence"] = {
            "status": "not_written",
            "database_exists": db_path.exists(),
        }
        return payload
    conn = connect(db_path)
    try:
        init_db(conn)
        result = persist_discovery_plan(
            conn,
            plan,
            observed_at_utc=observed_at_utc,
            snapshot_root=snapshot_root,
        )
    finally:
        conn.close()
    payload["persistence"] = {"status": "stored", **result.to_dict()}
    return payload
