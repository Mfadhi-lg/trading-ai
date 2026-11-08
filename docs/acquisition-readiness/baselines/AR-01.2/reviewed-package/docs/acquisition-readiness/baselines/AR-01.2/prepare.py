"""Offline exact-commit release preparation; never starts a runtime."""
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
COMMIT = 'c3ddbbb9c298d07b8b46a362996251417282476a'
CONTEXT = Path('/private/tmp/trading-ai-ar012-context')

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')

if __name__ == '__main__':
    status = git('status', '--porcelain=v1', '-uall').decode()
    paths = git('ls-files', '-m', '-o', '--exclude-standard', '-z').decode().split('\0')
    write('working-tree-before.json', {
        'head': git('rev-parse', 'HEAD').decode().strip(),
        'tree': git('rev-parse', 'HEAD^{tree}').decode().strip(),
        'status': status,
        'files': {p: digest((ROOT/p).read_bytes()) if (ROOT/p).is_file() else None
                  for p in paths if p},
    })
    CONTEXT.mkdir(exist_ok=False)
    archive = git('archive', COMMIT, 'Dockerfile', '.dockerignore', 'scrapper')
    records = []
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source:
            if member.isdir():
                continue
            assert member.isfile(), member.name
            assert not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
            data = source.extractfile(member).read()
            path = CONTEXT / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(member.mode)
            records.append({'path': member.name, 'sha256': digest(data), 'mode': member.mode})
    encoded = json.dumps(records, sort_keys=True, separators=(',', ':')).encode()
    write('source-manifest.json', {
        'commit': COMMIT, 'tree': git('rev-parse', COMMIT+'^{tree}').decode().strip(),
        'context': str(CONTEXT), 'files': records, 'manifest_sha256': digest(encoded),
        'source_date_epoch': int(git('show', '-s', '--format=%ct', COMMIT)),
        'excluded_working_tree_changes': ['scrapper/workflows/exchange/universe.py',
                                         'scrapper/tests/test_ar02_snapshot_dates.py'],
    })
    migrations = sorted((CONTEXT/'scrapper/db/migrations').glob('*.sql'))
    pending = [p for p in migrations if '069' <= p.name[:3] <= '075']
    assert len(pending) == 7
    write('migration-manifest.json', {
        'baseline': '068_exchange_source_access_compatibility.sql',
        'pending': [{'filename': p.name, 'sha256': digest(p.read_bytes())} for p in pending],
        'reviewed_effects': 'docs/acquisition-readiness/baselines/AR-01.1/migration-effect-manifest.json',
        'recovery': 'quiescent pre-write snapshot restore only; no blind partial-DDL retry',
        'baseline_foreign_key_violations': 10771,
    })
    print(json.dumps({'context': str(CONTEXT), 'files': len(records), 'source_sha256': digest(encoded)}))
