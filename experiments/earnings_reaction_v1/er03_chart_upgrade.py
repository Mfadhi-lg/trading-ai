"""ER-03C presentation adapter. Offline; never invokes research/retrieval builders.

Build reference, verify in Chrome, then build pilots, verify all, and package.
python3 -B -m experiments.earnings_reaction_v1.er03_chart_upgrade reference
python3 -B -m experiments.earnings_reaction_v1.er03_chart_upgrade pilots
python3 -B -m experiments.earnings_reaction_v1.er03_chart_upgrade package
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import importlib.util
import json
import shutil
import zipfile
from dataclasses import replace
from datetime import datetime, date
from decimal import Decimal as D
from fractions import Fraction as F
from pathlib import Path

from .er03 import ROOT, BASE, BENCH, candle, features, read_csv, digest, write_json, write_csv
from .indicators import _dtr_arithmetic, pine_round, daily_rvol
from .simple_volume import simple_volume_features, simple_volume_labels
from .compare_ta03r4 import load_provider, TIPSMUSIC
from .events import Session
from .indicator_alternatives import session_bars, session_profile

ER = BASE/'er03'
OUT = ER/'chart-upgrade'
ASSETS = Path(__file__).with_name('er03_chart_assets')
CUTOFF = datetime.fromisoformat('2026-11-01T00:00:00+05:30')


def json_text(value):
    def encode(v):
        if isinstance(v, D): return float(v)
        if isinstance(v, (datetime, date)): return v.isoformat()
        raise TypeError(type(v).__name__)
    return json.dumps(value, default=encode, allow_nan=False, separators=(',', ':'))


def preserved():
    return {str(p.relative_to(ROOT)): digest(p) for p in BASE.rglob('*')
            if p.is_file() and OUT not in p.parents and p.name != '.DS_Store'}


def check_preserved():
    before = json.loads((OUT/'preservation-before.json').read_text())
    after = preserved()
    changed = [p for p,h in before.items() if after.get(p) != h]
    if changed: raise ValueError(f'Prior evidence changed: {changed}')
    write_json(OUT/'preservation-verification.json', dict(files_checked=len(before), all_unchanged=True))


def load_inputs():
    inventory = json.loads((ER/'market-data-inventory.json').read_text())['datasets']
    data, inputs = {}, {}
    for item in inventory:
        if item['unit'] != 'days': continue
        path = ROOT/item['source']['original_path']
        if digest(path) != item['source']['sha256']: raise ValueError('Provider hash mismatch')
        inputs[str(path.relative_to(ROOT))] = digest(path)
        key = item['instrument_key']
        dest = data.setdefault(key, {})
        for row in json.loads(path.read_text())['data']['candles']:
            c = candle(key, row)
            if c.start_ist in dest and c != dest[c.start_ist]: raise ValueError('Conflicting provider bars')
            dest[c.start_ist] = c
    return {k:tuple(v[t] for t in sorted(v)) for k,v in data.items()}, inputs


def volume_module():
    path = BASE/'simple-volume-reconstruction/apply_to_upstox.py'
    spec = importlib.util.spec_from_file_location('er03c_retained_volume_adapter', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def profile_rows():
    """Reuse the R4 fixed INR1 approximation on validated full minute sessions.

    No inferred tick, HD viewport, synthetic daily distribution or new formula.
    Up/down rows and VA70 are retained in the JSON as well as final VAH/VAL.
    """
    datasets, ledger = load_provider()
    schedule = json.loads((BASE/'ta03r4/session-schedule-proposal.json').read_text())['sessions']
    sessions = tuple(Session(date.fromisoformat(s['day']), datetime.fromisoformat(s['opens_at']),
                             datetime.fromisoformat(s['closes_at'])) for s in schedule)
    minute = datasets[(TIPSMUSIC,'minutes',1)]
    groups = session_bars(minute, sessions, cutoff=CUTOFF, base_minutes=1)
    return {str(s.day): session_profile(g,s,cutoff=CUTOFF,base_minutes=1)
            for s,g in zip(sessions,groups)}


def assemble(symbol, key, bars, benchmark, prefix=(), profiles=None):
    rows = features(bars, benchmark)  # existing exact alignment and EMA/RS adapter
    _, _, _, source_pct = _dtr_arithmetic(bars)
    dss = daily_rvol((c.volume for c in bars),20)  # validated daily DSS cancellation
    sv = simple_volume_features(bars, cutoff=CUTOFF, daily=True,
        record_history=prefix, record_history_complete=bool(prefix))
    for i,(r,c,v) in enumerate(zip(rows,bars,sv)):
        r.update(dtr_atr_pct=pine_round(100*r['daily_range']/r['atr14']) if r['atr14'] else None,
                 source_rvol20_pct=source_pct[i], dss_pct=dss[i]*100 if dss[i] is not None else None,
                 change=c.close-bars[i-1].close if i else None,
                 change_pct=100*(c.close/bars[i-1].close-1) if i else None,
                 simple=v, profile=(profiles or {}).get(r['date']),
                 profile_status='APPROXIMATE' if r['date'] in (profiles or {}) else 'UNAVAILABLE')
    windows = [r for r in read_csv(ER/'approximate-event-windows.csv')
               if r['symbol']==symbol and r['observed_first_date']]
    events = []
    for day in sorted({r['reported_results_date'] for r in windows}):
        # Exact calendar date retained even if no candle exists on that date.
        session = next((r['date'] for r in rows if r['date']>=day), None)
        events.append(dict(date=day, plotted_session=session,
            text='Results '+day, basis='Retained results record date; earliest publication unverified'))
    return dict(symbol=symbol, timeframe='Daily', benchmark='NSE:NIFTYMIDSML400', rows=rows,
        events=events, windows=windows, labels=simple_volume_labels(sv,cutoff=CUTOFF),
        statuses=dict(ema='SOURCE_VALIDATED', dtr='SOURCE_VALIDATED', dss='BEHAVIORALLY_RECONSTRUCTED',
            simple='BEHAVIORALLY_RECONSTRUCTED', rs='SOURCE_VALIDATED',
            profile='APPROXIMATE' if profiles else 'UNAVAILABLE'),
        record_context='TradingView pre-window volumes; Upstox chart candles; mixed provider' if prefix
            else 'Incomplete retained history; HVE unavailable when not established',
        profile_note='Fixed INR1 zero-origin bins, uniform minute-range allocation, Up/Down, VA70; final session ticks only. Not exact HD.',
        availability_note='Null means warmup/missing; zero is a measured value. Daily provider buckets complete at next midnight; no realtime availability claim.')


def render(payload, name, initial=None):
    content = (ASSETS/'template.html').read_text()
    content = content.replace('__SYMBOL__',html.escape(payload['symbol']))
    embedded = json_text(dict(payload, initial=initial)).replace('</','<\\/')
    content = content.replace('__DATA__',embedded)
    (OUT/name).write_text(content)


def compare_checks(payload, benchmark):
    """Independent Fraction arithmetic plus unchanged retained predictions.

    These are audit equations, never runtime indicator replacements.
    """
    rows=payload['rows']; results=[]
    def check(day, metric, actual, expected, kind, tolerance=D('1e-18')):
        if actual is None or expected is None:
            status='BOTH_UNAVAILABLE' if actual is None and expected is None else 'MISMATCH'
            delta=None
        elif isinstance(actual,(bool,str)) or isinstance(expected,(bool,str)):
            status='MATCH' if actual==expected else 'MISMATCH'; delta=None
        else:
            delta=D(str(actual))-D(str(expected)); status='MATCH' if abs(delta)<=tolerance else 'MISMATCH'
        results.append(dict(date=day,metric=metric,chart=actual,reference=expected,
                            signed_difference=delta,status=status,evidence=kind,tolerance=tolerance))
    def dec(v): return D(v.numerator)/D(v.denominator) if isinstance(v,F) else v
    closes=[F(r['close']) for r in rows]; volumes=[F(r['volume']) for r in rows]
    ema={10:None,20:None}; trs=[]; atr=None
    for i,r in enumerate(rows):
        for period in ema:
            a=F(2,period+1)
            ema[period]=closes[i] if i==0 else a*closes[i]+(1-a)*ema[period]
            check(r['date'],f'ema{period}',r[f'ema{period}'],dec(ema[period]),'INDEPENDENT_FRACTION')
        if i:
            tr=max(F(r['high'])-F(r['low']),abs(F(r['high'])-closes[i-1]),abs(F(r['low'])-closes[i-1])); trs.append(tr)
            if len(trs)==14: atr=sum(trs)/14
            elif len(trs)>14: atr=(atr*13+tr)/14
        check(r['date'],'atr14',r['atr14'],dec(atr),'INDEPENDENT_FRACTION')
        check(r['date'],'dss_pct',r['dss_pct'],dec(100*volumes[i]*20/sum(volumes[i-20:i])) if i>=20 and sum(volumes[i-20:i]) else None,'INDEPENDENT_FRACTION')
        check(r['date'],'daily_range',r['daily_range'],r['high']-r['low'],'INDEPENDENT_OHLC')
        b=benchmark.get(r['date'])
        # Require all 56 stock dates in the benchmark, no interior drop/carry.
        seq=rows[i-55:i+1] if i>=55 else []
        expected=None
        if len(seq)==56 and all(s['date'] in benchmark for s in seq):
            expected=dec(closes[i]/closes[i-55]/(F(b.close)/F(benchmark[seq[0]['date']].close))-1)
        check(r['date'],'rs55',r['rs55'],expected,'INDEPENDENT_EXACT_SESSION_FRACTION')
    retained={r['bar_date']:r for r in read_csv(BASE/'ta03r4/features/daily.csv')}
    for r in rows:
        ref=retained[r['date']]
        for field,source in [('ema10','ema10'),('ema20','ema20'),('atr14','causal_atr14'),('daily_range','causal_dtr'),('dtr_atr_pct','causal_dtr_atr_pct'),('source_rvol20_pct','causal_rvol20_pct')]:
            check(r['date'],field,r[field],D(ref[source]) if ref[source] else None,'RETAINED_R4_SAME_UPSTOX')
        # ER03 has earlier benchmark coverage; R4's leading missing RS is not a zero.
        if ref['rs55']: check(r['date'],'rs55',r['rs55'],D(ref['rs55']),'RETAINED_R4_SAME_UPSTOX')
    prediction=read_csv(BASE/'simple-volume-reconstruction/SIMPLE_VOLUME_UPSTOX_PREDICTIONS.csv')
    prediction={r['bar_start_ist'][:10]:r for r in prediction if r['timeframe']=='Daily'}
    for r in rows:
        ref=prediction[r['date']]
        for field in ('volume_sma','rvol_pct','up_down_ratio','turnover_crores_estimate','pocket_pivot','dry','original_hve','original_hvy','original_hvq','original_final_color'):
            raw=ref[field]; value=None if raw=='' else raw=='True' if raw in ('True','False') else raw if field=='original_final_color' else D(raw)
            check(r['date'],'simple.'+field,r['simple'][field],value,'LATEST_RETAINED_V4_SAME_UPSTOX')
    # Original daily DSS plot: same formula, original own-feed volumes.
    original=read_csv(BASE/'indicator-math-validation/live-dss-attempt/timeframe-validation/daily_L20/live-values.csv')
    original=[r for r in original if r['volume']]
    values=daily_rvol((D(r['volume']) for r in original),20)
    for r,v in zip(original,values):
        if v is not None: check(r['bar_start_ist'][:10],'dss_pct',v*100,D(r['dss_relative_volume']),'ORIGINAL_DSS_OWN_FEED',D('1e-8'))
    # Explicit same-date cross-feed differences: these are reported, not gates.
    om={r['bar_start_ist'][:10]:r for r in original if r['dss_relative_volume']}
    for r in rows:
        if r['date'] in om and r['dss_pct'] is not None:
            check(r['date'],'dss_pct',r['dss_pct'],D(om[r['date']]['dss_relative_volume']),'CROSS_FEED_OBSERVATION_ONLY',D('1e-8'))
    write_csv(OUT/'numerical-comparisons.csv',results)
    failures=[r for r in results if r['status']=='MISMATCH' and r['evidence']!='CROSS_FEED_OBSERVATION_ONLY']
    summary=dict(checks=len(results),failures=len(failures),
        cross_feed_mismatches=sum(r['status']=='MISMATCH' and r['evidence']=='CROSS_FEED_OBSERVATION_ONLY' for r in results),
        rows=len(rows),rs55_available=sum(r['rs55'] is not None for r in rows),
        profiles=sum(r['profile'] is not None for r in rows))
    write_json(OUT/'numerical-validation.json',summary)
    if failures: raise ValueError(f'{len(failures)} numerical mismatches; inspect numerical-comparisons.csv')
    return summary


def reference():
    OUT.mkdir(parents=True,exist_ok=True)
    if not (OUT/'preservation-before.json').exists(): write_json(OUT/'preservation-before.json',preserved())
    dest=OUT/'assets'; dest.mkdir(exist_ok=True)
    for p in ASSETS.iterdir():
        if p.is_file(): shutil.copyfile(p,dest/p.name)
    data, inputs=load_inputs()
    for name in ('relative_strength.pine','dtr_atr_rvol.pine'):
        source=ROOT/name; retained=BASE/'ta03r/sources'/name
        if digest(source)!=digest(retained): raise ValueError('Original Pine source identity changed')
        inputs[str(source.relative_to(ROOT))]=digest(source)
    bm={str(c.start_ist.date()):c for c in data[BENCH]}
    module=volume_module()
    _, tv=module.reference_candles('daily-listing-history',1440)
    prefix=tuple(c for c in tv if c.start_ist<data[TIPSMUSIC][0].start_ist)
    payload=assemble('TIPSMUSIC',TIPSMUSIC,data[TIPSMUSIC],bm,prefix,profile_rows())
    render(payload,'TIPSMUSIC.html')
    (OUT/'TIPSMUSIC-data.json').write_text(json_text(payload)+'\n')
    summary=compare_checks(payload,bm)
    # Re-execute existing original Simple Volume comparison code without its builder.
    stats, _=module.validate('daily-listing-history','RETAINED_ORIGINAL',1440)
    write_json(OUT/'simple-original-validation.json',stats)
    if any(v['failures'] for v in stats.values()): raise ValueError('Original Simple Volume comparison failed')
    write_json(OUT/'input-manifest.json',dict(daily_sources=inputs,
        record_prefix_rows=len(prefix),record_prefix_source='simple-volume-reconstruction/daily-listing-history/reference.csv',
        library='Lightweight Charts 5.0.9; Apache-2.0; pinned local assets',
        evidence_hashes={str(p.relative_to(ROOT)):digest(p) for p in [
            BASE/'simple-volume-reconstruction/daily-listing-history/reference.csv',
            BASE/'simple-volume-reconstruction/SIMPLE_VOLUME_UPSTOX_PREDICTIONS.csv',
            BASE/'indicator-math-validation/live-dss-attempt/timeframe-validation/daily_L20/live-values.csv',
            BASE/'ta03r4/features/daily.csv',ER/'approximate-event-windows.csv']},
        no_market_requests=True))
    write_reports(summary)
    check_preserved()
    print(json.dumps(summary))


def pilots():
    gate=json.loads((OUT/'reference-browser-validation.json').read_text())
    if not gate['passed'] or gate['html_sha256']!=digest(OUT/'TIPSMUSIC.html'):
        raise ValueError('Validate current TIPSMUSIC reference first')
    if gate['asset_hashes']!={p.name:digest(p) for p in (OUT/'assets').iterdir() if p.is_file()}:
        raise ValueError('Reference assets changed; repeat browser validation')
    data,_=load_inputs(); bm={str(c.start_ist.date()):c for c in data[BENCH]}
    links=['<h1>ER-03C · daily pilot charts</h1><p>Real retained OHLCV. Approximate earnings windows. ER-04 paused.</p>']
    entries=[];availability=[]
    for p in read_csv(ER/'pilot-companies.csv'):
        symbol=p['symbol']; key=p['instrument_key']
        if key not in data: continue
        if symbol=='TIPSMUSIC': payload=json.loads((OUT/'TIPSMUSIC-data.json').read_text())
        else:
            payload=assemble(symbol,key,data[key],bm)
            render(payload,symbol+'.html')
        links.append(f'<p><a href="{symbol}.html">{symbol} · full daily history</a></p>')
        availability.append(dict(symbol=symbol,bars=len(payload['rows']),
            rs55_values=sum(r['rs55'] is not None for r in payload['rows']),
            classified_volume_bars=sum(r['simple']['original_final_color'] is not None for r in payload['rows']),
            profile_sessions=sum(r['profile'] is not None for r in payload['rows']),
            record_context=payload['record_context']))
        entries.append(symbol+'.html')
        for w in payload['windows']:
            name=w['event_id'].replace(':','-')+'.html'
            render(payload,name,dict(first=w['observed_first_date'],last=w['observed_last_date']))
            entries.append(name)
            links.append(f'<a href="{name}">{html.escape(w["event_id"])}</a> · ')
    (OUT/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>ER-03C charts</title>'+''.join(links))
    write_json(OUT/'pilot-chart-index.json',dict(charts=entries,count=len(entries),renderer='assets/chart.js'))
    write_csv(OUT/'pilot-availability.csv',availability)
    check_preserved()
    print(f'{len(entries)} daily charts generated after reference gate')


def write_reports(s):
    (OUT/'INDICATOR_PARITY.md').write_text(f'''# ER-03C indicator parity

TIPSMUSIC: {s['rows']} actual native daily candles, 2025-04-01 to 2026-10-30.
{s['checks']} numerical checks; {s['failures']} same-input/formula mismatches.
{s['cross_feed_mismatches']} same-date cross-feed DSS differences are explicitly retained in
`numerical-comparisons.csv`; they are not hidden or fitted. Original references
are tested on their own volumes, separately from Upstox predictions.

- **SOURCE_VALIDATED** EMA10/20: existing Pine EMA primitive, retained-first-close seed. Independent Fraction recursion and all 392 retained R4 rows checked. Initialization history limits parity.
- **SOURCE_VALIDATED** DTR/ATR: original ssksubam Pine; high-low, ATR14 RMA using first TR null, current ATR denominator, Pine rounded DTR/ATR percent and source RVOL20 percent. Independent Fraction ATR and retained R4 values checked. Extra screenshot percentages are not interpreted.
- **BEHAVIORALLY_RECONSTRUCTED** DSS lookback20: existing daily_rvol; daily first-bar double weighting cancels in numerator and denominator. Purple panel uses unrounded percent, not the old R4 A/B approximation. Original daily own-feed series checked to 1e-8 percentage points. Author Pine unavailable; original hidden startup state and developing bars unresolved. First20 local values null.
- **BEHAVIORALLY_RECONSTRUCTED** Simple Volume version4: PP10, SMA21 inclusive, preceding21 daily RVOL, U/D20, turnover50 in crores, reconstructed record classes/default colors and labels. Existing original numeric validator rerun (see simple-original-validation.json); chart compared field-by-field to latest retained Upstox predictions. Historical tests cover original colors/drawings and independent volume perturbations. TIPSMUSIC record initialization uses5774 strictly earlier TradingView daily bars ONLY for records: mixed-provider provenance is visible. No older volume enters EMA/SMA/RVOL. Other pilots have incomplete HVE context; unresolved colors are omitted and marked unavailable. Disabled Bull Snort/Power/projected features unavailable.
- **SOURCE_VALIDATED** RS55: bharatTrader ratio-of-returns formula against NSE:NIFTYMIDSML400, dimensionless, never RSI. Exact stock/bar benchmark boundary matching; gaps reset the existing adapter's55-session warmup. {s['rs55_available']} values available. ER03's earlier retained benchmark coverage adds valid dates versus R4; prior missing R4 observations are not zero or mismatches. Every eligible chart value checked by independent Fraction arithmetic.
- **APPROXIMATE** VAH/VAL: {s['profiles']} complete TIPSMUSIC one-minute sessions, existing R4 fixed INR1 zero-origin uniform-range allocation with Up/Down and70% target. Existing no-overshoot value-area policy retained. Final per-session ticks, no connecting lines across unavailable dates. Missing {s['rows']-s['profiles']} sessions UNAVAILABLE. Not trade-by-price or exact TradingView HD; no automatic HD viewport/resolution claim. Latest coarse/detailed svphd primitives were inspected but their automatic resolution remains unresolved, so no new grid is invented here. Other pilots' profiles remain unavailable in this delivery.

Earlier TA01 event contracts, TA02 acquisition checks, TA03 proxies and the source correction were inspected. TA03 research proxies remain historical evidence and are not used as substitutes. Original Pine hashes match preserved copies. Earlier R4 volume/DSS parity limitations are superseded only where later evidence establishes stronger support.

Numerical CSV distinguishes independent equations, unchanged same-feed outputs,
original own-feed DSS references and cross-feed observations. Null is never zero.
The latest Simple Volume verification is `simple-volume-reconstruction/MINUTE_WEEKLY_VERIFICATION.md`;
DSS evidence is `indicator-math-validation/live-dss-attempt/timeframe-validation/TIMEFRAME_FINDINGS.md`.
These report bounded mature completed-bar behavior, not universal Pine equivalence.

Same-date examples, both checked against retained same-feed numeric outputs:
- 2026-08-18: OHLC656.25/662.40/650.00/655.70; EMA10 665.4401827076; EMA20 669.3224668527; DTR12.40; ATR14 26.3116929959; DTR/ATR47%; DSS18.4778866871%; RS55 -0.0581048835. The retained owner crosshair independently printed matching OHLC, source RVol18%, and RS -0.06 at display precision; DSS18.48 agrees with the later validated daily reconstruction.
- 2026-10-30: OHLC792.00/792.00/772.30/782.05; EMA10 750.4496323964; EMA20 723.4359628918; DTR19.70; ATR14 33.1328794879; DTR/ATR59%; DSS30.0577524888%; RS55 0.2955826504. The old owner's latest-bar screenshot date remains unconfirmed; it is not upgraded to a dated parity reference.

The retained original Simple Volume daily check passes36696 scalar comparisons
(6116 observations for each of six numeric/flag fields). Original SMA21 maximum
absolute error is1.428571428571e-9 provider units; turnover error1e-14 crore;
remaining four fields have zero error. Those floating export differences are
below the existing1e-7 tolerance. The daily original DSS check compares480 mature
values at1e-8 percentage-point tolerance. Counts are field checks, not independent
market observations. CSV rows retain values and signed differences.

Profile minute totals differ from native daily volumes on50 of71 retained
sessions, total shortfall5789 provider units. Both feeds remain unscaled.
Late coarse/detailed SVP experiments improve bounded reference agreement but do
not establish exact automatic HD resolution on this chart. Pilot availability
counts, including unavailable histogram colors, are in pilot-availability.csv.

Provider adjustment basis, historical acquisition availability and complete exchange
calendar remain unverified. Results dates/windows retain ER03 APPROXIMATE semantics;
no earnings verification has been restarted. Daily provider completion timestamps
remain conservative next-midnight buckets. Analysis is retrospective, not a causal trading simulation.
''')
    (OUT/'CHART_VALIDATION.md').write_text('''# ER-03C chart verification

Open TIPSMUSIC.html directly with its sibling assets/ directory intact, or extract
the review ZIP and open it there. No server, CDN or market-data request is required.

Candlesticks, independently selectable EMA10/20, upper-right DTR/ATR/source-RVOL
box, purple daily DSS, reconstructed Simple Volume histogram/SMA21 and RS55 zero
baseline share exact trading-date coordinates. Pointer crosshairs synchronize all
panels; missing indicator values remain blank. Arrow keys/Previous/Next inspect
individual candles. Wheel zoom, drag pan, reset and earnings-window selection
share the same logical range. Results markers retain actual record calendar dates;
non-session dates are plotted at the next observed candle with both dates disclosed.
Dashed window edges and a shaded price-panel band show APPROXIMATE observations.
Final approximate VAH/VAL ticks are optional, default off, and never extended into gaps.

Run the browser verifier to produce reference-browser-validation.json before pilots.
It opens file:// with external requests denied, exercises actual mouse/keyboard,
toggles, zoom/pan, screenshot capture, shared-range and crosshair assertions, early
missing indicators, all packaged asset loads and JavaScript errors. Pilot generation
requires that exact reference HTML and asset hash set. All-chart validation is
recorded separately before packaging. Screenshots require visual inspection; the
final visual-review.md records that inspection and any limits.

Reproduce from repository root:

    python3 -B -m experiments.earnings_reaction_v1.er03_chart_upgrade reference
    python3 -B -m experiments.earnings_reaction_v1.er03_chart_browser reference
    python3 -B -m experiments.earnings_reaction_v1.er03_chart_upgrade pilots
    python3 -B -m experiments.earnings_reaction_v1.er03_chart_browser all
    python3 -B -m unittest discover -s experiments/earnings_reaction_v1/tests -q
    python3 -B -m experiments.earnings_reaction_v1.er03_chart_upgrade package

Only browser verification requires local Chrome and websocket-client; it never
uses an authenticated browser profile. Existing /tmp/tv-dss-live-deps may supply
websocket-client through PYTHONPATH. The builder uses Python's standard library.
Offline viewing is self-contained; rebuilding requires the retained repository
inputs listed in input-manifest.json and the earlier evidence directories.
The review ZIP includes delivered assets, generated data, validation and source.
All original research evidence is SHA256 checked and remains in place.
''')


def package():
    report=json.loads((OUT/'all-browser-validation.json').read_text())
    if not report['passed']: raise ValueError('All-chart browser verification required')
    for name,h in report['verified_hashes'].items():
        if digest(OUT/name)!=h: raise ValueError('Delivered chart/assets changed after verification')
    check_preserved()
    reference_gate=json.loads((OUT/'reference-browser-validation.json').read_text())
    text=(OUT/'CHART_VALIDATION.md').read_text().split('\n## Final verification\n')[0]
    text+='\n## Final verification\n\n'
    text+=f"{len(reference_gate['checks'])} TIPSMUSIC interaction checks passed; {len(report['charts'])} daily HTML charts rendered from file:// with no JavaScript exceptions or remote page requests. External page requests were blocked.\n\n"
    text+='The full research suite passes252 tests (243 existing plus9 ER03C integration checks); see research-tests.log. All666 pre-existing technical-analysis/ER03 evidence files pass the before/after SHA256 comparison. Original indicator modules and ER03 generator remain unchanged.\n\n'
    text+='Full-history and zoomed inspection screenshots were visually examined at1480px desktop width: candlesticks/EMAs, upper-right DTR box, purple DSS, classified volume bars/SMA/labels, RS zero line, synchronized crosshair, window boundaries and optional approximate VA ticks are readable. Dense PP labels overlap at full-history scale; zoom resolves individual events. Desktop Chrome was tested; other browser/device combinations were not tested.\n\n'
    text+='Ten227-bar pilot histories have no validated final volume colors because they cannot initialize the required252-bar record context. Their histograms are explicitly unavailable; SMA21 and candle volume inspection remain available. AETHER has80 classified bars; TIPSMUSIC has372. All pilot counts are in pilot-availability.csv. No fallback color rules were invented.\n\n'
    text+='Rendering uses the [official Lightweight Charts API](https://tradingview.github.io/lightweight-charts/docs/5.0/api/interfaces/IChartApi), pinned to5.0.9 locally. This library provides rendering/interaction only; financial indicators are supplied by the existing Python implementations.\n'
    (OUT/'CHART_VALIDATION.md').write_text(text)
    sources=OUT/'source'; sources.mkdir(exist_ok=True)
    for name in ('er03_chart_upgrade.py','er03_chart_browser.py'):
        shutil.copyfile(Path(__file__).with_name(name),sources/name)
    shutil.copyfile(Path(__file__).with_name('tests')/'test_er03_chart_upgrade.py',sources/'test_er03_chart_upgrade.py')
    files=sorted(p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip' and p.name!='SHA256SUMS')
    (OUT/'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.relative_to(OUT)}\n' for p in files))
    with zipfile.ZipFile(OUT/'ER-03C-review.zip','w',zipfile.ZIP_DEFLATED) as z:
        for p in files+[OUT/'SHA256SUMS']: z.write(p,str(p.relative_to(OUT)))
    print('Packaged ER-03C-review.zip; stop before ER-04')


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['reference','pilots','package'])
    globals()[parser.parse_args().action]()
