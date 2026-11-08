"""Extract the compact ZIP, rebuild offline, compare every deliverable hash."""
from pathlib import Path
import json,subprocess,sys,tempfile,zipfile
from .prepare import OUT,ROOT
from .report import package,reports
from experiments.earnings_reaction_v1.er03 import digest,write_json

def main():
    reports();archive=package()
    outputs=sorted(p for p in OUT.rglob('*') if p.is_file() and (p.suffix=='.csv' or ('charts' in p.parts and p.suffix in ('.html','.js','.css')) or p.name in ('status.json','chart-index.json','ACQUISITION_RESOLUTION.json','numerical-validation.json','checkpoint-prefix-validation.json')))
    expected={str(p.relative_to(ROOT)):digest(p) for p in outputs}
    runs=[]
    with tempfile.TemporaryDirectory(prefix='ss-index02-replay-') as tmp:
        dest=Path(tmp)
        with zipfile.ZipFile(archive) as z:z.extractall(dest)
        commands=[['-m','experiments.subsector_index_v2.build','--compact-review'],['-m','experiments.subsector_index_v2.verify'],
            ['-m','unittest','experiments.subsector_index_v2.test_v2','experiments.subsector_index_v1.tests.test_indices','experiments.earnings_reaction_v1.tests.test_er04']]
        for args in commands:
            r=subprocess.run([sys.executable,'-B',*args],cwd=dest,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            runs.append(dict(command='python3 -B '+' '.join(args),returncode=r.returncode,stdout=r.stdout[-2000:],stderr=r.stderr[-3000:]))
            if r.returncode:
                write_json(OUT/'ZIP_REPLAY.json',dict(passed=False,runs=runs));raise AssertionError(runs[-1])
        actual={path:digest(dest/path) for path in expected}
        different=[path for path in expected if actual[path]!=expected[path]]
        result=dict(passed=not different,outputs_compared=len(expected),differences=different,runs=runs,output_sha256=actual,
            limitation='Replay verifies included evidence and outputs; absent original databases/full-workspace preservation files are explicitly reported by compact mode')
        write_json(OUT/'ZIP_REPLAY.json',result)
        if different:raise AssertionError(different)
    # The final ZIP includes the replay receipt; verify deterministic packaging
    # twice with identical member bytes and fixed metadata.
    reports();first=package();a=digest(first);package();b=digest(first)
    if a!=b:raise AssertionError('ZIP packaging nondeterministic')
    (OUT/'ZIP_DETERMINISM.json').write_text(json.dumps(dict(passed=True,identical_zip_sha256=a,metadata='Fixed timestamps, sorted paths, permissions and compression; repeated packaging identical'),indent=2)+'\n')
    print(json.dumps(dict(replay_outputs=len(expected),differences=0,zip_deterministic=True,zip_sha256=a)))

if __name__=='__main__':main()
