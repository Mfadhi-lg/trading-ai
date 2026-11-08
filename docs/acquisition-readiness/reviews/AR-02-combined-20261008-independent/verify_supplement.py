"""Independent patch replay, whole-config comparison and retained-proof reading."""
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
B=ROOT/'docs/acquisition-readiness/baselines/AR-02-combined-20261008'
R=ROOT/'ops/docker/release/ar02-combined-20261008'
def sha(data): return hashlib.sha256(data).hexdigest()
def read(p): return json.loads(p.read_text())
source=read(B/'source-manifest.json')
raw=subprocess.check_output(['git','archive',source['base_commit'],'Dockerfile','.dockerignore','scrapper'],cwd=ROOT)
with tempfile.TemporaryDirectory(prefix='ar02-independent-review-',dir='/private/tmp') as scratch:
    scratch=Path(scratch)
    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
        for m in t:
            if m.isdir(): continue
            assert m.isfile() and not Path(m.name).is_absolute() and '..' not in Path(m.name).parts
            p=scratch/m.name; p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(t.extractfile(m).read()); p.chmod(m.mode)
    subprocess.run(['git','apply',str(B/'combined.patch')],cwd=scratch,check=True)
    assert {str(p.relative_to(scratch)) for p in scratch.rglob('*') if p.is_file()}=={i['path'] for i in source['files']}
    for i in source['files']:
        assert sha((scratch/i['path']).read_bytes())==i['sha256'],i['path']
candidate=read(R/'compose.candidate.json')
accepted=read(ROOT/'ops/docker/release/c28.1/compose.candidate.json')
candidate['x-runtime-image']=accepted['x-runtime-image']
for n,s in candidate['services'].items(): s['image']=accepted['services'][n]['image']
for n,c in candidate['configs'].items(): c['file']=accepted['configs'][n]['file']
assert candidate==accepted
prior=ROOT/'docs/acquisition-readiness/reviews/AR-02-combined-20261008'
for line in (prior/'SHA256SUMS').read_text().splitlines():
    digest,path=line.split('  ',1)
    assert sha((ROOT/path).read_bytes())==digest
replay=read(ROOT/'docs/acquisition-readiness/baselines/AR-02.1/original-replay.json')
capture=read(ROOT/'docs/acquisition-readiness/baselines/AR-02.1/original-capture.json')
originals=[]
for item in replay['originals']:
    p=ROOT/'docs/acquisition-readiness/baselines/AR-02.1/originals'/ (item['sha256']+'.csv')
    assert sha(p.read_bytes())==item['sha256'] and p.stat().st_size==item['bytes']
    originals.append({'snapshot_id':item['snapshot_id'],'sha256':item['sha256'],'parsed_accepted':item['parsed_accepted'],'parsed_rejected':item['parsed_rejected'],'after_IST':item['after_IST']['status'],'findings':[f['code'] for f in item['after_IST']['findings']]})
assert next(i for i in originals if i['snapshot_id']==266)['findings']==['row_count_floor']
assert next(i for i in originals if i['snapshot_id']==261)['parsed_accepted']==4351
assert next(i for i in originals if i['snapshot_id']==261)['parsed_rejected']==1
assert 'Ran 94 tests' in (ROOT/'docs/acquisition-readiness/baselines/AR-02.1/tests.log').read_text()
parent=read(ROOT/'docs/acquisition-readiness/baselines/AR-01.3-parent-20261008/final.json')
assert parent['status']=='PASS' and len(parent['probes'])==3
assert all(p['status']=='PASS' and p['exit_code']==0 for p in parent['probes'])
assert len({p['db_sha256'] for p in parent['probes']})==1
result={'status':'PASS','exact_patch_replay_files':len(source['files']),'whole_compose_equal_after_only_image_and_config_file_path_normalization':True,'previous_review_checksum_index_sha256':sha((prior/'SHA256SUMS').read_bytes()),'previous_review_records_verified':4,'authentic_originals':originals,'retained_94_test_suite':'PASS; not rerun','retained_parent_probes':parent['probes'],'scope':'Offline existing evidence only; no database opened or operations repeated'}
(OUT/'supplement.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps(result,indent=2))
