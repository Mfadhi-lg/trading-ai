"""HCD-02: bounded rollout of the retained manifest, using the HCD-01 archive.

No scheduler is installed. Full execution requires a separate manifest digest
authorization and a passing scaled validation; batches then resume automatically.
"""
from collections import Counter
from datetime import datetime, timezone
import argparse
import gzip
import json
import shutil
import sqlite3
import time

from . import acquire
from .acquire import normalize as provider_normalize
from .storage import Archive, OUT, dump, sha, atomic
from .control import checkpoint,wait as pause_aware_wait,PauseRequested

EVIDENCE = OUT / 'hcd02'


def normalize(raw,q,received):
    # Negative finite source numbers remain exact, isolated, and unusable.
    # Shape/type/nonfinite/fractional-volume errors still stop the campaign.
    return provider_normalize(raw,q,received,quarantine_negative=True)


def zero_placeholder(row):
    return all(row[k]==0 for k in ('open','high','low','close','volume','open_interest'))


def now():
    return datetime.now(timezone.utc).isoformat()


def prepare():
    raw = (OUT / 'full-universe-manifest.json').read_bytes()
    full = json.loads(raw)
    assert len(full['requests']) == 83688
    companies = [c for c in full['companies'] if c.get('instrument_key')]
    pilot = json.loads((OUT / 'pilot-companies.json').read_text())
    selected = {c['company_id']: c for c in companies if c['company_id'] in {p['company_id'] for p in pilot}}
    # Ten BSE identities plus long, recent and unknown listing histories.
    bse = sorted((c for c in companies if c['instrument_key'].startswith('BSE_EQ|')), key=lambda c: c['company_id'])
    for c in bse[:10]:
        selected[c['company_id']] = c
    nse = sorted((c for c in companies if c['instrument_key'].startswith('NSE_EQ|') and c['company_id'] not in selected),
                 key=lambda c: (c.get('listing_date') or '9999', c['company_id']))
    for pool in (nse[:], list(reversed(nse))):
        for c in pool[:35]:
            selected[c['company_id']] = c
    for c in nse:
        if len(selected) >= 100:
            break
        selected[c['company_id']] = c
    assert len(selected) == 100
    requests = [q for q in full['requests'] if q['company_id'] in selected]
    manifest = dict(kind='OWNER_AUTHORIZED_INITIAL_100_COMPANY_ROLLOUT', source_manifest_sha256=sha(raw),
                    companies=list(selected.values()), requests=requests,
                    full_manifest_authorized=False, schedules_enabled=False)
    dump(EVIDENCE / 'rollout-manifest.json', manifest)
    # Explicit, bounded evidence requests for demonstrated pilot defects.
    issues = json.loads((OUT / 'duplicate-revision-review.json').read_text())['invalid_source_rows']
    identities = {c['company_id']: c for c in pilot}
    specs = set()
    for p in issues:
        cid, day = p['company_id'], p['timestamp'][:10]
        specs.add((cid, 'days', 1, 'Daily', day))
        if p['kind'] == 'OHLC_CONSTRAINT_FAILURE' and p['timeframe'] != 'Daily':
            specs.add((cid, 'minutes', 1, '1-minute', day))
            specs.add((cid, 'hours', 1, '1-hour', day))
            specs.add((cid, 'hours', 4, '4-hour', day))
    for c in pilot:
        for day in ('2022-10-24', '2023-11-12', '2024-03-02', '2024-05-18'):
            specs.add((c['company_id'], 'minutes', 1, '1-minute', day))
    requests = []
    for cid, unit, interval, tf, day in sorted(specs):
        q = dict(company_id=cid, instrument_key=identities[cid]['instrument_key'], unit=unit,
                 interval=interval, timeframe=tf, from_date=day, to_date=day, role='HCD02_DEFECT_EVIDENCE')
        q['request_id'] = sha(json.dumps(q, sort_keys=True).encode())
        requests.append(q)
    dump(EVIDENCE / 'defect-manifest.json', dict(companies=pilot, requests=requests, schedules_enabled=False))
    dump(EVIDENCE / 'scope.json', dict(source_manifest_sha256=sha(raw), source_requests=83688,
         rollout_requests=len(manifest['requests']), rollout_companies=100,
         exchanges=Counter(c['instrument_key'].split('|')[0] for c in selected.values()),
         defect_requests=len(requests), full_manifest_authorized=False))
    return manifest


def prepare_special():
    """Minute evidence for the two demonstrated split-session grid defects."""
    full=json.loads((OUT/'full-universe-manifest.json').read_text())
    initial=json.loads((EVIDENCE/'rollout-manifest.json').read_text())
    for kind,companies in [('special',initial['companies']),('special-full',full['companies']+full['historically_eligible_extension'])]:
        companies=[c for c in companies if (c.get('instrument_key') or '').startswith('NSE_EQ|')]
        requests=[]
        for c in companies:
            for day in ('2024-03-02','2024-05-18'):
                q=dict(company_id=c['company_id'],instrument_key=c['instrument_key'],unit='minutes',interval=1,
                    timeframe='1-minute',from_date=day,to_date=day,role='HCD02_DEFECT_EVIDENCE')
                q['request_id']=sha(json.dumps(q,sort_keys=True).encode());requests.append(q)
        dump(EVIDENCE/f'{kind}-manifest.json',dict(companies=companies,requests=requests,
            source_manifest_sha256=sha((OUT/'full-universe-manifest.json').read_bytes()),
            purpose='Exact-minute constituents for two official split-session dates; no fill or adjustment',
            full_owner_authorization_required=kind=='special-full',schedules_enabled=False))
    return len(requests)


def validate_manifest(manifest):
    identities = {c['company_id']: c['instrument_key'] for c in
                  manifest['companies'] + manifest.get('historically_eligible_extension', [])}
    seen = set()
    for q in manifest['requests']:
        if identities.get(q['company_id']) != q['instrument_key'] or q['request_id'] in seen:
            raise RuntimeError('STOP_MANIFEST_IDENTITY_OR_DUPLICATE')
        expected = sha(json.dumps({k: v for k, v in q.items() if k != 'request_id'}, sort_keys=True).encode())
        if expected != q['request_id']:
            raise RuntimeError('STOP_MANIFEST_REQUEST_DIGEST')
        seen.add(q['request_id'])


def recover(a, q):
    """Receipts are durable before normalization. Reuse saved bytes after a crash."""
    old = a.db.execute('SELECT status,raw_sha256,raw_path,received_at,attempts FROM requests WHERE request_id=?',
                       (q['request_id'],)).fetchone()
    if old and old[0] in ('ISOLATED_FAILURE','QUARANTINED_RESPONSE'):
        return False,old
    receipt = a.root / 'receipts' / (q['request_id'] + '.json')
    if old and not receipt.exists():
        # A retained error response or unknown transmission is evidence to
        # review, never permission to issue the same request again.
        if old[0]=='STOPPED' and old[2]:
            raise RuntimeError('STOP_RETAINED_RESPONSE_REQUIRES_REVIEW; NO_REPEAT_GET')
        if old[0]=='IN_FLIGHT' or (old[0]=='STOPPED' and (EVIDENCE/'transport-receipts'/(q['request_id']+'.json')).exists()):
            raise RuntimeError('STOP_UNCERTAIN_PROVIDER_DELIVERY; NO_REPEAT_GET')
    if receipt.exists() and (not old or old[0] not in ('SUCCESS', 'RAW_SAVED')):
        r = json.loads(receipt.read_text())
        if not old:
            raise RuntimeError('STOP_RECEIPT_WITHOUT_REQUEST')
        a.db.execute("UPDATE requests SET status='RAW_SAVED',raw_sha256=?,raw_path=?,received_at=?,http_status=200 WHERE request_id=?",
                     (r['raw_sha256'], r['raw_path'], r['received_at'], q['request_id']))
        a.db.commit()
        old = ('RAW_SAVED', r['raw_sha256'], r['raw_path'], r['received_at'], old[4])
    if old and old[0] in ('SUCCESS', 'RAW_SAVED'):
        raw = gzip.decompress((a.root / old[2]).read_bytes())
        if sha(raw) != old[1]:
            raise RuntimeError('STOP_RESTART_CHECKSUM')
        if old[0] == 'RAW_SAVED':
            rows, issues = normalize(raw, q, old[3])
            a.parquet(rows, q, old[1], old[3])
            if issues:
                dump(EVIDENCE/'quality'/(q['request_id']+'.json'),issues)
            a.db.execute("UPDATE requests SET status='SUCCESS',row_count=? WHERE request_id=?", (len(rows), q['request_id']))
            a.db.commit()
        else:
            for path, digest in a.db.execute("SELECT parquet_path,parquet_sha256 FROM datasets WHERE request_id=? AND definition='UPSTOX_NATIVE'", (q['request_id'],)):
                if sha((a.root / path).read_bytes()) != digest:
                    raise RuntimeError('STOP_PARQUET_CHECKSUM')
        return True, old
    return False, old


def execute(kind='defect', batch_size=200, authorize=None, interrupt_after_raw_save=False, fast_resume=False):
    if fast_resume and kind not in ('full', 'special-full'):
        raise ValueError('Fast resume requires the full campaign final audit')
    path = EVIDENCE / f'{kind}-manifest.json' if kind != 'full' else OUT / 'full-universe-manifest.json'
    manifest = json.loads(path.read_text())
    if kind == 'full':
        acceptance = json.loads((EVIDENCE / 'scaled-validation.json').read_text())
        if (authorize != sha(path.read_bytes()) or acceptance.get('safe_for_full_manifest') is not True
                or acceptance.get('full_manifest_sha256')!=authorize):
            raise PermissionError('STOP_FULL_MANIFEST_REQUIRES_OWNER_DIGEST_AUTHORIZATION_AND_SCALED_ACCEPTANCE')
    elif kind=='special-full':
        full_state=json.loads((EVIDENCE/'full-execution.json').read_text())
        if (authorize!=sha(path.read_bytes()) or not full_state.get('full_manifest_authorized')
                or full_state['status'] not in ('MANIFEST_PASS_FINISHED','FINISHED_WITH_ISOLATED_FAILURES')
                or manifest['source_manifest_sha256']!=full_state['owner_authorized_manifest_sha256']):
            raise PermissionError('STOP_FULL_SESSION_SUPPLEMENT_REQUIRES_DIGEST_AUTHORIZATION_AND_NATIVE_BACKFILL')
    elif kind not in ('defect', 'rollout', 'operation','special'):
        raise ValueError('unknown scope')
    validate_manifest(manifest)
    checkpoint()
    a = Archive()
    acquire.OUT = EVIDENCE  # Preserve all HCD-01 artifacts, including receipts.
    token = acquire.secure_supplier()
    if any(ch in token for ch in '\r\n"\\'):
        raise ValueError('unsafe credential')
    state_path = EVIDENCE / f'{kind}-execution.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else dict(
        started_at=now(), manifest_sha256=sha(path.read_bytes()), runs=[], schedules_enabled=False)
    if state['manifest_sha256'] != sha(path.read_bytes()):
        raise RuntimeError('STOP_EXECUTION_MANIFEST_CHANGED')
    run = dict(started_at=now(), successful=0, failed=0, reused=0, resumed=0, batches=0)
    state['runs'].append(run)
    if state.get('reason'):
        state.setdefault('stop_history',[]).append(dict(reason=state.pop('reason'),at=state.get('updated_at')))
    state.update(status='RUNNING', volume_uuid=a.info['VolumeUUID'], free_bytes=shutil.disk_usage(a.mount).free)
    if kind=='full':
        state.update(full_manifest_authorized=True,owner_authorized_manifest_sha256=authorize,
                     scaled_validation_sha256=sha((EVIDENCE/'scaled-validation.json').read_bytes()),
                     automatic_resumable_batches_authorized=True)
    elif kind=='special-full':
        state.update(full_session_supplement_authorized=True,owner_authorized_manifest_sha256=authorize,
                     automatic_resumable_batches_authorized=True)
    state.update(stage='VERIFYING_SAVED_DATA', manifest_requests=len(manifest['requests']),
                 requests_checked=0, current_request=None, updated_at=now())
    dump(state_path, state)
    progress_saved = time.monotonic()
    recent = list(state.get('provider_attempt_times', []))
    blocked = set()
    consecutive_stock_failures=0
    try:
        # The finite full campaign audits every saved raw/Parquet checksum in
        # finalize(). Resume durable SUCCESS rows from its exclusively owned
        # index rather than rereading the entire archive on each wake. Partial
        # or uncertain requests still use recover() and its fail-closed rules.
        # Most rows are complete. The status index is not covering and would
        # cause one random table-page read per result on the external drive.
        completed = ({row[0] for row in a.db.execute(
            "SELECT request_id FROM requests NOT INDEXED WHERE status='SUCCESS'")}
            if fast_resume else set())
        for position, q in enumerate(manifest['requests']):
            checkpoint()
            state.update(stage='RESUMING_COMPLETED' if q['request_id'] in completed else 'VERIFYING_SAVED_DATA', requests_checked=position,
                         current_request=q['request_id'])
            if time.monotonic() - progress_saved >= 10:
                state['updated_at'] = now()
                dump(state_path, state)
                progress_saved = time.monotonic()
            if q['request_id'] in completed:
                run['resumed'] += 1
                continue
            a.guard()
            if q['company_id'] in blocked:
                continue
            reused, previous = recover(a, q)
            if reused:
                run['reused'] += 1
                continue
            if previous and previous[0] in ('ISOLATED_FAILURE', 'QUARANTINED_RESPONSE'):
                blocked.add(q['company_id'])
                continue
            if previous and previous[4] >= 3:
                raise RuntimeError('STOP_BOUNDED_RECOVERY_EXHAUSTED')
            # Budget all attempts, including internal transport retries, across restarts.
            recent = [t for t in recent if time.time() - t < 1800]
            while len(recent) > 1794:
                pause_aware_wait(min(30, max(.1, recent[0] + 1800 - time.time())))
                recent = [t for t in recent if time.time() - t < 1800]
            started = time.monotonic()
            state['stage'] = 'ACQUIRING'
            a.db.execute('''INSERT INTO requests(request_id,company_id,instrument_key,unit,interval,from_date,to_date,status,attempts)
                VALUES(?,?,?,?,?,?,?,'IN_FLIGHT',1) ON CONFLICT(request_id) DO UPDATE SET status='IN_FLIGHT',attempts=attempts+1''',
                (q['request_id'],q['company_id'],q['instrument_key'],q['unit'],q['interval'],q['from_date'],q['to_date']))
            a.db.commit()
            # Curl plus alternate TLS can issue at most six attempts per fetch.
            # Reserve before the GET: interruption cannot lose quota accounting.
            reserved = [time.time()] * 6
            recent.extend(reserved)
            state['provider_attempt_times'] = recent
            state['updated_at'] = now()
            dump(state_path, state)
            raw, status = acquire.fetch(q, token,retain_error_body=True)
            transport = json.loads((EVIDENCE/'transport-receipts'/(q['request_id']+'.json')).read_text())
            actual_attempts = sum('transport_exit' in t or t.get('transport') == 'urllib_verified_TLS' for t in transport)
            recent = recent[:-6] + [time.time()] * actual_attempts
            state['provider_attempt_times'] = recent
            received = now()
            # Retain original responses, including malformed successes and stock-level errors.
            digest, raw_path = a.raw(raw)
            # The filesystem receipt must survive a SQLite commit failure too.
            # Recovery can then normalize saved bytes without another GET.
            receipt = a.root / 'receipts' / (q['request_id'] + '.json')
            if status == 200:
                dump(receipt, dict(request_id=q['request_id'],raw_sha256=digest,raw_path=raw_path,received_at=received))
            a.db.execute('UPDATE requests SET raw_sha256=?,raw_path=?,received_at=?,http_status=?,elapsed_seconds=? WHERE request_id=?',
                         (digest,raw_path,received,status,time.monotonic()-started,q['request_id']))
            a.db.commit()
            receipt = a.root / 'receipts' / (q['request_id'] + '.json')
            if status != 200:
                if status not in (400,404,422):
                    raise RuntimeError(f'STOP_PROVIDER_HTTP_{status}')
                a.db.execute("UPDATE requests SET status='ISOLATED_FAILURE',error_kind=? WHERE request_id=?", (f'HTTP_{status}',q['request_id']))
                a.db.commit()
                blocked.add(q['company_id'])
                run['failed'] += 1
                consecutive_stock_failures+=1
            else:
                dump(receipt, dict(request_id=q['request_id'],raw_sha256=digest,raw_path=raw_path,received_at=received))
                a.db.execute("UPDATE requests SET status='RAW_SAVED' WHERE request_id=?", (q['request_id'],))
                a.db.commit()
                if interrupt_after_raw_save:
                    raise RuntimeError('STOP_TEST_INTERRUPTION_AFTER_DURABLE_RAW_SAVE')
                try:
                    rows, issues = normalize(raw, q, received)
                except ValueError:
                    a.db.execute("UPDATE requests SET status='QUARANTINED_RESPONSE',error_kind='INVALID_PROVIDER_SCHEMA' WHERE request_id=?", (q['request_id'],))
                    a.db.commit()
                    blocked.add(q['company_id']);run['failed']+=1
                    state.update(updated_at=now(),current_request=q['request_id'],blocked_companies=sorted(blocked))
                    dump(state_path,state)
                    from .repair_queue import refresh
                    refresh(a,manifest,kind)
                    print(json.dumps(dict(scope=kind,company_id=q['company_id'],request=q['request_id'],status='QUARANTINED_RESPONSE; CONTINUING_OTHER_STOCKS')),flush=True)
                    pause_aware_wait(max(0,1.1-(time.monotonic()-started)))
                    continue
                # Exact all-zero rows convey no usable trade. They stay in the
                # quarantine partition and must not be counted as price history.
                bad = sum(r['quality'] in ('QUARANTINED_OHLC','QUARANTINED_NUMERIC') and not zero_placeholder(r) for r in rows)
                issues.extend(dict(timestamp=r['start'],kind='QUARANTINED_ZERO_OHLCV_PLACEHOLDER') for r in rows if zero_placeholder(r))
                a.parquet(rows, q, digest, received)
                a.db.execute("UPDATE requests SET status='SUCCESS',row_count=?,error_kind=NULL WHERE request_id=?", (len(rows),q['request_id']))
                a.db.commit()
                if issues:
                    dump(EVIDENCE/'quality'/(q['request_id']+'.json'), issues)
                run['successful'] += 1
                consecutive_stock_failures=0
                if bad > max(5, len(rows) * .01):
                    # Owner explicitly requested acquisition to continue while
                    # corrupt source records remain held for their repair list.
                    a.db.execute("UPDATE requests SET error_kind='EXCESSIVE_SOURCE_ROWS_QUARANTINED' WHERE request_id=?",(q['request_id'],))
                    a.db.commit()
            state.update(updated_at=now(), current_request=q['request_id'], blocked_companies=sorted(blocked),
                         requests_checked=position + 1)
            if (run['successful'] + run['failed']) % batch_size == 0:
                run['batches'] += 1
                from .repair_queue import refresh
                refresh(a,manifest,kind)
                acquisition_inventory(a.db,manifest,EVIDENCE/f'{kind}-inventory.json')
            dump(state_path, state)
            print(json.dumps(dict(scope=kind,successful=run['successful'],failed=run['failed'],
                  company_id=q['company_id'],timeframe=q['timeframe'],http_status=status)), flush=True)
            # Conservative sustained quota: reserve all three transport attempts.
            pause_aware_wait(max(0, 1.1 - (time.monotonic() - started)))
        state['requests_checked'] = len(manifest['requests'])
        state['status'] = 'MANIFEST_PASS_FINISHED' if not blocked else 'FINISHED_WITH_ISOLATED_FAILURES'
    except BaseException as error:
        state.update(status='STOPPED', reason=str(error) if isinstance(error,RuntimeError) and str(error).startswith('STOP_') else type(error).__name__)
        if isinstance(error,PauseRequested):state.update(status='PAUSED',reason='OWNER_LIFECYCLE_PAUSE')
        # Keep durable RAW_SAVED evidence recoverable, but do not describe a
        # stopped provider call as still in flight.
        try:
            a.guard()
            if 'q' in locals():
                a.db.execute("UPDATE requests SET status='STOPPED',error_kind=? WHERE request_id=? AND status='IN_FLIGHT'",
                             (state['reason'],q['request_id']))
                a.db.commit()
        except (OSError,RuntimeError,sqlite3.Error):
            pass  # A failed mount/storage guard must never trigger another write.
        raise
    finally:
        try:
            a.guard()
            free_bytes=shutil.disk_usage(a.mount).free
        except (OSError,RuntimeError):
            free_bytes=None
        state.update(updated_at=now(), free_bytes=free_bytes, stage=state['status'])
        run['finished_at'] = now()
        dump(state_path, state)
        try:
            if free_bytes is not None:
                acquisition_inventory(a.db,manifest,EVIDENCE/f'{kind}-inventory.json')
                from .repair_queue import refresh
                refresh(a,manifest,kind)
        finally:
            a.db.close()
            a.writer_lock.close()
    return state


def acquisition_inventory(db,manifest,path):
    """Report actual native-request completion, without claiming calendar coverage."""
    saved={r[0]:r[1:] for r in db.execute('SELECT request_id,status,row_count,received_at FROM requests')}
    groups={}
    for q in manifest['requests']:
        item=groups.setdefault((q['company_id'],q['timeframe']),dict(company_id=q['company_id'],timeframe=q['timeframe'],
            expected_requests=0,successful_requests=0,failed_requests=0,source_rows_returned=0))
        item['expected_requests']+=1
        old=saved.get(q['request_id'])
        if old:
            item['successful_requests']+=old[0]=='SUCCESS'
            item['failed_requests']+=old[0] not in ('SUCCESS','RAW_SAVED','IN_FLIGHT')
            item['source_rows_returned']+=old[1] or 0
    for item in groups.values():
        item['all_planned_requests_successful']=item['successful_requests']==item['expected_requests']
        item['returned_nonempty_history']=item['source_rows_returned']>0
    companies={cid for cid,tf in groups}
    complete=sum(all(groups.get((cid,tf),{}).get('all_planned_requests_successful',False) and groups.get((cid,tf),{}).get('returned_nonempty_history',False)
                     for tf in ('Weekly','Daily','1-hour','4-hour')) for cid in companies)
    dump(path,dict(created_at=now(),companies_with_all_four_planned_native_histories=complete,
        expected_companies=len(companies),successful_requests=sum(r['successful_requests'] for r in groups.values()),
        failed_requests=sum(r['failed_requests'] for r in groups.values()),timeframes=list(groups.values()),
        calendar_and_adjustment_completeness='NOT_INFERRED_FROM_REQUEST_SUCCESS',schedules_enabled=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['prepare','prepare-special','defect','rollout','full','evidence','review','operation','finalize','report','special','finish-rollout','authorized-full','watch-report','lifecycle-handoff','lifecycle-check','lifecycle-seal'])
    p.add_argument('--batch-size', type=int, default=200)
    p.add_argument('--authorize-manifest-sha256')
    p.add_argument('--authorize-special-manifest-sha256')
    args = p.parse_args()
    if not 1 <= args.batch_size <= 1000:
        p.error('batch size must be 1..1000')
    if args.action == 'prepare':
        prepare()
    elif args.action=='prepare-special':
        prepare_special()
    elif args.action == 'evidence':
        from .hcd02_review import public_evidence
        public_evidence()
    elif args.action == 'review':
        from .hcd02_review import review
        review()
    elif args.action == 'operation':
        from .hcd02_review import operational_checks
        operational_checks()
    elif args.action == 'finalize':
        from .hcd02_review import finalize
        finalize()
    elif args.action == 'report':
        from .hcd02_review import write_report
        write_report()
    elif args.action=='finish-rollout':
        from .hcd02_review import finish_rollout
        finish_rollout()
    elif args.action=='authorized-full':
        from .campaign import run
        run()
    elif args.action=='watch-report':
        from .campaign import watch_report
        watch_report()
    elif args.action=='lifecycle-handoff':
        from ops.host.historical_candle_lifecycle import install_handoff
        print(json.dumps(install_handoff()),flush=True)
    elif args.action=='lifecycle-check':
        from ops.host.historical_candle_lifecycle import verify_pause_resume
        print(json.dumps(verify_pause_resume()),flush=True)
    elif args.action=='lifecycle-seal':
        from ops.host.historical_candle_lifecycle import seal_lifecycle
        print(json.dumps(seal_lifecycle()),flush=True)
    else:
        if args.action=='full' and args.authorize_special_manifest_sha256:
            if args.authorize_special_manifest_sha256!=sha((EVIDENCE/'special-full-manifest.json').read_bytes()):
                p.error('special-session authorization digest must match its prepared manifest')
        execute(args.action, args.batch_size, args.authorize_manifest_sha256)
        if args.action=='full' and args.authorize_special_manifest_sha256:
            execute('special-full',args.batch_size,args.authorize_special_manifest_sha256)
