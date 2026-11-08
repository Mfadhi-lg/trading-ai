"""Fresh read-only archive/package verification; no Docker or production access."""
import hashlib, json, tarfile, io, gzip, subprocess, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
E=ROOT/'docs/acquisition-readiness/baselines/AR-02-combined-20261008'
P=ROOT/'ops/docker/release/ar02-combined-20261008'
def sha(b): return hashlib.sha256(b).hexdigest()
def file_sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''): h.update(b)
 return h.hexdigest()
def read(p):return json.loads(p.read_text())
sealed=[]
for line in (E/'SHA256SUMS').read_text().splitlines():
 h,p=line.split('  ',1); assert file_sha(ROOT/p)==h,p;sealed.append(p)
m=read(P/'release-manifest.json'); expected='sha256:4206ea61254ec1c490e5284d1aac0361c08b084c7e1186a86141a42c1a2c0b08'
assert m['oci_index_digest']==expected and m['image_reference'].endswith('@'+expected)
arc=ROOT/m['archive']; assert file_sha(arc)==m['archive_sha256'];assert arc.stat().st_size==m['archive_bytes']
source=read(E/'source-manifest.json');assert file_sha(E/'source-manifest.json')==m['source_manifest_sha256']
assert sha(json.dumps(source['files'],sort_keys=True,separators=(',',':')).encode())==m['source_projection_sha256']
app={};predicates=[]
with tarfile.open(arc) as t:
 def blob(d):
  b=t.extractfile('blobs/sha256/'+d.split(':')[1]).read();assert 'sha256:'+sha(b)==d;return b
 index=json.loads(t.extractfile('index.json').read());assert len(index['manifests'])==1; assert index['manifests'][0]['digest']==expected
 idx=json.loads(blob(expected));plat=next(x for x in idx['manifests'] if x.get('platform')=={'architecture':'arm64','os':'linux'})
 assert plat['digest']==m['platform_manifest_digest'];image=json.loads(blob(plat['digest']))
 assert image['config']['digest']==m['config_digest'];config=json.loads(blob(image['config']['digest']))
 assert config['os']=='linux' and config['architecture']=='arm64';assert config['config']['User']=='scraper'
 labels=config['config']['Labels'];assert labels['ai.trading.source.projection.sha256']==m['source_projection_sha256'];assert labels['ai.trading.source.patch.sha256']==m['source_patch_sha256'];assert labels['org.opencontainers.image.revision']==m['source_base_commit'];assert labels['org.opencontainers.image.version']==m['release_id']
 assert [x['digest'] for x in image['layers']]==m['layers']
 for n,layer in enumerate(image['layers']):
  b=blob(layer['digest']);raw=gzip.decompress(b) if b[:2]==b'\x1f\x8b' else b
  assert 'sha256:'+sha(raw)==config['rootfs']['diff_ids'][n]
  with tarfile.open(fileobj=io.BytesIO(raw)) as lt:
   for item in lt:
    name=item.name.removeprefix('./')
    if not name.startswith('app/scrapper/'):continue
    rel=name[len('app/'):]
    assert '.wh.' not in name,name
    if item.isfile():app[rel]=sha(lt.extractfile(item).read())
    elif not item.isdir():raise AssertionError(('unexpected application link',name))
  del raw,b
 att=next(x for x in idx['manifests'] if x.get('annotations',{}).get('vnd.docker.reference.type')=='attestation-manifest')
 assert att['digest']==m['attestation_manifest_digest'];attm=json.loads(blob(att['digest']));assert attm['subject']['digest']==m['platform_manifest_digest']
 for layer in attm['layers']:
  statement=json.loads(blob(layer['digest']));assert any(s['digest'].get('sha256')==m['platform_manifest_digest'].split(':')[1] for s in statement['subject'])
  predicates.append(statement['predicateType'])
 assert set(m['predicates'])==set(predicates)
assert app=={x['path']:x['sha256'] for x in source['files'] if x['path'].startswith('scrapper/')}
baseline={}
b=subprocess.check_output(['git','archive',m['source_base_commit'],'Dockerfile','.dockerignore','scrapper'],cwd=ROOT)
with tarfile.open(fileobj=io.BytesIO(b)) as t:
 for item in t:
  if item.isfile():baseline[item.name]={'path':item.name,'mode':item.mode,'sha256':sha(t.extractfile(item).read())}
new={x['path']:x for x in source['files']};assert not set(baseline)-set(new)
delta=sorted(p for p in new if baseline.get(p)!=new[p])
assert delta==sorted(['scrapper/workflows/exchange/universe.py','scrapper/workflows/exchange/schedule.py','scrapper/tests/test_ar02_snapshot_dates.py','scrapper/tests/test_ar022_access_block.py'])
assert file_sha(E/'combined.patch')==m['source_patch_sha256']
# Exact original package preservation including original OCI and original indexes.
before=read(E/'working-tree-before.json');drift=[];count=0
for p,h in before['files'].items():
 if p=='docs/current/TRADING_AI_ACQUISITION_READINESS_EXECUTION.md' or p.startswith('docs/acquisition-readiness/baselines/AR-02-combined-20261008/'):continue
 actual=file_sha(ROOT/p) if (ROOT/p).is_file() else None
 assert actual==h,p;count+=1
assert file_sha(E/'ledger-before.md')==before['files']['docs/current/TRADING_AI_ACQUISITION_READINESS_EXECUTION.md']
reuse=read(E/'reused-evidence.json')
historical_ledger_resolutions=[]
for f in reuse['files']:
 path=ROOT/f['path']
 if f['path']=='docs/current/TRADING_AI_ACQUISITION_READINESS_EXECUTION.md' and file_sha(path)!=f['sha256']:
  path=E/'ledger-before.md';historical_ledger_resolutions.append({'historical_path':f['path'],'retained_path':str(path.relative_to(ROOT)),'sha256':f['sha256']})
 assert file_sha(path)==f['sha256'],f['path']
original=read(ROOT/'docs/acquisition-readiness/baselines/AR-02.1-release-20261008/source-manifest.json');a={x['path']:x for x in original['files']}
assert sorted(p for p in new if a.get(p)!=new[p])==['scrapper/tests/test_ar022_access_block.py','scrapper/workflows/exchange/schedule.py']
lock=subprocess.check_output(['git','show',m['source_base_commit']+':scrapper/requirements.test.lock'],cwd=ROOT).decode()
blocks=re.split(r'\n(?=[a-zA-Z][a-zA-Z0-9_-]*==)',lock);selected=[b for b in blocks if b.split('==')[0].strip() in ['pytest','iniconfig','packaging','pluggy','pygments']]
assert (E/'test-support.lock').read_text()=='\n'.join(selected)+'\n'
for wheel in (E/'test-wheels').glob('*.whl'):assert file_sha(wheel) in (E/'test-support.lock').read_text()
log=(E/'image-proof.log').read_text();assert 'Ran 10 tests in 2.507s\n\nOK' in log
assert read(E/'image-proof-exit.json')['exit_code']==0
assert read(E/'attempt-01-image-proof-exit.json')['exit_code']!=0
assert "No module named 'pytest'" in (E/'attempt-01-image-proof.log').read_text()
command=read(E/'image-proof-command.json');assert m['image_reference'] in command
assert command[command.index('--network')+1]=='none';assert command[command.index('--pull')+1]=='never';assert '--read-only' in command and '--no-healthcheck' in command
mounts=[command[i+1] for i,x in enumerate(command) if x=='--mount'];assert mounts==[f'type=bind,source={E},target=/proof,readonly']
prior=read(ROOT/'ops/docker/release/c28.1/compose.candidate.json');candidate=read(P/'compose.candidate.json')
assert len(candidate['services'])==21
for name,s in candidate['services'].items():
 assert s==dict(prior['services'][name],image=m['image_reference']),name
 assert s['profiles']==['authorized-'+name] and s['restart']=='no' and 'build' not in s
for name,c in candidate['configs'].items():assert c==dict(prior['configs'][name],file=str(P/Path(prior['configs'][name]['file']).name))
for key in set(candidate)-{'configs','services'}:assert candidate[key]==prior[key]
for kind in ['core','documents']:
 identity=read(P/f'runtime-identity-{kind}.json');assert identity['authorized'] is False and identity['image_id']==m['platform_manifest_digest'];assert identity['source_projection_sha256']==m['source_projection_sha256']
assert not (E/'default-services.log').read_text().strip()
assert (E/'runtime-before.log').read_bytes()==(E/'runtime-after.log').read_bytes()
assert len((E/'runtime-after.log').read_text().splitlines())==15
assert all(' exited ' in l for l in (E/'runtime-after.log').read_text().splitlines())
assert m['production_activation_authorized'] is False and m['production_activation']=='NOT_STARTED'
result={'status':'PASS','reviewed_digest':expected,'package_checksum_index_sha256':file_sha(E/'SHA256SUMS'),'sealed_files_verified':len(sealed),'archive_sha256':file_sha(arc),'source_projection_sha256':m['source_projection_sha256'],'actual_oci_application_files_verified':len(app),'oci_compressed_and_uncompressed_layer_digests_verified':len(m['layers']),'attestation_predicate_subjects_verified':predicates,'intended_delta':delta,'original_ar021_and_preexisting_files_verified':count,'reused_checksums_verified':len(reuse['files']),'historical_ledger_resolutions':historical_ledger_resolutions,'focused_tests_reused':10,'test_wheels_hash_verified':5,'role_configs_verified':21,'historical_stopped_inventory':15,'docker_commands_executed':False,'production_access':False,'exchange_requests':0,'tests_builds_migrations_repeated':False}
(OUT/'verification.json').write_text(json.dumps(result,indent=2)+'\n'); print(json.dumps(result))
