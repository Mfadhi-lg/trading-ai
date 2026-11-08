"""Immutable read-only trial preflight/final census. No source access or writers."""
import hashlib, json, runpy, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot
from production_ownership import assert_no_conflicting_leases
from scrapper.db.database import check_schema_compatibility
from scrapper.db.host_permits import reviewed_policy

db=Path('/data/scraper.db');before=assert_safe_empty_wal(db)
c=sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
c.execute('PRAGMA query_only=ON');check_schema_compatibility(c)
baseline=json.loads(Path('/activation/production-baseline.json').read_text())
def require(ok,reason):
    if not ok:raise RuntimeError(reason)
def q(sql,args=()):return [dict(r) for r in c.execute(sql,args)]
require(db.stat().st_ino==baseline['db_after']['inode'] and db.stat().st_size>=baseline['db_after']['bytes'],'DB identity drift')
for name,item in baseline['physical_markers'].items():require(hashlib.sha256(Path(name).read_bytes()).hexdigest()==item['sha256'],'Physical marker drift')
require(q('SELECT * FROM schema_migrations ORDER BY version')==baseline['ledger'],'Ledger drift')
require(q('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')==baseline['schema'],'Schema drift')
require(q('SELECT * FROM document_extraction_claims WHERE resource_key=?',(baseline['control_row']['resource_key'],))==[baseline['control_row']],'Control receipt drift')
now=datetime.now(timezone.utc).isoformat();assert_no_conflicting_leases(c,now=now)
holds=q('SELECT * FROM exchange_source_access_events ORDER BY event_id')
for dataset in ('bse_security_master','bse_financial_results'):
    events=[r for r in holds if r['dataset']==dataset]
    require(events and events[-1]['action']=='BLOCKED','BSE API hold absent: '+dataset)
policies={host:reviewed_policy(c,host) for host in ('www.screener.in','www.nseindia.com','www.sacheerome.com','www.crisil.com','crisilratings.com','www.icra.in')}
require(all(policies[h].blocked and policies[h].max_concurrency==0 for h in ('www.crisil.com','crisilratings.com','www.icra.in')),'CRA exclusion drift')
require(all(policies[h].min_delay_seconds>=4 and policies[h].max_concurrency<=1 for h in ('www.screener.in','www.nseindia.com','www.sacheerome.com')),'Host safety floor drift')
tables=('document_artifacts','artifact_sources','artifact_contents','artifact_download_queue','artifact_download_attempts',
    'artifact_discovery_pages','artifact_expectations','artifact_check_events','response_blobs','fetch_observations',
    'document_processing_inbox','document_processing_work','document_extractions','document_pages','document_page_elements',
    'credit_rating_download_queue','credit_rating_resolution_attempts','credit_rating_resolution_steps',
    'accepted_page_versions','financial_observations')
data={'status':'PASS','observed_at_utc':now,'source_requests':0,'production_writes':0,'holds':holds,
    'source_policies':{h:vars(p) for h,p in policies.items()},'counts':{t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in tables},
    'download_queue':q('SELECT status,count(*) n FROM artifact_download_queue GROUP BY status'),
    'host_state':q('SELECT * FROM acquisition_host_state'),
    'extractions':q('SELECT extraction_status,count(*) n FROM document_extractions GROUP BY extraction_status'),
    'db_bytes':db.stat().st_size,'guard':before}
if '--final' in sys.argv:
    epoch=json.loads(Path('/activation/phase4_schedule.json').read_text())['activated_at_utc']
    data['new_download_attempts']=q('SELECT * FROM artifact_download_attempts WHERE started_at_utc>=?',(epoch,))
    data['new_extractions']=q('SELECT * FROM document_extractions WHERE created_at_utc>=?',(epoch,))
    data['new_fetches']=q('SELECT * FROM fetch_observations WHERE fetched_at_utc>=?',(epoch,))
    data['new_rating_attempts']=q('SELECT * FROM credit_rating_resolution_attempts WHERE started_at_utc>=?',(epoch,))
    data['rating_steps']=q('SELECT s.* FROM credit_rating_resolution_steps s JOIN credit_rating_resolution_attempts a USING(resolution_attempt_id) WHERE a.started_at_utc>=?',(epoch,))
    data['permits']=q('SELECT * FROM acquisition_host_permits WHERE acquired_at>=?',(datetime.fromisoformat(epoch).timestamp(),))
    from verify_phase4_unattended import verify
    data['unattended_verification']=verify(c,receipts='/receipts',epoch=epoch)
c.close();require(snapshot(db)==before['files'],'DB/sidecars changed during snapshot')
if '--final' not in sys.argv:runpy.run_path('/activation/phase4_routine_preflight_runtime.py',run_name='__main__')
print(json.dumps({'stage':'trial_snapshot','snapshot':data}),flush=True)
