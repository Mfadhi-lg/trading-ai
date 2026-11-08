from __future__ import annotations

import unittest

from scrapper.pipeline.finalize_acquisition_states import _state


class Row(dict):
    def __getattr__(self, name: str):
        return self[name]


class DocumentAcquisitionClosureTests(unittest.TestCase):
    def test_validated_content_dominates_failed_sources(self) -> None:
        state, _ = _state(
            contents=[Row(extraction_status="failed")],
            sources=[Row(availability_status="available")],
            jobs=[Row(status="TERMINAL_FAILURE", last_result_status="http_404", last_error="")],
            recovery=None,
            quarantined=False,
        )
        self.assertEqual(state, "ACQUIRED_VALIDATED")

    def test_unknown_recovery_is_never_called_recovered(self) -> None:
        state, _ = _state(
            contents=[],
            sources=[Row(availability_status="available")],
            jobs=[],
            recovery=Row(status="PENDING", content_class="UNKNOWN_RATING_DOCUMENT"),
            quarantined=False,
        )
        self.assertEqual(state, "TECHNICAL_FAILURE_RETRYABLE")

    def test_dead_url_is_distinct_from_not_offered(self) -> None:
        missing, _ = _state(
            contents=[],
            sources=[Row(availability_status="available")],
            jobs=[Row(status="TERMINAL_FAILURE", last_result_status="http_404", last_error="http_404")],
            recovery=None,
            quarantined=False,
        )
        not_offered, _ = _state(
            contents=[],
            sources=[Row(availability_status="not_offered")],
            jobs=[],
            recovery=None,
            quarantined=False,
        )
        self.assertEqual(missing, "SOURCE_MISSING_TERMINAL")
        self.assertEqual(not_offered, "NOT_OFFERED_CONFIRMED")

    def test_recovered_partial_evidence_remains_explicit(self) -> None:
        state, _ = _state(
            contents=[Row(extraction_status="ok")],
            sources=[Row(availability_status="available")],
            jobs=[],
            recovery=Row(status="RECOVERED", content_class="RATING_ACTION_INTIMATION"),
            quarantined=False,
        )
        self.assertEqual(state, "ACQUIRED_PARTIAL_EVIDENCE")

    def test_blocked_publisher_content_is_quarantined_not_validated(self) -> None:
        # 924 rating documents were pulled straight from crisil.com/icra.in before
        # the host policy was enforced. Possessing the bytes must not certify the
        # acquisition, or the violation is laundered into ACQUIRED_VALIDATED.
        state, basis = _state(
            contents=[Row(extraction_status="ok")],
            sources=[Row(availability_status="available")],
            jobs=[Row(status="SUCCEEDED", last_result_status="validated", last_error="")],
            recovery=None,
            quarantined=False,
            policy_blocked_source=True,
        )
        self.assertEqual(state, "QUARANTINED_INVALID_SOURCE")
        self.assertEqual(basis, "content_acquired_from_blocked_publisher")

    def test_blocked_publisher_outranks_a_successful_recovery(self) -> None:
        state, _ = _state(
            contents=[Row(extraction_status="ok")],
            sources=[Row(availability_status="available")],
            jobs=[],
            recovery=Row(status="RECOVERED", content_class="FULL_RATING_RATIONALE"),
            quarantined=False,
            policy_blocked_source=True,
        )
        self.assertEqual(state, "QUARANTINED_INVALID_SOURCE")

    def test_recovery_quarantine_outranks_held_content(self) -> None:
        state, basis = _state(
            contents=[Row(extraction_status="ok")],
            sources=[Row(availability_status="available")],
            jobs=[],
            recovery=None,
            quarantined=True,
        )
        self.assertEqual(state, "QUARANTINED_INVALID_SOURCE")
        self.assertEqual(basis, "recovery_quarantine")

    def test_clean_provenance_still_validates(self) -> None:
        state, _ = _state(
            contents=[Row(extraction_status="ok")],
            sources=[Row(availability_status="available")],
            jobs=[],
            recovery=None,
            quarantined=False,
            policy_blocked_source=False,
        )
        self.assertEqual(state, "ACQUIRED_VALIDATED")


if __name__ == "__main__":
    unittest.main()
