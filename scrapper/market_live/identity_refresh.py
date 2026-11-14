"""Opt-in daily official instrument overlay, without changing company registry."""
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import io
import json
from pathlib import Path
from urllib.request import build_opener, Request
from zoneinfo import ZoneInfo

from experiments.historical_candle_archive_v1.storage import atomic, dump
from scrapper.market_refresh.quota import reserve
from .admission import admit
from .transport import NoRedirect

URL='https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz'


def fetch():
    with build_opener(NoRedirect).open(Request(URL,headers={'Accept':'application/gzip'}),timeout=20) as response:
        raw=response.read(20*1024*1024+1)
        if len(raw)>20*1024*1024:raise ValueError('bounded instrument metadata required')
        return raw,response.status


def current(config, baseline, now, stop_event, *, fetcher=fetch, clock=lambda:datetime.now(timezone.utc)):
    """At most one new transport per IST day. Failed/uncertain receipts retained.

    An explicit service grant must opt into this public metadata request. The
    original verified company/registry identifiers remain pinned and immutable;
    provider symbol or ISIN drift is checked by the existing IdentityResolver.
    """
    if config.get('identity_refresh_enabled') is not True:return baseline
    root=Path(config['state_dir'])/'identities';root.mkdir(parents=True,exist_ok=True)
    day=now.astimezone(ZoneInfo('Asia/Kolkata')).date().isoformat()
    path=root/(day+'.json')
    with (root/'.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        receipt=json.loads(path.read_text()) if path.exists() else None
        if receipt and receipt['status'] not in ('SUCCESS','RAW_SAVED'):
            raise RuntimeError('instrument metadata blocked; retained delivery requires review')
        if receipt is None:
            admit(config,clock(),stop_event)
            if not reserve(config['provider_quota_path'],clock()):
                raise RuntimeError('instrument metadata quota deferred')
            admit(config,clock(),stop_event)
            receipt=dict(status='UNCERTAIN_DELIVERY',url=URL,attempt_started_at=clock().isoformat())
            dump(path,receipt)
            raw,status=fetcher()
            if status!=200:
                receipt.update(status='HTTP_'+str(status));dump(path,receipt)
                raise RuntimeError('instrument metadata rejected')
            digest=hashlib.sha256(raw).hexdigest()
            raw_path=root/(digest+'.json.gz')
            if not raw_path.exists():atomic(raw_path,raw)
            receipt.update(status='RAW_SAVED',sha256=digest,raw_file=raw_path.name,received_at=clock().isoformat())
            dump(path,receipt)
        raw=(root/receipt['raw_file']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=receipt['sha256']:
            raise ValueError('instrument metadata checksum mismatch')
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
            expanded=compressed.read(100*1024*1024+1)
        if len(expanded)>100*1024*1024:raise ValueError('instrument metadata expansion limit')
        records=json.loads(expanded)
        if not isinstance(records,list) or not records:raise ValueError('instrument metadata records required')
        if receipt['status']!='SUCCESS':
            receipt['status']='SUCCESS';dump(path,receipt)
        return dict(baseline,provider_records=records,observed_at=receipt['received_at'],
                    source_ref=URL+'#sha256='+receipt['sha256'])
