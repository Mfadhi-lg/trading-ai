"""Acquisition-only IST admission; existing scheduler clock, no backlog dispatch."""
from datetime import datetime, timedelta, timezone
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from scrapper.sources.exchange.reporting_periods import IST, priority_result_periods

STATE = ROOT / 'artifacts/automatic-acquisition'
RECEIPTS = ROOT / 'artifacts/bse-browser-results'
LABEL = 'com.trading-ai.automatic-acquisition'
PYTHON = '/Users/mohamedfadhi/.cache/uv/archive-v0/-WyGD_mChAFVX11K/bin/python'
SITE_PACKAGES = '/Users/mohamedfadhi/.cache/uv/archive-v0/4GTYw8_mNnscx89r/lib/python3.13/site-packages'


class SlotConsumed(Exception):
    """A retained attempt receipt suppresses access without being a new failure."""


def launchagent():
    return {'Label': LABEL, 'ProgramArguments': [PYTHON, str(Path(__file__).resolve()), '--tick'],
        'WorkingDirectory': str(ROOT), 'StartInterval': 60, 'RunAtLoad': True,
        'LimitLoadToSessionType': 'Aqua', 'ProcessType': 'Interactive',
        'EnvironmentVariables': {'PATH': '/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
            'PYTHONPATH': SITE_PACKAGES, 'PYTHONDONTWRITEBYTECODE': '1'},
        'StandardOutPath': str(STATE / 'launchd.log'),
        'StandardErrorPath': str(STATE / 'launchd.err')}


def document_jobs():
    from phase4_schedule import jobs as document_schedule_jobs
    return document_schedule_jobs()


def jobs():
    from apscheduler.triggers.cron import CronTrigger
    from scrapper.workflows.exchange.schedule import LADDER
    # Reuse the existing primary rung. Later retry/catch-up rungs stay disabled.
    hour, minute = map(int, LADDER[0].ist_time.split(':'))
    from step3_schedule import jobs as step3_jobs
    return [('nse-primary', 'NSE', CronTrigger(day_of_week='mon-sat', hour=hour, minute=minute, timezone=IST)),
        ('bse-morning', 'BSE', CronTrigger(hour=9, minute=0, timezone=IST)),
        ('bse-evening', 'BSE', CronTrigger(hour=18, minute=0, timezone=IST))] + step3_jobs() + document_jobs()


def due(now):
    if now.tzinfo is None:
        raise ValueError('Aware scheduler clock required')
    current = now.astimezone(IST)
    # Only the current minute, never replay missed days/slots after sleep/login.
    start = current.replace(second=0, microsecond=0)
    return [(name, exchange, start) for name, exchange, trigger in jobs()
            if trigger.get_next_fire_time(None, start) == start]


def save(path, data):
    path.write_text(json.dumps(data, indent=2, default=str) + '\n')


def verify_config():
    manifest = json.loads((HERE / 'automatic-acquisition-manifest.json').read_text())
    for name, digest in manifest['files'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('Scheduled acquisition source/config drift: ' + name)
    return manifest


def desktop_check():
    import pwd
    owner = os.stat('/dev/console').st_uid
    if owner != os.getuid() or pwd.getpwuid(owner).pw_name in ('root', 'loginwindow'):
        raise RuntimeError('Visible Chrome requires this user to have an active logged-in desktop session')
    subprocess.run(['launchctl', 'print', 'gui/' + str(os.getuid())], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=False)
        try:
            page = browser.new_page()
            page.set_content('<title>Trading AI desktop verification</title>Visible Chrome scheduling check')
            if page.title() != 'Trading AI desktop verification':
                raise RuntimeError('Visible Chrome desktop verification failed')
        finally:
            browser.close()
    return {'status': 'PASS', 'channel': 'chrome', 'headless': False,
            'active_desktop_session_required': True, 'network_requests': 0}


def bse_once(out, preflight=False):
    import nse_results as nse
    from production_ownership import owner_gate
    from scrapper.research.bse_browser_results import daily_fallback
    permission = json.loads((HERE / 'scheduled-bse-permission.json').read_text())
    if not permission.get('scheduled_browser_access'):
        raise RuntimeError('Scheduled BSE permission is not confirmed')
    if not preflight:
        current = datetime.now(IST)
        day = current.date().isoformat()
        slot = 'morning' if current.hour < 12 else 'evening'
        if any((RECEIPTS / name).exists() for name in (day + '.json', day + '-' + slot + '.json')):
            raise SlotConsumed('Retained BSE daily/slot receipt already consumes this access budget')
    with (HERE / '.nse-owner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        nse.check_configuration()
        save(out / 'owner-before.json', owner_gate())
        if not preflight:
            desktop_check()
        manifest = json.loads((ROOT / 'artifacts/bse-production-ingestion-ownership-fixed-2026-10-09/manifest.json').read_text())
        image = json.loads(nse.checked(['docker', 'image', 'inspect', manifest['operation_image']]))[0]
        nse.require(image['Id'] == manifest['operation_image'], 'Existing BSE application image changed')
        volume = json.loads(nse.checked(['docker', 'volume', 'inspect', nse.VOLUME]))[0]
        nse.require(volume['Labels'].get('com.docker.compose.project') == 'trading-ai', 'Volume identity changed')
        if preflight:
            evidence = ROOT / 'artifacts/bse-visible-runner-verification-2026-10-09'
            for source, target in (('receipts/2026-10-09-morning.json', 'receipt.json'),
                    ('receipts/2026-10-09-morning.raw', 'response.raw'), ('verification.json', 'verification.json')):
                shutil.copyfile(evidence / source, out / target)
        else:
            capture_bse(out, permission, daily_fallback)
        application_hashes=dict(manifest['application_hashes'])
        application_hashes['scrapper/sources/exchange/result_filings.py']=hashlib.sha256((ROOT/'scrapper/sources/exchange/result_filings.py').read_bytes()).hexdigest()
        save(out / 'checks.json', {'application_hashes': application_hashes,
            'operator_hashes': {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in
                ('nse_results_runtime.py', 'bse_scheduled_runtime.py', 'production_ownership.py', 'sqlite_sidecar_guard.py')}})
        return ingest_bse_capture(out, image['Id'], preflight=preflight)


def capture_bse(out, permission, daily_fallback):
    import nse_results as nse
    started = datetime.now(timezone.utc)
    period = priority_result_periods(now=started)[0]
    # One page attempt. Reuse the original permanent slot receipts.
    try:
        result = daily_fallback(receipt_dir=RECEIPTS, consent_evidence=permission['evidence'], result_period=period)
    except FileExistsError as exc:
        raise SlotConsumed(str(exc)) from exc
    finished = datetime.now(timezone.utc)
    nse.require(result.parsed is not None, 'BSE browser failed: ' + result.reason)
    local = started.astimezone(IST)
    stem = local.date().isoformat() + ('-morning' if local.hour < 12 else '-evening')
    shutil.copyfile(RECEIPTS / (stem + '.json'), out / 'receipt.json')
    shutil.copyfile(RECEIPTS / (stem + '.raw'), out / 'response.raw')
    receipt = json.loads((out / 'receipt.json').read_text())
    save(out / 'verification.json', {'started_at_utc': started.isoformat(), 'finished_at_utc': finished.isoformat(),
        'raw_sha256': hashlib.sha256(result.response.content).hexdigest(), 'raw_bytes': len(result.response.content),
        'response': receipt['response'], 'browser_result': result.reason, 'result_period': period})
    nse.require(len(result.response.content) <= 10 * 1024 * 1024, 'Browser response byte budget exceeded')


def ingest_bse_capture(out, image_id, preflight=False):
    from production_ownership import owner_gate
    compose = json.loads((HERE / 'compose.nse.json').read_text())
    service = compose['services']['nse-results']
    service.update(image=image_id, entrypoint=['python', '/activation/bse_scheduled_runtime.py'], profiles=['bse'])
    if preflight:
        service['command'] = ['--verify-only']
    # Resolve relative paths before storing a transient compose in evidence.
    for mount in service['volumes']:
        if mount['type'] == 'bind':
            mount['source'] = str(HERE)
    service['volumes'].append({'type': 'bind', 'source': str(out), 'target': '/input', 'read_only': True})
    policy='scrapper/sources/exchange/result_filings.py'
    service['volumes'].append({'type':'bind','source':str(ROOT/policy),'target':'/app/'+policy,
        'read_only':True,'bind':{'create_host_path':False}})
    identity = json.loads((HERE / 'runtime-identity-nse-results.json').read_text())
    identity.update(image_id=image_id, release_id='bse-scheduled-20261009')
    save(out / 'identity.json', identity)
    for name, path in (('store', '/data'), ('snapshots', '/data/snapshots')):
        save(out / (name + '.json'), {'environment': 'production', 'path': path,
            'release_id': identity['release_id'], 'roles': ['store' if name == 'store' else 'documents']})
    compose['configs'] = {'nse_identity': {'file': str(out / 'identity.json')},
        'nse_store': {'file': str(out / 'store.json')}, 'nse_snapshots': {'file': str(out / 'snapshots.json')}}
    save(out / 'compose.json', compose)
    command = ['docker', 'compose', '-f', str(out / 'compose.json'), '--profile', 'bse',
        'run', '--rm', '--no-deps', '--name', 'trading-ai-bse-scheduled', 'nse-results']
    # Captured browser evidence is the only transport in the writer container.
    service['network_mode'] = 'none'
    save(out / 'compose.json', compose)
    with (out / 'runtime.log').open('w') as log:
        executed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    save(out / 'owner-after.json', owner_gate())
    return executed.returncode


def run_slot(name, exchange, at, execute=None):
    STATE.mkdir(parents=True, exist_ok=True)
    day = at.astimezone(IST).date().isoformat()
    slot_suffix='-'+at.astimezone(IST).strftime('%H%M') if exchange in ('FINANCIAL','CAP') or exchange.startswith('DOCUMENT_') else ''
    out = STATE / (day + '-' + name + slot_suffix)
    if out.exists():
        return {'job': name, 'status': 'slot_already_consumed', 'path': str(out)}
    out.mkdir()  # Atomic durable admission: crash/failure consumes this slot.
    priorities=priority_result_periods(now=at)
    selected_periods=(priorities[0],) if exchange=='FINANCIAL' else () if exchange=='CAP' else priorities
    from financial_priority import historical_slot
    save(out / 'admission.json', {'job': name, 'exchange': exchange, 'scheduled_at_ist': at,
        'dispatch_origin': 'launchd' if os.environ.get('XPC_SERVICE_NAME') == LABEL else 'manual_tick',
        'started_at_utc': datetime.now(timezone.utc), 'periods': selected_periods,
        'automatic_retry': False, 'historical_processing': exchange=='FINANCIAL' and historical_slot(at),
        'financial_selection_policy':'three-priority-v1' if exchange=='FINANCIAL' else None})
    try:
        verify_config()
        if (STATE / (exchange + '.stopped.json')).exists():
            raise RuntimeError(exchange + ' is stopped after a prior failure; operator resolution required')
        if execute is not None:
            code = execute(exchange, out)
        elif exchange == 'NSE':
            with (out / 'runtime.log').open('w') as log:
                code = subprocess.run([sys.executable, str(HERE / 'nse_results.py'), '--execute'],
                    stdout=log, stderr=subprocess.STDOUT).returncode
        elif exchange.startswith('DOCUMENT_'):
            from phase4_schedule import execute_once
            code=execute_once(exchange,out)
        elif exchange in ('FINANCIAL','CAP'):
            from step3_schedule import execute_once
            code=execute_once(exchange,out)
        else:
            code = bse_once(out)
        payload = {'job': name, 'exchange': exchange, 'status': 'PASS' if code == 0 else 'STOP',
            'exit_code': code, 'finished_at_utc': datetime.now(timezone.utc), 'automatic_retry': False}
    except SlotConsumed as exc:
        payload = {'job': name, 'exchange': exchange, 'status': 'SKIPPED_ALREADY_ATTEMPTED',
            'reason': str(exc), 'finished_at_utc': datetime.now(timezone.utc), 'automatic_retry': False}
    except Exception as exc:
        transient=isinstance(exc,BlockingIOError) or any(marker in str(exc) for marker in ('Cannot inspect ownership: docker','Exchange acquisition window reserved'))
        payload = {'job': name, 'exchange': exchange, 'status': 'DEFERRED_ENVIRONMENT' if transient else 'STOP', 'error': str(exc),
            'finished_at_utc': datetime.now(timezone.utc), 'automatic_retry': False}
    save(out / 'outcome.json', payload)
    summary = summarize_runtime(out)
    save(out / 'acquisition-summary.json', summary)
    if payload['status'] == 'STOP':
        if not (STATE / (exchange + '.stopped.json')).exists():
            save(STATE / (exchange + '.stopped.json'), payload)
        print(json.dumps(payload, default=str), file=sys.stderr, flush=True)
    return payload | {'path':str(out)}


def summarize_runtime(out):
    records = []
    log = out / 'runtime.log'
    for line in log.read_text().splitlines() if log.exists() else ():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get('stage') == 'outcome':
            result = entry['result']
            records.append({'query_period': entry['query_period'],
                'source_rows': result.get('accepted_row_count', 0),
                'inserted_filings': (result.get('filings') or {}).get('stored', 0),
                'filing_decisions': result.get('filings', {}),
                'queue_decisions': result.get('enqueue', {}),
                'snapshot_id': result.get('snapshot_id'), 'fetch_id': result.get('fetch_id')})
        elif isinstance(entry,dict) and entry.get('stage') == 'document_stage_outcome':
            records.append({'stage':entry['stage'],'result':entry,'source_rows':0,'inserted_filings':0,'queue_decisions':{}})
        elif isinstance(entry,dict) and entry.get('stage') in ('financial_batch_outcome','scope_monitor_outcome','financial_scheduled_idle'):
            records.append({'stage':entry['stage'],'result':entry.get('result'),
                'source_rows':0,'inserted_filings':0,'queue_decisions':{},'current_period':entry.get('current_period')})
    return {'periods': records, 'source_rows': sum(r['source_rows'] for r in records),
        'inserted_filings': sum(r['inserted_filings'] for r in records),
        'automatic_retry': False, 'runtime_evidence': str(log)}


def tick(now=None):
    STATE.mkdir(parents=True, exist_ok=True)
    with (HERE / '.scheduler-admission.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy'}
        verify_config()
        if (STATE / 'verify-desktop-once').exists():
            (STATE / 'verify-desktop-once').unlink()
            result = desktop_check()
            result['verification_environment'] = 'installed Aqua LaunchAgent'
            save(STATE / 'desktop-launchagent-verification.json', result)
            return result
        results = [run_slot(*item) for item in due(now or datetime.now(IST))]
        historical_claims=0
        for entry in results:
            log=Path(entry.get('path','') or STATE)/'runtime.log'
            if log.is_file():
                for line in log.read_text().splitlines():
                    try:record=json.loads(line)
                    except ValueError:continue
                    if record.get('stage')=='priority_financial_selection':historical_claims+=record.get('priorities',[]).count(3)
        result = {'status': 'STOP' if any(r['status'] == 'STOP' for r in results) else 'checked', 'checked_at_utc': datetime.now(timezone.utc),
            'runs': results, 'historical_tasks_dispatched': historical_claims}
        save(STATE / 'last-tick.json', result)
        from verify_unattended_acquisition import audit_existing_runs
        audit_existing_runs()
        from monitor_phase4_receipts import audit as audit_documents
        audit_documents()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tick', action='store_true')
    parser.add_argument('--desktop-check', action='store_true')
    parser.add_argument('--prepare-plist', action='store_true')
    parser.add_argument('--bse-preflight', action='store_true')
    args = parser.parse_args()
    if args.prepare_plist:
        STATE.mkdir(parents=True, exist_ok=True)
        (HERE / (LABEL + '.plist')).write_bytes(plistlib.dumps(launchagent()))
        result = {'status': 'prepared'}
    elif args.desktop_check:
        result = desktop_check()
        save(STATE / 'desktop-verification.json', result)
    elif args.tick:
        result = tick()
    elif args.bse_preflight:
        verify_config()
        out = STATE / datetime.now(timezone.utc).strftime('bse-preflight-%Y%m%dT%H%M%S%fZ')
        out.mkdir(parents=True)
        code = bse_once(out, preflight=True)
        result = {'status': 'PASS' if code == 0 else 'STOP', 'exit_code': code, 'path': str(out),
            'production_ingestion': False, 'source_requests': 0}
        save(out / 'outcome.json', result)
        if code:
            raise RuntimeError('BSE production preflight failed; see ' + str(out))
    else:
        from step3_schedule import settings
        config=settings()
        result = {'timezone': str(IST), 'jobs': [{'job': n, 'exchange': e, 'trigger': str(t)} for n, e, t in jobs()],
            'calendar_catchup': False, 'slot_replay': False, 'broad_workers_enabled': False,
            'financial_enabled':config['financial_enabled'],'cap_enabled':config['cap_enabled']}
    print(json.dumps(result, default=str), flush=True)
    if result.get('status') == 'STOP':
        sys.exit(2)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        STATE.mkdir(parents=True, exist_ok=True)
        failure = {'status': 'STOP', 'error': str(exc), 'automatic_retry': False,
            'failed_at_utc': datetime.now(timezone.utc)}
        save(STATE / 'scheduler-failure.json', failure)
        print(json.dumps(failure, default=str), file=sys.stderr, flush=True)
        sys.exit(2)
