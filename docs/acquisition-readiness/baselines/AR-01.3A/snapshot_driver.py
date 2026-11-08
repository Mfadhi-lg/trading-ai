"""AR-01.3A only: source RO, snapshot and disposable restoration; no migrations."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
TARGET = ROOT / 'artifacts/acquisition-readiness/AR-01.3/pre-migration-068'
spec = importlib.util.spec_from_file_location('host_gate', ROOT / 'ops/docker/canary/ar013_readonly_host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def log(stage, **values):
    line = json.dumps({'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'stage': stage, **values})
    print(line, flush=True)
    with (OUT / 'driver.log').open('a') as f:
        f.write(line + '\n')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def base_command():
    return ['docker', 'run', '--rm', '--pull', 'never', '--no-healthcheck', '--network', 'none',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--cpus', '1', '--memory', '1536m', '--user', f'{os.getuid()}:{os.getgid()}',
            '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m']


def copy_source(stage, verify_only=False):
    gate = host.writer_gate()
    gate['copy_script_sha256'] = sha(OUT / 'copy_source.py')
    save('snapshot-gate.json', gate)
    save(stage + '-writer-before.json', gate)
    command = base_command() + [
        '--mount', 'type=volume,src=trading-ai_scraper-data,dst=/source,readonly',
        '--mount', f'type=bind,src={OUT},dst=/proof,readonly',
    ]
    if not verify_only:
        command += ['--mount', f'type=bind,src={TARGET},dst=/snapshot']
    command += ['--entrypoint', 'python', host.IMAGE, '/proof/copy_source.py']
    if verify_only:
        command += ['--verify-only']
    save(stage + '-command.json', command)
    log(stage + '_start')
    r = subprocess.run(command, capture_output=True, text=True)
    (OUT / (stage + '-stdout.log')).write_text(r.stdout)
    (OUT / (stage + '-stderr.log')).write_text(r.stderr)
    if r.returncode or r.stderr:
        raise RuntimeError(stage + ' failed; stop without retry or production recovery: ' + r.stderr)
    result = json.loads(r.stdout)
    save(stage + '.json', result)
    save(stage + '-writer-after.json', host.writer_gate())
    log(stage + '_PASS', sha256=result['source_sha256_after'])
    return result


def validate(path, name, expected):
    evidence = OUT / name
    evidence.mkdir()
    command = base_command() + [
        '--mount', f'type=bind,src={path},dst=/copy,readonly',
        '--mount', f'type=bind,src={OUT},dst=/proof,readonly',
        '--mount', f'type=bind,src={evidence},dst=/evidence',
        '--entrypoint', 'python', host.IMAGE, '/proof/validate_copy.py', expected,
    ]
    save(name + '-command.json', command)
    log(name + '_start')
    with (evidence / 'stdout.log').open('w') as stdout, (evidence / 'stderr.log').open('w') as stderr:
        r = subprocess.run(command, stdout=stdout, stderr=stderr)
    if r.returncode or (evidence / 'stderr.log').stat().st_size:
        raise RuntimeError(name + ' failed; retain copies and do not retry or recover production')
    log(name + '_PASS')


def main():
    if (OUT / 'final.json').exists() or TARGET.exists():
        raise RuntimeError('Evidence/snapshot destination exists; refuse overwrite/retry')
    result = {'status': 'FAIL', 'recovery_authority': 'USER', 'execution_agent': 'CODEX',
              'production_migrations': 0, 'production_writes': 0, 'services_started': 0}
    try:
        log('START_AR013A_ONLY')
        state = os.statvfs(ROOT)
        if state.f_bavail * state.f_frsize < 60 * 1024**3 or state.f_bavail / state.f_blocks < 0.15:
            raise RuntimeError('Snapshot destination lacks reviewed storage reserve')
        save('destination-capacity.json', {'free_bytes': state.f_bavail * state.f_frsize,
                                          'free_percent': 100 * state.f_bavail / state.f_blocks})
        host.OUT = OUT / 'fresh-canary'
        if host.main() != 0:
            raise RuntimeError('Fresh schema/writer canary failed')
        baseline = json.loads((host.OUT / 'result.json').read_text())['probe']
        old_schema = json.loads((ROOT / 'docs/acquisition-readiness/baselines/AR-01.1/schema-before.json').read_text())
        TARGET.mkdir(parents=True)
        save('snapshot-destination.json', {'raw': str(TARGET / 'raw/scraper.db'),
             'validation': str(TARGET / 'validation/scraper.db'), 'mode': 'new destination; no overwrite'})
        copied = copy_source('source-snapshot')
        expected = copied['source_sha256_before']
        if any(copied['source_before'][key] != baseline['db_before'][key]
               for key in ('bytes', 'mtime_ns', 'ctime_ns', 'device', 'inode')):
            raise RuntimeError('Source changed after fresh schema preflight')
        validation_dir = TARGET / 'validation'
        validation_dir.mkdir()
        validation = validation_dir / 'scraper.db'
        shutil.copyfile(TARGET / 'raw/scraper.db', validation)
        validation.chmod(0o444)
        if sha(validation) != expected:
            raise RuntimeError('Validation copy hash mismatch')
        validate(validation_dir, 'snapshot-validation', expected)
        schema = json.loads((OUT / 'snapshot-validation/schema.json').read_text())
        if schema != old_schema:
            raise RuntimeError('Fresh snapshot schema differs from reviewed 068 DDL')
        fk = json.loads((OUT / 'snapshot-validation/foreign-keys.json').read_text())
        historical = json.loads((ROOT / 'docs/acquisition-readiness/baselines/AR-01.1/foreign-keys-before.json').read_text())
        if Counter(map(tuple, fk)) != Counter(map(tuple, historical)):
            raise RuntimeError('Fresh FK identities differ from reviewed historical baseline')
        restore_dir = Path(tempfile.mkdtemp(prefix='trading-ai-ar013a-restore-', dir='/private/tmp'))
        restored = restore_dir / 'scraper.db'
        # Recreate only a disposable target from the verified retained snapshot.
        # The original production volume is not mounted in either validation.
        shutil.copyfile(TARGET / 'raw/scraper.db', restored)
        restored.chmod(0o444)
        if sha(restored) != expected:
            raise RuntimeError('Disposable recreation hash mismatch')
        save('disposable-restore-identity.json', {'path': str(restored), 'sha256': expected,
            'bytes': restored.stat().st_size, 'inode': restored.stat().st_ino,
            'production_restore': False})
        validate(restore_dir, 'restore-validation', expected)
        for name in ('schema.json', 'ledger.json', 'rows.json'):
            if json.loads((OUT / 'snapshot-validation' / name).read_text()) != json.loads((OUT / 'restore-validation' / name).read_text()):
                raise RuntimeError('Disposable restored manifest mismatch: ' + name)
        restored_fk = json.loads((OUT / 'restore-validation/foreign-keys.json').read_text())
        if Counter(map(tuple, fk)) != Counter(map(tuple, restored_fk)):
            raise RuntimeError('Disposable restored FK identity multiset mismatch')
        final_source = copy_source('source-final-verification', verify_only=True)
        if final_source['source_sha256_after'] != expected or final_source['source_after'] != copied['source_before']:
            raise RuntimeError('Production main or sidecar state changed across proof window')
        rows = json.loads((OUT / 'snapshot-validation/rows.json').read_text())
        result.update({'status': 'PASS', 'source_sha256': expected, 'snapshot_sha256': expected,
            'restored_sha256': expected, 'snapshot_path': str(TARGET / 'raw/scraper.db'),
            'validation_path': str(validation), 'restored_path': str(restored),
            'schema': '068', 'pending_migrations': baseline['pending_migrations'],
            'table_count': len(rows), 'row_count': sum(v['rows'] for v in rows.values()),
            'fk_count': len(fk), 'fk_multiset_matches_historical_and_restored': True,
            'restored_manifests_match': True, 'production_source_unchanged': True})
        log('PASS_AR013A_ONLY', sha256=expected, tables=len(rows), fk_count=len(fk))
        return 0
    except Exception as exc:
        result['error'] = str(exc)
        log('STOP_NO_RETRY_NO_PRODUCTION_RESTORE', error=str(exc))
        return 2
    finally:
        save('final.json', result)
        print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    sys.exit(main())
