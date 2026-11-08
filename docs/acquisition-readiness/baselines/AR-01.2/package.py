"""Render a locked candidate package after verifying its OCI blobs."""
import hashlib
import importlib.util
import json
import subprocess
import re
import tarfile
from pathlib import Path
from prepare import ROOT, OUT, COMMIT, digest, write

RELEASE = ROOT/'ops/docker/release/c28.1'
RELEASE_ID = 'c28.1-ar012-20261007'
ARCHIVE = Path('/private/tmp/trading-ai-ar012-release.oci.tar')

def read(path):
    return json.loads(Path(path).read_text())

if __name__ == '__main__':
    spec = importlib.util.spec_from_file_location('oci', ROOT/'scripts/audit/check_oci_release.py')
    oci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oci)
    image = oci.inspect(ARCHIVE)
    write('oci-verification.json', image)
    with tarfile.open(ARCHIVE) as archive:
        attestation = json.loads(oci.load_blob(archive, image['attestation_manifest_digest']))
        for layer in attestation['layers']:
            kind = layer.get('annotations', {}).get('in-toto.io/predicate-type')
            name = {'https://spdx.dev/Document': 'sbom.json',
                    'https://slsa.dev/provenance/v1': 'provenance.json'}.get(kind)
            if name:
                (OUT/name).write_bytes(oci.load_blob(archive, layer['digest']))
    reference = 'trading-ai-c281-release@' + image['oci_index_digest']
    source = read(OUT/'source-manifest.json')
    context = Path(source['context'])
    lock = context/'scrapper/requirements.lock'
    env_names = set()
    for path in (context/'scrapper').rglob('*.py'):
        if '/tests/' not in str(path):
            env_names.update(re.findall(r'os\.(?:getenv|environ\.get)\(\s*[\"\x27]([A-Z][A-Z0-9_]+)', path.read_text()))
    write('dependency-config-inventory.json', {
        'dockerfile_sha256': digest((context/'Dockerfile').read_bytes()),
        'runtime_lock_sha256': digest(lock.read_bytes()),
        'python_pins': re.findall(r'^([A-Za-z0-9_-]+==[^\s;]+)', lock.read_text(), re.MULTILINE),
        'base_image': (context/'Dockerfile').read_text().splitlines()[0],
        'source_environment_variable_names': sorted(env_names),
        'environment_values_not_collected': True,
        'secret_reference': '/run/operator-provided/screener_session -> /run/secrets/screener_session',
        'os_packages': 'exact built versions retained in sbom.json; apt indexes are not snapshot-pinned',
        'rebuild_limit': 'no claim of byte-identical future builds from moving apt repositories',
    })
    manifest = {
        'status': 'AWAITING_INDEPENDENT_REVIEW', 'production_activation': 'none',
        'release_id': RELEASE_ID, 'source_commit': COMMIT, 'source_tree': source['tree'],
        'source_manifest_sha256': digest((OUT/'source-manifest.json').read_bytes()),
        'source_projection_sha256': source['manifest_sha256'], 'image_reference': reference,
        **image, 'platform': 'linux/arm64', 'archive': str(ARCHIVE),
        'archive_bytes': ARCHIVE.stat().st_size,
        'archive_sha256': None,
        'build_source_date_epoch': 1791345462,
        'reviewed_c28_integration': 'docs/cleanup/baselines/C28.1/verification.json',
        'excluded_ar02_patch': 'docs/acquisition-readiness/chunks/AR-02.1.md',
    }
    h = hashlib.sha256()
    with ARCHIVE.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''):
            h.update(block)
    manifest['archive_sha256'] = h.hexdigest()
    for kind, documents in [('core', '/data/snapshots'), ('documents', '/documents')]:
        identity = {'authorized': False, 'environment': 'production', 'git_commit': COMMIT,
                    'image_id': image['platform_manifest_digest'], 'release_id': RELEASE_ID,
                    'targets': {'db': '/data/scraper.db', 'store': '/data', 'documents': documents}}
        (RELEASE/f'runtime-identity-{kind}.json').write_text(json.dumps(identity, indent=2)+'\n')
    for name, target, role in [('store', '/data', 'store'), ('core-documents', '/data/snapshots', 'documents'),
                               ('external-documents', '/documents', 'documents')]:
        (RELEASE/f'{name}-sentinel.json').write_text(json.dumps({
            'environment': 'production', 'path': target, 'release_id': RELEASE_ID, 'roles': [role],
        }, indent=2)+'\n')
    config = read('/private/tmp/ar012-compose.json')
    browser = read('/private/tmp/ar012-browser-compose.json')
    config['services'].update(browser['services'])
    config['name'] = 'trading-ai'
    config['volumes']['scraper-data']['name'] = 'trading-ai_scraper-data'
    config['volumes']['scraper-data']['external'] = True
    for name, item in config['configs'].items():
        item['file'] = str(RELEASE / Path(item['file']).name)
    inventory = {}
    for name, service in config['services'].items():
        service['image'] = reference
        service['profiles'] = ['authorized-'+name]
        service['restart'] = 'no'
        service['cpus'] = min(float(service.get('cpus', 0.5)), 0.6)
        service['mem_limit'] = service.get('mem_limit', 1610612736)
        service['stop_grace_period'] = '180s'
        # Generic SQLite liveness must not create an empty DB or imply role health.
        service['healthcheck'] = {'test': ['CMD', 'python', '-c',
            "import sqlite3; c=sqlite3.connect('file:/data/scraper.db?mode=ro',uri=True); "
            "c.execute('PRAGMA query_only=ON'); c.execute('SELECT 1'); c.close()"],
            'interval': '30s', 'timeout': '5s', 'retries': 3}
        inventory[name] = {key: service[key] for key in (
            'command', 'entrypoint', 'environment', 'volumes', 'configs', 'secrets',
            'profiles', 'restart', 'cpus', 'mem_limit', 'stop_grace_period', 'healthcheck') if key in service}
    (RELEASE/'compose.candidate.json').write_text(json.dumps(config, indent=2)+'\n')
    write('config-mount-map.json', {
        'roles': inventory, 'secrets': config['secrets'], 'configs': config['configs'],
        'volumes': config['volumes'], 'no_default_services': True,
        'secret_values_read': False, 'identities_authorized': False,
        'capacity_reserve_bytes': 107374182400, 'capacity_reserve_percent': 15,
        'capacity_file_required': True,
        'writable_paths': ['/data/{scraper.db,scraper.db-wal,scraper.db-shm,scraper.db.migrate.lock}',
                           '/data/{files,snapshots,exports,campaigns,logs,browser-state,backups}',
                           '/documents (acquisition roles only)', '/tmp (container ephemeral)'],
        'unbounded_startup_roles_held': ['scheduler', 'daemon', 'market-cap-monitor',
            'artifact-reconciler', 'rating-recovery-discovery', 'document-processing-worker'],
    })
    prior = read('/private/tmp/ar012-prior-image.json')[0]
    old = read('/private/tmp/ar012-container-inspect.json')
    write('prior-image-identity.json', {
        'container_name': old['Name'], 'state': old['State']['Status'], 'container_Image': old['Image'],
        'docker_inspect_Id': prior['Id'], 'RepoDigests': prior['RepoDigests'],
        'manifest_identity': read(ROOT/'ops/docker/release/c05.3/release-manifest.json')['release'],
        'interpretation': 'Docker containerd reports OCI index as .Image/.Id; platform and config are separate digests',
        'rollback_compatibility': 'C05.3 retained as evidence, never restart as acquisition rollback',
        'mounts': old['Mounts'],
    })
    (RELEASE/'release-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'image': reference, 'roles': len(inventory), 'archive_sha256': h.hexdigest()}))
