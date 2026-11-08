"""Read-only document census; no source requests or document writes."""
import json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path[:0] = ['/activation', '/app']
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot
from scrapper.db.database import check_schema_compatibility
p = Path('/data/scraper.db')
before = assert_safe_empty_wal(p)
c = sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1', uri=True)
c.row_factory = sqlite3.Row
check_schema_compatibility(c)
def q(sql): return [dict(r) for r in c.execute(sql)]
data = {'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'guard': before,
    'artifacts': q('SELECT artifact_type,acquisition_status,count(*) n FROM document_artifacts GROUP BY 1,2'),
    'expectations': q('SELECT artifact_type,availability_status,count(*) n FROM artifact_expectations GROUP BY 1,2'),
    'download_queue': q('SELECT a.artifact_type,q.status,count(*) n FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id) GROUP BY 1,2'),
    'contents': q('SELECT a.artifact_type,c.media_type,c.extraction_status,count(*) n FROM artifact_contents c JOIN document_artifacts a USING(artifact_id) GROUP BY 1,2,3'),
    'processing': q('SELECT a.artifact_type,coalesce(e.extraction_status,"pending") state,count(*) n FROM document_processing_work w JOIN artifact_contents c USING(content_id) JOIN document_artifacts a USING(artifact_id) LEFT JOIN document_extractions e USING(extraction_id) GROUP BY 1,2'),
    'inbox': q('SELECT count(*) n FROM document_processing_inbox'),
    'canary_candidates': q('''SELECT q.*,a.company_id,a.artifact_type,a.period_end,a.title_raw,a.first_observed_at_utc,s.classification_evidence_json,s.discovered_from_source FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id) JOIN artifact_sources s ON s.artifact_id=q.artifact_id AND s.document_url=q.requested_url WHERE q.status='PENDING' AND a.artifact_type IN ('presentation','transcript') AND NOT EXISTS (SELECT 1 FROM artifact_contents c WHERE c.artifact_id=a.artifact_id) AND EXISTS (SELECT 1 FROM screener_collection_scope sc WHERE sc.company_id=a.company_id AND sc.eligibility='eligible')  ORDER BY a.period_end DESC,a.first_observed_at_utc DESC LIMIT 8'''),
    'recent_links': q("SELECT parse_id,document_type,availability_status,source_url,period_label_raw FROM document_links WHERE parse_id>=34420 AND document_type IN ('concall_ppt','concall_transcript') ORDER BY document_link_id DESC LIMIT 12"),
    'saved_candidates': q('''SELECT c.*,a.artifact_type,a.period_end,b.local_path FROM artifact_contents c JOIN document_artifacts a USING(artifact_id) JOIN response_blobs b USING(blob_id) WHERE a.artifact_type IN ('presentation','transcript') ORDER BY c.created_at_utc DESC LIMIT 4''')}
c.close()
if snapshot(p) != before['files']: raise RuntimeError('Production changed during inspection')
print(json.dumps(data))
