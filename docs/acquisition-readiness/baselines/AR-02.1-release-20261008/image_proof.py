"""Runs inside the digest-pinned image with ephemeral storage only."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, '/app')
source = json.loads(Path('/proof/source-manifest.json').read_text())
checked = 0
for item in source['files']:
    if item['path'].startswith('scrapper/'):
        assert hashlib.sha256((Path('/app')/item['path']).read_bytes()).hexdigest() == item['sha256'], item['path']
        checked += 1
expected = {item['path'][len('scrapper/'):] for item in source['files'] if item['path'].startswith('scrapper/')}
actual = {str(p.relative_to('/app/scrapper')) for p in Path('/app/scrapper').rglob('*') if p.is_file()}
assert expected == actual, sorted(expected ^ actual)
subprocess.run(['python', '-m', 'pip', 'check'], check=True)
subprocess.run(['python', '-m', 'unittest', 'scrapper.tests.test_ar02_snapshot_dates', '-v'], check=True)
for module in ['scrapper.cli', 'scrapper.commands.quarter_processing_role', 'scrapper.commands.browser_rating_recovery']:
    subprocess.run(['python', '-m', module, '--help'], check=True, stdout=subprocess.DEVNULL)
from scrapper.runtime_identity import assert_writable_targets
os.environ['TRADING_AI_ENV'] = 'production'
try:
    assert_writable_targets(db=Path('/fixture/scraper.db'), store=Path('/fixture'), documents=Path('/fixture/documents'))
except RuntimeError as exc:
    assert 'authorized exact runtime identity' in str(exc)
else:
    raise AssertionError('unidentified production writes allowed')
print(json.dumps({'status': 'PASS', 'source_files_verified': checked, 'extra_source_files': 0,
    'focused_tests': 8, 'pip_check': True, 'cli_help': True, 'identity_fails_closed': True,
    'production_mounts': [], 'network': 'none', 'migration_rehearsal_repeated': False}))
