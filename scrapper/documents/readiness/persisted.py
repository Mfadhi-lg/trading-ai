"""Readiness outcomes from durable D01-D07 evidence."""
from __future__ import annotations
import sqlite3
from typing import Any
from ...domain.artifact_reconciliation import ClaimedExpectation
from ...db.documents.readiness import _same_event, _accepted_artifact, matching_artifact_sources, latest_machine_transcription
from ..availability import ArtifactCheckResult
class PersistedArtifactAdapter:
    """Reconcile source-adapter work already persisted by D01-D07.

    Production publisher adapters can wrap this class: check their source, pass a
    newly discovered item through D01-D07, then call this check to verify the
    durable accepted handoff before reporting availability.
    """

    source = "persisted_artifact_pipeline"

    def check(
        self,
        conn: sqlite3.Connection,
        expectation: ClaimedExpectation,
        checked_at_utc: str,
        *, _accepted_artifact=_accepted_artifact, _same_event=_same_event,
    ) -> ArtifactCheckResult:
        artifact = _accepted_artifact(conn, expectation)
        if artifact is not None:
            return ArtifactCheckResult(
                "available",
                self.source,
                evidence={
                    "acceptance_basis": "accepted_d01_d07_artifact",
                    "artifact_type": expectation.artifact_type,
                },
                discovered_artifact_id=int(artifact["artifact_id"]),
            )

        matching = matching_artifact_sources(conn, expectation, _same_event=_same_event)
        explicit = [
            row for row in matching
            if row["source_status"] in {
                "not_offered", "not_applicable", "source_not_covered",
                "inaccessible", "discovery_failed", "unknown",
            }
        ]
        if explicit and len(explicit) == len(matching):
            status_order = (
                "inaccessible", "discovery_failed", "unknown",
                "not_offered", "not_applicable", "source_not_covered",
            )
            status = next(
                item for item in status_order
                if any(row["source_status"] == item for row in explicit)
            )
            selected = next(row for row in explicit if row["source_status"] == status)
            return ArtifactCheckResult(
                status,
                self.source,
                requested_url=selected["document_url"],
                evidence={"basis": "persisted_explicit_source_status"},
                retryable=status in {"discovery_failed", "unknown"},
            )

        machine = None
        if expectation.artifact_type == "transcript":
            machine = latest_machine_transcription(conn, matching)
        return ArtifactCheckResult(
            "expected_not_yet_published",
            self.source,
            evidence={
                "basis": "event_evidenced_artifact_not_accepted",
                "recording_fallback_present": machine is not None,
                "machine_transcription_run_id": (
                    int(machine["transcription_run_id"]) if machine is not None else None
                ),
            },
            retryable=True,
        )
