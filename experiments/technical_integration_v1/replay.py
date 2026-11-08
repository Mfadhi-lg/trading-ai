"""Isolated offline ZIP replay and deterministic packaging of unchanged members."""
from pathlib import Path
import json,subprocess,sys,tempfile,zipfile
from .prepare import OUT,ROOT
from .report import reports,package,outstanding,legacy_evidence
from experiments.earnings_reaction_v1.er03 import digest,write_json

def main():
    outstanding();legacy_evidence();reports();archive=package()
    outputs=sorted(p for p in OUT.rglob('*') if p.is_file() and (p.suffix=='.csv' or ('charts' in p.parts and p.suffix in ('.html','.js','.css')) or p.name in ('status.json','chart-index.json','numerical-validation.json')))
    expected={str(p.relative_to(ROOT)):digest(p) for p in outputs};runs=[]
    with tempfile.TemporaryDirectory(prefix='tech05-replay-') as tmp:
        target=Path(tmp)
        with zipfile.ZipFile(archive) as z:z.extractall(target)
        commands=[['-m','experiments.technical_integration_v1.build','--compact-review'],['-m','experiments.technical_integration_v1.verify'],
            ['-m','unittest','experiments.technical_integration_v1.test_integration','experiments.earnings_reaction_v1.tests.test_market_context','experiments.earnings_reaction_v1.tests.test_er04','experiments.subsector_index_v1.tests.test_indices','experiments.subsector_index_v2.test_v2']]
        for args in commands:
            r=subprocess.run([sys.executable,'-B',*args],cwd=target,capture_output=True,text=True)
            runs.append(dict(command='python3 -B '+' '.join(args),returncode=r.returncode,stdout=r.stdout[-2000:],stderr=r.stderr[-3000:]))
            if r.returncode:
                write_json(OUT/'ZIP_REPLAY.json',dict(passed=False,runs=runs));raise AssertionError(runs[-1])
        actual={p:digest(target/p) for p in expected};different=[p for p in expected if expected[p]!=actual[p]]
        write_json(OUT/'ZIP_REPLAY.json',dict(passed=not different,outputs_compared=len(expected),differences=different,runs=runs,output_sha256=actual,
            limitation='Original research databases/full-workspace preservation are deliberately omitted; compact build explicitly reports missing baseline files. The15 reconciled legacy database tests were run in the original workspace.'))
        if different:raise AssertionError(different)
    reports();package();a=digest(archive);package();b=digest(archive)
    if a!=b:raise AssertionError('ZIP packaging differs for unchanged members')
    write_json(OUT/'ZIP_DETERMINISM.json',dict(passed=True,sha256=a,method='Two identical fixed-timestamp/sorted-member ZIP builds of unchanged bytes; receipt excluded to avoid self-referential archive hash'))
    print(json.dumps(dict(replayed_outputs=len(expected),differences=0,zip_deterministic=True,sha256=a)))

if __name__=='__main__':main()
