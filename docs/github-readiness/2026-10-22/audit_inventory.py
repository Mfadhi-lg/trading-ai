"""Local metadata audit. Never connects to the production DB or emits records/secrets.

Run from repository root: python3 -B docs/github-readiness/2026-10-22/audit_inventory.py
Outputs only within this package. SQLite immutable reads are restricted to the
retained AR snapshots without nonempty WAL; the host operational DB is not opened.
"""
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent
def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)
def save(name, data):
    (OUT/name).write_text(json.dumps(data, indent=2, sort_keys=True)+'\n')
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024), b''): h.update(b)
    return h.hexdigest()
def classify(p):
    x=p.lower()
    if any(s in x for s in ('.screener_session','.screener_csrf','.env','cookies','storage_state','private_key')):
        return 'RETAIN_OUTSIDE_GIT','sensitive configuration; review path false positives'
    if p.startswith('OLD/'):
        return 'SUPERSEDED_BUT_REQUIRED','governance builder still reads historical Focus input paths'
    if any(s in x for s in ('/store/','/snapshots/')) or x.endswith(('.db','.db-wal','.db-shm','.sqlite','.sqlite3')):
        return 'RETAIN_OUTSIDE_GIT','data or retained database; verify restore role'
    if p.startswith(('sample_files','artifacts/','docs/cleanup/baselines/','docs/acquisition-readiness/baselines/')):
        return 'RETAIN_OUTSIDE_GIT','generated/private evidence; curated redacted text may be selected separately'
    if any(s in x for s in ('__pycache__','node_modules/','.venv','/.pytest_cache')) or x.endswith(('.pyc','.log','.pid','.zip','.tar','.tar.gz','.pdf','.oci.tar','.whl','.png','.jpg','.pptx')):
        return 'RETAIN_OUTSIDE_GIT','runtime/generated/binary material; fixtures need individual rights review'
    if p.startswith(('experiments/','scrapper/research/document_intelligence/','scrapper/research/general_ai_experiment/','docs/phase5/')):
        return 'REVIEW_REQUIRED','experimental work; preserve unchanged, separate acceptance and source review'
    if 'backup' in x or x.endswith(('.bak','.orig','.tmp','.old')):
        return 'ARCHIVE_CANDIDATE','apparent backup/temp; no redundancy or deletion established'
    return 'KEEP','source/control candidate; inclusion requires secret and licensing review'

indexed = {x.decode(errors='replace') for x in git('ls-files','-z').split(b'\0') if x}
status=git('status','--porcelain=v1','-z')
(OUT/'git-status-before.txt').write_bytes(status.replace(b'\0',b'\n'))
save('repository-identity.json',dict(captured_at=dt.datetime.now(dt.timezone.utc).isoformat(),branch=git('branch','--show-current').decode().strip(),head=git('rev-parse','HEAD').decode().strip(),remote=git('remote','-v').decode().splitlines(),indexed_files=len(indexed),status_entries=len([x for x in status.split(b'\0') if x])))
paths=[]; dbs=[]; totals={}; largest=[]
for base, dirs, files in os.walk(ROOT, followlinks=False):
    dirs[:]=[d for d in dirs if d not in {'.git','node_modules','__pycache__','.pytest_cache','.venv','.venv-control-center','venv'}]
    for name in files:
        path=Path(base)/name
        p=path.relative_to(ROOT).as_posix()
        if path.is_symlink() or path.is_relative_to(OUT): continue
        st=path.stat(); c,r=classify(p)
        paths.append((p,st.st_size,p in indexed,c,r))
        top=p.split('/')[0]; a=totals.setdefault(top,{'files':0,'bytes':0});a['files']+=1;a['bytes']+=st.st_size
        if st.st_size>=10*1024*1024:largest.append(dict(path=p,bytes=st.st_size,indexed=p in indexed))
        if name.startswith('.env') or name in {'.screener_session','.screener_csrf'}:
            continue
        with path.open('rb') as f: header=f.read(16)
        if header==b'SQLite format 3\x00':dbs.append(path)
with (OUT/'file-classification.csv').open('w') as f:
    w=csv.writer(f);w.writerow(['path','bytes','indexed','classification','reason']);w.writerows(sorted(paths))
save('directory-summary.json',totals)
save('large-files.json',sorted(largest,key=lambda r:-r['bytes']))
print('Metadata inventory complete',len(paths),'files;',len(dbs),'SQLite databases',flush=True)
results=[]
for path in dbs:
    p=path.relative_to(ROOT).as_posix();before=path.stat()
    r=dict(path=p,bytes=before.st_size,mtime_utc=dt.datetime.fromtimestamp(before.st_mtime,dt.timezone.utc).isoformat(),classification='RETAIN_OUTSIDE_GIT',production_consumer='none established; production uses Docker named volume',backup_role='historical AR snapshot; do not delete; not current production backup',sidecars={s:Path(str(path)+s).stat().st_size for s in ('-wal','-shm','-journal') if Path(str(path)+s).exists()})
    if not p.startswith('artifacts/acquisition-readiness/AR-01.3'):
        r.update(backup_role='host historical store, separate from production',schema_status='NOT_OPENED: operational sidecars/ownership not proven cold',sha256_status='NOT_HASHED: no stable cold-state guarantee',records='UNVERIFIED',latest_relevant_write='UNVERIFIED; mtime is not a logical write timestamp')
        if p.startswith('artifacts/phase5/'):
            r.update(production_consumer='none established; isolated Phase5 tool cache',backup_role='experiment tool cache; preserve ongoing experiments untouched',schema_status='NOT_OPENED: ongoing experiment cache',sha256_status='NOT_HASHED: ongoing experiment cache')
    else:
        r['sha256']=sha(path)
        if r['sidecars'].get('-wal',0) or r['sidecars'].get('-journal',0):
            r['schema_status']='NOT_OPENED: WAL/journal present'
        else:
            conn=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
            conn.execute('PRAGMA query_only=ON');conn.execute('PRAGMA temp_store=MEMORY')
            schema=conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
            r['schema_sha256']=hashlib.sha256(json.dumps(schema).encode()).hexdigest()
            r['schema']=schema
            r['tables']={}
            for typ,t,_,_ in schema:
                if typ!='table':continue
                q='"'+t.replace('"','""')+'"'
                cols=[v[1] for v in conn.execute('PRAGMA table_info('+q+')')]
                data={'records':conn.execute('SELECT COUNT(*) FROM '+q).fetchone()[0]}
                # Each table's strongest relevant recorded timestamp, not every payload.
                timecols=[c for c in ('accepted_at_utc','fetched_at_utc','created_at_utc','updated_at_utc','first_observed_at_utc','applied_at') if c in cols]
                if timecols:
                    c=timecols[0];data['latest_recorded_timestamp']={c:conn.execute('SELECT MAX("'+c+'") FROM '+q).fetchone()[0]}
                r['tables'][t]=data
            for t in ('financial_observations','accepted_page_versions'):
                if t not in r['tables']:continue
                q='"'+t+'"';cols=[v[1] for v in conn.execute('PRAGMA table_info('+q+')')]
                group=[c for c in ('reporting_scope','section_code','period_type','unit_code','missing_kind') if c in cols]
                if group:
                    g=','.join(group);r[t+'_coverage']=conn.execute('SELECT '+g+',COUNT(*) FROM '+q+' GROUP BY '+g).fetchall()
                period='period_end' if 'period_end' in cols else 'resolved_source_period'
                r[t+'_period_range']=conn.execute('SELECT MIN('+period+'),MAX('+period+'),COUNT(DISTINCT '+period+') FROM '+q).fetchone()
            conn.close();r['schema_status']='immutable SELECT-only historical snapshot; no integrity/restore certification'
    after=path.stat();r['file_metadata_stable']=before.st_size==after.st_size and before.st_mtime_ns==after.st_mtime_ns
    if not r['file_metadata_stable']:r['hash_validity']='UNVERIFIED: changed during read'
    results.append(r);save('databases.json',results)
    print('DB inventory complete:',p,flush=True)
save('inventory-complete.json',{'complete':True,'database_count':len(results)})
