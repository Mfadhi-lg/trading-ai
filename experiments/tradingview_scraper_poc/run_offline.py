"""Trusted launcher. OS isolation and probe MUST succeed before upstream imports."""
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parents[1] / 'artifacts/tradingview-scraper-poc/tv01'


def main():
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}
    venv_path = ROOT / '.venv'
    if not (venv_path / 'bin/python3').exists():
        venv.EnvBuilder(with_pip=False).create(venv_path)
    command = ['/usr/bin/sandbox-exec', '-f', str(ROOT / 'network-deny.sb'),
               str(venv_path / 'bin/python3'), '-I', '-S', '-B']
    probe = """import socket,json,errno
results=[]
for family,address in [(socket.AF_INET,('192.0.2.1',443)),(socket.AF_INET6,('2001:db8::1',443))]:
    try:
        s=socket.socket(family); s.settimeout(0.2); s.connect(address)
    except PermissionError as e:
        assert e.errno in (errno.EPERM,errno.EACCES)
        results.append({'family':family,'blocked':True,'errno':e.errno})
    else:
        raise SystemExit('FAIL: network was not denied')
print(json.dumps(results))
"""
    check = subprocess.run(command + ['-c', probe], env=env, capture_output=True, text=True, timeout=10)
    recorded_command = [part.replace(str(ROOT.parents[1]), '<workspace>') for part in command]
    evidence = dict(command=recorded_command, returncode=check.returncode, stdout=check.stdout, stderr=check.stderr,
                    destinations='Reserved TEST-NET / documentation IPs only. No DNS or TradingView probes.')
    (OUT / 'network-isolation.json').write_text(json.dumps(evidence, indent=2) + '\n')
    if check.returncode:
        raise SystemExit('Isolation failed. Upstream code was NOT executed.')
    run = subprocess.run(command + [str(ROOT / 'test_offline.py')], env=env,
                         capture_output=True, text=True, timeout=45)
    (OUT / 'tests.log').write_text(run.stdout + run.stderr)
    print(run.stdout + run.stderr)
    return run.returncode


if __name__ == '__main__':
    sys.exit(main())
