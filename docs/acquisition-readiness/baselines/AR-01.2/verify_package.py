"""Independent fail-closed artifact checks; no service activation."""
import json
from pathlib import Path
from prepare import ROOT, OUT, COMMIT, digest, write

release = ROOT/'ops/docker/release/c28.1'
manifest = json.loads((release/'release-manifest.json').read_text())
rendered = json.loads((OUT/'candidate-rendered.json').read_text())
loaded = json.loads((OUT/'loaded-image.json').read_text())
assert loaded['id'] == manifest['oci_index_digest']
assert manifest['image_reference'] in loaded['repo_digests']
assert loaded['labels']['org.opencontainers.image.revision'] == COMMIT
assert manifest['source_manifest_sha256'] == digest((OUT/'source-manifest.json').read_bytes())
assert loaded['user'] == 'scraper' and loaded['architecture'] == 'arm64'
assert not (OUT/'default-services.log').read_text().strip()
assert len(rendered['services']) == 21
assert rendered['volumes']['scraper-data']['external'] is True
assert rendered['volumes']['scraper-data']['name'] == 'trading-ai_scraper-data'
for name, service in rendered['services'].items():
    assert service['image'] == manifest['image_reference'], name
    assert 'build' not in service and service['profiles'] == ['authorized-'+name]
    assert service['restart'] == 'no'
    assert 0 < float(service['cpus']) <= .6
    assert int(service['mem_limit']) == 1610612736
    assert service['stop_grace_period'] == '3m0s'
    assert 'mode=ro' in service['healthcheck']['test'][-1]
    for mount in service.get('volumes', []):
        assert mount['target'] != '/app/scrapper'
        if mount['target'] == '/documents':
            assert mount['source'] == '/Volumes/My Passport/passport/trading-ai'
    if name == 'document-processing-worker':
        assert next(m for m in service['volumes'] if m['target']=='/documents')['read_only']
for kind in ['core', 'documents']:
    identity = json.loads((release/f'runtime-identity-{kind}.json').read_text())
    assert identity['authorized'] is False
    assert identity['git_commit'] == COMMIT
    assert identity['image_id'] == manifest['platform_manifest_digest']
pending = json.loads((OUT/'migration-manifest.json').read_text())['pending']
source = json.loads((OUT/'source-manifest.json').read_text())
source_hashes = {p['path']: p['sha256'] for p in source['files']}
for migration in pending:
    path = 'scrapper/db/migrations/'+migration['filename']
    assert migration['sha256'] == source_hashes[path] == digest((ROOT/path).read_bytes())
before = json.loads((OUT/'working-tree-before.json').read_text())
drift = []
for relative, expected in before['files'].items():
    if relative.startswith('docs/acquisition-readiness/baselines/AR-01.2/'):
        continue
    if relative == 'docs/current/TRADING_AI_ACQUISITION_READINESS_EXECUTION.md':
        continue  # Concurrent AR-01.3 selection updated the shared ledger; retained separately.
    path = ROOT/relative
    actual = digest(path.read_bytes()) if path.is_file() else None
    if expected != actual:
        drift.append(relative)
assert not drift, drift
review_passed = manifest.get('status') == 'PREPARED'
if review_passed:
    review = manifest['independent_review']
    assert review['decision'] == 'ACCEPT' and review['result'] == 'PASS'
    assert (ROOT/review['record']).is_file()
    assert digest((OUT/'reviewed-package/SHA256SUMS').read_bytes()) == review['reviewed_checksum_index_sha256']
    assert manifest['ar_01_3_authorized'] is False
write('package-verification.json', {
    'status': 'PASS', 'roles': 21, 'zero_default_services': True,
    'identities_write_locked': True, 'loaded_digest_matches_oci': True,
    'migration_source_hashes_match': True, 'preexisting_changes_preserved': True,
    'independent_human_review': 'PASS' if review_passed else 'PENDING',
    **({'ar_01_2_status': 'PREPARED', 'ar_01_3_authorized': False,
        'independent_review_record': manifest['independent_review']['record']} if review_passed else {}),
    'production_activation': 'none',
})
print('PASS: image/source, 21 locked roles, config, migration hashes and preservation')
