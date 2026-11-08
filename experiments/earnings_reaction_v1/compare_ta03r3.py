"""Offline review package builder. No HTTP, credentials or previous-evidence writes.

python3 -B -m experiments.earnings_reaction_v1.compare_ta03r3
"""
from dataclasses import asdict
from datetime import datetime, time
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
import csv
import hashlib
import html
import json
import subprocess
import sys
import zipfile

from .candles import IST
from .events import Session
from .upstox_candles import REQUESTS, parse_response
from .indicators import calculate_daily, pine_average, pine_rvol
from .indicator_alternatives import (VERSION, TIMEFRAMES, aggregate, intra_rvol,
    volume_features, session_profile, latest_markers)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'artifacts/earnings-reaction-v1/ta03r3'
BASE = ROOT/'artifacts/earnings-reaction-v1/ta02b'
SOURCE_FILES = [
    'experiments/earnings_reaction_v1/indicator_alternatives.py',
    'experiments/earnings_reaction_v1/compare_ta03r3.py',
    'experiments/earnings_reaction_v1/TA03R3_CONTRACT.md',
    'experiments/earnings_reaction_v1/tests/test_indicator_alternatives.py',
    'experiments/earnings_reaction_v1/tests/test_ta03r3_package.py',
]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value,default=str,indent=2,sort_keys=True)+'\n')


def write_csv(path, rows):
    if not rows:
        raise ValueError('no rows; refuse empty feature output')
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_retained():
    """Check original evidence, reparse raw provider bytes, reconcile all candles."""
    for name in ('SHA256SUMS','TA02D-SHA256SUMS'):
        for line in (BASE/name).read_text().splitlines():
            digest,relative=line.split(maxsplit=1)
            if sha(ROOT/relative.strip()) != digest:
                raise ValueError('retained source checksum mismatch')
    batches=[]
    for number,request in enumerate(REQUESTS,1):
        raw,= [p for p in (BASE/'ta02d-retrieved').glob(f'{number}-*.json') if not p.name.endswith('.validation.json')]
        retained=json.loads(raw.with_suffix('.validation.json').read_text())
        batch=parse_response(raw.read_bytes(),request,
            retrieved_at=datetime.fromisoformat(retained['retrieved_at_utc']),
            raw_response_ref=retained['raw_response_ref'],
            instrument_metadata_ref=retained['instrument_metadata_ref'],instrument_metadata_verified=True)
        if batch.response_sha256 != retained['response_sha256']:
            raise ValueError('response identity mismatch')
        if json.loads(json.dumps([asdict(c) for c in batch.candles],default=str)) != retained['candles']:
            raise ValueError('candle reconciliation failed')
        batches.append(batch)
    return tuple(batches)


def render_chart(tf, bars, features, rvol, profiles, dtr, *, symbol='AETHER',
                 reference_status=None, rs_values=None, base_minutes=5,
                 candidate_b_note=None, max_bars=90):
    """Deterministic standalone HTML/SVG; no CDN, library or synthetic prices."""
    shown=bars[-max_bars:]
    offset=len(bars)-len(shown)
    feat=features[offset:]
    width=1200
    def text(x,y,value,color='#cbd5e1',size=12):
        return f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}">{html.escape(str(value))}</text>'
    height=1040 if rs_values is not None else 890
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 {height}" role="img" aria-label="{html.escape(symbol)} independent indicator research chart">',
         f'<rect width="1200" height="{height}" fill="#101722"/>']
    svg += [text(20,28,f'{symbol} · {tf} · Upstox retained real OHLCV',size=20),
            text(20,50,reference_status or 'REFERENCE_MISSING — no TIPSMUSIC comparison; adjustment basis and exchange calendar certification unresolved'),
            text(20,70,'EMA 10 cyan / EMA 20 amber · final VAH red / VAL green ticks at close · INR' if rs_values is not None else
                 'EMA 10 cyan / EMA 20 amber · final session VAH/VAL yellow dots shown only at close · INR')]
    left,right=85,1120
    x=lambda i:left+(i+.5)*(right-left)/len(shown)
    lo=float(min(c.low for c in shown))*.995
    hi=float(max(c.high for c in shown))*1.005
    y=lambda v:370-(float(v)-lo)/(hi-lo)*270
    for j in range(6):
        v=lo+(hi-lo)*j/5
        svg += [f'<path d="M {left} {y(v):.2f} H {right}" stroke="#263244"/>',text(10,y(v)+4,f'{v:.2f}')]
    for i,c in enumerate(shown):
        color='#28bfa1' if c.close >= c.open else '#ec5967'
        cx=x(i)
        w=max(2,min(9,(right-left)/len(shown)*.65))
        top=min(y(c.open),y(c.close))
        svg += [f'<g><title>{c.start_ist.isoformat()} / {c.end_ist.isoformat()} O={c.open} H={c.high} L={c.low} C={c.close} V={c.volume}</title>',
                f'<path d="M {cx:.2f} {y(c.high):.2f} V {y(c.low):.2f}" stroke="{color}"/>',
                f'<rect x="{cx-w/2:.2f}" y="{top:.2f}" width="{w:.2f}" height="{max(1,abs(y(c.open)-y(c.close))):.2f}" fill="{color}"/></g>']
    for n,color in ((10,'#56d9e4'),(20,'#f5ba56')):
        vals=pine_average((c.close for c in bars),n,'EMA')[offset:]
        pts=' '.join(f'{x(i):.2f},{y(v):.2f}' for i,v in enumerate(vals))
        svg += [f'<polyline points="{pts}" stroke="{color}" fill="none"/>']
    for p in profiles:
        # No completed-session profile is painted back onto earlier intraday bars.
        for i,c in enumerate(shown):
            if c.completed_at == p['available_at'] or (tf=='Daily' and c.start_ist.date()==p['available_at'].date()):
                for k in ('vah','val'):
                    if p[k] is not None:
                        if rs_values is None:
                            svg += [f'<circle cx="{x(i):.2f}" cy="{y(p[k]):.2f}" r="3" fill="#e4d477"/>',
                                    text(x(i)+5,y(p[k])-3,f'{k.upper()} {p[k]}',size=9)]
                        else:
                            color='#f04f65' if k=='vah' else '#50b357'
                            svg += [f'<path d="M {x(i)-3:.2f} {y(p[k]):.2f} h 6" stroke="{color}" stroke-width="2"/>',
                                    f'<circle cx="{x(i):.2f}" cy="{y(p[k]):.2f}" r="5" fill="transparent"><title>{p["session_id"]} final {k.upper()}={p[k]} available {p["available_at"]}</title></circle>']
                            if i==len(shown)-1:
                                svg += [text(x(i)-65,y(p[k])-4,f'{k.upper()} {p[k]}',color,size=9)]
    svg += [text(20,400,'DTR / ATR14 / RVOL20: '+dtr),
            text(20,425,'DSS research alternatives · ratio x · A purple: cumulative/full-day mean · B lilac: same-time cumulative mean')]
    rvvals=[(rvol.get(c.completed_at,{}).get('candidate_a'),rvol.get(c.completed_at,{}).get('candidate_b')) for c in shown]
    allv=[float(v) for pair in rvvals for v in pair if v is not None]
    if allv:
        ymax=max(1.1,max(allv)*1.1)
        for j in (0,1):
            for i,(a,b) in enumerate(rvvals):
                v=(a,b)[j]
                if v is not None:
                    yy=530-float(v)/ymax*75
                    svg += [f'<circle cx="{x(i):.2f}" cy="{yy:.2f}" r="2.5" fill="{("#ad78e8","#e5c4ff")[j]}"/>']
                    if rs_values is not None and i and rvvals[i-1][j] is not None:
                        prior_y=530-float(rvvals[i-1][j])/ymax*75
                        svg += [f'<path d="M {x(i-1):.2f} {prior_y:.2f} L {x(i):.2f} {yy:.2f}" stroke="{("#ad78e8","#e5c4ff")[j]}" fill="none"/>']
        svg += [text(10,460,f'{ymax:.2f}x'),text(10,530,'0x'),text(85,550,candidate_b_note or 'Candidate B UNAVAILABLE: only 3 sessions retained; requires 20 preceding sessions')]
    else:
        svg += [text(85,490,'UNAVAILABLE: no supported session history')]
    svg += [text(20,580,'Simple Volume alternative · provider volume units · SMA21 white · pocket pivots blue · independent color precedence')]
    vmax=max(float(r['volume']) for r in feat)*1.1
    for i,r in enumerate(feat):
        height=float(r['volume'])/vmax*95 if vmax else 0
        svg += [f'<rect x="{x(i)-3:.2f}" y="{705-height:.2f}" width="6" height="{height:.2f}" fill="{r["color"]}"/>']
    markers=latest_markers(features,cutoff=bars[-1].completed_at)
    for name,index in markers.items():
        if index is not None and index >= offset:
            i=index-offset
            yy=705-float(features[index]['volume'])/vmax*95-6 if vmax else 700
            svg += [text(x(i)-20,yy,name.replace('record_high_','RH').replace('record_observed_history','RH retained'),size=9)]
    path=[]
    for i,r in enumerate(feat):
        if r['volume_sma'] is not None and vmax:
            path.append(f'{x(i):.2f},{705-float(r["volume_sma"])/vmax*95:.2f}')
    if path:
        svg += [f'<polyline points="{" ".join(path)}" stroke="#ddd" fill="none"/>']
    svg += [text(10,615,f'{vmax:.0f}'),text(10,705,'0')]
    shift=150 if rs_values is not None else 0
    if rs_values is None:
        svg += [text(20,745,'Relative Strength 55 / Close / NSE:NIFTYMIDSML400: UNAVAILABLE — configured benchmark not retained')]
    else:
        svg += [text(20,745,'Relative Strength 55 / Close / NSE:NIFTYMIDSML400 · dimensionless ratio')]
        values=[rs_values.get(c.completed_at) for c in shown]
        available=[float(v) for v in values if v is not None]+[0.0]
        bottom,top=min(available),max(available)
        span=top-bottom or 1
        yy=lambda v:855-(float(v)-bottom)/span*90
        svg += [f'<path d="M {left} {yy(0):.2f} H {right}" stroke="#b0418b"/>',text(10,765,f'{top:.3f}'),text(10,855,f'{bottom:.3f}')]
        for i,v in enumerate(values):
            if v is None:
                continue
            color='#50b357' if v>0 else '#ed5967'
            svg += [f'<circle cx="{x(i):.2f}" cy="{yy(v):.2f}" r="1.8" fill="{color}"/>']
            if i and values[i-1] is not None:
                svg += [f'<path d="M {x(i-1):.2f} {yy(values[i-1]):.2f} L {x(i):.2f} {yy(v):.2f}" stroke="{color}"/>']
    svg += [text(20,775+shift,f'VAH/VAL: fixed INR 1 bins from {base_minutes}-minute uniform-range allocation; approximation, not TradingView HD'),
            text(20,800+shift,'Nulls mean missing data/warmup; EMA seeded at retained history start; weekly remains a deferred requirement.')]
    for i in sorted({0,len(shown)//2,len(shown)-1}):
        svg += [text(x(i)-45,835+shift,shown[i].start_ist.strftime('%Y-%m-%d %H:%M'),size=10)]
    svg += [text(480,865+shift,'Observation / exchange session time (IST)'),'</svg>']
    return '<!doctype html><meta charset="utf-8"><title>'+html.escape(symbol)+' '+html.escape(tf)+' research</title><style>body{margin:0;background:#101722;font-family:system-ui}svg{width:100%;max-width:1600px;display:block;margin:auto}</style>'+''.join(svg)


def build():
    OUT.mkdir(parents=True,exist_ok=True)
    for d in ('features','charts'):
        (OUT/d).mkdir(exist_ok=True)
    preservation=json.loads((OUT/'preservation-before.json').read_text())
    for p,h in preservation.items():
        if sha(ROOT/p) != h:
            raise ValueError(f'preservation boundary changed: {p}')
    batches=load_retained()
    daily=tuple(sorted(batches[0].candles,key=lambda c:c.start_ist))
    minute=tuple(sorted(batches[1].candles,key=lambda c:c.start_ist))
    cutoff=min(b.retrieved_at_utc for b in batches)
    # An explicit provisional schedule for this bounded fixture, not a new
    # exchange calendar inferred from weekdays. Holidays outside it unasserted.
    sessions=tuple(Session(datetime.fromisoformat(day).date(),
        datetime.combine(datetime.fromisoformat(day).date(),time(9,15),IST),
        datetime.combine(datetime.fromisoformat(day).date(),time(15,30),IST))
        for day in ('2026-07-30','2026-07-31','2026-08-03'))
    write_json(OUT/'session-schedule.json',dict(status='PROVISIONAL_BOUNDED_REGULAR_HOURS;NOT_EXCHANGE_CALENDAR_CERTIFIED',
        sessions=[asdict(s) for s in sessions],reason='explicit schedule; validated 75 five-minute bars per date; missing sessions outside this scope unresolved'))
    rv=intra_rvol(minute,sessions,daily,cutoff=cutoff)
    # Independent exact rational check using raw candle volumes rather than the
    # implementation's accumulators or Decimal baseline fields.
    for row in rv:
        day=row['observation_at'].date()
        prior=[c.volume for c in daily if c.start_ist.date() < day][-20:]
        total=sum((Fraction(c.volume) for c in minute if c.start_ist.date()==day and c.end_ist <= row['observation_at']),Fraction(0))
        expected=total*20/sum(map(Fraction,prior))
        if abs(Fraction(row['candidate_a'])-expected) >= Fraction(1,10**20):
            raise ValueError('independent real-data candidate A check failed')
    write_json(OUT/'independent-real-data-checks.json',dict(candidate_a_rows=len(rv),
        method='exact Fraction sums directly from raw-reparsed volumes; preceding20 full days',
        absolute_tolerance='1e-20',passed=True,candidate_b='NO_ELIGIBLE_BASELINE;NOT_NUMERICALLY_REAL_DATA_TESTED'))
    write_csv(OUT/'features/intra-rvol-5-minute.csv',list(rv))
    rvmap={r['observation_at']:r for r in rv}
    profiles=[session_profile(tuple(c for c in minute if c.start_ist.date()==s.day),s,cutoff=cutoff) for s in sessions]
    write_json(OUT/'features/session-profiles.json',profiles)
    reconciliation=[]
    for s in sessions:
        full=next(c.volume for c in daily if c.start_ist.date()==s.day)
        lower=sum(c.volume for c in minute if c.start_ist.date()==s.day)
        reconciliation.append(dict(session_id=f'NSE:{s.day}',daily_volume=full,five_minute_volume=lower,
            absolute_difference=lower-full,relative_difference=(lower-full)/full,
            status='EQUAL' if lower==full else 'PROVIDER_RESOLUTION_MISMATCH',
            action='preserve_both;no_rescaling_or_synthetic_volume'))
    write_csv(OUT/'features/provider-resolution-reconciliation.csv',reconciliation)
    source_daily=calculate_daily(daily,batches[2].candles,cutoff=cutoff)
    score,summary=[],{}
    metrics=('candidate_a','candidate_b','pocket_pivot','volume_sma','rvol_ratio','up_down_ratio','vah','val','rs55')
    for tf in TIMEFRAMES:
        bars=daily if tf=='Daily' else aggregate(minute,sessions,tf,cutoff=cutoff)
        features=volume_features(bars,cutoff=cutoff,daily=tf=='Daily')
        ema10=pine_average((c.close for c in bars),10,'EMA')
        ema20=pine_average((c.close for c in bars),20,'EMA')
        rows=[]
        chart_rv=dict(rvmap)
        digest=hashlib.sha256(json.dumps(sorted(b.response_sha256 for b in batches)).encode()).hexdigest()
        for i,(c,f) in enumerate(zip(bars,features)):
            # Never backdate a provider daily bucket's conservative completion.
            observation=c.completed_at
            rr=rvmap.get(observation,{})
            if tf=='Daily':
                # Candidate A at completed daily close: cumulative is final V.
                rr=dict(rr,candidate_a=source_daily[i].rvol_ratio)
                chart_rv[observation]=rr
                chart_rv[c.completed_at]=rr
            pp=next((p for p in profiles if p['available_at'] <= observation and
                (p['available_at']==observation or (tf=='Daily' and p['session_id']==f'NSE:{c.start_ist.date()}'))),{})
            row=dict(symbol='NSE:AETHER',timeframe=tf,start_ist=c.start_ist,end_ist=c.end_ist,
                observation_at=observation,open=c.open,high=c.high,low=c.low,close=c.close,
                ema10=ema10[i],ema20=ema20[i],candidate_a=rr.get('candidate_a'),candidate_b=rr.get('candidate_b'),
                vah=pp.get('vah'),val=pp.get('val'),rs55=None,**{k:v for k,v in f.items() if k!='observation_at'},
                dtr=source_daily[i].dtr if tf=='Daily' else None,
                atr14=source_daily[i].atr if tf=='Daily' else None,
                dtr_atr_pct=source_daily[i].dtr_pct if tf=='Daily' else None,
                source_rvol20_pct=source_daily[i].rvol_pct if tf=='Daily' else None,
                indicator_version=VERSION,data_source='Upstox Historical Candle V3',data_sha256=digest,
                adjustment_status=batches[0].adjustment_status)
            rows.append(row)
            for metric in metrics:
                score.append(dict(symbol='NSE:AETHER',chart_timeframe=tf,observation_timestamp=observation,
                    session_id=f'NSE:{c.start_ist.date()}',metric=metric,original_tradingview_value=None,
                    python_result=row.get(metric),absolute_difference=None,relative_difference=None,
                    signal_classification_agreement=None,indicator_version=VERSION,
                    data_source=row['data_source'],data_sha256=digest,comparison_status='REFERENCE_MISSING',
                    reference_symbol='NSE:TIPSMUSIC',reason='no matched symbol/timeframe/timestamp TradingView observation'))
        slug=tf.lower()
        write_csv(OUT/f'features/{slug}.csv',rows)
        # Daily source calculations retained internally as daily values only.
        dtr='UNAVAILABLE: daily security merge / developing ATR is unsupported'
        if tf=='Daily':
            last=source_daily[-1]
            dtr=f'{last.dtr} INR / {last.atr:.6f} INR / {last.rvol_pct}% (completed daily; TA-03R)'
        (OUT/f'charts/{slug}.html').write_text(render_chart(tf,bars,features,chart_rv,profiles,dtr))
        summary[tf]=dict(real_bars=len(bars),pocket_pivots=sum(r['pocket_pivot'] is True for r in features),
            candidate_a_count=sum(r['candidate_a'] is not None for r in rows),candidate_b_count=sum(r['candidate_b'] is not None for r in rows),
            vah_val_count=sum(r['vah'] is not None for r in rows),
            volume_sma_count=sum(r['volume_sma'] is not None for r in features),
            latest_research_markers=latest_markers(features,cutoff=cutoff))
    # Target symbol remains separate; no substitution masquerading as comparison.
    for tf in TIMEFRAMES:
        for metric in metrics:
            score.append(dict(symbol='NSE:TIPSMUSIC',chart_timeframe=tf,observation_timestamp=None,
                session_id=None,metric=metric,original_tradingview_value=None,python_result=None,
                absolute_difference=None,relative_difference=None,signal_classification_agreement=None,
                indicator_version=VERSION,data_source=None,data_sha256=None,comparison_status='NOT_COMPARABLE',
                reference_symbol='NSE:TIPSMUSIC',reason='TIPSMUSIC candles and dated reference observations unavailable'))
    write_csv(OUT/'comparison-scorecard.csv',score)
    write_json(OUT/'run-summary.json',dict(timeframes=summary,sources=[dict(instrument_key=b.request.instrument_key,
        rows=len(b.candles),sha256=b.response_sha256,raw_response_ref=b.raw_response_ref,
        retrieved_at=b.retrieved_at_utc,issues=b.issues,adjustment_status=b.adjustment_status) for b in batches],
        equivalence=False,visually_compared=False,numerically_compared=False,
        data_sha256_definition='SHA256 of JSON encoding of lexically sorted three raw response SHA256 strings, default json spacing',
        candidate_b_real_data_tested=False,causal_scope='completed observations only; not historical acquisition availability certification'))
    gates={}
    for name in ('intra_rvol_candidate_a','intra_rvol_candidate_b','simple_volume_alternative','session_profile_alternative','ema10_20_reused'):
        gates[name]=dict(IMPLEMENTED=True,UNIT_TESTED=True,
            REAL_DATA_TESTED=name!='intra_rvol_candidate_b',VISUALLY_COMPARED=False,
            NUMERICALLY_COMPARED=False,EQUIVALENT=False,CAUSAL_RESEARCH_SAFE=True,
            causal_scope='completed-observation mathematical contract only; prefix/cutoff tested',
            historical_backtest_admissible=False,
            qualifications='see contract and run-summary for warmup and calendar/adjustment/availability gaps')
    write_json(OUT/'acceptance-gates.json',gates)
    for p,h in preservation.items():
        if sha(ROOT/p) != h:
            raise ValueError('preservation verification failed')
    write_json(OUT/'preservation-verification.json',dict(files_checked=len(preservation),all_unchanged=True))


def package():
    # Include runnable authoritative dependencies and original attribution;
    # input provider datasets remain in place and are referenced by hash.
    dependencies=['experiments/__init__.py','experiments/earnings_reaction_v1/__init__.py',
        'experiments/earnings_reaction_v1/candles.py','experiments/earnings_reaction_v1/events.py',
        'experiments/earnings_reaction_v1/indicators.py','experiments/earnings_reaction_v1/upstox_candles.py',
        'artifacts/earnings-reaction-v1/ta03r/sources/relative_strength.pine',
        'artifacts/earnings-reaction-v1/ta03r/sources/dtr_atr_rvol.pine']
    paths=[ROOT/p for p in SOURCE_FILES+dependencies if (ROOT/p).is_file()]
    paths += sorted(p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip' and p.name!='implementation-manifest.json')
    manifest=dict(version=VERSION,added_files=SOURCE_FILES,modified_preexisting_files=[],
        manifest_excludes_own_hash=True,zip_excluded_from_manifest=True,
        files=[dict(path=str(p.relative_to(ROOT)),sha256=sha(p),bytes=p.stat().st_size) for p in paths])
    write_json(OUT/'implementation-manifest.json',manifest)
    paths.append(OUT/'implementation-manifest.json')
    with zipfile.ZipFile(OUT/'ta03r3-review.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for p in paths:
            info=zipfile.ZipInfo(str(p.relative_to(ROOT)),date_time=(2026,10,31,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,p.read_bytes())


def main():
    build()
    def fingerprints():
        paths=sorted((OUT/'features').glob('*'))+sorted((OUT/'charts').glob('*.html'))+[OUT/'comparison-scorecard.csv']
        return {str(p.relative_to(ROOT)):sha(p) for p in paths}
    before=fingerprints()
    build()
    after=fingerprints()
    if before != after:
        raise ValueError('non-repeatable feature/chart outputs')
    write_json(OUT/'reproducibility.json',dict(repeated_builds=2,all_identical=True,files=after))
    test=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',
        'experiments/earnings_reaction_v1/tests','-v'],cwd=ROOT,capture_output=True,text=True)
    (OUT/'tests.log').write_text(test.stdout+test.stderr)
    if test.returncode:
        raise RuntimeError('tests failed; see ta03r3/tests.log')
    package()
    print('Built TA-03R3 offline package:',OUT/'ta03r3-review.zip')


if __name__=='__main__':
    main()
