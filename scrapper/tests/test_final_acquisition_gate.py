"""AC-FINAL-01: the final gate may not go GREEN with unresolved reconciliation."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scrapper.db.database import connect, init_db
from scrapper.pipeline.evaluate_document_acquisition_gate import (
    ACQUIRED_CLOSURE_STATES,
    ACTIVE_CLOSURE_STATES,
    EXPLICIT_TERMINAL_RESIDUAL_STATES,
    _machine_gate,
    evaluate,
    write_gate_json,
)


SCOPE = "document-acquisition-full-v1"
PRODUCTION_DB = Path("/data/scraper.db")
NOW = "2026-08-17T00:00:00+00:00"

# One closure state per governed class, so residual accounting is observable.
CLOSURE_STATES = (
    "ACQUIRED_VALIDATED",
    "NOT_OFFERED_CONFIRMED",
    "POLICY_BLOCKED_NO_ALTERNATE",
)


def _green_reports() -> dict[str, Any]:
    """Every JSON input the evaluator reads, in its all-clear form."""

    return {
        "02_ACQUISITION_P0_MANIFEST": {
            "company_ids": list(range(1, 41)),
            "artifact_types": ["transcript", "presentation", "credit_rating"],
        },
        "08_P0_STORAGE_REPORT": {"integrity": {"pass": True}},
        "07_P0_EXTRACTION_REPORT": {
            "by_artifact_type": {"transcript": {"failed": 0, "unusable": 0}}
        },
        "15_FINAL_CORPUS_INTEGRITY": {"pass": True, "orphan_physical_files": []},
        "17_HOST_POLICY_AUDIT": {
            "blocked_host_policy_pass": True,
            "blocked_cra_requests_total": 0,
        },
        "18_STORAGE_FINAL": {"reserve_respected": True},
        "14_RECORDING_ACQUISITION_REPORT": {
            "queue_states": {"SUCCEEDED": 3, "TERMINAL_FAILURE": 1}
        },
        "12_TRANSCRIPT_COVERAGE": {
            "transcript_state_counts": {
                "OFFICIAL_TRANSCRIPT_AVAILABLE": 10,
                "OFFICIAL_TRANSCRIPT_NOT_OFFERED": 2,
                "TECHNICAL_FAILURE": 0,
                "UNKNOWN_REQUIRES_RECONCILIATION": 0,
            },
            "recording_only_state_counts": {
                "RECORDING_ONLY_NO_TRANSCRIPT_ARTIFACT": 4,
                "UNKNOWN_REQUIRES_RECONCILIATION": 0,
            },
            "recording_only_requires_reconciliation": 0,
            "fallback_queued_from_unresolved_states": 0,
        },
        "09_D09_RECONCILIATION_REPORT": {"pass": True},
        "13_RECORDING_FALLBACK_SCOPE": {"artifact_ids": []},
        "03_EXTERNAL_STORE_FAIL_CLOSED": {
            "gate": "external_store_fail_closed",
            "passed": True,
            "run_id": "test",
            "evidence": {"unsentinelled_store_refused": True},
        },
        "05_RESTART_RESUME": {
            "gate": "restart_resume",
            "passed": True,
            "run_id": "test",
            "before_snapshot": "before.json",
            "after_snapshot": "after.json",
            "repeated_successful_downloads": 0,
            "recovered_expired_leases": 3,
        },
    }


class FinalGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.output_dir = self.root / "artifacts"
        self.output_dir.mkdir()
        # fg00_preserved is resolved relative to the working directory.
        (self.root / "artifacts" / "focus_ml" / "governance").mkdir(parents=True)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.reports = _green_reports()
        self.conn = connect(self.root / "gate.db")
        self.addCleanup(self.conn.close)
        init_db(self.conn, apply_migrations=True)
        self._build_corpus()

    def _build_corpus(self) -> None:
        self.conn.execute(
            """INSERT INTO artifact_download_scopes
               (scope_id,scope_kind,description,artifact_types_json,policy_json,
                created_at_utc,updated_at_utc)
               VALUES(?,'custom','test','["transcript"]','{}',?,?)""",
            (SCOPE, NOW, NOW),
        )
        self.conn.execute(
            "INSERT INTO companies(company_id,current_legal_name,status) VALUES(1,'Co','active')"
        )
        for index, final_state in enumerate(CLOSURE_STATES, start=1):
            cursor = self.conn.execute(
                """INSERT INTO document_artifacts
                   (company_id,artifact_key,artifact_type,publisher,title_raw,event_date,
                    period_end,fiscal_year,fiscal_quarter,published_at_utc,
                    first_observed_at_utc,preferred_content_id,acquisition_status,
                    transcript_preference_status)
                   VALUES(1,?,'transcript','issuer',?,NULL,NULL,NULL,NULL,NULL,?,NULL,
                          'discovered','not_applicable')""",
                (f"artifact-{index}", f"Transcript {index}", NOW),
            )
            artifact_id = int(cursor.lastrowid)
            self.conn.execute(
                "INSERT INTO artifact_download_scope_artifacts(scope_id,artifact_id) VALUES(?,?)",
                (SCOPE, artifact_id),
            )
            self.set_closure_state(artifact_id, final_state)

    def set_closure_state(self, artifact_id: int, final_state: str) -> None:
        self.conn.execute(
            """INSERT INTO document_acquisition_closure_states
               (scope_id,artifact_id,final_state,evidence_json,evaluated_at_utc)
               VALUES(?,?,?,'{}',?)
               ON CONFLICT(scope_id,artifact_id)
               DO UPDATE SET final_state=excluded.final_state""",
            (SCOPE, artifact_id, final_state, NOW),
        )

    def add_closure_state(self, final_state: str) -> int:
        cursor = self.conn.execute(
            """INSERT INTO document_artifacts
               (company_id,artifact_key,artifact_type,publisher,title_raw,event_date,
                period_end,fiscal_year,fiscal_quarter,published_at_utc,
                first_observed_at_utc,preferred_content_id,acquisition_status,
                transcript_preference_status)
               VALUES(1,?,'transcript','issuer','Extra',NULL,NULL,NULL,NULL,NULL,?,NULL,
                      'discovered','not_applicable')""",
            (f"artifact-{final_state}", NOW),
        )
        artifact_id = int(cursor.lastrowid)
        self.conn.execute(
            "INSERT INTO artifact_download_scope_artifacts(scope_id,artifact_id) VALUES(?,?)",
            (SCOPE, artifact_id),
        )
        self.set_closure_state(artifact_id, final_state)
        return artifact_id

    def write_reports(self) -> None:
        for name in _green_reports():
            path = self.output_dir / f"{name}.json"
            if name in self.reports:
                path.write_text(
                    json.dumps(self.reports[name], indent=2, sort_keys=True), encoding="utf-8"
                )
            elif path.exists():
                path.unlink()

    def run_gate(self) -> dict[str, Any]:
        self.write_reports()
        return evaluate(
            self.conn,
            output_dir=self.output_dir,
            scope_id=SCOPE,
            db_path=PRODUCTION_DB,
        )

    # --- baseline -----------------------------------------------------------

    def test_clean_corpus_is_green(self) -> None:
        report = self.run_gate()
        self.assertEqual(report["document_acquisition"], "GREEN", report["checks"])
        self.assertTrue(report["ready_for_research_intelligence"])
        self.assertEqual(report["counts"]["explicit_terminal_residual_count"], 2)
        self.assertEqual(report["counts"]["active_retryable_acquisition_count"], 0)

    def test_development_db_is_never_production_closure(self) -> None:
        self.write_reports()
        report = evaluate(
            self.conn,
            output_dir=self.output_dir,
            scope_id=SCOPE,
            db_path=self.root / "gate.db",
        )
        self.assertFalse(report["checks"]["production_db_path"])
        self.assertEqual(report["document_acquisition"], "RED")

    # --- unresolved reconciliation blocks GREEN -----------------------------

    def test_unknown_transcript_reconciliation_blocks_green(self) -> None:
        self.reports["12_TRANSCRIPT_COVERAGE"]["transcript_state_counts"][
            "UNKNOWN_REQUIRES_RECONCILIATION"
        ] = 1
        report = self.run_gate()
        self.assertFalse(report["checks"]["transcript_reconciliation_resolved"])
        self.assertEqual(report["document_acquisition"], "RED")
        self.assertFalse(report["ready_for_research_intelligence"])
        self.assertEqual(report["counts"]["transcript_unknown_reconciliation_count"], 1)

    def test_unknown_recording_reconciliation_blocks_green(self) -> None:
        self.reports["12_TRANSCRIPT_COVERAGE"]["recording_only_state_counts"][
            "UNKNOWN_REQUIRES_RECONCILIATION"
        ] = 1
        report = self.run_gate()
        self.assertFalse(report["checks"]["recording_reconciliation_resolved"])
        self.assertEqual(report["document_acquisition"], "RED")
        self.assertEqual(report["counts"]["recording_unknown_reconciliation_count"], 1)

    def test_legacy_coverage_payload_still_reports_recording_unknowns(self) -> None:
        coverage = self.reports["12_TRANSCRIPT_COVERAGE"]
        coverage.pop("recording_only_state_counts")
        coverage["recording_only_requires_reconciliation"] = 2
        report = self.run_gate()
        self.assertEqual(report["counts"]["recording_unknown_reconciliation_count"], 2)
        self.assertFalse(report["ready_for_research_intelligence"])

    def test_unknown_converted_to_terminal_state_unblocks_gate(self) -> None:
        counts = self.reports["12_TRANSCRIPT_COVERAGE"]["transcript_state_counts"]
        counts["UNKNOWN_REQUIRES_RECONCILIATION"] = 1
        blocked = self.run_gate()
        self.assertEqual(blocked["document_acquisition"], "RED")
        # Reconciliation resolves the case into an evidence-backed terminal state.
        counts["UNKNOWN_REQUIRES_RECONCILIATION"] = 0
        counts["OFFICIAL_TRANSCRIPT_SOURCE_MISSING"] = 1
        self.add_closure_state("SOURCE_MISSING_TERMINAL")
        report = self.run_gate()
        self.assertEqual(report["document_acquisition"], "GREEN", report["checks"])
        self.assertEqual(report["counts"]["explicit_terminal_residual_count"], 3)

    # --- residual philosophy ------------------------------------------------

    def test_active_retryable_closure_state_blocks_green(self) -> None:
        self.add_closure_state("TECHNICAL_FAILURE_RETRYABLE")
        report = self.run_gate()
        self.assertFalse(report["checks"]["no_retryable_closure_state"])
        self.assertEqual(report["counts"]["active_retryable_acquisition_count"], 1)
        self.assertEqual(report["document_acquisition"], "RED")

    def test_exhausted_technical_failure_is_a_counted_residual(self) -> None:
        self.add_closure_state("TECHNICAL_FAILURE_EXHAUSTED")
        report = self.run_gate()
        self.assertEqual(report["document_acquisition"], "GREEN", report["checks"])
        self.assertEqual(report["counts"]["explicit_terminal_residual_count"], 3)
        self.assertEqual(report["counts"]["active_retryable_acquisition_count"], 0)

    def test_not_offered_confirmed_is_not_unresolved(self) -> None:
        report = self.run_gate()
        self.assertTrue(report["ready_for_research_intelligence"])
        self.assertEqual(report["counts"]["transcript_unknown_reconciliation_count"], 0)

    def test_policy_blocked_no_alternate_is_explicit_not_unknown(self) -> None:
        report = self.run_gate()
        self.assertEqual(report["counts"]["unclassified_closure_states"], [])
        self.assertTrue(report["checks"]["closure_states_all_governed"])

    def test_ungoverned_closure_state_is_never_silently_accepted(self) -> None:
        # The schema CHECK rejects invented states, so drift can only arrive as a
        # new governed state the evaluator has not classified yet.
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_closure_state("SOMETHING_NEW")
        self.conn.rollback()
        governed = {
            *ACTIVE_CLOSURE_STATES,
            *EXPLICIT_TERMINAL_RESIDUAL_STATES,
            *ACQUIRED_CLOSURE_STATES,
        }
        self.assertEqual(governed, self._schema_closure_states())

    def _schema_closure_states(self) -> set[str]:
        sql = str(
            self.conn.execute(
                """SELECT sql FROM sqlite_master
                    WHERE type='table' AND name='document_acquisition_closure_states'"""
            ).fetchone()[0]
        )
        body = sql.split("final_state IN (", 1)[1].split(")", 1)[0]
        return {part.strip().strip("',") for part in body.split(",") if part.strip()}

    def test_every_governed_state_is_classified_by_the_gate(self) -> None:
        for state in sorted(self._schema_closure_states()):
            with self.subTest(state=state):
                self.set_closure_state(1, state)
                report = self.run_gate()
                self.assertEqual(report["counts"]["unclassified_closure_states"], [])
                expected_green = state not in ACTIVE_CLOSURE_STATES
                self.assertEqual(
                    report["document_acquisition"] == "GREEN", expected_green, report["checks"]
                )

    # --- fallback allowlist and structured evidence -------------------------

    def test_fallback_queued_from_unresolved_state_blocks_green(self) -> None:
        self.reports["12_TRANSCRIPT_COVERAGE"]["fallback_queued_from_unresolved_states"] = 1
        report = self.run_gate()
        self.assertFalse(report["checks"]["recording_fallback_allowlist_respected"])
        self.assertEqual(report["document_acquisition"], "RED")

    def test_missing_structured_evidence_fails_closed(self) -> None:
        for name in ("03_EXTERNAL_STORE_FAIL_CLOSED", "05_RESTART_RESUME"):
            with self.subTest(gate=name):
                reports = self.reports.pop(name)
                report = self.run_gate()
                self.assertEqual(report["document_acquisition"], "RED")
                self.reports[name] = reports

    def test_missing_report_input_is_red_not_a_crash(self) -> None:
        del self.reports["12_TRANSCRIPT_COVERAGE"]
        report = self.run_gate()
        self.assertEqual(report["document_acquisition"], "RED")
        self.assertFalse(report["checks"]["gate_inputs_present"])
        # An absent coverage ledger must never read as "reconciliation resolved".
        self.assertFalse(report["checks"]["transcript_reconciliation_resolved"])
        self.assertFalse(report["checks"]["recording_reconciliation_resolved"])
        self.assertEqual(
            [entry.split(":")[0] for entry in report["counts"]["missing_gate_inputs"]],
            ["12_TRANSCRIPT_COVERAGE.json"],
        )

    def test_structured_evidence_must_declare_its_own_gate(self) -> None:
        self.reports["05_RESTART_RESUME"]["gate"] = "external_store_fail_closed"
        report = self.run_gate()
        self.assertFalse(report["checks"]["restart_resume"])
        self.assertEqual(report["document_acquisition"], "RED")

    def test_failed_structured_evidence_is_not_overridden_by_markdown(self) -> None:
        self.reports["05_RESTART_RESUME"]["passed"] = False
        (self.output_dir / "05_P0_RESTART_REPORT.md").write_text(
            "Restart/resume: PASS\n", encoding="utf-8"
        )
        report = self.run_gate()
        self.assertFalse(report["checks"]["restart_resume"])
        self.assertEqual(report["document_acquisition"], "RED")

    # --- machine-readable artifact -----------------------------------------

    def test_machine_gate_reports_counts_not_only_pass_fail(self) -> None:
        self.reports["12_TRANSCRIPT_COVERAGE"]["transcript_state_counts"][
            "UNKNOWN_REQUIRES_RECONCILIATION"
        ] = 7
        self.reports["12_TRANSCRIPT_COVERAGE"]["recording_only_state_counts"][
            "UNKNOWN_REQUIRES_RECONCILIATION"
        ] = 3
        machine = write_gate_json(self.run_gate(), self.output_dir)
        written = json.loads(
            (self.output_dir / "FINAL_ACQUISITION_GATE.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written, machine)
        self.assertEqual(machine["transcript_unknown_reconciliation_count"], 7)
        self.assertEqual(machine["recording_unknown_reconciliation_count"], 3)
        self.assertFalse(machine["coverage_ledger_reconciled"])
        self.assertEqual(machine["document_acquisition"], "RED")
        self.assertFalse(machine["ready_for_research_intelligence"])

    def test_machine_gate_exposes_the_required_contract(self) -> None:
        machine = _machine_gate(self.run_gate())
        for field in (
            "document_acquisition",
            "ready_for_research_intelligence",
            "external_store_fail_closed",
            "restart_resume",
            "content_integrity",
            "deduplication",
            "blocked_host_policy",
            "blocked_cra_request_count",
            "transcript_unknown_reconciliation_count",
            "recording_unknown_reconciliation_count",
            "active_retryable_acquisition_count",
            "explicit_terminal_residual_count",
            "coverage_ledger_reconciled",
        ):
            with self.subTest(field=field):
                self.assertIn(field, machine)
        self.assertTrue(machine["coverage_ledger_reconciled"])
        self.assertTrue(machine["deduplication"])

    def test_duplicate_physical_paths_fail_the_dedup_gate(self) -> None:
        # Two blob rows pointing at the same stored file: content-addressed
        # reuse has broken even though each SHA256 row is individually unique.
        for sha in ("a" * 64, "b" * 64):
            self.conn.execute(
                """INSERT INTO response_blobs
                   (content_sha256,content_type,encoding,byte_size,local_path,
                    first_observed_at_utc,created_at_utc)
                   VALUES(?,'application/pdf',NULL,10,'/store/shared.pdf',?,?)""",
                (sha, NOW, NOW),
            )
        report = self.run_gate()
        self.assertFalse(report["checks"]["content_deduplication"])
        self.assertEqual(report["counts"]["duplicate_blob_path_count"], 1)
        self.assertEqual(report["document_acquisition"], "RED")

    def test_blocked_cra_requests_block_green(self) -> None:
        self.reports["17_HOST_POLICY_AUDIT"]["blocked_cra_requests_total"] = 4
        report = self.run_gate()
        self.assertFalse(report["checks"]["blocked_cra_requests_zero"])
        self.assertEqual(report["counts"]["blocked_cra_request_count"], 4)
        self.assertEqual(report["document_acquisition"], "RED")


if __name__ == "__main__":
    unittest.main()
