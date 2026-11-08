"""Extract downloaded presentation and transcript candidates for one quarter.

Only candidates in the eligible company inventory are processed. The command
records a compact outcome; the full extraction stays in the immutable store.
"""
import argparse
import json
import sqlite3
from pathlib import Path

from ..config import DB_PATH, assert_configured_writable_targets
from ..pipeline.extract_document import extract_document
from ..workflows.documents.quarter import build_inventory


def pending_contents(conn, *, period, limit):
    inventory = build_inventory(conn, period=period)
    candidates = sorted(
        (item for item in inventory["artifacts"]
         if item["status"] == "downloaded_needs_extraction"
         and item["artifact_type"] in ("presentation", "transcript")),
        key=lambda item: (item["period_basis"] != "tagged_quarter_requires_content_verification",
                          -item["artifact_id"]),
    )
    selected = []
    for item in candidates:
        row = conn.execute(
            """SELECT content.content_id,content.media_type FROM artifact_contents content
               JOIN document_artifacts artifact USING (artifact_id)
               WHERE content.artifact_id=? AND content.extraction_status='not_started'
               ORDER BY (content.content_id=artifact.preferred_content_id) DESC,
                        content.is_official DESC,content.content_id DESC LIMIT 1""",
            (item["artifact_id"],),
        ).fetchone()
        if row and row["media_type"] in ("application/pdf",
                                             "application/vnd.ms-powerpoint",
                                             "application/vnd.openxmlformats-officedocument.presentationml.presentation"):
            selected.append((item["artifact_id"], row["content_id"]))
            if len(selected) >= limit:
                break
    return selected, len(candidates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--period", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("Limit must be between 1 and 100")
    if args.apply:
        assert_configured_writable_targets(db_path=args.db)
    conn = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        selected, backlog = pending_contents(conn, period=args.period, limit=args.limit)
    finally:
        conn.close()
    if not args.apply:
        print(json.dumps({"period_end": args.period, "candidate_backlog": backlog,
                          "next_contents": selected}))
        return
    outcomes = []
    for artifact_id, content_id in selected:
        try:
            payload = extract_document(artifact_id=artifact_id, content_id=content_id,
                                       db_path=args.db)
        except Exception as exc:
            outcomes.append({"artifact_id": artifact_id, "content_id": content_id,
                             "status": "error", "error": f"{type(exc).__name__}: {exc}"})
            break
        outcomes.append({"artifact_id": artifact_id, "content_id": content_id,
                         "status": payload["extraction"]["extraction_status"]})
    print(json.dumps({"period_end": args.period, "candidate_backlog_before": backlog,
                      "processed": len(outcomes), "outcomes": outcomes}))
    if outcomes and outcomes[-1]["status"] == "error":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
