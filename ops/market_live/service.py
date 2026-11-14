"""Independent, explicitly activated host supervision; no installation side effects.

The stream never owns acquisition's admission lock. An optional incremental
worker has a different lock and cannot stall the stream's session watchdog.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import threading
import time

LABEL = 'com.trading-ai.market-live'


def aware(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result.tzinfo is None:
        raise ValueError('Aware timestamp required')
    return result


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    with temporary.open('w') as out:
        json.dump(value, out, sort_keys=True, indent=2, default=str)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}


def authorized_config(path, digest, now):
    raw = Path(path).read_bytes()
    if not digest or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Exact configuration digest authorization required')
    config = json.loads(raw)
    if config.get('owner_approved') is not True or not config.get('activation_ref'):
        raise ValueError('Owner activation approval reference required')
    if not aware(config['active_from']) <= now < aware(config['active_until']):
        raise ValueError('Outside approved activation window')
    if not Path(config['state_dir']).is_absolute():
        raise ValueError('Explicit absolute service state_dir required')
    if not Path(config['provider_quota_path']).is_absolute():
        raise ValueError('Explicit absolute shared provider quota ledger required')
    for name in ('identity',):
        raw_identity = Path(config[name+'_path']).read_bytes()
        if hashlib.sha256(raw_identity).hexdigest() != config.get(name+'_sha256'):
            raise ValueError('Verified identity digest changed or missing')
    if config.get('refresh_config_path'):
        refresh_raw = Path(config['refresh_config_path']).read_bytes()
        if hashlib.sha256(refresh_raw).hexdigest() != config.get('refresh_config_sha256'):
            raise ValueError('Approved refresh configuration digest changed or missing')
        child = json.loads(refresh_raw)
        if (not config.get('provider_quota_path') or not child.get('provider_quota_path')
                or Path(config['provider_quota_path']).resolve() != Path(child['provider_quota_path']).resolve()):
            raise ValueError('Live and incremental workers must share the provider quota ledger')
    return config


@contextmanager
def ownership(state_dir, name='.service.lock'):
    """Advisory lock ownership, never PID-based signalling or stale lock deletion."""
    root = Path(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    with (root / name).open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def active_session(calendar, now):
    """Only owner-reviewed calendar rows can admit authenticated streaming."""
    # Importing ZoneInfo does not load application config, credentials or stores.
    from zoneinfo import ZoneInfo
    today = now.astimezone(ZoneInfo('Asia/Kolkata')).date().isoformat()
    rows = calendar.get('days', [])
    row = rows.get(today) if isinstance(rows, dict) else next((r for r in rows if r['day'] == today), None)
    if not row or row.get('verified') is not True:
        return 'BLOCKED', None
    if row.get('status', '').endswith('UNRESOLVED'):
        return 'BLOCKED', None
    for session in row.get('sessions', []):
        start, end = aware(session['opens_at']), aware(session['closes_at'])
        if start <= now < end:
            return 'ACTIVE', (start, end)
    return 'IDLE', None


def reviewed_calendar(config):
    raw = Path(config['calendar_path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != config.get('calendar_sha256'):
        raise ValueError('Reviewed calendar digest changed or missing')
    return json.loads(raw)


class SessionWatchdog:
    """Detect wall-clock gaps, including sleep; recovery never fabricates bars."""

    def __init__(self, previous=None, maximum_gap_seconds=30):
        self.previous = aware(previous) if previous else None
        self.maximum_gap_seconds = maximum_gap_seconds

    def step(self, calendar, now):
        now = aware(now)
        missed = None
        if self.previous and (now - self.previous).total_seconds() > self.maximum_gap_seconds:
            missed = self.previous.isoformat()
        elif self.previous and now < self.previous:
            # A backwards wall-clock jump invalidates freshness just as sleep does.
            missed = now.isoformat()
        self.previous = now
        state, session = active_session(calendar, now)
        return dict(state=state, session=session, recovery_after=missed)


def refresh_once(config, now, refresh=None, admission_check=None):
    if not config.get('refresh_config_path'):
        return dict(status='UNKNOWN', reason='Incremental refresh is not configured')
    if refresh is None:
        from scrapper.market_refresh.service import refresh
    with ownership(config['state_dir'], '.refresh-admission.lock'):
        return refresh(config['refresh_config_path'], now,
                       authorized_sha256=config.get('refresh_config_sha256'),
                       admission_check=admission_check)


def parent_admission(config_path, digest, clock, stop_event=None):
    """Per-request grant check; an in-flight response may drain after stop."""
    if stop_event is not None and stop_event.is_set():
        return False
    try:
        config = authorized_config(config_path, digest, clock())
        reviewed_calendar(config)
    except (OSError, ValueError, KeyError):
        return False
    return not (Path(config['state_dir'])/'stop.request').exists()


def run_service(config_path, digest, *, connect=None, refresh=None,
                clock=lambda: datetime.now(timezone.utc), wait=time.sleep, max_ticks=None):
    config = authorized_config(config_path, digest, clock())
    if connect is None:
        from scrapper.market_live.service import stream as connect
    root = Path(config['state_dir'])
    with ownership(root):
        previous = read(root / 'supervisor.json')
        watchdog = SessionWatchdog(previous.get('observed_at'), config.get('sleep_gap_seconds', 30))
        stop_path = root / 'stop.request'
        # A retained stop survives launchd restart. Only explicit start resumes it.
        thread = None
        stop = threading.Event()
        refresh_thread = None
        refresh_stop = threading.Event()
        refresh_at = None
        failure = {}
        last = {}

        def streaming(recovery):
            try:
                connect(config, stop, recovery)
            except Exception as error:
                # Provider exceptions may contain authenticated URLs. Persist only type.
                failure['stream'] = type(error).__name__

        def incremental(now):
            try:
                result = refresh_once(config, now, refresh,
                    admission_check=lambda: parent_admission(config_path, digest, clock, refresh_stop))
                save(root / 'refresh_status.json', dict(observed_at=clock().isoformat(), result=result))
            except BlockingIOError:
                save(root / 'refresh_status.json', dict(observed_at=clock().isoformat(), status='BUSY'))
            except Exception as error:
                save(root / 'refresh_status.json', dict(observed_at=clock().isoformat(), status='BLOCKED', error_type=type(error).__name__))

        try:
            ticks = 0
            while max_ticks is None or ticks < max_ticks:
                ticks += 1
                now = clock()
                try:
                    # Expiry is checked while running, not only when starting.
                    authorized_config(config_path, digest, now)
                    calendar = reviewed_calendar(config)
                    current = watchdog.step(calendar, now)
                except (ValueError, KeyError, OSError):
                    current = dict(state='BLOCKED', session=None, recovery_after=None)
                stopping = stop_path.exists()
                if stopping or current['state'] == 'BLOCKED':
                    refresh_stop.set()
                restart = current['recovery_after'] is not None
                if thread and (stopping or current['state'] != 'ACTIVE' or restart):
                    stop.set()
                    thread.join(timeout=config.get('stop_timeout_seconds', 10))
                    if thread.is_alive():
                        raise RuntimeError('Stream did not honor stop event')
                    thread = None
                if stopping:
                    last = dict(status='STOPPED', observed_at=now.isoformat(), worker_running=False)
                    save(root / 'supervisor.json', last)
                    wait(min(5, max(.1, config.get('poll_seconds', 5))))
                    continue
                if thread and not thread.is_alive():
                    # Bounded retries belong to the stream. Do not defeat its
                    # exhausted reconnect budget by restarting it every tick.
                    thread = None
                    failure.setdefault('stream', 'StreamExited')
                if current['state'] == 'ACTIVE' and thread is None and not failure:
                    stop = threading.Event()
                    thread = threading.Thread(target=streaming, args=(current['recovery_after'] or previous.get('observed_at'),), daemon=True)
                    thread.start()
                if (config.get('refresh_config_path') and current['state'] != 'BLOCKED'
                        and (refresh_thread is None or not refresh_thread.is_alive())
                        and (refresh_at is None or (now-refresh_at).total_seconds() >= config.get('refresh_interval_seconds', 300))):
                    refresh_at = now
                    refresh_stop.clear()
                    refresh_thread = threading.Thread(target=incremental, args=(now,), daemon=True)
                    refresh_thread.start()
                last = dict(status='BLOCKED' if failure else current['state'], observed_at=now.isoformat(),
                            worker_running=bool(thread and thread.is_alive()),
                            refresh_running=bool(refresh_thread and refresh_thread.is_alive()),
                            recovery_after=current['recovery_after'], error_types=failure)
                save(root / 'supervisor.json', last)
                wait(min(5, max(.1, config.get('poll_seconds', 5))))
        finally:
            stop.set()
            refresh_stop.set()
            if thread:
                thread.join(timeout=config.get('stop_timeout_seconds', 10))
            if refresh_thread:
                refresh_thread.join(timeout=config.get('stop_timeout_seconds', 10))
        return last


def status(config_path, now=None):
    """Receipts/lock only; no network, credentials, DB or implicit store discovery."""
    now = now or datetime.now(timezone.utc)
    config = read(config_path)
    root = Path(config['state_dir'])
    supervisor = read(root / 'supervisor.json')
    running = False
    path = root / '.service.lock'
    if path.exists():
        with path.open('rb') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                running = True
    observed = supervisor.get('observed_at')
    age = (now-aware(observed)).total_seconds() if observed else None
    health = ('UNKNOWN' if not observed else 'STALE' if age < 0 or age > 30
              else 'BLOCKED' if supervisor.get('status') == 'BLOCKED'
              else 'FRESH' if running else 'UNKNOWN')
    stream = read(root/'stream_status.json')
    last_message = stream.get('last_received_at')
    message_age = (now-aware(last_message)).total_seconds() if last_message else None
    market_status = (stream.get('status', 'UNKNOWN')
                     if running and health != 'STALE' and supervisor.get('status') != 'STOPPED' else 'UNKNOWN')
    if market_status == 'LIVE' and (message_age is None or message_age < 0 or message_age > config.get('stale_seconds', 90)):
        market_status = 'STALE'
    chart = read(root/'chart.json')
    chart_coverage = [dict(instrument_key=row['instrument_key'], day=row.get('day'),
                           status=row['status'], latest_completed_minute=row.get('latest_completed_minute'),
                           missing_minutes=len(row.get('missing_minutes', [])),
                           latest_completed_candles={tf: bars[-1]['end'] if bars else None
                                                     for tf, bars in row.get('completed', {}).items()})
                      for row in chart.get('instruments', [])]
    return dict(status=market_status, supervisor_health=health,
                worker_running=running, supervisor=supervisor,
                supervisor_age_seconds=age, stream=stream, last_message_age_seconds=message_age,
                chart_coverage=chart_coverage,
                refresh=read(root/'refresh_status.json'),
                always_on_host_required=True,
                caveat='launchd cannot collect while the Mac sleeps; reconciliation is required on recovery')


def launchagent(python, repo, config_path, digest, log_dir):
    """Return a template only. The caller chooses an output file; never load it."""
    return dict(Label=LABEL, ProgramArguments=[str(python), '-B', str(Path(repo)/'scripts/market-live'),
                'supervise', '--config', str(config_path), '--authorize', digest],
                WorkingDirectory=str(repo), RunAtLoad=True, KeepAlive=True, ThrottleInterval=30,
                LimitLoadToSessionType='Aqua', ProcessType='Background',
                EnvironmentVariables={'PYTHONDONTWRITEBYTECODE': '1'},
                StandardOutPath=str(Path(log_dir)/'market-live.log'),
                StandardErrorPath=str(Path(log_dir)/'market-live.err'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'supervise', 'stop', 'health', 'status', 'refresh-tick', 'launchd-template', 'replay'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--authorize')
    parser.add_argument('--python', default=os.sys.executable)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.action != 'replay' and args.config is None:
        parser.error('--config is required for this action')
    now = datetime.now(timezone.utc)
    if args.action == 'replay':
        if not args.output:
            parser.error('--output is required for the isolated replay')
        from scrapper.market_live.replay import run
        result = run(args.output)
    elif args.action in ('status', 'health'):
        result = status(args.config, now)
    elif args.action in ('start', 'supervise'):
        if args.action == 'start':
            config = authorized_config(args.config, args.authorize, now)
            # Explicit owner invocation resumes the same service; launchd uses
            # supervise and always preserves an operator's retained stop.
            (Path(config['state_dir'])/'stop.request').unlink(missing_ok=True)
        try:
            result = run_service(args.config, args.authorize)
        except BlockingIOError:
            result = dict(status='BUSY', reason='Existing market supervisor retains ownership')
    elif args.action == 'stop':
        config = read(args.config)
        # Graceful request only, never signal a possibly reused PID.
        root = Path(config['state_dir'])
        if root.exists():
            save(root/'stop.request', dict(requested_at=now.isoformat()))
        result = dict(status='STOP_REQUESTED' if root.exists() else 'NOT_RUNNING')
    elif args.action == 'refresh-tick':
        config = authorized_config(args.config, args.authorize, now)
        result = refresh_once(config, now,
            admission_check=lambda: parent_admission(args.config, args.authorize,
                                                      lambda: datetime.now(timezone.utc)))
    else:
        if not args.output:
            parser.error('--output is required for an uninstalled template')
        config = read(args.config)
        repo = Path(__file__).resolve().parents[2]
        agent = launchagent(args.python, repo, args.config.resolve(), args.authorize or 'OWNER_APPROVED_CONFIG_SHA256', config['state_dir'])
        with args.output.open('wb') as out:
            plistlib.dump(agent, out)
        result = dict(status='PREPARED_NOT_INSTALLED', label=LABEL)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
