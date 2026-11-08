"""Install the concrete acquisition-only Aqua LaunchAgent; no ingestion on install."""
import json
import os
from pathlib import Path
import plistlib
import subprocess

from automatic_acquisition import HERE, LABEL, STATE, launchagent, verify_config, save
from production_ownership import owner_gate
from nse_results import check_configuration


def main():
    verify_config()
    check_configuration()
    permission = json.loads((HERE / 'scheduled-bse-permission.json').read_text())
    if not permission['scheduled_browser_access']:
        raise RuntimeError('BSE scheduled written consent scope is not confirmed')
    owner = owner_gate()
    expected = launchagent()
    prepared = HERE / (LABEL + '.plist')
    if plistlib.loads(prepared.read_bytes()) != expected:
        raise RuntimeError('Prepared schedule differs from intended configuration')
    destination = Path.home() / 'Library/LaunchAgents' / prepared.name
    if destination.exists() and plistlib.loads(destination.read_bytes()) != expected:
        raise RuntimeError('An existing different schedule needs explicit reconciliation')
    STATE.mkdir(parents=True, exist_ok=True)
    # The initial RunAtLoad tick performs a local-only desktop check and exits.
    (STATE / 'verify-desktop-once').touch(exist_ok=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(prepared.read_bytes())
    domain = 'gui/' + str(os.getuid())
    loaded = subprocess.run(['launchctl', 'print', domain + '/' + LABEL], capture_output=True)
    if loaded.returncode:
        subprocess.run(['launchctl', 'bootstrap', domain, str(destination)], check=True)
    else:
        subprocess.run(['launchctl', 'kickstart', domain + '/' + LABEL], check=True)
    save(STATE / 'installation.json', {'status': 'INSTALLED', 'label': LABEL,
        'path': str(destination), 'domain': domain, 'owner_gate': owner,
        'timezone': 'Asia/Kolkata', 'NSE': 'Mon–Sat 19:30 IST',
        'BSE': 'Daily 09:00 and 18:00 IST', 'automatic_retry': False,
        'historical_processing': False, 'workers_activated': False})
    print(json.dumps({'status': 'INSTALLED', 'path': str(destination), 'domain': domain}))


if __name__ == '__main__':
    main()
