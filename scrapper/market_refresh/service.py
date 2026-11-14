"""Owner-gated finite refresh tick for the independent market supervisor.

This is source preparation, not an installed timer or an activation grant.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import fcntl
import json
from pathlib import Path

from experiments.historical_candle_archive_v1.access import HistoricalStore
from experiments.historical_candle_archive_v1.storage import Archive, dump
from .derived import collect_coverage, refresh_outputs
from .planner import aware, coverage_report, digest, plan_refresh
from .runner import execute_plan


def load_calendar(path):
    payload = json.loads(Path(path).read_text())
    days = payload['days']
    records = {r['day']:r for r in days} if isinstance(days, list) else days
    if len(records) != len(days):
        raise ValueError('duplicate reviewed calendar date')
    def calendar(day):
        return records.get(str(day), dict(day=str(day), verified=False, status='UNKNOWN', sessions=[]))
    return calendar


def _receipts(output):
    return [json.loads(p.read_text()) for p in sorted((output/'receipts').glob('*.json'))]


def _official_coverage(companies, saved, receipts):
    """Merge validated index receipts, invalidating dates on later bad revisions."""
    result = {}
    for company in companies:
        key = company['instrument_key']
        if company.get('kind') != 'NSE_INDEX':
            continue
        record = dict(saved.get(key, {}))
        valid = set(record.get('validated_sessions', []))
        evidence = []
        for rec in sorted((r for r in receipts if r['series'] == key), key=lambda r:r.get('received_at', '')):
            valid.difference_update(rec.get('invalid_sessions', []))
            valid.update(rec.get('validated_sessions', []))
            if rec.get('sha256'):
                evidence.append(dict(sha256=rec['sha256'], received_at=rec['received_at'], request_id=rec['request_id']))
            record.update(last_refresh_attempt=rec.get('attempt_started_at'), last_result=rec['status'])
        record.update(validated_sessions=sorted(valid), raw_response_evidence=evidence)
        result[key] = record
    return result


def _refresh_unlocked(config_path, now, *, authorized_sha256=None, store_factory=HistoricalStore,
            archive_factory=Archive, token_supplier=None, fetch=None, sleep=None, clock=None,
            admission_check=None):
    """One bounded tick; config hash/approval/expiry checked before any store open.

    Config keys: companies, calendar_path, state_dir, max_requests (default 100),
    lookback_days (10), owner_approved, activation_ref, expires_at. Optional
    derived_output/derived_start/groups/benchmark/actions enable full-window
    recomputation. Calendar and company identity evidence must be independently
    reviewed before activation; a hash does not create owner authorization.
    """
    raw = Path(config_path).read_bytes()
    if authorized_sha256 != hashlib.sha256(raw).hexdigest():
        raise PermissionError('exact owner-approved refresh configuration required')
    config = json.loads(raw)
    at = aware(now)
    if (config.get('owner_approved') is not True or not config.get('activation_ref')
            or not config.get('expires_at') or aware(config['expires_at']) <= at):
        raise PermissionError('refresh activation grant missing or expired')
    calendar = load_calendar(config['calendar_path'])
    # Bind calendar bytes, independently of mutable path contents.
    if config.get('calendar_sha256') != hashlib.sha256(Path(config['calendar_path']).read_bytes()).hexdigest():
        raise PermissionError('reviewed calendar hash mismatch')
    companies = config['companies']
    if not config.get('provider_quota_path'):
        raise ValueError('explicit shared provider_quota_path required')
    output = Path(config['state_dir'])
    lookback = config.get('lookback_days', 10)
    existing = json.loads((output/'coverage-state.json').read_text()) if (output/'coverage-state.json').exists() else {}
    receipts = _receipts(output)
    store = store_factory()
    try:
        coverage = collect_coverage(store, companies, calendar=calendar, now=at,
                                    lookback_days=lookback, receipts=receipts)
    finally:
        store.close()
    coverage.update(_official_coverage([c for c in companies if c.get('kind') == 'NSE_INDEX'], existing, receipts))
    plan = plan_refresh(companies, coverage, calendar=calendar, now=at,
                        budget=config.get('max_requests', 100), lookback_days=lookback,
                        history_idle=config.get('history_idle', False), historical_gaps=config.get('historical_gaps'))
    if token_supplier is None:
        from experiments.earnings_reaction_v1.execute_ta02d import secure_supplier
        token_supplier = secure_supplier
    clock = clock or (lambda: datetime.now(timezone.utc))
    def still_authorized():
        if hashlib.sha256(Path(config_path).read_bytes()).hexdigest() != authorized_sha256:
            return False
        if hashlib.sha256(Path(config['calendar_path']).read_bytes()).hexdigest() != config['calendar_sha256']:
            return False
        if aware(config['expires_at']) <= aware(clock()):
            return False
        return admission_check is None or admission_check()
    kwargs = dict(calendar=calendar, token_supplier=token_supplier, archive_factory=archive_factory,
                  clock=clock, quota_path=config['provider_quota_path'], admission_check=still_authorized)
    if fetch is not None:
        kwargs['fetch'] = fetch
    if sleep is not None:
        kwargs['sleep'] = sleep
    run = execute_plan(plan, output, authorized_sha256=digest(plan), **kwargs)
    receipts = _receipts(output)
    store = store_factory()
    try:
        fresh = collect_coverage(store, companies, calendar=calendar, now=at,
                                 lookback_days=lookback, receipts=receipts)
        fresh.update(_official_coverage([c for c in companies if c.get('kind') == 'NSE_INDEX'], coverage, receipts))
        derived = None
        explorer = None
        if config.get('derived_output') and config.get('derived_start') and run['status'] == 'COMPLETE':
            last = max((r['expected_latest_completed_session'] for r in plan['coverage']
                        if r['expected_latest_completed_session']), default=None)
            if last:
                derived = refresh_outputs(store, companies, calendar=calendar, now=at,
                    output=config['derived_output'], start=config['derived_start'], end=last,
                    groups=config.get('groups', []), benchmark=config.get('benchmark'), actions=config.get('actions', []))
        if config.get('index_export_output') and run['status']=='COMPLETE':
            from .explorer import archive_snapshot, publish
            last = max((r['expected_latest_completed_session'] for r in plan['coverage']
                        if r['expected_latest_completed_session']), default=None)
            if last:
                snapshot=dict(archive=archive_snapshot(store,config.get('index_start') or config['derived_start'],last),
                              official_receipts=receipts)
                explorer=publish(config,snapshot,calendar=calendar,now=at,end=last,incremental_state=output)
    finally:
        store.close()
    reports = [coverage_report(c, fresh.get(c['instrument_key'], {}), calendar=calendar, now=at,
                               lookback_days=lookback) for c in companies]
    result = dict(status=run['status'], reason=run.get('reason'), observed_at=at.isoformat(),
                  coverage=reports, counts=dict(Counter(r['status'] for r in reports)),
                  missing_sessions=sum(len(r['missing_sessions']) for r in reports),
                  deferred_requests=plan['deferred_requests'],
                  derived_build=derived['build'] if derived else None, explorer=explorer,
                  historical_acquisition='SEPARATE_OWNER;NO_BACKFILL_STARTED')
    dump(output/'coverage-state.json', fresh)
    dump(output/'status.json', result)
    return result


def refresh(config_path, now, *, authorized_sha256=None, store_factory=HistoricalStore,
            archive_factory=Archive, token_supplier=None, fetch=None, sleep=None, clock=None,
            admission_check=None):
    """Serialize the entire read-plan-run-derive-publication tick, independently
    of the short shared archive owner and the runner's request admission lock.
    """
    raw = Path(config_path).read_bytes()
    if authorized_sha256 != hashlib.sha256(raw).hexdigest():
        raise PermissionError('exact owner-approved refresh configuration required')
    config = json.loads(raw)
    at = aware(now)
    if (config.get('owner_approved') is not True or not config.get('activation_ref')
            or not config.get('expires_at') or aware(config['expires_at']) <= at):
        raise PermissionError('refresh activation grant missing or expired')
    output = Path(config['state_dir'])
    output.mkdir(parents=True, exist_ok=True)
    with (output/'.refresh-service.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(status='BLOCKED', reason='REFRESH_SERVICE_BUSY')
        return _refresh_unlocked(config_path, now, authorized_sha256=authorized_sha256,
            store_factory=store_factory, archive_factory=archive_factory, token_supplier=token_supplier,
            fetch=fetch, sleep=sleep, clock=clock, admission_check=admission_check)
