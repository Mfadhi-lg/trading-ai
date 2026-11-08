"""Lifecycle for the bounded Aqua dispatcher. No worker or container restoration."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
ACTIVATION = ROOT / 'ops/activation'
sys.path[:0] = [str(ACTIVATION), str(ROOT)]
import automatic_acquisition as dispatcher
import historical_candle_lifecycle as candles

STATE = ROOT / 'artifacts/operational-recovery/lifecycle'
PAUSE = dispatcher.STATE / 'lifecycle-paused.json'
PREVIOUS = STATE / 'previous.json'
PLIST = Path.home() / 'Library/LaunchAgents' / (dispatcher.LABEL + '.plist')
TARGET = 'gui/' + str(os.getuid()) + '/' + dispatcher.LABEL
VOLUME = 'trading-ai_scraper-data'
ACTIVE = {'running', 'paused', 'restarting', 'removing'}
CURRENT_PROJECTS = {'trading-ai-nse', 'trading-ai-phase4-routine', 'trading-ai-current-financial-once'}


def run(args, timeout=40, allowed=(0,)):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode not in allowed:
        raise RuntimeError(args[0] + ' inspection/action failed: ' + result.stderr.strip()[:500])
    return result


def read(path):
    return json.loads(path.read_text())


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    with temporary.open('x') as stream:
        json.dump(value, stream, indent=2, default=str)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def lock(path, wait=0, create=False):
    # Status must not create lock files. Missing existing locks are a blocker.
    with path.open('a' if create else 'rb') as stream:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Active owner has not drained: ' + path.name)
                time.sleep(.25)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def loaded():
    result = run(['launchctl', 'print', TARGET], allowed=(0, 113))
    if result.returncode == 0:
        expected = ['path = ' + str(PLIST), 'program = ' + dispatcher.PYTHON,
                    dispatcher.launchagent()['ProgramArguments'][1], '--tick']
        if not all(item in result.stdout for item in expected):
            raise RuntimeError('Loaded dispatcher definition differs from the installed configuration')
    return result.returncode == 0


def config():
    dispatcher.verify_config()
    if plistlib.loads(PLIST.read_bytes()) != dispatcher.launchagent():
        raise RuntimeError('Installed LaunchAgent differs from authorized dispatcher')
    for folder in (PLIST.parent, Path('/Library/LaunchAgents'), Path('/Library/LaunchDaemons')):
        if not folder.exists():
            continue
        for path in folder.glob('*.plist'):
            payload = plistlib.loads(path.read_bytes().lstrip())
            args = payload.get('ProgramArguments', [])
            is_dispatcher = any(Path(arg).name == 'automatic_acquisition.py' for arg in args)
            if is_dispatcher and path != PLIST:
                raise RuntimeError('Duplicate dispatcher configuration: ' + str(path))
    if (ACTIVATION / 'automatic_acquisition.py').read_text().count("STATE / 'lifecycle-paused.json'") != 2:
        raise RuntimeError('Current dispatcher does not have both reviewed lifecycle admission gates')
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        ACTIVATION / 'automatic-acquisition-manifest.json',
        ACTIVATION / 'step3_schedule.json', ACTIVATION / 'phase4_schedule.json')}


def containers():
    ids = run(['docker', 'ps', '-aq']).stdout.split()
    rows = json.loads(run(['docker', 'inspect', *ids]).stdout) if ids else []
    from phase4_documents import STORE
    selected = []
    for row in rows:
        labels = row['Config'].get('Labels') or {}
        project = labels.get('com.docker.compose.project', '')
        def document_mount(mount):
            if mount.get('Type') != 'bind':
                return False
            source = mount.get('Source', '').removeprefix('/host_mnt').rstrip('/') or '/'
            target = str(STORE)
            return source == target or source.startswith(target + '/') or target.startswith(source.rstrip('/') + '/')
        relevant = project.startswith('trading-ai') or any(
            m.get('Name') == VOLUME or document_mount(m) for m in row.get('Mounts', []))
        if relevant:
            receipt_stage = None
            for mount in row.get('Mounts', []):
                source = Path(mount.get('Source', ''))
                if mount.get('Destination') == '/selected' and source.resolve().is_relative_to(dispatcher.STATE.resolve()):
                    try:
                        receipt_stage = read(source / 'plan.json').get('stage')
                    except (OSError, ValueError):
                        pass
            selected.append({'name': row['Name'].lstrip('/'), 'state': row['State']['Status'],
                'project': project, 'service': labels.get('com.docker.compose.service'),
                'classification': 'current bounded adapter' if project in CURRENT_PROJECTS else 'legacy/manual; never restored',
                'receipt_stage': receipt_stage, 'started_at': row['State'].get('StartedAt'),
                'mounts': row.get('Mounts', [])})
    return selected


def external_handles():
    from phase4_documents import STORE
    from production_ownership import parse_lsof
    listing = run(['lsof', '-nP', '-Fpfan'], allowed=(0, 1))
    if not listing.stdout:
        raise RuntimeError('Cannot establish host file-handle visibility')
    handles = []
    for pid, files in parse_lsof(listing.stdout).items():
        for item in files:
            name = item.get('name', '')
            if name == str(STORE) or name.startswith(str(STORE) + '/'):
                handles.append({'pid': pid, **item})
    uncertain = [f for f in handles if f.get('access') != 'r' and f.get('fd') not in ('cwd', 'txt')]
    if uncertain:
        raise RuntimeError('External document store has writable/unknown host handles: ' +
                           json.dumps(uncertain))
    return handles


def database_status(script="/host/acquisition_db_status.py", arguments=()):
    # Inspect before mount: Docker must not create an absent production volume.
    row = json.loads(run(['docker', 'volume', 'inspect', VOLUME]).stdout)[0]
    if row['Name'] != VOLUME or (row.get('Labels') or {}).get('com.docker.compose.project') != 'trading-ai':
        raise RuntimeError('Production data volume identity differs')
    from phase4_documents import IMAGE
    live = '--live' in arguments or (script == '/host/acquisition_queries.py' and len(arguments)>1 and json.loads(arguments[1]).get('live') is True)
    reader_args = (["census", json.dumps({'summary':'--summary' in arguments})] if script == '/host/acquisition_db_status.py' else list(arguments)) if live else []
    args = ['docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
        '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=.5', '--memory=1536m',
        '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m',
        '--mount', 'type=volume,source=' + VOLUME + ',target=/data'+('' if live else ',readonly'),
        '--mount', 'type=bind,source=' + str(ACTIVATION) + ',target=/activation,readonly',
        '--mount', 'type=bind,source=' + str(ROOT / 'ops/host') + ',target=/host,readonly',
        '--mount', 'type=bind,source=' + str(ROOT / 'scrapper/view/render.py') + ',target=/acquisition_financial_renderer.py,readonly',
        '--env', 'PYTHONDONTWRITEBYTECODE=1', '--entrypoint', 'python', IMAGE,
        *(['/host/acquisition_live_reader.py',*reader_args] if live else [script,*arguments])]
    if live:args[2:2]=['--label','com.trading-ai.role=monitor-reader']
    return json.loads(run(args, timeout=90).stdout)


def quiescent(include_summary=False):
    from production_ownership import owner_gate
    owner = owner_gate()
    rows = containers()
    active = [r['name'] for r in rows if r['state'] in ACTIVE]
    if active:
        raise RuntimeError('Relevant containers still active; left untouched: ' + ', '.join(active))
    # Refuse any document-store bind consumer, even a read-only consumer, when
    # determining whether disconnecting the drive is possible.
    handles = external_handles()
    db = database_status(arguments=('--summary',)) if include_summary else database_status()
    if db['leases']['status'] != 'PASS':
        raise RuntimeError(db['leases']['error'])
    # Recheck after the census as well as before it. Supported writers are
    # excluded by the held owner locks; uncoordinated consumers fail closed.
    owner = owner_gate()
    active = [r['name'] for r in containers() if r['state'] in ACTIVE]
    if active:
        raise RuntimeError('Relevant container appeared during verification: ' + ', '.join(active))
    handles = external_handles()
    return {'ownership': owner, 'database': db, 'external_read_handles': handles}


def receipts():
    completed, unfinished = [], []
    for path in dispatcher.STATE.glob('*/admission.json'):
        admission = read(path)
        outcome = path.parent / 'outcome.json'
        if outcome.exists():
            item = read(outcome)
            item['receipt'] = str(outcome.relative_to(ROOT))
            completed.append(item)
        else:
            unfinished.append({'receipt': str(path.relative_to(ROOT)), 'job': admission.get('job'),
                'started': admission.get('started_at_utc'),
                'state': 'incomplete receipt; active or interrupted, never automatically replayed'})
    completed.sort(key=lambda x: str(x.get('finished_at_utc', '')))
    latest_documents = None
    for item in reversed(completed):
        if not str(item.get('exchange', '')).startswith('DOCUMENT_'):
            continue
        log = ROOT / item['receipt']
        log = log.with_name('runtime.log')
        if not log.exists():
            continue
        with log.open('rb') as stream:
            stream.seek(max(0, log.stat().st_size - 65536))
            lines = stream.read().decode(errors='replace').splitlines()
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get('stage') == 'document_stage_outcome':
                latest_documents = {key: record.get(key) for key in (
                    'document_stage', 'queue_depth', 'processing_counts', 'finished_at_utc')}
                latest_documents['source'] = str(log.relative_to(ROOT))
                latest_documents['fresh_live_census'] = False
                break
        if latest_documents:
            break
    return {'last_successful_batch': next((r for r in reversed(completed) if r.get('status') == 'PASS'), None),
        'last_successful_financial_batch': next((r for r in reversed(completed)
            if r.get('status') == 'PASS' and r.get('exchange') == 'FINANCIAL'), None),
        'last_document_queue_observation': latest_documents,
        'incomplete_admissions': unfinished,
        'recent_non_success': [r for r in completed if r.get('status') != 'PASS'][-8:],
        'blocked_stages': {p.name: read(p) for p in dispatcher.STATE.glob('*.stopped.json')}}


_schedule_cache = {}


def schedule_status(now=None):
    """Use fresh deployed modules, independent of a long-lived monitor's imports."""
    from acquisition_release import active_root, workspace_root
    release = active_root(ROOT)
    workspace = workspace_root(ROOT)
    observed = now or datetime.now(timezone.utc)
    reader = workspace/'ops/host/acquisition_schedule_reader.py'
    paths = [reader, *(release/'ops/activation'/name for name in (
        'automatic_acquisition.py', 'phase4_schedule.py', 'phase4_schedule.json',
        'step3_schedule.py', 'step3_schedule.json'))]
    fingerprint = (str(release), tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths))
    cached = _schedule_cache.get('value')
    if (now is None and cached is not None and _schedule_cache.get('fingerprint') == fingerprint
            and time.monotonic()-_schedule_cache.get('at', 0) < 5):
        return cached
    env = os.environ.copy()
    env.update(PYTHONPATH=dispatcher.SITE_PACKAGES, PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([dispatcher.PYTHON, '-B', str(reader), str(release), observed.isoformat()],
        capture_output=True, text=True, timeout=10, env=env)
    if result.returncode:
        detail = next((line.strip() for line in reversed(result.stderr.splitlines()) if line.strip()),
                      'Schedule reader failed')
        raise RuntimeError(detail)
    value = json.loads(result.stdout)
    if now is None:
        _schedule_cache.update(value=value, fingerprint=fingerprint, at=time.monotonic())
    return value

def status(include_summary=False, *, live=False):
    result = {'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'admissions_paused': PAUSE.exists()}
    errors = []
    for key, action in [('dispatcher_loaded', loaded), ('configuration', config),
                        ('schedule', schedule_status), ('receipts', receipts), ('containers', containers),
                        ('historical_candles', candles.status)]:
        try:
            result[key] = action()
        except Exception as exc:
            errors.append(key + ': ' + str(exc))
    for name in ('last-tick.json', 'scheduler-failure.json'):
        path = dispatcher.STATE / name
        if path.exists():
            result[name] = read(path)
    try:
        from phase4_documents import storage_preflight
        result['storage'] = storage_preflight()
    except Exception as exc:
        errors.append('storage: ' + str(exc))
    if live:
        try:
            arguments=('--live','--summary') if include_summary else ('--live',)
            try:
                database=database_status(arguments=arguments)
            except RuntimeError as exc:
                if 'unable to open database file' not in str(exc):raise
                with lock(ACTIVATION / '.scheduler-admission.lock'):
                    with lock(ACTIVATION / '.nse-owner.lock'):
                        database=quiescent(include_summary=include_summary)['database']
            result['live_inspection']={'database':database}
            result['currently_executing_stage']='See active bounded stages; monitoring does not require idle acquisition'
        except Exception as exc:
            errors.append('live database snapshot unavailable: '+str(exc))
    else:
        try:
            next_batch = result.get('schedule', {}).get('next_scheduled_batch')
            if next_batch and (datetime.fromisoformat(next_batch['next_at_ist']) - datetime.now(timezone.utc)).total_seconds() < 120:
                raise RuntimeError('Census deferred within 120 seconds of scheduled work; inspect receipts/containers')
            with lock(ACTIVATION / '.scheduler-admission.lock'):
                with lock(ACTIVATION / '.nse-owner.lock'):
                    result['quiescent_inspection'] = quiescent(include_summary=True) if include_summary else quiescent()
            result['currently_executing_stage'] = 'none (ownership verified); incomplete receipts may be interrupted'
        except Exception as exc:
            result['currently_executing_stage'] = 'active or unavailable; see containers and incomplete admissions'
            errors.append('ownership/queue census unavailable: ' + str(exc))
    result['inspection_errors'] = errors
    result['active_bounded_stages'] = [
        {'container': row['name'], 'service': row['service'], 'state': row['state'], 'receipt_stage': row.get('receipt_stage'), 'started_at': row.get('started_at')}
        for row in result.get('containers', [])
        if row['state'] in ACTIVE and row['classification'] == 'current bounded adapter']
    result['pipeline_health'] = ('ADMISSIONS_PAUSED' if PAUSE.exists() else
        'BLOCKED_STAGES' if result.get('receipts', {}).get('blocked_stages') else
        'EXPIRED_AUTHORIZATION' if result.get('schedule',{}).get('document_window_expired') else 'DISPATCHING')
    result['status'] = 'DEGRADED' if errors else 'PASS'
    return result


def shutdown(wait=660):
    config_hashes = config()
    # Serialize lifecycle operators, independently of the running bounded tick.
    with lock(STATE / 'operator.lock', create=True):
        if not PAUSE.exists():
            previous = {'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
                'dispatcher_loaded': loaded(), 'configuration': config_hashes,
                'authorized_services': [dispatcher.LABEL], 'restore_containers': []}
            atomic(PREVIOUS, previous)
            atomic(PAUSE, previous)
        # Marker is durable before waiting. Never bootout/kill an active tick.
        print('Admissions paused. Waiting for bounded owners to finish; no writer will be killed.', file=sys.stderr, flush=True)
        deadline = time.monotonic() + wait
        candle_checks=candles.pause(wait=max(0,deadline-time.monotonic()))
        with lock(ACTIVATION / '.scheduler-admission.lock', wait=max(0,deadline-time.monotonic())):
            with lock(ACTIVATION / '.nse-owner.lock', wait=max(0, deadline - time.monotonic())):
                checks = quiescent()
                # Sync only after every known writer, container and lease cleared.
                run(['/bin/sync'])
                checks['external_read_handles'] = external_handles()
                result = {'status': 'PASS', 'admissions_paused': True, 'checks': checks,
                    'historical_candles':candle_checks,
                    'safe_to_shut_down_mac': True,
                    'safe_to_disconnect_drive': False,
                    'drive_action': 'Acquisition writes are quiescent. Eject My Passport in Finder; disconnect only after macOS confirms ejection.',
                    'previous_configuration': str(PREVIOUS)}
                atomic(STATE / 'shutdown-verification.json', result)
                return result


def missed_slots(previous, now):
    start = datetime.fromisoformat(previous['recorded_at_utc'])
    result = []
    # Enumerate schedules at pause time; document slots remain bounded by expiry.
    from phase4_schedule import jobs as document_jobs, settings
    from step3_schedule import jobs as financial_jobs
    end = datetime.fromisoformat(settings()['expires_at_utc'])
    jobs = [r for r in dispatcher.jobs() if not r[1].startswith('DOCUMENT_')]
    jobs += document_jobs(start)
    for name, exchange, trigger in jobs:
        at = trigger.get_next_fire_time(None, start)
        count, first, last = 0, None, None
        while at and at < now and (not exchange.startswith('DOCUMENT_') or at < end):
            first = first or at.isoformat()
            last = at.isoformat()
            count += 1
            at = trigger.get_next_fire_time(at, at + timedelta(microseconds=1))
        if count:
            result.append({'job': name, 'missed_slots': count, 'first': first, 'last': last, 'replayed': False})
    return result


def wake():
    with lock(STATE / 'operator.lock', create=True):
        fingerprints = config()
        from phase4_documents import storage_preflight, IMAGE
        capacity = storage_preflight()
        if os.stat('/dev/console').st_uid != os.getuid():
            raise RuntimeError('Current user must have the logged-in Aqua desktop session')
        if not Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').is_file():
            raise RuntimeError('The current visible-Chrome browser dependency is missing')
        session = ROOT / '.screener_session'
        if not session.is_file() or not session.stat().st_size:
            raise RuntimeError('Current Screener session file is missing/empty; do not launch acquisition')
        run(['docker', 'info', '--format', '{{.ServerVersion}}'])
        from nse_results import check_configuration
        nse_release = check_configuration()
        # Match the NSE runner: its sealed local content ID survives loss of
        # repository-digest aliases (for example after Docker save/load).
        run(['docker', 'image', 'inspect', IMAGE, nse_release['oci_index_digest']])
        candle_status=candles.status()
        if (not PAUSE.exists() and loaded() and candle_status.get('status')=='RUNNING'
                and candle_status.get('workers',{}).get('acquisition')):
            # No activation occurs on an already-running wake. Its own known
            # Passport writer must not be mistaken for an unrelated blocker.
            return {'status':'PASS','already_awake':True,'schedule':schedule_status(),
                    'storage':capacity,'historical_candles':candle_status}
        with lock(ACTIVATION / '.scheduler-admission.lock'):
            with lock(ACTIVATION / '.nse-owner.lock'):
                checks = quiescent()
                is_loaded = loaded()
                if not PAUSE.exists():
                    if not is_loaded:
                        raise RuntimeError('No current lifecycle authorization to restore unloaded dispatcher; old shutdown state ignored')
                    return {'status': 'PASS', 'already_awake': True, 'schedule': schedule_status(),
                            'storage': capacity, 'checks': checks,'historical_candles':candles.wake()}
                previous = read(PAUSE)
                if previous['configuration'] != fingerprints or previous['authorized_services'] != [dispatcher.LABEL]:
                    raise RuntimeError('Paused configuration/authorization changed; explicit reconciliation required')
                if not previous['dispatcher_loaded']:
                    raise RuntimeError('Dispatcher was not previously active; wake cannot authorize activation')
                if not is_loaded:
                    # Bootstrap runs a tick under the held admission lock; it
                    # returns busy. The minute timer resumes after validation.
                    run(['launchctl', 'bootstrap', 'gui/' + str(os.getuid()), str(PLIST)])
                    if not loaded():
                        raise RuntimeError('Dispatcher bootstrap did not load the expected label')
                missed = missed_slots(previous, datetime.now(timezone.utc))
                # Finish all potentially failing planning before reopening
                # admissions. Restore the marker if receipt persistence fails.
                result = {'status': 'PASS', 'admissions_paused': False, 'schedule': schedule_status(),
                    'missed_schedule_opportunities': missed, 'replayed': False, 'checks': checks,
                    'storage': capacity, 'legacy_shutdown_state_ignored': True}
                try:
                    PAUSE.unlink()
                    fd = os.open(PAUSE.parent, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    result['historical_candles']=candles.wake()
                    atomic(STATE / 'wake-verification.json', result)
                except BaseException:
                    atomic(PAUSE, previous)
                    candles.pause(wait=60)
                    raise
                return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'shutdown', 'wake'))
    parser.add_argument('--wait-seconds', type=int, default=660)
    args = parser.parse_args()
    try:
        if args.action != 'status':
            STATE.mkdir(parents=True, exist_ok=True)
        result = status() if args.action == 'status' else wake() if args.action == 'wake' else shutdown(args.wait_seconds)
    except Exception as exc:
        result = {'status': 'BLOCKED', 'error': str(exc), 'admissions_paused': PAUSE.exists(),
                  'safe_to_shut_down_mac': False, 'safe_to_disconnect_drive': False}
    print(json.dumps(result, indent=2, default=str))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
