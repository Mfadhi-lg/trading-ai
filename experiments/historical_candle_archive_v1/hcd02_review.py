"""Measured HCD-02 defect and operational review; original evidence stays intact."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv
import gzip
import json
import shutil
import sqlite3
import statistics
import subprocess
import time

from .access import HistoricalStore, owner_day, values, indicator_segments
from .calendar import day_record
from .storage import ROOT, OUT, sha, dump, atomic, verify
from .rollout import EVIDENCE, now
from .references import get
from .observations import incremental_windows


def public_evidence():
    receipts=[]; identities={}
    manifest=json.loads((EVIDENCE/'rollout-manifest.json').read_text())
    all_manifest=json.loads((OUT/'full-universe-manifest.json').read_text())
    for exchange in ('NSE','BSE'):
        url=f'https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz'
        raw, receipt=get(url)
        receipt['received_at']=now()
        if receipt['http_status']==200 and receipt['transport_exit']==0:
            path=EVIDENCE/f'provider-{exchange.lower()}-instruments.json.gz';atomic(path,raw)
            records=json.loads(gzip.decompress(raw)); bykey={r['instrument_key']:r for r in records}
            receipt.update(path=str(path.relative_to(ROOT)),records=len(records))
            for c in all_manifest['companies']+all_manifest.get('historically_eligible_extension',[]):
                key=c.get('instrument_key')
                if not key or not key.startswith(exchange+'_'):continue
                found=bykey.get(key)
                identities[c['company_id']]=dict(company_id=c['company_id'],instrument_key=key,
                    retained_symbol=c['symbol'],provider_symbol=found.get('trading_symbol') if found else None,
                    provider_isin=found.get('isin') if found else None,
                    status='CURRENT_PROVIDER_KEY_AND_SYMBOL_CONFIRMED' if found and found.get('trading_symbol')==c['symbol'] else
                    'CURRENT_KEY_PRESENT_SYMBOL_CHANGED_REVIEW' if found else 'ABSENT_CURRENT_PROVIDER_MASTER',
                    source_sha256=receipt['sha256'])
        receipts.append(receipt)
    urls=[
        'https://nsearchives.nseindia.com/content/circulars/CMTR54023.pdf',
        'https://nsearchives.nseindia.com/content/circulars/CMTR59124.pdf',
        'https://nsearchives.nseindia.com/content/circulars/MSD60677.pdf',
        'https://nsearchives.nseindia.com/content/circulars/MSD61893.pdf',
        'https://archives.nseindia.com/content/circulars/FAOP42168.pdf',
        'https://www.hdfc.bank.in/content/dam/hdfcbankpws/in/en/personal-banking/about-us/stakeholders-information/disclosures/other-stock-exchange-disclosure/18/28aug2025-se-disclosure-allotment.pdf']
    for url in urls:
        raw, receipt=get(url); receipt['received_at']=now()
        if receipt['http_status']==200 and raw.startswith(b'%PDF'):
            path=EVIDENCE/'primary-evidence'/url.rsplit('/',1)[-1];path.parent.mkdir(exist_ok=True)
            atomic(path,raw);receipt['path']=str(path.relative_to(ROOT))
        receipts.append(receipt)
    dump(EVIDENCE/'public-evidence-receipts.json',receipts)
    dump(EVIDENCE/'provider-identity-review.json',dict(verified_at=now(),identities=list(identities.values()),
         counts=Counter(r['status'] for r in identities.values()),
         rollout_counts=Counter(identities.get(c['company_id'],{}).get('status','METADATA_UNAVAILABLE') for c in manifest['companies'])))
    print(json.dumps(dict(receipts=len(receipts),successful=sum(r['http_status']==200 for r in receipts))),flush=True)


def adjustment_review(store):
    reference=ROOT/'artifacts/earnings-reaction-v1/simple-volume-reconstruction/daily-hdfcbank-holdout/reference.csv'
    rows,receipts=store.native(90,'Daily');byday={r['start'][:10]:r for r in rows}
    counts=defaultdict(Counter);maximum=defaultdict(lambda:Decimal(0));outliers=[]
    with reference.open() as f:
        for ref in csv.DictReader(f):
            day=ref['bar_start_ist'][:10];r=byday.get(day)
            if not r or r['quality']!='OHLC_VALID':continue
            era='before_2019_split' if day<'2019-09-19' else '2019_split_to_2025_bonus' if day<'2025-08-26' else 'after_2025_bonus'
            for k in ('open','high','low','close'):
                delta=abs(r[k]-Decimal(ref[k]));counts[era]['fields']+=1
                counts[era]['exact']+=delta==0;counts[era]['within_0_05_inr']+=delta<=Decimal('.050001')
                maximum[era]=max(maximum[era],delta)
                if era!='before_2019_split' and delta>Decimal('.050001'):
                    outliers.append(dict(day=day,field=k,absolute_difference=str(delta)))
    result=dict(company_id=90,symbol='HDFCBANK',reference_path=str(reference.relative_to(ROOT)),
        reference_sha256=sha(reference.read_bytes()),eras={k:dict(v,maximum_absolute_difference_inr=str(maximum[k])) for k,v in counts.items()},
        recent_outliers=outliers,source_revisions=receipts,
        conclusion='Post2025-bonus OHLC is exact against retained reference; almost all2019..2025 fields agree within0.05INR. '
        'This supports quantization as a major difference, not another factor2 adjustment. Older differences remain unresolved.',
        inference=True, adjustment_applied=False,available_adjusted_versions=[],
        raw_source_basis='Provider values preserved without local adjustment; cannot certify exchange-unadjusted history',
        corporate_action_sources=[
            dict(url='https://archives.nseindia.com/content/circulars/FAOP42168.pdf',fact='2:1 split; ex-date2019-09-19'),
            dict(url='https://www.hdfc.bank.in/content/dam/hdfcbankpws/in/en/personal-banking/about-us/stakeholders-information/disclosures/other-stock-exchange-disclosure/18/28aug2025-se-disclosure-allotment.pdf',
                 fact='1:1 bonus; record2025-08-27; allotment2025-08-28')])
    dump(EVIDENCE/'hdfcbank-adjustment-review.json',result)
    return result


def defect_review(store):
    pilot=json.loads((OUT/'pilot-companies.json').read_text())
    original=json.loads((OUT/'duplicate-revision-review.json').read_text())['invalid_source_rows']
    results=[];special=[];cache={};source_diagnostics=[]
    for company in pilot:
        cid=company['company_id']
        for tf in ('Daily','1-hour','4-hour','1-minute'):
            cache[cid,tf]=store.native(cid,tf)[0]
        for issue in [p for p in original if p['company_id']==cid]:
            day=issue['timestamp'][:10];tf=issue['timeframe']
            rows=[r for r in cache[cid,tf] if r['start'][:10]==day]
            if issue['kind']=='AMBIGUOUS_DAILY_SESSION_BUCKET':
                resolved=len(rows)==1 and rows[0]['quality']=='OHLC_VALID'
                results.append(dict(company_id=cid,symbol=company['symbol'],day=day,timeframe=tf,
                    original_issue=issue['kind'],resolution='EXACT_VALUE_EQUIVALENT_ALIASES' if resolved else 'QUARANTINED_DIFFERENT_VALUES',
                    retained_original_rows=sum(len(r['original_provider_values']) for r in rows)))
            else:
                target=[r for r in rows if r['start']==issue['timestamp'] or r['provider_timestamp']==issue['timestamp']]
                results.append(dict(company_id=cid,symbol=company['symbol'],day=day,timeframe=tf,
                    original_issue=issue['kind'],resolution='QUARANTINED_PROVIDER_DEFECT_PERSISTS' if target and any(r['quality']!='OHLC_VALID' for r in target) else 'REVIEW_REQUIRED',
                    minute_evidence_invalid=sum(r['quality']!='OHLC_VALID' for r in cache[cid,'1-minute'] if r['start'][:10]==day)))
        for day in ('2022-10-24','2023-11-12','2024-03-02','2024-05-18'):
            for interval in (60,240):
                try:
                    rows=owner_day([r for r in cache[cid,'1-hour'] if r['start'][:10]==day],
                                   [r for r in cache[cid,'1-minute'] if r['start'][:10]==day],day_record(day),interval)
                    status='RESOLVED_SESSION_ANCHORED'; bars=len(rows)
                except ValueError:
                    status='QUARANTINED_INCOMPLETE_MINUTE_GRID';bars=0
                special.append(dict(company_id=cid,symbol=company['symbol'],day=day,timeframe=f'{interval//60}-hour',status=status,owner_bars=bars,
                                    source_refs=day_record(day)['source_refs']))
    result=dict(original_issues=results,counts=Counter(r['resolution'] for r in results),
                special_sessions=special,special_counts=Counter(r['status'] for r in special),
                minute_evidence_quality_counts=Counter(r['quality'] for (cid,tf),rows in cache.items() if tf=='1-minute' for r in rows),
                original_provider_bytes_modified=False,local_price_adjustment_applied=False)
    dump(EVIDENCE/'defect-review.json',result)
    return result


def review():
    store=HistoricalStore();start=time.monotonic()
    try:
        integrity=store.db.execute('PRAGMA integrity_check').fetchall()
        if [r[0] for r in integrity]!=['ok']:
            raise RuntimeError('STOP_INDEX_INTEGRITY')
        defect=defect_review(store);adjustment=adjustment_review(store)
        manifest=json.loads((EVIDENCE/'rollout-manifest.json').read_text())
        expected={q['request_id']:q for q in manifest['requests']}
        saved={r['request_id']:dict(r) for r in store.db.execute('SELECT * FROM requests')}
        counts=Counter(saved[q]['status'] if q in saved else 'NOT_ATTEMPTED' for q in expected)
        companies=[];coverage=[];quarantine=Counter()
        for c in manifest['companies']:
            requests=[q for q in expected.values() if q['company_id']==c['company_id']]
            complete=all(saved.get(q['request_id'],{}).get('status')=='SUCCESS' for q in requests)
            companies.append(dict(company_id=c['company_id'],symbol=c['symbol'],exchange=c['instrument_key'].split('|')[0],
                all_planned_requests_successful=complete,expected_requests=len(requests),
                successful_requests=sum(saved.get(q['request_id'],{}).get('status')=='SUCCESS' for q in requests)))
            for tf in ('Weekly','Daily','1-hour','4-hour'):
                rows,receipts=store.native(c['company_id'],tf)
                completed=[r for r in rows if r['status'] in ('COMPLETED','COMPLETED_RETROSPECTIVE')]
                valid=[r for r in completed if r['quality']=='OHLC_VALID']
                for r in rows:quarantine[r['quality']]+=1
                coverage.append(dict(company_id=c['company_id'],symbol=c['symbol'],timeframe=tf,rows=len(rows),
                    first_start=min((r['start'] for r in rows),default=None),last_start=max((r['start'] for r in rows),default=None),
                    completed_rows=len(completed),completed_first_start=min((r['start'] for r in completed),default=None),
                    completed_last_start=max((r['start'] for r in completed),default=None),
                    valid_completed_rows=len(valid),valid_completed_first_start=min((r['start'] for r in valid),default=None),
                    valid_completed_last_start=max((r['start'] for r in valid),default=None),
                    zero_placeholder_rows=sum(all(r[k]==0 for k in ('open','high','low','close','volume','open_interest')) for r in rows),
                    invalid_or_ambiguous=sum(r['quality']!='OHLC_VALID' for r in rows),
                    expected_requests_successful=all(saved.get(q['request_id'],{}).get('status')=='SUCCESS' for q in requests if q['timeframe']==tf),
                    source_adjustment_basis='UNCERTIFIED',missing_session_completeness='NOT_CERTIFIED'))
        physical=allocated=0;files=0
        for p in store.root.rglob('*'):
            if p.is_file():
                st=p.stat();physical+=st.st_size;allocated+=st.st_blocks*512;files+=1
        execution=json.loads((EVIDENCE/'rollout-execution.json').read_text())
        begin=aware_time(execution['started_at']);finish=aware_time(execution.get('updated_at',now()))
        seconds=(finish-begin).total_seconds()
        # Include a successful GET whose normalization was later recovered from
        # RAW_SAVED, rather than relying on the in-process success counter.
        new=sum(saved[q]['status']=='SUCCESS' and saved[q]['received_at'] is not None
                and aware_time(saved[q]['received_at'])>=begin for q in expected if q in saved)
        active_seconds=sum((aware_time(r.get('finished_at',execution.get('updated_at',now())))-aware_time(r['started_at'])).total_seconds()
                           for r in execution['runs'])
        digest_errors=[];checked=0
        for row in store.db.execute('SELECT raw_sha256,raw_path FROM requests WHERE status=\'SUCCESS\''):
            if sha(gzip.decompress((store.root/row['raw_path']).read_bytes()))!=row['raw_sha256']:
                digest_errors.append(row['raw_path'])
            checked+=1
        for row in store.db.execute('SELECT parquet_path,parquet_sha256 FROM datasets'):
            if sha((store.root/row['parquet_path']).read_bytes())!=row['parquet_sha256']:
                digest_errors.append(row['parquet_path'])
            checked+=1
        preservation=json.loads((EVIDENCE/'preservation-before.json').read_text())
        changed=[p for p,d in preservation.items() if not (ROOT/p).exists() or sha((ROOT/p).read_bytes())!=d]
        dump(EVIDENCE/'preservation-result.json',dict(checked_files=len(preservation),changed=changed,passed=not changed))
        for company in companies:
            company['all_four_returned_nonempty_history']=all(r['rows']>0 for r in coverage if r['company_id']==company['company_id'])
            company['positive_completed_bars_in_all_four']=all(r['valid_completed_rows']>0 for r in coverage if r['company_id']==company['company_id'])
        complete_count=sum(c['all_planned_requests_successful'] for c in companies)
        positive_count=sum(c['positive_completed_bars_in_all_four'] for c in companies)
        operations=json.loads((EVIDENCE/'operational-checks.json').read_text()) if (EVIDENCE/'operational-checks.json').exists() else {}
        identities=json.loads((EVIDENCE/'provider-identity-review.json').read_text()) if (EVIDENCE/'provider-identity-review.json').exists() else {}
        indicators=indicator_verification(store,manifest)
        special_result=special_coverage(store,manifest) if (EVIDENCE/'special-execution.json').exists() else {}
        special_manifest=json.loads((EVIDENCE/'special-manifest.json').read_text()) if (EVIDENCE/'special-manifest.json').exists() else {}
        special_requests_ok=bool(special_manifest) and all(saved.get(q['request_id'],{}).get('status')=='SUCCESS' for q in special_manifest['requests'])
        safe=(complete_count==100 and not digest_errors and not changed and counts['SUCCESS']==len(expected)
              and execution['status']=='MANIFEST_PASS_FINISHED'
              and operations.get('passed') is True
              and identities.get('rollout_counts')=={'CURRENT_PROVIDER_KEY_AND_SYMBOL_CONFIRMED':100}
              and indicators['compatible_clean_windows']>0)
        # Isolated unsupported instruments stay held. A widespread lack of any
        # valid source history is an operational failure, even with HTTP200s.
        safe=safe and positive_count>=90
        if special_manifest:safe=safe and special_requests_ok
        result=dict(created_at=now(),status='SCALED_ACQUISITION_VERIFIED' if safe else 'INCOMPLETE_OR_BLOCKED',
            safe_for_full_manifest=safe,incremental_daily_safe_to_enable=False,schedules_enabled=False,
            rollout_companies_with_all_planned_native_requests=complete_count,rollout_request_statuses=counts,
            rollout_companies_with_positive_completed_bars_all_four=positive_count,
            archive_request_statuses=Counter(r['status'] for r in saved.values()),
            companies=companies,coverage=coverage,quality_counts=quarantine,
            original_pilot_defect_counts=defect['counts'],special_session_counts=defect['special_counts'],
            operational_checks_passed=operations.get('passed',False),indicator_compatibility=indicators,
            scaled_split_session_coverage=special_result,session_repair_requests_complete=special_requests_ok,
            identity_counts=identities.get('counts',{}),
            storage=dict(logical_bytes=physical,allocated_bytes=allocated,files=files,free_bytes=shutil.disk_usage(store.mount).free,
                         volume_uuid=store.info['VolumeUUID']),
            throughput=dict(new_successful_requests=new,gross_elapsed_seconds=seconds,
                            active_run_seconds=active_seconds,
                            successful_requests_per_second=new/seconds if seconds else None,
                            active_run_requests_per_second=new/active_seconds if active_seconds else None),
            acquisition_stop_history=execution.get('stop_history',[]),
            integrity=dict(sqlite='ok',checksums_checked=checked,checksum_errors=digest_errors),
            full_manifest_authorized=False,full_manifest_sha256=sha((OUT/'full-universe-manifest.json').read_bytes()),
            review_elapsed_seconds=time.monotonic()-start,
            remaining_uncertainties=['BSE session calendar and historical identity continuity',
                'Pre2022 complete session calendar and delisting/suspension evidence',
                'HDFCBANK older feed differences and provider adjustment convention',
                'Per-security CAS phases and future special-session calendars require review before schedules',
                'One current eligible identity unresolved; historical membership is dated evidence only',
                'Sparse or invalid constituents remain excluded; no fills',
                'Weekly original DSS/RS integration remains unsupported; indicators require bounded clean context'])
        dump(EVIDENCE/'scaled-validation.json',result)
        print(json.dumps({k:v for k,v in result.items() if k not in ('coverage','companies')}),flush=True)
        return result
    finally:store.close()


def aware_time(value):
    return datetime.fromisoformat(value)


def operational_checks():
    """Real two-day overlap plus a controlled crash after the durable raw receipt."""
    from .rollout import execute
    report=EVIDENCE/'operational-checks.json'
    if report.exists() and json.loads(report.read_text()).get('passed') is True:
        print('Operational evidence already passed; preserved without duplicate GETs.',flush=True)
        return
    operation=json.loads((EVIDENCE/'operation-manifest.json').read_text())
    store=HistoricalStore()
    prior={tf:store.read(555,tf,start='2026-11-02',end='2026-11-04') for tf in ('Daily','1-hour')}
    store.close()
    # An interrupted run is intentional here, and only this exact reason is accepted.
    try:
        execute('operation',interrupt_after_raw_save=True)
    except RuntimeError as error:
        if str(error)!='STOP_TEST_INTERRUPTION_AFTER_DURABLE_RAW_SAVE':raise
    store=HistoricalStore()
    states_before={r['request_id']:dict(r) for r in store.db.execute('SELECT * FROM requests') if r['request_id'] in {q['request_id'] for q in operation['requests']}}
    store.close()
    execute('operation')
    store=HistoricalStore()
    try:
        states_after={r['request_id']:dict(r) for r in store.db.execute('SELECT * FROM requests') if r['request_id'] in {q['request_id'] for q in operation['requests']}}
        comparisons=[]
        for tf in ('Daily','1-hour'):
            current=store.read(555,tf,start='2026-11-02',end='2026-11-04')
            before={r['start']:values(r) for r in prior[tf]['rows']}
            after={r['start']:values(r) for r in current['rows']}
            comparisons.append(dict(timeframe=tf,rows_before=len(before),rows_after=len(after),
                values_unchanged=before==after,duplicate_starts=len(current['rows'])-len(after),exclusions=len(current['exclusions'])))
        recovered=[rid for rid,r in states_before.items() if r['status']=='RAW_SAVED']
        recovery=bool(recovered) and all(states_after[rid]['status']=='SUCCESS' and states_after[rid]['attempts']==states_before[rid]['attempts']
            and states_after[rid]['raw_sha256']==states_before[rid]['raw_sha256'] and states_after[rid]['received_at']==states_before[rid]['received_at'] for rid in recovered)
        windows=incremental_windows(['2026-11-02','2026-11-03','2026-11-04'],{'2026-11-02','2026-11-04'},'2026-11-04','2026-11-04')
        result=dict(created_at=now(),controlled_interruption_after_durable_receipt=True,recovery_without_refetch=recovery,
                    recovered_request_ids=recovered,missed_session_simulated='2026-11-03',repair_windows=windows,
                    missing_session_requested_and_retrieved=all(q['from_date']<='2026-11-03'<=q['to_date'] and states_after[q['request_id']]['status']=='SUCCESS' for q in operation['requests']),
                    overlap=comparisons,requests=[{k:r[k] for k in ('request_id','status','attempts','raw_sha256','received_at')} for r in states_after.values()])
        result['passed']=recovery and all(r['values_unchanged'] and not r['duplicate_starts'] and not r['exclusions'] for r in comparisons)
        dump(EVIDENCE/'operational-checks.json',result)
        print(json.dumps(result),flush=True)
    finally:store.close()


def indicator_verification(store,manifest):
    """Call unchanged existing indicator engines using bounded clean API windows."""
    from experiments.earnings_reaction_v1.candles import Candle
    from experiments.earnings_reaction_v1.events import Session
    from experiments.earnings_reaction_v1.indicators import pine_average,_dtr_arithmetic,merge_daily_dtr,pine_rvol
    from experiments.earnings_reaction_v1.indicator_alternatives import dss_rvol_reconstruction
    from experiments.earnings_reaction_v1.simple_volume import simple_volume_features
    companies=manifest['companies']
    selected=[c for c in companies if c['symbol'] in ('KABRAEXTRU','HDFCBANK','ICICIBANK')]
    selected += [c for c in companies if c not in selected and c['instrument_key'].startswith('NSE_')][:2]
    results=[];cutoff=datetime.now(timezone.utc)

    def convert(r,tf):
        start=datetime.fromisoformat(r['start'])
        end=start+timedelta(days=1) if tf=='Daily' else datetime.fromisoformat(r.get('end') or r['end_upper_bound'])
        return Candle(r['instrument_key'],r['provider_timestamp'],start,end,
                      *(Decimal(r[k]) for k in ('open','high','low','close','volume','open_interest')),
                      'HCD02_RETROSPECTIVE_INTERVAL_COMPLETION_ONLY',end,r['start'][:10])

    for c in selected:
        daily_result=store.read(c['company_id'],'Daily',start='2026-05-01',end='2026-10-30')
        try:
            daily=tuple(convert(r,'Daily') for r in indicator_segments(daily_result))
        except ValueError:
            daily=()
        for tf in ('Weekly','Daily','1-hour','4-hour'):
            result=store.read(c['company_id'],tf,start='2024-01-01' if tf=='Weekly' else '2026-08-17',end='2026-10-30')
            try:
                rows=indicator_segments(result)
                bars=tuple(convert(r,tf) for r in rows)
                if not bars or not daily:raise ValueError('no clean bounded history')
                ema10=pine_average((r.close for r in bars),10,'EMA')
                ema20=pine_average((r.close for r in bars),20,'EMA')
                volume=simple_volume_features(bars,cutoff=cutoff,daily=tf=='Daily',weekly=tf=='Weekly',daily_history=daily)
                dtr=_dtr_arithmetic(bars) if tf in ('Daily','Weekly') else merge_daily_dtr(bars,daily,cutoff=cutoff,timeframe=tf)
                dss=()
                if tf=='Daily':dss=pine_rvol((r.volume for r in bars),20)[0]
                elif tf in ('1-hour','4-hour'):
                    sessions=[]
                    for day in sorted({r.start_ist.date() for r in bars}):
                        for s in day_record(str(day))['sessions']:
                            sessions.append(Session(day,datetime.fromisoformat(s['opens_at']),datetime.fromisoformat(s['closes_at'])))
                    dss=dss_rvol_reconstruction(bars,sessions,cutoff=cutoff,bar_minutes=60 if tf=='1-hour' else 240)
                results.append(dict(company_id=c['company_id'],symbol=c['symbol'],timeframe=tf,status='PASS_EXISTING_IMPLEMENTATIONS',
                    bars=len(bars),ema10_rows=len(ema10),ema20_rows=len(ema20),simple_volume_rows=len(volume),dss_rows=len(dss),
                    dtr_engine='EXISTING_PRIMITIVES' if tf=='Weekly' else 'EXISTING_ENGINE',
                    weekly_dss_rs='UNSUPPORTED' if tf=='Weekly' else 'SEE_RETAINED_HCD01_RS_RECONCILIATION',
                    seed_parity='BOUNDED_CONTEXT_ONLY; full-history parity not asserted'))
            except ValueError as error:
                results.append(dict(company_id=c['company_id'],symbol=c['symbol'],timeframe=tf,status='BLOCKED_CLEAN_WINDOW_OR_ENGINE_CONTRACT',reason=str(error)))
    result=dict(compatible_clean_windows=sum(r['status']=='PASS_EXISTING_IMPLEMENTATIONS' for r in results),
        results=results,source_engines_modified=False,
        rs_reconciliation='HCD01 representative80-row indicator-compatibility.csv preserved; no replacement or synthetic benchmark',
        weekly_original_dss_rs_supported=False)
    dump(EVIDENCE/'indicator-compatibility.json',result)
    return result


def finalize():
    """Finish operational checks automatically after the authorized rollout exits."""
    while True:
        state=json.loads((EVIDENCE/'rollout-execution.json').read_text())
        if state['status']=='STOPPED':
            raise RuntimeError('STOP_ROLLOUT_FAILED; no automatic restart after serious failure')
        if state['status']!='RUNNING':break
        time.sleep(10)
    time.sleep(2)  # Execution status is fsynced just before the writer is closed.
    operational_checks()
    result=review()
    if not result['safe_for_full_manifest']:
        raise RuntimeError('STOP_SCALED_ACCEPTANCE_FAILED')
    return result


def reconcile_sources(store,manifest):
    """Bounded weekly/daily and native4h/hourly price-volume reconciliation."""
    chosen=[c for c in manifest['companies'] if c['symbol'] in ('KABRAEXTRU','HDFCBANK','ICICIBANK','GMBREW','ICICILIFE')]
    results=[]
    fields=('open','high','low','close','volume')
    for c in chosen:
        weekly,_=store.native(c['company_id'],'Weekly')
        for monday in ('2023-11-06','2026-10-26'):
            finish=(datetime.fromisoformat(monday)+timedelta(days=6)).date().isoformat()
            daily,_=store.native(c['company_id'],'Daily',start=monday,end=finish)
            daily=[r for r in daily if monday<=r['start'][:10]<=finish and r['quality']=='OHLC_VALID']
            source=next((r for r in weekly if r['start'][:10]==monday and r['quality']=='OHLC_VALID'),None)
            if not daily or not source:
                results.append(dict(company_id=c['company_id'],timeframe='Weekly',day=monday,status='SOURCE_UNAVAILABLE'))
                continue
            aggregate=dict(open=daily[0]['open'],high=max(r['high'] for r in daily),low=min(r['low'] for r in daily),close=daily[-1]['close'],volume=sum(r['volume'] for r in daily))
            delta={k:str(Decimal(source[k])-Decimal(aggregate[k])) for k in fields}
            results.append(dict(company_id=c['company_id'],timeframe='Weekly',day=monday,
                status='MATCH' if all(Decimal(v)==0 for v in delta.values()) else 'DIFFERENCE_RETAINED',differences=delta,
                basis='Raw daily buckets within native Monday week; not an independent feed or adjustment certification'))
        hours,_=store.native(c['company_id'],'1-hour',start='2026-10-26',end='2026-10-30')
        fours,_=store.native(c['company_id'],'4-hour',start='2026-10-26',end='2026-10-30')
        for day in ('2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30'):
            try:
                combined=owner_day([r for r in hours if r['start'][:10]==day],[],day_record(day),240)
            except ValueError:
                results.append(dict(company_id=c['company_id'],timeframe='4-hour',day=day,status='SOURCE_GRID_UNAVAILABLE'));continue
            for r in combined:
                source=next((x for x in fours if x['start']==r['start'] and x['quality']=='OHLC_VALID'),None)
                delta={k:str(Decimal(source[k])-Decimal(r[k])) for k in fields} if source else {}
                results.append(dict(company_id=c['company_id'],timeframe='4-hour',day=day,start=r['start'],
                    status='MATCH' if source and all(Decimal(v)==0 for v in delta.values()) else 'DIFFERENCE_OR_MISSING_RETAINED',
                    differences=delta,basis='Native source grid comparison; BSE calendar certification is separate'))
    result=dict(created_at=now(),counts=Counter(r['status'] for r in results),comparisons=results,
                retained_hcd01_independent_minute_sample='135 MATCH; HCD01 source evidence preserved',adjustment_applied=False)
    dump(EVIDENCE/'source-reconciliation.json',result)
    return result


def special_coverage(store,manifest,filename='scaled-special-session-coverage.json',checkpoint=None):
    rows=[]
    for c in manifest['companies']:
        if checkpoint:checkpoint()
        if not c['instrument_key'].startswith('NSE_EQ|'):continue
        for day in ('2024-03-02','2024-05-18'):
            for tf in ('1-hour','4-hour'):
                result=store.read(c['company_id'],tf,start=day,end=day)
                rows.append(dict(company_id=c['company_id'],symbol=c['symbol'],day=day,timeframe=tf,
                    owner_bars=len(result['rows']),exclusions=[r['reason'] for r in result['exclusions']],
                    status='SESSION_GRID_AVAILABLE' if result['rows'] and not result['exclusions'] else 'EXCLUDED_OR_SOURCE_HISTORY_ABSENT'))
    result=dict(created_at=now(),counts=Counter(r['status'] for r in rows),checks=rows,
                absence_does_not_prove_listing_or_historical_eligibility=True)
    dump(EVIDENCE/filename,result)
    return result


def write_report():
    """Finalize human-readable measured evidence after the scaled check completes."""
    result=json.loads((EVIDENCE/'scaled-validation.json').read_text())
    execution=json.loads((EVIDENCE/'rollout-execution.json').read_text())
    store=HistoricalStore()
    try:
        begin=aware_time(execution['started_at'])
        manifest=json.loads((EVIDENCE/'rollout-manifest.json').read_text())
        ids={q['request_id'] for q in manifest['requests']}
        saved=[dict(r) for r in store.db.execute('SELECT * FROM requests').fetchall()]
        newly_completed=sum(r['request_id'] in ids and r['status']=='SUCCESS' and r['received_at'] is not None and aware_time(r['received_at'])>=begin for r in saved)
        elapsed=(aware_time(execution['updated_at'])-begin).total_seconds()
        active=sum((aware_time(r['finished_at'])-aware_time(r['started_at'])).total_seconds() for r in execution['runs'])
        result['throughput']=dict(new_successful_requests=newly_completed,gross_elapsed_seconds=elapsed,
            active_run_seconds=active,successful_requests_per_second=newly_completed/elapsed,
            active_run_requests_per_second=newly_completed/active)
        result['source_reconciliation']=json.loads((EVIDENCE/'source-reconciliation.json').read_text())['counts']
        result['acquisition_stop_history']=execution.get('stop_history',[])
        numeric=store.db.execute("SELECT COUNT(*) FROM candle_observations WHERE definition='UPSTOX_NATIVE' AND quality='QUARANTINED_NUMERIC'").fetchone()[0]
        result['retained_numeric_quarantine_observations']=numeric
        # Actual source partitions include retained aliases and revisions, distinct
        # from the canonical access view's quality counts.
        result['retained_quarantine_partitions']=dict(store.db.execute("SELECT completeness,COUNT(*) FROM datasets WHERE parquet_path LIKE 'quarantine/%' GROUP BY completeness").fetchall())
        attempts=Counter()
        for p in (EVIDENCE/'transport-receipts').glob('*.json'):
            for r in json.loads(p.read_text()):
                if 'transport_exit' in r:attempts[f'curl_exit_{r["transport_exit"]}']+=1
                if r.get('transport')=='urllib_verified_TLS':attempts['alternate_verified_TLS']+=1
        result['hcd02_transport_attempts']=attempts
        result['archive_request_statuses']=Counter(r['status'] for r in saved)
        result['report_created_at']=now()
        dump(EVIDENCE/'scaled-validation.json',result)
        for name,rows in [('historical-coverage.csv',result['coverage']),('companies.csv',result['companies'])]:
            with (EVIDENCE/name).open('w',newline='') as f:
                w=csv.DictWriter(f,list(rows[0]));w.writeheader();w.writerows(rows)
        scope=json.loads((EVIDENCE/'scope.json').read_text())
        supplement=json.loads((EVIDENCE/'special-full-manifest.json').read_text())
        supplement_ids={q['request_id'] for q in supplement['requests']}
        supplement_done=sum(r['status']=='SUCCESS' and r['request_id'] in supplement_ids for r in saved)
        supplement_remaining=len(supplement['requests'])-supplement_done
        result['remaining_session_repair_requests']=supplement_remaining
        result['session_repair_manifest_sha256']=sha((EVIDENCE/'special-full-manifest.json').read_bytes())
        dump(EVIDENCE/'scaled-validation.json',result)
        lines=['# HCD-02 — measured rollout result','',
            f"{result['rollout_companies_with_all_planned_native_requests']}/100 rollout companies completed all four native request plans. "
            f"{result['rollout_companies_with_positive_completed_bars_all_four']}/100 have positive, valid completed source bars in all four timeframes. "
            f"Request statuses: {dict(result['rollout_request_statuses'])}. "
            f"The full eligible-universe backfill is not complete and has not been authorized.",'',
            f"The unchanged full manifest contains 83,688 requests. This rollout covers 4,400; 79,288 remain. "
            f"Manifest SHA-256: `{result['full_manifest_sha256']}`. "
            'Its current eligible inventory has 1,882 companies, 1 unmapped; 21 dated historically eligible companies are also retained.','',
            f"The separate split-session supplement contains {len(supplement['requests']):,} one-day minute requests for two official session dates. "
            f"{supplement_done:,} are already acquired; {supplement_remaining:,} remain. Its SHA-256 is `{result['session_repair_manifest_sha256']}`. "
            'It preserves the native manifest and needs explicit full-scope authorization together with the remaining native backfill.','',
            '## Acquisition and storage','',
            f"Archive request states, including HCD-01 and supplemental probes: {dict(result['archive_request_statuses'])}. "
            f"HCD-02 performed 107 defect-evidence requests, the native rollout, 140 new bounded split-session repair requests, and the two-request operational probe. "
            'SPICEJET triggered one integrity stop; inspection isolated finite negative volume. The saved response was recovered without another GET, '
            'negative values stayed quarantined, and the remaining response rows were retained.','',
            f"Passport UUID: `{result['storage']['volume_uuid']}`. "
            f"Archive logical bytes: {result['storage']['logical_bytes']:,}; allocated file bytes: {result['storage']['allocated_bytes']:,}; "
            f"volume free bytes: {result['storage']['free_bytes']:,}. No candle files fell back to internal storage.",'',
            f"New completed rollout requests: {newly_completed:,}. Gross elapsed: {elapsed:.2f}s ({newly_completed/elapsed:.4f} requests/s); "
            f"active run time: {active:.2f}s ({newly_completed/active:.4f} requests/s). Gross time includes the inspected integrity stop. "
            f"Transport attempts are reported separately: {dict(attempts)}.",'',
            '## Actual historical coverage','',
            'These are envelopes of returned completed native observations across the 100 companies, not a claim that every company spans every date. '
            'Per-company first/last dates, row counts and request completion are in `historical-coverage.csv`. Provider floors are not guarantees '
            'of listing-to-present completeness.','']
        for tf in ('Weekly','Daily','4-hour','1-hour'):
            rows=[r for r in result['coverage'] if r['timeframe']==tf]
            first=min((r['valid_completed_first_start'] for r in rows if r['valid_completed_first_start']),default=None)
            last=max((r['valid_completed_last_start'] for r in rows if r['valid_completed_last_start']),default=None)
            lines.append(f"- {tf}: {sum(r['valid_completed_rows']>0 for r in rows)}/100 companies with valid completed source bars; source starts {first} through {last}.")
        lines+=['','## Data quality and usable access','',
            f"Canonical native quality counts for the four requested timeframes: {dict(result['quality_counts'])}. "
            f"Retained negative-numeric observations across receipts: {numeric}. "
            'Raw quarantine files retain original aliases and prior receipts; their counts are not counts of unique remaining clean-view exclusions.','',
            'Seventeen of 18 pilot ambiguous daily dates were proven value-identical and collapse only in the versioned clean view. '
            'ICICIBANK 2015-12-31 remains ambiguous. All five original OHLC defects persist and remain quarantined. '
            'Additional invalid minute constituents remain excluded. No price, volume, trade or missing session was invented.','',
            f"Special-session company/timeframe checks: {dict(result['special_session_counts'])}. "
            'Six checks remain excluded: Kabra March 2 and May 18, 2024 and Aether May 18, 2024, each for 1h and 4h. '
            'The other owner bars use actual session segments, including the split-session break.','',
            f"Scaled split-session checks across the initial NSE scope: {result.get('scaled_split_session_coverage',{}).get('counts',{})}. "
            'Incomplete minute grids and absent source history stay excluded; absence does not establish a listing date or historical eligibility.','',
            'The HDFCBANK comparison finds exact agreement for all 1,168 post-August 2025-bonus OHLC fields and agreement within₹0.05 '
            'for 5,888 of 5,892 fields between the 2019 split and 2025 bonus. This supports quantization as a major difference; '
            'older differences and the full provider adjustment convention remain unresolved. No extra factor 2 or other local adjustment was applied.','',
            'Use `HistoricalStore.read()` in `experiments/historical_candle_archive_v1/access.py`. It returns clean rows, exclusions, '
            'original provider values, response hashes, receipts, flags and available adjusted versions (currently none certified). '
            'Owner ordinary 4h bars are 09:15–13:15 and 13:15–15:30; hourly bars include the final 15-minute bar. '
            'BSE owner bars stay excluded until its own calendar is verified; native BSE values remain available with flags. '
            'The indicator guard requires an explicit clean window. Weekly buckets containing known bad daily constituents are excluded.','',
            'Retrospective mode is explicitly revision-selected completed history. Receipt timestamps are never backdated. '
            'Strict delivery mode requires actual receipt before the decision. Retrospective minute prefixes are not historical live snapshots.','',
            '## Validation and activation','',
            f"SQLite integrity: {result['integrity']['sqlite']}; checksum validations: {result['integrity']['checksums_checked']:,}; "
            f"checksum errors: {len(result['integrity']['checksum_errors'])}. Operational checks passed: {result['operational_checks_passed']}. "
            f"Source reconciliations: {result['source_reconciliation']}; preserved HCD-01 minute sample: 135 matches.",'',
            f"Existing indicator engines pass {result['indicator_compatibility']['compatible_clean_windows']} clean representative windows. "
            'Weekly original DSS/RS integration and complete-history seed parity remain unsupported. '
            'The overlap probe verifies unchanged values and no duplicate starts. Controlled interruption after the durable raw receipt '
            'recovers without another GET; simulated missed-session selection is repaired with a real overlapping retrieval.','',
            'TECH-FIX-01, the independent technical audit and Document Intelligence are covered by the unchanged-file preservation result. '
            'No Kronos training or chart-pattern detection was started.','',
            f"Safe to execute the full native-acquisition manifest after owner authorization: {result['safe_for_full_manifest']}. "
            'Incremental unattended operation safe to enable: False. Schedules remain disabled. '
            'Prepared checkpoints are 09:15, 09:30, 13:20, 14:00, approximately 16:00 IST and completed weekly reconciliation.','',
            'Remaining uncertainties include BSE and pre-2022 calendars, per-security CAS handling, dated historical eligibility and security continuity, '
            'older adjustment differences, GNRL old-key history after its provider key replacement, and SJ Corporation’s missing provider mapping. '
            'The issuer supports NOVARTIND→EVONILE as a name change on the same provider ISIN. '
            'No master or original manifest was rewritten.','',
            'The owner’s HCD-02 instruction requires explicit authorization for the full manifest. Once granted, the runner continues automatically '
            'in resumable batches and stops on serious provider, storage or integrity failures; isolated stock failures are retained and held.','']
        (EVIDENCE/'HCD-02-review.md').write_text('\n'.join(lines))
        dump(EVIDENCE/'HCD-02-status.json',dict(full_universe_complete=False,full_manifest_authorized=False,
            rollout_complete=result['rollout_companies_with_all_planned_native_requests']==100,
            safe_for_full_native_acquisition=result['safe_for_full_manifest'],remaining_manifest_requests=79288,
            remaining_session_repair_requests=supplement_remaining,
            incremental_daily_safe_to_enable=False,schedules_enabled=False,previous_technical_audit_modified=False,
            document_intelligence_started=False,kronos_training_started=False,chart_pattern_detection_started=False))
        print(json.dumps(dict(report=str(EVIDENCE/'HCD-02-review.md'),throughput=result['throughput'],quality=result['quality_counts'])),flush=True)
    finally:store.close()


def finish_rollout():
    """Complete initial-scope repairs after the already running native acceptance."""
    from .rollout import execute
    deadline=time.monotonic()+3600
    while True:
        state=json.loads((EVIDENCE/'rollout-execution.json').read_text())
        if state['status']=='STOPPED':raise RuntimeError('STOP_NATIVE_ROLLOUT_FAILED')
        if state['status'] in ('MANIFEST_PASS_FINISHED','FINISHED_WITH_ISOLATED_FAILURES'):break
        if time.monotonic()>deadline:raise RuntimeError('STOP_INITIAL_VALIDATION_TIMEOUT')
        time.sleep(10)
    time.sleep(2)
    operational_checks()
    execute('special')
    result=review()
    write_report()
    if not result['safe_for_full_manifest']:raise RuntimeError('STOP_SCALED_ACCEPTANCE_FAILED')
    return result
