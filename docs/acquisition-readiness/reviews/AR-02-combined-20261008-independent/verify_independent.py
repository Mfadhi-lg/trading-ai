"""Separate release review: read OCI and retained evidence without Docker or activation.

Written by /root/independent_release_reviewer; does not import prior verifier.
Only output is verification.json in this new review directory.
"""
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
B = ROOT / 'docs/acquisition-readiness/baselines/AR-02-combined-20261008'
R = ROOT / 'ops/docker/release/ar02-combined-20261008'
def sha(data):
    return hashlib.sha256(data).hexdigest()
def filehash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4*1024*1024), b''):
            h.update(block)
    return h.hexdigest()
def read(path):
    return json.loads(path.read_text())

manifest = read(R/'release-manifest.json')
expected_index = '4206ea61254ec1c490e5284d1aac0361c08b084c7e1186a86141a42c1a2c0b08'
assert manifest['oci_index_digest'] == 'sha256:'+expected_index
package_index_hash = filehash(B/'SHA256SUMS')
assert package_index_hash == '46965ab5e380794dff07060feda9ee8c76f2e9921189885253e282c8525c68f4'
checks = []
for line in (B/'SHA256SUMS').read_text().splitlines():
    digest, path = line.split('  ', 1)
    assert filehash(ROOT/path) == digest, path
    checks.append(path)
source = read(B/'source-manifest.json')
assert filehash(B/'source-manifest.json') == manifest['source_manifest_sha256']
assert sha(json.dumps(source['files'],sort_keys=True,separators=(',',':')).encode()) == manifest['source_projection_sha256']
assert filehash(B/'combined.patch') == manifest['source_patch_sha256']
context = Path(source['context'])
source_files = {i['path']:i for i in source['files']}
assert {str(p.relative_to(context)) for p in context.rglob('*') if p.is_file()} == set(source_files)
for path, item in source_files.items():
    assert filehash(context/path) == item['sha256'], path
    assert (context/path).stat().st_mode & 0o777 == item['mode'], path
assert subprocess.check_output(['git','rev-parse',source['base_commit']+'^{tree}'],cwd=ROOT,text=True).strip() == manifest['source_base_tree']
base_bytes = subprocess.check_output(['git','archive',source['base_commit']],cwd=ROOT)
with tarfile.open(fileobj=io.BytesIO(base_bytes)) as t:
    base = {m.name:t.extractfile(m).read() for m in t if m.isfile() and m.name in source_files}
changed = sorted(p for p,i in source_files.items() if p not in base or sha(base[p]) != i['sha256'])
assert changed == sorted(source['changed_paths']), changed
assert changed == ['scrapper/tests/test_ar022_access_block.py','scrapper/tests/test_ar02_snapshot_dates.py','scrapper/workflows/exchange/schedule.py','scrapper/workflows/exchange/universe.py']
with tarfile.open(fileobj=io.BytesIO(base_bytes)) as t:
    accepted_paths = {m.name for m in t if m.isfile() and (m.name.startswith('scrapper/') or m.name in ['Dockerfile','.dockerignore'])}
assert not accepted_paths-set(source_files)
ar021 = read(ROOT/'docs/acquisition-readiness/baselines/AR-02.1-release-20261008/source-manifest.json')
ar021_files = {i['path']:i['sha256'] for i in ar021['files']}
ar021_delta = sorted(p for p,i in source_files.items() if ar021_files.get(p) != i['sha256'])
assert ar021_delta == ['scrapper/tests/test_ar022_access_block.py','scrapper/workflows/exchange/schedule.py']

archive = ROOT/manifest['archive']
assert archive.stat().st_size == manifest['archive_bytes']
assert filehash(archive) == manifest['archive_sha256']
application = {}
layer_results = []
with tarfile.open(archive) as t:
    def blob(descriptor):
        digest = descriptor['digest'].split(':')[1]
        data = t.extractfile('blobs/sha256/'+digest).read()
        assert sha(data) == digest
        assert len(data) == descriptor['size']
        return data
    outer = json.loads(t.extractfile('index.json').read())
    target = next(d for d in outer['manifests'] if d['digest']=='sha256:'+expected_index)
    index = json.loads(blob(target))
    platform_d = next(d for d in index['manifests'] if d['digest']==manifest['platform_manifest_digest'])
    assert platform_d['platform']['os']=='linux' and platform_d['platform']['architecture']=='arm64'
    platform = json.loads(blob(platform_d))
    assert platform['config']['digest'] == manifest['config_digest']
    config = json.loads(blob(platform['config']))
    labels = config['config']['Labels']
    assert source['base_commit'] in labels.values()
    assert source['patch_sha256'] in labels.values()
    assert source['source_projection_sha256'] in labels.values()
    assert [d['digest'] for d in platform['layers']] == manifest['layers']
    assert len(platform['layers']) == len(config['rootfs']['diff_ids'])
    for d, diff_id in zip(platform['layers'], config['rootfs']['diff_ids']):
        compressed = blob(d)
        raw = gzip.decompress(compressed) if compressed[:2]==b'\x1f\x8b' else compressed
        assert 'sha256:'+sha(raw) == diff_id
        with tarfile.open(fileobj=io.BytesIO(raw)) as layer:
            for m in layer:
                name = m.name.removeprefix('./').lstrip('/')
                if not name.startswith('app/scrapper/'):
                    continue
                rel = name.removeprefix('app/')
                assert '.wh.' not in rel, ('unexpected application whiteout',rel)
                assert not m.issym() and not m.islnk(), ('application link',rel)
                if m.isfile():
                    application[rel] = sha(layer.extractfile(m).read())
        layer_results.append({'digest':d['digest'],'diff_id':diff_id,'compressed_bytes':len(compressed)})
    expected_app = {p:i['sha256'] for p,i in source_files.items() if p.startswith('scrapper/')}
    assert application == expected_app
    att_d = next(d for d in index['manifests'] if d['digest']==manifest['attestation_manifest_digest'])
    att = json.loads(blob(att_d))
    blob(att['config'])
    statements = []
    for d in att['layers']:
        statement = json.loads(blob(d))
        assert any(s['digest']['sha256']==manifest['platform_manifest_digest'].split(':')[1] for s in statement['subject'])
        statements.append(statement['predicateType'])
    assert sorted(statements)==sorted(manifest['predicates'])

preservation = read(B/'working-tree-before.json')
preserved = []
preparation_owned = []
for path,digest in preservation['files'].items():
    if path.startswith(str(B.relative_to(ROOT))+'/') or path.startswith(str(R.relative_to(ROOT))+'/') or path.startswith('artifacts/acquisition-readiness/AR-02-combined-20261008/'):
        preparation_owned.append({'path':path,'before_sha256':digest,'final_sha256':filehash(ROOT/path) if (ROOT/path).is_file() else None})
        continue
    if path == 'docs/current/TRADING_AI_ACQUISITION_READINESS_EXECUTION.md':
        assert filehash(B/'ledger-before.md') == digest
    elif digest is None:
        assert not (ROOT/path).exists()
    else:
        assert filehash(ROOT/path) == digest, path
    preserved.append(path)
reused = read(B/'reused-evidence.json')
qualified_reuse = []
for item in reused['files']:
    path = ROOT/item['path']
    if filehash(path) != item['sha256']:
        assert item['path']=='docs/current/TRADING_AI_ACQUISITION_READINESS_EXECUTION.md'
        assert filehash(B/'ledger-before.md')==item['sha256']
        qualified_reuse.append({'original_path':item['path'],'resolved_path':str((B/'ledger-before.md').relative_to(ROOT)),'sha256':item['sha256']})

candidate = read(R/'compose.candidate.json')
accepted = read(ROOT/'ops/docker/release/c28.1/compose.candidate.json')
assert set(candidate['services'])==set(accepted['services']) and len(candidate['services'])==21
roles=[]
for name,service in candidate['services'].items():
    assert service['image']==manifest['image_reference']
    assert service['restart']=='no' and 'build' not in service
    assert service['profiles'] and all(p.startswith('authorized-') for p in service['profiles'])
    old=accepted['services'][name].copy()
    new=service.copy()
    old.pop('image'); new.pop('image')
    assert old==new, name
    roles.append({'name':name,'profiles':service['profiles'],'image':service['image'],'restart':service['restart'],'config_sources':[v['source'] for v in service.get('configs',[])]})
for name,conf in candidate['configs'].items():
    old=accepted['configs'][name].copy(); new=conf.copy()
    old.pop('file'); new.pop('file'); assert old==new
    assert Path(conf['file']).parent==R
for name in ('runtime-identity-core.json','runtime-identity-documents.json'):
    identity=read(R/name)
    assert identity['authorized'] is False
    assert identity['image_id']==manifest['platform_manifest_digest']
    assert identity['source_projection_sha256']==manifest['source_projection_sha256']
    assert identity['source_patch_sha256']==manifest['source_patch_sha256']
assert (B/'default-services.log').read_bytes()==b''
assert (B/'runtime-before.log').read_bytes()==(B/'runtime-after.log').read_bytes()
inventory=(B/'runtime-after.log').read_text().splitlines()
assert len(inventory)==15 and all(line.split()[1]=='exited' for line in inventory)
command=read(B/'image-proof-command.json')
assert command[command.index('--network')+1]=='none'
assert '--read-only' in command and manifest['image_reference'] in command
assert all('readonly' in command[i+1] and '/proof' in command[i+1] for i,x in enumerate(command) if x=='--mount')
assert read(B/'image-proof-exit.json')['exit_code']==0
assert read(B/'attempt-01-image-proof-exit.json')['exit_code']==1
log=(B/'image-proof.log').read_text()
assert 'Ran 10 tests' in log and '\nOK\n' in log
assert 'No broken requirements found.' in log
wheels=[]
lock=(B/'test-support.lock').read_text()
for p in sorted((B/'test-wheels').glob('*.whl')):
    digest=filehash(p)
    assert 'sha256:'+digest in lock
    wheels.append({'file':p.name,'sha256':digest})
result={'status':'PASS','reviewer':'Codex /root/independent_release_reviewer','method':'New independently authored checks; direct offline OCI tar inspection; no prior verifier executed','image':manifest['image_reference'],'package_checksum_index_sha256':package_index_hash,'archive_sha256':filehash(archive),'source_projection_sha256':manifest['source_projection_sha256'],'package_entries_verified':len(checks),'source_context_files':len(source_files),'actual_archive_application_files':len(application),'source_delta':changed,'delta_vs_original_ar021':ar021_delta,'layers':layer_results,'attestation_predicates':statements,'preserved_preexisting_paths':len(preserved),'preparation_owned_paths':preparation_owned,'reused_evidence_entries':len(reused['files']),'qualified_reuse':qualified_reuse,'roles':roles,'test_wheels':wheels,'focused_test_evidence':'10 tests / exit 0; reused, not rerun','operations':'No Docker command, service, production access/write, exchange request, hold change, or AR-02.3 execution'}
(OUT/'verification.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['roles','layers','test_wheels']},indent=2))
