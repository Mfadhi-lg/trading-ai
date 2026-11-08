"""Exact residual/accounting report for DA-06 official-source recovery."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import DOCUMENT_RECOVERY_EXPORTS
from ..db.repo import utc_now


RECOVERY_REPORT_SCHEMA_VERSION = "1.0.0"


def _source_bucket(source_type: str | None) -> str:
    if source_type == "NSE_EXCHANGE_ATTACHMENT":
        return "NSE"
    if source_type == "BSE_EXCHANGE_ATTACHMENT":
        return "BSE"
    if source_type == "ISSUER_IR_ATTACHMENT":
        return "issuer_ir"
    if source_type in {"NSE_DEBT_DOCUMENT", "BSE_DEBT_DOCUMENT"}:
        return "debt_issue_document"
    return "other"


def build_alternate_source_recovery_report(
    conn: sqlite3.Connection, *, generated_at_utc: str | None = None
) -> dict[str, Any]:
    generated = generated_at_utc or utc_now()
    targets = conn.execute(
        """SELECT target.*,candidate.source_type,candidate.content_class
             FROM artifact_recovery_targets AS target
             LEFT JOIN artifact_recovery_candidates AS candidate
               ON candidate.candidate_id=target.resolved_candidate_id
            ORDER BY target.recovery_target_id"""
    ).fetchall()
    quarantine = conn.execute(
        "SELECT * FROM artifact_recovery_quarantine ORDER BY artifact_id"
    ).fetchall()
    status_counts = Counter(str(row["status"]) for row in targets)
    recovered = [row for row in targets if row["status"] == "RECOVERED"]
    full_by_source: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    for row in recovered:
        content_class = str(row["content_class"] or "UNKNOWN_RATING_DOCUMENT")
        class_counts[content_class] += 1
        if content_class in {
            "FULL_RATING_RATIONALE", "ISSUE_DOCUMENT_EMBEDDED_RATIONALE"
        }:
            full_by_source[_source_bucket(row["source_type"])] += 1

    already = status_counts["ALREADY_SATISFIED"]
    ambiguous = status_counts["AMBIGUOUS"]
    no_official = status_counts["NO_OFFICIAL_SOURCE_FOUND"]
    manual = (
        status_counts["LICENSE_OR_MANUAL_REQUIRED"]
        + status_counts["TERMINAL_FAILURE"]
        + len(quarantine)
    )
    in_progress_statuses = {
        "PENDING", "SEARCHING", "CANDIDATE_FOUND", "RETRYABLE_FAILURE"
    }
    in_progress = sum(status_counts[status] for status in in_progress_statuses)
    total = len(targets) + len(quarantine)
    accounted = already + len(recovered) + ambiguous + no_official + manual + in_progress
    if accounted != total:
        raise RuntimeError(
            f"Alternate-source recovery accounting mismatch: {accounted} != {total}"
        )
    residual = [
        row for row in targets
        if row["status"] not in {"RECOVERED", "ALREADY_SATISFIED"}
    ]
    residual_by_originator = Counter(str(row["document_originator"]) for row in residual)
    payload = {
        "schema_version": RECOVERY_REPORT_SCHEMA_VERSION,
        "generated_at_utc": generated,
        "summary": {
            "total_blocked_logical_artifacts": total,
            "already_satisfied": already,
            "full_rationale_recovered": {
                "NSE": full_by_source["NSE"],
                "BSE": full_by_source["BSE"],
                "issuer_ir": full_by_source["issuer_ir"],
                "debt_issue_document": full_by_source["debt_issue_document"],
            },
            "rating_letter_recovered": class_counts["RATING_LETTER"],
            "rating_action_intimation_recovered": class_counts[
                "RATING_ACTION_INTIMATION"
            ],
            "rating_rationale_excerpt_recovered": class_counts[
                "RATING_RATIONALE_EXCERPT"
            ],
            "metadata_only_recovered": (
                class_counts["REGULATORY_RATING_METADATA"]
                + class_counts["ANNUAL_REPORT_RATING_SUMMARY"]
            ),
            "unknown_rating_document_recovered": class_counts[
                "UNKNOWN_RATING_DOCUMENT"
            ],
            "ambiguous_candidates": ambiguous,
            "no_official_source_found": no_official,
            "manual_authorized_import_required": manual,
            "licensed_feed_or_written_permission_required": no_official,
            "crisil_residual": residual_by_originator["CRISIL"],
            "icra_residual": residual_by_originator["ICRA"],
            "quarantined_unresolvable": len(quarantine),
            "in_progress": in_progress,
            "recovered_total": len(recovered),
            "accounted_total": accounted,
        },
        "status_counts": dict(sorted(status_counts.items())),
        "targets": [dict(row) for row in targets],
        "quarantine": [dict(row) for row in quarantine],
    }
    return payload


def write_alternate_source_recovery_report(
    conn: sqlite3.Connection,
    *,
    output: Path | None = None,
    generated_at_utc: str | None = None,
) -> tuple[dict[str, Any], Path]:
    payload = build_alternate_source_recovery_report(
        conn, generated_at_utc=generated_at_utc
    )
    stamp = payload["generated_at_utc"].replace(":", "").replace("+", "_")
    destination = output or DOCUMENT_RECOVERY_EXPORTS / f"alternate-source-{stamp}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload, destination

