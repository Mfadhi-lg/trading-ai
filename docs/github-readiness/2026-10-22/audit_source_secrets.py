"""Supplement reachable-history scan with exact indexed and untracked source text.
Only path, rule and line are recorded; no matched bytes or values are emitted.
"""
import csv
import json
import re
import subprocess
from pathlib import Path
OUT=Path(__file__).resolve().parent
RX={
 'private_key':re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
 'github_token':re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
 'aws_access_id':re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
 'provider_key':re.compile(rb'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{30,}'),
 'credential_assignment':re.compile(rb'''(?i)\b(?:api_key|access_token|auth_token|sessionid|csrftoken|password)\s*[=:]\s*["']([A-Za-z0-9_+/.=-]{20,})["']'''),
 'basic_auth_url':re.compile(rb'https?://[^\s/:]{2,}:[^\s/@]{4,}@'),
}
suffixes={'.py','.md','.json','.jsonl','.csv','.yml','.yaml','.toml','.sh','.js','.ts','.tsx','.sql','.txt','.html','.xml','.plist','.pem','.key','.patch'}
hits=[];scanned=0;skipped=[]
def scan(path,data,scope):
    global scanned
    if b'\0' in data:return
    scanned+=1
    for rule,rx in RX.items():
        for m in rx.finditer(data):hits.append(dict(path=path,scope=scope,rule=rule,line=data[:m.start()].count(b'\n')+1))
index_items=[]
for item in subprocess.check_output(['git','ls-files','-s','-z']).split(b'\0'):
    if not item:continue
    meta,path=item.split(b'\t',1);mode,oid,stage=meta.split();name=path.decode()
    if Path(name).suffix.lower() not in suffixes:continue
    index_items.append((name,oid))
metadata=subprocess.run(['git','cat-file','--batch-check=%(objectsize)'],input=b'\n'.join(oid for _,oid in index_items)+b'\n',capture_output=True,check=True).stdout.splitlines()
batch=subprocess.Popen(['git','cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
for (name,oid),size_raw in zip(index_items,metadata):
    size=int(size_raw)
    if size>8*1024*1024:skipped.append(dict(path=name,bytes=size,scope='index'));continue
    batch.stdin.write(oid+b'\n');batch.stdin.flush()
    header=batch.stdout.readline().split()
    data=batch.stdout.read(int(header[2]));batch.stdout.read(1)
    scan(name,data,'exact_index_blob')
batch.stdin.close();batch.wait()
for r in csv.DictReader((OUT/'file-classification.csv').open()):
    name=r['path'];path=Path(name)
    if not name.startswith(('scrapper/','ops/','scripts/','control-center/','experiments/','.github/')):continue
    if name.startswith('scrapper/store/') or path.suffix.lower() not in suffixes:continue
    if int(r['bytes'])>8*1024*1024:skipped.append(dict(path=name,bytes=int(r['bytes']),scope='local_source'));continue
    if path.is_file():scan(name,path.read_bytes(),'local_source_including_untracked')
(OUT/'source-secret-scan.json').write_text(json.dumps(dict(texts_scanned=scanned,findings=hits,skipped=skipped,scope='exact index text + local source roots including untracked; <=8MiB per file; heuristic only; no archive extraction'),indent=2)+'\n')
print('text scans',scanned,'findings',len(hits),'skipped',len(skipped))
