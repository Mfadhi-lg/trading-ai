"""Reconstruct accepted combined source and add only period-selection inputs."""
import difflib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
CONTEXT = Path('/private/tmp/trading-ai-ar02-period-release-context-20261008')
COMMIT = 'c3ddbbb9c298d07b8b46a362996251417282476a'
RELEASE_ID = 'c28.1-ar02-period-20261008'
RELEASE = ROOT / 'ops/docker/release/ar02-period-20261008'
ARCHIVE = ROOT / 'artifacts/acquisition-readiness/AR-02-period-20261008/release.oci.tar'
BASE = ROOT / 'docs/acquisition-readiness/baselines/AR-02-combined-20261008'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def write(name, value):
    (OUT/name).write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')

if __name__ == '__main__':
    # Retained historical ledger resolves the sole mutable documentation path.
    verified = []
    for index in [BASE/'SHA256SUMS', ROOT/'docs/acquisition-readiness/reviews/AR-02-combined-20261008-independent/SHA256SUMS']:
        for line in index.read_text().splitlines():
            expected, name = line.split('  ', 1)
            path = ROOT/name
            if name == 'docs/current/TRADING_AI_ACQUISITION_READINESS_EXECUTION.md':
                path = ROOT/'docs/acquisition-readiness/archive/execution-v1.5-accepted-package.md'
            assert digest(path.read_bytes()) == expected, name
            verified.append({'path': str(path.relative_to(ROOT)), 'sha256': expected})
    write('reused-evidence.json', {'status': 'PASS', 'files': verified,
        'baseline': str(BASE.relative_to(ROOT)), 'schema075_evidence_reused': True,
        'migrations_repeated': False, 'recovery_rehearsals_repeated': False,
        'unrelated_tests_repeated': False})
    write('preservation-before.json', {str(p.relative_to(ROOT)): digest(p.read_bytes())
        for folder in [ROOT/'ops/docker/release/ar02-combined-20261008', BASE,
                       ROOT/'docs/acquisition-readiness/reviews/AR-02-combined-20261008-independent']
        for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    CONTEXT.mkdir(exist_ok=False)
    data = subprocess.check_output(['git', 'archive', COMMIT, 'Dockerfile', '.dockerignore', 'scrapper'], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            if not member.isfile():
                continue
            path = CONTEXT/member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(member).read())
            path.chmod(member.mode)
    subprocess.run(['git','apply', str(BASE/'combined.patch')], cwd=CONTEXT, check=True)
    base = json.loads((BASE/'source-manifest.json').read_text())
    for row in base['files']:
        assert digest((CONTEXT/row['path']).read_bytes()) == row['sha256'], row['path']
    changed = ['scrapper/sources/exchange/reporting_periods.py',
               'scrapper/sources/exchange/result_filings.py',
               'scrapper/workflows/exchange/schedule.py',
               'scrapper/tests/test_reporting_periods.py',
               'scrapper/tests/test_e01_exchange_universe.py',
               'scrapper/tests/test_e06_scheduler.py']
    delta = b''
    for name in changed:
        path = CONTEXT/name
        before = path.read_text().splitlines(keepends=True) if path.exists() else []
        after = (ROOT/name).read_bytes()
        delta += ''.join(difflib.unified_diff(before, after.decode().splitlines(keepends=True),
            fromfile='a/'+name if path.exists() else '/dev/null', tofile='b/'+name)).encode()
        path.write_bytes(after)
        path.chmod(0o644)
    (OUT/'period.patch').write_bytes(delta)
    combined = (BASE/'combined.patch').read_bytes() + delta
    (OUT/'combined.patch').write_bytes(combined)
    files = [{'path':str(p.relative_to(CONTEXT)), 'mode':p.stat().st_mode & 0o777,
              'sha256':digest(p.read_bytes())} for p in sorted(CONTEXT.rglob('*')) if p.is_file()]
    previous = {r['path']:r for r in base['files']}
    actual = [r['path'] for r in files if previous.get(r['path']) != r]
    assert sorted(actual) == sorted(changed), actual
    write('source-manifest.json', {'base_commit':COMMIT, 'base_tree':base['base_tree'],
        'source_identity':'accepted combined AR-02 source plus exact period patch',
        'files':files, 'context':str(CONTEXT), 'source_date_epoch':base['source_date_epoch'],
        'patch_sha256':digest(combined),
        'source_projection_sha256':digest(json.dumps(files,sort_keys=True,separators=(',',':')).encode()),
        'changed_paths_from_combined':actual})
    write('source-delta-verification.json', {'status':'PASS', 'exact_delta':actual,
        'unchanged_inputs_verified':len(files)-len(actual), 'dependency_and_migration_changes':[]})
    RELEASE.mkdir()
    ARCHIVE.parent.mkdir(parents=True)
    print(json.dumps({'status':'PASS','delta':actual,'reused_entries':len(verified)}))
