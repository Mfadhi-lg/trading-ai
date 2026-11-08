"""Owner repair queue: metadata in the repo, original provider bytes on Passport."""
from collections import Counter,defaultdict
import csv
import fcntl
import gzip
import io
import json

from .storage import OUT,dump,atomic,sha
from .acquire import normalize
from .control import checkpoint

EVIDENCE=OUT/'hcd02'
FIELDS=['company_id','symbol','instrument_key','timeframe','timestamp','reason','request_id',
        'received_at','raw_sha256','original_response_path','pending_requests','action']


def dictionaries(cursor):
    names=[c[0] for c in cursor.description]
    return [dict(zip(names,r)) for r in cursor.fetchall()]


def refresh(a,manifest,scope):
    with (EVIDENCE/'.repair-queue.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _refresh(a,manifest,scope)


def _refresh(a,manifest,scope):
    checkpoint()
    a.guard()
    full=json.loads((OUT/'full-universe-manifest.json').read_text()) if (OUT/'full-universe-manifest.json').exists() else manifest
    companies={c['company_id']:c for c in full['companies']+full.get('historically_eligible_extension',[])}
    companies.update({c['company_id']:c for c in manifest['companies']})
    requests={r['request_id']:r for r in dictionaries(a.db.execute('SELECT * FROM requests'))}
    candidates=dictionaries(a.db.execute("""SELECT DISTINCT request_id FROM datasets
        WHERE definition='UPSTOX_NATIVE' AND parquet_path LIKE 'quarantine/%'
        AND completeness NOT LIKE 'SUPERSEDED%'"""))
    events=[];resolved_daily=set()
    for candidate in candidates:
        checkpoint()
        saved=requests.get(candidate['request_id'])
        if not saved or saved['status']!='SUCCESS':continue
        cache=EVIDENCE/'repair-cache'/(saved['request_id']+'.json')
        old=json.loads(cache.read_text()) if cache.exists() else None
        if old and old.get('policy')=='hcd02-owner-repairs-v2' and old['sha256']==saved['raw_sha256']:
            events.extend(old['events'])
            if old.get('equivalent_daily_proven_valid'):resolved_daily.add((saved['company_id'],'Daily'))
            continue
        tf={('weeks',1):'Weekly',('days',1):'Daily',('hours',1):'1-hour',('hours',4):'4-hour',('minutes',1):'1-minute'}[(saved['unit'],saved['interval'])]
        raw=gzip.decompress((a.root/saved['raw_path']).read_bytes())
        if sha(raw)!=saved['raw_sha256']:raise RuntimeError('STOP_REPAIR_SOURCE_CHECKSUM')
        rows,_=normalize(raw,dict(saved,timeframe=tf),saved['received_at'],quarantine_negative=True)
        equivalent=set()
        if tf=='Daily':
            days=defaultdict(list)
            for r in rows:days[r['start'][:10]].append(r)
            for day,part in days.items():
                if len(part)>1 and len({tuple(r[k] for k in ('open','high','low','close','volume','open_interest')) for r in part})==1:
                    if all(r['quality'] in ('OHLC_VALID','AMBIGUOUS_DAILY_SESSION_BUCKET') for r in part):equivalent.add(day)
        part=[]
        for r in rows:
            if r['quality']=='OHLC_VALID' or r['start'][:10] in equivalent:continue
            zero=all(r[k]==0 for k in ('open','high','low','close','volume','open_interest'))
            part.append(dict(company_id=saved['company_id'],symbol=companies.get(saved['company_id'],{}).get('symbol',''),
                instrument_key=saved['instrument_key'],timeframe=tf,timestamp=r['start'],
                reason='ZERO_OHLCV_PLACEHOLDER' if zero else r['quality'],request_id=saved['request_id'],
                received_at=saved['received_at'],raw_sha256=saved['raw_sha256'],
                original_response_path=str(a.root/saved['raw_path']),pending_requests='',action='REVIEW_OR_REPLACE_SOURCE; QUARANTINED'))
        if equivalent:resolved_daily.add((saved['company_id'],'Daily'))
        dump(cache,dict(policy='hcd02-owner-repairs-v2',sha256=saved['raw_sha256'],events=part,equivalent_daily_proven_valid=bool(equivalent)))
        events.extend(part)
    # Keep the latest retained bad observation for each actual source timestamp.
    unique={}
    for event in sorted(events,key=lambda r:r['received_at']):
        key=(event['instrument_key'],event['timeframe'],event['timestamp'])
        latest=a.db.execute("""SELECT quality FROM candle_observations WHERE instrument_key=?
            AND timeframe=? AND definition='UPSTOX_NATIVE' AND start=? ORDER BY observed_at DESC LIMIT 1""",key).fetchone()
        if latest and latest[0]=='OHLC_VALID':continue
        unique[key]=event
    events=list(unique.values())
    for r in requests.values():
        if r['status'] in ('SUCCESS','RAW_SAVED','IN_FLIGHT'):continue
        events.append(dict(company_id=r['company_id'],symbol=companies.get(r['company_id'],{}).get('symbol',''),
            instrument_key=r['instrument_key'],timeframe=f"{r['unit']}/{r['interval']}",timestamp=f"{r['from_date']}..{r['to_date']}",
            reason=r['error_kind'] or r['status'],request_id=r['request_id'],received_at=r['received_at'],raw_sha256=r['raw_sha256'],
            original_response_path=str(a.root/r['raw_path']) if r['raw_path'] else '',pending_requests='',action='STOCK_OR_REQUEST_HELD; OTHER_STOCKS_CONTINUE'))
    valid={tuple(r) for r in a.db.execute("""SELECT company_id,timeframe FROM datasets WHERE definition='UPSTOX_NATIVE'
        AND parquet_path LIKE 'normalized/%' AND completeness NOT LIKE 'SUPERSEDED%' GROUP BY company_id,timeframe""").fetchall()}
    valid|=resolved_daily
    groups=defaultdict(list)
    for q in manifest['requests']:groups[q['company_id'],q['timeframe']].append(q)
    for (cid,tf),qs in groups.items():
        complete=all(requests.get(q['request_id'],{}).get('status')=='SUCCESS' for q in qs)
        held=[requests[q['request_id']] for q in qs if q['request_id'] in requests and requests[q['request_id']]['status'] in ('ISOLATED_FAILURE','QUARANTINED_RESPONSE')]
        if held or (complete and (cid,tf) not in valid):
            r=held[0] if held else requests[qs[-1]['request_id']]
            events.append(dict(company_id=cid,symbol=companies.get(cid,{}).get('symbol',''),instrument_key=r['instrument_key'],timeframe=tf,timestamp='',
                reason='HELD_REQUEST_WINDOWS' if held else 'NO_VALID_COMPLETED_SOURCE_HISTORY',request_id=r['request_id'],received_at=r['received_at'],
                raw_sha256=r['raw_sha256'],original_response_path=str(a.root/r['raw_path']) if r['raw_path'] else '',
                pending_requests=sum(requests.get(q['request_id'],{}).get('status')!='SUCCESS' for q in qs),
                action='REVIEW_PROVIDER_SUPPORT_OR_LISTING_HISTORY; DO_NOT_FILL'))
    for cid,c in companies.items():
        if not c.get('instrument_key'):
            events.append(dict(company_id=cid,symbol=c.get('symbol') or c.get('name',''),instrument_key='',timeframe='ALL',timestamp='',
                reason='PROVIDER_IDENTITY_UNMAPPED',request_id='',received_at='',raw_sha256='',original_response_path='',pending_requests='',
                action='RESOLVE_RETAINED_EXCHANGE_IDENTIFIER; NO_SUBSTITUTION_APPLIED'))
        elif c['instrument_key'].startswith('BSE_') and any((cid,tf) in valid for tf in ('1-hour','4-hour')):
            r=next((r for r in requests.values() if r['company_id']==cid and r['raw_path']),None)
            events.append(dict(company_id=cid,symbol=c.get('symbol',''),instrument_key=c['instrument_key'],timeframe='1-hour;4-hour',timestamp='',
                reason='BSE_OWNER_SESSION_CALENDAR_UNVERIFIED',request_id=r['request_id'] if r else '',received_at=r['received_at'] if r else '',
                raw_sha256=r['raw_sha256'] if r else '',original_response_path=str(a.root/r['raw_path']) if r else '',pending_requests='',
                action='VERIFY_EXCHANGE_SESSION_CALENDAR; NATIVE_SOURCE_RETAINED'))
    special=EVIDENCE/'full-special-session-coverage.json'
    if not special.exists():special=EVIDENCE/'scaled-special-session-coverage.json'
    if special.exists():
        for r in json.loads(special.read_text())['checks']:
            if r['status']=='SESSION_GRID_AVAILABLE':continue
            c=companies.get(r['company_id'],{})
            events.append(dict(company_id=r['company_id'],symbol=r['symbol'],instrument_key=c.get('instrument_key',''),timeframe=r['timeframe'],timestamp=r['day'],
                reason='SESSION_REPAIR_UNRESOLVED_OR_PRELISTING',request_id='',received_at='',raw_sha256='',original_response_path='',pending_requests='',
                action='CHECK_LISTING_DATE_AND_MINUTE_COVERAGE; NO_FILL'))
    events.sort(key=lambda r:(r['company_id'],r['timeframe'],r['timestamp'] or '',r['reason']))
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,FIELDS);writer.writeheader();writer.writerows(events)
    atomic(EVIDENCE/'repair-queue.csv',stream.getvalue().encode())
    dump(EVIDENCE/'repair-queue-summary.json',dict(scope=scope,items=len(events),companies=len({r['company_id'] for r in events}),
        reasons=Counter(r['reason'] for r in events),original_values='Original immutable JSON gzip responses remain on Passport at the paths in the CSV',
        resolved_equivalent_daily_aliases_excluded=True,unattended_schedules_enabled=False))
    return len(events)
