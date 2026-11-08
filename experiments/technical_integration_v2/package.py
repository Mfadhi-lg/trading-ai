"""Compact deterministic review ZIP with offline rebuild and included-source checks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
from .build import ROOT,OUT,OLD,read,sha

def paths():
    selected={p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip' and p.name not in ('.DS_Store','SHA256SUMS','ZIP_RECEIPT.json')}
    for name in ('earnings_reaction_v1','subsector_index_v1','subsector_index_v2','technical_integration_v1','technical_integration_v2'):
        selected|={p for p in (ROOT/'experiments'/name).glob('*.py')}
    selected|={p for p in (OLD/'charts').rglob('*') if p.is_file() and p.name!='.DS_Store'}
    for name in ('GLOBAL_MARKET_DAILY.csv','INDIA_PREOPEN_CONTEXT.csv','TECHNICAL_ACCEPTANCE.md','OUTSTANDING_ACQUISITION_MANIFEST.json'):
        selected.add(OLD/name)
    selected.add(OLD/'inputs/preservation-before.json')
    for r in read(OUT/'MARKET_DATA_PROVENANCE.csv'):selected.add(ROOT/r['source_file'])
    for r in read(OUT/'RETAINED_INDEX_INTERVALS.csv'):selected.add(ROOT/r['source_file'])
    for p in ('artifacts/subsector-index-v2/inputs/classifications.csv','artifacts/subsector-index-v2/Q2_COHORT_MAPPING.csv',
              'artifacts/subsector-ss01/v2/q2-deduplicated-focus.csv','artifacts/earnings-reaction-v1/er03/market-data-inventory.json'):
        selected.add(ROOT/p)
    # Build may use all retained index interval references listed in the inventory.
    data=json.loads((ROOT/'artifacts/earnings-reaction-v1/er03/market-data-inventory.json').read_text())
    for r in data['datasets']:
        if r['instrument_key'].startswith('NSE_INDEX|'):selected.add(ROOT/r['source']['original_path'])
    return sorted(selected)

def archive(target,selected):
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in selected:
            info=zipfile.ZipInfo(str(p.relative_to(ROOT)),date_time=(2026,11,4,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16;z.writestr(info,p.read_bytes())

def main():
    target=OUT/'TECH-06-review.zip';selected=paths();archive(target,selected)
    # All generated transforms/charts/reports must reproduce byte for byte.
    compare=[p for p in OUT.rglob('*') if p.is_file() and p.suffix in ('.csv','.html','.js','.md') and 'inputs' not in p.parts and 'raw' not in p.parts and p.name!='VALIDATION_REPORT.md']
    with tempfile.TemporaryDirectory(prefix='tech06-replay-') as directory:
        temp=Path(directory)
        with zipfile.ZipFile(target) as z:z.extractall(temp)
        results=[]
        for module in ('build','verify'):
            cmd=[sys.executable,'-m','experiments.technical_integration_v2.'+module]+(['--compact-review'] if module=='verify' else [])
            env=dict(os.environ);env.pop('PYTHONPATH',None)
            result=subprocess.run(cmd,cwd=temp,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            results.append(dict(module=module,exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr))
            if result.returncode:raise RuntimeError(str(results))
        differences=[str(p.relative_to(ROOT)) for p in compare if not (temp/p.relative_to(ROOT)).exists() or sha(p)!=sha(temp/p.relative_to(ROOT))]
        if differences:raise AssertionError(differences)
        replay=dict(passed=True,generated_files_compared=len(compare),byte_differences=differences,commands=results,
            scope='Offline transforms and charts from included original evidence. Compact verification skips absent preservation-baseline paths; no full-repository or provider-delivery certification.')
    (OUT/'ZIP_REPLAY.json').write_text(json.dumps(replay,indent=2)+'\n')
    selected=paths()
    (OUT/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(ROOT))+'\n' for p in selected))
    selected.append(OUT/'SHA256SUMS');selected.sort();archive(target,selected)
    with tempfile.TemporaryDirectory(prefix='tech06-zip-determinism-') as directory:
        other=Path(directory)/'again.zip';archive(other,selected);assert sha(target)==sha(other)
    # Separate receipt avoids a self-referential ZIP hash.
    (OUT/'ZIP_RECEIPT.json').write_text(json.dumps(dict(sha256=sha(target),bytes=target.stat().st_size,entries=len(selected),deterministic=True,receipt_outside_archive=True),indent=2)+'\n')
    print(json.dumps(dict(replay_passed=True,generated_files_compared=len(compare),entries=len(selected),zip_bytes=target.stat().st_size)))

if __name__=='__main__':main()
