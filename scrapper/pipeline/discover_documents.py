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
    from ..workflows.documents.discovery import discover_documents as _owner
    return _owner(symbol=symbol, manifest_path=manifest_path, dry_run=dry_run, db_path=db_path, snapshot_root=snapshot_root, observed_at_utc=observed_at_utc, build_discovery_plan=build_discovery_plan, connect=connect, init_db=init_db, persist_discovery_plan=persist_discovery_plan)
