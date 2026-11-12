"""Pilot verification from retained Passport evidence; no network access."""
from collections import defaultdict,Counter
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
import gzip
import json
import sqlite3
import time
from .storage import Archive,OUT,dump,sha
from .acquire import normalize
from .prepare import csvout,END
from .calendar import day_record
from .observations import aggregate_minutes,boundaries,select_asof

FIELDS=('open','high','low','close','volume')

def comparable(row):
    return tuple(Decimal(row[k]) for k in (*FIELDS,'open_interest'))

def load(a):
    manifest=json.loads((OUT/'pilot-manifest.json').read_text())
    expected={q['request_id']:q for q in manifest['requests']}
    groups=defaultdict(dict);duplicates=[];requests=[];issues=[]
    a.db.row_factory=sqlite3.Row
    records=list(a.db.execute("SELECT * FROM requests WHERE status='SUCCESS' ORDER BY received_at"))
    for record in records:
        record=dict(record);q=expected.get(record['request_id'])
        if q is None: continue
        raw=gzip.decompress((a.root/record['raw_path']).read_bytes())
        if sha(raw)!=record['raw_sha256']: raise ValueError('raw checksum failure')
        rows,problems=normalize(raw,q,record['received_at'])
        issues.extend(dict(company_id=q['company_id'],timeframe=q['timeframe'],**p) for p in problems)
        for row in rows:
            key=(q['company_id'],q['timeframe'])
            if row['start'] in groups[key]:
                duplicates.append(dict(company_id=q['company_id'],timeframe=q['timeframe'],timestamp=row['start'],
                                       role=q['role'],changed=comparable(groups[key][row['start']])!=comparable(row)))
            groups[key][row['start']]=row
        requests.append(dict(**q,status=record['status'],rows=len(rows),
            raw_sha256=record['raw_sha256'],received_at=record['received_at'],raw_path=record['raw_path'],
            elapsed_seconds=record['elapsed_seconds']))
    return {k:sorted(v.values(),key=lambda r:r['start']) for k,v in groups.items()},requests,duplicates,issues

def differences(a,b):
    return {k:str(Decimal(a[k])-Decimal(b[k])) for k in FIELDS}

def reconcile_sample(a,groups,pilot):
    results=[];custom=[];prefix_results=[]
    for company in pilot[:3]:
        cid=company['company_id'];minutes=groups.get((cid,'1-minute'),[])
        for ds in sorted({r['start'][:10] for r in minutes}):
            session=day_record(ds)['sessions'][0]
            part=[r for r in minutes if r['start'][:10]==ds]
            for tf,n in [('1-hour',60),('4-hour',240)]:
                try:
                    aggregated=aggregate_minutes(part,session,n,session['closes_at'],part[0]['observed_at'])
                except ValueError as e:
                    results.append(dict(company_id=cid,symbol=company['symbol'],day=ds,timeframe=tf,status='SOURCE_MINUTE_GAP'))
                    continue
                native={r['start']:r for r in groups.get((cid,tf),[])}
                for r in aggregated:
                    source=native.get(r['start']);diff=differences(source,r) if source else {}
                    results.append(dict(company_id=cid,symbol=company['symbol'],day=ds,timeframe=tf,
                        start=r['start'],end=r['end'],status='MATCH' if source and all(Decimal(v)==0 for v in diff.values()) else 'MISMATCH_OR_MISSING',
                        **{'native_minus_minute_'+k:v for k,v in diff.items()}))
                q=dict(company_id=cid,instrument_key=company['instrument_key'],timeframe=tf,
                       request_id=sha(f'OWNER_MINUTE_SAMPLE:{cid}:{tf}:{ds}'.encode()))
                a.parquet(aggregated,q,part[0]['source_response_sha256'],part[0]['observed_at'],
                          definition='OWNER_SESSION_ANCHORED_FROM_1MIN')
                prefix=aggregate_minutes(part,session,n,ds+'T14:00:00+05:30',part[0]['observed_at'])
                # Reconstruction is research only; its receipt is today, never backdated.
                prefix_results.append(dict(company_id=cid,day=ds,timeframe=tf,
                    provisional_bars=sum(r['status']=='PROVISIONAL' for r in prefix),
                    strict_historical_14h_visible=len(select_asof(prefix,ds+'T14:00:00+05:30',True)),
                    future_full_bar_visible=False,
                    snapshot_kind='RETROSPECTIVE_PREFIX_RECONSTRUCTION; NOT_LIVE_RECEIPT'))
                pq=dict(q,request_id=sha((q['request_id']+'14H').encode()))
                a.parquet(prefix,pq,part[0]['source_response_sha256'],part[0]['observed_at'],
                          definition='OWNER_14H_RECONSTRUCTION_NOT_HISTORICAL_DELIVERY')
    csvout(OUT/'candle-boundary-reconciliation.csv',results)
    dump(OUT/'completed-provisional-validation.json',dict(rows=prefix_results,
        strict_cutoff_policy='observed_at <= decision_time AND completed_end <= decision_time; provisional is opt-in',
        live_snapshots_acquired=0,retrospective_prefixes_never_promoted_to_live=True))
    return results

def coverage(groups,pilot):
    result=[];gaps=[];cross=[];custom=defaultdict(list)
    for company in pilot:
        cid=company['company_id']
        for tf in ('Weekly','Daily','4-hour','1-hour'):
            rows=groups.get((cid,tf),[])
            by_day=defaultdict(list)
            for r in rows: by_day[r['start'][:10]].append(r)
            present={r['start'][:10] for r in rows}
            missing=[];unexpected=[];unresolved=[];grid_differences=[];bad_grids=0
            if tf!='Weekly':
                listing=date.fromisoformat(company['listing_date'][:10]) if company['listing_date'] else date(2022,1,1)
                d=max(date(2022,1,1),listing)
                last=END
                while d<=last:
                    record=day_record(d)
                    expected=bool(record['sessions'])
                    if not expected and record['status']!='CLOSED':
                        unresolved.append(str(d))
                        d+=timedelta(days=1)
                        continue
                    if expected and str(d) not in present: missing.append(str(d))
                    if not expected and str(d) in present: unexpected.append(str(d))
                    if expected and tf in ('1-hour','4-hour') and str(d) in present:
                        actual=[r['start'] for r in by_day[str(d)]]
                        starts=[s.isoformat() for session in record['sessions']
                                for s,e in boundaries(session['opens_at'],session['closes_at'],60 if tf=='1-hour' else 240)]
                        if sorted(actual)!=sorted(starts):
                            bad_grids+=1
                            grid_differences.append(dict(day=str(d),
                                missing_starts=sorted(set(starts)-set(actual)),
                                unexpected_starts=sorted(set(actual)-set(starts))))
                    d+=timedelta(days=1)
            result.append(dict(company_id=cid,symbol=company['symbol'],timeframe=tf,rows=len(rows),
                first_start=rows[0]['start'] if rows else None,last_start=rows[-1]['start'] if rows else None,
                listing_date_evidence=company['listing_date'],provider_floor='2000-01-01' if tf in ('Weekly','Daily') else '2022-01-01',
                provisional_rows=sum(r['status']=='PROVISIONAL' for r in rows),
                ohlc_invalid=sum(r['quality']=='QUARANTINED_OHLC' for r in rows),
                ambiguous_daily_bucket_rows=sum(r['quality']=='AMBIGUOUS_DAILY_SESSION_BUCKET' for r in rows),
                missing_scheduled_sessions_since2022=len(missing),unexpected_session_dates=len(unexpected),
                unresolved_calendar_dates=len(unresolved),
                session_coverage_status='NOT_EVALUATED_WEEKLY' if tf=='Weekly' else 'PARTIAL_CALENDAR_SINCE2022',
                native_session_grid_differences=bad_grids,corporate_action_status='UNKNOWN_PROVIDER_ADJUSTMENT_BASIS',
                completeness='RETURNED_COVERAGE_MEASURED; CALENDAR_EXCEPTION_REVIEW_PARTIAL; PRE2022_GAPS_UNCERTIFIED'))
            if missing or unexpected or unresolved or grid_differences:
                gaps.append(dict(company_id=cid,symbol=company['symbol'],timeframe=tf,
                                 missing_dates=missing,unexpected_dates=unexpected,
                                 unresolved_dates=unresolved,grid_differences=grid_differences))
        hourly=groups.get((cid,'1-hour'),[]);four={r['start']:r for r in groups.get((cid,'4-hour'),[])}
        hourly_by_day=defaultdict(list)
        for r in hourly: hourly_by_day[r['start'][:10]].append(r)
        daily={r['start'][:10]:r for r in groups.get((cid,'Daily'),[])}
        for ds,part in sorted(hourly_by_day.items()):
            record=day_record(ds)
            if record['status']!='REGULAR_SCHEDULED': continue
            schedule=list(boundaries(record['sessions'][0]['opens_at'],record['sessions'][0]['closes_at'],60))
            if [r['start'] for r in part]!=[s.isoformat() for s,e in schedule]: continue
            if any(r['quality']!='OHLC_VALID' for r in part): continue
            for r,(start,end) in zip(part,schedule):
                r=dict(r,end=end.isoformat(),end_upper_bound=end.isoformat(),
                       end_basis='NSE_SCHEDULE_PLUS_NATIVE_GRID; minute parity sample only',definition='OWNER_SESSION_ANCHORED_FROM_NATIVE_1H')
                custom[(cid,'1-hour')].append(r)
            for subset in (part[:4],part[4:]):
                r=dict(subset[0]);end= min(datetime.fromisoformat(r['start'])+timedelta(hours=4),datetime.fromisoformat(record['sessions'][0]['closes_at']))
                r.update(timeframe='4-hour',end=end.isoformat(),end_upper_bound=end.isoformat(),
                    end_basis='NSE_SCHEDULE_PLUS_1H_AGGREGATION',definition='OWNER_SESSION_ANCHORED_FROM_NATIVE_1H',
                    high=max(Decimal(x['high']) for x in subset),low=min(Decimal(x['low']) for x in subset),
                    close=Decimal(subset[-1]['close']),volume=sum(x['volume'] for x in subset))
                custom[(cid,'4-hour')].append(r)
                native=four.get(r['start']);diff=differences(native,r) if native else {}
                cross.append(dict(company_id=cid,timeframe='4-hour',day=ds,start=r['start'],
                    status='MATCH' if native and all(Decimal(x)==0 for x in diff.values()) else 'MISMATCH_OR_MISSING',
                    **{'native_minus_1h_'+k:v for k,v in diff.items()}))
            if ds in daily:
                r=dict(part[0],high=max(Decimal(x['high']) for x in part),low=min(Decimal(x['low']) for x in part),
                       close=Decimal(part[-1]['close']),volume=sum(x['volume'] for x in part))
                diff=differences(daily[ds],r)
                cross.append(dict(company_id=cid,timeframe='Daily',day=ds,start=r['start'],
                    status='MATCH' if all(Decimal(x)==0 for x in diff.values()) else 'DIFFERENCE_RETAINED',
                    **{'native_minus_1h_'+k:v for k,v in diff.items()}))
    csvout(OUT/'historical-coverage-inventory.csv',result)
    dump(OUT/'missing-session-review.json',gaps)
    csvout(OUT/'native-cross-timeframe-reconciliation.csv',cross)
    return result,custom,cross

def main():
    a=Archive();started=time.perf_counter()
    groups,requests,duplicates,issues=load(a)
    load_seconds=time.perf_counter()-started
    pilot=json.loads((OUT/'pilot-companies.json').read_text())
    bounds=reconcile_sample(a,groups,pilot)
    rows,custom,cross=coverage(groups,pilot)
    for (cid,tf),part in custom.items():
        key=part[0]['instrument_key'];receipt=max(r['observed_at'] for r in part)
        q=dict(company_id=cid,instrument_key=key,timeframe=tf,
               request_id=sha(f'OWNER_FULL:{cid}:{tf}:{len(part)}'.encode()))
        source_hashes=sorted({r['source_response_sha256'] for r in part})
        derived_source_digest=sha(json.dumps(source_hashes).encode())
        dump(OUT/'derived-lineage'/f'{q["request_id"]}.json',dict(
            definition='OWNER_SESSION_ANCHORED_FROM_NATIVE_1H',source_response_sha256=source_hashes,
            combined_source_sha256=derived_source_digest,company_id=cid,timeframe=tf))
        a.parquet(part,q,derived_source_digest,receipt,
                  definition='OWNER_SESSION_ANCHORED_FROM_NATIVE_1H')
    csvout(OUT/'request-coverage-inventory.csv',requests)
    dump(OUT/'duplicate-revision-review.json',dict(duplicates=duplicates,invalid_source_rows=issues))
    dump(OUT/'pilot-verification-progress.json',dict(
        successful_requests=len(requests),expected_requests=len(json.loads((OUT/'pilot-manifest.json').read_text())['requests']),
        coverage_rows=len(rows),companies_with_all_four_timeframes=sum(all(groups.get((c['company_id'],tf)) for tf in ('Weekly','Daily','4-hour','1-hour')) for c in pilot),
        boundary_sample=Counter(r['status'] for r in bounds),
        native_cross_timeframe=Counter(r['status'] for r in cross),
        raw_checksum_reparse_seconds=load_seconds,duplicate_rows=len(duplicates),
        invalid_ohlc_rows=sum(r['kind']=='OHLC_CONSTRAINT_FAILURE' for r in issues),
        ambiguous_daily_session_dates=sum(r['kind']=='AMBIGUOUS_DAILY_SESSION_BUCKET' for r in issues),
        schedules_enabled=False,full_universe_executed=False))
    print((OUT/'pilot-verification-progress.json').read_text())

if __name__=='__main__': main()
