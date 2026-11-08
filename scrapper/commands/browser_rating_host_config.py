"""Inert, standard-library host settings shared by lifecycle and browser bridge."""
import os
from pathlib import Path

from scrapper.config import BROWSER_STATE, LOGS, assert_configured_writable_targets

ROOT = Path(__file__).resolve().parents[2]
LEGACY_FOLDER = ROOT / 'artifacts/icra-download-test'
STATE = BROWSER_STATE if 'SCRAPER_BROWSER_STATE_PATH' in os.environ else LEGACY_FOLDER
LOG_ROOT = LOGS if 'SCRAPER_LOGS_PATH' in os.environ else LEGACY_FOLDER
PID_FILE = Path(os.environ.get('SCRAPER_BROWSER_PID_PATH', str(STATE / 'icra-recovery.pid')))
LOCK_FILE = Path(os.environ.get('SCRAPER_BROWSER_LOCK_PATH', str(STATE / 'icra-lifecycle.lock')))
LOG_FILE = Path(os.environ.get('SCRAPER_BROWSER_LOG_PATH', str(LOG_ROOT / 'icra-recovery.log')))
PYTHON = Path(os.environ.get('SCRAPER_BROWSER_PYTHON', str(LEGACY_FOLDER / '.venv/bin/python')))
RUN_ID = os.environ.get('SCRAPER_BROWSER_RUN_ID', 'browser-ratings-20261001')
CONTAINER = os.environ.get('SCRAPER_BROWSER_BRIDGE_CONTAINER', 'trading-ai-document-download-worker-1')


def assert_host_targets():
    # Validate all destinations before mkdir/open/unlink/signal/bridge startup.
    targets = [PID_FILE, LOCK_FILE, LOG_FILE]
    if os.environ.get('PLAYWRIGHT_BROWSERS_PATH'):
        if os.environ['PLAYWRIGHT_BROWSERS_PATH'] == '0':
            raise RuntimeError('Use an absolute PLAYWRIGHT_BROWSERS_PATH for host browser storage')
        targets.append(Path(os.environ['PLAYWRIGHT_BROWSERS_PATH']))
    assert_configured_writable_targets(*targets)
    if not RUN_ID.strip() or not CONTAINER.strip():
        raise RuntimeError('Browser run ID and bridge container must be nonempty')


def bridge_command():
    return ['docker', 'exec', '-i', CONTAINER, 'python', '-m',
            'scrapper.commands.browser_rating_recovery', '--run-id', RUN_ID,
            '--bridge', '--operator-authorized']


def environment_check_command():
    # Read-only check in the chosen interpreter, no browser/DB/network startup.
    return [str(PYTHON), '-B', '-c', '''import importlib.metadata as m, json, sys
from pathlib import Path
assert sys.version_info[:2] == (3, 14), "Host browser requires Python 3.14"
expected = {"playwright":"1.60.0", "greenlet":"3.5.6", "pyee":"13.0.1", "typing_extensions":"4.16.0"}
for name, version in expected.items():
    assert m.version(name) == version, f"{name} must equal {version}"
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    executable = Path(p.chromium.executable_path)
    assert executable.is_file(), f"Chromium missing: {executable}"
print(json.dumps({"python":sys.version.split()[0], "packages":expected, "chromium":str(executable)}))
''']
