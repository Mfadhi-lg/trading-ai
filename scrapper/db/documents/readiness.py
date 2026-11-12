"""Read-only queries for durable document acceptance and readiness."""
from __future__ import annotations
import sqlite3
from ...domain.artifact_reconciliation import ClaimedExpectation
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)


def _same_event(expectation: ClaimedExpectation, artifact: sqlite3.Row) -> bool:
    return bool(
        (expectation.event_date is not None and artifact["event_date"] == expectation.event_date)
        or (expectation.period_end is not None and artifact["period_end"] == expectation.period_end)
        or (
            expectation.fiscal_year is not None
            and expectation.fiscal_quarter is not None
            and artifact["fiscal_year"] == expectation.fiscal_year
            and artifact["fiscal_quarter"] == expectation.fiscal_quarter
        )
    )


@atomic_repository('documents')
def _accepted_artifact(
    conn: sqlite3.Connection, expectation: ClaimedExpectation, *, _same_event=_same_event
) -> sqlite3.Row | None:
    candidates = execute(conn,
        """SELECT * FROM document_artifacts
           WHERE company_id=? AND artifact_type=? ORDER BY artifact_id""",
        (expectation.company_id, expectation.artifact_type),
    ).fetchall()
    for artifact in reversed(candidates):
        if not _same_event(expectation, artifact):
            continue
        artifact_id = int(artifact["artifact_id"])
        if expectation.artifact_type == "transcript":
            accepted = execute(conn,
                """SELECT 1 FROM transcripts WHERE artifact_id=?
                   AND origin='official' AND validation_status='accepted' LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        elif expectation.artifact_type == "recording":
            accepted = execute(conn,
                """SELECT 1 FROM recording_normalizations WHERE artifact_id=?
                   AND normalization_status='accepted' LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        elif expectation.artifact_type == "credit_rating":
            accepted = execute(conn,
                """SELECT 1 FROM credit_rating_normalizations WHERE artifact_id=?
                   AND normalization_status='accepted' LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        else:
            accepted = execute(conn,
                """SELECT 1 FROM document_extractions AS extraction
                   JOIN artifact_contents AS content USING (content_id)
                   WHERE content.artifact_id=? AND extraction.extraction_status='ok'
                   LIMIT 1""",
                (artifact_id,),
            ).fetchone()
        if accepted is not None:
            return artifact
    return None



@atomic_repository('documents')
def matching_artifact_sources(conn, expectation, *, _same_event=_same_event):
    return [
        row
        for row in execute(conn,
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


@atomic_repository('documents')
def latest_machine_transcription(conn, matching):
    machine = None
    matching_artifact_ids = tuple(
        dict.fromkeys(int(row["artifact_id"]) for row in matching)
    )
    if matching_artifact_ids:
        placeholders = ",".join("?" for _ in matching_artifact_ids)
        machine = execute(conn,
            f"""SELECT transcription_run_id
                FROM machine_transcription_runs
                WHERE transcript_artifact_id IN ({placeholders})
                  AND run_status='accepted'
                ORDER BY transcription_run_id DESC LIMIT 1""",
            matching_artifact_ids,
        ).fetchone()
    return machine
