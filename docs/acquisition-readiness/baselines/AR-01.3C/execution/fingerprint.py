"""Full copy fingerprints/FK identities; no production or control-key writes."""
import hashlib,json,pickle,sqlite3,time,sys
from pathlib import Path


def save(name,data):
    (Path('/evidence')/name).write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')


def emit(stage,**data):
    print(json.dumps({'at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'stage':stage,**data}),flush=True)


def q(name):return '"'+name.replace('"','""')+'"'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()


db=Path('/copy/scraper.db');expected=sys.argv[1]
if any(Path(str(db)+s).exists() for s in ('-wal','-shm','-journal')) or sha(db)!=expected:raise RuntimeError('Unsafe or different standalone copy')
conn=sqlite3.connect('file:/copy/scraper.db?mode=ro&immutable=1',uri=True);conn.row_factory=sqlite3.Row;conn.execute('PRAGMA query_only=ON')
schema=[dict(r) for r in conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')];save('schema.json',schema)
ledger=[dict(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')];save('ledger.json',ledger)
emit('foreign_key_check_start');fk=[list(r) for r in conn.execute('PRAGMA foreign_key_check')];fk.sort(key=lambda r:json.dumps(r,separators=(',',':')));save('foreign-keys.json',fk);emit('foreign_key_check_end',count=len(fk))
rows={}
for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    cols=[r[1] for r in conn.execute('PRAGMA table_info('+q(name)+')')];ddl=conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone()[0];order='rowid'
    if 'WITHOUT ROWID' in (ddl or '').upper():
        info=list(conn.execute('PRAGMA table_info('+q(name)+')'));order=','.join(q(r[1]) for r in sorted(info,key=lambda r:r[5]) if r[5])
    h,n=hashlib.sha256(),0
    for r in conn.execute('SELECT '+','.join(map(q,cols))+' FROM '+q(name)+' ORDER BY '+order):
        b=pickle.dumps(tuple(r),protocol=4);h.update(len(b).to_bytes(8,'big'));h.update(b);n+=1
    rows[name]={'columns':cols,'rows':n,'sha256':h.hexdigest()};save('rows.json',rows);emit('fingerprint',table=name,rows=n)
save('claims.json',[dict(r) for r in conn.execute('SELECT * FROM document_extraction_claims ORDER BY resource_key')]);save('work-references.json',[dict(r) for r in conn.execute('SELECT * FROM document_processing_work')]);conn.close()
if sha(db)!=expected:raise RuntimeError('Copy changed while fingerprinting')
save('final.json',{'status':'PASS','sha256':expected,'tables':len(rows),'rows':sum(r['rows'] for r in rows.values()),'fk_count':len(fk),'production_mounts':[]});emit('PASS',tables=len(rows))
