"""Read-only verification of the failed exact canary; no worker or source access."""
import hashlib,json,sqlite3,sys
from pathlib import Path
from datetime import datetime,timezone
sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal,snapshot
p=Path('/data/scraper.db');before=assert_safe_empty_wal(p)
c=sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
q=lambda sql,args=():[dict(r) for r in c.execute(sql,args)]
result={'observed_at_utc':datetime.now(timezone.utc).isoformat(),'guard':before,
 'queue':q('SELECT * FROM artifact_download_queue WHERE download_job_id=144466'),
 'artifact':q('SELECT * FROM document_artifacts WHERE artifact_id=81417'),
 'contents':q('SELECT * FROM artifact_contents WHERE artifact_id=81417'),
 'expected_hash_contents':q('SELECT content_id,artifact_id FROM artifact_contents WHERE content_sha256=?',('f6101da1213c2893484b94861f85e66375726e12b78a313b97298c63586ca3a3',)),
 'attempts':q('SELECT d.* FROM artifact_download_queue_attempts l JOIN artifact_download_attempts d USING(download_attempt_id) WHERE l.download_job_id=144466'),
 'attempt_sources':q('SELECT s.* FROM artifact_download_queue_attempts l JOIN artifact_download_attempt_sources s USING(download_attempt_id) WHERE l.download_job_id=144466'),
 'sources':q('SELECT * FROM artifact_sources WHERE artifact_id=81417'),
 'host_permits':q("SELECT * FROM acquisition_host_permits WHERE host='www.sacheerome.com' AND acquired_at>=1791744977"),
 'host_health':q("SELECT * FROM artifact_download_host_health WHERE host='www.sacheerome.com'"),
 'processing_inbox':q('SELECT i.* FROM document_processing_inbox i JOIN artifact_contents c USING(content_id) WHERE c.artifact_id=81417'),
 'processing_work':q('SELECT w.* FROM document_processing_work w JOIN artifact_contents c USING(content_id) WHERE c.artifact_id=81417'),
 'extractions':q('SELECT e.extraction_id,e.extraction_status FROM document_extractions e JOIN artifact_contents c USING(content_id) WHERE c.artifact_id=81417'),
 'pages':q('SELECT count(*) n FROM document_pages p JOIN artifact_contents c USING(content_id) WHERE c.artifact_id=81417'),
 'protected_table_definition':q("SELECT name,sql FROM sqlite_master WHERE name IN ('accepted_page_versions','financial_observations')")}
from phase4_documents_runtime import fingerprint
result['protected_fingerprints']={t:fingerprint(c,t) for t in ('accepted_page_versions','financial_observations','exchange_source_access_events')}
c.close()
if snapshot(p)!=before['files']:raise RuntimeError('Production changed during verification')
print(json.dumps(result))
