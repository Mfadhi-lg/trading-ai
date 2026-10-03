"""Reconcile every frozen high-value artifact to an auditable acquisition state."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..config import DB_PATH, assert_configured_writable_targets
from ..core.access_policy import is_blocked_terms_target
from ..db.database import connect, init_db
from ..db.repo import utc_now


ACTIVE_QUEUE_STATES = {"PENDING", "LEASED", "RETRYABLE_FAILURE"}


def _counts(rows: list[Any], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row[field] or "NULL") for row in rows).items()))


def _state(
    *,
    contents: list[Any],
    sources: list[Any],
    jobs: list[Any],
    recovery: Any | None,
    quarantined: bool,
    policy_blocked_source: bool = False,
) -> tuple[str, str]:
    # Provenance outranks possession. Bytes obtained from a publisher whose
    # reviewed terms forbid automated acquisition are not a valid acquisition, so
    # these two tests must precede the content branch below — otherwise holding
    # the file is enough to certify it as ACQUIRED_VALIDATED and the violation
    # disappears into the ledger.
    if policy_blocked_source:
        return "QUARANTINED_INVALID_SOURCE", "content_acquired_from_blocked_publisher"
    if quarantined:
        return "QUARANTINED_INVALID_SOURCE", "recovery_quarantine"
    if contents:
        if recovery is not None and recovery["status"] == "RECOVERED":
            content_class = str(recovery["content_class"] or "")
            if content_class in {
                "FULL_RATING_RATIONALE",
                "ISSUE_DOCUMENT_EMBEDDED_RATIONALE",
            }:
                return "ACQUIRED_ALTERNATE_SOURCE", "recovered_full_alternate_evidence"
            if content_class and content_class != "UNKNOWN_RATING_DOCUMENT":
                return "ACQUIRED_PARTIAL_EVIDENCE", "recovered_basic_alternate_evidence"
        return "ACQUIRED_VALIDATED", "validated_immutable_content_registered"
    if sources and all(str(row["availability_status"]) == "not_offered" for row in sources):
        return "NOT_OFFERED_CONFIRMED", "all_recorded_sources_explicitly_not_offered"
    recovery_status = str(recovery["status"]) if recovery is not None else None
    if recovery_status == "AMBIGUOUS":
        return "IDENTITY_AMBIGUOUS", "alternate_candidate_identity_ambiguous"
    if recovery_status == "LICENSE_OR_MANUAL_REQUIRED":
        return "LICENSE_OR_MANUAL_REQUIRED", "recovery_requires_authorized_manual_path"
    if recovery_status in {"NO_OFFICIAL_SOURCE_FOUND", "TERMINAL_FAILURE"}:
        return "POLICY_BLOCKED_NO_ALTERNATE", "blocked_originator_no_permitted_alternate"
    if recovery_status in {
        "PENDING",
        "SEARCHING",
        "CANDIDATE_FOUND",
        "RETRYABLE_FAILURE",
    }:
        return "TECHNICAL_FAILURE_RETRYABLE", f"recovery_{recovery_status.lower()}"
    queue_states = {str(row["status"]) for row in jobs}
    if queue_states & ACTIVE_QUEUE_STATES:
        return "TECHNICAL_FAILURE_RETRYABLE", "durable_queue_has_active_or_retryable_work"
    results = {
        str(row["last_result_status"] or "") for row in jobs
    }
    errors = " ".join(str(row["last_error"] or "") for row in jobs).lower()
    if any(code in results or code in errors for code in ("http_404", "http_410")):
        return "SOURCE_MISSING_TERMINAL", "source_returned_404_or_410"
    if any(code in results or code in errors for code in ("http_401", "http_403")):
        return "LICENSE_OR_MANUAL_REQUIRED", "source_denied_automated_access"
    if jobs and queue_states <= {"SKIPPED_POLICY"}:
        return "POLICY_BLOCKED_NO_ALTERNATE", "all_download_jobs_policy_skipped"
    if jobs and queue_states <= {"TERMINAL_FAILURE", "SKIPPED_POLICY"}:
        return "TECHNICAL_FAILURE_EXHAUSTED", "all_download_jobs_terminal"
    if not sources:
        return "SOURCE_MISSING_TERMINAL", "no_artifact_source_record"
    if all(str(row["availability_status"]) == "inaccessible" for row in sources):
        return "LICENSE_OR_MANUAL_REQUIRED", "all_recorded_sources_inaccessible"
    return "TECHNICAL_FAILURE_EXHAUSTED", "no_successful_content_or_claimable_queue_path"


def reconcile_scope(
    conn: Any, *, scope_id: str, evaluated_at_utc: str | None = None
) -> dict[str, Any]:
    now = evaluated_at_utc or utc_now()
    artifacts = conn.execute(
        """SELECT artifact.artifact_id,artifact.company_id,artifact.artifact_type
             FROM artifact_download_scope_artifacts AS selected
             JOIN document_artifacts AS artifact USING (artifact_id)
            WHERE selected.scope_id=? ORDER BY artifact.artifact_id""",
        (scope_id,),
    ).fetchall()
    contents_by_artifact: dict[int, list[Any]] = defaultdict(list)
    for row in conn.execute(
        """SELECT content.artifact_id,content.content_id,content.extraction_status,
                  content.content_sha256,content.byte_size
             FROM artifact_contents AS content
             JOIN artifact_download_scope_artifacts AS selected USING (artifact_id)
            WHERE selected.scope_id=? ORDER BY content.content_id""",
        (scope_id,),
    ):
        contents_by_artifact[int(row["artifact_id"])].append(row)
    sources_by_artifact: dict[int, list[Any]] = defaultdict(list)
    for row in conn.execute(
        """SELECT source.artifact_id,source.artifact_source_id,
                  source.availability_status,source.automation_status
             FROM artifact_sources AS source
             JOIN artifact_download_scope_artifacts AS selected USING (artifact_id)
            WHERE selected.scope_id=? ORDER BY source.artifact_source_id""",
        (scope_id,),
    ):
        sources_by_artifact[int(row["artifact_id"])].append(row)
    jobs_by_artifact: dict[int, list[Any]] = defaultdict(list)
    for row in conn.execute(
        """SELECT artifact_id,status,last_result_status,last_error,attempt_count
             FROM artifact_download_queue WHERE scope_id=? ORDER BY download_job_id""",
        (scope_id,),
    ):
        jobs_by_artifact[int(row["artifact_id"])].append(row)
    recovery_by_artifact = {
        int(row["artifact_id"]): row
        for row in conn.execute(
            """SELECT target.artifact_id,target.status,candidate.source_type,
                      candidate.content_class
                 FROM artifact_recovery_targets AS target
                 LEFT JOIN artifact_recovery_candidates AS candidate
                   ON candidate.candidate_id=target.resolved_candidate_id"""
        )
    }
    quarantined = {
        int(row[0]) for row in conn.execute("SELECT artifact_id FROM artifact_recovery_quarantine")
    }
    # Derived from the attempt log rather than a curated list: any content whose
    # own acquisition touched a blocked-terms host is quarantined wherever it
    # came from, including a redirect that ended there.
    policy_blocked_sources = {
        int(row["artifact_id"])
        for row in conn.execute(
            """SELECT DISTINCT attempt.artifact_id,attempt.requested_url,attempt.final_url
                 FROM artifact_download_attempts AS attempt
                 JOIN artifact_download_scope_artifacts AS selected USING (artifact_id)
                WHERE selected.scope_id=? AND attempt.content_id IS NOT NULL""",
            (scope_id,),
        )
        if is_blocked_terms_target(str(row["requested_url"] or ""))
        or is_blocked_terms_target(str(row["final_url"] or ""))
    }
    rows: list[dict[str, Any]] = []
    with conn:
        for artifact in artifacts:
            artifact_id = int(artifact["artifact_id"])
            contents = contents_by_artifact.get(artifact_id, [])
            sources = sources_by_artifact.get(artifact_id, [])
            jobs = jobs_by_artifact.get(artifact_id, [])
            recovery = recovery_by_artifact.get(artifact_id)
            final_state, basis = _state(
                contents=contents,
                sources=sources,
                jobs=jobs,
                recovery=recovery,
                quarantined=artifact_id in quarantined,
                policy_blocked_source=artifact_id in policy_blocked_sources,
            )
            evidence = {
                "basis": basis,
                "content_count": len(contents),
                "content_extraction_states": _counts(contents, "extraction_status"),
                "queue_states": _counts(jobs, "status"),
                "queue_result_states": _counts(jobs, "last_result_status"),
                "source_availability_states": _counts(sources, "availability_status"),
                "source_automation_states": _counts(sources, "automation_status"),
                "policy_blocked_source": artifact_id in policy_blocked_sources,
                "recovery_status": str(recovery["status"]) if recovery is not None else None,
                "recovery_source_type": str(recovery["source_type"] or "") if recovery is not None else None,
                "rating_evidence_class": str(recovery["content_class"] or "") if recovery is not None else None,
            }
            conn.execute(
                """INSERT INTO document_acquisition_closure_states
                   (scope_id,artifact_id,final_state,evidence_json,evaluated_at_utc)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(scope_id,artifact_id) DO UPDATE SET
                     final_state=excluded.final_state,
                     evidence_json=excluded.evidence_json,
                     evaluated_at_utc=excluded.evaluated_at_utc""",
                (
                    scope_id,
                    artifact_id,
                    final_state,
                    json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                    now,
                ),
            )
            rows.append(
                {
                    "artifact_id": artifact_id,
                    "company_id": int(artifact["company_id"]),
                    "artifact_type": str(artifact["artifact_type"]),
                    "final_state": final_state,
                    "basis": basis,
                }
            )
    by_state = Counter(row["final_state"] for row in rows)
    by_type_state: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_type_state[row["artifact_type"]][row["final_state"]] += 1
    return {
        "schema_version": "1.0.0",
        "scope_id": scope_id,
        "evaluated_at_utc": now,
        "artifact_count": len(rows),
        "by_final_state": dict(sorted(by_state.items())),
        "by_artifact_type_and_state": {
            key: dict(sorted(value.items()))
            for key, value in sorted(by_type_state.items())
        },
        "invariants": {
            "every_scoped_artifact_accounted": len(rows) == len(artifacts),
            "state_counts_reconcile": sum(by_state.values()) == len(rows),
            "no_unknown_state": all(row["final_state"] for row in rows),
        },
        "rows": rows,
    }


def write_ledger(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "16_FINAL_COVERAGE_LEDGER.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("artifact_id", "company_id", "artifact_type", "final_state", "basis"),
        )
        writer.writeheader()
        writer.writerows(report["rows"])
    lines = [
        "# Final Document Acquisition Coverage Ledger",
        "",
        f"Scope: `{report['scope_id']}`",
        f"Artifacts: {report['artifact_count']}",
        "",
    ]
    for artifact_type, states in report["by_artifact_type_and_state"].items():
        lines.extend(
            [
                f"## {artifact_type}",
                "",
                *[f"- {state}: {count}" for state, count in states.items()],
                "",
            ]
        )
    lines.extend(
        [
            "## Invariants",
            "",
            *[
                f"- {key}: {'PASS' if value else 'FAIL'}"
                for key, value in report["invariants"].items()
            ],
        ]
    )
    (output_dir / "16_FINAL_COVERAGE_LEDGER.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--scope-id", default="document-acquisition-full-v1")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    assert_configured_writable_targets(args.output_dir, db_path=args.db)
    conn = connect(args.db)
    try:
        init_db(conn)
        report = reconcile_scope(conn, scope_id=args.scope_id)
    finally:
        conn.close()
    write_ledger(report, args.output_dir)
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
