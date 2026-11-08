"""Verify new OCI export and produce a separate, locked review package."""
import importlib.util
import json
import tarfile
from prepare import ROOT, OUT, ARCHIVE, RELEASE, COMMIT, RELEASE_ID, digest, write

spec = importlib.util.spec_from_file_location('oci', ROOT/'scripts/audit/check_oci_release.py')
oci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oci)
image = oci.inspect(ARCHIVE)
write('oci-verification.json', image)
with tarfile.open(ARCHIVE) as archive:
    config = json.loads(oci.load_blob(archive, image['config_digest']))
    write('oci-config.json', config)
    attestation = json.loads(oci.load_blob(archive, image['attestation_manifest_digest']))
    for layer in attestation['layers']:
        kind = layer.get('annotations', {}).get('in-toto.io/predicate-type')
        name = {'https://spdx.dev/Document': 'sbom.json', 'https://slsa.dev/provenance/v1': 'provenance.json'}.get(kind)
        if name:
            (OUT/name).write_bytes(oci.load_blob(archive, layer['digest']))
source = json.loads((OUT/'source-manifest.json').read_text())
labels = config['config']['Labels']
assert labels['org.opencontainers.image.revision'] == COMMIT
assert labels['org.opencontainers.image.version'] == RELEASE_ID
assert labels['ai.trading.source.projection.sha256'] == source['source_projection_sha256']
assert labels['ai.trading.source.patch.sha256'] == source['patch_sha256']
assert config['architecture'] == 'arm64' and config['os'] == 'linux'
assert config['config']['User'] == 'scraper'
reference = 'trading-ai-ar021-release@' + image['oci_index_digest']
import hashlib
h = hashlib.sha256()
with ARCHIVE.open('rb') as stream:
    for block in iter(lambda: stream.read(8*1024*1024), b''):
        h.update(block)
prior = json.loads((ROOT/'ops/docker/release/c28.1/release-manifest.json').read_text())
manifest = {'status': 'PREPARED', 'package_verification': 'PENDING',
    'independent_review': 'PENDING_FOR_NEW_DIGEST', 'production_activation': 'NOT_STARTED',
    'production_activation_authorized': False, 'release_id': RELEASE_ID,
    'source_base_commit': COMMIT, 'source_base_tree': source['base_tree'],
    'source_identity': source['source_identity'], 'source_patch_sha256': source['patch_sha256'],
    'source_manifest_sha256': digest((OUT/'source-manifest.json').read_bytes()),
    'source_projection_sha256': source['source_projection_sha256'],
    'image_reference': reference, **image, 'platform': 'linux/arm64',
    'archive': str(ARCHIVE.relative_to(ROOT)), 'archive_bytes': ARCHIVE.stat().st_size,
    'archive_sha256': h.hexdigest(), 'build_source_date_epoch': source['source_date_epoch'],
    'previous_accepted_image': prior['image_reference'],
    'reuse_record': str((OUT/'reused-evidence.json').relative_to(ROOT)),
    'rollback': 'Leave production stopped on schema075; retained prior image is compatible but lacks the patch. No restore or migration replay.',
    'rebuild_limit': 'OS repositories are not snapshot pinned; this exported payload is authoritative.'}
(RELEASE/'release-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
for name in ['runtime-identity-core.json', 'runtime-identity-documents.json',
             'store-sentinel.json', 'core-documents-sentinel.json', 'external-documents-sentinel.json']:
    value = json.loads((ROOT/'ops/docker/release/c28.1'/name).read_text())
    value['release_id'] = RELEASE_ID
    if name.startswith('runtime-identity'):
        value.update(authorized=False, image_id=image['platform_manifest_digest'],
            source_projection_sha256=source['source_projection_sha256'], source_patch_sha256=source['patch_sha256'])
    (RELEASE/name).write_text(json.dumps(value, indent=2)+'\n')
candidate = json.loads((ROOT/'ops/docker/release/c28.1/compose.candidate.json').read_text())
for config_item in candidate['configs'].values():
    config_item['file'] = str(RELEASE / __import__('pathlib').Path(config_item['file']).name)
for service in candidate['services'].values():
    service['image'] = reference
(RELEASE/'compose.candidate.json').write_text(json.dumps(candidate, indent=2)+'\n')
write('layer-reuse.json', {'previous_image': prior['image_reference'],
    'unchanged_layer_digests': [v for v in image['layers'] if v in prior['layers']],
    'new_layer_digests': [v for v in image['layers'] if v not in prior['layers']],
    'dependency_lock_and_dockerfile_unchanged': True,
    'new_sbom_retained': True, 'no_byte_identical_dependency_layers_claimed': True})
print(json.dumps({'status': 'PASS', 'image': reference, 'archive_sha256': h.hexdigest()}))
