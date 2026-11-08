"""Existing recovery updates after generic download persistence/completion."""
from __future__ import annotations
from ...db.document_recovery_repo import reconcile_recovery_for_source, reopen_recovery_after_download_failure
from ...db.document_download_queue_repo import blocked_terms_hosts

def reconcile_download_success(conn, *, persisted, result, now, reconcile_recovery_for_source=reconcile_recovery_for_source):
    if result.is_validated and persisted.content_id is not None:
        for artifact_source_id in persisted.artifact_source_ids:
            reconcile_recovery_for_source(
                conn,
                artifact_source_id=artifact_source_id,
                content_id=persisted.content_id,
                reconciled_at_utc=now(),
            )


def reconcile_download_outcome(conn, *, job, persisted, result, status, now, blocked_terms_hosts=blocked_terms_hosts, reopen_recovery_after_download_failure=reopen_recovery_after_download_failure):
    if (result.http_status == 200 and result.detected_media_type == "text/html"
            and result.body and persisted.artifact_source_ids):
        from ...documents.landing_page import issuer_pdf_link
        from ...pipeline.document_download_recovery import register_replacement_document_url
        attachment = issuer_pdf_link(
            result.body, page_url=result.final_url or str(job["requested_url"]),
            artifact_type=str(job["artifact_type"]), blocked_hosts=blocked_terms_hosts(conn))
        if attachment:
            register_replacement_document_url(
                conn, artifact_id=int(job["artifact_id"]),
                replaces_artifact_source_id=persisted.artifact_source_ids[0],
                replacement_url=attachment, reason_code="RESOLVER_UPDATED",
                reason_detail="Unambiguous PDF attachment on the issuer document landing page",
                evidence={"landing_url":result.final_url,"download_attempt_id":persisted.download_attempt_id},
            )
    if status == "TERMINAL_FAILURE":
        for artifact_source_id in persisted.artifact_source_ids:
            reopen_recovery_after_download_failure(
                conn,
                artifact_source_id=artifact_source_id,
                failed_at_utc=now(),
            )
