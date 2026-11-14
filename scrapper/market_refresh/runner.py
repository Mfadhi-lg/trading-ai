"""Durable serial refresh executor, sharing the canonical archive writer guard.

No network is performed by import or planning. Production callers must pass the
exact authorized plan digest and the existing secure supplier. Transport errors
remain uncertain delivery, never an automatic repeat. Saved raw responses resume
locally without another request; HCD-02 receipts are never changed.
"""
from datetime import datetime, timezone
import fcntl
import gzip
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace

from experiments.earnings_reaction_v1.upstox_candles import url_for

from experiments.historical_candle_archive_v1.acquire import normalize
from experiments.historical_candle_archive_v1.storage import Archive, dump, sha
from experiments.technical_fix_v2.update import get
from .planner import aware, digest
from .quota import reserve


def _close(archive):
    archive.db.close()
    archive.writer_lock.close()


def _persist(archive, query, receipt, raw, calendar):
    rows, issues = normalize(raw, query, receipt['received_at'], quarantine_negative=True)
    now = aware(receipt['received_at'])
    for row in rows:
        record = calendar(datetime.fromisoformat(row['start']).date())
        if record.get('verified') is not True or not record.get('sessions'):
            row.update(quality='QUARANTINED_UNVERIFIED_SESSION', status='PROVISIONAL')
            continue
        completion = max(aware(s['closes_at']) for s in record['sessions'])
        # Daily source labels remain untouched. Only the completion bound is
        # refined using separately retained, verified session evidence.
        row.update(end=completion.isoformat(), end_upper_bound=completion.isoformat(),
                   end_basis='LIVE_FRESH_VERIFIED_SESSION;' + ';'.join(record.get('source_refs', [])),
                   status='COMPLETED_RETROSPECTIVE' if completion <= now else 'PROVISIONAL')
    if query['kind'] == 'STOCK':
        archive.db.execute('''INSERT OR IGNORE INTO requests
            (request_id,company_id,instrument_key,unit,interval,from_date,to_date,status,attempts)
            VALUES (?,?,?,?,?,?,?,'RAW_SAVED',1)''', tuple(query[k] for k in
            ('request_id','company_id','instrument_key','unit','interval','from_date','to_date')))
        archive.db.execute('''UPDATE requests SET raw_sha256=?,raw_path=?,received_at=?,http_status=200,
            status='RAW_SAVED' WHERE request_id=?''',
            (receipt['sha256'], receipt['raw_path'], receipt['received_at'], query['request_id']))
        archive.db.commit()
        archive.parquet(rows, query, receipt['sha256'], receipt['received_at'])
        archive.db.execute("UPDATE requests SET status='SUCCESS',row_count=? WHERE request_id=?",
                           (len(rows), query['request_id']))
        archive.db.commit()
    valid = [r for r in rows if r['quality'] == 'OHLC_VALID' and r['status'] != 'PROVISIONAL']
    receipt.update(status='SUCCESS' if valid else 'EMPTY', accepted=len(valid),
                   quarantined=len(rows)-len(valid), issues=issues,
                   validated_sessions=sorted({r['start'][:10] for r in valid}),
                   invalid_sessions=sorted({r['start'][:10] for r in rows if r not in valid}),
                   latest_valid_observation_at=max((r['start'] for r in valid), default=None))
    return valid


def execute_plan(plan, state_dir, *, authorized_sha256, calendar, token_supplier,
                 fetch=get, archive_factory=Archive, clock=None, pace=1.05, sleep=time.sleep,
                 on_validated=None, quota_path=None, admission_check=None):
    """Run a finite plan. Caller owns activation approval and calendar review.

    `on_validated` receives completed rows after durable canonical publication.
    It must rebuild derived outputs in a new directory, retaining warmup inputs.
    Auth/quota/transport blocks are durable and require a reviewed new plan.
    The archive guard spans each request, never a WebSocket or idle lifetime.
    """
    if authorized_sha256 != digest(plan):
        raise PermissionError('exact authorized incremental plan digest required')
    if len(plan['requests']) > plan['max_requests'] or not 0 <= plan['max_requests'] <= 1800:
        raise ValueError('bounded plan required')
    identifiers = [q['request_id'] for q in plan['requests']]
    if len(set(identifiers)) != len(identifiers) or any(not re.fullmatch(r'LIVE_FRESH_[0-9a-f]{64}', k) for k in identifiers):
        raise ValueError('incremental receipt namespace required')
    for query in plan['requests']:
        if (query['kind'] not in ('STOCK', 'NSE_INDEX') or query['unit'] != 'days'
                or query['interval'] != 1 or query['timeframe'] != 'Daily'
                or not query.get('identity_ref')):
            raise ValueError('daily verified incremental scope required')
        expected_url = url_for(SimpleNamespace(instrument_key=query['instrument_key'], unit='days', interval=1,
            from_date=datetime.fromisoformat(query['from_date']).date(), to_date=datetime.fromisoformat(query['to_date']).date()))
        if query['url'] != expected_url:
            raise ValueError('canonical Upstox URL required')
    clock = clock or (lambda: datetime.now(timezone.utc))
    output = Path(state_dir)
    output.mkdir(parents=True, exist_ok=True)
    with (output/'.refresh.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(status='BLOCKED', reason='INCREMENTAL_WORKER_BUSY', requests={})
        folder = output/'runs'/digest(plan)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder/'UPDATE_LEDGER.json'
        state = json.loads(path.read_text()) if path.exists() else dict(
            manifest_sha256=digest(plan), status='RUNNING', requests={})
        if state['manifest_sha256'] != digest(plan):
            raise ValueError('incremental ledger scope mismatch')
        dump(folder/'manifest.json', plan)
        token = None
        quota_path = Path(quota_path) if quota_path is not None else output/'quota.json'
        for query in plan['requests']:
            ident = query['request_id']
            global_path = output/'receipts'/(ident+'.json')
            receipt = json.loads(global_path.read_text()) if global_path.exists() else state['requests'].get(ident)
            if receipt and receipt.get('query_sha256') != digest(query):
                raise ValueError('retained request scope changed')
            def retain():
                # Global semantic receipts deduplicate across daily plan changes.
                dump(global_path, receipt)
                dump(path, state)
            if receipt:
                state['requests'][ident] = receipt
            if receipt and receipt['status'] not in ('RAW_SAVED', 'SUCCESS', 'EMPTY'):
                state.update(status='BLOCKED', reason=receipt['status'])
                dump(path, state)
                return state
            archive = None
            try:
                try:
                    archive = archive_factory()
                except RuntimeError as error:
                    if 'WRITER_BUSY' not in str(error):
                        raise
                    state.update(status='BLOCKED', reason='HISTORICAL_ARCHIVE_WRITER_BUSY')
                    dump(path, state)
                    return state
                archive.guard()
                if receipt:
                    raw = gzip.decompress((archive.root/receipt['raw_path']).read_bytes())
                    if sha(raw) != receipt['sha256']:
                        raise ValueError('saved raw checksum mismatch')
                    if receipt['status'] == 'EMPTY' or (receipt['status'] == 'SUCCESS'
                            and (on_validated is None or receipt.get('derived_status') == 'COMPLETE')):
                        continue
                else:
                    stamp = aware(clock())
                    if admission_check is not None and not admission_check():
                        state.update(status='BLOCKED', reason='ADMISSION_STOPPED')
                        dump(path, state)
                        return state
                    if not reserve(quota_path, stamp):
                        state.update(status='BLOCKED', reason='PROVIDER_QUOTA_EXHAUSTED')
                        dump(path, state)
                        return state
                    if token is None:
                        token = token_supplier()
                    if not isinstance(token, str) or not token or any(c in token for c in '\r\n"\\'):
                        raise ValueError('unsafe secure token handoff')
                    if admission_check is not None and not admission_check():
                        state.update(status='BLOCKED', reason='ADMISSION_STOPPED')
                        dump(path, state)
                        return state
                    receipt = dict(request_id=ident, kind=query['kind'], series=query['series'],
                                   url=query['url'], status='UNCERTAIN_DELIVERY',
                                   query_sha256=digest(query), attempt_started_at=stamp.isoformat())
                    state['requests'][ident] = receipt
                    retain()  # crash here never silently repeats delivery
                    raw, http_status = fetch(query, token)
                    received = aware(clock()).isoformat()
                    if token.encode() in raw:
                        raise ValueError('unsafe credential echo')
                    receipt.update(http_status=http_status, received_at=received)
                    if http_status != 200:
                        receipt.update(status='HTTP_'+str(http_status), response_sha256=sha(raw))
                        state.update(status='BLOCKED', reason=receipt['status'])
                        retain()
                        return state
                    checksum, raw_path = archive.raw(raw)
                    receipt.update(status='RAW_SAVED', sha256=checksum, raw_path=raw_path)
                    retain()
                rows = _persist(archive, query, receipt, raw, calendar)
                if on_validated and rows:
                    receipt['derived_status'] = 'PENDING'
                retain()
                if on_validated and rows:
                    on_validated(query, rows, dict(receipt))
                    receipt['derived_status'] = 'COMPLETE'
                    retain()
            except Exception as error:
                state.update(status='BLOCKED', reason=type(error).__name__)
                dump(path, state)  # never serialize credentials/provider exception text
                raise
            finally:
                if archive is not None:
                    _close(archive)
            sleep(max(1.05, pace))
        state.update(status='COMPLETE', reason=None, completed_at=aware(clock()).isoformat())
        dump(path, state)
        return state
