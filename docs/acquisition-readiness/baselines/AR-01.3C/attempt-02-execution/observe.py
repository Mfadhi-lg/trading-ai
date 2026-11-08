"""Independent RO inspection of the reserved control key; no ownership calls."""
import hashlib
import json
import os
import sqlite3
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0,'/app')
from scrapper.db.database import check_schema_compatibility
from scrapper import config

KEY='AR-01.3C/control-ownership/2026-10-08/v1'
db=Path('/data/scraper.db')


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()


def state():
    s=db.lstat()
    if not stat.S_ISREG(s.st_mode) or any(os.path.lexists(str(db)+x) for x in ('-wal','-shm','-journal')):
        raise RuntimeError('Unsafe main/sidecar state; no immutable read')
    result={k:getattr(s,v) for k,v in [('bytes','st_size'),('mtime_ns','st_mtime_ns'),('ctime_ns','st_ctime_ns'),('inode','st_ino'),('device','st_dev')]}
    result['sidecars']={x:False for x in ('-wal','-shm','-journal')}
    return result


gate=json.loads(Path('/proof/host-gate.json').read_text())
if not gate['writer_gate_passed'] or not 0<=time.time()-gate['observed_at_epoch']<=10:raise RuntimeError('Writer gate absent/stale')
mounts=[x.split(' - ',1)[0].split() for x in Path('/proc/self/mountinfo').read_text().splitlines()]
if not any(x[4]=='/data' and 'ro' in x[5].split(',') for x in mounts):raise RuntimeError('Production must be read-only')
before=state()
conn=sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True);conn.row_factory=sqlite3.Row
conn.execute('PRAGMA query_only=ON');check_schema_compatibility(conn)
schema=[dict(r) for r in conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
ledger=[dict(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]
if schema!=json.loads(Path('/proof/schema075.json').read_text()) or ledger!=json.loads(Path('/proof/ledger075.json').read_text()):raise RuntimeError('Schema/ledger drift')
row=conn.execute('SELECT * FROM document_extraction_claims WHERE resource_key=?',(KEY,)).fetchone()
refs=conn.execute('SELECT COUNT(*) FROM document_processing_work WHERE resource_key=?',(KEY,)).fetchone()[0]
other_claims=conn.execute('SELECT COUNT(*) FROM document_extraction_claims WHERE resource_key!=?',(KEY,)).fetchone()[0]
result={'status':'PASS','phase':sys.argv[1],'db_before':before,'schema':schema,'ledger':ledger,
        'control_row':dict(row) if row else None,'work_references':refs,'other_claim_keys':other_claims,'schema_compatibility':'PASS'}
if refs:raise RuntimeError('Unexpected processing-work reference')
if sys.argv[1]=='pre':
    baseline=json.loads(Path('/proof/baseline075.json').read_text())
    if row is not None or other_claims or before!=baseline['db_before']:raise RuntimeError('Control key/table or DB identity differs before writes')
    dirs=['STORE','FILES','SNAPSHOTS','EXPORTS','AMC_WORKBOOK_SNAPSHOTS','MF_WORKBOOK_EXPORTS','MF_FEATURE_EXPORTS',
          'MF_COVERAGE_EXPORTS','MFDATA_SNAPSHOTS','MFDATA_EXPORTS','DOCUMENT_DISCOVERY_SNAPSHOTS',
          'DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS','DOCUMENT_DOWNLOAD_EXPORTS','DOCUMENT_EXTRACTION_EXPORTS',
          'DOCUMENT_CREDIT_RATING_EXPORTS','DOCUMENT_TRANSCRIPT_EXPORTS','DOCUMENT_RECORDING_EXPORTS',
          'DOCUMENT_MACHINE_TRANSCRIPT_EXPORTS','DOCUMENT_QUALITY_EXPORTS','DOCUMENT_RECONCILIATION_EXPORTS',
          'DOCUMENT_ARTIFACT_COVERAGE_EXPORTS','EXCHANGE_SCHEDULE_EXPORTS','MF_IMPORT_INBOX','MF_IMPORT_ACCEPTED',
          'MF_IMPORT_REJECTED','MF_IMPORT_QUARANTINE']
    result['required_dirs']={str(getattr(config,n)):Path(getattr(config,n)).is_dir() for n in dirs}
    if not all(result['required_dirs'].values()):raise RuntimeError('Required path missing; no incidental production mkdir allowed')
markers={}
for p in (Path('/data/.trading_ai_runtime_identity.json'),Path('/data/snapshots/.trading_ai_runtime_identity.json')):
    if not p.is_file():raise RuntimeError('Sentinel bind target missing')
    markers[str(p)]={'sha256':sha(p),'bytes':p.stat().st_size}
result['physical_markers']=markers
conn.close()
result['db_sha256']=sha(db);result['db_after']=state()
if result['db_after']!=before:raise RuntimeError('Source changed during read')
if sys.argv[1]=='pre' and result['db_sha256']!='d773f4863e41266ca2cbbd76f1d542eb888878be1380e40114c34b4164fe4afc':raise RuntimeError('Fresh 075 source hash differs')
print(json.dumps(result,indent=2,sort_keys=True))
