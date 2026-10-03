"""Backfill portable PDF derivatives for validated publisher HTML content."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import DB_PATH, DOCUMENT_CONTENT_SNAPSHOTS, assert_configured_writable_targets
from ..db.database import connect, init_db
from ..db.html_document_rendering_repo import render_and_persist_html_pdf


def _source_url(conn: sqlite3.Connection, content_id: int, artifact_id: int) -> str:
    attempt = conn.execute(
        """SELECT final_url,requested_url FROM artifact_download_attempts
            WHERE content_id=? AND result_status='validated'
            ORDER BY download_attempt_id DESC LIMIT 1""",
        (content_id,),
    ).fetchone()
    if attempt is not None:
        return str(attempt["final_url"] or attempt["requested_url"])
    source = conn.execute(
        """SELECT document_url FROM artifact_sources WHERE artifact_id=?
            ORDER BY is_primary_source DESC,artifact_source_id LIMIT 1""",
        (artifact_id,),
    ).fetchone()
    if source is None:
        raise ValueError(f"Artifact {artifact_id} has no source URL for PDF metadata")
    return str(source["document_url"])


def render_html_document(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    source_content_id: int,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
) -> dict[str, Any]:
    rendered = render_and_persist_html_pdf(
        conn,
        artifact_id=artifact_id,
        source_content_id=source_content_id,
        source_url=_source_url(conn, source_content_id, artifact_id),
        snapshot_root=snapshot_root,
    )
    return {
        "artifact_id": rendered.artifact_id,
        "source_content_id": rendered.source_content_id,
        "rendered_content_id": rendered.rendered_content_id,
        "rendering_id": rendered.rendering_id,
        "reused": rendered.reused,
        "visible_character_count": rendered.visible_character_count,
        "block_count": rendered.block_count,
    }


def render_pending_html_documents(
    *,
    db_path: Path = DB_PATH,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
    scope_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Render validated original HTML rows that lack the current PDF derivative."""

    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    conn = connect(db_path)
    try:
        init_db(conn)
        scope_join = ""
        parameters: list[Any] = []
        if scope_id is not None:
            scope_join = (
                "JOIN artifact_download_scope_artifacts AS scoped "
                "ON scoped.artifact_id=content.artifact_id AND scoped.scope_id=?"
            )
            parameters.append(scope_id)
        limit_clause = " LIMIT ?" if limit is not None else ""
        if limit is not None:
            parameters.append(limit)
        rows = conn.execute(
            f"""SELECT content.artifact_id,content.content_id
                  FROM artifact_contents AS content
                  {scope_join}
                 WHERE content.media_type='text/html'
                   AND content.content_origin='publisher_original'
                   AND NOT EXISTS (
                     SELECT 1 FROM html_document_renderings AS rendering
                      WHERE rendering.source_content_id=content.content_id
                        AND rendering.renderer_name='validated-html-text-pdf'
                        AND rendering.renderer_version='1.0.0'
                   )
                 ORDER BY content.content_id{limit_clause}""",
            parameters,
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            try:
                results.append(
                    {
                        "status": "ok",
                        **render_html_document(
                            conn,
                            artifact_id=int(row["artifact_id"]),
                            source_content_id=int(row["content_id"]),
                            snapshot_root=snapshot_root,
                        ),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "status": "failed",
                        "artifact_id": int(row["artifact_id"]),
                        "source_content_id": int(row["content_id"]),
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    }
                )
        states = Counter(item["status"] for item in results)
        return {
            "scope_id": scope_id,
            "selected_count": len(rows),
            "rendered_count": states["ok"],
            "failed_count": states["failed"],
            "results": results,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--snapshot-root", type=Path, default=DOCUMENT_CONTENT_SNAPSHOTS)
    parser.add_argument("--scope-id")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    assert_configured_writable_targets(args.snapshot_root, db_path=args.db)
    report = render_pending_html_documents(
        db_path=args.db,
        snapshot_root=args.snapshot_root,
        scope_id=args.scope_id,
        limit=args.limit,
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["failed_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
