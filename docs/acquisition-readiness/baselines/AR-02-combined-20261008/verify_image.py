"""Load/export verification and a bounded offline image proof. No compose up."""
import json
import subprocess
from prepare import ROOT, OUT, RELEASE, ARCHIVE, write

manifest = json.loads((RELEASE/'release-manifest.json').read_text())
with (OUT/'image-load.log').open('w') as log:
    subprocess.run(['docker', 'load', '-i', str(ARCHIVE)], stdout=log, stderr=subprocess.STDOUT, check=True)
ref = manifest['image_reference']
loaded = json.loads(subprocess.check_output(['docker', 'image', 'inspect', ref]))[0]
# Store public identity fields only, never environment/secret values.
write('loaded-image.json', {'id': loaded['Id'], 'repo_digests': loaded['RepoDigests'],
    'labels': loaded['Config']['Labels'], 'user': loaded['Config']['User'],
    'architecture': loaded['Architecture'], 'os': loaded['Os']})
assert loaded['Id'] == manifest['oci_index_digest']
assert ref in loaded['RepoDigests']
cmd = ['docker', 'run', '--rm', '--pull', 'never', '--name', 'trading-ai-ar02-combined-offline-image-proof',
    '--no-healthcheck', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
    '--security-opt', 'no-new-privileges', '--cpus', '0.5', '--memory', '1536m', '--pids-limit', '128',
    '--tmpfs', '/fixture:rw,nosuid,nodev,size=256m,mode=1777',
    '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m,mode=1777',
    '--tmpfs', '/data:rw,nosuid,nodev,size=16m,mode=1777',
    '--mount', f'type=bind,source={OUT},target=/proof,readonly',
    '--env', 'TRADING_AI_ENV=test', '--env', 'PYTHONPATH=/app',
    '--env', 'SCRAPER_STORE_PATH=/fixture', '--env', 'SCRAPER_DB_PATH=/fixture/scraper.db',
    '--env', 'SCRAPER_DOCUMENT_STORE_PATH=/fixture/documents',
    '--entrypoint', 'python', ref, '/proof/image_proof.py']
write('image-proof-command.json', cmd)
with (OUT/'image-proof.log').open('w') as log:
    result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
write('image-proof-exit.json', {'exit_code': result.returncode})
assert result.returncode == 0
base = ['docker', 'compose', '-f', str(RELEASE/'compose.candidate.json')]
rendered = json.loads(subprocess.check_output(base+['--profile', '*', 'config', '--format', 'json']))
write('candidate-rendered.json', rendered)
defaults = subprocess.check_output(base+['config', '--services']).decode()
(OUT/'default-services.log').write_text(defaults)
assert not defaults.strip()
assert len(rendered['services']) == 21
prior = json.loads((ROOT/'ops/docker/release/c28.1/compose.candidate.json').read_text())
candidate = json.loads((RELEASE/'compose.candidate.json').read_text())
for name, service in candidate['services'].items():
    expected = dict(prior['services'][name], image=ref)
    assert service == expected, name
    assert service['profiles'] == ['authorized-'+name] and service['restart'] == 'no'
    assert 'build' not in service
for name, value in candidate['configs'].items():
    assert value == dict(prior['configs'][name], file=str(RELEASE/__import__('pathlib').Path(prior['configs'][name]['file']).name))
for key in set(candidate)-{'services', 'configs'}:
    assert candidate[key] == prior[key], key
for kind in ['core', 'documents']:
    identity = json.loads((RELEASE/f'runtime-identity-{kind}.json').read_text())
    assert identity['authorized'] is False and identity['image_id'] == manifest['platform_manifest_digest']
with (OUT/'runtime-after.log').open('w') as log:
    subprocess.run(['docker', 'ps', '-a', '--filter', 'label=com.docker.compose.project=trading-ai',
                    '--format', '{{.Names}} {{.State}} {{.Image}}'], stdout=log, check=True)
assert (OUT/'runtime-before.log').read_bytes() == (OUT/'runtime-after.log').read_bytes()
assert all(' exited ' in line for line in (OUT/'runtime-after.log').read_text().splitlines())
write('package-verification.json', {'status': 'PASS', 'loaded_digest_matches_oci': True,
    'roles': 21, 'zero_default_services': True, 'identities_authorized': False,
    'config_change_scope': 'only image digest and new candidate config-file paths',
    'production_activation': 'NOT_STARTED', 'production_inventory_unchanged': True,
    'independent_review': 'PENDING_FOR_NEW_DIGEST'})
manifest['package_verification'] = 'PASS'
(RELEASE/'release-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
print('PASS: loaded digest, in-image source and ten tests, locked config, stopped inventory')
