"""Evaluate the final Document Acquisition gate and create the RI handoff only if green."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..config import DB_PATH, assert_configured_writable_targets
from ..db.database import connect, init_db


ACTIVE_QUEUE_STATES = ("PENDING", "LEASED", "RETRYABLE_FAILURE")
ACTIVE_RECOVERY_STATES = ("PENDING", "SEARCHING", "CANDIDATE_FOUND", "RETRYABLE_FAILURE")

# Closure states that keep acquisition work open. GREEN is impossible while any
# of these remain; everything else must be an explicit terminal residual.
ACTIVE_CLOSURE_STATES = ("TECHNICAL_FAILURE_RETRYABLE",)

# Explicit, evidence-backed residuals. These are counted and reported, and they
# do not block GREEN: Document Acquisition never required 100% download success.
EXPLICIT_TERMINAL_RESIDUAL_STATES = (
    "NOT_OFFERED_CONFIRMED",
    "SOURCE_MISSING_TERMINAL",
    "POLICY_BLOCKED_NO_ALTERNATE",
    "LICENSE_OR_MANUAL_REQUIRED",
    "TECHNICAL_FAILURE_EXHAUSTED",
    "IDENTITY_AMBIGUOUS",
    "PERIOD_AMBIGUOUS",
    "QUARANTINED_INVALID_SOURCE",
)

ACQUIRED_CLOSURE_STATES = (
    "ACQUIRED_VALIDATED",
    "ACQUIRED_ALTERNATE_SOURCE",
    "ACQUIRED_PARTIAL_EVIDENCE",
)

UNRESOLVED_RECORDING_STATES = ("UNKNOWN_REQUIRES_RECONCILIATION",)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _required_json(path: Path, missing: list[str]) -> dict[str, Any]:
    """Read a mandatory gate input, recording absence instead of raising.

    A gate input that was never produced is evidence that the closure run is
    incomplete. It must surface as an explicit RED with the missing artifact
    named, never as a crash and never as a silent pass.
    """

    try:
        return _json(path)
    except (OSError, ValueError) as error:
        missing.append(f"{path.name}: {error.__class__.__name__}")
        return {}


def _structured_gate(path: Path, gate: str) -> tuple[bool, dict[str, Any]]:
    """Read a machine-readable operational gate, failing closed when absent.

    Mandatory gates are never inferred from Markdown prose; a missing or
    malformed evidence file is a FAIL, not a pass.
    """

    if not path.is_file():
        return False, {"gate": gate, "passed": False, "error": f"missing evidence: {path.name}"}
    try:
        payload = _json(path)
    except (ValueError, OSError) as error:
        return False, {"gate": gate, "passed": False, "error": str(error)}
    if str(payload.get("gate")) != gate:
        return False, {
            "gate": gate,
            "passed": False,
            "error": f"evidence declares gate {payload.get('gate')!r}",
        }
    return bool(payload.get("passed")), payload


def _is_production_db_path(path: Path) -> bool:
    """Gate against the database actually opened, not the process default."""

    try:
        return path.resolve(strict=False) == Path("/data/scraper.db")
    except OSError:
        return str(path) == "/data/scraper.db"


def _reconciliation_counts(coverage: dict[str, Any]) -> tuple[int, int, int]:
    transcript_unknown = int(
        coverage.get("transcript_state_counts", {}).get("UNKNOWN_REQUIRES_RECONCILIATION", 0)
    )
    recording_only_counts = coverage.get("recording_only_state_counts", {})
    recording_unknown = sum(
        int(recording_only_counts.get(state, 0)) for state in UNRESOLVED_RECORDING_STATES
    )
    if not recording_only_counts:
        # Pre-hardening coverage payload: trust only the explicit unknown count.
        recording_unknown = int(coverage.get("recording_only_requires_reconciliation", 0))
    fallback_from_unresolved = int(coverage.get("fallback_queued_from_unresolved_states", 0))
    return transcript_unknown, recording_unknown, fallback_from_unresolved


def evaluate(
    conn: Any, *, output_dir: Path, scope_id: str, db_path: Path = DB_PATH
) -> dict[str, Any]:
    missing_inputs: list[str] = []
    p0_manifest = _required_json(output_dir / "02_ACQUISITION_P0_MANIFEST.json", missing_inputs)
    p0_storage = _required_json(output_dir / "08_P0_STORAGE_REPORT.json", missing_inputs)
    p0_extraction = _required_json(output_dir / "07_P0_EXTRACTION_REPORT.json", missing_inputs)
    integrity = _required_json(output_dir / "15_FINAL_CORPUS_INTEGRITY.json", missing_inputs)
    host = _required_json(output_dir / "17_HOST_POLICY_AUDIT.json", missing_inputs)
    storage = _required_json(output_dir / "18_STORAGE_FINAL.json", missing_inputs)
    recording = _required_json(output_dir / "14_RECORDING_ACQUISITION_REPORT.json", missing_inputs)
    coverage = _required_json(output_dir / "12_TRANSCRIPT_COVERAGE.json", missing_inputs)
    store_passed, store_evidence = _structured_gate(
        output_dir / "03_EXTERNAL_STORE_FAIL_CLOSED.json", "external_store_fail_closed"
    )
    restart_passed, restart_evidence = _structured_gate(
        output_dir / "05_RESTART_RESUME.json", "restart_resume"
    )
    transcript_unknown, recording_unknown, fallback_from_unresolved = _reconciliation_counts(coverage)
    scoped = int(
        conn.execute(
            "SELECT COUNT(*) FROM artifact_download_scope_artifacts WHERE scope_id=?",
            (scope_id,),
        ).fetchone()[0]
    )
    closure = int(
        conn.execute(
            "SELECT COUNT(*) FROM document_acquisition_closure_states WHERE scope_id=?",
            (scope_id,),
        ).fetchone()[0]
    )
    active_queue = int(
        conn.execute(
            """SELECT COUNT(*) FROM artifact_download_queue
                WHERE scope_id=? AND status IN ('PENDING','LEASED','RETRYABLE_FAILURE')""",
            (scope_id,),
        ).fetchone()[0]
    )
    active_recovery = int(
        conn.execute(
            """SELECT COUNT(*) FROM artifact_recovery_targets
                WHERE status IN ('PENDING','SEARCHING','CANDIDATE_FOUND','RETRYABLE_FAILURE')"""
        ).fetchone()[0]
    )
    d03_pending = int(
        conn.execute(
            """SELECT COUNT(*) FROM artifact_contents AS content
                 JOIN document_artifacts AS artifact USING (artifact_id)
                WHERE artifact.artifact_type IN
                      ('transcript','presentation','credit_rating')
                  AND content.extraction_status IN ('not_started','pending')"""
        ).fetchone()[0]
    )
    false_unknown_recovery = int(
        conn.execute(
            """SELECT COUNT(*) FROM artifact_recovery_targets AS target
                 JOIN artifact_recovery_candidates AS candidate
                   ON candidate.candidate_id=target.resolved_candidate_id
                WHERE target.status='RECOVERED'
                  AND candidate.content_class='UNKNOWN_RATING_DOCUMENT'"""
        ).fetchone()[0]
    )
    acquisition_fk = int(
        conn.execute(
            """SELECT COUNT(*) FROM pragma_foreign_key_check
                WHERE \"table\" IN (
                  'document_artifacts','artifact_sources','artifact_contents',
                  'artifact_download_queue','artifact_recovery_targets',
                  'document_acquisition_closure_states')"""
        ).fetchone()[0]
    )
    # Content-addressed reuse: one physical blob per SHA256 and one path per blob.
    duplicate_blob_shas = int(
        conn.execute(
            """SELECT COUNT(*) FROM (
                 SELECT content_sha256 FROM response_blobs
                  GROUP BY content_sha256 HAVING COUNT(DISTINCT blob_id) > 1)"""
        ).fetchone()[0]
    )
    duplicate_blob_paths = int(
        conn.execute(
            """SELECT COUNT(*) FROM (
                 SELECT local_path FROM response_blobs
                  GROUP BY local_path HAVING COUNT(DISTINCT blob_id) > 1)"""
        ).fetchone()[0]
    )
    retryable_closure = int(
        conn.execute(
            """SELECT COUNT(*) FROM document_acquisition_closure_states
                WHERE scope_id=? AND final_state='TECHNICAL_FAILURE_RETRYABLE'""",
            (scope_id,),
        ).fetchone()[0]
    )
    closure_by_state = {
        str(row[0]): int(row[1])
        for row in conn.execute(
            """SELECT final_state,COUNT(*) FROM document_acquisition_closure_states
                WHERE scope_id=? GROUP BY final_state""",
            (scope_id,),
        )
    }
    active_retryable = sum(
        closure_by_state.get(state, 0) for state in ACTIVE_CLOSURE_STATES
    )
    explicit_residual = sum(
        closure_by_state.get(state, 0) for state in EXPLICIT_TERMINAL_RESIDUAL_STATES
    )
    acquired = sum(closure_by_state.get(state, 0) for state in ACQUIRED_CLOSURE_STATES)
    # Any closure state outside the governed vocabulary is unaccounted, not benign.
    unclassified_closure = sorted(
        state
        for state in closure_by_state
        if state
        not in {
            *ACTIVE_CLOSURE_STATES,
            *EXPLICIT_TERMINAL_RESIDUAL_STATES,
            *ACQUIRED_CLOSURE_STATES,
        }
    )
    fallback_active = sum(
        int(recording.get("queue_states", {}).get(state, 0))
        for state in ACTIVE_QUEUE_STATES
    )
    coverage_present = bool(coverage)
    checks = {
        "production_db_path": _is_production_db_path(db_path),
        "gate_inputs_present": not missing_inputs,
        "p0_company_count_30_to_50": 30 <= len(p0_manifest.get("company_ids", [])) <= 50,
        "p0_recordings_excluded": bool(p0_manifest)
        and "recording" not in p0_manifest.get("artifact_types", ["recording"]),
        "p0_content_integrity": bool(p0_storage.get("integrity", {}).get("pass")),
        "p0_d03": bool(p0_extraction)
        and all(
            int(values["failed"]) == 0 and int(values["unusable"]) == 0
            for values in p0_extraction.get("by_artifact_type", {}).values()
        ),
        "external_store_fail_closed": store_passed,
        "restart_resume": restart_passed,
        "full_scope_accounted": scoped > 0 and closure == scoped,
        "full_queue_processed": active_queue == 0,
        "rating_recovery_processed": active_recovery == 0,
        "no_unknown_called_recovered": false_unknown_recovery == 0,
        "full_d03_processed": d03_pending == 0,
        "transcript_coverage_created": (output_dir / "12_TRANSCRIPT_COVERAGE.json").is_file(),
        "recording_fallback_scope_created": (output_dir / "13_RECORDING_FALLBACK_SCOPE.json").is_file(),
        "recording_fallback_processed": fallback_active == 0,
        "recording_fallback_allowlist_respected": coverage_present
        and fallback_from_unresolved == 0,
        "transcript_reconciliation_resolved": coverage_present and transcript_unknown == 0,
        "recording_reconciliation_resolved": coverage_present and recording_unknown == 0,
        "closure_states_all_governed": not unclassified_closure,
        "no_retryable_closure_state": retryable_closure == 0,
        "blocked_cra_requests_zero": bool(host.get("blocked_host_policy_pass"))
        and int(host.get("blocked_cra_requests_total", 0)) == 0,
        "final_content_integrity": bool(integrity.get("pass")),
        "content_deduplication": bool(integrity)
        and duplicate_blob_shas == 0
        and duplicate_blob_paths == 0
        and not integrity.get("orphan_physical_files", ["unknown"]),
        "storage_reserve_respected": bool(storage.get("reserve_respected")),
        "acquisition_foreign_keys": acquisition_fk == 0,
        "d09_executed": (output_dir / "09_D09_RECONCILIATION_REPORT.json").is_file(),
        "fg00_preserved": Path("/workspace/artifacts/focus_ml/governance").is_dir()
        or Path("artifacts/focus_ml/governance").is_dir(),
    }
    return {
        "document_acquisition": "GREEN" if all(checks.values()) else "RED",
        "ready_for_research_intelligence": all(checks.values()),
        "checks": checks,
        "counts": {
            "scoped_artifacts": scoped,
            "closure_rows": closure,
            "active_queue_jobs": active_queue,
            "active_recovery_targets": active_recovery,
            "d03_pending_contents": d03_pending,
            "retryable_closure_states": retryable_closure,
            "fallback_active_jobs": fallback_active,
            "transcript_unknown_reconciliation_count": transcript_unknown,
            "recording_unknown_reconciliation_count": recording_unknown,
            "fallback_queued_from_unresolved_states": fallback_from_unresolved,
            "active_retryable_acquisition_count": active_retryable,
            "explicit_terminal_residual_count": explicit_residual,
            "acquired_closure_count": acquired,
            "unclassified_closure_states": unclassified_closure,
            "blocked_cra_request_count": int(host.get("blocked_cra_requests_total", 0)),
            "acquisition_fk_violations": acquisition_fk,
            "missing_gate_inputs": missing_inputs,
            "duplicate_blob_sha_count": duplicate_blob_shas,
            "duplicate_blob_path_count": duplicate_blob_paths,
        },
        "gate_evidence": {
            "external_store_fail_closed": store_evidence,
            "restart_resume": restart_evidence,
        },
    }


def _machine_gate(report: dict[str, Any]) -> dict[str, Any]:
    """Flatten the gate into the machine-readable contract consumers depend on.

    This - not the Markdown report - is the authoritative closure artifact.
    """

    checks = report["checks"]
    counts = report["counts"]
    return {
        "schema_version": "1.0.0",
        "document_acquisition": report["document_acquisition"],
        "ready_for_research_intelligence": report["ready_for_research_intelligence"],
        "external_store_fail_closed": checks["external_store_fail_closed"],
        "restart_resume": checks["restart_resume"],
        "content_integrity": checks["final_content_integrity"],
        "deduplication": checks["content_deduplication"],
        "blocked_host_policy": checks["blocked_cra_requests_zero"],
        "blocked_cra_request_count": counts["blocked_cra_request_count"],
        "transcript_unknown_reconciliation_count": counts[
            "transcript_unknown_reconciliation_count"
        ],
        "recording_unknown_reconciliation_count": counts[
            "recording_unknown_reconciliation_count"
        ],
        "active_retryable_acquisition_count": counts["active_retryable_acquisition_count"],
        "explicit_terminal_residual_count": counts["explicit_terminal_residual_count"],
        "coverage_ledger_reconciled": bool(
            checks["transcript_reconciliation_resolved"]
            and checks["recording_reconciliation_resolved"]
            and checks["recording_fallback_allowlist_respected"]
            and checks["closure_states_all_governed"]
            and checks["full_scope_accounted"]
        ),
        "checks": checks,
        "counts": counts,
        "gate_evidence": report.get("gate_evidence", {}),
    }


def write_gate_json(report: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    machine = _machine_gate(report)
    (output_dir / "FINAL_ACQUISITION_GATE.json").write_text(
        json.dumps(machine, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return machine


def write_gate(report: dict[str, Any], output_dir: Path) -> None:
    lines = [
        "# Document Acquisition Final Gate",
        "",
        f"DOCUMENT_ACQUISITION: **{report['document_acquisition']}**",
        f"READY_FOR_RESEARCH_INTELLIGENCE: **{'YES' if report['ready_for_research_intelligence'] else 'NO'}**",
        "",
        "## Checks",
        "",
        *[
            f"- {key}: {'PASS' if value else 'FAIL'}"
            for key, value in report["checks"].items()
        ],
        "",
        "## Counts",
        "",
        *[f"- {key}: {value}" for key, value in report["counts"].items()],
    ]
    (output_dir / "DOCUMENT_ACQUISITION_FINAL_GATE.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def write_handoff(output_dir: Path) -> None:
    text = """# Document Acquisition Handoff to Research Intelligence

Document Acquisition is GREEN. Research Intelligence may read immutable source
bytes from the external content-addressed document store through
`artifact_contents -> response_blobs.local_path`; it must verify or retain the
stored SHA256 in `artifact_contents.content_sha256`.

## Durable lookup and provenance

- Logical identity: `document_artifacts` (company, document class, exact event /
  period metadata where known).
- Discovery and retrieval provenance: `artifact_sources`, including preserved
  discovery origin, retrieval host/type, publisher originator, and policy state.
- Immutable bytes: `artifact_contents` and `response_blobs`.
- Basic extraction: `document_extractions`, `document_pages`, and
  `document_page_elements`; `selected_text_origin` distinguishes native text
  from OCR.
- Rating evidence: `artifact_source_evidence_classifications`; UNKNOWN is not
  valid rating coverage.
- Final acquisition missingness: `document_acquisition_closure_states`.
- Recording lineage: `recording_normalizations`,
  `artifact_content_relationships`, and `machine_transcription_runs`.

Available automatic classes are transcript, presentation, and credit_rating.
Recording content exists only for the explicit transcript-gap fallback scope.
Annual reports, DRHP, generic announcements, Screener notes, and ambiguous
concall-other documents remain deferred by acquisition policy.

RI may trust validated hashes, byte paths, extraction origin/status, exact
period fields when populated, availability timestamps, and recorded source
provenance. RI must not assume a missing URL means not offered; must not convert
technical failure into source absence; must not treat UNKNOWN rating evidence
as coverage; must not infer a fiscal quarter from document month; and must not
represent machine transcription as publisher-provided transcript.
"""
    (output_dir / "DOCUMENT_ACQUISITION_HANDOFF_TO_RI.md").write_text(
        text, encoding="utf-8"
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
        report = evaluate(
            conn, output_dir=args.output_dir, scope_id=args.scope_id, db_path=args.db
        )
    finally:
        conn.close()
    write_gate(report, args.output_dir)
    machine = write_gate_json(report, args.output_dir)
    if report["ready_for_research_intelligence"]:
        write_handoff(args.output_dir)
    print(json.dumps(machine, sort_keys=True))
    return 0 if report["ready_for_research_intelligence"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
