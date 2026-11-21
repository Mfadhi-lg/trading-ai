"""Opt-in real Docker startup at the production /host and /activation depth.

ACQ_TEST_IMAGE selects a local image; only source directories are mounted.
No database, socket, credential, document or runtime-state mounts are permitted.
"""
import os
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = os.environ.get('ACQ_TEST_IMAGE')
pytestmark = pytest.mark.skipif(not IMAGE, reason='requires explicit local ACQ_TEST_IMAGE and Docker')


@pytest.mark.parametrize('arguments', [
    ['/host/diagnose_financial_pending.py', '--help'],
    ['/host/financial_queue_eligibility.py', '--help'],
    ['-c', "import sys; sys.path[:0]=['/host']; import acquisition_live_reader; import acquisition_queries"],
    ['-c', "import runpy; runpy.run_path('/host/acquisition_queries.py', run_name='startup_test')"],
])
def test_actual_mounted_startup(arguments):
    command = ['docker', 'run', '--rm', '--pull=never', '--network=none',
        '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
        '--env', 'PYTHONDONTWRITEBYTECODE=1']
    for directory, destination in [('ops/host', '/host'), ('ops/activation', '/activation')]:
        command += ['--mount', f'type=bind,src={ROOT/directory},dst={destination},readonly']
    command += ['--entrypoint', 'python', IMAGE, *arguments]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
