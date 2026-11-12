"""Read-only, explicitly scoped D09 evidence rows; never read current readiness."""
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)


@atomic_repository('documents')
def document_check_rows(conn, *, expectation_id):
    cursor = execute(conn,
        """SELECT expectation.expectation_id, expectation.company_id,
                  expectation.artifact_type, expectation.created_at_utc,
                  expectation.expectation_basis, expectation.expected_after_utc,
                  event.event_id, event.event_key, event.event_type, event.period_end,
                  event.event_date, event.fiscal_year, event.fiscal_quarter,
                  event.evidenced_at_utc, event.evidence_source,
                  event.evidence_json AS event_evidence_json,
                  check_event.check_id, check_event.checked_at_utc,
                  check_event.source, check_event.requested_url,
                  check_event.result_status, check_event.evidence_json,
                  check_event.discovered_artifact_id, check_event.error_json,
                  check_event.retryable
           FROM artifact_expectations AS expectation
           JOIN artifact_events AS event USING (event_id)
           LEFT JOIN artifact_check_events AS check_event USING (expectation_id)
           WHERE expectation.expectation_id=?
           ORDER BY check_event.check_id""",
        (expectation_id,),
    )
    columns = [item[0] for item in cursor.description]
    return tuple(dict(zip(columns, row)) for row in cursor.fetchall())
