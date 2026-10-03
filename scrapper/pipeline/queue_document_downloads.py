"""DA-05 scope registration and idempotent queue materialization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import DB_PATH
from ..db.database import connect, init_db
from ..db.document_download_queue_repo import (
    materialize_download_queue,
    preview_download_queue,
)
from ..documents.download_scope import load_scope_manifest, register_download_scope


def queue_document_downloads(
    *,
    manifest_path: Path,
    db_path: Path = DB_PATH,
    priority: int = 100,
    dry_run: bool = False,
    enforce_pilot_composition: bool = True,
) -> dict[str, Any]:
    """Register a manifest-backed scope and materialize its URL jobs."""

    if priority < 0:
        raise ValueError("priority cannot be negative")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid download scope JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Download scope manifest must be a JSON object")
    definition = load_scope_manifest(raw)
    conn = connect(db_path)
    created_for_dry_run = False
    try:
        init_db(conn)
        existing_scope = conn.execute(
            "SELECT 1 FROM artifact_download_scopes WHERE scope_id=?",
            (definition["scope_id"],),
        ).fetchone()
        scope = register_download_scope(
            conn,
            **definition,
            enforce_pilot_composition=enforce_pilot_composition,
        )
        created_for_dry_run = dry_run and existing_scope is None and scope["created"]
        preview = preview_download_queue(conn, scope_id=scope["scope_id"])
        queue = (
            {**preview, "created_job_count": 0, "dry_run": True}
            if dry_run
            else {
                **materialize_download_queue(
                    conn, scope_id=scope["scope_id"], priority=priority
                ),
                "dry_run": False,
            }
        )
        return {"scope": scope, "preview": preview, "queue": queue}
    finally:
        if created_for_dry_run:
            with conn:
                conn.execute(
                    "DELETE FROM artifact_download_scopes WHERE scope_id=?",
                    (definition["scope_id"],),
                )
        conn.close()
