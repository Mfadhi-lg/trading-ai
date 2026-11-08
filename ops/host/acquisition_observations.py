"""Read-only acquisition observations shared by the terminal commands.

No durable state, schedule or queue is owned by the terminal monitor.
"""
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops/host'), str(ROOT / 'ops/activation'), str(ROOT)]
import acquisition_lifecycle as lifecycle


def now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(path):
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.exists() else None


def fact(state, source, updated_at, data=None, **extra):
    return dict(state=state, source=source, updated_at=updated_at, data=data, **extra)


def redact(value):
    secrets = []
    for path in (ROOT / '.screener_session', ROOT / '.screener_csrf'):
        if path.is_file():
            secret = path.read_text().strip()
            if secret:
                secrets.append(secret)
    def clean(item):
        if isinstance(item, dict):
            return {k: '[REDACTED]' if k.lower() in ('sessionid','csrftoken','cookie','password','api_key','screener_session','screener_csrf') or (k.lower()=='authorization' and isinstance(v,str)) else clean(v) for k,v in item.items()}
        if isinstance(item, (tuple,list)):
            return [clean(v) for v in item]
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, '[REDACTED]')
            item = re.sub(r'(?i)(authorization[\" :]+)(?:Bearer|Basic)\s+[^\s\",;]+', r'\1[REDACTED]', item)
            item = re.sub(r'(?i)(sessionid|csrftoken|cookie|password|api_key)([ :=]+)[^\s,;]+', r'\1\2[REDACTED]', item)
            return re.sub(r'(https?://[^\s?]+)\?[^\s]+', r'\1?[REDACTED]', item)
        return item
    return clean(value)


def history(limit=100, offset=0):
    result = []
    for path in lifecycle.dispatcher.STATE.glob('*/admission.json'):
        try:
            admission = lifecycle.read(path)
            outcome_path = path.with_name('outcome.json')
            outcome = lifecycle.read(outcome_path) if outcome_path.exists() else {}
            started = admission.get('started_at_utc')
            finished = outcome.get('finished_at_utc')
            duration = None
            if started and finished:
                duration = max(0, (datetime.fromisoformat(finished)-datetime.fromisoformat(started)).total_seconds())
            result.append(dict(id=path.parent.name, job=admission.get('job'), exchange=admission.get('exchange'),
                state=({'PASS':'completed','IDLE':'idle','DEFERRED_ENVIRONMENT':'blocked','EXPIRED_AUTHORIZATION':'blocked','BLOCKED_SOURCE_POLICY':'blocked','SKIPPED_ALREADY_ATTEMPTED':'idle'}.get(outcome.get('status'),'failed')) if outcome else 'unknown',
                error=outcome.get('error') or outcome.get('reason'), receipt_status=outcome.get('status'), started_at=started, finished_at=finished, duration_seconds=duration,
                source=str((outcome_path if outcome else path).relative_to(ROOT)),
                updated_at=finished or started or timestamp(path),
                retry_eligibility='No automatic replay. Retained admission consumes its slot; separate diagnosis required.'))
        except (OSError, ValueError):
            continue
    result.sort(key=lambda x: x['updated_at'] or '', reverse=True)
    return redact(dict(items=result[offset:offset+limit], total=len(result), offset=offset, limit=limit))


def logs(job=None, lines=100):
    if job:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,150}', job):
            raise ValueError('Invalid job identifier')
        path = lifecycle.dispatcher.STATE / job / 'runtime.log'
    else:
        path = lifecycle.dispatcher.STATE / 'launchd.err'
    if path.is_symlink() or not path.resolve().is_relative_to(lifecycle.dispatcher.STATE.resolve()):
        raise ValueError('Log outside dispatcher receipts refused')
    if not path.is_file():
        return fact('unknown', str(path.relative_to(ROOT)), None, 'No retained log for this job')
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size-65536))
        text = stream.read().decode(errors='replace')
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    text = ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)
    return redact(fact('completed', str(path.relative_to(ROOT)), timestamp(path), '\n'.join(text.splitlines()[-min(300,max(1,lines)):]), historical=True))


def dependencies(raw):
    observed = raw['observed_at_utc']
    def path_check(path):
        p = Path(path)
        return p.is_file() and p.stat().st_size > 0
    checks = [
        ('Docker inspection', 'completed' if 'containers' in raw else 'unknown', 'docker ps + inspect'),
        ('Pinned dispatcher Python', 'completed' if path_check(lifecycle.dispatcher.PYTHON) else 'blocked', lifecycle.dispatcher.PYTHON),
        ('Google Chrome', 'completed' if path_check('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome') else 'blocked', '/Applications/Google Chrome.app'),
        ('Screener session configured', 'completed' if path_check(ROOT/'.screener_session') else 'blocked', '.screener_session metadata only; validity not tested'),
        ('Dispatcher configuration', 'completed' if raw.get('configuration') else 'unknown', 'existing source manifest + installed/loaded plist validation'),
    ]
    try:
        checks.append(('Aqua desktop session', 'completed' if os.stat('/dev/console').st_uid == os.getuid() else 'blocked', '/dev/console ownership'))
    except OSError:
        checks.append(('Aqua desktop session','unknown','/dev/console ownership unavailable'))
    return [dict(name=name, **fact(state,source,observed)) for name,state,source in checks]


def build(raw):
    observed = raw['observed_at_utc']
    receipts = raw.get('receipts', {})
    active = raw.get('active_bounded_stages', [])
    errors = raw.get('inspection_errors', [])
    schedule = raw.get('schedule', {})
    tick = raw.get('last-tick.json', {})
    tick_at = tick.get('checked_at_utc')
    stale = True
    try:
        stale = (datetime.fromisoformat(observed)-datetime.fromisoformat(tick_at)).total_seconds() > 180
    except (ValueError, TypeError):
        pass
    state = ('paused' if raw.get('admissions_paused') else 'running' if active else
             'unknown' if raw.get('dispatcher_loaded') is not True or stale else 'scheduled')
    if not active and state == 'scheduled' and any('Active owner has not drained' in e for e in errors):
        state = 'unknown'
    stages = []
    recent = history(300)['items']
    mapping = [('filings','Filing discovery',('NSE','BSE'),('nse','bse')),
        ('financial','Financial acquisition',('FINANCIAL',),('financial',)),
        ('discovery','Document discovery',('DOCUMENT_DISCOVERY','DOCUMENT_PATROL','DOCUMENT_RATING'),('discovery','patrol','rating')),
        ('downloads','Downloads',('DOCUMENT_DOWNLOAD',),('download',)),
        ('processing','Processing',('DOCUMENT_PROCESSING',),('processing',))]
    queues = raw.get('live_inspection', raw.get('quiescent_inspection',{})).get('database',{}).get('queues',{})
    queue_map = {'filings':['exchange_result_filings','exchange_screener_enqueues'],
        'financial':['screener_company_queue'], 'discovery':['artifact_expectations'],
        'downloads':['artifact_download_queue','credit_rating_download_queue'],
        'processing':['document_processing_inbox','document_processing_work']}
    for key, label, exchanges, needles in mapping:
        matching = [c for c in active if not (key=='financial' and c.get('container')=='trading-ai-step3-cap') and any(n in (c.get('container','')+' '+str(c.get('service',''))+' '+str(c.get('receipt_stage',''))).lower() for n in needles)]
        stopped = {k:v for k,v in receipts.get('blocked_stages',{}).items() if any(e in k for e in exchanges)}
        next_jobs = [j for j in schedule.get('jobs',[]) if j['exchange'] in exchanges]
        last = next((j for j in recent if j['exchange'] in exchanges), None)
        stage_state = 'running' if matching else 'paused' if raw.get('admissions_paused') else 'blocked' if stopped else 'blocked' if key in ('discovery','downloads','processing') and schedule.get('document_window_expired') else 'unknown' if state == 'unknown' else 'scheduled' if next_jobs else 'idle'
        if not matching and not stopped and last and last['state']=='failed' and stage_state not in ('blocked','paused','unknown'):
            stage_state = 'failed'
        stages.append(fact(stage_state, 'dispatcher containers + admission/outcome receipts + authorized schedules', observed,
            dict(id=key,label=label,active=matching,blockers=stopped,next=next_jobs[0] if next_jobs else None,last=last,
                 counts={n:queues[n] for n in queue_map[key] if n in queues}), stale=stale,
            warning='Queue totals include historical work; eligibility is not implied.'))
    db = raw.get('live_inspection', raw.get('quiescent_inspection',{})).get('database')
    if db and db.get('summary',{}).get('discovery'):
        stages[2]['data']['counts']['artifact_expectations'] = db['summary']['discovery']
    warnings = [dict(message='BSE blocked: '+str(v.get('error',v)), source='artifacts/automatic-acquisition/'+k, updated_at=v.get('finished_at_utc')) for k,v in receipts.get('blocked_stages',{}).items()]
    warnings += [dict(message=e,source='lifecycle read-only inspection',updated_at=observed) for e in errors]
    if db and db.get('summary_error'):
        warnings.append(dict(message='Additional database summary unavailable: '+db['summary_error'],source='guarded SQLite summary',updated_at=observed))
    if schedule.get('document_window_expired'):
        warnings.append(dict(message='Document authorization expired. No renewal or extension permitted.',source='ops/activation/phase4_schedule.json',updated_at=timestamp(ROOT/'ops/activation/phase4_schedule.json')))
    return redact(dict(**raw, monitor_version=1,
        acquisition=fact(state,'launchctl + current bounded adapters + last-tick.json',observed,dict(active=active,heartbeat=tick_at,stale=stale)),
        stages=stages, dependencies=dependencies(raw),
        database=fact('completed' if db else 'unknown','Docker volume trading-ai_scraper-data:/data/scraper.db; guarded immutable census',observed if db else None,db,reason=None if db else 'Live census deferred or unavailable; inspect warnings. No host-database fallback.'),
        drive=fact('completed' if raw.get('storage') else 'blocked','phase4_documents.storage_preflight: UUID, sentinel, hashes, reserve',observed,raw.get('storage'),error=next((e for e in errors if e.startswith('storage:')),None)),
        authorization=fact('blocked' if schedule.get('document_window_expired') else 'scheduled' if schedule.get('document_expires_at_utc') else 'unknown','ops/activation/phase4_schedule.json',timestamp(ROOT/'ops/activation/phase4_schedule.json'),dict(expires_at=schedule.get('document_expires_at_utc'),expired=schedule.get('document_window_expired'))),
        shutdown_readiness=fact('blocked' if active else 'unknown','read-only observation; controlled lifecycle test outstanding',observed,dict(safe_to_shut_down_mac=False,reason='Use the guarded CLI shutdown during an authorized maintenance window. Monitoring cannot certify shutdown or drive ejection.')),
        backup=fact('blocked','local reachable Git history; docs/operations/security-backup.md',timestamp(ROOT/'docs/operations/security-backup.md'),dict(reason='Historical .screener_csrf credential; source-only backup and credential maintenance outstanding.')),
        warnings=warnings, recent_jobs=history(30),
        current_quarter=fact('unknown','reporting_periods.priority_result_periods',observed,None)))


class Backend:
    def __init__(self):
        self.mutex = threading.Lock()
        self.cached = None
        self.cached_at = 0
    def status(self):
        with self.mutex:
            if self.cached is not None and time.monotonic()-self.cached_at < 30:
                return self.cached
            try:
                raw = lifecycle.status(include_summary=True, live=True)
            except Exception as exc:
                raw = {'observed_at_utc':now(), 'status':'DEGRADED', 'inspection_errors':['Read-only inspection unavailable: '+str(exc)]}
            value = build(raw)
            from scrapper.sources.exchange.reporting_periods import priority_result_periods
            value['current_quarter'] = fact('completed','reporting_periods.priority_result_periods',raw['observed_at_utc'],priority_result_periods(now=datetime.now(timezone.utc)))
            self.cached, self.cached_at = value, time.monotonic()
            return value
    def company(self, action, **kwargs):
        with self.mutex:
            key=hashlib.sha256(json.dumps([action,kwargs],sort_keys=True).encode()).hexdigest()
            cache=lifecycle.dispatcher.STATE/'monitor-snapshots'/(key+'.json')
            try:
                # A WAL-aware read-only connection can read committed data while
                # the writer works. Monitoring never acquires acquisition locks.
                try:
                    data = lifecycle.database_status('/host/acquisition_queries.py',
                        (action,json.dumps(dict(kwargs,live=True))))
                except RuntimeError as exc:
                    # A checkpointed database may have no -shm file. A read-only
                    # Docker mount cannot initialize it. Use the cold reader only
                    # after acquiring the original idle-owner gates.
                    if 'unable to open database file' not in str(exc):raise
                    with lifecycle.lock(lifecycle.ACTIVATION/'.scheduler-admission.lock'):
                        with lifecycle.lock(lifecycle.ACTIVATION/'.nse-owner.lock'):
                            from production_ownership import owner_gate
                            owner_gate()
                            if any(c['state'] in lifecycle.ACTIVE for c in lifecycle.containers()):
                                raise RuntimeError('Live snapshot waiting for SQLite shared memory; retry after active batch')
                            data=lifecycle.database_status('/host/acquisition_queries.py',(action,json.dumps(kwargs)))
                            owner_gate()

                value=redact(fact('completed','trading-ai_scraper-data:/data/scraper.db; read-only snapshot',now(),data))
                cache.parent.mkdir(parents=True,exist_ok=True)
                lifecycle.atomic(cache,value)
                return value
            except Exception as exc:
                try:
                    saved=lifecycle.read(cache)
                    if saved.get('data') is not None:
                        return {**saved,'stale':True,'source':str(cache),
                            'error':'Showing previous snapshot; current refresh unavailable: '+str(exc)}
                except (OSError,ValueError):pass
                return redact(fact('unknown','trading-ai_scraper-data:/data/scraper.db',None,None,error=str(exc)))

backend = Backend()
