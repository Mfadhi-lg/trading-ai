"""Offline integrity, deduplication, host, and storage audit for one DA scope."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import statistics
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from ..config import DB_PATH, assert_configured_writable_targets
from ..db.database import connect, init_db


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _signature(path: Path, media_type: str) -> bool:
    with path.open("rb") as handle:
        head = handle.read(4096)
    if media_type == "application/pdf":
        return b"%PDF-" in head[:1024]
    if media_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
            return "[Content_Types].xml" in names and "ppt/presentation.xml" in names
        except (OSError, zipfile.BadZipFile):
            return False
    if media_type == "application/vnd.ms-powerpoint":
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if media_type in {"audio/mpeg"}:
        return head.startswith(b"ID3") or (
            len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
        )
    if media_type in {"audio/wav", "video/x-msvideo"}:
        return head.startswith(b"RIFF")
    if media_type in {"audio/mp4", "video/mp4", "video/quicktime"}:
        return len(head) >= 12 and head[4:8] == b"ftyp"
    if media_type in {"audio/ogg", "video/ogg"}:
        return head.startswith(b"OggS")
    if media_type == "audio/flac":
        return head.startswith(b"fLaC")
    if media_type == "video/webm":
        return head.startswith(b"\x1aE\xdf\xa3")
    if media_type == "video/mpeg":
        return head.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"))
    return bool(head)


def _scope_rows(conn: sqlite3.Connection, scope_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT artifact.artifact_id, artifact.company_id,
                  artifact.artifact_type, content.content_id, content.blob_id,
                  content.content_sha256, content.byte_size, content.media_type,
                  content.extraction_status, blob.local_path,
                  content.content_id=artifact.preferred_content_id AS is_preferred
             FROM artifact_download_scope_artifacts AS selected
             JOIN document_artifacts AS artifact USING (artifact_id)
             JOIN artifact_contents AS content USING (artifact_id)
             JOIN response_blobs AS blob USING (blob_id)
            WHERE selected.scope_id=?
            ORDER BY artifact.artifact_id, content.content_id""",
        (scope_id,),
    ).fetchall()


def audit_scope(
    conn: sqlite3.Connection, *, scope_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = _scope_rows(conn, scope_id)
    audited: list[dict[str, Any]] = []
    for row in rows:
        path = Path(str(row["local_path"]))
        exists = path.is_file()
        actual_size = path.stat().st_size if exists else None
        actual_sha = _sha256(path) if exists else None
        signature_valid = _signature(path, str(row["media_type"])) if exists else False
        audited.append(
            {
                "artifact_id": int(row["artifact_id"]),
                "company_id": int(row["company_id"]),
                "artifact_type": str(row["artifact_type"]),
                "content_id": int(row["content_id"]),
                "blob_id": int(row["blob_id"]),
                "media_type": str(row["media_type"]),
                "db_path": str(path),
                "db_byte_size": int(row["byte_size"]),
                "actual_byte_size": actual_size,
                "db_sha256": str(row["content_sha256"]),
                "actual_sha256": actual_sha,
                "file_exists": exists,
                "size_matches": exists and actual_size == int(row["byte_size"]),
                "sha256_matches": exists and actual_sha == str(row["content_sha256"]),
                "media_signature_valid": signature_valid,
                "is_preferred": bool(row["is_preferred"]),
                "extraction_status": str(row["extraction_status"]),
            }
        )
    failed = [
        item
        for item in audited
        if not (
            item["file_exists"]
            and item["size_matches"]
            and item["sha256_matches"]
            and item["media_signature_valid"]
        )
    ]
    distinct_blobs: dict[int, int] = {}
    for item in audited:
        distinct_blobs[item["blob_id"]] = item["db_byte_size"]
    logical_bytes = sum(item["db_byte_size"] for item in audited)
    physical_bytes = sum(distinct_blobs.values())
    by_artifact: dict[int, dict[str, Any]] = {}
    for item in audited:
        existing = by_artifact.get(item["artifact_id"])
        if existing is None or item["is_preferred"]:
            by_artifact[item["artifact_id"]] = item
    preferred = list(by_artifact.values())
    by_type: dict[str, dict[str, Any]] = {}
    for artifact_type in sorted({item["artifact_type"] for item in preferred}):
        type_rows = [item for item in preferred if item["artifact_type"] == artifact_type]
        sizes = [item["db_byte_size"] for item in type_rows]
        type_blob_sizes = {
            item["blob_id"]: item["db_byte_size"] for item in type_rows
        }
        validated = sum(
            item["file_exists"]
            and item["size_matches"]
            and item["sha256_matches"]
            and item["media_signature_valid"]
            for item in type_rows
        )
        by_type[artifact_type] = {
            "count": len(type_rows),
            "validated_count": validated,
            "total_bytes": sum(sizes),
            "mean_bytes": round(statistics.mean(sizes), 2) if sizes else None,
            "median_bytes": _percentile(sizes, 0.5),
            "p90_bytes": _percentile(sizes, 0.9),
            "p95_bytes": _percentile(sizes, 0.95),
            "max_bytes": max(sizes) if sizes else None,
            "deduplicated_physical_bytes": sum(type_blob_sizes.values()),
            "duplicate_savings_bytes": sum(sizes) - sum(type_blob_sizes.values()),
        }
    queue = {
        str(row["status"]): int(row["count"])
        for row in conn.execute(
            """SELECT status,COUNT(*) AS count FROM artifact_download_queue
                WHERE scope_id=? GROUP BY status ORDER BY status""",
            (scope_id,),
        )
    }
    production_dedup = conn.execute(
        """SELECT COUNT(*) AS logical_attachments,
                  COUNT(DISTINCT content.blob_id) AS unique_physical_blobs,
                  COALESCE(SUM(content.byte_size),0) AS logical_bytes
             FROM artifact_contents AS content
             JOIN document_artifacts AS artifact USING (artifact_id)
            WHERE artifact.artifact_type IN
                  ('transcript','presentation','credit_rating')"""
    ).fetchone()
    production_physical_bytes = int(
        conn.execute(
            """SELECT COALESCE(SUM(byte_size),0) FROM (
                 SELECT content.blob_id,MAX(content.byte_size) AS byte_size
                   FROM artifact_contents AS content
                   JOIN document_artifacts AS artifact USING (artifact_id)
                  WHERE artifact.artifact_type IN
                        ('transcript','presentation','credit_rating')
                  GROUP BY content.blob_id
               )"""
        ).fetchone()[0]
    )
    production_duplicate_examples = [
        dict(row)
        for row in conn.execute(
            """SELECT content.blob_id,blob.content_sha256,blob.local_path,
                      COUNT(DISTINCT content.artifact_id) AS logical_artifacts,
                      GROUP_CONCAT(DISTINCT content.artifact_id) AS artifact_ids,
                      MAX(content.byte_size) AS physical_bytes
                 FROM artifact_contents AS content
                 JOIN document_artifacts AS artifact USING (artifact_id)
                 JOIN response_blobs AS blob USING (blob_id)
                WHERE artifact.artifact_type IN
                      ('transcript','presentation','credit_rating')
                GROUP BY content.blob_id
               HAVING COUNT(DISTINCT content.artifact_id)>1
                ORDER BY logical_artifacts DESC,content.blob_id LIMIT 20"""
        )
    ]
    report = {
        "scope_id": scope_id,
        "integrity": {
            "content_relationship_count": len(audited),
            "verified_count": len(audited) - len(failed),
            "failed_count": len(failed),
            "failed_content_ids": [item["content_id"] for item in failed],
            "pass": not failed and bool(audited),
        },
        "deduplication": {
            "logical_attachments": len(audited),
            "unique_physical_blobs": len(distinct_blobs),
            "duplicate_logical_attachments": len(audited) - len(distinct_blobs),
            "logical_bytes": logical_bytes,
            "deduplicated_physical_bytes": physical_bytes,
            "bytes_saved": logical_bytes - physical_bytes,
            "proven": len(audited) > len(distinct_blobs),
        },
        "production_natural_deduplication": {
            "logical_attachments": int(production_dedup["logical_attachments"]),
            "unique_physical_blobs": int(production_dedup["unique_physical_blobs"]),
            "duplicate_logical_attachments": int(production_dedup["logical_attachments"])
            - int(production_dedup["unique_physical_blobs"]),
            "logical_bytes": int(production_dedup["logical_bytes"]),
            "deduplicated_physical_bytes": production_physical_bytes,
            "bytes_saved": int(production_dedup["logical_bytes"])
            - production_physical_bytes,
            "proven": int(production_dedup["logical_attachments"])
            > int(production_dedup["unique_physical_blobs"]),
            "examples": production_duplicate_examples,
        },
        "by_artifact_type": by_type,
        "queue_status_counts": queue,
    }
    return audited, report


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# P0 Storage and Integrity Report",
        "",
        f"Scope: `{report['scope_id']}`",
        "",
        f"Integrity: **{'PASS' if report['integrity']['pass'] else 'FAIL'}**",
        f"Verified contents: {report['integrity']['verified_count']} / {report['integrity']['content_relationship_count']}",
        "",
        "## Deduplication",
        "",
        f"- Logical attachments: {report['deduplication']['logical_attachments']}",
        f"- Unique physical blobs: {report['deduplication']['unique_physical_blobs']}",
        f"- Duplicate logical attachments: {report['deduplication']['duplicate_logical_attachments']}",
        f"- Bytes saved: {report['deduplication']['bytes_saved']}",
        "",
        "## Production natural duplicate proof",
        "",
        f"- Logical attachments: {report['production_natural_deduplication']['logical_attachments']}",
        f"- Unique physical blobs: {report['production_natural_deduplication']['unique_physical_blobs']}",
        f"- Duplicate logical attachments: {report['production_natural_deduplication']['duplicate_logical_attachments']}",
        f"- Bytes saved: {report['production_natural_deduplication']['bytes_saved']}",
        f"- Proven: {report['production_natural_deduplication']['proven']}",
        "",
        "## By artifact type",
        "",
    ]
    for artifact_type, values in report["by_artifact_type"].items():
        lines.extend(
            [
                f"### {artifact_type}",
                "",
                *[f"- {key}: {value}" for key, value in values.items()],
                "",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    assert_configured_writable_targets(args.output_dir, db_path=args.db)
    conn = connect(args.db)
    try:
        init_db(conn)
        audited, report = audit_scope(conn, scope_id=args.scope_id)
    finally:
        conn.close()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "06_P0_CONTENT_INTEGRITY.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(audited[0]) if audited else ["content_id"])
        writer.writeheader()
        writer.writerows(audited)
    (args.output_dir / "08_P0_STORAGE_REPORT.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_markdown(report, args.output_dir / "08_P0_STORAGE_REPORT.md")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["integrity"]["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
