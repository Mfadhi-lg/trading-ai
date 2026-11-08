"""Exact accepted context plus verified AR-02.1 bytes; no production access."""
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
CONTEXT = Path('/private/tmp/trading-ai-ar021-release-context-20261008')
COMMIT = 'c3ddbbb9c298d07b8b46a362996251417282476a'
RELEASE = ROOT / 'ops/docker/release/ar02.1-20261008'
ARCHIVE = ROOT / 'artifacts/acquisition-readiness/AR-02.1-release-20261008/release.oci.tar'
RELEASE_ID = 'c28.1-ar021-20261008'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)

if __name__ == '__main__':
    paths = git('ls-files', '-m', '-o', '--exclude-standard', '-z').decode().split('\0')
    write('working-tree-before.json', {
        'head': git('rev-parse', 'HEAD').decode().strip(),
        'tree': git('rev-parse', 'HEAD^{tree}').decode().strip(),
        'status': git('status', '--porcelain=v1', '-uall').decode(),
        'files': {p: digest((ROOT/p).read_bytes()) if (ROOT/p).is_file() else None for p in paths if p},
    })
    indexes = [
        'docs/acquisition-readiness/baselines/AR-01.2/reviewed-package/SHA256SUMS',
        'docs/acquisition-readiness/baselines/AR-02.1/SHA256SUMS',
        'docs/acquisition-readiness/baselines/AR-01.3-parent-20261008/SHA256SUMS',
    ]
    verified = []
    for index in indexes:
        for line in (ROOT/index).read_text().splitlines():
            expected, path = line.split('  ', 1)
            retained = ROOT/path
            if 'reviewed-package' in index:
                retained = (ROOT/index).parent/path
            actual = digest(retained.read_bytes())
            assert actual == expected, (path, expected, actual)
            verified.append({'path': str(retained.relative_to(ROOT)), 'sha256': actual, 'index': index})
    write('reused-evidence.json', {'status': 'PASS', 'files': verified,
        'tests_repeated': False, 'migrations_repeated': False, 'restore_repeated': False,
        'original_capture_repeated': False, 'original_replay_repeated': False,
        'reuse_reason': 'Exact unchanged source/test/input identities; only new image packaging needs proof.'})
    CONTEXT.mkdir(exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(git('archive', COMMIT, 'Dockerfile', '.dockerignore', 'scrapper'))) as archive:
        for member in archive:
            if member.isdir():
                continue
            assert member.isfile() and '..' not in Path(member.name).parts and not Path(member.name).is_absolute()
            path = CONTEXT/member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(member).read())
            path.chmod(member.mode)
    baseline = json.loads((ROOT/'docs/acquisition-readiness/baselines/AR-01.2/source-manifest.json').read_text())
    for item in baseline['files']:
        assert digest((CONTEXT/item['path']).read_bytes()) == item['sha256'], item['path']
    patch = ROOT/'docs/acquisition-readiness/baselines/AR-02.1/candidate.patch'
    subprocess.run(['git', 'apply', '--check', str(patch)], cwd=CONTEXT, check=True)
    subprocess.run(['git', 'apply', str(patch)], cwd=CONTEXT, check=True)
    test = 'scrapper/tests/test_ar02_snapshot_dates.py'
    # The retained candidate patch includes the new test; refuse additional edits.
    for path in ['scrapper/workflows/exchange/universe.py', test]:
        assert (CONTEXT/path).read_bytes() == (ROOT/path).read_bytes(), path
    files = [{'path': str(p.relative_to(CONTEXT)), 'mode': p.stat().st_mode & 0o777,
              'sha256': digest(p.read_bytes())} for p in sorted(CONTEXT.rglob('*')) if p.is_file()]
    old = {r['path']: r for r in baseline['files']}
    delta = [r['path'] for r in files if r['path'] not in old or r != old[r['path']]]
    assert sorted(delta) == sorted(['scrapper/workflows/exchange/universe.py', test]), delta
    assert set(old) <= {r['path'] for r in files}
    projection = digest(json.dumps(files, sort_keys=True, separators=(',', ':')).encode())
    write('source-manifest.json', {'base_commit': COMMIT, 'base_tree': baseline['tree'],
        'files': files, 'source_projection_sha256': projection, 'context': str(CONTEXT),
        'patch_sha256': digest(patch.read_bytes()), 'changed_paths': delta,
        'source_date_epoch': baseline['source_date_epoch'],
        'source_identity': 'accepted commit plus exact retained patch; not an unmodified commit'})
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps({'status': 'PASS', 'files': len(files), 'delta': delta, 'reused_files': len(verified)}))
