"""Human review, source limits and compact reproducible SS-INDEX-01 package."""
from pathlib import Path
import json
import shutil
import zipfile
from decimal import Decimal as D
from experiments.earnings_reaction_v1.er03 import ROOT,BASE,read_csv,write_json,digest
from experiments.earnings_reaction_v1 import er03_chart_upgrade as chart
from .sources import OUT,INPUT,MEMBERS,CUTOFFS,NAMES
from .build import preserve

def methodology():
    (OUT/'METHODOLOGY.md').write_text('''# SS-INDEX-01 — historical closing-price subset indices

## Membership and formation

This is a bounded reconstruction of the existing ER04 pilot and SS03B researched peers, not a new classification universe. SS01–SS04 exports, SS03B reviews, the current major universe, ER03 peer indices and ER03C renderer are retained. Original tutor row IDs, labels, source hashes and SS04 statuses remain separate from independent business decisions in HISTORICAL_CONSTITUENTS.csv and PRIOR_PEER_REVIEWS_AND_TUTOR_PROVENANCE.csv. No original classification or universe flag is overwritten.

Actual calculated fixed subsets:

- TYRES: CEATLTD, MRF, TOLINS. GRPLTD is a rubber/nylon recycler and is excluded. Tolins manufactures tyres and retreading products; its product mix and size differ from CEAT/MRF. **Tolins is an existing ER04 pilot outside the current SS04 major universe.** This is an explicit retained-pilot exception, not a claim of major-universe eligibility at either historical cutoff.
- EMS: SYRMA, CENTUM, PGEL. This is an **extended diversified contract-manufacturing exposure subset**. Centum has operational EMS plus defence/BTS/engineering services; PGEL has appliance ODM/OEM and plastic moulding. Current Screener categories differ across these businesses. SS03B's narrower core candidates are Syrma, Dixon, Avalon and Kaynes, but only Syrma has retained prices. The pure-core benchmark and pure-core leave-one-out benchmarks remain insufficient. Do not interpret this extended series as a certified pure EMS index.
- TRANSFORMERS: TARIL, VOLTAMP, SHILCTECH. These manufacture complete transformers. Bushings-only suppliers, unrelated electrical equipment, distributors and contractors are not inferred to be transformer manufacturers.

Both formation cutoffs are preserved: 31 March and 30 June 2026. Actual pre/post-cutoff NSE observations support historical listing; issuer business reports support the activity. SHILCTECH began NSE trading on 24 November 2025, before both cutoffs; BSE history is not spliced. March 31 has no retained market observation: last observed pre-cutoff session is March 30. Each formation set stays fixed through its own event follow-up; there is no mid-window constituent addition, removal or quarterly splice.

Selection and research occurred after the cutoffs. The major universe itself is a later snapshot, and business-source publication chronology is not comprehensively archived. All indices are **RETROSPECTIVE_DESCRIPTIVE**, never POINT_IN_TIME_VERIFIED. The current Screener market pages cross-check current groups only; their prices, market capitalisations and membership do not supply historical formation inputs. Other previously researched peers remain missing-data candidates in BASKET_SCOPE_AND_COVERAGE.csv and the unexecuted request manifest. The three-member denominator always means this named subset, not all sector companies.

## Daily weighting and scale

Use common retained benchmark sessions from 1 December 2025 to 30 October 2026. For N fixed members, each daily return has weight 1/N:

    R_i,t = adjusted_close_i,t / adjusted_close_i,t-1 - 1
    R_sector,t = sum(R_i,t) / N
    I_0 = 1000
    I_t = I_t-1 × (1 + R_sector,t)

The mathematical portfolio resets equal weights at every previous session close. Holdings therefore change daily; membership does not. This differs from ER03's initial-equal-weight, drifting-holdings formula. No raw share prices are averaged. Rebalancing transaction costs and execution are not modelled; this is a descriptive index, not an executable strategy.

The common-history base is 1,000 on December 1. Rows up to each formation cutoff are explicitly PRE_FORMATION_MEMBERSHIP_BACKCAST_WARMUP, used to initialize EMA/RS before the earnings study. They are not a claim that these constituents were selected at December 1. The same actual membership at both cutoffs produces identical mathematical levels in this pilot; separate formation identifiers preserve membership provenance and prevent implicit regime switching.

Only a closing-price index is calculated. **No composite open/high/low or composite candlestick is supplied.** Closing index charts use the existing renderer's LineSeries adapter. Stock charts retain their actual OHLC candlesticks.

## Corporate actions and units

Raw observations, original provider timestamps, source files and SHA256s are retained. Ordinary cash dividends are left as ex-dividend price moves: no reinvestment, so these are price indices, not total-return indices.

For a confirmed unadjusted split/bonus, the engine multiplies the cumulative share-unit factor on the ex-date and later dates. Adjusted close = raw close × factor; comparable volume = raw volume / factor. This makes a 2-for-1 split neutral without rewriting earlier prices using future information. An unknown provider basis blocks the adjustment to prevent double adjustment. Unresolved rights, demergers or special capital distributions also block the series until an independently supported reference is supplied. Multiple actions on one day require explicit review rather than an inferred combination.

Reviewed public listed-action histories identify **no split, bonus or rights adjustment inside December 2025–October 2026 for these nine members**. Actual applied share-unit factors are therefore 1. Known PGEL July-2024 split, TARIL February-2025 bonus and SHILCTECH June-2025 bonus precede the base and are not applied again. Tolins' pre-IPO 2023 events exist in its prospectus; a listed-history aggregator's broad “no history” wording is not adopted outside the study interval.

The primary public NSE corporate-action request failed at transport and the host was stopped without retries or bypass. Most action checks therefore rely on public secondary histories corroborated where available with issuer/NSE material. This is a documented action review, **not exhaustive primary-feed certification**; the provider's general historical adjustment convention remains unattested. This limitation is exported on every index row. No adjustment factor is fitted from a price jump. Positive split/bonus, unsupported rights and unknown-basis handling are verified with isolated synthetic unit-test fixtures, never inserted into research data.

## Calendar and missing values

The 227 retained NIFTYMIDSML400 sessions form the exact common observation grid. All nine constituents have actual observations on all 227 dates. The 1 February 2026 Sunday Budget session is retained and corroborated by the provider's dated trading-session notice. No weekend-only filter removes it. This is an observed provider calendar, not independent proof that every exchange session or historical suspension was captured.

A daily index requires every fixed member's current and prior synchronized observations. Missing prices or unresolved actions produce blank returns/levels, explicit missing members and actual coverage. The cumulative level chain remains broken afterward until an explicitly separate base/series is created. No zero return, carry-forward, dropped session or daily denominator reduction is used. Broad-market RS needs all 56 aligned sessions. Indicator warmups remain blank, with eligible breadth denominators retained.

## Momentum, breadth and activity

- Returns over 5/10/20/40 observed trading sessions: 100 × (I_t / I_t-n − 1).
- RS55: (I_t / I_t-55) / (B_t / B_t-55) − 1, against the same NIFTYMIDSML400 price index used in ER04. It is dimensionless RS, not RSI. First available date is 18 February 2026.
- EMA10/20: alpha 2/(n+1), first retained close seed. Subsector EMAs use index closes; constituent breadth EMAs use the same study-grid history. Original ER04 stock-chart EMAs continue using their original longer histories; those earlier files are not changed.
- Above-EMA20 breadth: percentage of all fixed members strictly above their own EMA20 after 20 observations. Rising breadth: percentage with a strictly positive one-session comparable-close return. Ties are not advances.
- Constituent RVOL20: current comparable volume divided by the mean of the previous 20 sessions, excluding current. Volume participation breadth is the percentage with RVOL20>1; rising-and-participating breadth and median RVOL are supplied separately. These are natural reference comparisons, not optimized thresholds.
- Activity proxy: sum(raw close × raw NSE traded shares)/10,000,000, labelled INR crore and **not exact turnover** because close is not traded VWAP. Median constituent traded-value/previous-20-session-average is also supplied. Raw share volumes are never summed as a comparable sector activity measure.

## Leave-one-out and event comparisons

Each supported pilot stock is removed entirely from its benchmark. Two remaining independently priced companies are the minimum for this bounded descriptive peer comparator; each has 1/2 weight in every daily return. Two peers remain a small, unrepresentative set. Target-price perturbation tests prove that a stock cannot improve its own leave-one-out benchmark. AETHER, TIPSMUSIC and GRPLTD have explicit unsupported/out-of-scope cases; no unrelated substitute sector is supplied.

All 24 ER04 event identities and dates are reused unchanged. Eighteen events support these three groups. Checkpoints S1/S3/S5/S20/S40 are strictly after the retained result calendar date and before a known next-results date. All feature calculations use only the candle prefix through that checkpoint. Closing information is conceptual session-close information, conservatively bounded by provider bucket completion at next IST midnight; actual historical delivery time is not attested. The CSV is authoritative for early information; full interactive charts contain later bars.

New matched comparisons use **checkpoint close to Nth later close**, identically dated for stock, leave-one-out peers and NIFTYMIDSML400. Original ER04 next-open outcomes are copied into separately named columns and never subtracted from closing-index returns. Exact next-results sessions are excluded. Incomplete windows have separate partial-return columns; unknown next-results boundaries remain censored even with N observations. Summary files separate all full windows from known-next-results-only windows. Future boundary dates are outcome-isolation metadata, not predictor variables.

Two time questions are kept distinct: CONTINUATION_FADING_COMPARISON.csv uses 5/10/20/40 sessions **after** each checkpoint; FIXED_CHECKPOINT_CONTINUATION_FADING.csv compares S1/S3/S5 directly with event-session S20/S40. A positive early gain followed by a positive subsequent close return is called continuation; a nonpositive subsequent close return is fade/flat. Fixed-session trajectories additionally distinguish early gain lost from gain retained but reduced. These are disclosed descriptive sign definitions, not validated sustained-rally thresholds.

Only earlier peer return, EMA slope and RS changes form comparison groups. Later peer states in the trajectory file are explicitly AFTER_EARLY_CHECKPOINT_OUTCOME_DESCRIPTION_ONLY. No future sector change is used as an early input. This remains a small, retrospectively selected, same-company and common-market-dependent sample with overlapping windows. No causation, statistical significance, fitted threshold or predictive-accuracy claim is made.
''')

def findings():
    status=json.loads((OUT/'status.json').read_text())
    rows=read_csv(OUT/'CONTINUATION_FADING_COMPARISON.csv')
    fixed=read_csv(OUT/'FIXED_CHECKPOINT_CONTINUATION_FADING.csv')
    cases=read_csv(OUT/'EVENT_COUNTEREXAMPLES.csv')
    trajectories=read_csv(OUT/'EVENT_FIXED_CHECKPOINT_TRAJECTORIES.csv')
    lines=['# SS-INDEX-01 findings',
      'Actual daily closing-price series are built for the three named retained subsets. ER04 and SS01–SS04 outputs are preserved. No new authenticated request was made.',
      '## Series and coverage',
      'Each subset has 227 synchronized sessions, 1 December 2025–30 October 2026, base 1,000. Two formation identifiers preserve the March 31 and June 30 cutoffs. All three prices are available on every retained session. Returns, EMA10/20, RS55, constituent breadth, RVOL participation and traded-value proxies are included in the daily datasets.']
    for g,syms in MEMBERS.items():
        data=[r for r in read_csv(OUT/(g+'_DAILY_INDEX.csv')) if r['quarter']=='FY2027-Q1'];r=data[-1]
        lines.append(f"- {g}: {' / '.join(syms)}. Final close {float(r['index_close']):.4f}; 20-session return {float(r['return_20s_pct']):.2f}%; 40-session return {float(r['return_40s_pct']):.2f}%; RS55 {float(r['rs55']):.6f}.")
    lines += ['These end-of-history figures describe the final retained session; they are not early-checkpoint inputs. TYRES excludes GRP recycling, and its existing Tolins pilot is explicitly outside the current major universe. EMS is a diversified manufacturing-exposure subset, not a pure EMS benchmark; core-only coverage has one retained stock and remains insufficient. Transformer constituents manufacture complete transformers. These are neither complete-sector nor major-only market indices.',
      'Eighteen of 24 original events have two-peer leave-one-out benchmarks. All 90 supported S1/S3/S5/S20/S40 snapshots are available; the other 30 checkpoint rows explicitly preserve the six unsupported events. All 120 requested event/checkpoint combinations are retained. Peer benchmarks exclude the target stock on every date.',
      '## What accompanied continuation and fading',
      'For session 3 and the following 20 sessions, matched close-to-close comparisons show:']
    for pattern in ('peer_return20_positive','peer_rs55_deteriorating_5s','stock_rs55_deteriorating_5s'):
        for condition in ('True','False'):
            r=next(r for r in rows if r['checkpoint']=='3' and r['horizon']=='20' and r['subsector']=='ALL' and r['boundary_scope']=='ALL_FULL_WINDOWS' and r['pattern']==pattern and r['condition']==condition)
            lines.append(f"- {pattern}={condition}: {r['events']} events; {r['positive_stock_returns']} positive and {r['negative_stock_returns']} negative later stock returns. Among {r['early_rally_events']} positive early rallies, {r['continuations']} continued and {r['fades']} faded/flattened. Median stock return {float(r['median_stock_close_return_pct']):.2f}%; median stock-minus-peer return {float(r['median_stock_minus_peer_pp']):.2f}pp. {r['unknown_boundary_events']} next-results boundaries are unknown.")
    lines += ['The positive-earlier-peer group does not deliver a uniformly stronger subsequent stock outcome: its median stock return is negative, and it contains both continuation and fading. Conversely, several stocks recover after negative earlier peer momentum or deteriorating RS. These observations do not validate a rule that subsector weakness causes an earnings rally to fade.',
      'Fixed event-session comparisons answer the separate S3→S20/S40 question:']
    for later in ('20','40'):
        for condition in ('True','False'):
            r=next(r for r in fixed if r['early_checkpoint']=='3' and r['later_event_session']==later and r['subsector']=='ALL' and r['boundary_scope']=='ALL_SUPPORTED' and r['pattern']=='early_peer_return20_positive' and r['condition']==condition)
            lines.append(f"- S3→event S{later}, earlier peer 20-session return positive={condition}: {r['events']} supported events; {r['early_rallies']} early rallies, {r['continuations']} continuations and {r['fades']} fades/flat outcomes; {r['early_gain_lost']} lost the pre-results gain and {r['gain_reduced_but_retained']} retained a reduced gain. {r['unknown_boundary_events']} boundaries unknown.")
    lines += ['Known-next-results-only rows are supplied for every comparison rather than mixed silently with uncertain windows. At S3/following20 only nine events remain in that stricter subset, with just one positive early rally. That is too little evidence to compare fading frequencies responsibly. Do not add events across checkpoints, conditions, or overlapping horizons.',
      '## Concrete counterexamples',
      'Cases below use information at S3 and the following 20 closing sessions; full rows are in EVENT_COUNTEREXAMPLES.csv.']
    for event_id in ('FY2027-Q1:PGEL','FY2027-Q1:VOLTAMP','FY2026-Q4:SYRMA','FY2026-Q4:SHILCTECH'):
        r=next(r for r in cases if r['event_id']==event_id and r['checkpoint']=='3' and r['horizon']=='20')
        lines.append(f"- {event_id}, {r['checkpoint_date']}: prior 20-session LOO peer return {float(r['peer_return20_at_checkpoint_pct']):.2f}%, peer five-session RS55 change {float(r['peer_rs55_change5_at_checkpoint']):.6f}; stock's early return {float(r['early_stock_return_pct']):.2f}%. Later stock return {float(r['stock_forward_close_return_pct']):.2f}% versus peers {float(r['peer_forward_close_return_pct']):.2f}%, a {float(r['stock_minus_peer_pp']):.2f}pp difference. Next-results boundary unknown: {r['unknown_next_results_boundary']}.")
    lines += ['PGEL Q1 is the clear counterexample to a universal sector explanation: its early gain faded while both earlier and subsequent diversified EMS peer context were positive. VOLTAMP Q1 continued despite weak prior peers and declining subsequent peers. SYRMA Q4 recovered strongly after negative earlier peer momentum and declining stock RS; it was not a positive early rally, so it is labelled a recovery rather than continuation. SHILCTECH Q4 weakened despite positive earlier 20-session peer momentum, while peer RS was already deteriorating—different strength measurements can disagree.',
      '## Limits and outstanding acquisition',
      'All membership is RETROSPECTIVE_DESCRIPTIVE. Complete primary corporate-action coverage remains unverified after the official public transport failure. Public secondary action histories identify no in-window split/bonus/rights event; factors are 1, known pre-base events are not reapplied, and ordinary dividends remain in price returns. These limitations prevent calling the series fully certified historical benchmarks.',
      'The exact outstanding manifest proposes seven daily Upstox requests for previously researched missing peers: APOLLOTYRE, JKTYRE, DIXON, AVALON, KAYNES, POWERINDIA and ATLANTAELE, 1 December 2025–30 October 2026. None was executed. New data would require adjustment, listing and business eligibility review before any separate basket revision.',
      'The observed-session calendar, approximate original earnings dates, original unknown next-results dates, small/heterogeneous subsets, current-major-universe survivorship and unverified real-time provider delivery remain material limitations. Future sector changes are outcome descriptions only. No statistical significance, threshold optimization, model training, strategy or expansion beyond the three groups was attempted.',
      'STOP after SS-INDEX-01.']
    (OUT/'RESEARCH_FINDINGS.md').write_text('\n\n'.join(lines)+'\n')

def validation():
    numerical=json.loads((OUT/'numerical-validation.json').read_text())
    prefix=json.loads((OUT/'checkpoint-prefix-validation.json').read_text())
    browser=json.loads((OUT/'browser-validation.json').read_text())
    preservation=json.loads((OUT/'preservation-verification.json').read_text())
    tests=json.loads((OUT/'test-summary.json').read_text())
    (OUT/'VALIDATION_REPORT.md').write_text(f'''# SS-INDEX-01 validation

- New and unchanged ER04 regression tests: {tests['tests']} passed, zero failures. See tests.log and test-summary.json. New fixtures exercise raw-price scale invariance, daily rebalancing, split/bonus neutrality, dividend exclusion, unknown-basis and rights blocking, future-action isolation, strict missing-member handling, lagged RVOL, no aggregate share volume, stock exclusion and target perturbation, fixed event sessions and future-outcome isolation.
- Independent Fraction audit: {numerical['comparisons']:,} comparisons, {numerical['failures']} failures, tolerance 1e-18 against Decimal output. It independently checks daily equal-weight chains, EMA10/20, 5/10/20/40 returns, RS55, member RVOL, breadth and traded-value proxies for all six formation indices and 18 leave-one-out series.
- Historical prefix audit: {prefix['checks']} complete supported checkpoint snapshots rebuilt from price prefixes; {prefix['failures']} future-sensitivity failures. Every exported early feature, including benchmark close, agrees with the prefix result.
- Actual local Chrome: {browser['charts']} charts, {browser['assertions']} assertions. Closing indices are LineSeries with no O/H/L or aggregate share-volume fields; original stock OHLC candlesticks agree with actual data. Existing four-panel crosshairs, navigation and toggles work; peer context follows the selected date. No JavaScript errors or HTTP/HTTPS requests. Four desktop screenshots are retained; index and stock/peer screenshots were visually reviewed. Other devices/browsers were not tested.
- Preservation: all {preservation['files_in_original_baseline']:,} pre-existing SS01–SS04 and ER04 files have unchanged hashes. Original class assignments, mentor rows, current major flags, charts and ER04 market-context artifacts were not regenerated. The earlier ER04 preview discrepancy evidence remains untouched.
- Raw evidence: original raw provider responses are copied by SHA256, their original paths are retained, and frozen input hashes are verified before each build. No credentials are accessed by the builder. The only new external execution was one unauthenticated official corporate-action probe, which failed at transport; its ledger records no complete feed and no retries. Seven authenticated peer requests remain unexecuted.

## What these checks establish

The reported daily index arithmetic and causal use of price prefixes reproduce the actual retained inputs. All nine constituents have all 227 benchmark-observed sessions; no missing session is filled, hidden or reweighted. Each index row reports expected/observed/return member counts and price coverage; each leave-one-out series lists the exact two members. RS first becomes available after 56 aligned closes; early warmups are blank. Source daily timestamps and conservative next-midnight availability are preserved.

These checks do not establish point-in-time selection, complete historical industry membership, complete corporate-action/event/suspension coverage, exact turnover, total returns or actual intraday provider delivery. The action feed's absence is not interpreted as proof of no actions. Corporate-action factors of 1 rely on the disclosed public listed-history review; any later conflicting evidence requires a new explicit revision. Existing approximate earnings dates and unknown future-result boundaries are preserved. Both complete-window and known-boundary-only event comparisons are supplied.

Read METHODOLOGY.md for the Tolins current-major exception, extended EMS scope and the strict distinction between subsequent-N-session outcomes and event-session S20/S40. This is retrospective descriptive research, with no significance or causal claim.
''')

def readme():
    (OUT/'README.md').write_text('''# SS-INDEX-01 — three historical strength indices

Open [charts/index.html](charts/index.html) for six formation-specific index charts and eighteen stock/leave-one-out examples using the existing ER03C renderer. Start with RESEARCH_FINDINGS.md; definitions and limitations are in METHODOLOGY.md and VALIDATION_REPORT.md.

Primary data files:

- TYRES_DAILY_INDEX.csv, EMS_DAILY_INDEX.csv, TRANSFORMERS_DAILY_INDEX.csv: 227 actual closing-index observations per formation, base 1,000, daily equal weighting, return/EMA/RS/breadth/activity fields and explicit counts. Each CSV contains both fixed formation identifiers.
- HISTORICAL_CONSTITUENTS.csv, PRIOR_PEER_REVIEWS_AND_TUTOR_PROVENANCE.csv and BASKET_SCOPE_AND_COVERAGE.csv: independently reviewed fixed subsets, original mentor provenance, current-major flags and missing full-peer candidates.
- CONSTITUENT_DAILY_FEATURES.csv and SESSION_ALIGNMENT.csv: actual constituent observations, source hashes, RVOL, value proxies and synchronized calendar coverage.
- LEAVE_ONE_OUT_INDICES.csv, LEAVE_ONE_OUT_DAILY_COMPARISONS.csv and LEAVE_ONE_OUT_COVERAGE.csv: self-excluded peer returns/RS versus stock and NIFTYMIDSML400, including insufficient/out-of-scope cases.
- ER04_SUBSECTOR_CHECKPOINTS.csv and ER04_SUBSECTOR_FORWARD_COMPARISONS.csv: all 24 events at S1/S3/S5/S20/S40, supported early features and separately named matched-close and original ER04 next-open outcomes.
- CONTINUATION_FADING_COMPARISON.csv and EVENT_COUNTEREXAMPLES.csv: subsequent-horizon comparisons using early features only.
- EVENT_FIXED_CHECKPOINT_TRAJECTORIES.csv and FIXED_CHECKPOINT_CONTINUATION_FADING.csv: S1/S3/S5 directly to event-session S20/S40; future peer states explicitly outcome-only.
- inputs/: frozen source snapshots and business/action/current-Screener evidence. evidence/provider/: actual retained raw price responses. raw-provider-evidence.json maps copies to original responses.
- OUTSTANDING_ACQUISITION_MANIFEST.json and adjacent SHA256: exact seven-request proposal, unexecuted. Public corporate-action manifest/ledger record the failed unauthenticated host probe separately.

All indices are RETROSPECTIVE_DESCRIPTIVE and named retained subsets. EMS is extended manufacturing exposure, not pure-core EMS. Tolins is a pre-existing ER04 pilot outside the current major scope; its original universe flag is preserved. No complete primary action-feed certification is claimed.

## Reproduce offline

From the repository root:

    python3 -B -m experiments.subsector_index_v1.build
    python3 -B -m experiments.subsector_index_v1.verify
    python3 -B -m unittest experiments.subsector_index_v1.tests.test_indices experiments.earnings_reaction_v1.tests.test_er04
    PYTHONPATH=/tmp/tv-dss-live-deps python3 -B -m experiments.subsector_index_v1.browser
    python3 -B -m experiments.subsector_index_v1.report

The builder snapshots already retained data only on the first run; delivered inputs are frozen and reused afterward. Do not run acquire_public.py during offline reproduction. No authentication or API token is needed. Standard-library Python plus timezone data is sufficient for calculations. Browser verification additionally uses local Chrome and websocket-client, which already existed in /tmp; no new dependency was downloaded.

The compact review ZIP includes actual raw provider evidence, frozen snapshots, code/tests and local renderer assets. From its extracted root use `python3 -B -m experiments.subsector_index_v1.build --compact-review`, `python3 -B -m experiments.subsector_index_v1.verify --compact-review` and the test command above. Compact mode reports omitted original preservation-baseline files explicitly and verifies only included ones; it does not claim full SS/ER preservation without the original repository. Full-workspace preservation and browser checks are retained in this review. ZIP replay results and output hashes are in zip-reproduction-verification.json.

STOP after the three-subsector pilot. No global acquisition, 628-event expansion, ML, threshold fitting or production-system changes.
''')

def package(compact=False):
    preserve(compact)
    code=OUT/'code';code.mkdir(exist_ok=True)
    source=Path(__file__).parent
    for p in source.rglob('*.py'):
        target=code/p.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    files={p for p in OUT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.zip' and p.name!='SHA256SUMS'}
    files|={p for d in (source,ROOT/'experiments/earnings_reaction_v1') for p in d.rglob('*.py') if '__pycache__' not in p.parts}
    files|={p for p in chart.ASSETS.iterdir() if p.is_file()}
    files|={BASE/'er03/approximate-event-windows.csv',BASE/'er03/market-data-inventory.json'}
    provenance=json.loads((INPUT/'source-provenance.json').read_text())
    # Original relative source paths allow the unchanged ER04 regression test to
    # use its original candle loader. Retained copies also travel as audit evidence.
    files|={ROOT/p for p in provenance['original_provider_files']}
    missing=[str(p) for p in files if not p.exists()]
    if missing:raise ValueError('Missing package dependency: '+str(missing))
    sums=OUT/'SHA256SUMS'
    sums.write_text(''.join(f'{digest(p)}  {p.relative_to(ROOT)}\n' for p in sorted(files)))
    files.add(sums)
    archive=OUT/'SS-INDEX-01-review.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(files):z.write(p,str(p.relative_to(ROOT)))
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:raise ValueError('ZIP integrity failure')
        if any(n.endswith('.env') for n in z.namelist()):raise ValueError('Unexpected credential path')
    print(json.dumps(dict(zip=str(archive.relative_to(ROOT)),bytes=archive.stat().st_size,files=len(files),sha256=digest(archive))))

def main():
    methodology();findings();validation();readme();package('--compact-review' in __import__('sys').argv)

if __name__=='__main__':main()
