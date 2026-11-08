"""Continue copied-snapshot validation on isolated native disposable storage."""
import importlib.util
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('snapshot', OUT / 'snapshot_driver.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
EVIDENCE = OUT / 'native-proof'
SNAPSHOT_VOLUME = 'trading-ai-ar013a-snapshot-validation-20261007'
RESTORE_VOLUME = 'trading-ai-ar013a-restore-proof-20261007'


def save(name, value):
    (EVIDENCE / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def logged(command, name):
    save(name + '-command.json', command)
    driver.log(name + '_start')
    with (EVIDENCE / (name + '-stdout.log')).open('w') as stdout, (EVIDENCE / (name + '-stderr.log')).open('w') as stderr:
        r = subprocess.run(command, stdout=stdout, stderr=stderr)
    if r.returncode or (EVIDENCE / (name + '-stderr.log')).stat().st_size:
        raise RuntimeError(name + ' failed; preserve state and stop')
    driver.log(name + '_PASS')


def seed(volume, name, expected):
    # Root owns the new empty native volume; no production volume is mounted.
    command = driver.base_command()
    index = command.index('--user')
    command[index + 1] = '0:0'
    command += ['--mount', f'type=bind,src={driver.TARGET / "raw"},dst=/input,readonly',
                '--mount', f'type=volume,src={volume},dst=/output',
                '--mount', f'type=bind,src={OUT},dst=/proof,readonly',
                '--entrypoint', 'python', driver.host.IMAGE, '/proof/copy_disposable.py', expected]
    logged(command, name)
    result = json.loads((EVIDENCE / (name + '-stdout.log')).read_text())
    save(name + '.json', result)


def validate(volume, name, expected):
    evidence = EVIDENCE / name
    evidence.mkdir()
    command = driver.base_command() + [
        '--mount', f'type=volume,src={volume},dst=/copy,readonly',
        '--mount', f'type=bind,src={OUT},dst=/proof,readonly',
        '--mount', f'type=bind,src={evidence},dst=/evidence',
        '--entrypoint', 'python', driver.host.IMAGE, '/proof/validate_copy.py', expected]
    save(name + '-command.json', command)
    driver.log(name + '_start')
    with (evidence / 'stdout.log').open('w') as stdout, (evidence / 'stderr.log').open('w') as stderr:
        r = subprocess.run(command, stdout=stdout, stderr=stderr)
    if r.returncode or (evidence / 'stderr.log').stat().st_size:
        raise RuntimeError(name + ' failed; preserve state and stop')
    driver.log(name + '_PASS')


def main():
    if EVIDENCE.exists():
        raise RuntimeError('Native proof already exists; refuse overwrite/retry')
    EVIDENCE.mkdir()
    result = {'status': 'FAIL', 'recovery_authority': 'USER', 'execution_agent': 'CODEX',
              'production_migrations': 0, 'production_writes': 0, 'production_restore': False}
    try:
        save('writer-before.json', driver.host.writer_gate())
        copy = json.loads((OUT / 'source-snapshot.json').read_text())
        if copy['status'] != 'PASS':
            raise RuntimeError('Original raw snapshot did not pass copy/hash gate')
        expected = copy['snapshot_sha256']
        names = driver.host.checked(['docker', 'volume', 'ls', '--format', '{{.Name}}']).splitlines()
        if SNAPSHOT_VOLUME in names or RESTORE_VOLUME in names:
            raise RuntimeError('Disposable volume name collision')
        for volume in (SNAPSHOT_VOLUME, RESTORE_VOLUME):
            driver.host.checked(['docker', 'volume', 'create', '--label', 'org.trading-ai.disposable-proof=AR-01.3A', volume])
        save('volumes.json', {'snapshot_validation': SNAPSHOT_VOLUME, 'restore_proof': RESTORE_VOLUME,
                             'production_volume': 'not mounted by native validators or copy seeding'})
        seed(SNAPSHOT_VOLUME, 'seed-snapshot', expected)
        validate(SNAPSHOT_VOLUME, 'snapshot-validation', expected)
        schema = json.loads((EVIDENCE / 'snapshot-validation/schema.json').read_text())
        old_schema = json.loads((driver.ROOT / 'docs/acquisition-readiness/baselines/AR-01.1/schema-before.json').read_text())
        if schema != old_schema:
            raise RuntimeError('Fresh copied schema differs from reviewed 068 baseline')
        fk = json.loads((EVIDENCE / 'snapshot-validation/foreign-keys.json').read_text())
        historical = json.loads((driver.ROOT / 'docs/acquisition-readiness/baselines/AR-01.1/foreign-keys-before.json').read_text())
        if Counter(map(tuple, fk)) != Counter(map(tuple, historical)):
            raise RuntimeError('Fresh FK identity multiset differs from historical baseline')
        # Recreate into a second independent disposable target only after the
        # snapshot validation passes. Never replace any production file.
        seed(RESTORE_VOLUME, 'seed-restored', expected)
        validate(RESTORE_VOLUME, 'restore-validation', expected)
        for name in ('schema.json', 'ledger.json', 'rows.json', 'foreign-keys.json'):
            if json.loads((EVIDENCE / 'snapshot-validation' / name).read_text()) != json.loads((EVIDENCE / 'restore-validation' / name).read_text()):
                raise RuntimeError('Restored manifest mismatch: ' + name)
        source = driver.copy_source('source-final-verification', verify_only=True)
        if source['source_sha256_after'] != expected or source['source_after'] != copy['source_before']:
            raise RuntimeError('Production source identity/hash/sidecars changed during proof window')
        rows = json.loads((EVIDENCE / 'snapshot-validation/rows.json').read_text())
        result.update({'status': 'PASS', 'source_sha256': expected, 'snapshot_sha256': expected,
            'restored_sha256': expected, 'snapshot_path': str(driver.TARGET / 'raw/scraper.db'),
            'snapshot_validation_volume': SNAPSHOT_VOLUME, 'restore_volume': RESTORE_VOLUME,
            'schema': '068', 'table_count': len(rows), 'row_count': sum(v['rows'] for v in rows.values()),
            'fk_count': len(fk), 'restored_manifests_match': True,
            'fk_multiset_matches_historical_and_restored': True, 'production_source_unchanged': True})
        driver.log('PASS_AR013A_NATIVE_PROOF', tables=len(rows), fk_count=len(fk), sha256=expected)
        return 0
    except Exception as exc:
        result['error'] = str(exc)
        driver.log('STOP_NO_RETRY_NO_PRODUCTION_RESTORE', error=str(exc))
        return 2
    finally:
        save('final.json', result)
        print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    sys.exit(main())
