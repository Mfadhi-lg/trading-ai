"""Read-only queries for official alternate-source recovery strategies."""
from __future__ import annotations
from typing import Any
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)

@atomic_repository('documents')
def list_saved_issuer_pages(conn: Any, *, company_id: int) -> list[Any]:
    return execute(conn,
        """SELECT page.discovery_page_url,page.blob_id,blob.local_path
             FROM artifact_discovery_pages AS page
             JOIN response_blobs AS blob USING (blob_id)
            WHERE page.company_id=? AND page.discovered_from_source='company_ir'
            ORDER BY page.discovery_page_id""",
        (company_id,),
    ).fetchall()


@atomic_repository('documents')
def list_target_candidates(conn: Any, *, recovery_target_id: int) -> list[Any]:
    return execute(conn,
        """SELECT candidate_id,confidence,attached_source_id
             FROM artifact_recovery_candidates
            WHERE recovery_target_id=? ORDER BY candidate_id""",
        (recovery_target_id,),
    ).fetchall()
