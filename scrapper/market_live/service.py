"""Bounded reconnecting V3 focus stream; supervised by ops.market_live.

No network at import. Ownership is held in the independent live directory only.
"""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import time
import threading
import uuid
from zoneinfo import ZoneInfo

from experiments.historical_candle_archive_v1.storage import dump
from scrapper.market_refresh.service import load_calendar
from .engine import LiveStore
from .admission import admit
from .transport import AuthenticationBlocked, connect
from .watchlist import load


def subscriptions(socket, current, desired):
    for method, keys in (('unsub',sorted(current-desired)),('sub',sorted(desired-current))):
        if keys:
            socket.send(json.dumps(dict(guid=str(uuid.uuid4()),method=method,
                data=dict(mode='full',instrumentKeys=keys))).encode())
    return set(desired)


def stream(config, stop_event, recovery_after=None, *, connector=None,
           clock=lambda:datetime.now(timezone.utc)):
    root=Path(config['state_dir']);root.mkdir(parents=True,exist_ok=True)
    calendar=load_calendar(config['calendar_path'])
    supplier=config.get('_token_supplier')
    if supplier is None:
        from experiments.earnings_reaction_v1.execute_ta02d import secure_supplier
        supplier=secure_supplier
    connector=connector or (lambda:connect(supplier,admission=lambda:admit(config,clock(),stop_event)))
    account_lock=Path(config.get('provider_quota_path',str(root/'.account'))+'.stream-owner.lock')
    account_lock.parent.mkdir(parents=True,exist_ok=True)
    with account_lock.open('a+b') as account, (root/'.stream.lock').open('a+b') as lock:
        fcntl.flock(account,fcntl.LOCK_EX|fcntl.LOCK_NB)
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        store=LiveStore(root,calendar)
        failures=0; last_received=None; last_provider=None; prices={}; versions={}
        recovery_thread=None; recovery_last=None; published_at=None; chart=None
        def reconcile(keys, at):
            from .recovery import recover
            try:
                result=recover(config,keys,at,stop_event,supplier=supplier)
            except Exception as error:
                result=dict(status='BLOCKED',error_type=type(error).__name__)
            dump(root/'recovery_status.json',result)
        stale=config.get('stale_seconds',90)
        if not 10<=stale<=300: raise ValueError('stale_seconds must be 10..300')
        try:
            while not stop_event.is_set():
                admit(config,clock(),stop_event)
                definition,resolved=load(config,clock(),stop_event)
                keys=set(resolved)
                status=dict(status='UNKNOWN',connected=False,requested_instruments=sorted(keys),
                    watchlist_version=definition['version'],recovery_after=recovery_after)
                try:
                    with connector() as socket:
                        admit(config,clock(),stop_event)
                        subscribed=subscriptions(socket,set(),keys)
                        connected_at=clock(); last_key={k:connected_at for k in subscribed}; last_received=None
                        while not stop_event.is_set():
                            now=clock()
                            admit(config,now,stop_event)
                            if (config.get('provider_quota_path') and
                                (recovery_thread is None or not recovery_thread.is_alive()) and
                                (recovery_last is None or (now-recovery_last).total_seconds()>=300)):
                                recovery_last=now
                                recovery_thread=threading.Thread(target=reconcile,args=(set(subscribed),now),daemon=True)
                                recovery_thread.start()
                            definition,resolved=load(config,now,stop_event)
                            desired=set(resolved)
                            fingerprint=hashlib.sha256(json.dumps(definition,sort_keys=True).encode()).hexdigest()
                            if definition['version'] in versions and versions[definition['version']]!=fingerprint:
                                raise ValueError('watchlist version reused for changed membership')
                            versions[definition['version']]=fingerprint
                            for key in desired-subscribed: last_key[key]=now
                            for key in subscribed-desired: last_key.pop(key,None); prices.pop(key,None)
                            if any((now-last_key[k]).total_seconds()>stale for k in subscribed & desired):
                                raise ConnectionError('stale subscribed instrument')
                            subscribed=subscriptions(socket,subscribed,desired)
                            try:
                                raw=socket.recv(timeout=1)
                            except TimeoutError:
                                if (now-(last_received or connected_at)).total_seconds()>stale:
                                    raise ConnectionError('missing feed messages') from None
                                if any((now-last_key.get(k,connected_at)).total_seconds()>stale for k in subscribed):
                                    raise ConnectionError('stale subscribed instrument') from None
                                continue
                            now=clock()  # actual receipt after blocking recv, never the poll-start clock
                            frame=store.ingest(raw,now,subscribed)
                            last_received=now
                            last_provider=frame['currentTs']
                            if now.timestamp()-last_provider/1000>stale:
                                raise ConnectionError('stale provider feed timestamp')
                            for k,v in frame['feeds'].items():
                                if k in subscribed:
                                    last_key[k]=now
                                    if v.get('ltpc'): prices[k]=v['ltpc']
                            if published_at is None or (now-published_at).total_seconds()>=5:
                                chart=store.publish(subscribed,now.astimezone(ZoneInfo('Asia/Kolkata')).date(),now)
                                published_at=now
                            status.update(status='LIVE',connected=True,subscribed_instruments=sorted(subscribed),
                                watchlist_version=definition['version'],last_received_at=now.isoformat(),
                                provider_timestamp_ms=last_provider,prices=prices,
                                candle_status={r['instrument_key']:r['status'] for r in chart['instruments']},
                                reconciliation_required=any(r['missing_minutes'] for r in chart['instruments']))
                            dump(root/'stream_status.json',status)
                except AuthenticationBlocked:
                    status.update(status='BLOCKED',reason='CREDENTIAL_EXPIRED_OR_DENIED',connected=False)
                    dump(root/'stream_status.json',status)
                    return
                except (OSError,ConnectionError,TimeoutError) as error:
                    failures+=1
                    status.update(status='STALE',connected=False,reconnect_attempts=failures,
                        error_type=type(error).__name__,reconciliation_required=True)
                    dump(root/'stream_status.json',status)
                    if failures>=min(20,config.get('max_reconnects',8)): return
                    stop_event.wait(min(60,2**min(failures,6)))
                except Exception as error:
                    # Includes malformed payload, identity drift and protocol errors.
                    # Do not retry validation failures or leak authenticated URLs.
                    status.update(status='BLOCKED',connected=False,error_type=type(error).__name__)
                    dump(root/'stream_status.json',status)
                    return
        finally:
            if recovery_thread: recovery_thread.join(timeout=2)
            store.close()
            path=root/'stream_status.json'
            if path.exists():
                status=json.loads(path.read_text());status.update(connected=False)
                if stop_event.is_set(): status['status']='FRESH' if not status.get('reconciliation_required',True) else 'GAP'
                dump(path,status)
