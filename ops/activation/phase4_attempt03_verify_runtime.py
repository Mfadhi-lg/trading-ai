"""Immutable selected-lifecycle verification after the authorized one-shot."""
import json,sqlite3,sys
from pathlib import Path
from datetime import datetime,timezone
sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal,snapshot
p=Path('/data/scraper.db');before=assert_safe_empty_wal(p)
c=sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
q=lambda sql,args=():[dict(r) for r in c.execute(sql,args)]
result={'observed_at_utc':datetime.now(timezone.utc).isoformat(),'guard_before':before,
 'queue':q('SELECT * FROM artifact_download_queue WHERE download_job_id=144466'),
 'artifact':q('SELECT * FROM document_artifacts WHERE artifact_id=81417'),
 'attempts':q('SELECT a.* FROM artifact_download_queue_attempts q JOIN artifact_download_attempts a USING(download_attempt_id) WHERE q.download_job_id=144466 ORDER BY download_attempt_id'),
 'attempt_sources':q('SELECT s.* FROM artifact_download_queue_attempts q JOIN artifact_download_attempt_sources s USING(download_attempt_id) WHERE q.download_job_id=144466 ORDER BY download_attempt_id'),
 'sources':q('SELECT * FROM artifact_sources WHERE artifact_id=81417'),
 'content':q('SELECT * FROM artifact_contents WHERE content_id=78206'),
 'blob':q('SELECT * FROM response_blobs WHERE blob_id=144864'),
 'inbox':q('SELECT * FROM document_processing_inbox WHERE content_id=78206'),
 'work':q('SELECT * FROM document_processing_work WHERE content_id=78206'),
 'extraction':q('SELECT * FROM document_extractions WHERE extraction_id=33641'),
 'pages':q('SELECT page_or_slide_number,native_text_usable,selected_text_origin,length(selected_text) text_characters FROM document_pages WHERE extraction_id=33641 ORDER BY page_or_slide_number'),
 'elements':q('SELECT element_kind,count(*) n FROM document_page_elements WHERE extraction_id=33641 GROUP BY element_kind'),
 'validations':q('SELECT * FROM document_extraction_validations WHERE extraction_id=33641'),
 'extraction_claim':q('SELECT f.* FROM document_extraction_claims f JOIN document_processing_work w USING(resource_key) WHERE w.content_id=78206'),
 'host_permits':q("SELECT * FROM acquisition_host_permits WHERE host='www.sacheerome.com' AND acquired_at>=1791780431"),
 'host_health':q("SELECT * FROM artifact_download_host_health WHERE host='www.sacheerome.com'"),
 'host_state':q("SELECT * FROM acquisition_host_state WHERE publisher_group='host:www.sacheerome.com'"),
 'processing_backlog':q("SELECT a.artifact_type,c.extraction_status,count(*) n FROM artifact_contents c JOIN document_artifacts a USING(artifact_id) WHERE a.artifact_type IN ('presentation','transcript') GROUP BY 1,2"),
 'inbox_work_counts':q('SELECT (SELECT count(*) FROM document_processing_inbox) inbox,(SELECT count(*) FROM document_processing_work) work')}
c.close()
if snapshot(p)!=before['files']:raise RuntimeError('DB/sidecar changed during verification')
result['database_and_sidecars_unchanged_during_verification']=True
print(json.dumps(result))
