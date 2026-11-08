"""Build final operational, policy, integrity, and storage reports for DA."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import statistics
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..config import DB_PATH, DOCUMENT_MIN_FREE_BYTES, DOCUMENT_MIN_FREE_PERCENT, DOCUMENT_STORE, assert_configured_writable_targets
from ..core.access_policy import BLOCKED_TERMS_HOSTS, host_matches_policy
from ..db.database import connect, init_db


HIGH_VALUE_TYPES = ("transcript", "presentation", "credit_rating")


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


def _stats(values: list[int]) -> dict[str, Any]:
    return {
        "count": len(values),
        "total_bytes": sum(values),
        "mean_bytes": round(statistics.mean(values), 2) if values else None,
        "median_bytes": _percentile(values, 0.5),
        "p95_bytes": _percentile(values, 0.95),
        "max_bytes": max(values) if values else None,
    }


def _write_pair(output_dir: Path, stem: str, payload: dict[str, Any], title: str) -> None:
    (output_dir / f"{stem}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [f"# {title}", ""]
    for key, value in payload.items():
        if isinstance(value, dict):
            lines.extend([f"## {key.replace('_', ' ').title()}", ""])
            for nested_key, nested_value in value.items():
                if not isinstance(nested_value, (dict, list)):
                    lines.append(f"- {nested_key}: {nested_value}")
            lines.append("")
        elif not isinstance(value, list):
            lines.append(f"- {key}: {value}")
    (output_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def production_download_report(conn: Any, *, scope_id: str, run_start: str) -> dict[str, Any]:
    queue = [
        dict(row)
        for row in conn.execute(
            """SELECT artifact.artifact_type,queue.status,COUNT(*) AS count
                 FROM artifact_download_queue AS queue
                 JOIN document_artifacts AS artifact USING (artifact_id)
                WHERE queue.scope_id=? GROUP BY artifact.artifact_type,queue.status
                ORDER BY artifact.artifact_type,queue.status""",
            (scope_id,),
        )
    ]
    attempts = conn.execute(
        """SELECT COUNT(*) AS requests,
                  SUM(attempt.result_status='validated') AS validated,
                  COALESCE(SUM(attempt.byte_size),0) AS response_bytes,
                  COALESCE(SUM(CASE WHEN attempt.result_status='validated'
                                    THEN attempt.byte_size ELSE 0 END),0) AS validated_bytes
             FROM artifact_download_attempts AS attempt
             JOIN artifact_download_scope_artifacts AS selected USING (artifact_id)
            WHERE selected.scope_id=? AND attempt.started_at_utc>=?""",
        (scope_id, run_start),
    ).fetchone()
    new_contents = [
        dict(row)
        for row in conn.execute(
            """SELECT artifact.artifact_type,COUNT(*) AS count,
                      COALESCE(SUM(content.byte_size),0) AS bytes
                 FROM artifact_contents AS content
                 JOIN document_artifacts AS artifact USING (artifact_id)
                 JOIN artifact_download_scope_artifacts AS selected USING (artifact_id)
                WHERE selected.scope_id=? AND content.created_at_utc>=?
                GROUP BY artifact.artifact_type ORDER BY artifact.artifact_type""",
            (scope_id, run_start),
        )
    ]
    return {
        "scope_id": scope_id,
        "run_start_utc": run_start,
        "queue_by_type_and_state": queue,
        "attempts": dict(attempts),
        "new_contents_by_type": new_contents,
    }


def host_policy_report(conn: Any, *, scope_id: str, run_start: str) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT attempt.*
             FROM artifact_download_attempts AS attempt
             JOIN artifact_download_scope_artifacts AS selected USING (artifact_id)
            WHERE selected.scope_id=? AND attempt.started_at_utc>=?
            ORDER BY attempt.download_attempt_id""",
        (scope_id, run_start),
    ).fetchall()
    hosts: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "requests": 0,
            "success": 0,
            "403": 0,
            "404": 0,
            "429": 0,
            "5xx": 0,
            "timeouts": 0,
            "bytes": 0,
            "latencies_ms": [],
            "retry_after_responses": 0,
        }
    )
    blocked_counts = Counter({"crisil.com": 0, "crisilratings.com": 0, "icra.in": 0})
    for row in rows:
        host = (urlsplit(str(row["requested_url"])).hostname or "unknown").lower().rstrip(".")
        item = hosts[host]
        item["requests"] += 1
        item["success"] += int(row["result_status"] == "validated")
        status = int(row["http_status"]) if row["http_status"] is not None else None
        for code in (403, 404, 429):
            item[str(code)] += int(status == code)
        item["5xx"] += int(status is not None and 500 <= status <= 599)
        item["timeouts"] += int(row["result_status"] == "timed_out")
        item["bytes"] += int(row["byte_size"] or 0)
        item["latencies_ms"].append(int(row["elapsed_ms"]))
        try:
            headers = json.loads(row["response_headers_json"])
        except (TypeError, json.JSONDecodeError):
            headers = {}
        item["retry_after_responses"] += int(
            any(str(key).lower() == "retry-after" for key in headers)
        )
        for blocked in blocked_counts:
            if host == blocked or host.endswith(f".{blocked}"):
                blocked_counts[blocked] += 1
    circuit_deferrals = {
        str(row["target_host"]): int(row["count"])
        for row in conn.execute(
            """SELECT target_host,COUNT(*) AS count FROM artifact_download_queue
                WHERE scope_id=? AND last_result_status='HOST_CIRCUIT_OPEN'
                GROUP BY target_host ORDER BY target_host""",
            (scope_id,),
        )
    }
    output_hosts: dict[str, Any] = {}
    for host, item in sorted(hosts.items()):
        latencies = item.pop("latencies_ms")
        output_hosts[host] = {
            **item,
            "success_rate": round(item["success"] / item["requests"], 6),
            "median_latency_ms": _percentile(latencies, 0.5),
            "circuit_deferred_jobs": circuit_deferrals.get(host, 0),
        }
    blocked_total = sum(blocked_counts.values())
    return {
        "scope_id": scope_id,
        "run_start_utc": run_start,
        "total_requests": len(rows),
        "hosts": output_hosts,
        "blocked_cra_requests": dict(blocked_counts),
        "blocked_cra_requests_total": blocked_total,
        "blocked_host_policy_pass": blocked_total == 0,
        "circuit_deferred_jobs_by_host": circuit_deferrals,
    }


def recording_report(conn: Any, *, fallback_scope_id: str) -> dict[str, Any]:
    scope_exists = conn.execute(
        "SELECT 1 FROM artifact_download_scopes WHERE scope_id=?", (fallback_scope_id,)
    ).fetchone() is not None
    fallback_artifacts = int(
        conn.execute(
            "SELECT COUNT(*) FROM artifact_download_scope_artifacts WHERE scope_id=?",
            (fallback_scope_id,),
        ).fetchone()[0]
    ) if scope_exists else 0
    queue = {
        str(row["status"]): int(row["count"])
        for row in conn.execute(
            """SELECT status,COUNT(*) AS count FROM artifact_download_queue
                WHERE scope_id=? GROUP BY status ORDER BY status""",
            (fallback_scope_id,),
        )
    } if scope_exists else {}
    acquired = int(
        conn.execute(
            """SELECT COUNT(DISTINCT content.artifact_id)
                 FROM artifact_contents AS content
                 JOIN document_artifacts AS artifact USING (artifact_id)
                WHERE artifact.artifact_type='recording'
                  AND content.content_origin='publisher_original'"""
        ).fetchone()[0]
    )
    normalizations = {
        str(row["normalization_status"]): int(row["count"])
        for row in conn.execute(
            """SELECT normalization_status,COUNT(*) AS count
                 FROM recording_normalizations GROUP BY normalization_status"""
        )
    }
    transcriptions = {
        str(row["run_status"]): int(row["count"])
        for row in conn.execute(
            """SELECT run_status,COUNT(*) AS count
                 FROM machine_transcription_runs GROUP BY run_status"""
        )
    }
    return {
        "fallback_scope_id": fallback_scope_id,
        "fallback_artifacts": fallback_artifacts,
        "queue_states": queue,
        "recording_artifacts_with_source_content": acquired,
        "normalization_states": normalizations,
        "transcription_states": transcriptions,
    }


def _hash(path: Path) -> str:
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
    if media_type == "text/html":
        lowered = head.lstrip().lower()
        return b"<!doctype html" in lowered or b"<html" in lowered
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


def integrity_and_storage(
    conn: Any, *, document_store: Path, sample_size: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = conn.execute(
        """SELECT content.content_id,content.artifact_id,artifact.artifact_type,
                  content.blob_id,content.content_sha256,content.byte_size,
                  content.media_type,content.content_origin,blob.local_path
             FROM artifact_contents AS content
             JOIN document_artifacts AS artifact USING (artifact_id)
             JOIN response_blobs AS blob USING (blob_id)
            WHERE artifact.artifact_type IN
                  ('transcript','presentation','credit_rating','recording')
            ORDER BY content.content_sha256,content.content_id"""
    ).fetchall()
    missing: list[int] = []
    size_mismatch: list[int] = []
    signature_failures: list[int] = []
    for row in rows:
        path = Path(str(row["local_path"]))
        if not path.is_file():
            missing.append(int(row["content_id"]))
        elif path.stat().st_size != int(row["byte_size"]):
            size_mismatch.append(int(row["content_id"]))
        elif not _signature(path, str(row["media_type"])):
            signature_failures.append(int(row["content_id"]))
    sample: list[Any] = []
    if rows:
        count = min(sample_size, len(rows))
        indexes = sorted({round(index * (len(rows) - 1) / max(count - 1, 1)) for index in range(count)})
        sample = [rows[index] for index in indexes]
    hash_failures: list[int] = []
    for row in sample:
        path = Path(str(row["local_path"]))
        if path.is_file() and _hash(path) != str(row["content_sha256"]):
            hash_failures.append(int(row["content_id"]))
    expected_external = {
        str(Path(str(row["local_path"])).resolve())
        for row in conn.execute("SELECT local_path FROM response_blobs")
        if str(row["local_path"]).startswith(str(document_store))
    }
    content_root = document_store / "artifact_content"
    actual_external = {
        str(path.resolve())
        for path in content_root.rglob("*")
        if path.is_file()
    } if content_root.is_dir() else set()
    temporary = sorted(
        path for path in actual_external
        if Path(path).name.endswith(".tmp") or ".recording-tmp" in path
    )
    orphans = sorted(actual_external - expected_external - set(temporary))
    referenced_missing = sorted(expected_external - actual_external)
    blob_sizes: dict[int, int] = {}
    logical_bytes = 0
    by_type_values: dict[str, list[int]] = defaultdict(list)
    by_type_blobs: dict[str, dict[int, int]] = defaultdict(dict)
    for row in rows:
        size = int(row["byte_size"])
        blob_id = int(row["blob_id"])
        artifact_type = str(row["artifact_type"])
        logical_bytes += size
        blob_sizes[blob_id] = size
        by_type_values[artifact_type].append(size)
        by_type_blobs[artifact_type][blob_id] = size
    by_type: dict[str, Any] = {}
    for artifact_type, values in sorted(by_type_values.items()):
        physical = sum(by_type_blobs[artifact_type].values())
        by_type[artifact_type] = {
            **_stats(values),
            "deduplicated_physical_bytes": physical,
            "duplicate_savings_bytes": sum(values) - physical,
        }
    usage = shutil.disk_usage(document_store)
    reserve = max(
        DOCUMENT_MIN_FREE_BYTES,
        int(usage.total * (DOCUMENT_MIN_FREE_PERCENT / 100.0)),
    )
    physical = sum(blob_sizes.values())
    integrity = {
        "content_relationships_checked": len(rows),
        "existence_and_size_checked": len(rows),
        "missing_content_ids": missing,
        "size_mismatch_content_ids": size_mismatch,
        "media_signature_failure_content_ids": signature_failures,
        "hash_sample_size": len(sample),
        "hash_failure_content_ids": hash_failures,
        "orphan_physical_files": orphans,
        "orphan_db_content_paths": referenced_missing,
        "temporary_files": temporary,
        "pass": not missing and not size_mismatch and not signature_failures
        and not hash_failures and not referenced_missing,
    }
    storage = {
        "document_store": str(document_store),
        "disk_total_bytes": usage.total,
        "disk_used_bytes": usage.used,
        "disk_free_bytes": usage.free,
        "reserved_bytes": reserve,
        "usable_above_reserve_bytes": max(0, usage.free - reserve),
        "logical_content_bytes": logical_bytes,
        "deduplicated_physical_bytes": physical,
        "duplicate_savings_bytes": logical_bytes - physical,
        "by_artifact_type": by_type,
        "temporary_file_count": len(temporary),
        "orphan_physical_file_count": len(orphans),
        "hash_verification_pass": not hash_failures,
        "reserve_respected": usage.free >= reserve,
    }
    return integrity, storage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--scope-id", default="document-acquisition-full-v1")
    parser.add_argument(
        "--fallback-scope-id", default="document-acquisition-recording-fallback-v1"
    )
    parser.add_argument("--run-start", required=True)
    parser.add_argument("--document-store", type=Path, default=DOCUMENT_STORE)
    parser.add_argument("--hash-sample-size", type=int, default=1000)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    assert_configured_writable_targets(args.output_dir, db_path=args.db)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(args.db)
    try:
        init_db(conn)
        production = production_download_report(
            conn, scope_id=args.scope_id, run_start=args.run_start
        )
        host = host_policy_report(conn, scope_id=args.scope_id, run_start=args.run_start)
        recording = recording_report(conn, fallback_scope_id=args.fallback_scope_id)
        integrity, storage = integrity_and_storage(
            conn,
            document_store=args.document_store,
            sample_size=args.hash_sample_size,
        )
    finally:
        conn.close()
    _write_pair(args.output_dir, "10_PRODUCTION_DOWNLOAD_REPORT", production, "Production Download Report")
    _write_pair(args.output_dir, "14_RECORDING_ACQUISITION_REPORT", recording, "Recording Acquisition Report")
    _write_pair(args.output_dir, "15_FINAL_CORPUS_INTEGRITY", integrity, "Final Corpus Integrity")
    _write_pair(args.output_dir, "17_HOST_POLICY_AUDIT", host, "Host Policy Audit")
    _write_pair(args.output_dir, "18_STORAGE_FINAL", storage, "Final External Storage Report")
    print(json.dumps({
        "blocked_host_policy_pass": host["blocked_host_policy_pass"],
        "integrity_pass": integrity["pass"],
        "reserve_respected": storage["reserve_respected"],
    }, sort_keys=True))
    return 0 if host["blocked_host_policy_pass"] and integrity["pass"] and storage["reserve_respected"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
