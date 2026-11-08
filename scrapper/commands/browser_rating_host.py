"""Host-side lifecycle control; standard-library only, safe with stale PID files."""
import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ''):
    # ./scraper invokes this file directly through the host's stdlib Python.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scrapper.commands.browser_rating_host_config import (
    ROOT, LEGACY_FOLDER, PID_FILE, LOCK_FILE, LOG_FILE, PYTHON,
    assert_host_targets, bridge_command, environment_check_command,
)

FOLDER = LEGACY_FOLDER  # compatibility import; settings own new destinations
WORKER = ROOT / 'scrapper/commands/browser_rating_headed.py'


def running_pid():
    try:
        pid = int(PID_FILE.read_text().strip())
        if pid <= 1:
            return None
        process = subprocess.run(['ps', '-p', str(pid), '-o', 'command='],
            capture_output=True, text=True, timeout=5)
        if process.returncode == 0 and str(WORKER) in process.stdout:
            return pid
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def start():
    assert_host_targets()
    existing = running_pid()
    if existing:
        print(f'ICRA background browser RUNNING (PID {existing}); already started.')
        return
    python = PYTHON
    if not python.exists():
        raise RuntimeError(f'ICRA browser environment missing: {python}')
    # Keep the legacy environment usable; configured environments follow the lock.
    if 'SCRAPER_BROWSER_PYTHON' in os.environ:
        subprocess.run(environment_check_command(), check=True, capture_output=True, text=True, timeout=30)
    for parent in {PID_FILE.parent, LOG_FILE.parent}:
        parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
    with LOG_FILE.open('a') as log:
        process = subprocess.Popen([str(python), str(WORKER), '--background'],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True, env=env)
    PID_FILE.write_text(f'{process.pid}\n')
    time.sleep(2)
    if process.poll() is not None:
        raise RuntimeError(f'ICRA worker exited during startup; check {LOG_FILE}')
    print(f'ICRA background browser started (PID {process.pid}).')


def stop():
    assert_host_targets()
    pid = running_pid()
    if pid:
        print(f'ICRA: finishing current report and closing browser (PID {pid})...', flush=True)
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 180
        while running_pid() == pid:
            if time.monotonic() > deadline:
                raise RuntimeError('ICRA did not stop within 180s; containers kept running for safe retry.')
            time.sleep(0.5)
    # The browser is gone before any claim is released. The live DB bridge owns
    # only this host worker's jobs; container workers are never rearmed here.
    result = subprocess.run(bridge_command(),
        input=json.dumps({'action': 'release'}) + '\n', text=True,
        capture_output=True, timeout=30)
    if result.returncode:
        # Already shut-down Docker is fine only when there was no active worker.
        if pid:
            raise RuntimeError(f'Could not release ICRA claim: {result.stderr.strip()}')
    elif '"error"' in result.stdout:
        raise RuntimeError(result.stdout.strip())
    PID_FILE.unlink(missing_ok=True)
    print('ICRA background browser STOPPED; interrupted report remains queued.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status', 'is-running', 'check-environment'])
    args = parser.parse_args()
    if args.action == 'check-environment':
        result = subprocess.run(environment_check_command(), timeout=30)
        return result.returncode
    if args.action in ('status', 'is-running'):
        pid = running_pid()
        if args.action == 'is-running':
            return 0 if pid else 1
        print(f'ICRA background browser: {"RUNNING (PID " + str(pid) + ")" if pid else "STOPPED"}')
        return 0
    if args.action in ('start', 'stop'):
        assert_host_targets()
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            (start if args.action == 'start' else stop)()
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print(f'ERROR: {error}')
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
