"""Compact supported-integration review ZIP, with isolated offline replay."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from .prepare_narrowed import ROOT, BASE, OUT, sha
from .execute_narrowed import save

ARCHIVE = BASE / 'TECH-06-global-market-review.zip'


def collect():
    baseline = json.loads((OUT / 'PRESERVATION_BASELINE.json').read_text())
    paths = {ROOT / name for name in baseline}
    paths.update(p for p in OUT.rglob('*') if p.is_file() and p.suffix != '.zip')
    paths.update((ROOT / 'experiments/technical_integration_v2').glob('*.py'))
    paths.add(ROOT / 'experiments/technical_integration_v2/browser_narrowed.mjs')
    for name in ('README.md', 'TECHNICAL_BLOCK_STATUS.md', 'UPSTOX_DIAGNOSTIC_MANIFEST.json',
                 'UPSTOX_DIAGNOSTIC_MANIFEST.sha256', 'SESSION_CALENDAR_AUDIT.csv',
                 'inputs/global.json.gz'):
        paths.add(BASE / name)
    for name in ('global-market.html', 'global-market-data.js', 'global-minute-data.js', 'global-context.js'):
        paths.add(BASE / 'charts' / name)
    for name in ('__init__.py', 'retrieve_er03.py', 'er03.py', 'candles.py', 'events.py',
                 'indicators.py', 'upstox_candles.py', 'execute_ta02d.py'):
        paths.add(ROOT / 'experiments/earnings_reaction_v1' / name)
    # Retained Dow provenance; no second acquisition.
    old = ROOT / 'artifacts/technical-integration-v1'
    paths.add(old / 'retrieval-execution.json')
    record = next(r for r in json.loads((old / 'retrieval-execution.json').read_text())['requests'] if r['series'] == 'DJIA')
    paths.add(ROOT / record['retained_path'])
    # Only required local assets/evidence, no databases, credentials or old ZIPs.
    assert all(p.is_file() and p.is_relative_to(ROOT) for p in paths)
    assert all(p.name not in ('.env', 'scraper.db') for p in paths)
    return sorted(p for p in paths if p.name not in ('SHA256SUMS', 'ZIP_RECEIPT.json'))


def archive(paths):
    (OUT / 'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(ROOT)}\n' for p in paths))
    with ZipFile(ARCHIVE, 'w', ZIP_DEFLATED, compresslevel=9) as z:
        for p in paths + [OUT / 'SHA256SUMS']:
            z.write(p, str(p.relative_to(ROOT)))
    with ZipFile(ARCHIVE) as z:
        assert z.testzip() is None


def main():
    from . import test_narrowed
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(test_narrowed))
    (OUT / 'TEST_RESULTS.txt').write_text(stream.getvalue())
    save(OUT / 'TEST_VALIDATION.json', dict(passed=result.wasSuccessful(), tests=result.testsRun,
        errors=len(result.errors), failures=len(result.failures),
        command='python3 -m unittest experiments.technical_integration_v2.test_narrowed -v'))
    assert result.wasSuccessful()
    assert json.loads((OUT / 'BROWSER_VALIDATION.json').read_text())['passed']
    paths = collect()
    archive(paths)
    # Confirm the review package itself is sufficient for its documented rebuild
    # and data/preservation tests. No browser, secret access or network needed.
    with tempfile.TemporaryDirectory(prefix='tech06-package-replay-') as temp:
        extracted = Path(temp)
        with ZipFile(ARCHIVE) as z:
            z.extractall(extracted)
        checksums = extracted / OUT.relative_to(ROOT) / 'SHA256SUMS'
        for line in checksums.read_text().splitlines():
            expected, relative = line.split(None, 1)
            assert sha(extracted / relative) == expected, relative
        before = {str(p.relative_to(ROOT)): sha(p) for p in paths
                  if p.suffix == '.csv' and p.is_relative_to(OUT)}
        build = subprocess.run([sys.executable, '-m', 'experiments.technical_integration_v2.build_narrowed'],
                               cwd=extracted, capture_output=True, text=True)
        if build.returncode:
            raise RuntimeError('Isolated offline rebuild failed: ' + build.stderr)
        tests = subprocess.run([sys.executable, '-m', 'unittest', 'experiments.technical_integration_v2.test_narrowed', '-v'],
                               cwd=extracted, capture_output=True, text=True)
        if tests.returncode:
            raise RuntimeError('Isolated package tests failed: ' + tests.stderr)
        reproduced = all(sha(extracted / name) == digest for name, digest in before.items())
        assert reproduced, 'Nondeterministic CSV replay'
        save(OUT / 'PACKAGE_REPLAY.json', dict(passed=True, all_included_hashes_verified=True,
            isolated_offline_rebuild=True, isolated_tests_passed=True,
            deterministic_csv_replay=True, csv_files_compared=len(before),
            authenticated_requests=0, credentials_read=False,
            note='Only the offline supported build and tests were invoked. No acquisition runner or broad build ran.'))
    paths = collect()
    archive(paths)
    receipt = dict(path=str(ARCHIVE.relative_to(ROOT)), sha256=sha(ARCHIVE), bytes=ARCHIVE.stat().st_size,
                   included_files=len(paths) + 1, zip_integrity_verified=True,
                   no_credentials_or_databases=True, isolated_offline_replay_passed=True)
    save(OUT / 'ZIP_RECEIPT.json', receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
