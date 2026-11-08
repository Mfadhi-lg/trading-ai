"""Read-only exact-canary preflight exercising the repaired preservation helper."""
import hashlib,json,sqlite3,sys,time
from datetime import datetime,timezone
from pathlib import Path
sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal,snapshot
from production_ownership import assert_no_conflicting_leases
from phase4_documents_runtime import fingerprint,assert_preserved
from scrapper.db.database import check_schema_compatibility

p=Path('/data/scraper.db');before=assert_safe_empty_wal(p)
plan=json.loads(Path('/selected/plan.json').read_text())
manifest=json.loads(Path('/selected/manifest.json').read_text())
baseline=json.loads(Path('/activation/production-baseline.json').read_text())
def require(ok,message):
    if not ok:raise RuntimeError(message)
require(hashlib.sha256(Path('/selected/plan.json').read_bytes()).hexdigest()==manifest['plan_sha256'],'Original scope seal changed')
require(plan['artifact_id']==81417 and plan['download_job_id']==144466 and plan['max_downloads']==1 and plan['max_extractions']==1
        and plan['max_response_bytes']==10485760 and plan['max_redirects']==0 and plan['deadline_seconds']==300
        and plan['financial_writes']==0,'Exact authorized bounds changed')
require(p.stat().st_ino==baseline['db_after']['inode'] and p.stat().st_size>=baseline['db_after']['bytes'],'Database identity mismatch')
for name,expected in baseline['physical_markers'].items():
    require(hashlib.sha256(Path(name).read_bytes()).hexdigest()==expected['sha256'],'Physical DB marker changed')
c=sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
c.execute('PRAGMA query_only=ON')
check_schema_compatibility(c)
assert_no_conflicting_leases(c,now=datetime.now(timezone.utc).isoformat())
require([dict(r) for r in c.execute('SELECT * FROM schema_migrations ORDER BY version')]==baseline['ledger'],'Migration ledger changed')
require([dict(r) for r in c.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]==baseline['schema'],'Schema changed')
control=c.execute('SELECT * FROM document_extraction_claims WHERE resource_key=?',(baseline['control_row']['resource_key'],)).fetchone()
require(control is not None and dict(control)==baseline['control_row'],'Control receipt changed')
row=c.execute('SELECT * FROM artifact_download_queue WHERE download_job_id=?',(plan['download_job_id'],)).fetchone()
require(row is not None and row['status']=='PENDING' and row['attempt_count']==0,'Queue changed')
require(row['artifact_id']==plan['artifact_id'] and row['requested_url']==plan['url'] and row['scope_id']==plan['scope_id'],'Source identity changed')
artifact=c.execute('SELECT company_id,artifact_type FROM document_artifacts WHERE artifact_id=?',(plan['artifact_id'],)).fetchone()
require(artifact['company_id']==plan['company_id'] and artifact['artifact_type']==plan['artifact_type'],'Document/company identity changed')
require(c.execute("SELECT 1 FROM screener_collection_scope WHERE company_id=? AND eligibility='eligible'",(plan['company_id'],)).fetchone(),'Company ineligible')
require(not c.execute('SELECT 1 FROM artifact_contents WHERE artifact_id=? OR content_sha256=?',(plan['artifact_id'],plan['expected_sha256'])).fetchone(),'Content already saved')
protected=('accepted_page_versions','financial_observations','exchange_source_access_events')
keys={table:[r['name'] for r in sorted(c.execute('PRAGMA table_info("'+table+'")').fetchall(),key=lambda r:r['pk']) if r['pk']] for table in protected}
start=time.monotonic();fingerprints={table:fingerprint(c,table) for table in protected}
assert_preserved(c,fingerprints)
result={'status':'PASS','observed_at_utc':datetime.now(timezone.utc).isoformat(),'primary_keys':keys,
    'protected_fingerprints':fingerprints,'fingerprint_seconds':time.monotonic()-start,
    'repaired_runtime_sha256':hashlib.sha256(Path('/activation/phase4_documents_runtime.py').read_bytes()).hexdigest(),
    'queue':dict(row),'guard_before':before,'source_requests':0,'queue_claims':0,'production_writes':0,
    'worker_started':False,'canary_executed':False,'immutable_query_only':True}
c.close()
require(snapshot(p)==before['files'],'Database/sidecar drift during read-only preflight')
result['database_and_sidecars_unchanged']=True
print(json.dumps(result))
