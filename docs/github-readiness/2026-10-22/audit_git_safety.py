"""Redacted heuristic scan of reachable Git blobs, index and local text.
No credential values/snippets are written. Archives are not unpacked.
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

OUT=Path(__file__).resolve().parent
def git(*a):return subprocess.check_output(['git',*a])
patterns={
 'private_key':rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
 'github_token':rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})',
 'aws_access_id':rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
 'provider_key':rb'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{30,}',
 'credential_assignment':rb'''(?i)\b(?:api_key|access_token|auth_token|sessionid|csrftoken|password)\s*[=:]\s*["']([A-Za-z0-9_+/.=-]{20,})["']''',
 'basic_auth_url':rb'https?://[^\s/:]{2,}:[^\s/@]{4,}@',
}
compiled={k:re.compile(v) for k,v in patterns.items()}
objects=[]
for line in git('rev-list','--objects','--all').decode(errors='replace').splitlines():
    oid,_,p=line.partition(' ');objects.append((oid,p))
p=subprocess.Popen(['git','cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
findings=[];large=[];total=0;blobs=0;binary=0
for oid,path in objects:
    p.stdin.write((oid+'\n').encode());p.stdin.flush()
    head=p.stdout.readline().split()
    if len(head)!=3:continue
    size=int(head[2]);typ=head[1];remaining=size;tail=b'';hits=set();is_binary=False
    while remaining:
        data=p.stdout.read(min(1024*1024,remaining));remaining-=len(data)
        if typ==b'blob':
            is_binary |= b'\0' in data
            buf=tail+data
            hits.update(k for k,rx in compiled.items() if rx.search(buf))
            tail=buf[-512:]
    p.stdout.read(1)
    if typ!=b'blob':continue
    blobs+=1;total+=size;binary+=is_binary
    if size>=50*1024*1024:large.append(dict(oid=oid,path=path,bytes=size,over_100_MiB=size>100*1024*1024))
    if hits:findings.append(dict(oid=oid,path=path,patterns=sorted(hits),binary=is_binary))
p.stdin.close();p.wait()
index=git('ls-files','-s','-z').split(b'\0');indexed=[]
for line in index:
    if not line:continue
    meta,path=line.split(b'\t',1);mode,oid,stage=meta.split()
    indexed.append({'path':path.decode(errors='replace'),'oid':oid.decode(),'stage':stage.decode()})
oid_findings={r['oid']:r for r in findings}
local=[];absolute=[]
for row in indexed:
    path=Path(row['path'])
    if not path.is_file() or path.is_symlink() or path.stat().st_size>8*1024*1024:continue
    if path.is_relative_to(OUT.relative_to(Path.cwd())):continue
    data=path.read_bytes()
    if b'\0' in data:continue
    hits=[k for k,rx in compiled.items() if rx.search(data)]
    if hits:local.append(dict(path=row['path'],patterns=hits))
    if re.search(rb'/(?:Users/[^/\s]+|Volumes/[^/\n]+|private/tmp)/',data):absolute.append(row['path'])
secret_commit='e71d1ee168d4a2b5c155aa6302347aeaad841e46'
known=git('show',secret_commit+':.screener_csrf')
reachable=subprocess.run(['git','merge-base','--is-ancestor',secret_commit,'HEAD']).returncode==0
out=dict(scope='all refs reachable Git objects; index tracked local text <=8 MiB; no archive unpacking or entropy scanner',reachable_objects=len(objects),reachable_blobs=blobs,blob_bytes_scanned=total,binary_blobs_scanned_raw=binary,heuristic_findings=findings,index_matches=[r for r in indexed if r['oid'] in oid_findings],local_indexed_text_findings=local,absolute_path_files=absolute,large_history_blobs=large,confirmed_historical_credential=dict(path='.screener_csrf',commit=secret_commit,nonempty_bytes=len(known.strip()),reachable_from_HEAD=reachable,value='REDACTED'),limitations=['heuristics can miss credentials and produce false positives','compressed archives/binary encodings not unpacked','remote exposure/visibility not refreshed; no network used','ignored local secrets not searched as source; no credential values emitted','untracked local text scanned separately by file manifest; not cleared for publication'])
(OUT/'git-safety.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:out[k] for k in ('reachable_objects','reachable_blobs','blob_bytes_scanned','confirmed_historical_credential')}))
print('Heuristic candidates:',len(findings),'large history blobs:',len(large))
