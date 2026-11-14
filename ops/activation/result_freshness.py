"""Separately authorized bounded result polling; never widens dispatcher grants.

Invoke plan/status without network. A tick needs an exact configuration grant,
an explicit execution flag and the existing finite acquisition owner lock.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, time, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def load_config(path):
    raw = Path(path).read_bytes()
    config = json.loads(raw)
    if config.get('schema_version') != 1 or type(config.get('enabled')) is not bool:
        raise ValueError('Invalid result polling configuration')
    if config.get('cadence_minutes') not in (5, 10, 15):
        raise ValueError('Polling cadence must be 5, 10 or 15 minutes')
    if type(config.get('max_new_queue_entries')) is not int or not 1 <= config['max_new_queue_entries'] <= 100:
        raise ValueError('Financial admission must be bounded to 1–100')
    start, end = time.fromisoformat(config['monitored_start_ist']), time.fromisoformat(config['monitored_end_ist'])
    if start >= end or start.tzinfo or end.tzinfo:
        raise ValueError('A same-day monitored IST window is required')
    sources = config.get('sources', {})
    if not sources or set(sources) - {'nse_financial_results', 'bse_financial_results'}:
        raise ValueError('Only canonical official result datasets may be polled')
    for policy in sources.values():
        if (type(policy.get('requests_max_per_day')) is not int or
                not 1 <= policy['requests_max_per_day'] <= 288 or
                not isinstance(policy.get('source_policy_evidence'), str) or
                not policy['source_policy_evidence'].strip()):
            raise ValueError('Each source needs a finite reviewed request budget and evidence')
    for key in ('db_path', 'snapshot_root', 'state_dir', 'acquisition_owner_lock'):
        if not Path(config[key]).is_absolute():
            raise ValueError('Explicit absolute paths required: ' + key)
    return config, hashlib.sha256(raw).hexdigest()


def plan(config, digest, *, now):
    from scrapper.sources.exchange.reporting_periods import IST, priority_result_periods
    if now.tzinfo is None:
        raise ValueError('Aware polling clock required')
    local = now.astimezone(IST)
    window = (time.fromisoformat(config['monitored_start_ist']) <= local.time().replace(tzinfo=None)
              < time.fromisoformat(config['monitored_end_ist']))
    slot = local.replace(second=0, microsecond=0)
    due = window and local.minute % config['cadence_minutes'] == 0
    periods = priority_result_periods(now=now)[:2]
    return {'schema_version': 1, 'configuration_sha256': digest,
            'status': 'PAUSED' if not config['enabled'] else 'DUE' if due else 'IDLE',
            'slot_ist': slot.isoformat(), 'day_ist': slot.date().isoformat(),
            'requests': [{'dataset': dataset, 'result_period': period}
                         for dataset in sorted(config['sources']) for period in periods],
            'live_detection_target_verified': False}


def verify_grant(grant_path, digest, *, now):
    from scrapper.documents.availability import parse_utc
    if grant_path is None:
        raise RuntimeError('An independently approved result-polling owner grant is required')
    grant = json.loads(Path(grant_path).read_bytes())
    if (grant.get('configuration_sha256') != digest or grant.get('authorized') is not True
            or not isinstance(grant.get('owner_authorization_evidence'), str)
            or not grant['owner_authorization_evidence'].strip()):
        raise RuntimeError('An exact independently approved result-polling owner grant is required')
    if not parse_utc(grant['starts_at_utc']) <= now.astimezone(timezone.utc) < parse_utc(grant['expires_at_utc']):
        raise RuntimeError('Result-polling authorization is outside its finite window')
    return grant


@contextmanager
def owner_lock(path):
    # Require the existing dispatcher lock, never silently substitute a new one.
    with Path(path).open('r+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_once(path, payload):
    with path.open('x') as stream:
        json.dump(payload, stream, sort_keys=True, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def shared_fetcher(conn, clock, check=lambda: None):
    """One bounded official GET per reservation, through canonical host permits."""
    from types import SimpleNamespace
    from dataclasses import dataclass
    from scrapper.config import USER_AGENT
    from scrapper.core.http import FetchResponse
    from scrapper.documents.downloader import DownloadPolicy
    from scrapper.operations.workers.host_requests import HostSession
    from scrapper.sources.exchange.transport import validate_exchange_dataset_url, _classify_exchange_response
    @dataclass(frozen=True)
    class ReceivedExchangeResponse(FetchResponse):
        received_at_utc: str
    def fetch(url, exchange):
        validate_exchange_dataset_url(url, exchange)
        policy = DownloadPolicy(max_bytes=2 * 1024 * 1024, total_timeout_seconds=45, max_redirects=0)
        with HostSession(conn, mode='generic', owner='fresh-result-poll', policy=policy,
                         check=check, wait_seconds=0) as session:
            response = session.get(url, allow_redirects=False, stream=True, timeout=(10, 20),
                headers={'User-Agent': USER_AGENT, 'Referer': 'https://www.nseindia.com/' if exchange == 'NSE'
                         else 'https://www.bseindia.com/corporates/comp_results.aspx'})
            try:
                body = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    body.extend(chunk)
                    if len(body) > policy.max_bytes:
                        raise ValueError('Official result response exceeds bounded byte budget')
                raw = bytes(body)
                status = _classify_exchange_response(SimpleNamespace(status_code=response.status_code,
                    headers=response.headers, content=raw))
                return ReceivedExchangeResponse(url, response.url, response.status_code, raw,
                    response.headers.get('Content-Type'), None, response.headers.get('ETag'),
                    response.headers.get('Last-Modified'), 'public', status, clock().isoformat())
            finally:
                response.close()
    return fetch


def tick(config_path, grant_path, *, execute=False, now=None, fetcher=None, workflow=None, clock=None):
    config, digest = load_config(config_path)
    now = now or datetime.now(timezone.utc)
    clock = clock or (lambda: datetime.now(timezone.utc))
    proposed = plan(config, digest, now=now)
    if proposed['status'] != 'DUE':
        return proposed
    if not execute:
        return {**proposed, 'status': 'AWAITING_EXPLICIT_EXECUTION'}
    verify_grant(grant_path, digest, now=now)
    from scrapper.db.database import connect, check_schema_compatibility
    from scrapper.db.exchange_repo import active_source_access_holds
    from scrapper.operations.coordinator.exchange import ownership
    if workflow is None:
        from scrapper.workflows.exchange.universe import discover_exchange_results
        workflow = discover_exchange_results
    results = []
    with owner_lock(config['acquisition_owner_lock']) as acquired:
        if not acquired:
            return {**proposed, 'status': 'ACQUISITION_BUSY'}
        conn = connect(Path(config['db_path']))
        try:
            check_schema_compatibility(conn)
            with ownership(config['db_path'], 'executor') as acquired:
                if not acquired:
                    return {**proposed, 'status': 'ACQUISITION_BUSY'}
                state = Path(config['state_dir'])
                state.mkdir(parents=True, exist_ok=True)
                holds = active_source_access_holds(conn)
                for request in proposed['requests']:
                    dataset = request['dataset']
                    key = hashlib.sha256((proposed['slot_ist'] + dataset + request['result_period']).encode()).hexdigest()[:24]
                    receipt = state / (proposed['day_ist'] + '-' + key + '.reserved.json')
                    blocked = state / (dataset + '.blocked.json')
                    if dataset in holds or blocked.exists():
                        results.append({**request, 'status': 'BLOCKED_SOURCE_ACCESS'})
                        continue
                    if receipt.exists():
                        results.append({**request, 'status': 'SLOT_CONSUMED',
                                        'outcome_retained': receipt.with_suffix('.result.json').exists()})
                        continue
                    uncertain = any(json.loads(path.read_bytes())['dataset'] == dataset and
                        not path.with_suffix('.result.json').exists() for path in state.glob('*.reserved.json'))
                    if uncertain:
                        results.append({**request, 'status': 'BLOCKED_UNCERTAIN_DELIVERY'})
                        continue
                    used = sum(json.loads(path.read_bytes())['dataset'] == dataset
                               for path in state.glob(proposed['day_ist'] + '-*.reserved.json'))
                    if used >= config['sources'][dataset]['requests_max_per_day']:
                        results.append({**request, 'status': 'QUOTA_EXHAUSTED'})
                        continue
                    reservation = {**request, 'slot_ist': proposed['slot_ist'],
                                   'configuration_sha256': digest, 'reserved_at_utc': now.isoformat(),
                                   'uncertain_delivery_consumes_slot': True}
                    write_once(receipt, reservation)
                    try:
                        verify_grant(grant_path, digest, now=clock())
                        result = workflow(dataset=dataset, result_period=request['result_period'],
                            effective_date=proposed['day_ist'], db_path=Path(config['db_path']),
                            snapshot_root=Path(config['snapshot_root']), now_utc=lambda: clock().isoformat(),
                            fetcher=fetcher or shared_fetcher(conn, clock,
                                lambda: verify_grant(grant_path, digest, now=clock())),
                            max_new_queue_entries=config['max_new_queue_entries'])
                    except Exception as exc:
                        # Retain consumed delivery; no automatic retry and no URL/token logging.
                        result = {'status': 'BLOCKED', 'error_type': type(exc).__name__}
                    if result.get('validation', {}).get('status') == 'failed':
                        result = {**result, 'status': 'BLOCKED', 'error_type': 'SourceValidationFailed'}
                    write_once(receipt.with_suffix('.result.json'), result)
                    if result.get('status') == 'BLOCKED':
                        write_once(blocked, {'dataset': dataset, 'blocked_at_utc': now.isoformat(),
                            'error_type': result['error_type'], 'owner_review_required': True})
                    results.append({**request, 'status': result.get('status', 'OBSERVED')})
                state = ('BLOCKED' if any(r['status'].startswith('BLOCKED') for r in results) else
                         'GAP' if any(r['status'] == 'QUOTA_EXHAUSTED' for r in results) else 'OBSERVED')
                return {**proposed, 'status': state, 'results': results}
        finally:
            conn.close()


def launchagent(config_path, grant_path):
    """Return a reviewable template only; never install or load launchd state."""
    config, _ = load_config(config_path)
    if grant_path is None:
        raise ValueError('An explicit future owner grant path is required')
    state = Path(config['state_dir'])
    return {'Label':'com.trading-ai.result-freshness',
        'ProgramArguments':[sys.executable, '-B', str(Path(__file__).resolve()), 'tick',
            '--config',str(Path(config_path).resolve()), '--owner-grant',str(Path(grant_path).resolve()), '--execute'],
        'WorkingDirectory':str(ROOT), 'StartInterval':60, 'RunAtLoad':True,
        'LimitLoadToSessionType':'Aqua', 'ProcessType':'Background',
        'StandardOutPath':str(state/'launchd.log'), 'StandardErrorPath':str(state/'launchd.err')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'status', 'tick', 'launchd-template'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--owner-grant', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.command == 'launchd-template':
        import plistlib
        print(plistlib.dumps(launchagent(args.config,args.owner_grant)).decode(),end='')
        return
    if args.command == 'tick':
        result = tick(args.config, args.owner_grant, execute=args.execute)
    else:
        config, digest = load_config(args.config)
        result = plan(config, digest, now=datetime.now(timezone.utc))
        if args.command == 'status':
            state = Path(config['state_dir'])
            result['blocked_sources'] = [path.name.removesuffix('.blocked.json') for path in state.glob('*.blocked.json')]
            result['requests_reserved_today'] = sum(1 for _ in state.glob(result['day_ist']+'-*.reserved.json'))
            result['timing_status'] = 'UNKNOWN_WITHOUT_RETAINED_FILING_OBSERVATIONS'
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
