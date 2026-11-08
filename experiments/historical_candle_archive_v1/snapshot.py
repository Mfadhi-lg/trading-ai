"""One bounded live Kabra minute snapshot; never a background schedule."""
from datetime import datetime,timedelta,timezone
import json
from .storage import Archive,OUT,dump,sha
from .acquire import fetch,normalize,secure_supplier,IST
from .calendar import day_record
from .observations import aggregate_minutes,select_asof

def main():
    a=Archive();now=datetime.now(IST);ds=str(now.date())
    if ds!='2026-11-05': raise PermissionError('HCD01 live snapshot is bounded to2026-11-05')
    record=day_record(ds)
    if record['status']!='REGULAR_SCHEDULED': raise ValueError('review explicit session first')
    session=record['sessions'][0]
    if not datetime.fromisoformat(session['opens_at'])<=now<datetime.fromisoformat(session['closes_at']):
        raise RuntimeError('live developing snapshot requires open session')
    q=dict(company_id=555,instrument_key='NSE_EQ|INE900B01029',unit='minutes',interval=1,
        from_date=ds,to_date=ds,timeframe='1-minute',role='LIVE_INTRADAY_SNAPSHOT')
    q['request_id']=sha(json.dumps(q,sort_keys=True).encode())
    result_path=OUT/'live-provisional-snapshot.json'
    if result_path.exists():
        print('Existing bounded live snapshot retained; no repeated GET');return
    raw,status=fetch(q,secure_supplier());received=datetime.now(timezone.utc).isoformat()
    if status!=200:
        dump(result_path,dict(status='HTTP_STOP',http_status=status));return
    rows,issues=normalize(raw,q,received);digest,path=a.raw(raw)
    a.parquet(rows,q,digest,received,definition='UPSTOX_INTRADAY_NATIVE')
    complete=[r for r in rows if datetime.fromisoformat(r['end_upper_bound'])<=datetime.fromisoformat(received)]
    if not complete:
        dump(result_path,dict(status='NO_COMPLETED_SOURCE_MINUTES',received_at=received,raw_sha256=digest));return
    cutoff=min(datetime.fromisoformat(received).astimezone(IST).replace(second=0,microsecond=0),
               datetime.fromisoformat(complete[-1]['end_upper_bound']))
    results=[]
    for tf,n in [('1-hour',60),('4-hour',240)]:
        try:
            bars=aggregate_minutes(complete,session,n,cutoff,received)
        except ValueError:
            results.append(dict(timeframe=tf,status='SOURCE_MINUTE_GAP; NO_SYNTHETIC_PREFIX'));continue
        for r in bars:
            r['definition']='OWNER_LIVE_SESSION_SNAPSHOT'
            r['historical_delivery_verified']=True # actual captured observation from this point onward only
            if r['status']=='COMPLETED_RETROSPECTIVE': r['status']='COMPLETED'
        request=dict(q,timeframe=tf,request_id=sha((q['request_id']+tf).encode()))
        a.parquet(bars,request,digest,received,definition='OWNER_LIVE_SESSION_SNAPSHOT')
        results.append(dict(timeframe=tf,status='CAPTURED',completed=sum(r['status']=='COMPLETED' for r in bars),
            provisional=sum(r['status']=='PROVISIONAL' for r in bars),
            visible_one_microsecond_before_receipt=len(select_asof(bars,datetime.fromisoformat(received)-timedelta(microseconds=1),True)),
            visible_at_receipt=len(select_asof(bars,received,True))))
    dump(result_path,dict(status='CAPTURED',instrument_key=q['instrument_key'],received_at=received,
        source_prefix_through=cutoff.isoformat(),raw_sha256=digest,raw_path=path,results=results,
        issues=issues,definition='Actual receipt now; no assertion of historical live delivery before this receipt',
        source_url='https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/',
        unattended_schedule=False))
    print(result_path.read_text())

if __name__=='__main__':main()
