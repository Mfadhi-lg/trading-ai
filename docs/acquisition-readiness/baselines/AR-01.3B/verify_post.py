"""Full read-only verification of a byte-identical disposable production copy."""
import hashlib
import json
import pickle
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path


def emit(stage, **data):
    print(json.dumps({'at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'stage':stage,**data}),flush=True)


def save(name, data):
    (Path('/evidence')/name).write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()


def q(s):
    return '"'+s.replace('"','""')+'"'


db=Path('/copy/scraper.db')
expected=sys.argv[1]
if any(Path(str(db)+s).exists() for s in ('-wal','-shm','-journal')) or sha(db)!=expected:
    raise RuntimeError('Disposable input is not the exact standalone captured DB')
conn=sqlite3.connect('file:/copy/scraper.db?mode=ro&immutable=1',uri=True)
conn.row_factory=sqlite3.Row
conn.execute('PRAGMA query_only=ON')
checks={}
if '--resume-after-sqlite-checks' in sys.argv:
    checks=json.loads(Path('/proof/post-verification/sqlite-checks.json').read_text())
    prior_command=json.loads(Path('/proof/post-verification-command.json').read_text())
    if checks!={'quick_check':['ok'],'integrity_check':['ok']} or prior_command[-1]!=expected:
        raise RuntimeError('Prior completed checks do not cover this exact copy hash')
    save('sqlite-checks.json',checks)
    emit('reuse_completed_sqlite_checks',db_sha256=expected,reason='same fully rehashed immutable input and pinned image after external Docker recovery')
else:
    for name in ('quick_check','integrity_check'):
        emit(name+'_start')
        checks[name]=[r[0] for r in conn.execute('PRAGMA '+name)]
        save('sqlite-checks.json',checks)
        emit(name+'_end',result=checks[name])
        if checks[name]!=['ok']:raise RuntimeError(name+' failed; stop, do not repair')
schema=[dict(r) for r in conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
before_schema=json.loads(Path('/proof/schema-before.json').read_text())
after_schema=json.loads(Path('/proof/schema-after.json').read_text())
save('schema.json',schema)
if schema!=after_schema:raise RuntimeError('Unexpected full schema after migrations')
old_map={(r['type'],r['name']):r for r in before_schema}
new_map={(r['type'],r['name']):r for r in schema}
save('schema-diff.json',{'added':[v for k,v in new_map.items() if k not in old_map],
    'removed':[v for k,v in old_map.items() if k not in new_map],
    'modified':[{'before':old_map[k],'after':new_map[k]} for k in old_map.keys()&new_map.keys() if old_map[k]!=new_map[k]]})
ledger=[dict(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]
old_ledger=json.loads(Path('/proof/ledger-before.json').read_text())
old_versions=[r['version'] for r in old_ledger]
expected_new=[r['filename'] for r in json.loads(Path('/proof/migration-manifest.json').read_text())['pending']]
if len(ledger)!=76 or [r for r in ledger if r['version'] in old_versions]!=old_ledger or sorted(set(r['version'] for r in ledger)-set(old_versions))!=expected_new:
    raise RuntimeError('Ledger change exceeds exactly seven reviewed identities')
save('ledger.json',ledger)
emit('foreign_key_check_start')
fk=[list(r) for r in conn.execute('PRAGMA foreign_key_check')]
fk.sort(key=lambda r:json.dumps(r,separators=(',',':')))
old_fk=json.loads(Path('/proof/foreign-keys-before.json').read_text())
new_count=sum((Counter(map(tuple,fk))-Counter(map(tuple,old_fk))).values())
missing_count=sum((Counter(map(tuple,old_fk))-Counter(map(tuple,fk))).values())
save('foreign-keys.json',fk)
save('fk-comparison.json',{'before':len(old_fk),'after':len(fk),'new_identities':new_count,'missing_identities':missing_count})
emit('foreign_key_check_end',before=len(old_fk),after=len(fk),new=new_count,missing=missing_count)
if new_count or missing_count or len(fk)!=10771:raise RuntimeError('Foreign-key identity drift; do not repair')
baseline=json.loads(Path('/proof/rows-before.json').read_text())
projected={}
for table,entry in baseline.items():
    cols=entry['columns'];ddl=conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(table,)).fetchone()[0]
    order='rowid'
    if 'WITHOUT ROWID' in (ddl or '').upper():
        info=list(conn.execute('PRAGMA table_info('+q(table)+')'))
        order=','.join(q(c[1]) for c in sorted(info,key=lambda c:c[5]) if c[5])
    query='SELECT '+','.join(map(q,cols))+' FROM '+q(table)
    params=[]
    if table=='schema_migrations':
        query+=' WHERE version IN ('+','.join('?' for _ in old_versions)+')';params=old_versions
    query+=' ORDER BY '+order
    h,n=hashlib.sha256(),0
    for r in conn.execute(query,params):
        b=pickle.dumps(tuple(r),protocol=4);h.update(len(b).to_bytes(8,'big'));h.update(b);n+=1
    projected[table]={'columns':cols,'rows':n,'sha256':h.hexdigest()}
    save('original-row-fingerprints.json',projected)
    emit('fingerprint',table=table,rows=n)
    if projected[table]!=entry:raise RuntimeError('Original data fingerprint changed: '+table)
new_tables=sorted(set(r['name'] for r in schema if r['type']=='table')-set(baseline))
counts={t:conn.execute('SELECT COUNT(*) FROM '+q(t)).fetchone()[0] for t in new_tables}
tokens={t:conn.execute('SELECT COUNT(*) FROM '+q(t)+' WHERE claim_token IS NOT NULL').fetchone()[0]
    for t in ('artifact_download_queue','credit_rating_download_queue','browser_rating_recovery_jobs')}
if len(new_tables)!=8 or any(counts.values()) or any(tokens.values()):raise RuntimeError('Unexpected new work or non-NULL historical claims')
conn.close()
if sha(db)!=expected:raise RuntimeError('Validation copy changed')
result={'status':'PASS','quick_check':checks['quick_check'],'integrity_check':checks['integrity_check'],
    'original_tables':len(projected),'original_rows':sum(r['rows'] for r in projected.values()),
    'original_data_preserved':True,'original_ledger_rows':69,'new_ledger_rows':7,'total_ledger_rows':76,
    'new_table_counts':counts,'nonnull_claim_tokens':tokens,'fk_before':len(old_fk),'fk_after':len(fk),
    'new_fk_identities':0,'missing_fk_identities':0,'db_sha256':expected,'production_mounts':[]}
save('verification.json',result);emit('PASS',**result)
