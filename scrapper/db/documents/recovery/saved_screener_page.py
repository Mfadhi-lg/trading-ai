"""Read-only selection of saved rejected-page link repair candidates."""
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)

@atomic_repository('documents')
def list_repair_candidates(conn, *, period, limit, months, promoter_version):
    placeholders = ",".join("?" for _ in months)
    rows = execute(conn, f"""WITH ranked AS (
      SELECT f.company_id,p.parse_id,max(f.fetched_at_utc) fetched_at_utc,
             row_number() OVER (PARTITION BY f.company_id
               ORDER BY max(f.fetched_at_utc) DESC,p.parse_id DESC) rn
      FROM parse_artifacts p
      JOIN fetch_observations f ON f.blob_id=p.blob_id AND f.source='screener'
        AND f.resource_kind='company_page' AND f.company_id IS NOT NULL
      JOIN screener_collection_scope scope ON scope.company_id=f.company_id
        AND scope.eligibility='eligible'
      JOIN source_identifiers sid ON sid.company_id=f.company_id
        AND sid.source='screener' AND sid.mapping_status='verified'
      JOIN document_links link ON link.parse_id=p.parse_id
        AND link.document_type IN ('concall_ppt','concall_transcript','concall_recording')
        AND link.period_label_raw IN ({placeholders})
      WHERE p.parse_status='ok' AND p.validation_status='failed'
        AND EXISTS (SELECT 1 FROM validation_results v WHERE v.parse_id=p.parse_id
          AND v.rule_code='screener_manifest_coverage' AND v.status='failed')
        AND NOT EXISTS (SELECT 1 FROM validation_results v WHERE v.parse_id=p.parse_id
          AND v.severity='error' AND v.status='failed'
          AND v.rule_code!='screener_manifest_coverage')
        AND EXISTS (SELECT 1 FROM accepted_page_versions a
          WHERE a.company_id=f.company_id AND a.source='screener'
            AND a.reporting_scope='standalone' AND a.resolved_source_period=?)
        AND NOT EXISTS (SELECT 1 FROM accepted_page_versions a WHERE a.parse_id=p.parse_id)
      GROUP BY f.company_id,p.parse_id
    ) SELECT company_id,parse_id FROM ranked r WHERE rn=1
      AND EXISTS (SELECT 1 FROM document_links l WHERE l.parse_id=r.parse_id
        AND l.document_type IN ('concall_ppt','concall_transcript','concall_recording')
        AND NOT EXISTS (SELECT 1 FROM document_link_promotions m
          WHERE m.document_link_id=l.document_link_id AND m.promoter_version=?))
      ORDER BY company_id LIMIT ?""",(*months,period[:7],promoter_version,limit)).fetchall()
    return [(r["company_id"],r["parse_id"]) for r in rows]
