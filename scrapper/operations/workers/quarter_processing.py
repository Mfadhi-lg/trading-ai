"""Configured quarter owner over finite, persisted C21 processing campaigns."""
import json
import os
import fcntl
from datetime import date
from pathlib import Path
from .lifecycle import WorkerLifecycle, stderr_heartbeat
from .processing_campaign import (read_connection, freeze_campaign, digest,
    validate_campaign, campaign_status, run_campaign)
from ...config import assert_configured_writable_targets
from ...pipeline.extract_document import processing_version


def role_health(db_path, snapshot, now):
    """Read-only schema, readable document mount, heartbeat and active progress."""
    from ...config import DOCUMENT_STORE
    from ...db.database import check_schema_compatibility
    errors = []
    try:
        conn = read_connection(db_path)
        try:
            check_schema_compatibility(conn)
            conn.execute('SELECT work_id,resource_key,extraction_id FROM document_processing_work LIMIT 0')
            conn.execute('SELECT resource_key,status,claim_token FROM document_extraction_claims LIMIT 0')
        finally:
            conn.close()
    except Exception as exc:
        errors.append('schema:'+type(exc).__name__)
    if not DOCUMENT_STORE.is_dir() or not os.access(DOCUMENT_STORE,os.R_OK|os.X_OK):
        errors.append('dependency:document_store_readable')
    if now-snapshot['heartbeat_at_monotonic']>30:
        errors.append('heartbeat:stale')
    if snapshot['active_job'] is not None and now-snapshot.get('claimed_at_monotonic',snapshot['progress_at_monotonic'])>1800:
        errors.append('progress:stalled')
    return {'role':'quarter-processing','healthy':not errors,'errors':errors,
        'waiting':snapshot['active_job'] is None and snapshot['state']=='WAITING'}


def _persist(path, encoded):
    temporary = path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w') as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_scope(period, limit):
    parsed = date.fromisoformat(period)
    if parsed.isoformat() != period or period[5:] not in ('03-31','06-30','09-30','12-31'):
        raise ValueError('Explicit quarter-end period required')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Finite batch limit must be 1–100')


def run_batch(*, db_path, period, limit, state_dir, ocr_engine, quarter_selector,
              lifecycle=None, now_utc=None):
    """Resume the saved batch before selecting arrivals; never retry partial caches.

    Selection is exactly the legacy quarter selector supplied by the command.
    A manifest is persisted before enqueue. No database scan outside that scope,
    schema application, browser normalization or publisher request occurs.
    """
    validate_scope(period, limit)
    state_dir = Path(state_dir)
    assert_configured_writable_targets(state_dir, db_path=db_path)
    if lifecycle is not None and lifecycle.is_set():
        return {'period':period,'status':'drained','outstanding':0}
    version = processing_version(ocr_engine)
    active = state_dir / 'active.json'
    conn = read_connection(db_path)
    try:
        conn.execute('SELECT work_id FROM document_processing_work LIMIT 0')
        campaign = None
        if active.exists():
            envelope = json.loads(active.read_text())
            saved = envelope['manifest']
            if saved['scope'] != {'kind':'quarter','period':period}:
                raise ValueError('Saved campaign belongs to a different quarter')
            validate_campaign(conn,saved,expected_sha256=envelope['sha256'],version=version)
            if campaign_status(conn,saved)['outstanding']:
                campaign = saved
        if campaign is None:
            campaign = freeze_campaign(conn,campaign_id='quarter-'+period,version=version,
                period=period,limit=limit,quarter_selector=quarter_selector)
            if not campaign['contents']:
                return {'period':period,'status':'waiting','outstanding':0,
                    'snapshot_accounting':campaign['snapshot_accounting']}
    finally:
        conn.close()
    if lifecycle is not None and lifecycle.is_set():
        return {'period':period,'status':'drained','outstanding':len(campaign['contents'])}
    checksum = digest(campaign)
    state_dir.mkdir(parents=True,exist_ok=True)
    encoded = json.dumps({'sha256':checksum,'manifest':campaign},sort_keys=True,indent=2)+'\n'
    history = state_dir / (checksum+'.json')
    if history.exists() and history.read_text() != encoded:
        raise ValueError('Frozen campaign file changed')
    if not history.exists():
        _persist(history,encoded)
    _persist(active,encoded)
    kwargs = {'now_utc':now_utc} if now_utc is not None else {}
    report = run_campaign(db_path=db_path,campaign=campaign,expected_sha256=checksum,
        ocr_engine=ocr_engine,output_dir=state_dir/checksum,limit=limit,
        lifecycle=lifecycle,**kwargs)
    return {'period':period,'manifest_sha256':checksum,**report}


def run_role(*, db_path, period, state_dir, ocr_engine, quarter_selector,
             limit=10, lifecycle=None, max_cycles=None, emit=None):
    validate_scope(period, limit)
    if max_cycles is not None and (type(max_cycles) is not int or max_cycles < 1):
        raise ValueError('Positive fixture cycle budget required')
    if lifecycle is None:
        with WorkerLifecycle(observer=stderr_heartbeat).signals() as owned:
            return run_role(db_path=db_path,period=period,state_dir=state_dir,
                ocr_engine=ocr_engine,quarter_selector=quarter_selector,limit=limit,
                lifecycle=owned,max_cycles=max_cycles,emit=emit)
    if lifecycle.is_set():
        return {'period':period,'cycles':0,'error_batches':0,'status':'drained'}
    state_dir = Path(state_dir)
    assert_configured_writable_targets(state_dir, db_path=db_path)
    conn = read_connection(db_path)
    try:
        conn.execute('SELECT work_id FROM document_processing_work LIMIT 0')
    finally:
        conn.close()
    state_dir.mkdir(parents=True,exist_ok=True)
    with (state_dir/'owner.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Configured quarter state already has an owner')
        previous = lifecycle.health_probe
        lifecycle.health_probe = lambda snapshot:role_health(db_path,snapshot,lifecycle.clock())
        try:
            return _run_cycles(db_path=db_path,period=period,state_dir=state_dir,
                ocr_engine=ocr_engine,quarter_selector=quarter_selector,limit=limit,
                lifecycle=lifecycle,max_cycles=max_cycles,emit=emit)
        finally:
            lifecycle.health_probe = previous


def _run_cycles(*, db_path, period, state_dir, ocr_engine, quarter_selector,
                limit, lifecycle, max_cycles, emit):
    emit = emit or (lambda report:print(json.dumps(report,sort_keys=True),flush=True))
    cycles = 0
    errors = 0
    while not lifecycle.is_set():
        try:
            emit(run_batch(db_path=db_path,period=period,state_dir=state_dir,
                ocr_engine=ocr_engine,quarter_selector=quarter_selector,limit=limit,
                lifecycle=lifecycle))
        except Exception as exc:
            errors += 1
            emit({'period':period,'status':'error','error':type(exc).__name__+': '+str(exc)})
        if lifecycle.active_job is not None:
            raise RuntimeError('Processing batch returned with unfinished owned work')
        cycles += 1
        if lifecycle.is_set() or (max_cycles is not None and cycles >= max_cycles):
            break
        lifecycle.wait(60,state='WAITING')
    return {'period':period,'cycles':cycles,'error_batches':errors,
        'status':'drained' if lifecycle.is_set() else 'complete'}
