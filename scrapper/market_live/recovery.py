"""Bounded gap reconciliation in a separate worker, never synthetic filling."""
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from experiments.historical_candle_archive_v1.storage import dump
from experiments.technical_fix_v2.update import get
from scrapper.market_refresh.planner import aware
from scrapper.market_refresh.service import load_calendar
from .engine import LiveStore


def recover(config, keys, now, stop_event, *, supplier, fetch=get, clock=lambda:datetime.now(timezone.utc)):
    root=Path(config['state_dir'])
    calendar=load_calendar(config['calendar_path'])
    # Recovery is finite: recent missed sessions only. Older gaps remain reported.
    days=min(28,max(1,config.get('recovery_days',5)))
    today=aware(now).astimezone(ZoneInfo('Asia/Kolkata')).date()
    maximum=min(100,max(1,config.get('recovery_max_requests',50)))
    root.mkdir(parents=True,exist_ok=True)
    with (root/'.recovery.lock').open('a+b') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return dict(status='BLOCKED',reason='RECOVERY_BUSY')
        store=LiveStore(root,calendar)
        attempted=0
        try:
            for key in sorted(keys):
                for offset in range(days):
                    day=today-timedelta(days=offset)
                    if stop_event.is_set(): return dict(status='STOPPED',requests=attempted)
                    from .admission import admit
                    admit(config,clock(),stop_event)
                    chart=store.chart(key,day,now)
                    if chart['status']!='GAP': continue
                    # Retry only for changed missing-minute coverage, not each wake.
                    scope=dict(key=key,day=str(day),missing=chart['missing_minutes'])
                    ident=hashlib.sha256(json.dumps(scope,sort_keys=True).encode()).hexdigest()
                    receipt_path=root/'recovery'/(ident+'.json')
                    previous=json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
                    if previous:
                        if previous.get('status')=='RAW_SAVED':
                            raw=(root/previous['raw_path']).read_bytes()
                            if hashlib.sha256(raw).hexdigest()!=previous['sha256']:
                                raise ValueError('recovery checksum mismatch')
                            store.ingest_rest(raw,previous['received_at'],key,day)
                            previous['status']='SUCCESS';dump(receipt_path,previous)
                        if previous.get('status')!='SUCCESS':
                            return dict(status='BLOCKED',reason=previous['status'],requests=attempted)
                        continue
                    if attempted>=maximum: return dict(status='GAP',reason='RECOVERY_BUDGET',requests=attempted)
                    from scrapper.market_refresh.quota import reserve
                    if not reserve(config['provider_quota_path'],aware(clock()).timestamp()):
                        return dict(status='BLOCKED',reason='PROVIDER_QUOTA_EXHAUSTED',requests=attempted)
                    base='https://api.upstox.com/v3/historical-candle/'
                    key_path=quote(key,safe='')
                    url=(base+'intraday/'+key_path+'/minutes/1' if day==today
                         else base+key_path+'/minutes/1/'+str(day)+'/'+str(day))
                    token=supplier()
                    admit(config,clock(),stop_event)
                    receipt=dict(status='UNCERTAIN_DELIVERY',scope=scope,url=url)
                    dump(receipt_path,receipt)
                    raw,http=fetch(dict(kind='STOCK',url=url),token)
                    attempted+=1
                    if token.encode() in raw: raise ValueError('unsafe credential echo')
                    if http!=200:
                        receipt.update(status='HTTP_'+str(http),response_sha256=hashlib.sha256(raw).hexdigest())
                        dump(receipt_path,receipt)
                        return dict(status='BLOCKED',reason=receipt['status'],requests=attempted)
                    # Receipt time is actual response delivery, never historical candle time.
                    received=aware(clock()).isoformat()
                    digest=hashlib.sha256(raw).hexdigest()
                    path=root/'raw'/(digest+'.json');path.parent.mkdir(exist_ok=True)
                    from experiments.historical_candle_archive_v1.storage import atomic
                    if not path.exists(): atomic(path,raw)
                    receipt.update(status='RAW_SAVED',received_at=received,sha256=digest,
                                   raw_path=str(path.relative_to(root)))
                    dump(receipt_path,receipt)
                    store.ingest_rest(raw,received,key,day)
                    receipt['status']='SUCCESS';dump(receipt_path,receipt)
                    stop_event.wait(1.1)
            return dict(status='COMPLETE',requests=attempted)
        finally:
            store.publish(keys,today,clock())
            store.close()
