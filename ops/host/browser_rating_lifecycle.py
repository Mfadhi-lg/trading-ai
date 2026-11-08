"""Host lifecycle owner; dependencies supplied per call by the compatible command adapter."""

def running_pid(*, hooks):
    try:
        pid = int(hooks.PID_FILE.read_text().strip())
        if pid <= 1:
            return None
        process = hooks.subprocess.run(['ps', '-p', str(pid), '-o', 'command='],
            capture_output=True, text=True, timeout=5)
        if process.returncode == 0 and str(hooks.WORKER) in process.stdout:
            return pid
    except (OSError, ValueError, hooks.subprocess.SubprocessError):
        pass
    return None


def start(*, hooks):
    hooks.assert_host_targets()
    existing = hooks.running_pid()
    if existing:
        print(f'ICRA background browser RUNNING (PID {existing}); already started.')
        return
    python = hooks.PYTHON
    if not python.exists():
        raise RuntimeError(f'ICRA browser environment missing: {python}')
    # Keep the legacy environment usable; configured environments follow the lock.
    if 'SCRAPER_BROWSER_PYTHON' in hooks.os.environ:
        hooks.subprocess.run(hooks.environment_check_command(), check=True, capture_output=True, text=True, timeout=30)
    for parent in {hooks.PID_FILE.parent, hooks.LOG_FILE.parent}:
        parent.mkdir(parents=True, exist_ok=True)
    env = hooks.os.environ.copy()
    env['PYTHONPATH'] = str(hooks.ROOT) + hooks.os.pathsep + env.get('PYTHONPATH', '')
    with hooks.LOG_FILE.open('a') as log:
        process = hooks.subprocess.Popen([str(python), str(hooks.WORKER), '--background'],
            cwd=hooks.ROOT, stdin=hooks.subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True, env=env)
    hooks.PID_FILE.write_text(f'{process.pid}\n')
    hooks.time.sleep(2)
    if process.poll() is not None:
        raise RuntimeError(f'ICRA worker exited during startup; check {hooks.LOG_FILE}')
    print(f'ICRA background browser started (PID {process.pid}).')


def stop(*, hooks):
    hooks.assert_host_targets()
    pid = hooks.running_pid()
    if pid:
        print(f'ICRA: finishing current report and closing browser (PID {pid})...', flush=True)
        hooks.os.kill(pid, hooks.signal.SIGTERM)
        deadline = hooks.time.monotonic() + 180
        while hooks.running_pid() == pid:
            if hooks.time.monotonic() > deadline:
                raise RuntimeError('ICRA did not stop within 180s; containers kept running for safe retry.')
            hooks.time.sleep(0.5)
    # The browser is gone before any claim is released. The live DB bridge owns
    # only this host worker's jobs; container workers are never rearmed here.
    result = hooks.subprocess.run(hooks.bridge_command(),
        input=hooks.json.dumps({'action': 'release'}) + '\n', text=True,
        capture_output=True, timeout=30)
    if result.returncode:
        # Already shut-down Docker is fine only when there was no active worker.
        if pid:
            raise RuntimeError(f'Could not release ICRA claim: {result.stderr.strip()}')
    elif '"error"' in result.stdout:
        raise RuntimeError(result.stdout.strip())
    hooks.PID_FILE.unlink(missing_ok=True)
    print('ICRA background browser STOPPED; interrupted report remains queued.')


def main(*, hooks):
    parser = hooks.argparse.ArgumentParser(description=hooks.__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status', 'is-running', 'check-environment'])
    args = parser.parse_args()
    if args.action == 'check-environment':
        result = hooks.subprocess.run(hooks.environment_check_command(), timeout=30)
        return result.returncode
    if args.action in ('status', 'is-running'):
        pid = hooks.running_pid()
        if args.action == 'is-running':
            return 0 if pid else 1
        print(f'ICRA background browser: {"RUNNING (PID " + str(pid) + ")" if pid else "STOPPED"}')
        return 0
    if args.action in ('start', 'stop'):
        hooks.assert_host_targets()
    hooks.LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with hooks.LOCK_FILE.open('a') as lock:
        hooks.fcntl.flock(lock, hooks.fcntl.LOCK_EX)
        try:
            (hooks.start if args.action == 'start' else hooks.stop)()
        except (OSError, RuntimeError, hooks.subprocess.SubprocessError) as error:
            print(f'ERROR: {error}')
            return 1
    return 0


