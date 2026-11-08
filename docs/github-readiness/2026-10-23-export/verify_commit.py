"""Verify the independent Git object closure and test an offline materialization."""
from pathlib import Path
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
EXPORT=ROOT/'github-source-candidate-20261023'

def main():
    env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=EXPORT,env=env)
    expected=json.loads((OUT/'publication-inclusion-list.json').read_text())
    actual=git('ls-tree','-r','--name-only','-z','HEAD').split(b'\0')
    names={n.decode() for n in actual if n}
    assert names=={r['path'] for r in expected}
    mismatches=[]
    for r in expected:
        if hashlib.sha256(git('show','HEAD:'+r['path'])).hexdigest()!=r['sha256']:
            mismatches.append(r['path'])
    assert not mismatches
    known=[(ROOT/n).read_bytes().strip() for n in ('.screener_csrf','.screener_session')]
    patterns={
        'github_token':rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})',
        'aws_access_id':rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
        'provider_key':rb'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{30,}',
        'basic_auth_url':rb'https?://[^\s/:]{2,}:[^\s/@]{4,}@',
    }
    matches=[];pattern_candidates=[];blobs=0;size=0;largest=0
    objects=git('rev-list','--objects','--all').splitlines()
    for row in objects:
        oid=row.split(b' ',1)[0].decode()
        if git('cat-file','-t',oid).strip()!=b'blob':continue
        data=git('cat-file','blob',oid)
        blobs+=1;size+=len(data);largest=max(largest,len(data))
        if any(v and v in data for v in known):matches.append({'object':oid,'rule':'known_local_credential'})
        if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',data):
            matches.append({'object':oid,'rule':'private_key'})
        for rule,pattern in patterns.items():
            for match in re.finditer(pattern,data):
                pattern_candidates.append({'object':oid,'path':row.split(b' ',1)[1].decode() if b' ' in row else None,
                    'line':data[:match.start()].count(b'\n')+1,'rule':rule})
    assert not matches
    target=OUT/'from-commit'
    target.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='source-commit-materialization-') as scratch:
        base=Path(scratch)
        archive=tarfile.open(fileobj=io.BytesIO(git('archive','--format=tar','HEAD')))
        for member in archive:
            path=base/member.name
            if Path(member.name).is_absolute() or '..' in Path(member.name).parts:
                raise RuntimeError('Unsafe archive path')
            if member.isdir():path.mkdir(parents=True,exist_ok=True)
            elif member.isfile():
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(archive.extractfile(member).read())
                path.chmod(member.mode)
            else:raise RuntimeError('Nonregular source member')
        runenv=env.copy();runenv['EXPORT_TEST_REPORT_DIR']=str(target)
        r=subprocess.run([sys.executable,'-B',str(base/'scripts/audit/run_source_regressions.py')],
                         cwd=base,env=runenv,capture_output=True,text=True)
        migration=subprocess.run([sys.executable,'-B',str(base/'scripts/audit/check_migration_manifest.py')],
                                 cwd=base,env=runenv,capture_output=True,text=True)
        (target/'migration-lineage.json').write_text(migration.stdout)
        if r.returncode or migration.returncode:
            raise RuntimeError('Commit materialization verification failed; see isolated logs')
        hostenv=env.copy();hostenv['EXPORT_TEST_SOURCE_ROOT']=str(base)
        host=subprocess.run([sys.executable,'-B',str(OUT/'run_host_regressions.py')],
                            cwd=base,env=hostenv,capture_output=True,text=True)
    result={'commit':git('rev-parse','HEAD').decode().strip(),
            'commit_count':int(git('rev-list','--all','--count')),
            'tree_matches_explicit_manifest':True,'tracked_files':len(names),
            'reachable_blobs_scanned':blobs,'reachable_blob_bytes':size,
            'largest_reachable_blob_bytes':largest,'known_credential_or_private_key_matches':matches,
            'reachable_blob_pattern_candidates':pattern_candidates,
            'from_commit_regressions_passed':True,'from_commit_migration_lineage_passed':True,
            'from_commit_host_test_returncode':host.returncode,
            'materialization':'git archive HEAD into temporary directory, no clone/fetch/remote',
            'remote_names':git('remote').decode().splitlines(),
            'status':git('status','--porcelain').decode(),
            'fsck_output':git('fsck','--full','--no-reflogs').decode()}
    (OUT/'commit-integrity.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
