"""D09 lease-safe delayed-artifact reconciliation orchestration."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Protocol

from ..config import DB_PATH, DOCUMENT_RECONCILIATION_EXPORTS
from ..db.artifact_reconciliation_repo import (
    ClaimedExpectation,
    claim_due_expectations,
    complete_artifact_check,
    preview_due_expectations,
    release_claims,
)
from ..db.database import connect, init_db
from ..db.repo import utc_now
from ..documents.availability import ArtifactCheckResult


RECONCILIATION_SCHEMA_VERSION = "1.0.0"


class ArtifactReconciliationAdapter(Protocol):
    """Source adapter boundary.

    Implementations may run the resumable D01-D07 discovery/acquisition pipeline.
    They return ``available`` only after the matching artifact is accepted by that
    pipeline; the scheduler itself never fabricates an accepted artifact.
    """

    def check(
        self,
        conn: sqlite3.Connection,
        expectation: ClaimedExpectation,
        checked_at_utc: str,
    ) -> ArtifactCheckResult: ...


def _same_event(expectation: ClaimedExpectation, artifact: sqlite3.Row) -> bool:
    matches = 0
    for field in ("event_date", "period_end", "fiscal_year", "fiscal_quarter"):
        expected = getattr(expectation, field)
        actual = artifact[field]
        if expected is not None and actual is not None:
            if expected != actual:
                return False
            matches += 1
    return matches > 0


def _accepted_artifact(
    conn: sqlite3.Connection, expectation: ClaimedExpectation
) -> sqlite3.Row | None:
    candidates = conn.execute(
        """SELECT * FROM document_artifacts
           WHERE company_id=? AND artifact_type=? ORDER BY artifact_id""",
        (expectation.company_id, expectation.artifact_type),
    ).fetchall()
    for artifact in reversed(candidates):
        if not _same_event(expectation, artifact):
            continue
        artifact_id = int(artifact["artifact_id"])
        if expectation.artifact_type == "transcript":
            accepted = conn.execute(
                """SELECT 1 FROM transcripts WHERE artifact_id=?
                   AND origin='official' AND validation_status='accepted' LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        elif expectation.artifact_type == "recording":
            accepted = conn.execute(
                """SELECT 1 FROM recording_normalizations WHERE artifact_id=?
                   AND normalization_status='accepted' LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        elif expectation.artifact_type == "credit_rating":
            accepted = conn.execute(
                """SELECT 1 FROM credit_rating_normalizations WHERE artifact_id=?
                   AND normalization_status='accepted' LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        else:
            accepted = conn.execute(
                """SELECT 1 FROM document_extractions AS extraction
                   JOIN artifact_contents AS content USING (content_id)
                   WHERE content.artifact_id=? AND extraction.extraction_status='ok'
                   LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        if accepted is not None:
            return artifact
    return None


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

        matching = [
            row
            for row in conn.execute(
                """SELECT artifact.*, source.document_url,
                          source.availability_status AS source_status
                   FROM document_artifacts AS artifact
                   LEFT JOIN artifact_sources AS source USING (artifact_id)
                   WHERE artifact.company_id=? AND artifact.artifact_type=?
                   ORDER BY artifact.artifact_id, source.artifact_source_id""",
                (expectation.company_id, expectation.artifact_type),
            ).fetchall()
            if _same_event(expectation, row)
        ]
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
            matching_artifact_ids = tuple(
                dict.fromkeys(int(row["artifact_id"]) for row in matching)
            )
            if matching_artifact_ids:
                placeholders = ",".join("?" for _ in matching_artifact_ids)
                machine = conn.execute(
                    f"""SELECT transcription_run_id
                        FROM machine_transcription_runs
                        WHERE transcript_artifact_id IN ({placeholders})
                          AND run_status='accepted'
                        ORDER BY transcription_run_id DESC LIMIT 1""",
                    matching_artifact_ids,
                ).fetchone()
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


def _default_output(checked_at_utc: str, run_label: str) -> Path:
    safe = checked_at_utc.replace(":", "").replace("+", "_")
    safe_label = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in run_label
    )
    return DOCUMENT_RECONCILIATION_EXPORTS / f"{safe}-{safe_label}.json"


def _write(payload: dict[str, Any], output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {**payload, "output": str(output.resolve())}


def reconcile_artifacts(
    *,
    due: bool,
    limit: int = 100,
    dry_run: bool = False,
    db_path: Path = DB_PATH,
    output: Path | None = None,
    adapter: ArtifactReconciliationAdapter | None = None,
    now_utc: Callable[[], str] = utc_now,
    worker_id: str | None = None,
) -> dict[str, Any]:
    if not due:
        raise ValueError("D09 reconciliation currently requires --due")
    adapter = adapter or PersistedArtifactAdapter()
    checked_at = now_utc()
    worker_id = worker_id or f"reconciler-{uuid.uuid4().hex}"
    conn = connect(db_path)
    claimed: tuple[ClaimedExpectation, ...] = ()
    completed_ids: set[int] = set()
    checks: list[dict[str, Any]] = []
    try:
        init_db(conn)
        if dry_run:
            due_rows = preview_due_expectations(conn, now_utc=checked_at, limit=limit)
            return _write(
                {
                    "schema_version": RECONCILIATION_SCHEMA_VERSION,
                    "mode": "dry_run",
                    "checked_at_utc": checked_at,
                    "worker_id": None,
                    "policy_source": "persisted_per_expectation",
                    "summary": {
                        "due_count": len(due_rows),
                        "claimed_count": 0,
                        "completed_count": 0,
                        "status_counts": {},
                    },
                    "due": [row.to_dict() for row in due_rows],
                    "checks": [],
                },
                output or _default_output(checked_at, "dry-run"),
            )
        claimed = claim_due_expectations(
            conn,
            now_utc=checked_at,
            limit=limit,
            worker_id=worker_id,
        )
        for expectation in claimed:
            try:
                result = adapter.check(conn, expectation, checked_at)
            except Exception as exc:
                result = ArtifactCheckResult(
                    "discovery_failed",
                    "reconciliation_adapter",
                    evidence={"adapter": type(adapter).__name__},
                    retryable=True,
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
            check_id = complete_artifact_check(
                conn,
                expectation=expectation,
                result=result,
                checked_at_utc=checked_at,
            )
            completed_ids.add(expectation.expectation_id)
            state = conn.execute(
                """SELECT availability_status, attempt_count, next_check_at_utc,
                          terminal_reason FROM artifact_expectations
                   WHERE expectation_id=?""",
                (expectation.expectation_id,),
            ).fetchone()
            checks.append(
                {
                    "check_id": check_id,
                    "expectation_id": expectation.expectation_id,
                    "event_key": expectation.event_key,
                    "artifact_type": expectation.artifact_type,
                    "result_status": result.result_status,
                    "source": result.source,
                    "requested_url": result.requested_url,
                    "discovered_artifact_id": result.discovered_artifact_id,
                    "retryable": result.retryable,
                    "attempt_count": int(state["attempt_count"]),
                    "next_check_at_utc": state["next_check_at_utc"],
                    "terminal_reason": state["terminal_reason"],
                }
            )
    finally:
        remaining = tuple(
            row for row in claimed if row.expectation_id not in completed_ids
        )
        if remaining:
            release_claims(conn, remaining, released_at_utc=checked_at)
        conn.close()
    counts = Counter(row["result_status"] for row in checks)
    return _write(
        {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "mode": "execute",
            "checked_at_utc": checked_at,
            "worker_id": worker_id,
            "policy_source": "persisted_per_expectation",
            "summary": {
                "due_count": len(claimed),
                "claimed_count": len(claimed),
                "completed_count": len(checks),
                "status_counts": dict(sorted(counts.items())),
            },
            "due": [row.to_dict() for row in claimed],
            "checks": checks,
        },
        output or _default_output(checked_at, worker_id),
    )
