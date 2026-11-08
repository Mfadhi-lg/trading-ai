"""Bounded pilot acquisition; no full-universe execution entry point."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import argparse
import gzip
import json
import subprocess
import time
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from urllib.parse import quote
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from .storage import Archive, OUT, dump, sha
from .prepare import PILOT
from experiments.earnings_reaction_v1.execute_ta02d import secure_supplier
from experiments.earnings_reaction_v1.upstox_candles import url_for

IST=ZoneInfo('Asia/Kolkata')

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

def normalize(raw,q,received,quarantine_negative=False):
    def unique(pairs):
        r={}
        for k,v in pairs:
            if k in r: raise ValueError('duplicate_json_key')
            r[k]=v
        return r
    payload=json.loads(raw,parse_float=Decimal,object_pairs_hook=unique,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite')))
    if payload.get('status')!='success' or not isinstance(payload.get('data',{}).get('candles'),list):
        raise ValueError('invalid_provider_schema')
    rows=payload['data']['candles'];seen=set();result=[];issues=[];response_digest=sha(raw)
    for r in rows:
        if not isinstance(r,list) or len(r)!=7: raise ValueError('invalid_candle_shape')
        t=datetime.fromisoformat(r[0])
        if t.tzinfo is None: raise ValueError('naive_provider_timestamp')
        t=t.astimezone(IST)
        if not q['from_date']<=str(t.date())<=q['to_date']: raise ValueError('outside_request')
        if t in seen: raise ValueError('duplicate_timestamp')
        seen.add(t)
        if any(isinstance(v,bool) or not isinstance(v,(int,Decimal)) for v in r[1:]):
            raise ValueError('invalid_numeric_type')
        o,h,l,c,v,oi=map(Decimal,r[1:])
        if any(not x.is_finite() for x in (o,h,l,c,v,oi)) or v!=v.to_integral_value():
            raise ValueError('invalid_numeric_value')
        negative=any(x<0 for x in (o,h,l,c,v,oi))
        if negative and not quarantine_negative: raise ValueError('invalid_numeric_value')
        if negative: issues.append(dict(timestamp=r[0],kind='NEGATIVE_SOURCE_NUMERIC'))
        valid=l<=min(o,c)<=max(o,c)<=h and l>0
        if not valid: issues.append(dict(timestamp=r[0],kind='OHLC_CONSTRAINT_FAILURE'))
        # Unknown session calendar cannot confer completion or point-in-time delivery.
        # Conservative upper bounds are used until an official session row is supplied.
        if q['unit']=='weeks': end=t+timedelta(days=7)
        elif q['unit']=='days': end=t.replace(hour=23,minute=59,second=59)
        elif q['unit']=='hours': end=t+timedelta(hours=q['interval'])
        else: end=t+timedelta(minutes=q['interval'])
        result.append(dict(company_id=q['company_id'],instrument_key=q['instrument_key'],
            timeframe=q['timeframe'],definition='UPSTOX_NATIVE',provider_timestamp=r[0],start=t.isoformat(),
            end_upper_bound=end.isoformat(),end_basis='CONSERVATIVE_UNVERIFIED_CALENDAR',
            open=o,high=h,low=l,close=c,volume=int(v),open_interest=oi,
            observed_at=received,source='Upstox Intraday Candle V3' if q.get('role')=='LIVE_INTRADAY_SNAPSHOT' else 'Upstox Historical Candle V3',
            source_response_sha256=response_digest,
            status='COMPLETED_RETROSPECTIVE' if end<=datetime.fromisoformat(received) else 'PROVISIONAL',
            quality='QUARANTINED_NUMERIC' if negative else 'OHLC_VALID' if valid else 'QUARANTINED_OHLC',
            historical_delivery_verified=False,corporate_action_status='UNKNOWN_PROVIDER_ADJUSTMENT_BASIS'))
    if q['unit']=='days':
        date_starts={}
        for r in result:date_starts.setdefault(r['start'][:10],[]).append(r)
        for ds,part in date_starts.items():
            if len(part)>1:
                issues.append(dict(timestamp=ds,kind='AMBIGUOUS_DAILY_SESSION_BUCKET',starts=[r['start'] for r in part]))
                for r in part:
                    if r['quality']=='OHLC_VALID':r['quality']='AMBIGUOUS_DAILY_SESSION_BUCKET'
    return sorted(result,key=lambda r:r['start']),issues

def fetch(q,token,retain_error_body=False):
    request=SimpleNamespace(instrument_key=q['instrument_key'],unit=q['unit'],interval=q['interval'],
        from_date=datetime.fromisoformat(q['from_date']).date(),to_date=datetime.fromisoformat(q['to_date']).date())
    config='header = "Accept: application/json"\nheader = "Authorization: Bearer '+token+'"\n'
    url=url_for(request)
    if q.get('role')=='LIVE_INTRADAY_SNAPSHOT':
        if (q['company_id'],q['instrument_key'],q['unit'],q['interval'])!=(555,'NSE_EQ|INE900B01029','minutes',1):
            raise PermissionError('live pilot snapshot restricted to Kabra1m')
        url='https://api.upstox.com/v3/historical-candle/intraday/'+quote(q['instrument_key'],safe='')+'/minutes/1'
    attempts=[]
    for attempt in range(3):
        p=subprocess.run(['/usr/bin/curl','--disable','--http1.1','--config','-','--silent','--proto','=https',
            '--max-redirs','0','--retry','0','--max-time','45','--write-out','\n%{http_code}',url],
            input=config.encode(),capture_output=True)
        attempts.append(dict(attempt=attempt+1,transport_exit=p.returncode,at=datetime.now(timezone.utc).isoformat()))
        if not p.returncode: break
        if p.returncode==35:
            # Alternate verified TLS stack for the same allowlisted GET;
            # system SecureTransport intermittently fails before HTTP delivery.
            # urllib uses default certificate validation; redirects are refused.
            try:
                req=Request(url,headers={'Accept':'application/json','Authorization':'Bearer '+token})
                with build_opener(NoRedirect()).open(req,timeout=45) as response:
                    raw=response.read();status=response.status
                if token.encode() in raw: raise RuntimeError('UNSAFE_CREDENTIAL_ECHO')
                attempts.append(dict(attempt=attempt+1,transport='urllib_verified_TLS',http_status=status,
                                     at=datetime.now(timezone.utc).isoformat()))
                dump(OUT/'transport-receipts'/f'{q["request_id"]}.json',attempts)
                return raw,status
            except HTTPError as error:
                dump(OUT/'transport-receipts'/f'{q["request_id"]}.json',attempts)
                body=error.read() if retain_error_body else b''
                if token.encode() in body: raise RuntimeError('UNSAFE_CREDENTIAL_ECHO')
                return body,error.code
            except (URLError,OSError):
                attempts.append(dict(attempt=attempt+1,transport='urllib_verified_TLS',status='CONNECTION_FAILURE'))
        if p.returncode not in (6,7,28,35,52,56) or attempt==2:
            dump(OUT/'transport-receipts'/f'{q["request_id"]}.json',attempts)
            raise RuntimeError(f'TRANSPORT_EXIT_{p.returncode}')
        time.sleep(2**(attempt+1))
    dump(OUT/'transport-receipts'/f'{q["request_id"]}.json',attempts)
    raw,status=p.stdout.rsplit(b'\n',1)
    if token.encode() in raw: raise RuntimeError('UNSAFE_CREDENTIAL_ECHO')
    return raw,int(status)

def execute(limit=1):
    manifest=json.loads((OUT/'pilot-manifest.json').read_text())
    if tuple(c['symbol'] for c in manifest['companies'])!=PILOT or len(manifest['companies'])!=20:
        raise PermissionError('STOP_PILOT_SCOPE')
    allowed={c['company_id']:c['instrument_key'] for c in manifest['companies']}
    for q in manifest['requests']:
        if allowed.get(q['company_id'])!=q['instrument_key']: raise PermissionError('STOP_SCOPE_IDENTITY')
    archive=Archive()
    token=secure_supplier()
    if any(ch in token for ch in '\r\n"\\'): raise ValueError('unsafe_credential')
    executed=0
    for q in manifest['requests']:
        archive.guard()
        previous=archive.db.execute('SELECT status,raw_sha256,raw_path,received_at,attempts FROM requests WHERE request_id=?',
                                    (q['request_id'],)).fetchone()
        receipt_path=archive.root/'receipts'/(q['request_id']+'.json')
        if previous and previous[0] not in ('SUCCESS','RAW_SAVED') and receipt_path.exists():
            receipt=json.loads(receipt_path.read_text())
            raw=gzip.decompress((archive.root/receipt['raw_path']).read_bytes())
            if sha(raw)!=receipt['raw_sha256']: raise RuntimeError('STOP_RECEIPT_RECOVERY_CHECKSUM')
            archive.db.execute("UPDATE requests SET status='RAW_SAVED',raw_sha256=?,raw_path=?,received_at=?,http_status=200 WHERE request_id=?",
                (receipt['raw_sha256'],receipt['raw_path'],receipt['received_at'],q['request_id']))
            archive.db.commit()
            previous=archive.db.execute('SELECT status,raw_sha256,raw_path,received_at,attempts FROM requests WHERE request_id=?',
                                       (q['request_id'],)).fetchone()
        if previous and previous[0] in ('SUCCESS','RAW_SAVED'):
            raw=gzip.decompress((archive.root/previous[2]).read_bytes())
            if sha(raw)!=previous[1]: raise RuntimeError('STOP_RESTART_CHECKSUM')
            records,issues=normalize(raw,q,previous[3])
            archive.parquet(records,q,previous[1],previous[3])
            if previous[0]=='RAW_SAVED':
                archive.db.execute("UPDATE requests SET status='SUCCESS',row_count=? WHERE request_id=?",(len(records),q['request_id']))
                archive.db.commit()
            continue
        if previous and previous[4]>=3: raise RuntimeError('STOP_BOUNDED_RECOVERY_EXHAUSTED')
        if executed>=limit: break
        started=time.monotonic();received=datetime.now(timezone.utc).isoformat()
        archive.db.execute('''INSERT INTO requests(request_id,company_id,instrument_key,unit,interval,from_date,to_date,status,attempts)
            VALUES(?,?,?,?,?,?,?,'IN_FLIGHT',1) ON CONFLICT(request_id) DO UPDATE SET status='IN_FLIGHT',attempts=attempts+1''',
            (q['request_id'],q['company_id'],q['instrument_key'],q['unit'],q['interval'],q['from_date'],q['to_date']))
        archive.db.commit()
        status=None
        try:
            raw,status=fetch(q,token)
            received=datetime.now(timezone.utc).isoformat()
            if status!=200: raise RuntimeError(f'HTTP_{status}')
            records,issues=normalize(raw,q,received)
            digest,path=archive.raw(raw)
            archive.guard()
            dump(receipt_path,dict(request_id=q['request_id'],raw_sha256=digest,raw_path=path,received_at=received))
            archive.db.execute("UPDATE requests SET status='RAW_SAVED',raw_sha256=?,raw_path=?,received_at=?,http_status=? WHERE request_id=?",
                               (digest,path,received,status,q['request_id']))
            archive.db.commit()
            archive.parquet(records,q,digest,received)
            archive.db.execute('''UPDATE requests SET status='SUCCESS',raw_sha256=?,raw_path=?,received_at=?,
                http_status=?,row_count=?,error_kind=NULL,elapsed_seconds=? WHERE request_id=?''',
                (digest,path,received,status,len(records),time.monotonic()-started,q['request_id']))
            archive.db.commit()
            if issues: dump(OUT/'quality'/f'{q["request_id"]}.json',issues)
            executed+=1
            print(json.dumps(dict(request=q['request_id'],company_id=q['company_id'],timeframe=q['timeframe'],
                                  rows=len(records),status='SUCCESS')),flush=True)
        except Exception as e:
            # Never serialize provider bodies, credentials or arbitrary exception strings.
            kind=str(e) if isinstance(e,RuntimeError) and str(e).startswith(('HTTP_','TRANSPORT_EXIT_')) else type(e).__name__
            archive.guard()
            archive.db.execute("UPDATE requests SET status='STOPPED',http_status=?,error_kind=?,elapsed_seconds=? WHERE request_id=?",
                               (status,kind,time.monotonic()-started,q['request_id']))
            archive.db.commit()
            dump(OUT/'acquisition-status.json',dict(status='STOPPED',reason=kind,request=q,
                 received_at=received,full_universe_executed=False,schedules_enabled=False,
                 credential_values_retained=False,error_bodies_retained=False))
            print(json.dumps(dict(status='STOPPED',reason=kind)),flush=True)
            return False
        # Serial 1.1-second minimum cycle <= 1637 requests per 30min, with headroom.
        time.sleep(max(0,1.1-(time.monotonic()-started)))
    dump(OUT/'acquisition-status.json',dict(status='BOUNDED_BATCH_COMPLETE',new_requests=executed,
        full_universe_executed=False,schedules_enabled=False))
    return True

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--limit',type=int,default=1)
    args=p.parse_args()
    if not 1<=args.limit<=1000: p.error('pilot batch limit 1..1000 required')
    execute(args.limit)
