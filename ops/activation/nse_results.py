"""Offline check by default. --execute runs the bounded NSE operation once."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RELEASE = ROOT / 'ops/docker/release/ar02-period-20261008'
BASELINE_RELEASE = ROOT / 'ops/docker/release/ar02-combined-20261008'
SOURCE = ROOT / 'docs/acquisition-readiness/baselines/AR-02-period-20261008/source-manifest.json'
REVIEW = ROOT / 'docs/acquisition-readiness/reviews/AR-02-combined-20261008-independent'
IMAGE = 'trading-ai-ar02-period-release@sha256:a7025c3e70ea14939874aa2c1337a41845e3311ba9b5a4fc1a1004c6d6f4c16d'
VOLUME = 'trading-ai_scraper-data'
sys.path.insert(0, str(ROOT))
from scrapper.sources.exchange.reporting_periods import priority_result_periods


def require_packaged_period_logic():
    source = SOURCE
    manifest = json.loads((RELEASE / 'release-manifest.json').read_text())
    require(hashlib.sha256(source.read_bytes()).hexdigest() == manifest['source_manifest_sha256'],
            'Accepted source manifest changed')
    files = {row['path']: row['sha256'] for row in json.loads(source.read_text())['files']}
    paths = ('scrapper/sources/exchange/reporting_periods.py',
             'scrapper/sources/exchange/result_filings.py',
             'scrapper/workflows/exchange/schedule.py')
    scheduling=json.loads((HERE/'automatic-acquisition-manifest.json').read_text())
    for name in paths:
        actual=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        if name=='scrapper/sources/exchange/result_filings.py' and actual!=files.get(name):
            require(scheduling.get('application_overlay_base_hashes',{}).get(name)==files.get(name)
                and scheduling['files'].get(name)==actual,
                'Phase 3 BSE period policy overlay identity differs from its sealed base or tested hash')
        else:
            require(files.get(name)==actual,'Date-driven application source differs from accepted image; package and verify a replacement release: '+name)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def checked(args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


def check_configuration():
    from postgresql_release import target,verify
    native=target()
    if native:
        verify()
        manifest=json.loads((HERE/'automatic-acquisition-manifest.json').read_text())
        return {**manifest,'oci_index_digest':native['image'],'source_base_commit':native['source_revision']}
    # Verify the retained sign-off's integrity; do not repeat its technical review.
    for line in (REVIEW / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        require(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, 'Review record changed: ' + name)
    manifest = json.loads((RELEASE / 'release-manifest.json').read_text())
    baseline = json.loads((BASELINE_RELEASE / 'release-manifest.json').read_text())
    review = json.loads((REVIEW / 'review.json').read_text())
    require(review['decision'] == 'ACCEPT' and review['image_reference'] == baseline['image_reference']
            and manifest['previous_accepted_image'] == baseline['image_reference']
            and manifest['image_reference'] == IMAGE
            and manifest['package_verification'] == 'PASS', 'Accepted release identity mismatch')
    require(review['archive_sha256'] == baseline['archive_sha256'], 'Reviewed archive identity mismatch')
    index = ROOT / 'docs/acquisition-readiness/baselines/AR-02-combined-20261008/SHA256SUMS'
    require(hashlib.sha256(index.read_bytes()).hexdigest() == review['package_checksum_index_sha256'], 'Package index changed')
    for line in (RELEASE / 'SHA256SUMS').read_text().splitlines():
        expected_hash, name = line.split('  ', 1)
        # The application image and sealed release stay unchanged. These three
        # read-only host/operator files are superseded by the scheduling phase;
        # verify both their original seal identity and the new phase's hash.
        replacements = ('ops/activation/nse_results.py',
                        'ops/activation/nse_results_runtime.py',
                        'ops/activation/test_nse_results.py',
                        'ops/activation/production-baseline.json')
        if name in replacements:
            scheduling = json.loads((HERE / 'automatic-acquisition-manifest.json').read_text())
            require(scheduling['superseded_operator_hashes'][name] == expected_hash,
                    'Original operator seal identity changed: ' + name)
            expected_hash = scheduling['files'][name]
        require(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected_hash,
                'Replacement configuration/source evidence changed: ' + name)
    require_packaged_period_logic()
    identity = json.loads((HERE / 'runtime-identity-nse-results.json').read_text())
    expected = json.loads((RELEASE / 'runtime-identity-core.json').read_text())
    expected['authorized'] = True
    require(identity == expected, 'Only the selected runtime identity may be unlocked')
    compose = json.loads((HERE / 'compose.nse.json').read_text())
    require(list(compose['services']) == ['nse-results'], 'Unrelated services enabled')
    service = compose['services']['nse-results']
    require(service['image'] == IMAGE and service['restart'] == 'no'
            and service['profiles'] == ['nse'], 'Activation role configuration mismatch')
    require(json.loads((RELEASE / 'runtime-identity-core.json').read_text())['authorized'] is False
            and json.loads((RELEASE / 'runtime-identity-documents.json').read_text())['authorized'] is False,
            'Sealed release roles must remain locked')
    return manifest


def owner_gate():
    from production_ownership import owner_gate as verified_gate
    return verified_gate()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--period', help='Optional YYYY-MM quarter override for testing/recovery')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--preflight', action='store_true', help='Verify production guards without source requests or ingestion')
    args = parser.parse_args()
    out = None
    try:
        periods = priority_result_periods(override=args.period)
        manifest = check_configuration()
        if not args.execute:
            print(json.dumps({'offline_configuration': 'PASS', 'baseline_review': 'ACCEPT', 'replacement_verification': 'PASS', 'image': IMAGE,
                              'production_access': False, 'enabled_role': 'nse-results',
                              'priority_periods': periods, 'replacement_package_required': False}))
            return 0
        require_packaged_period_logic()
        with (HERE / '.nse-owner.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            out = ROOT / 'artifacts/acquisition-readiness/operations' / datetime.now(timezone.utc).strftime('nse-%Y%m%dT%H%M%S%fZ')
            out.mkdir(parents=True)
            image = json.loads(checked(['docker', 'image', 'inspect', manifest['oci_index_digest']]))[0]
            from postgresql_release import target
            native=target()
            if native:
                require(image['Id']==native['image'] and image['Os']=='linux'
                    and image['Architecture']=='arm64', 'Native application image mismatch')
            else:
                labels = image['Config']['Labels']
                require(image['Id'] == manifest['oci_index_digest'] and image['Os'] == 'linux'
                        and image['Architecture'] == 'arm64'
                        and labels['org.opencontainers.image.revision'] == manifest['source_base_commit']
                        and labels['ai.trading.source.projection.sha256'] == manifest['source_projection_sha256']
                        and labels['ai.trading.source.patch.sha256'] == manifest['source_patch_sha256'], 'Loaded image mismatch')
            volume = json.loads(checked(['docker', 'volume', 'inspect', VOLUME]))[0]
            require(volume['Name'] == VOLUME and volume['Labels'].get('com.docker.compose.project') == 'trading-ai',
                    'Existing production volume mismatch')
            gate = owner_gate()
            (out / 'before.json').write_text(json.dumps({'owner_gate': gate, 'image': IMAGE, 'period_override': args.period, 'priority_periods': periods,
                                                       'max_new_queue_entries': 100, 'max_snapshot_rows': 6000, 'max_total_snapshot_rows': 12000}, indent=2) + '\n')
            # Resolve the already verified content ID; never pull/rebuild an alias.
            override = out / 'compose.image.json'
            overlays = ('scrapper/db/exchange/filings.py', 'scrapper/workflows/exchange/universe.py',
                        'scrapper/db/migrations/076_retire_crisil_icra_terms_holds.sql')
            scheduled = json.loads((HERE / 'automatic-acquisition-manifest.json').read_text())
            for name in overlays:
                require(hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == scheduled['files'][name],
                        'Bounded intake overlay drift: ' + name)
            override.write_text(json.dumps({'services': {'nse-results': {'image': image['Id'],
                'volumes': [{'type': 'bind', 'source': str(ROOT/name), 'target': '/app/'+name,
                    'read_only': True, 'bind': {'create_host_path': False}} for name in overlays]}}}))
            if native:
                from postgresql_release import configure
                compose=json.loads((HERE/'compose.nse.json').read_text())
                service=compose['services']['nse-results']
                for mount in service['volumes']:
                    if mount['type']=='bind':mount['source']=str(HERE)
                native_identity=out/'native-identity.json'
                native_identity.write_text((HERE/'runtime-identity-nse-results.json').read_text())
                configure(compose,service,'exchange',native_identity)
                compose['configs']['nse_identity']['file']=str(native_identity)
                for key in ('nse_store','nse_snapshots'):
                    compose['configs'][key]['file']=str((HERE/compose['configs'][key]['file']).resolve())
                (out/'compose.native.json').write_text(json.dumps(compose,indent=2)+'\n')
                command=['docker','compose','-f',str(out/'compose.native.json'),'--profile','nse',
                    'run','--rm','--no-deps','--name','trading-ai-nse-results','nse-results']
            else:
                command = ['docker', 'compose', '-f', str(HERE / 'compose.nse.json'), '-f', str(override), '--profile', 'nse',
                           'run', '--rm', '--no-deps', '--name', 'trading-ai-nse-results', 'nse-results']
            if args.period is not None:
                command.extend(['--period', args.period])
            if args.preflight:
                command.append('--verify-only')
            with (out / 'runtime.log').open('w') as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
            (out / 'outcome.json').write_text(json.dumps({'exit_code': result.returncode,
                'status': 'PASS' if result.returncode == 0 else 'DEFERRED_ENVIRONMENT' if result.returncode == 75 else 'STOP', 'automatic_retry': False,
                'owner_after': owner_gate()}, indent=2) + '\n')
            print((out / 'runtime.log').read_text())
            print('Outcome retained: ' + str(out))
            return result.returncode
    except Exception as exc:
        if out is not None:
            (out / 'stop.json').write_text(json.dumps({'status': 'STOP', 'error': str(exc),
                'automatic_retry': False}, indent=2) + '\n')
        print('STOP: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
