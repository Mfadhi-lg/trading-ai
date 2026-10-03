"""AC-FINAL-01: recording fallback must be driven by a terminal-state allowlist."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scrapper.db.database import connect, init_db
from scrapper.pipeline.build_transcript_coverage import (
    RECORDING_FALLBACK_ELIGIBLE_STATES,
    build_coverage,
    recording_fallback_decision,
)


FULL_SCOPE = "document-acquisition-full-v1"
NOW = "2026-08-17T00:00:00+00:00"


class Corpus:
    """Minimal acquisition corpus that can express one transcript state per company."""

    def __init__(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self._dir.name) / "coverage.db")
        init_db(self.conn, apply_migrations=True)
        self.conn.execute(
            """INSERT INTO artifact_download_scopes
               (scope_id,scope_kind,description,artifact_types_json,policy_json,
                created_at_utc,updated_at_utc)
               VALUES(?,'custom','test','["transcript"]','{}',?,?)""",
            (FULL_SCOPE, NOW, NOW),
        )
        self._company = 0
        self._blob = 0

    def close(self) -> None:
        self.conn.close()
        self._dir.cleanup()

    def _new_blob(self) -> int:
        self._blob += 1
        cursor = self.conn.execute(
            """INSERT INTO response_blobs
               (content_sha256,content_type,encoding,byte_size,local_path,
                first_observed_at_utc,created_at_utc)
               VALUES(?,'application/pdf',NULL,10,?,?,?)""",
            (f"{self._blob:064d}", f"/store/{self._blob}.pdf", NOW, NOW),
        )
        return int(cursor.lastrowid)

    def new_company(self) -> int:
        self._company += 1
        company_id = self._company
        self.conn.execute(
            "INSERT INTO companies(company_id,current_legal_name,status) VALUES(?,?,'active')",
            (company_id, f"Company {company_id}"),
        )
        self.conn.execute(
            """INSERT INTO artifact_discovery_pages
               (discovery_page_id,company_id,discovered_from_source,discovery_page_url,
                publisher_claim,blob_id,parser_name,parser_version,first_observed_at_utc)
               VALUES(?,?,'company_ir',?,NULL,?,'test','1',?)""",
            (company_id, company_id, f"https://example.test/{company_id}", self._new_blob(), NOW),
        )
        return company_id

    def add_artifact(
        self,
        company_id: int,
        artifact_type: str,
        *,
        labels: tuple[str, ...] = ("Q1FY26 Concall",),
        event_date: str | None = "2026-05-01",
        availability_status: str = "available",
        in_scope: bool = True,
    ) -> int:
        cursor = self.conn.execute(
            """INSERT INTO document_artifacts
               (company_id,artifact_key,artifact_type,publisher,title_raw,event_date,
                period_end,fiscal_year,fiscal_quarter,published_at_utc,
                first_observed_at_utc,preferred_content_id,acquisition_status,
                transcript_preference_status)
               VALUES(?,?,?,'issuer',?,?,NULL,NULL,NULL,NULL,?,NULL,'discovered','not_applicable')""",
            (
                company_id,
                f"{company_id}:{artifact_type}:{event_date}:{labels}",
                artifact_type,
                f"{artifact_type} {company_id}",
                event_date,
                NOW,
            ),
        )
        artifact_id = int(cursor.lastrowid)
        for position, label in enumerate(labels):
            self.conn.execute(
                """INSERT INTO artifact_sources
                   (artifact_id,discovery_page_id,discovered_from_source,discovery_page_url,
                    document_url,publisher_claim,exchange_announcement_id,source_position,
                    source_title_raw,classification_evidence_json,is_primary_source,
                    first_observed_at_utc,last_checked_at_utc,availability_status,
                    automation_status,document_originator,retrieval_source_type,retrieval_host,
                    published_at_utc,available_at_utc,availability_basis,recovery_target_id,
                    recovery_candidate_id,matching_confidence,recovery_evidence_json)
                   VALUES(?,?,'company_ir',?,?,NULL,NULL,?,?,?,1,?,?,?,'AVAILABLE',NULL,
                          'issuer_ir','example.test',NULL,NULL,NULL,NULL,NULL,NULL,'{}')""",
                (
                    artifact_id,
                    company_id,
                    f"https://example.test/{company_id}",
                    f"https://example.test/{artifact_id}/{position}.pdf",
                    position,
                    label,
                    json.dumps({"period_label_raw": label}),
                    NOW,
                    NOW,
                    availability_status,
                ),
            )
        if in_scope:
            self.conn.execute(
                "INSERT INTO artifact_download_scope_artifacts(scope_id,artifact_id) VALUES(?,?)",
                (FULL_SCOPE, artifact_id),
            )
        return artifact_id

    def add_content(
        self,
        artifact_id: int,
        *,
        extraction_status: str,
        is_official: int = 1,
        content_origin: str = "publisher_original",
    ) -> int:
        blob_id = self._new_blob()
        cursor = self.conn.execute(
            """INSERT INTO artifact_contents
               (artifact_id,blob_id,media_type,content_sha256,byte_size,extraction_status,
                is_official,content_origin,created_at_utc)
               VALUES(?,?,'application/pdf',?,10,?,?,?,?)""",
            (
                artifact_id,
                blob_id,
                f"{blob_id:064d}",
                extraction_status,
                is_official,
                content_origin,
                NOW,
            ),
        )
        return int(cursor.lastrowid)

    def add_extraction(self, content_id: int) -> int:
        cursor = self.conn.execute(
            """INSERT INTO document_extractions
               (content_id,parser_name,parser_version,ocr_engine,ocr_engine_version,
                options_json,started_at_utc,completed_at_utc,extraction_status,
                page_or_slide_count,native_page_count,ocr_page_count,unusable_page_count,
                error_json,created_at_utc)
               VALUES(?,'test','1','none','0','{}',?,?,'ok',1,1,0,0,'{}',?)""",
            (content_id, NOW, NOW, NOW),
        )
        return int(cursor.lastrowid)

    def add_queue(
        self, artifact_id: int, *, status: str, last_result_status: str | None = None
    ) -> None:
        self.conn.execute(
            """INSERT INTO artifact_download_queue
               (artifact_id,requested_url,target_host,priority,scope_id,status,attempt_count,
                next_attempt_at_utc,lease_owner,lease_expires_at_utc,last_result_status,
                last_error,created_at_utc,updated_at_utc)
               VALUES(?,?,'example.test',1,?,?,1,NULL,NULL,NULL,?,NULL,?,?)""",
            (
                artifact_id,
                f"https://example.test/{artifact_id}.pdf",
                FULL_SCOPE,
                status,
                last_result_status,
                NOW,
                NOW,
            ),
        )

    def add_transcript_row(self, artifact_id: int, *, origin: str) -> None:
        source_media_content_id: int | None = None
        page_start: int | None = None
        page_end: int | None = None
        if origin == "official":
            content_id = self.add_content(artifact_id, extraction_status="ok", is_official=1)
            extraction_id: int | None = self.add_extraction(content_id)
            page_start, page_end = 1, 1
        else:
            content_id = self.add_content(
                artifact_id,
                extraction_status="ok",
                is_official=0,
                content_origin="generated_derivative",
            )
            extraction_id = None
            source_media_content_id = self.add_content(
                artifact_id, extraction_status="not_started", is_official=0
            )
        self.conn.execute(
            """INSERT INTO transcripts
               (artifact_id,content_id,extraction_id,origin,source_media_content_id,language,
                engine,model_version,options_json,document_marker_raw,document_marker_page,
                created_at_utc,validation_status,segment_count,page_start,page_end,
                prepared_segment_count,qa_segment_count,unknown_segment_count,error_json)
               VALUES(?,?,?,?,?,'en','test','1','{}',NULL,NULL,?,'accepted',1,?,?,
                      1,0,0,'{}')""",
            (
                artifact_id,
                content_id,
                extraction_id,
                origin,
                source_media_content_id,
                NOW,
                page_start,
                page_end,
            ),
        )

    def transcript_in_state(
        self, state: str, *, with_recording: bool = True, label: str | None = None
    ) -> tuple[int, int]:
        """Create one company whose single transcript resolves to ``state``."""

        company_id = self.new_company()
        period = label or f"Q1FY26 Concall {company_id}"
        availability = "not_offered" if state == "OFFICIAL_TRANSCRIPT_NOT_OFFERED" else "available"
        transcript_id = self.add_artifact(
            company_id, "transcript", labels=(period,), availability_status=availability
        )
        if state == "OFFICIAL_TRANSCRIPT_AVAILABLE":
            self.add_transcript_row(transcript_id, origin="official")
        elif state == "MACHINE_TRANSCRIPT_ALREADY_AVAILABLE":
            self.add_transcript_row(transcript_id, origin="machine_generated")
        elif state == "OFFICIAL_TRANSCRIPT_UNUSABLE":
            self.add_content(transcript_id, extraction_status="failed")
        elif state == "OFFICIAL_TRANSCRIPT_NOT_OFFERED":
            pass
        elif state == "OFFICIAL_TRANSCRIPT_SOURCE_MISSING":
            self.add_queue(transcript_id, status="TERMINAL_FAILURE", last_result_status="http_404")
        elif state == "OFFICIAL_TRANSCRIPT_INACCESSIBLE":
            self.add_queue(transcript_id, status="TERMINAL_FAILURE", last_result_status="http_403")
        elif state == "TECHNICAL_FAILURE":
            self.add_queue(transcript_id, status="PENDING")
        elif state == "UNKNOWN_REQUIRES_RECONCILIATION":
            pass  # live source, no download job ever registered
        else:  # pragma: no cover - guards typos in the test itself
            raise AssertionError(f"unsupported state {state}")
        recording_id = 0
        if with_recording:
            recording_id = self.add_artifact(company_id, "recording", labels=(period,))
        return transcript_id, recording_id

    def coverage(self) -> dict[str, Any]:
        report, scope = build_coverage(self.conn, full_scope_id=FULL_SCOPE)
        self.last_scope = scope
        return report


def fallback_states(report: dict[str, Any]) -> dict[int, str]:
    return {
        int(row["transcript_artifact_id"]): str(row["transcript_state"])
        for row in report["ledger"]
        if row["fallback_state"] == "FALLBACK_REQUIRED"
    }


class RecordingFallbackDecisionTests(unittest.TestCase):
    """The pure decision must be a positive allowlist so new states fail closed."""

    def decide(self, state: str, **overrides: Any) -> tuple[bool, str]:
        kwargs: dict[str, Any] = {
            "transcript_state": state,
            "recording_present": True,
            "identity_resolved": True,
            "in_active_corpus": True,
            "period_already_satisfied": False,
        }
        kwargs.update(overrides)
        return recording_fallback_decision(**kwargs)

    def test_every_terminal_state_is_eligible(self) -> None:
        for state in sorted(RECORDING_FALLBACK_ELIGIBLE_STATES):
            with self.subTest(state=state):
                self.assertTrue(self.decide(state)[0])

    def test_unknown_future_state_fails_closed(self) -> None:
        eligible, reason = self.decide("SOURCE_RECHECK_REQUIRED")
        self.assertFalse(eligible)
        self.assertEqual(reason, "transcript_state_not_in_fallback_allowlist")

    def test_unresolved_states_are_not_eligible(self) -> None:
        for state in ("TECHNICAL_FAILURE", "UNKNOWN_REQUIRES_RECONCILIATION"):
            with self.subTest(state=state):
                self.assertFalse(self.decide(state)[0])

    def test_satisfied_states_are_not_eligible(self) -> None:
        for state in ("OFFICIAL_TRANSCRIPT_AVAILABLE", "MACHINE_TRANSCRIPT_ALREADY_AVAILABLE"):
            with self.subTest(state=state):
                self.assertFalse(self.decide(state)[0])

    def test_each_positive_condition_is_required(self) -> None:
        state = "OFFICIAL_TRANSCRIPT_UNUSABLE"
        self.assertEqual(
            self.decide(state, recording_present=False),
            (False, "no_recording_for_period"),
        )
        self.assertEqual(
            self.decide(state, identity_resolved=False),
            (False, "company_period_identity_unresolved"),
        )
        self.assertEqual(
            self.decide(state, in_active_corpus=False),
            (False, "period_not_in_active_acquisition_corpus"),
        )
        self.assertEqual(
            self.decide(state, period_already_satisfied=True),
            (False, "period_already_satisfied_by_usable_transcript"),
        )


class RecordingFallbackLedgerTests(unittest.TestCase):
    """End-to-end ledger behaviour over a real acquisition schema."""

    def setUp(self) -> None:
        self.corpus = Corpus()
        self.addCleanup(self.corpus.close)

    def assert_state_and_fallback(self, state: str, *, expected_fallback: bool) -> None:
        transcript_id, _ = self.corpus.transcript_in_state(state)
        report = self.corpus.coverage()
        row = next(
            row for row in report["ledger"] if row["transcript_artifact_id"] == transcript_id
        )
        self.assertEqual(row["transcript_state"], state, row["transcript_evidence"])
        self.assertEqual(row["fallback_state"] == "FALLBACK_REQUIRED", expected_fallback, row)
        self.assertEqual(report["fallback_queued_from_unresolved_states"], 0)

    def test_official_transcript_unusable_queues_fallback(self) -> None:
        self.assert_state_and_fallback("OFFICIAL_TRANSCRIPT_UNUSABLE", expected_fallback=True)

    def test_official_transcript_not_offered_queues_fallback(self) -> None:
        self.assert_state_and_fallback("OFFICIAL_TRANSCRIPT_NOT_OFFERED", expected_fallback=True)

    def test_official_transcript_source_missing_queues_fallback(self) -> None:
        self.assert_state_and_fallback("OFFICIAL_TRANSCRIPT_SOURCE_MISSING", expected_fallback=True)

    def test_official_transcript_inaccessible_queues_fallback(self) -> None:
        self.assert_state_and_fallback("OFFICIAL_TRANSCRIPT_INACCESSIBLE", expected_fallback=True)

    def test_technical_failure_never_queues_fallback(self) -> None:
        self.assert_state_and_fallback("TECHNICAL_FAILURE", expected_fallback=False)

    def test_unknown_reconciliation_never_queues_fallback(self) -> None:
        self.assert_state_and_fallback("UNKNOWN_REQUIRES_RECONCILIATION", expected_fallback=False)

    def test_official_transcript_available_never_queues_fallback(self) -> None:
        self.assert_state_and_fallback("OFFICIAL_TRANSCRIPT_AVAILABLE", expected_fallback=False)

    def test_machine_transcript_available_never_queues_fallback(self) -> None:
        self.assert_state_and_fallback(
            "MACHINE_TRANSCRIPT_ALREADY_AVAILABLE", expected_fallback=False
        )

    def test_acquired_but_unreconciled_bytes_are_technical_not_unknown(self) -> None:
        """Downloaded publisher bytes awaiting D03/provenance are not UNKNOWN."""

        company_id = self.corpus.new_company()
        transcript_id = self.corpus.add_artifact(company_id, "transcript")
        self.corpus.add_content(transcript_id, extraction_status="not_started", is_official=0)
        self.corpus.add_queue(
            transcript_id, status="SUCCEEDED", last_result_status="validated"
        )
        self.corpus.add_artifact(company_id, "recording")
        report = self.corpus.coverage()
        row = next(
            row for row in report["ledger"] if row["transcript_artifact_id"] == transcript_id
        )
        self.assertEqual(row["transcript_state"], "TECHNICAL_FAILURE")
        self.assertEqual(
            row["transcript_evidence"]["basis"], "acquired_content_extraction_pending"
        )
        self.assertNotEqual(row["fallback_state"], "FALLBACK_REQUIRED")
        self.assertEqual(
            report["transcript_state_counts"]["UNKNOWN_REQUIRES_RECONCILIATION"], 0
        )

    def test_acquired_bytes_without_official_provenance_are_technical(self) -> None:
        company_id = self.corpus.new_company()
        transcript_id = self.corpus.add_artifact(company_id, "transcript")
        self.corpus.add_content(transcript_id, extraction_status="ok", is_official=0)
        self.corpus.add_queue(
            transcript_id, status="SKIPPED_ALREADY_HAVE_CONTENT", last_result_status=""
        )
        report = self.corpus.coverage()
        row = next(
            row for row in report["ledger"] if row["transcript_artifact_id"] == transcript_id
        )
        self.assertEqual(row["transcript_state"], "TECHNICAL_FAILURE")
        self.assertEqual(
            row["transcript_evidence"]["basis"],
            "acquired_content_official_provenance_unresolved",
        )

    def test_generated_derivative_content_does_not_mask_acquisition_state(self) -> None:
        """Only publisher bytes prove the source produced something."""

        company_id = self.corpus.new_company()
        transcript_id = self.corpus.add_artifact(
            company_id, "transcript", availability_status="not_offered"
        )
        self.corpus.add_content(
            transcript_id,
            extraction_status="not_started",
            is_official=0,
            content_origin="generated_derivative",
        )
        report = self.corpus.coverage()
        row = next(
            row for row in report["ledger"] if row["transcript_artifact_id"] == transcript_id
        )
        self.assertEqual(row["transcript_state"], "OFFICIAL_TRANSCRIPT_NOT_OFFERED")

    def test_ambiguous_period_identity_blocks_fallback(self) -> None:
        company_id = self.corpus.new_company()
        transcript_id = self.corpus.add_artifact(
            company_id, "transcript", labels=("Q1FY26 Concall", "Q2FY26 Concall")
        )
        self.corpus.add_content(transcript_id, extraction_status="failed")
        self.corpus.add_artifact(company_id, "recording", labels=("Q1FY26 Concall",))
        report = self.corpus.coverage()
        row = next(
            row for row in report["ledger"] if row["transcript_artifact_id"] == transcript_id
        )
        self.assertEqual(row["transcript_state"], "OFFICIAL_TRANSCRIPT_UNUSABLE")
        self.assertNotEqual(row["fallback_state"], "FALLBACK_REQUIRED")
        self.assertEqual(row["fallback_reason"], "company_period_identity_unresolved")

    def test_missing_recording_blocks_fallback(self) -> None:
        transcript_id, _ = self.corpus.transcript_in_state(
            "OFFICIAL_TRANSCRIPT_UNUSABLE", with_recording=False
        )
        report = self.corpus.coverage()
        row = next(
            row for row in report["ledger"] if row["transcript_artifact_id"] == transcript_id
        )
        self.assertEqual(row["fallback_state"], "RECORDING_UNAVAILABLE")
        self.assertEqual(report["fallback_required"], 0)

    def test_out_of_scope_transcript_blocks_fallback(self) -> None:
        company_id = self.corpus.new_company()
        transcript_id = self.corpus.add_artifact(
            company_id, "transcript", labels=("Q1FY26 Concall",), in_scope=False
        )
        self.corpus.add_content(transcript_id, extraction_status="failed")
        self.corpus.add_artifact(company_id, "recording", labels=("Q1FY26 Concall",))
        report = self.corpus.coverage()
        row = next(
            row for row in report["ledger"] if row["transcript_artifact_id"] == transcript_id
        )
        self.assertEqual(row["fallback_reason"], "period_not_in_active_acquisition_corpus")
        self.assertEqual(report["fallback_required"], 0)

    def test_mixed_corpus_only_queues_terminal_states(self) -> None:
        expected: dict[int, bool] = {}
        for state in (
            "OFFICIAL_TRANSCRIPT_UNUSABLE",
            "OFFICIAL_TRANSCRIPT_NOT_OFFERED",
            "OFFICIAL_TRANSCRIPT_SOURCE_MISSING",
            "OFFICIAL_TRANSCRIPT_INACCESSIBLE",
            "TECHNICAL_FAILURE",
            "UNKNOWN_REQUIRES_RECONCILIATION",
            "OFFICIAL_TRANSCRIPT_AVAILABLE",
            "MACHINE_TRANSCRIPT_ALREADY_AVAILABLE",
        ):
            transcript_id, _ = self.corpus.transcript_in_state(state)
            expected[transcript_id] = state in RECORDING_FALLBACK_ELIGIBLE_STATES
        report = self.corpus.coverage()
        queued = fallback_states(report)
        self.assertEqual(
            {artifact_id for artifact_id, wanted in expected.items() if wanted},
            set(queued),
        )
        self.assertEqual(report["fallback_queued_by_transcript_state"].get("TECHNICAL_FAILURE", 0), 0)
        self.assertEqual(
            report["fallback_queued_by_transcript_state"].get(
                "UNKNOWN_REQUIRES_RECONCILIATION", 0
            ),
            0,
        )
        self.assertEqual(report["fallback_queued_from_unresolved_states"], 0)

    def test_rebuilding_fallback_is_idempotent(self) -> None:
        for state in (
            "OFFICIAL_TRANSCRIPT_UNUSABLE",
            "TECHNICAL_FAILURE",
            "UNKNOWN_REQUIRES_RECONCILIATION",
            "OFFICIAL_TRANSCRIPT_AVAILABLE",
        ):
            self.corpus.transcript_in_state(state)
        first, first_scope = build_coverage(self.corpus.conn, full_scope_id=FULL_SCOPE)
        second, second_scope = build_coverage(self.corpus.conn, full_scope_id=FULL_SCOPE)
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
        )
        self.assertEqual(
            json.dumps(first_scope, sort_keys=True), json.dumps(second_scope, sort_keys=True)
        )


if __name__ == "__main__":
    unittest.main()
