"""TIPSMUSIC continuation of TA-03R3; refuses missing/unauthorized provider input.

Run after approved bounded retrieval:
python3 -B -m experiments.earnings_reaction_v1.compare_ta03r4
No network operation is performed here. Uses the existing chart/feature engines.
"""
from dataclasses import replace
from datetime import date,datetime
from decimal import Decimal as D
from fractions import Fraction
from pathlib import Path
import csv
import hashlib
import json
import subprocess
import sys
import zipfile

from .retrieve_ta03r4 import ROOT,OUT,REQUESTS,manifest,canonical_bytes,TIPSMUSIC,BENCHMARK
from .upstox_candles import parse_response
from .events import Session
from .indicator_alternatives import TIMEFRAMES,VERSION,aggregate,intra_rvol,volume_features,session_profile
from .indicators import calculate_chart_rs,merge_daily_dtr,pine_average,pine_rvol
from .compare_ta03r3 import render_chart,write_json,write_csv,sha

MODIFIED=['indicator_alternatives.py','indicators.py','compare_ta03r3.py']
ADDED=['retrieve_ta03r4.py','compare_ta03r4.py','tests/test_ta03r4.py','tests/test_ta03r4_real.py','TA03R4_CONTRACT.md']


def load_provider():
    ledger_path=OUT/'retrieval-execution.json'
    if not ledger_path.is_file():
        raise PermissionError('owner scope approval and bounded retrieval required; no TIPSMUSIC chart can be fabricated')
    ledger=json.loads(ledger_path.read_text())
    digest=hashlib.sha256(canonical_bytes(manifest())).hexdigest()
    if not ledger.get('complete') or ledger.get('scope_sha256')!=digest or len(ledger['requests'])!=len(REQUESTS):
        raise ValueError('incomplete retrieval or scope identity mismatch')
    datasets={}
    for q,receipt in zip(REQUESTS,ledger['requests']):
        raw_path=ROOT/receipt['retained_path']
        if sha(raw_path)!=receipt['response_sha256']:
            raise ValueError('retained provider hash mismatch')
        batch=parse_response(raw_path.read_bytes(),q,retrieved_at=datetime.fromisoformat(receipt['received_at']),
            raw_response_ref=receipt['retained_path'],instrument_metadata_ref='ta03r4/instrument-identity.json',
            instrument_metadata_verified=True)
        datasets.setdefault((q.instrument_key,q.unit,q.interval),[]).extend(batch.candles)
    return {k:tuple(sorted(v,key=lambda c:c.start_ist)) for k,v in datasets.items()},ledger


def build():
    datasets,ledger=load_provider()
    schedule=json.loads((OUT/'session-schedule-proposal.json').read_text())
    sessions=tuple(Session(date.fromisoformat(s['day']),datetime.fromisoformat(s['opens_at']),
        datetime.fromisoformat(s['closes_at'])) for s in schedule['sessions'])
    closes={s.day:s.closes_at for s in sessions}
    cutoff=datetime.fromisoformat(ledger['finished_at'])
    daily=tuple(replace(c,completed_at=closes.get(c.start_ist.date(),c.completed_at)) for c in datasets[(TIPSMUSIC,'days',1)])
    bench_daily=tuple(replace(c,completed_at=closes.get(c.start_ist.date(),c.completed_at)) for c in datasets[(BENCHMARK,'days',1)])
    minute=datasets[(TIPSMUSIC,'minutes',1)]
    bench_minute=datasets[(BENCHMARK,'minutes',5)]
    bench_sessions=tuple(s for s in sessions if s.day>=date(2026,8,17))
    # Existing strict coverage guard rejects absent minutes/sessions. No zeros,
    # filling, carried prices or silently dropped benchmark bars are permitted.
    rv=intra_rvol(minute,sessions,daily,cutoff=cutoff,base_minutes=1)
    rvmap={r['observation_at']:r for r in rv}
    profiles=[session_profile(tuple(c for c in minute if c.start_ist.date()==s.day),s,
        cutoff=cutoff,base_minutes=1) for s in sessions]
    for dirname in ('features','charts'):
        (OUT/dirname).mkdir(exist_ok=True)
    write_csv(OUT/'features/intra-rvol-one-minute.csv',list(rv))
    write_json(OUT/'features/session-profiles.json',profiles)
    reconciliation=[]
    for s in sessions:
        full=next((c.volume for c in daily if c.start_ist.date()==s.day),None)
        lower=sum((c.volume for c in minute if c.start_ist.date()==s.day),D(0))
        reconciliation.append(dict(session_id=f'NSE:{s.day}',daily_volume=full,one_minute_volume=lower,
            signed_difference=lower-full if full is not None else None,
            status='MISSING_DAILY' if full is None else 'EQUAL' if full==lower else 'PROVIDER_RESOLUTION_MISMATCH',
            action='PRESERVE_BOTH;NO_RESCALING'))
    write_csv(OUT/'features/provider-resolution-reconciliation.csv',reconciliation)
    data_hash=hashlib.sha256(json.dumps(sorted(r['response_sha256'] for r in ledger['requests'])).encode()).hexdigest()
    source_daily_ratio,_=pine_rvol((c.volume for c in daily),20)
    collected={}
    summary={}
    for tf in TIMEFRAMES:
        bars=daily if tf=='Daily' else aggregate(minute,sessions,tf,cutoff=cutoff,base_minutes=1)
        bench=bench_daily if tf=='Daily' else aggregate(bench_minute,bench_sessions,tf,cutoff=cutoff,base_minutes=5)
        # Trim ONLY an explicit leading history outside benchmark coverage. No
        # missing date/bar inside the overlapping range is discarded.
        stock_rs=tuple(c for c in bars if bench[0].start_ist <= c.start_ist <= bench[-1].start_ist)
        rs=calculate_chart_rs(stock_rs,bench,cutoff=cutoff,timeframe=tf)
        rsmap={r['observation_at']:r['rs'] for r in rs}
        feat=volume_features(bars,cutoff=cutoff,daily=tf=='Daily')
        dtr=merge_daily_dtr(bars,daily,cutoff=cutoff,timeframe=tf)
        ema10=pine_average((c.close for c in bars),10,'EMA')
        ema20=pine_average((c.close for c in bars),20,'EMA')
        _,bar_rvol20=pine_rvol((c.volume for c in bars),20)
        rows=[];chart_rv={}
        for i,(c,f,context) in enumerate(zip(bars,feat,dtr)):
            at=c.completed_at
            if tf=='Daily':
                s=next((s for s in sessions if s.day==c.start_ist.date()),None)
                r=dict(rvmap.get(s.closes_at,{}) if s else {})
                r.update(candidate_a=source_daily_ratio[i],candidate_a_pct=source_daily_ratio[i]*100 if source_daily_ratio[i] is not None else None)
            else:
                r=rvmap.get(at,{})
            chart_rv[at]=r
            profile=next((p for p in profiles if p['available_at']<=at and
                (p['available_at']==at or (tf=='Daily' and p['session_id']==f'NSE:{c.start_ist.date()}'))),{})
            row=dict(symbol='NSE:TIPSMUSIC',timeframe=tf,bar_date=c.start_ist.date(),start_ist=c.start_ist,
                end_ist=c.end_ist,observation_at=at,open=c.open,high=c.high,low=c.low,close=c.close,
                ema10=ema10[i],ema20=ema20[i],rs55=rsmap.get(at),each_bar_rvol20_pct=bar_rvol20[i],
                candidate_a=r.get('candidate_a'),candidate_a_pct=r.get('candidate_a_pct'),
                candidate_b=r.get('candidate_b'),candidate_b_pct=r.get('candidate_b_pct'),
                vah=profile.get('vah'),val=profile.get('val'),poc=profile.get('poc'),
                **{k:v for k,v in f.items() if k!='observation_at'},
                **{f'causal_{k}':v for k,v in context['causal_last_completed_daily'].items()},
                **{f'historical_display_{k}':v for k,v in context['historical_display'].items()},
                causal_dependency_at=context['causal_dependency_at'],
                historical_atr_dependency_at=context['historical_atr_dependency_at'],
                historical_display_uses_future=context['historical_uses_future'],
                indicator_version=VERSION,data_source='Upstox Historical Candle V3',data_sha256=data_hash,
                adjustment_basis='UNKNOWN_PROVIDER_ADJUSTMENT_BASIS',calendar_status=schedule['status'])
            rows.append(row)
        collected[tf]=rows
        write_csv(OUT/f'features/{tf.lower()}.csv',rows)
        start=date(2026,5,4) if tf=='Daily' else date(2026,10,1)
        count=sum(c.start_ist.date()>=start for c in bars)
        latest=dtr[-1]['historical_display']
        atr_text=f"{latest['atr14']:.2f}" if latest['atr14'] is not None else 'unavailable'
        table=f"{latest['dtr']} vs {atr_text} INR => {latest['dtr_atr_pct']}% / {latest['rvol20_pct']}%; historical display, causal fields separate"
        (OUT/f'charts/{tf.lower()}.html').write_text(render_chart(tf,bars,feat,chart_rv,profiles,table,
            symbol='TIPSMUSIC',reference_status='Daily dated cursor reference available; other timeframes REFERENCE_MISSING; alternatives not equivalent',
            rs_values=rsmap,base_minutes=1,candidate_b_note='B uses previous20 same-offset sessions; A/B ratios shown; percentages retained separately',max_bars=count))
        summary[tf]=dict(bars=len(bars),display_bars=count,rs55_values=sum(r['rs55'] is not None for r in rows),
            candidate_b_values=sum(r['candidate_b'] is not None for r in rows))
    scorecard(collected,data_hash)
    write_json(OUT/'run-summary.json',dict(status='REAL_TIPSMUSIC_PROVIDER_OUTPUTS',timeframes=summary,
        data_sha256=data_hash,calendar_status=schedule['status'],equivalent=False))
    datasets_manifest=[]
    for receipt in ledger['requests']:
        raw=ROOT/receipt['retained_path']
        validation=raw.with_suffix('.validation.json')
        saved=json.loads(validation.read_text())
        datasets_manifest.append(dict(number=receipt['number'],raw_path=receipt['retained_path'],
            raw_sha256=receipt['response_sha256'],validation_path=str(validation.relative_to(ROOT)),
            validation_sha256=sha(validation),rows=receipt['rows'],
            earliest=min(c['start_ist'] for c in saved['candles']),latest=max(c['start_ist'] for c in saved['candles']),
            issues=saved['issues'],source_request=saved['request']))
    write_json(OUT/'data-source-manifest.json',dict(scope_sha256=ledger['scope_sha256'],requests=ledger['requests'],
        datasets=datasets_manifest,approved_manifest_unchanged=True,request_count=9,all_http_200=True,no_retries=True,
        data_sha256_definition='SHA256(JSON encoding of lexically sorted raw-response SHA256 strings; default json spacing)',
        instrument_identity_ref='instrument-identity.json',volume_units='provider units; index volume unused; no turnover inference for benchmark',
        calendar_certified=False,historical_capture_availability_certified=False,
        daily_completion_policy='explicit proposed regular session close on July21-Oct30; conservative provider bucket completion outside it',
        adjustment_basis='UNKNOWN_PROVIDER_ADJUSTMENT_BASIS'))


def scorecard(collected,data_hash):
    reference=json.loads((OUT/'reference-inventory.json').read_text())
    mapping={'open':'open','high':'high','low':'low','close':'close','rs55':'rs55','each_bar_rvol20_pct':'each_bar_rvol20_pct',
        'dtr':'historical_display_dtr','atr14':'historical_display_atr14','dtr_atr_pct':'historical_display_dtr_atr_pct',
        'rvol20_pct':'historical_display_rvol20_pct','simple_rvol21_pct':'rvol_pct'}
    scores=[]
    for r in reference['readings']:
        candidates=[x for x in collected[r['timeframe']] if str(x['bar_date'])==r['bar_date']]
        row=candidates[0] if len(candidates)==1 else (collected[r['timeframe']][-1] if r['bar_date'] is None else None)
        variants=('candidate_a_pct','candidate_b_pct') if r['metric']=='dss_rvol_display' else (mapping.get(r['metric'],r['metric']),)
        for metric in variants:
            actual=row.get(metric) if row else None
            expected=D(r['tradingview_display'])
            diff=actual-expected if actual is not None else None
            unit_verified=r['metric']!='dss_rvol_display'
            tolerance=D('.005') if r['metric']=='rs55' else D('.000001')
            comparable=actual is not None and r['bar_date'] is not None and unit_verified
            status=('MATCHED' if abs(diff)<=tolerance else 'MISMATCHED') if comparable else 'NOT_COMPARABLE'
            scores.append(dict(symbol=r['symbol'],chart_timeframe=r['timeframe'],metric=metric,
                bar_date=r['bar_date'],observation_timestamp=row['observation_at'] if row and r['bar_date'] else None,
                original_tradingview_value=expected,python_result=actual,absolute_difference=abs(diff) if diff is not None else None,
                relative_difference=diff/abs(expected) if diff is not None and expected else None,
                signal_classification_agreement=None,comparison_status=status,indicator_version=VERSION,
                data_source='Upstox Historical Candle V3',data_sha256=data_hash,reference_sha256=r['reference_sha256'],
                qualification='rounded displayed reading only; not equivalence' if comparable else
                    ('UNCONFIRMED_LAST_BAR_DATE: Python latest is '+str(row['bar_date'])+'; differences conditional on that date; DSS units also hypothetical' if row and r['bar_date'] is None else
                     'DSS display units unverified; differences are percent hypotheses only')))
    for tf,rows in collected.items():
        if tf!='Daily':
            for metric in ('candidate_a_pct','candidate_b_pct','pocket_pivot','color','ema10','ema20','rs55','vah','val'):
                scores.append(dict(symbol='NSE:TIPSMUSIC',chart_timeframe=tf,metric=metric,bar_date=None,
                    observation_timestamp=None,original_tradingview_value=None,python_result=None,absolute_difference=None,
                    relative_difference=None,signal_classification_agreement=None,comparison_status='REFERENCE_MISSING',
                    indicator_version=VERSION,data_source='Upstox Historical Candle V3',data_sha256=data_hash,
                    reference_sha256=None,qualification='no supplied original timeframe reference'))
    write_csv(OUT/'comparison-scorecard.csv',scores)


def audit_real_outputs():
    """Independent rational arithmetic over retained candle/feature outputs.

    Supplements the source-specific reference tests; does not certify feed
    adjustment basis, historical capture availability or TradingView equivalence.
    """
    counts={}
    for tf in TIMEFRAMES:
        with (OUT/f'features/{tf.lower()}.csv').open() as stream:
            rows=list(csv.DictReader(stream))
        volumes=[Fraction(r['volume']) for r in rows]
        closes=[Fraction(r['close']) for r in rows]
        direction=[0]+[(b>a)-(b<a) for a,b in zip(closes,closes[1:])]
        prior_ema={10:None,20:None}
        def check(actual,expected):
            if expected is None:
                if actual!='':
                    raise ValueError('independent audit expected unavailable input')
            elif actual=='' or abs(Fraction(actual)-expected)>=Fraction(1,10**20):
                raise ValueError('independent real-output arithmetic mismatch')
        for i,r in enumerate(rows):
            mean=sum(volumes[i-20:i+1])/21 if i>=20 else None
            previous=sum(volumes[i-21:i])/21 if i>=21 else None
            check(r['volume_sma'],mean)
            check(r['rvol_ratio'],volumes[i]/previous if previous else None)
            down=[volumes[j] for j in range(max(1,i-10),i) if direction[j]<0]
            pp=(direction[i]>0 and bool(down) and volumes[i]>max(down)) if i>=10 else None
            if r['pocket_pivot']!=('' if pp is None else str(pp)):
                raise ValueError('independent Pocket Pivot event mismatch')
            up=sum(volumes[j] for j in range(max(1,i-19),i+1) if direction[j]>0)
            dn=sum(volumes[j] for j in range(max(1,i-19),i+1) if direction[j]<0)
            check(r['up_down_ratio'],up/dn if i>=20 and dn else None)
            for n in (10,20):
                alpha=Fraction(2,n+1)
                prior_ema[n]=closes[i] if prior_ema[n] is None else alpha*closes[i]+(1-alpha)*prior_ema[n]
                check(r[f'ema{n}'],prior_ema[n])
            if r['causal_dependency_at'] and datetime.fromisoformat(r['causal_dependency_at'])>datetime.fromisoformat(r['observation_at']):
                raise ValueError('future dependency in causal fields')
        counts[tf]=dict(rows=len(rows),sma21=True,rvol21=True,pocket_pivot10=True,up_down20=True,
                       ema10_20=True,causal_dependency_timestamps=True)
    write_json(OUT/'independent-real-data-checks.json',dict(passed=True,absolute_tolerance='1e-20',
        method='exact Fraction arithmetic; independent CSV checks, no indicator functions for expected values',timeframes=counts))


def package():
    before=json.loads((OUT/'preservation-before.json').read_text())
    metadata_changes=[]
    for p,h in before['prior_artifacts'].items():
        if sha(ROOT/p)!=h:
            if Path(p).name=='.DS_Store':
                metadata_changes.append(dict(path=p,before_sha256=h,after_sha256=sha(ROOT/p),
                    classification='OS_DIRECTORY_METADATA;NOT_RESEARCH_EVIDENCE;NO_ATTEMPT_TO_RESTORE'))
                continue
            raise ValueError('prior evidence changed')
    write_json(OUT/'preservation-verification.json',dict(prior_artifacts_checked=len(before['prior_artifacts']),
        research_artifacts_checked=sum(Path(p).name!='.DS_Store' for p in before['prior_artifacts']),
        research_evidence_unchanged=True,all_original_bytes_unchanged=not metadata_changes,os_metadata_changes=metadata_changes,
        modified_source_files=MODIFIED,source_before_copies='source-before/'))
    source=ROOT/'experiments/earnings_reaction_v1'
    paths=sorted(p for p in source.rglob('*') if p.is_file() and p.suffix in ('.py','.md') and '__pycache__' not in p.parts)
    paths+=sorted(p for p in OUT.rglob('*') if p.is_file() and 'provider' not in p.relative_to(OUT).parts and p.suffix!='.zip'
        and p.name not in ('implementation-manifest.json','review-zip.sha256','.DS_Store'))
    paths+=sorted((ROOT/'indicator-settings').glob('*.png'))
    paths+=sorted((ROOT/'artifacts/earnings-reaction-v1/ta03r/sources').glob('*.pine'))
    original_references=ROOT/'artifacts/earnings-reaction-v1/ta03r3/source-references.md'
    if original_references.is_file():
        paths.append(original_references)
    write_json(OUT/'implementation-manifest.json',dict(modified_source_files=MODIFIED,added_source_files=ADDED,
        files=[dict(path=str(p.relative_to(ROOT)),sha256=sha(p),bytes=p.stat().st_size) for p in paths]))
    paths.append(OUT/'implementation-manifest.json')
    complete=(OUT/'run-summary.json').is_file()
    name='ta03r4-review.zip' if complete else 'ta03r4-pre-retrieval-review.zip'
    with zipfile.ZipFile(OUT/name,'w',zipfile.ZIP_DEFLATED) as archive:
        for p in paths:
            info=zipfile.ZipInfo(str(p.relative_to(ROOT)),date_time=(2026,10,31,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,p.read_bytes())
    (OUT/'review-zip.sha256').write_text(sha(OUT/name)+'  '+name+'\n')
    return OUT/name


def main():
    build()
    paths=sorted((OUT/'features').glob('*'))+sorted((OUT/'charts').glob('*.html'))
    first={str(p):sha(p) for p in paths}
    build()
    if first!={str(p):sha(p) for p in paths}:
        raise ValueError('non-repeatable chart/features')
    write_json(OUT/'reproducibility.json',dict(two_builds_identical=True,hashes=first))
    audit_real_outputs()
    result=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','experiments/earnings_reaction_v1/tests','-v'],
        cwd=ROOT,capture_output=True,text=True)
    (OUT/'tests.log').write_text(result.stdout+result.stderr)
    if result.returncode:
        raise RuntimeError('test failure')
    print(package())


if __name__=='__main__':
    main()
