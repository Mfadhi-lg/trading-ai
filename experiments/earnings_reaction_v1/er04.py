"""Retrospective ER04 pilot; offline, existing indicators and renderer only.

python3 -B -m experiments.earnings_reaction_v1.er04
Partial outputs are explicitly provisional until ER03D acquisition is complete.
"""
from datetime import datetime
from decimal import Decimal as D
from statistics import median
from pathlib import Path
import json
import shutil
import zipfile
from .er03 import BASE, ROOT, BENCH, OUT as ER, read_csv, write_csv, write_json, digest, candle
from . import er03_chart_upgrade as chart
from .er03d import OUT as COMPLETION

OUT=BASE/'er04'

def retained_profiles():
    """Reuse already verified ER03C profile evidence, without recalculating it."""
    path=chart.OUT/'TIPSMUSIC-data.json'
    return {r['date']:r['profile'] for r in json.loads(path.read_text())['rows'] if r['profile'] is not None}

def load_data():
    data,sources=chart.load_inputs()
    ledger=COMPLETION/'retrieval-execution.json'
    if ledger.exists():
        receipt=json.loads(ledger.read_text())
        # Partial failed batches are never silently promoted to completed inputs.
        if not receipt['complete']: raise ValueError('Incomplete acquisition ledger')
        for r in receipt['requests']:
            path=ROOT/r['retained_path']
            if digest(path)!=r['response_sha256']: raise ValueError('Response hash mismatch')
            sources[str(path.relative_to(ROOT))]=digest(path)
            key=r['instrument_key']; merged={c.start_ist:c for c in data[key]}
            for raw in json.loads(path.read_text())['data']['candles']:
                c=candle(key,raw)
                if c.start_ist in merged: raise ValueError('Acquisition overlaps retained dates')
                merged[c.start_ist]=c
            data[key]=tuple(merged[t] for t in sorted(merged))
    return data,sources

def events():
    unique={}
    for w in read_csv(ER/'approximate-event-windows.csv'):
        key=w['quarter']+':'+w['symbol']
        if key in unique:
            if any(unique[key][f]!=w[f] for f in ('reported_results_date','next_results_date')):
                raise ValueError('Conflicting event boundaries')
        else: unique[key]=dict(w,event_id=key)
    if len(unique)!=24: raise ValueError('Exact 24 earnings events required')
    return list(unique.values())

def checkpoint(event, rows, session):
    eligible=[i for i,r in enumerate(rows) if r['date']>event['reported_results_date']
              and (not event['next_results_date'] or r['date']<event['next_results_date'])]
    if len(eligible)<session:return None
    i=eligible[session-1]; r=rows[i]; prev=rows[i-1]
    pre=next((p for p in reversed(rows[:i]) if p['date']<=event['reported_results_date']),None)
    sv=r['simple']; rs=r['rs55']; pre_rs=pre['rs55'] if pre else None
    history=[rows[j] for j in eligible[:session]]
    def change(a,b):return a-b if a is not None and b is not None else None
    result=dict(event_id=event['event_id'],symbol=event['symbol'],quarter=event['quarter'],checkpoint=session,
        results_date=event['reported_results_date'],checkpoint_date=r['date'],next_results_date=event['next_results_date'],
        available_at='SESSION_CLOSE_CONCEPTUAL;PROVIDER_BUCKET_COMPLETION_NEXT_MIDNIGHT',close=r['close'],
        ema10=r['ema10'],ema20=r['ema20'],above_ema10=r['close']>=r['ema10'],above_ema20=r['close']>=r['ema20'],
        ema10_change=change(r['ema10'],prev['ema10']),ema20_change=change(r['ema20'],prev['ema20']),
        ema10_rising=r['ema10']>prev['ema10'],ema20_rising=r['ema20']>prev['ema20'],
        rs55=rs,rs55_change_from_pre_results=change(rs,pre_rs),rs55_change_one_session=change(rs,prev['rs55']),
        rs55_change_from_session1=change(rs,history[0]['rs55']),dss_pct=r['dss_pct'],dtr_atr_pct=r['dtr_atr_pct'],
        post_results_return_pct=100*(r['close']/pre['close']-1) if pre else None,
        post_results_max_high_pct=100*(max(p['high'] for p in history)/pre['close']-1) if pre else None,
        early_peak_to_close_pct=100*(r['close']/max(p['high'] for p in history)-1),
        up_close=r['close']>prev['close'],volume=r['volume'],simple_rvol_pct=sv['rvol_pct'],
        volume_change_one_session=r['volume']-prev['volume'],
        above_both_emas_post_results_sessions=sum(p['close']>=p['ema10'] and p['close']>=p['ema20'] for p in history),
        positive_rs_post_results_sessions=sum(p['rs55'] is not None and p['rs55']>0 for p in history),
        volume_sma=sv['volume_sma'],pocket_pivot=sv['pocket_pivot'],dry=sv['dry'],high_up=sv['high_up'],high_down=sv['high_down'],
        simple_final_color=sv['original_final_color'],hve=sv['original_hve'],hvy=sv['original_hvy'],hvq=sv['original_hvq'],
        lvy=sv['original_lvy'],lvq=sv['original_lvq'],up_down_ratio=sv['up_down_ratio'],
        final_color_status='AVAILABLE' if sv['original_final_color'] else 'MISSING_252_BAR_CONTEXT',
        record_context='UPSTOX_ONLY;FULL_LISTING_HISTORY_NOT_ATTESTED',
        subsector=event['subsector'],subsector_strength=None,subsector_status='UNAVAILABLE_POINT_IN_TIME_PEER_MEMBERSHIP',
        event_date_status='APPROXIMATE_RETAINED_RECORD;EARLIEST_PUBLICATION_UNVERIFIED',
        next_boundary_status='KNOWN_APPROXIMATE' if event['next_results_date'] else 'UNKNOWN_NEXT_RESULTS_DATE_CENSORED')
    return i,result

def forward(event, rows, i, benchmark, horizon):
    cp=rows[i]; later=rows[i+1:]
    allowed=[r for r in later if not event['next_results_date'] or r['date']<event['next_results_date']]
    window=allowed[:horizon]; complete=len(window)==horizon
    result=dict(event_id=event['event_id'],symbol=event['symbol'],checkpoint_date=cp['date'],horizon=horizon,
        entry_date=window[0]['date'] if window else '',exit_date=window[-1]['date'] if window else '',
        observed_sessions=len(window),horizon_complete=complete,
        censored=not complete or not bool(event['next_results_date']),
        censor_reason=('' if complete else 'NEXT_RESULTS_BOUNDARY_OR_DATA_END;')+('NEXT_RESULTS_DATE_UNKNOWN' if not event['next_results_date'] else ''),
        entry_open=None,return_pct=None,partial_return_pct=None,mfe_pct=None,mae_pct=None,max_close_drawdown_pct=None,
        benchmark_return_pct=None,relative_return_pp=None,relative_ratio_return_pct=None,
        benchmark_status='NO_FORWARD_CANDLES',excursion_basis='OBSERVED_WINDOW;ENTRY_OPEN;HIGH_LOW_EXTREMES',
        uncertainty='PROVIDER_OBSERVED_SESSIONS;ADJUSTMENT_BASIS_UNKNOWN')
    if not window:return result
    base=window[0]['open']; ret=100*(window[-1]['close']/base-1)
    peak=base; dd=D(0)
    for r in window:
        peak=max(peak,r['close']);dd=min(dd,100*(r['close']/peak-1))
    result.update(entry_open=base,return_pct=ret if complete else None,partial_return_pct=ret if not complete else None,
        mfe_pct=100*(max(r['high'] for r in window)/base-1),mae_pct=100*(min(r['low'] for r in window)/base-1),max_close_drawdown_pct=dd)
    if all(r['date'] in benchmark for r in window):
        bret=100*(benchmark[window[-1]['date']].close/benchmark[window[0]['date']].open-1)
        result.update(benchmark_status='EXACT_OBSERVED_SESSION_ALIGNMENT',benchmark_return_pct=bret if complete else None,
                      relative_return_pp=ret-bret if complete else None,
                      relative_ratio_return_pct=100*((1+ret/100)/(1+bret/100)-1) if complete else None)
    else:result['benchmark_status']='MISSING_EXACT_BENCHMARK_SESSION'
    return result

# Fixed natural reference levels, declared before viewing returns; no fitted cutoffs.
HYPOTHESES={
    'rs_improving':lambda f:None if f['rs55_change_from_pre_results'] is None else f['rs55_change_from_pre_results']>0,
    'holding_both_emas':lambda f:f['above_ema10'] and f['above_ema20'],
    'both_emas_rising':lambda f:f['ema10_change']>0 and f['ema20_change']>0,
    'constructive_volume':lambda f:None if f['dss_pct'] is None else f['up_close'] and f['dss_pct']>100,
    'positive_and_improving_rs':lambda f:None if f['rs55'] is None or f['rs55_change_from_pre_results'] is None else f['rs55']>0 and f['rs55_change_from_pre_results']>0,
    'high_volume_no_price_progress':lambda f:None if f['dss_pct'] is None else f['dss_pct']>100 and f['post_results_return_pct']<=0,
    'early_rally_lost':lambda f:f['post_results_max_high_pct']>0 and f['post_results_return_pct']<=0,
    'pocket_pivot':lambda f:f['pocket_pivot'],
    'range_expansion':lambda f:None if f['dtr_atr_pct'] is None else f['dtr_atr_pct']>100,
}

def comparisons(features, returns):
    joined={(r['event_id'],r['checkpoint'],r['horizon']):r for r in returns}
    summaries=[]; examples=[]
    for session in (1,3,5):
        for horizon in (5,10,20,40):
            for name,predicate in HYPOTHESES.items():
                for present in (True,False):
                    group=[]
                    for f in features:
                        if f['checkpoint']!=session or predicate(f) is not present:continue
                        r=joined[(f['event_id'],session,horizon)]
                        if r['relative_return_pp'] is None:continue
                        group.append(r)
                        examples.append(dict(pattern=name,condition=present,event_id=f['event_id'],symbol=f['symbol'],
                            checkpoint=session,checkpoint_date=f['checkpoint_date'],horizon=horizon,
                            relative_return_pp=r['relative_return_pp'],return_pct=r['return_pct'],mfe_pct=r['mfe_pct'],mae_pct=r['mae_pct'],
                            censored=r['censored'],interpretation='OUTPERFORMED' if r['relative_return_pp']>0 else 'UNDERPERFORMED' if r['relative_return_pp']<0 else 'TIED',
                            forward_link=f"charts/{f['event_id'].replace(':','-')}-S{session}.html"))
                    vals=[r['relative_return_pp'] for r in group]
                    summaries.append(dict(pattern=name,condition=present,checkpoint=session,horizon=horizon,
                        unique_earnings_events=len({r['event_id'] for r in group}),outperformed=sum(v>0 for v in vals),
                        underperformed=sum(v<0 for v in vals),tied=sum(v==0 for v in vals),
                        median_relative_return_pp=median(vals) if vals else None,
                        median_absolute_return_pct=median([r['return_pct'] for r in group]) if group else None,
                        unknown_boundary_events=sum(r['censored'] for r in group),
                        independent_unit='EARNINGS_EVENT_WITHIN_THIS_ROW;DO_NOT_SUM_CHECKPOINTS_OR_HORIZONS;SAME_COMPANY_AND_MARKET_DEPENDENCE'))
    return summaries,examples

def render_charts(payloads, features):
    dest=OUT/'charts';dest.mkdir(exist_ok=True)
    shutil.copytree(chart.OUT/'assets',dest/'assets',dirs_exist_ok=True)
    original=chart.OUT
    chart.OUT=dest
    names=[]
    try:
        for f in features:
            payload=dict(payloads[f['symbol']])
            # Annotations identify checkpoint only; do not put future returns in early features.
            payload['events']=payload['events']+[dict(date=f['checkpoint_date'],plotted_session=f['checkpoint_date'],
                text=f"S{f['checkpoint']} close: early observation",basis='Retrospective checkpoint; available candles only')]
            payload['availability_note']+=' ER04 checkpoint annotations are retrospective. Full chart includes later candles; use feature CSV for checkpoint-only evidence.'
            name=f"{f['event_id'].replace(':','-')}-S{f['checkpoint']}.html"
            chart.render(payload,name,dict(first=f['results_date'],last=f['checkpoint_date']))
            path=dest/name
            path.write_text(path.read_text().replace('Historical analysis · ER-04 paused','ER-04 retrospective checkpoint · exploratory'))
            names.append(name)
        (dest/'index.html').write_text('<!doctype html><meta charset="utf-8"><h1>ER04 checkpoint charts</h1>'+''.join(f'<p><a href="{n}">{n}</a></p>' for n in names))
    finally:chart.OUT=original
    return names

def render_completed_phase_a(payloads,sufficient):
    dest=COMPLETION/'charts';dest.mkdir(exist_ok=True)
    shutil.copytree(chart.OUT/'assets',dest/'assets',dirs_exist_ok=True)
    original=chart.OUT;chart.OUT=dest;names=[]
    try:
        for symbol,payload in payloads.items():
            if symbol=='TIPSMUSIC':continue  # original unaffected mixed-source chart remains preserved
            ready=any(r['simple']['original_final_color'] is not None for r in payload['rows'])
            payload=dict(payload,record_context='Upstox-only earlier daily context; '+('rolling252 classification supported' if ready else 'rolling252 context unavailable; provider returned insufficient history')+'; full listing HVE unresolved')
            name=symbol+'.html';chart.render(payload,name);names.append(name)
            for w in payload['windows']:
                name=w['event_id'].replace(':','-')+'.html'
                chart.render(payload,name,dict(first=w['observed_first_date'],last=w['observed_last_date']));names.append(name)
        for name in names:
            p=dest/name;p.write_text(p.read_text().replace('Historical analysis · ER-04 paused','ER-03D acquired daily context · see coverage limitations'))
        (dest/'index.html').write_text('<!doctype html><meta charset="utf-8"><h1>ER03D updated charts</h1>'+''.join(f'<p><a href="{n}">{n}</a></p>' for n in names))
        write_json(COMPLETION/'chart-index.json',dict(charts=names,count=len(names)))
    finally:chart.OUT=original

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    data,sources=load_data(); benchmark={str(c.start_ist.date()):c for c in data[BENCH]}
    pilots=read_csv(ER/'pilot-companies.csv')
    payloads={p['symbol']:chart.assemble(p['symbol'],p['instrument_key'],data[p['instrument_key']],benchmark,
              profiles=retained_profiles() if p['symbol']=='TIPSMUSIC' else None) for p in pilots}
    features=[]; returns=[]; audit=[]; coverage=[]
    for event in events():
        rows=payloads[event['symbol']]['rows']
        for session in (1,3,5):
            found=checkpoint(event,rows,session)
            if found is None:raise ValueError('Missing checkpoint')
            i,f=found;features.append(f)
            # Prefix recomputation proves future bars cannot affect checkpoint fields.
            key=next(p['instrument_key'] for p in pilots if p['symbol']==event['symbol'])
            prefix=chart.assemble(event['symbol'],key,data[key][:i+1],benchmark)
            _,causal=checkpoint(event,prefix['rows'],session)
            if f!=causal:raise ValueError('Future-information sensitivity')
            audit.append(dict(event_id=f['event_id'],checkpoint=session,prefix_recomputed=True,all_fields_equal=f==causal))
            for horizon in (5,10,20,40):
                r=forward(event,rows,i,benchmark,horizon);r['checkpoint']=session;returns.append(r)
    for symbol,payload in payloads.items():
        fs=[f for f in features if f['symbol']==symbol]
        coverage.append(dict(symbol=symbol,bars=len(payload['rows']),checkpoints=len(fs),
            classified_checkpoints=sum(f['simple_final_color'] is not None for f in fs),rs_checkpoints=sum(f['rs55'] is not None for f in fs),
            classified_bars=sum(r['simple']['original_final_color'] is not None for r in payload['rows']),
            source='UPSTOX_ONLY;NO_TRADINGVIEW_RECORD_PREFIX',hve_complete_history=False))
    sufficient=all(f['simple_final_color'] is not None for f in features)
    write_csv(OUT/'early-checkpoint-features.csv',features)
    write_csv(OUT/'forward-returns-drawdowns.csv',returns)
    write_csv(COMPLETION/'indicator-coverage.csv',coverage)
    write_json(OUT/'checkpoint-causality-verification.json',dict(checks=len(audit),failures=0,rows=audit))
    summaries,examples=comparisons(features,returns)
    write_csv(OUT/'continuation-fading-comparisons.csv',summaries)
    write_csv(OUT/'company-examples-and-counterexamples.csv',examples)
    names=render_charts(payloads,features)
    render_completed_phase_a(payloads,sufficient)
    write_json(OUT/'chart-index.json',dict(charts=names,count=len(names)))
    write_json(OUT/'input-provenance.json',sources)
    executed=(COMPLETION/'retrieval-execution.json').exists()
    write_json(OUT/'status.json',dict(status='ER04_RESEARCH_COMPLETE_PENDING_FINAL_VERIFICATION' if sufficient else ('ACQUISITION_EXECUTED;PARTIAL_PROVIDER_HISTORY' if executed else 'PROVISIONAL_OFFLINE_RESEARCH;PHASE_A_AUTHORIZATION_PENDING'),
        phase_a_context_sufficient=sufficient,unique_earnings_events=len(events()),checkpoints=len(features),forward_rows=len(returns),
        complete_horizons=sum(r['horizon_complete'] for r in returns),unknown_boundary_rows=sum('UNKNOWN' in r['censor_reason'] for r in returns),
        missing_color_checkpoints=sum(f['simple_final_color'] is None for f in features),subsector_point_in_time_features=0))
    report(features,returns,summaries,examples,sufficient)
    print((OUT/'status.json').read_text())

def report(features,returns,summaries,examples,sufficient):
    def display(rows):
        return [{k:round(v,4) if isinstance(v,D) else v for k,v in r.items()} for r in rows]
    features,summaries,examples=map(display,(features,summaries,examples))
    lines=['# ER04 continuation versus fading',
      'Status: '+('indicator context sufficient for the research checkpoints.' if sufficient else '**PARTIAL COVERAGE: consult acquisition ledger and indicator-coverage.csv. Approved requests executed; SHILCTECH returned only five earlier bars and remains unclassified.**'),
      'This is retrospective exploratory research, not a statistically established strategy. Twelve selected companies, 24 distinct earnings events, 72 checkpoint observations. No predictive accuracy claim, trained model, optimized thresholds or new technical indicator.',
      '## Measured facts',
      'All snapshots use current and prior bars only, verified by72 separate prefix recalculations. Sessions1/3/5 are strictly after retained result calendar dates; same-day releases are excluded conservatively. Exact earliest publication remains unverified. Provider bucket completion is next midnight: “close” denotes the completed session OHLCV, not proof of real-time delivery.',
      'Each forward window starts at the next observed session OPEN and ends at the Nth observed close. MFE/MAE use actual highs/lows relative to that entry open; close drawdown uses running closing peaks, including entry open. Relative return is stock minus benchmark percentage return, both using identical entry/exit dates and opens/closes; compounded relative ratio is supplied separately. These are price returns, not adjusted total returns or executable net P&L.',
      'Known subsequent result dates are excluded, including the entire result-date session. Unknown next dates label every observation censored/boundary-uncertain even when N candles exist. Short windows have blank full-horizon return and a separately named partial return. Excursions always describe only the observed window. Complete-horizon unknown-boundary observations are descriptive and cannot establish absence of earnings contamination.',
      'Comparisons count distinct earnings events within each checkpoint/horizon row. Never sum across checkpoints or overlapping20/40 windows. Two quarters from one company and common market shocks are correlated;24 is an event count, not proof of24 statistically independent samples. No confidence intervals or validation claims are inferred.',
      '## Fixed hypothesis comparisons',
      'Natural zero/reference thresholds only: RS change>0; close>=each EMA; EMA one-session changes>0; DSS>100% with an up close; DTR/ATR>100%; early rally lost means an observed positive high versus pre-result close but checkpoint close<=pre-result close. “Substantial” drawdown has no fitted cutoff: continuous peak-to-close, MFE, MAE and drawdown are supplied instead. Indicator hypotheses are evaluated individually and complements are in the CSV.']
    fading={'high_volume_no_price_progress','early_rally_lost'}
    for name in HYPOTHESES:
        for session in (1,3,5):
            a=next(r for r in summaries if r['pattern']==name and r['condition'] and r['checkpoint']==session and r['horizon']==20)
            b=next(r for r in summaries if r['pattern']==name and not r['condition'] and r['checkpoint']==session and r['horizon']==20)
            support=a['underperformed'] if name in fading else a['outperformed']
            contradiction=a['outperformed'] if name in fading else a['underperformed']
            lines.append(f"- {name}, S{session}, forward20: {a['unique_earnings_events']} events meet condition; {support} support the hypothesized {'fading' if name in fading else 'continuation'} direction, {contradiction} contradict, {a['tied']} tie. Median relative return {a['median_relative_return_pp']}pp; complement {b['median_relative_return_pp']}pp ({b['unique_earnings_events']} events). {a['unknown_boundary_events']} condition events have unknown next-results boundaries.")
    lines+=['## Concrete examples and counterexamples',
      'Examples below are selected retrospectively to illustrate both signs. They are not additional independent samples. Full company/event/checkpoint evidence, including contradictory outcomes, is in company-examples-and-counterexamples.csv; exact early inputs are in early-checkpoint-features.csv.']
    for name in ('rs_improving','holding_both_emas','constructive_volume','high_volume_no_price_progress','early_rally_lost'):
        pool=[r for r in examples if r['pattern']==name and r['checkpoint']==3 and r['horizon']==20]
        for condition,sign in ((True,True),(True,False),(False,True)):
            options=[r for r in pool if r['condition']==condition and (r['relative_return_pp']>0)==sign]
            if not options:continue
            r=sorted(options,key=lambda r:abs(r['relative_return_pp']),reverse=True)[0]
            f=next(f for f in features if f['event_id']==r['event_id'] and f['checkpoint']==3)
            lines.append(f"- {name}={condition}: {r['event_id']} at {r['checkpoint_date']} S3; price {f['close']}, EMA10/20 {f['ema10']}/{f['ema20']}, RS55 {f['rs55']} (change since pre-results {f['rs55_change_from_pre_results']}), DSS {f['dss_pct']}%. Next-open forward20 return {r['return_pct']}%, relative {r['relative_return_pp']}pp, MFE {r['mfe_pct']}%, MAE {r['mae_pct']}%. Boundary uncertain: {r['censored']}. [Annotated chart]({r['forward_link']}).")
    def selected(name,condition=True):
        return next(r for r in summaries if r['pattern']==name and r['condition']==condition and r['checkpoint']==3 and r['horizon']==20)
    rs,rc,ema,ec,vol,lost=selected('rs_improving'),selected('rs_improving',False),selected('holding_both_emas'),selected('holding_both_emas',False),selected('constructive_volume'),selected('early_rally_lost')
    lines+=['## Interpretation and next research questions',
      f"At S3, RS-improving events had {rs['outperformed']} of {rs['unique_earnings_events']} outperformers over the next20 sessions, versus {rc['outperformed']} of {rc['unique_earnings_events']} without RS improvement. Holding both EMAs had {ema['outperformed']} of {ema['unique_earnings_events']} outperformers, versus {ec['outperformed']} of {ec['unique_earnings_events']} for its complement. Constructive volume split {vol['outperformed']}/{vol['underperformed']} outperforming/underperforming. Early rally lost had {lost['outperformed']} of {lost['unique_earnings_events']} subsequent outperformers. These are descriptive counts with unknown-boundary events included. They do not justify assuming the proposed continuation/fading relationships. Earlier EMA initialization may alter classifications after acquisition; regenerate these findings with completed context.",
      'The pattern-by-pattern support and contradiction counts above are the evidence; individual indicators admit counterexamples. Compare all checkpoints and horizons in the CSV before attributing stability to any association. These nested hypotheses, checkpoints and horizons create multiple-comparison risk. Event selection, only two quarters, market regime, same-company dependence, unverified adjustment basis and missing calendar certification prevent population-level inference.',
      'Prioritize a preregistered larger-cohort test of whether rising RS adds information beyond simply holding both EMAs, and whether volume expansion has different associations when price advances versus stalls. Test consistency across quarters and sectors, with event/company clustering and untouched evaluation data. Range expansion and Pocket Pivots should remain separate hypotheses. Do not turn exploratory threshold comparisons into validated trading rules.',
      'Subsector environment cannot be evaluated as checkpoint-available evidence: current classifications were documented2026-11-02, after these events, and the retained pilot baskets are small retrospective subsets. No historical membership is backdated and no new peer instruments are fetched. This hypothesis remains untested.',
      'All new research indicator calculations use genuine Upstox candles only. No TradingView record prefix is mixed in. HVE/listing-history certainty remains unresolved even after252-bar context. Existing EMA/RMA initialization, saved Simple Volume settings and RS alignment semantics are retained; earlier history may change EMA/RMA seeds, which is expected rather than a formula change. Full charts contain future bars and latest-only record labels, so they illustrate outcomes; the feature matrix is the authoritative checkpoint-only evidence.',
      'Phase A acquisition receipts, expected missing dates and coverage are under ../er03/data-completion. Previous charts and research evidence remain byte-preserved. STOP after ER04; no cohort expansion, ML, strategy or production changes.']
    (OUT/'RESEARCH_CONCLUSIONS.md').write_text('\n\n'.join(lines)+'\n')

def preservation():
    before=json.loads((COMPLETION/'preservation-before.json').read_text())
    changed=[p for p,h in before.items() if not (ROOT/p).is_file() or digest(ROOT/p)!=h]
    write_json(COMPLETION/'preservation-verification.json',dict(files_checked=len(before),changed=changed,passed=not changed))
    if changed:raise ValueError('Earlier evidence changed')

def package():
    preservation()
    code=OUT/'code';code.mkdir(exist_ok=True)
    for name in ('er03d.py','er04.py','retrieve_er03d.py','verify_er04.py'):
        p=Path(__file__).with_name(name)
        if p.exists():shutil.copyfile(p,code/name)
    for name in ('test_er04.py',):
        p=Path(__file__).with_name('tests')/name
        if p.exists():shutil.copyfile(p,code/name)
    files=[p for directory in (COMPLETION,OUT) for p in directory.rglob('*') if p.is_file() and p.suffix!='.zip' and p.name!='SHA256SUMS']
    sums=OUT/'SHA256SUMS';sums.write_text(''.join(f'{digest(p)}  {p.relative_to(BASE)}\n' for p in sorted(files)))
    sources=json.loads((OUT/'input-provenance.json').read_text())
    dependencies=[ROOT/p for p in sources]
    dependencies += [ER/'market-data-inventory.json',ER/'pilot-companies.csv',ER/'approximate-event-windows.csv',chart.OUT/'TIPSMUSIC-data.json']
    dependencies += [p for p in Path(__file__).parent.glob('*.py')]
    dependencies += [Path(__file__).parent/'tests/test_er04.py']
    dependencies += [p for d in (chart.ASSETS,chart.OUT/'assets') for p in d.iterdir() if p.is_file()]
    for relative in ('simple-volume-reconstruction/apply_to_upstox.py',
        'simple-volume-reconstruction/daily-listing-history/reference.csv',
        'simple-volume-reconstruction/daily-listing-history/result.json',
        'simple-volume-reconstruction/SIMPLE_VOLUME_UPSTOX_PREDICTIONS.csv',
        'ta03r4/features/daily.csv',
        'indicator-math-validation/live-dss-attempt/timeframe-validation/daily_L20/live-values.csv'):
        dependencies.append(BASE/relative)
    included=sorted(set(files+[sums]+dependencies))
    with zipfile.ZipFile(OUT/'ER-03D-ER-04-review.zip','w',zipfile.ZIP_DEFLATED) as z:
        for p in included:z.write(p,str(p.relative_to(ROOT)))
    print('Review ZIP packaged')

if __name__=='__main__':
    import sys
    package() if sys.argv[1:]==['package'] else main()
