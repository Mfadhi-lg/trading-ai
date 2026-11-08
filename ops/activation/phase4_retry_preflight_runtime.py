"""Focused immutable read-only admission preflight; no downloader or claims."""
import hashlib,json,sqlite3,sys
from pathlib import Path
from datetime import datetime,timezone
sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal,snapshot
from production_ownership import assert_no_conflicting_leases
from phase4_documents_runtime import assert_retry_admissible
from scrapper.db.database import check_schema_compatibility
from scrapper.documents.download.tls import verified_chain_context
p=Path('/data/scraper.db');before=assert_safe_empty_wal(p)
plan=json.loads(Path('/selected/plan.json').read_text());manifest=json.loads(Path('/selected/manifest.json').read_text())
def require(ok,message):
 if not ok:raise RuntimeError(message)
require(hashlib.sha256(Path('/selected/plan.json').read_bytes()).hexdigest()==manifest['plan_sha256'],'Plan seal changed')
require(hashlib.sha256(Path('/activation/phase4_documents_runtime.py').read_bytes()).hexdigest()==manifest['runtime_sha256'],'Runtime seal changed')
for name,digest in manifest['application_hashes'].items():require(hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()==digest,'Application/CA seal changed: '+name)
require((plan['artifact_id'],plan['download_job_id'],plan['max_downloads'],plan['max_extractions'],plan['max_response_bytes'],plan['max_redirects'],plan['deadline_seconds'],plan['financial_writes'])==(81417,144466,1,1,10485760,0,300,0),'Scope changed')
baseline=json.loads(Path('/activation/production-baseline.json').read_text())
require(p.stat().st_ino==baseline['db_after']['inode'] and p.stat().st_size>=baseline['db_after']['bytes'],'DB identity changed')
for name,expected in baseline['physical_markers'].items():require(hashlib.sha256(Path(name).read_bytes()).hexdigest()==expected['sha256'],'Physical marker changed')
c=sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
check_schema_compatibility(c)
require([dict(r) for r in c.execute('SELECT * FROM schema_migrations ORDER BY version')]==baseline['ledger'],'Migration ledger drift')
require([dict(r) for r in c.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]==baseline['schema'],'Schema drift')
now=datetime.now(timezone.utc).isoformat();assert_no_conflicting_leases(c,now=now)
queue=assert_retry_admissible(c,plan,now)
require(c.execute("SELECT 1 FROM screener_collection_scope WHERE company_id=? AND eligibility='eligible'",(plan['company_id'],)).fetchone(),'Company ineligible')
require(not c.execute('SELECT 1 FROM artifact_contents WHERE artifact_id=? OR content_sha256=?',(plan['artifact_id'],plan['expected_sha256'])).fetchone(),'Content already saved')
previous=[dict(r) for r in c.execute('SELECT * FROM artifact_download_attempts WHERE download_attempt_id=84702')]
health=[dict(r) for r in c.execute('SELECT * FROM artifact_download_host_health WHERE host=?',(queue['target_host'],))]
state=[dict(r) for r in c.execute('SELECT * FROM acquisition_host_state WHERE publisher_group=?',('host:'+queue['target_host'],))]
ctx=verified_chain_context()
result={'status':'PASS','observed_at_utc':now,'retry_eligible':True,'queue':dict(queue),'prior_attempt':previous,'host_health':health,'shared_host_state':state,
 'certificate_verification_required':int(ctx.verify_mode),'hostname_verification':ctx.check_hostname,'partial_chain_trust':bool(ctx.verify_flags&__import__('ssl').VERIFY_X509_PARTIAL_CHAIN),
 'queue_claims':0,'source_requests':0,'production_writes':0,'canary_executed':False,'guard_before':before}
c.close();require(snapshot(p)==before['files'],'DB/sidecar changed during preflight');result['database_and_sidecars_unchanged']=True
print(json.dumps(result))
