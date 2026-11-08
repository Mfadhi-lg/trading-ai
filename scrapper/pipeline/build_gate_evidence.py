"""Produce machine-readable evidence for mandatory operational acquisition gates.

The human Markdown drill reports remain, but the final gate must not decide
GREEN by searching those reports for the substring ``PASS``. Each gate here is
re-derived from executable checks or from the structured before/after snapshots
that the drill already preserved.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..config import DOCUMENT_STORE, DOCUMENT_STORE_SENTINEL, assert_configured_writable_targets
from ..documents.store import assert_document_store_ready


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def external_store_fail_closed_gate(
    *, document_store: Path, probe_root: Path, run_id: str
) -> dict[str, Any]:
    """Re-execute the fail-closed drill instead of trusting a prose report.

    An unsentinelled store root must be refused before any directory or byte is
    created, and the real configured store must still authorize normally.
    """

    probe_root.mkdir(parents=True, exist_ok=True)
    entries_before = sorted(path.name for path in probe_root.iterdir())
    refused = False
    refusal_error = None
    try:
        assert_document_store_ready(probe_root, require_sentinel=True)
    except RuntimeError as error:
        refused = True
        refusal_error = str(error)
    entries_after = sorted(path.name for path in probe_root.iterdir())
    created = [name for name in entries_after if name not in entries_before]

    real_store_authorized = False
    real_store_error = None
    if document_store.is_dir():
        try:
            assert_document_store_ready(document_store)
            real_store_authorized = True
        except RuntimeError as error:
            real_store_error = str(error)

    passed = refused and not created and real_store_authorized
    return {
        "gate": "external_store_fail_closed",
        "passed": passed,
        "run_id": run_id,
        "evidence": {
            "sentinel_filename": DOCUMENT_STORE_SENTINEL,
            "probe_root": str(probe_root),
            "unsentinelled_store_refused": refused,
            "refusal_error": refusal_error,
            "entries_created_by_refused_startup": created,
            "configured_document_store": str(document_store),
            "configured_store_authorized": real_store_authorized,
            "configured_store_error": real_store_error,
        },
    }


def restart_resume_gate(
    *, before_path: Path, after_path: Path, run_id: str
) -> dict[str, Any]:
    """Derive kill/restart/resume from the preserved queue and attempt snapshots."""

    before = _json(before_path)
    after = _json(after_path)
    before_jobs = {int(row["download_job_id"]): row for row in before["jobs"]}
    after_jobs = {int(row["download_job_id"]): row for row in after["jobs"]}
    before_attempts = {int(row["download_attempt_id"]) for row in before["attempts"]}

    succeeded_before = {
        job_id for job_id, row in before_jobs.items() if str(row["status"]) == "SUCCEEDED"
    }
    leased_before = {
        job_id for job_id, row in before_jobs.items() if str(row["status"]) == "LEASED"
    }
    # A job that already succeeded must never be fetched again after restart.
    new_attempt_job_ids = {
        int(row["download_job_id"])
        for row in after["attempts"]
        if int(row["download_attempt_id"]) not in before_attempts
    }
    repeated_successful_downloads = sorted(succeeded_before & new_attempt_job_ids)
    recovered_expired_leases = sorted(
        job_id
        for job_id in leased_before
        if job_id in after_jobs and str(after_jobs[job_id]["status"]) != "LEASED"
    )
    regressed_terminal = sorted(
        job_id
        for job_id, row in before_jobs.items()
        if str(row["status"]) in {"TERMINAL_FAILURE", "SKIPPED_POLICY"}
        and job_id in after_jobs
        and str(after_jobs[job_id]["status"]) != str(row["status"])
    )
    attempt_counts_regressed = sorted(
        job_id
        for job_id in succeeded_before
        if job_id in after_jobs
        and int(after_jobs[job_id]["attempt_count"]) != int(before_jobs[job_id]["attempt_count"])
    )
    residual_active = sorted(
        job_id
        for job_id, row in after_jobs.items()
        if str(row["status"]) in {"PENDING", "LEASED"}
    )

    passed = (
        bool(leased_before)
        and not repeated_successful_downloads
        and not regressed_terminal
        and not attempt_counts_regressed
        and len(recovered_expired_leases) == len(leased_before)
        and not residual_active
    )
    return {
        "gate": "restart_resume",
        "passed": passed,
        "run_id": run_id,
        "before_snapshot": str(before_path),
        "after_snapshot": str(after_path),
        "repeated_successful_downloads": len(repeated_successful_downloads),
        "recovered_expired_leases": len(recovered_expired_leases),
        "evidence": {
            "jobs_before": len(before_jobs),
            "jobs_after": len(after_jobs),
            "leased_before_job_ids": sorted(leased_before),
            "recovered_lease_job_ids": recovered_expired_leases,
            "repeated_successful_download_job_ids": repeated_successful_downloads,
            "terminal_rows_regressed_job_ids": regressed_terminal,
            "succeeded_attempt_count_changed_job_ids": attempt_counts_regressed,
            "residual_active_job_ids": residual_active,
            "new_attempts_after_restart": len(new_attempt_job_ids),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--document-store", type=Path, default=DOCUMENT_STORE)
    parser.add_argument(
        "--probe-root",
        type=Path,
        required=True,
        help="Empty directory used as an unsentinelled store during the drill",
    )
    parser.add_argument("--restart-before", type=Path, default=None)
    parser.add_argument("--restart-after", type=Path, default=None)
    args = parser.parse_args()
    assert_configured_writable_targets(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    store_gate = external_store_fail_closed_gate(
        document_store=args.document_store,
        probe_root=args.probe_root,
        run_id=args.run_id,
    )
    (args.output_dir / "03_EXTERNAL_STORE_FAIL_CLOSED.json").write_text(
        json.dumps(store_gate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    before = args.restart_before or args.output_dir / "05_P0_RESTART_BEFORE.json"
    after = args.restart_after or args.output_dir / "05_P0_RESTART_AFTER.json"
    restart_gate = restart_resume_gate(
        before_path=before, after_path=after, run_id=args.run_id
    )
    (args.output_dir / "05_RESTART_RESUME.json").write_text(
        json.dumps(restart_gate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "external_store_fail_closed": store_gate["passed"],
                "restart_resume": restart_gate["passed"],
            },
            sort_keys=True,
        )
    )
    return 0 if store_gate["passed"] and restart_gate["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
