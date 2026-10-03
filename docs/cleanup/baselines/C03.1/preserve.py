#!/usr/bin/env python3
"""C03.1 additive offline archives; never imports application/database code."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import zipfile
from collections import Counter

BASE='e71d1ee168d4a2b5c155aa6302347aeaad841e46'
GROUPS={
 'A2':('a2-review','artifacts/a2-2026-09-29'),
 'B1':('b1-review','artifacts/b1-2026-10-01'),
 'B2-draft':('b2-review','artifacts/b2-2026-10-01'),
 'B2-discovery':(None,'artifacts/b2-discovery-2026-10-01'),
}
SKIP_DIRS={'.git','.venv','venv','env','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','node_modules'}
SKIP_FILES={'.DS_Store','.env','.screener_session','.screener_csrf'}
def digest(data): return hashlib.sha256(data).hexdigest()
def hashfile(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()
def write(p,value): p.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def git(repo,*args,check=True):
    return subprocess.run(['git','-C',str(repo),*args],capture_output=True,check=check)
def files(repo,directory,excluded):
    result=[]
    for current,dirs,names in os.walk(repo/directory):
        for name in list(dirs):
            if name in SKIP_DIRS:
                excluded.append({'path':str((Path(current)/name).relative_to(repo)),'reason':'environment/cache/metadata directory; not archived'})
                dirs.remove(name)
        for name in sorted(names):
            p=Path(current)/name
            if name in SKIP_FILES or name.endswith(('.pyc','.pyo')):
                excluded.append({'path':str(p.relative_to(repo)),'reason':'secret/cache/OS metadata; not archived'})
                continue
            if not p.is_file() or p.is_symlink(): raise ValueError('Unsupported input: '+str(p))
            result.append(p)
    return sorted(result)
def add(tar,p,name):
    info=tar.gettarinfo(str(p),arcname=name)
    info.uid=info.gid=0; info.uname=info.gname=''; info.mtime=0
    with p.open('rb') as f: tar.addfile(info,f)
def patch(repo,work,source,target,filename):
    r=subprocess.run(['git','diff','--no-index','--binary','--no-renames','--',source,target],cwd=work,capture_output=True)
    if r.returncode not in (0,1): raise RuntimeError(r.stderr.decode())
    data=r.stdout.replace(('a/'+source+'/').encode(),b'a/').replace(('b/'+target+'/').encode(),b'b/')
    # For additions Git also names the source side after target; vice versa for deletions.
    data=data.replace(('a/'+target+'/').encode(),b'a/').replace(('b/'+source+'/').encode(),b'b/')
    (work/filename).write_bytes(data)
    dest=work/('verify-'+filename.replace('.patch',''))
    shutil.copytree(work/source,dest)
    if data:
        subprocess.run(['git','apply','--check',str(work/filename)],cwd=dest,check=True,capture_output=True)
        subprocess.run(['git','apply',str(work/filename)],cwd=dest,check=True,capture_output=True)
    expected={str(p.relative_to(work/target)):(hashfile(p),stat.S_IMODE(p.stat().st_mode)) for p in (work/target).rglob('*') if p.is_file()}
    actual={str(p.relative_to(dest)):(hashfile(p),stat.S_IMODE(p.stat().st_mode)) for p in dest.rglob('*') if p.is_file()}
    assert actual==expected,filename+' does not exactly reconstruct review files'
    return {'status':'PASS','sha256':hashfile(work/filename),'files_verified':len(expected),'applied_only_to_private_verification_copy':True}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,required=True); ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args(); repo=args.repo.resolve(); out=args.output.resolve()
    if out.is_relative_to(repo) or repo.is_relative_to(out): raise ValueError('Output must be outside source repository')
    out.mkdir(parents=True,exist_ok=False)
    head=git(repo,'rev-parse','HEAD').stdout.decode().strip()
    manifest={'format':'c031-v1','head':head,'base':BASE,'database_access':'none','groups':{},'excluded':[],
      'base_provenance':{'B1':'explicit Branch/base in B1_RECONCILIATION_AND_TESTS.md','A2':'reconstructed candidate: last main commit at A2 handoff; builder documented HEAD rather than commit; original patch apply/result verification below','B2-draft':'no documented commit; comparison anchor is documented B1 base; sparse overlay, not a complete checkout'},
      'C01_retention':'C01.1 record exists; original /private/tmp/trading-ai-cleanup-c01-20261002 capture directory absent; later C02.2 source snapshot checked for all copied-tree files'}
    verification={'groups':{},'archives':{},'production_impact':'none','source_files_not_mutated':True}
    cache={}
    def basefile(name):
        if name not in cache:
            r=git(repo,'show',BASE+':'+name,check=False)
            cache[name]=r.stdout if r.returncode==0 else None
        return cache[name]
    earlier=Path('/private/tmp/trading-ai-cleanup-c022-20261002/evidence/original-source-baseline.json')
    later=json.loads(earlier.read_text()) if earlier.exists() else {}
    for group,(tree,artifact) in GROUPS.items():
        work=out/(group+'-work'); work.mkdir()
        payload=[]; records=[]; copied=[]
        if tree:
            for folder in ('original','canonical','review'): (work/folder).mkdir()
            for p in files(repo,tree,manifest['excluded']):
                name=str(p.relative_to(repo/tree)); old=basefile(name); cur=repo/name
                raw=p.read_bytes(); mode=stat.S_IMODE(p.stat().st_mode)
                new=work/'review'/name; new.parent.mkdir(parents=True,exist_ok=True); new.write_bytes(raw); new.chmod(mode)
                for folder,body,permissions in [('original',old,0o755 if git(repo,'ls-tree',BASE,'--',name).stdout.startswith(b'100755') else 0o644),('canonical',cur.read_bytes() if cur.is_file() else None,stat.S_IMODE(cur.stat().st_mode) if cur.is_file() else 0o644)]:
                    if body is not None:
                        target=work/folder/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(body); target.chmod(permissions)
                original_equal=old is not None and old==raw
                canonical_equal=cur.is_file() and cur.read_bytes()==raw and stat.S_IMODE(cur.stat().st_mode)==mode
                classification='COPIED_UNCHANGED_FROM_BASE' if original_equal else 'ALREADY_PRESENT_IN_CANONICAL' if canonical_equal else 'REVIEW_ONLY_UNIQUE'
                disposition='SUPERSEDED_PROVISIONAL_AUDIT_ONLY' if group=='B2-draft' and not original_equal else classification
                record={'path':str(p.relative_to(repo)),'target':name,'sha256':digest(raw),'bytes':len(raw),'mode':mode,'base_sha256':digest(old) if old is not None else None,'canonical_sha256':hashfile(cur) if cur.is_file() else None,'equal_to_base':original_equal,'equal_to_canonical':canonical_equal,'classification':classification,'disposition':disposition,'later_snapshot_equal':later.get(str(p.relative_to(repo)),{}).get('sha256')==digest(raw)}
                records.append(record); copied.append(p)
                for folder in ('original','canonical','review'):
                    target=work/folder/name
                    if target.is_file(): payload.append((target,folder+'/'+name))
            verification['groups'][group]={'original_to_review':patch(repo,work,'original','review','original-to-review.patch'),'canonical_to_review':patch(repo,work,'canonical','review','canonical-to-review.patch')}
            payload.extend((work/n,n) for n in ('original-to-review.patch','canonical-to-review.patch'))
            if group in ('A2','B1'):
                historical=repo/artifact/(group+'_REVIEW.patch')
                target=work/'historical-patch-base'; shutil.copytree(work/'original',target)
                r=subprocess.run(['git','apply','--check',str(historical)],cwd=target,capture_output=True)
                report={'base_check_exit':r.returncode,'base_check_stderr':r.stderr.decode(),'historical_patch_sha256':hashfile(historical)}
                if r.returncode==0:
                    subprocess.run(['git','apply',str(historical)],cwd=target,check=True,capture_output=True)
                    mismatches=[]
                    for record in records:
                        if not record['equal_to_base']:
                            candidate=target/record['target']
                            if not candidate.is_file() or hashfile(candidate)!=record['sha256']: mismatches.append(record['target'])
                    report['unique_review_file_mismatches']=mismatches
                canonical=work/'historical-patch-canonical'; shutil.copytree(work/'canonical',canonical)
                r=subprocess.run(['git','apply','--check',str(historical)],cwd=canonical,capture_output=True)
                report['current_canonical_check_exit']=r.returncode; report['current_canonical_check_stderr']=r.stderr.decode()
                verification['groups'][group]['historical_patch']=report
        artifact_records=[]
        for p in files(repo,artifact,manifest['excluded']):
            if p.suffix=='.zip':
                with zipfile.ZipFile(p) as z:
                    assert all(not set(Path(i.filename).parts)&(SKIP_DIRS|SKIP_FILES) and not i.filename.startswith('/') and '..' not in Path(i.filename).parts for i in z.infolist()),'Unsafe embedded archive '+str(p)
            name=str(p.relative_to(repo)); entry={'path':name,'sha256':hashfile(p),'bytes':p.stat().st_size,'mode':stat.S_IMODE(p.stat().st_mode)}
            entry['disposition']='SUPERSEDED_PROVISIONAL_AUDIT_ONLY' if group=='B2-draft' else 'RETROSPECTIVE_EXPLORATORY_PIT_NOT_VERIFIED' if group=='B2-discovery' else 'REVIEW_ONLY_HISTORICAL_EVIDENCE'
            artifact_records.append(entry); payload.append((p,'artifacts/'+str(p.relative_to(repo/'artifacts'))))
        if group=='B1':
            for name in ('B0_SAMPLE_CHECK.csv','B0_FIELD_AUDIT.csv','B0_PIT_CLASSIFICATION.csv'):
                p=repo/'artifacts/b0-2026-09-29'/name
                artifact_records.append({'path':str(p.relative_to(repo)),'sha256':hashfile(p),'bytes':p.stat().st_size,'mode':stat.S_IMODE(p.stat().st_mode),'disposition':'REQUIRED_RETAINED_B0_INPUT; NOT_SOURCE_BYTE_CLOSURE'})
                payload.append((p,'artifacts/b0-2026-09-29/'+name))
        item={'copied_files':records,'artifact_files':artifact_records,'classification_counts':dict(Counter(r['classification'] for r in records)),'archive':group+'.tar.gz','status':'SUPERSEDED_PROVISIONAL_AUDIT_ONLY' if group=='B2-draft' else 'RETROSPECTIVE_EXPLORATORY_PIT_NOT_VERIFIED' if group=='B2-discovery' else 'REVIEW_ONLY_NOT_ACTIVATED'}
        write(work/'group-manifest.json',item); payload.append((work/'group-manifest.json','group-manifest.json'))
        archive=out/(group+'.tar.gz')
        print('Archiving '+group+' ('+str(sum(p.stat().st_size for p,n in payload))+' uncompressed bytes)',flush=True)
        with tarfile.open(archive,'w:gz',compresslevel=6) as tar:
            for p,name in sorted(payload,key=lambda item:item[1]): add(tar,p,name)
        seen=set()
        with tarfile.open(archive,'r:gz') as tar:
            expected={name:(hashfile(p),p.stat().st_size,stat.S_IMODE(p.stat().st_mode)) for p,name in payload}
            for member in tar:
                assert member.isfile() and member.name not in seen and not member.name.startswith('/') and '..' not in Path(member.name).parts
                assert not (set(Path(member.name).parts)&(SKIP_DIRS|SKIP_FILES))
                seen.add(member.name); h=hashlib.sha256()
                with tar.extractfile(member) as f:
                    for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
                assert (h.hexdigest(),member.size,member.mode)==expected[member.name],member.name
            assert seen==set(expected)
        verification['archives'][group]={'status':'PASS','members':len(seen),'sha256':hashfile(archive),'bytes':archive.stat().st_size,'full_member_bytes_hashes_modes_verified':True}
        manifest['groups'][group]=item
    assert all(r['later_snapshot_equal'] for g in manifest['groups'].values() for r in g['copied_files']),'Review tree differs from retained C02.2 source snapshot'
    verification['copied_tree_retention']={'status':'PASS','files':sum(len(g['copied_files']) for g in manifest['groups'].values()),'snapshot':str(earlier)}
    write(out/'manifest.json',manifest); write(out/'verification.json',verification)
    write(out/'checksums.json',{p.name:hashfile(p) for p in sorted(out.iterdir()) if p.is_file() and p.name!='checksums.json'})
    print(json.dumps({'status':'PASS','classification':{k:v['classification_counts'] for k,v in manifest['groups'].items()},'archives':verification['archives']}),flush=True)

if __name__=='__main__': main()
