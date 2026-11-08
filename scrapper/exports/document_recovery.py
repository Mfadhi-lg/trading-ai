"""Logical-document recovery report aggregation and file output."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import DOCUMENT_DOWNLOAD_EXPORTS
from ..db.documents.recovery.reports import document_recovery_jobs

RECOVERY_REPORT_SCHEMA_VERSION = "1.0.0"

def _suggested_action(status: str, result: str | None, error: str | None) -> str:
    error = error or ""
    if "Identical content is already attached" in error:
        return "RETRY_AFTER_CONTENT_DEDUP_FIX"
    if status == "SKIPPED_POLICY":
        return "MANUAL_OR_LICENSED_ACQUISITION"
    if status == "INTERACTION_REQUIRED":
        return "HOST_ADAPTER_OR_AUDITED_MANUAL_IMPORT"
    if result == "rejected_unexpected_html":
        return "RESOLVE_LANDING_PAGE"
    if result == "rejected_http_status":
        if "http_404" in error or "http_410" in error:
            return "REDISCOVER_REPLACEMENT_URL"
        if "http_401" in error or "http_403" in error:
            return "REVIEW_HOST_ACCESS"
        return "AUTOMATIC_RETRY"
    if result in {
        "network_error",
        "timed_out",
        "RETRYABLE_RESOLUTION_FAILURE",
        "WORKER_EXCEPTION",
        "LEASE_EXPIRED",
    }:
        return "AUTOMATIC_RETRY"
    return "INSPECT_CONTENT_OR_SOURCE"


def build_document_recovery_report(
    conn: sqlite3.Connection,
    *,
    scope_id: str | None = None,
    include_pending: bool = False,
    _hooks=None,
) -> dict[str, Any]:
    """Report jobs needing recovery without treating URL rows as documents."""

    _suggested_action = getattr(_hooks, "_suggested_action", globals()["_suggested_action"])
    RECOVERY_REPORT_SCHEMA_VERSION = getattr(_hooks, "RECOVERY_REPORT_SCHEMA_VERSION", globals()["RECOVERY_REPORT_SCHEMA_VERSION"])
    rows = document_recovery_jobs(conn, scope_id=scope_id, include_pending=include_pending)

    actionable: list[dict[str, Any]] = []
    job_states: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    logical: dict[int, dict[str, Any]] = {}
    for row in rows:
        row["covered"] = bool(row["covered"])
        action = (
            "NONE_CONTENT_ALREADY_PRESENT"
            if row["covered"]
            else _suggested_action(
                str(row["status"]), row["last_result_status"], row["last_error"]
            )
        )
        row["suggested_action"] = action
        job_states[str(row["status"])] += 1
        action_counts[action] += 1
        artifact_id = int(row["artifact_id"])
        item = logical.setdefault(
            artifact_id,
            {
                "artifact_id": artifact_id,
                "artifact_type": row["artifact_type"],
                "symbol": row["symbol"],
                "company": row["current_legal_name"],
                "covered": row["covered"],
                "job_count": 0,
                "statuses": set(),
                "suggested_actions": set(),
            },
        )
        item["covered"] = bool(item["covered"] or row["covered"])
        item["job_count"] += 1
        item["statuses"].add(row["status"])
        item["suggested_actions"].add(action)
        actionable.append(row)

    logical_items = []
    for item in logical.values():
        item["statuses"] = sorted(item["statuses"])
        item["suggested_actions"] = sorted(item["suggested_actions"])
        logical_items.append(item)
    logical_items.sort(key=lambda item: item["artifact_id"])
    return {
        "schema_version": RECOVERY_REPORT_SCHEMA_VERSION,
        "scope_id": scope_id,
        "include_pending": include_pending,
        "summary": {
            "job_count": len(actionable),
            "logical_artifact_count": len(logical_items),
            "covered_artifact_count": sum(item["covered"] for item in logical_items),
            "uncovered_artifact_count": sum(not item["covered"] for item in logical_items),
            "job_status_counts": dict(sorted(job_states.items())),
            "suggested_action_counts": dict(sorted(action_counts.items())),
        },
        "logical_artifacts": logical_items,
        "jobs": actionable,
    }


def write_document_recovery_report(
    conn: sqlite3.Connection,
    *,
    scope_id: str | None = None,
    include_pending: bool = False,
    output: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    payload = build_document_recovery_report(
        conn, scope_id=scope_id, include_pending=include_pending
    )
    name = scope_id or "all-queues"
    path = output or DOCUMENT_DOWNLOAD_EXPORTS / "recovery" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload, path

