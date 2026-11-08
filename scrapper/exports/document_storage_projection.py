"""DA-08 observed document-size statistics and scoped storage projection."""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from ..config import DOCUMENT_DOWNLOAD_EXPORTS, DOCUMENT_STORE


STORAGE_PROJECTION_SCHEMA_VERSION = "1.0.0"


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * (
        position - lower
    )
    return int(round(interpolated))


def _size_statistics(values: list[int]) -> dict[str, int | float | None]:
    if not values:
        return {
            "sample_count": 0,
            "mean_bytes": None,
            "median_bytes": None,
            "p90_bytes": None,
            "p95_bytes": None,
            "max_bytes": None,
        }
    return {
        "sample_count": len(values),
        "mean_bytes": round(sum(values) / len(values), 2),
        "median_bytes": _percentile(values, 0.5),
        "p90_bytes": _percentile(values, 0.9),
        "p95_bytes": _percentile(values, 0.95),
        "max_bytes": max(values),
    }


def build_document_storage_projection(
    conn: sqlite3.Connection,
    *,
    scope_id: str,
    planned_target_counts: Mapping[str, int] | None = None,
    headroom_percent: float = 25.0,
    document_store: Path = DOCUMENT_STORE,
) -> dict[str, Any]:
    """Project storage from actual, deduplicated content in one explicit scope."""

    if not 0 <= headroom_percent <= 100:
        raise ValueError("headroom_percent must be between 0 and 100")
    scope = conn.execute(
        "SELECT * FROM artifact_download_scopes WHERE scope_id=?", (scope_id,)
    ).fetchone()
    if scope is None:
        raise ValueError(f"Unknown artifact download scope: {scope_id}")
    planned = {str(key): int(value) for key, value in (planned_target_counts or {}).items()}
    if any(value < 0 for value in planned.values()):
        raise ValueError("Planned target counts cannot be negative")

    target_counts = Counter(
        {
            str(row["artifact_type"]): int(row["count"])
            for row in conn.execute(
                """SELECT artifact.artifact_type, COUNT(DISTINCT artifact.artifact_id) AS count
                     FROM artifact_download_scope_artifacts AS selected
                     JOIN document_artifacts AS artifact USING (artifact_id)
                    WHERE selected.scope_id=?
                    GROUP BY artifact.artifact_type""",
                (scope_id,),
            )
        }
    )
    if not target_counts:
        target_counts.update(
            {
                str(row["artifact_type"]): int(row["count"])
                for row in conn.execute(
                    """SELECT artifact.artifact_type,
                              COUNT(DISTINCT queue.artifact_id) AS count
                         FROM artifact_download_queue AS queue
                         JOIN document_artifacts AS artifact USING (artifact_id)
                        WHERE queue.scope_id=?
                        GROUP BY artifact.artifact_type""",
                    (scope_id,),
                )
            }
        )

    content_rows = conn.execute(
        """WITH ranked AS (
             SELECT artifact.artifact_id, artifact.artifact_type,
                    content.content_id, content.blob_id, content.byte_size,
                    ROW_NUMBER() OVER (
                      PARTITION BY artifact.artifact_id
                      ORDER BY (content.content_id=artifact.preferred_content_id) DESC,
                               content.is_official DESC, content.content_id DESC
                    ) AS position
               FROM artifact_download_scope_artifacts AS selected
               JOIN document_artifacts AS artifact USING (artifact_id)
               JOIN artifact_contents AS content USING (artifact_id)
              WHERE selected.scope_id=?
           )
           SELECT * FROM ranked WHERE position=1 ORDER BY artifact_id""",
        (scope_id,),
    ).fetchall()
    sizes: dict[str, list[int]] = {}
    covered_counts: Counter[str] = Counter()
    for row in content_rows:
        artifact_type = str(row["artifact_type"])
        sizes.setdefault(artifact_type, []).append(int(row["byte_size"]))
        covered_counts[artifact_type] += 1

    queue_rows = conn.execute(
        """SELECT artifact.artifact_type, queue.status, COUNT(*) AS count
             FROM artifact_download_queue AS queue
             JOIN document_artifacts AS artifact USING (artifact_id)
            WHERE queue.scope_id=?
            GROUP BY artifact.artifact_type, queue.status""",
        (scope_id,),
    ).fetchall()
    queue_by_type: dict[str, Counter[str]] = {}
    for row in queue_rows:
        queue_by_type.setdefault(str(row["artifact_type"]), Counter())[
            str(row["status"])
        ] = int(row["count"])

    artifact_types = sorted(set(target_counts) | set(sizes) | set(planned))
    by_type: dict[str, dict[str, Any]] = {}
    total_projected = 0
    for artifact_type in artifact_types:
        stats = _size_statistics(sizes.get(artifact_type, []))
        jobs = queue_by_type.get(artifact_type, Counter())
        attempted = sum(
            value
            for status, value in jobs.items()
            if status not in {"PENDING", "LEASED", "SKIPPED_POLICY"}
        )
        validated = jobs.get("SUCCEEDED", 0)
        planned_count = planned.get(artifact_type, target_counts.get(artifact_type, 0))
        mean = stats["mean_bytes"]
        projected = int(round(planned_count * float(mean))) if mean is not None else 0
        total_projected += projected
        by_type[artifact_type] = {
            "target_artifact_count": target_counts.get(artifact_type, 0),
            "covered_artifact_count": covered_counts.get(artifact_type, 0),
            "coverage_rate": (
                round(covered_counts.get(artifact_type, 0) / target_counts[artifact_type], 6)
                if target_counts.get(artifact_type, 0)
                else None
            ),
            "queue_job_count": sum(jobs.values()),
            "attempted_job_count": attempted,
            "validated_job_count": validated,
            "validated_rate": round(validated / attempted, 6) if attempted else None,
            "status_counts": dict(sorted(jobs.items())),
            "observed_sizes": stats,
            "planned_target_count": planned_count,
            "projected_bytes_before_headroom": projected,
        }
    projected_with_headroom = int(
        math.ceil(total_projected * (1 + headroom_percent / 100))
    )

    unique_blob_rows = conn.execute(
        """SELECT DISTINCT content.blob_id, content.byte_size
             FROM artifact_download_scope_artifacts AS selected
             JOIN artifact_contents AS content USING (artifact_id)
            WHERE selected.scope_id=?""",
        (scope_id,),
    ).fetchall()
    actual_unique_bytes = sum(int(row["byte_size"]) for row in unique_blob_rows)
    additional_bytes_required = max(projected_with_headroom - actual_unique_bytes, 0)
    store_path = document_store.resolve()
    disk = shutil.disk_usage(store_path) if store_path.exists() else None
    return {
        "schema_version": STORAGE_PROJECTION_SCHEMA_VERSION,
        "scope_id": scope_id,
        "scope_kind": scope["scope_kind"],
        "headroom_percent": headroom_percent,
        "by_artifact_type": by_type,
        "actual": {
            "unique_blob_count": len(unique_blob_rows),
            "unique_content_bytes": actual_unique_bytes,
        },
        "projection": {
            "bytes_before_headroom": total_projected,
            "bytes_with_headroom": projected_with_headroom,
            "additional_bytes_required": additional_bytes_required,
            "fits_current_free_space": (
                additional_bytes_required <= disk.free if disk is not None else None
            ),
        },
        "document_store": {
            "path": str(store_path),
            "exists": store_path.exists(),
            "total_bytes": disk.total if disk is not None else None,
            "used_bytes": disk.used if disk is not None else None,
            "free_bytes": disk.free if disk is not None else None,
        },
    }


def write_document_storage_projection(
    conn: sqlite3.Connection,
    *,
    scope_id: str,
    planned_target_counts: Mapping[str, int] | None = None,
    headroom_percent: float = 25.0,
    document_store: Path = DOCUMENT_STORE,
    output: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    payload = build_document_storage_projection(
        conn,
        scope_id=scope_id,
        planned_target_counts=planned_target_counts,
        headroom_percent=headroom_percent,
        document_store=document_store,
    )
    path = output or DOCUMENT_DOWNLOAD_EXPORTS / "storage" / f"{scope_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload, path
