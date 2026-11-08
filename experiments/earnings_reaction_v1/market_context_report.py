"""Build the bounded ER04 review, documentation and evidence package offline."""
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal as D
import json
import shutil
import subprocess
import zipfile
from .er03 import ROOT, BASE, read_csv, write_csv, write_json, digest
from .er04 import OUT as ER04, COMPLETION, preservation
from .market_context import OUT

def acquisition_report():
    ledger=json.loads((COMPLETION/'retrieval-execution.json').read_text())
    manifest=json.loads((COMPLETION/'acquisition-manifest.json').read_text())
    rows=[]
    for wanted,actual in zip(manifest['requests'],ledger['requests']):
        assert all(actual[k]==wanted[k] for k in ('number','instrument_key','unit','interval','from_date','to_date','url'))
        assert actual['http_status']==200 and digest(ROOT/actual['retained_path'])==actual['response_sha256']
        candles=json.loads((ROOT/actual['retained_path']).read_text())['data']['candles']
        rows.append(dict(symbol=wanted['symbol'],number=actual['number'],requested_from=wanted['from_date'],requested_to=wanted['to_date'],
            required_prior_rows=wanted['minimum_rows_required'],actual_rows=actual['rows'],sufficient=actual['rows']>=wanted['minimum_rows_required'],
            actual_first=min(c[0] for c in candles),actual_last=max(c[0] for c in candles),response_sha256=actual['response_sha256'],
            retained_path=actual['retained_path'],http_status=actual['http_status'],received_at=actual['received_at'],
            missing_data_flag='NSE_LISTING_2025_11_24;NO_EARLIER_NSE_HISTORY' if wanted['symbol']=='SHILCTECH' else ''))
    write_csv(COMPLETION/'actual-acquisition-coverage.csv',rows)
    write_json(COMPLETION/'acquisition-receipt.json',dict(status='EXECUTED_EXACT_APPROVED_MANIFEST;ONE_LISTING_HISTORY_LIMIT',
        approval_reference='User ER-04 Continuation: Execute the 11 exact approved daily Upstox requests using the reviewed manifest',
        manifest_sha256=digest(COMPLETION/'acquisition-manifest.json'),authenticated_calls=11,automatic_retries=0,scope_substitutions=0,
        all_http_200=True,actual_rows=sum(r['actual_rows'] for r in rows),sufficient_responses=10,insufficient_responses=['SHILCTECH'],
        credentials_in_artifacts=False,execution_ledger='retrieval-execution.json',previous_nonexecution_receipt_superseded=True))
    (COMPLETION/'ACQUISITION_COMPLETION.md').write_text('''# ER03D approved execution and remaining history limit

All eleven exact requests in manifest `a38be1561bbdd161135c50ccfc5f2747acccdad402ee26d64b7568bef8853e29` executed once under the user's continuation instruction. HTTP 200 for every request; raw bodies, hashes, validation sidecars, verified instrument identities and receipt retained. No retry, substitution or extra authenticated acquisition.

Ten histories meet their minimum required prior-bar counts. SHILCTECH returned only the five NSE sessions from 24–28 November 2025. Its issuer's May 2026 results, page 8 note 5, confirm NSE listing/trading began 24 November 2025: https://nsearchives.nseindia.com/corporate/SHILCHARTECH_05052026171713_BM_Outcome.pdf . The absence of earlier NSE candles is therefore consistent with its listing date, rather than a request to repeat. No fabricated bars or BSE volume splice was used. At the retained cutoff it has 232 NSE bars and cannot initialize the original 252-bar class. All six SHILCTECH checkpoint colors remain unavailable.

Eleven companies have all six checkpoint colors (66/72 overall); EMA, ATR, DSS, RS55 and supported shorter-window volume fields remain available at all 72 checkpoints. Rolling 252-bar readiness does not attest full listing-history HVE identity. VAH/VAL remains limited to the pre-existing TIPSMUSIC intraday evidence.

The 72 checkpoint charts were regenerated. Thirty-three affected Phase A charts (11 companies, full histories plus two event views each) were regenerated, explicitly retaining SHILCTECH's unavailable histogram colors. Original ER03C/chart-preview evidence was not edited. Six known pre-existing preview hash discrepancies remain unrepaired; see market-context/six-preview-discrepancies-preserved.json relative to ER04.

The original reviewed manifest and historical gap proposal remain unchanged as approval evidence. Their pre-execution wording is superseded by this report, actual-acquisition-coverage.csv and acquisition-receipt.json.
''')

def sector_notes():
    pilots=read_csv(BASE/'er03/pilot-companies.csv')
    source=BASE/'../subsector-ss02/evidence/existing-business-profiles.json'
    profiles=json.loads(source.read_text());byid={p['company_id']:p for p in profiles}
    retained=[]
    for p in pilots:
        r=byid[int(p['company_id'])]
        retained.append(dict(symbol=p['symbol'],subsector=p['subsector'],business_description=r['display_text'],
            source_url=r['final_url'],source_available_at=r['available_at_utc'],source_published_at=r['source_published_at_utc'],
            cited_primary_urls=r['primary_citation_urls'],authority=r['evidence_authority'],
            point_in_time_use='NOT_ELIGIBLE;POST_EVENT_COLLECTION_AND_ORIGINAL_PUBLICATION_UNVERIFIED'))
    write_json(OUT/'retained-business-exposure-evidence.json',dict(source_file=str(source.resolve().relative_to(ROOT)),source_sha256=digest(source),companies=retained))
    hypotheses={
      'AETHER':'Specialty chemicals: exported revenue and imported inputs create opposing currency channels. The issuer FY2025 report identifies USD exposure on both sides. Depreciation may help a net USD asset/export position, but inventories, hedges and input costs prevent a stable event-time sign.',
      'TIPSMUSIC':'Music rights: global platform/distribution receipts could create overseas demand or currency exposure, but the retained business summary does not establish a net FX balance. No Nasdaq beta or exporter benefit is assigned.',
      'CEATLTD':'Tyres: crude-linked materials and imported raw materials motivate an input-cost hypothesis. CEAT’s issuer FY2024 report describes imports, exports, foreign borrowings and hedging, so a weak rupee is not automatically adverse and lower oil is not automatically a stock-price tailwind.',
      'GRPLTD':'The existing TYRES mapping is provisional. The actual retained description is reclaimed rubber, recycled nylon and end-of-life tyre products. It must not inherit the same oil-cost sign as a virgin tyre manufacturer; substitution pricing, recycled feedstock costs and exports need separate evidence.',
      'SYRMA':'EMS design/manufacturing: imported components can offset export receipts; customer pass-through and hedges matter. Technology manufacturing is not interchangeable with Indian IT services; weak Nasdaq does not establish a company-specific earnings effect.',
      'VOLTAMP':'Oil-filled power/distribution transformers: transformer oil is a potential input channel, but copper, steel, project pricing and order execution can dominate. Net imports/exports and price adjustment clauses are not attested here.',
      'CENTUM':'Mission-critical electronics across defence, aerospace and industrial customers: geography, contracts, imported components and subsidiary translation need documented net exposures. No generic exporter/technology sign is assigned.',
      'MRF':'Tyre manufacturing: investigate rubber, crude-linked synthetic inputs and import costs jointly. The CEAT risk mechanism is a sector hypothesis, not issuer-specific proof of MRF’s exposures or pass-through.',
      'SHILCTECH':'Power, distribution and electronic transformers: overseas sales and input currencies need netting and hedging evidence. A provisional transformer mapping and the NSE listing limit do not justify any automatic macro-favorability label.',
      'TOLINS':'Tyres and precured tread rubber: examine crude-linked materials, natural rubber and replacement demand separately. The retained description alone cannot quantify the net oil/currency sensitivity.',
      'TARIL':'Power, furnace and rectifier transformers: domestic investment cycles, metal inputs and project terms may outweigh crude. No direct upstream oil benefit or verified net USD exposure is established.',
      'PGEL':'EMS/ODM/OEM plus plastic injection moulding: crude-linked resin costs and imported electronics are plausible cost channels, offset by customer contracts and any exports. Net currency/oil favorability is unresolved.'}
    lines=['# Sector sensitivity — hypotheses and documented limits',
      'The existing 12-company mappings are reused with their verified/provisional flags. Retained business descriptions were mostly collected in October 2026 (Tolins in August); original publication times are unverified. They support retrospective business interpretation only and are never backdated into April–August checkpoint features. Each retained description, collection time, citation and hash is saved in retained-business-exposure-evidence.json.',
      'No company in this pilot is documented here as an upstream oil producer. No company is assigned an IT-services exposure merely because it manufactures electronics. Net event-time FX, hedges, inventory lags, input contracts and pricing power are not inferred from a subsector name.']
    for p in pilots:lines.append(f"## {p['symbol']} — {p['subsector']} ({p['classification_status']})\n\n{hypotheses[p['symbol']]}")
    lines += ['## Primary evidence checked',
      'Aether issuer FY2025 annual report, Management Discussion and foreign-exchange risk note: export receipts and raw-material expenditure include foreign currencies. Both directions are relevant; the prior-year balance is not an event-time hedge book. https://aether.co.in/wp-content/uploads/2025/08/Annual%20Report%20FY%202024-25.pdf',
      'CEAT issuer-group FY2024 annual report, commodity and foreign-exchange risk section: procurement includes international materials; imports, exports and foreign borrowings are managed with hedging. This is evidence of channels, not a constant stock-return sign. https://www.rpggroup.com/assets/investors/pdfs/ceat-annual-report-fy241743584614.pdf',
      'Shilchar May-2026 issuer results, page 8 note 5, confirms NSE trading began 24 November 2025; the same filing describes its transformer segment. https://nsearchives.nseindia.com/corporate/SHILCHARTECH_05052026171713_BM_Outcome.pdf',
      'The source PDFs above were inspected through public web research; short paraphrased source findings and URLs are retained here. The existing source business descriptions remain separately hashed. No newly searched exposure claim is treated as a checkpoint feature.',
      '## What was tested',
      'Macro sign comparisons against actual forward returns are in MACRO_CONTINUATION_COMPARISON.csv. A supplementary grouping uses both EMAs held, improving pre-results RS and an up-close with DSS>100%; these natural reference levels reuse ER04 definitions and are not optimized. That combined filter exists only for close-time decisions. At S3/20 sessions it contains just one event with Nifty coverage, so a stable combined-signal association cannot be estimated.',
      'Subsector strengthening and business-favorable oil/FX remain untested: historical point-in-time peer membership, timestamped oil values and net event-time business exposures are missing. Current retrospective baskets are not backdated. The two Aether events do not constitute a meaningful exporter subgroup test. The raw macro moves can be inspected, but no universal “oil down good” or “rupee down exporter good” indicator is fabricated.']
    (OUT/'SECTOR_SENSITIVITY_NOTES.md').write_text('\n\n'.join(lines)+'\n')

def findings():
    rows=read_csv(OUT/'MACRO_CONTINUATION_COMPARISON.csv')
    def selected(phase,s,sign):
        return next(r for r in rows if r['phase']==phase and r['series']==s and r['checkpoint']=='3' and r['horizon']=='20' and r['macro_horizon']=='1' and r['stock_signal']=='ALL' and r['macro_change_sign']==sign)
    lines=['# ER04 global market context — bounded findings',
      'Scope: the existing 12 companies, 24 earnings events, 72 session-1/3/5 checkpoints and unchanged 5/10/20/40-session outcome definitions. No cohort expansion, strategy, fitted thresholds, model training or predictive-accuracy claim.',
      '## Acquisition and coverage',
      'All 11 exact authorized Upstox requests executed once and returned HTTP 200. Ten provide required warmup; SHILCTECH supplied only its five first NSE sessions. Its issuer filing confirms NSE trading started 24 November 2025. That venue cannot supply the missing earlier NSE volume. Final Simple Volume colors cover 66/72 checkpoints (11/12 companies); all 72 have RS55 and the existing shorter-history indicators. HVE full-history certainty and non-TIPSMUSIC profiles remain unresolved.',
      'Recovered public data: 187 Brent and 186 WTI nonmissing daily spot observations through 27 October 2026; 189 current-vintage USD/INR observations through 30 October; 179 unrevised daily currency observations from 37 dated H.10 weekly releases. Reused 279 Nifty 50 candles from 1 July 2025 through 14 August 2026. Original response timestamps, dates, units, URLs, hashes and retrieval timestamps are retained.',
      '## Availability at an Indian checkpoint',
      'PREOPEN_CONTEXT.csv uses 09:15 Asia/Kolkata exclusively. H.10 context uses the latest dated release proven available under a conservative bound, not the latest live FX rate. Noon New York is the official observation convention. Actual publication minutes are unavailable, so available_at is the end of the dated New York release day, an upper bound rather than an invented publication timestamp. The official 16:15 ET schedule is retained separately and never used as an actual receipt. In summer that upper bound is 09:30 IST next day, after the pre-open cutoff, so Tuesday pre-open conservatively retains the older release. All 72 pre-opens have delayed dated-release context, with exact timing uncertainty explicitly flagged.',
      'Nifty prior completed sessions are conservatively admitted after the retained daily bucket completes at next midnight IST. Exact previous Indian session matching prevents stale Nifty carry-forward after coverage ends. Coverage: 67/72 pre-open checkpoints, 66/72 conceptual close checkpoints. BY_CLOSE is a separate 15:30 IST conceptual market snapshot; it is not proof that Upstox delivered a historical candle at 15:30. Same-day closing stock signals never enter pre-open features.',
      'The most recent expected US session is determined using NYSE holidays, weekends, early closes and America/New_York DST. These calendar timestamps are explicitly not recovered US index observations. S&P 500, Nasdaq Composite and Dow histories were not downloaded for retention because the inspected sources identify pre-approval/licensing restrictions. No historical pre-open ES/NQ/GIFT quote archive is retained. No settlements replace futures snapshots.',
      'Oil current-vintage data lack an historical publication timestamp and exact intraday observation time. They are retained for retrospective daily changes and separate later-outcome descriptions, but are blank in checkpoint inputs. No unknown publication lag is invented. Live FX, pre-open overnight changes, India VIX and GIFT Nifty remain unavailable. The public NSE transport failed; no cookies, proxies, authentication or access-control bypass was attempted.',
      '## Descriptive comparisons at session 3, next-open forward 20 sessions',
      '“Continuation” in this extension is an event already above its pre-results close at the checkpoint and with a positive next-open forward return. “Fade” is an early rally followed by a nonpositive next-open forward return. These are descriptive sign definitions, not validated sustained-rally thresholds. Relative outperformance is separately measured against the existing Nifty MidSmall 400 benchmark, not Nifty 50.']
    for phase,s,signs in [('PREOPEN','NIFTY50',('POSITIVE','NEGATIVE')),('BY_CLOSE','NIFTY50',('POSITIVE','NEGATIVE')),('PREOPEN','USDINR',('POSITIVE','NEGATIVE','ZERO'))]:
        for sign in signs:
            r=selected(phase,s,sign)
            meaning=('rupee depreciation' if sign=='POSITIVE' else 'rupee appreciation' if sign=='NEGATIVE' else 'unchanged rate') if s=='USDINR' else ('Nifty strengthening' if sign=='POSITIVE' else 'Nifty weakening')
            lines.append(f"- {phase}, {meaning}: {r['events']} events; {r['positive_absolute_returns']} positive and {r['negative_absolute_returns']} negative forward returns; {r['outperformers']} outperformed and {r['underperformers']} underperformed. Median absolute {float(r['median_return_pct']):.2f}%, relative {float(r['median_relative_return_pp']):.2f}pp. Among {r['early_rally_events']} early rallies, {r['rally_continuations']} continued and {r['rally_fades']} faded. Unknown next-results boundaries: {r['unknown_next_results_boundaries']}.")
    lines += ['These rows describe associated conditions, not independent experiments. The apparent stronger close-time Nifty split is not a pre-open result. Currency groups describe the direction of the latest released historical noon observation; they do not measure that morning’s rupee move. None of these counts establishes causation or reliable predictive accuracy.',
      '## Counterexamples',
      'At S3, AETHER Q1 rallied 3.23% from its pre-results close even though the prior Nifty session fell 0.64%. Its next-open forward20 return was +4.20%, relative +5.29pp; the next-results boundary is unknown. VOLTAMP Q1 similarly rose 1.28% early, then +6.29% forward20, relative +7.37pp, with the same negative Nifty context and unknown boundary.',
      'PGEL Q1 had a positive prior Nifty session (+0.054%) and an early +1.03% rally, yet its next-open forward20 return was −10.11%, relative −9.20pp; its next-results boundary is unknown. TIPSMUSIC Q4 had positive prior Nifty context (+0.81%) and a +1.72% early rally; it subsequently gained +1.67% but underperformed by 0.45pp, showing that a positive stock return and relative outperformance are different outcomes. Its next-results boundary is known.',
      'All S3/20 currency and Nifty cases, rather than only selected extremes, appear in MACRO_COUNTEREXAMPLES.csv. “stock_signal_constructive” in that examples file describes the end-of-checkpoint close retrospectively and is not used as a pre-open filter. The full comparison retains all checkpoints and horizons and explicit unavailable groups.',
      '## Limits and outstanding requests',
      'Without recovered licensed US-index histories and timestamped pre-open futures/oil/live-FX observations, the requested global-equity, futures, oil-favorability and joint global-support hypotheses cannot be tested honestly. Subsector strengthening and net business exposure also lack historical attestation. This is partial macro coverage, not a completed all-series global explanation.',
      'The exact separate outstanding manifest contains two unexecuted daily Upstox requests: missing Nifty 50 dates and India VIX. Global licensed-data requirements specify symbols, fields, dates and checkpoints; provider-specific HTTP requests cannot be invented before vendor/entitlement selection. No additional authenticated requests were executed. No earlier NSE SHILCTECH retry is proposed.',
      'Half the existing 288 outcomes have unknown next-results boundaries, and the original event publication dates remain approximate. Price adjustment basis, observed-session calendar completeness, same-company dependence, overlapping horizons, two-quarter selection and missing global regimes prevent causal inference or population-level conclusions. Future macro changes are isolated in AFTER_CHECKPOINT_MACRO_OUTCOMES.csv and never reused as early inputs.',
      'The six pre-existing chart-preview discrepancies remain unchanged. The legacy suite therefore retains its one known preservation-test failure; the 743-file continuation baseline and all six task-start preview hashes pass. See MARKET_CONTEXT_VALIDATION.md for exact checks.',
      'STOP: this bounded ER04 extension ends here.']
    (OUT/'RESEARCH_FINDINGS.md').write_text('\n\n'.join(lines)+'\n')

def documentation():
    (OUT/'MARKET_CONTEXT_VALIDATION.md').write_text('''# Market context validation and reproducibility

## Completed checks

- 11 exact approved Upstox URLs, instrument identities, date bounds and raw response SHA256s checked; 11 HTTP 200 responses; no retry or substitution. 1,323 actual added candles. SHILCTECH's five returned sessions are retained as an explicit insufficiency.
- Independent indicator audit: 38,844 comparisons, zero numerical failures. Retained reference audit: 9,605 comparisons, zero formula mismatches. Original 72 prefix recalculations still prove checkpoint stock features do not depend on future candles.
- 12 new tests cover US DST changes, US holidays and weekends, early close, actual H10 parsing, weekly publication lag, holiday-shifted publication, inverse rupee direction, no futures/oil substitution, Nifty coverage expiry, conceptual close separation, prefix invariance and 1/5/20-observation arithmetic.
- 1,584 event/macro rows = 24 events × 3 checkpoints × 2 information phases × 11 series. Every populated conservative availability bound is at or before its checkpoint; futures, oil and unlicensed US equities are never populated from unverified EOD data. Pre-open stock-close feature references are excluded. H10 actual publication timestamps remain blank; its dated-release upper bound is distinct from the official scheduled release time.
- 72 existing ER04 charts passed actual offline Chrome checks, with 32 interaction checks. Five market-context chart examples passed 45 browser assertions, including pointer and next-candle synchronization, unchanged OHLC/volume colors, four existing panels, missing values and provenance. Browser reports contain no JavaScript errors or HTTP/HTTPS requests. Screenshots were visually reviewed at 1480px desktop width.
- All 743 files in the continuation preservation baseline remain byte-identical. All six already-discrepant chart-preview files match their task-start hashes. Neither the old comparison baseline nor those files was edited to make the legacy test pass.

The complete research-suite result and exact failure are retained in research-tests.log and test-summary.json in this directory. It has one known failure: `test_original_research_files_unchanged`, whose difference list is exactly the six pre-existing chart-preview discrepancies. This is not reported as an all-green suite. No new test failure is accepted.

## Data rules and practical limits

Asia/Kolkata is the decision timezone. Original source offsets and explicit IST conversions are both exported. NYC noon is an observation convention, not H10 availability. Because actual publication minutes are missing, end of the dated NY release day supplies a conservative availability upper bound; the 16:15 ET official schedule is a separate field, not an assumed actual timestamp. This deliberately delays admission at Tuesday pre-open. DST is implemented with ZoneInfo, not a fixed US/India offset. Federal Monday holidays use the actual Tuesday archive. Retained releases are unrevised historical vintages; current-vintage FRED FX is separate. Nifty close is a conceptual market fact; conservative provider bucket completion is used for pre-open selection. Actual historical delivery is not independently attested.

Changes use 1/5/20 prior valid observed daily records. Actual baseline dates are exported; holiday/source-null rows are never zero or interpolated. Calendar completeness is not asserted from a sequence of candles. Pre-open overnight changes require two real comparable timestamped prices and therefore remain unavailable. H10 changes do not measure live overnight FX. Oil is EIA/FRED spot, not exchange futures or a front-contract settlement; no roll adjustment is applied.

US expected session timestamps are calendar context only, not proof of recovered closing values or publication. SP500/Nasdaq/Dow source notices identify reproduction restrictions; no licensed histories are embedded. CME DataMine is a legitimate historical tick source requiring licensing/entitlement. GIFT Nifty contract-specific snapshots and roll/expiry details are absent. The NSE public history host probe failed at transport without a response; no access restriction was circumvented. Its Nifty gap request was not sent after that host failure. Two exact authenticated daily requests are proposed separately and remain unexecuted.

## Sources checked

- H10 schedule and dated vintage semantics: https://www.federalreserve.gov/releases/h10/default.htm and https://www.federalreserve.gov/releases/h10/about.htm
- H10 example dated release: https://www.federalreserve.gov/releases/h10/20260518/
- Public-domain oil/FX descriptions: https://fred.stlouisfed.org/series/DCOILBRENTEU , https://fred.stlouisfed.org/series/DCOILWTICO , https://fred.stlouisfed.org/series/DEXINUS
- Index restrictions and conventions: https://fred.stlouisfed.org/series/SP500 , https://fred.stlouisfed.org/series/NASDAQCOM , https://fred.stlouisfed.org/series/DJIA
- US session hours, holidays and early closes: https://www.nyse.com/markets/hours-calendars
- Licensed futures source: https://www.cmegroup.com/market-data/real-time-and-historical-data.html
- Indian sources: https://www.nseindia.com/reports-indices-historical-vix , https://www.nseix.com/

## Reproduce offline from repository or extracted ZIP

Use Python 3 with timezone data. Run from the repository root, or the extracted review ZIP root:

    python3 -B -m experiments.earnings_reaction_v1.er04
    python3 -B -m experiments.earnings_reaction_v1.verify_er04
    python3 -B -m experiments.earnings_reaction_v1.market_context
    python3 -B -m unittest discover -s experiments/earnings_reaction_v1/tests
    PYTHONPATH=/tmp/tv-dss-live-deps python3 -B -m experiments.earnings_reaction_v1.verify_er04 browser
    PYTHONPATH=/tmp/tv-dss-live-deps python3 -B -m experiments.earnings_reaction_v1.verify_market_context
    python3 -B -m experiments.earnings_reaction_v1.market_context_report

The browser checks additionally need local Chrome and websocket-client. The dependency already existed in /tmp; no package was downloaded. The final report generator packages the current evidence; it does not make network requests. Do not rerun either acquisition harness as part of reproduction. The exclusive Upstox ledger prevents accidental repetition. The public acquisition script reuses successful hash-checked raw files and does not retry HTTP failures.

Read the review ZIP README for its compact-scope reproduction command. The full legacy preservation suite needs all original baseline files, which intentionally are not duplicated in a compact review.
''')
    (OUT/'README.md').write_text('''# ER04 market context review

Start with RESEARCH_FINDINGS.md and MARKET_CONTEXT_VALIDATION.md. Requested CSVs and sector notes are in this folder. charts/index.html opens the five offline examples using the existing ER03C renderer. The synchronized context table shows pre-open and conceptual close values for the selected Indian date; missing series remain explicitly unavailable.

MACRO_DAILY_CONTEXT.csv retains current-vintage oil/FX observations separately from dated H10 vintage observations. PREOPEN_CONTEXT.csv is long form by Indian session and series. EARNINGS_MACRO_CHECKPOINTS.csv is the 1,584-row join. MACRO_CONTINUATION_COMPARISON.csv uses exact ER04 outcomes and separates checkpoints, horizons, macro lookbacks and close-only combined stock signals. MACRO_COUNTEREXAMPLES.csv includes all populated S3/20 pre-open cases. AFTER_CHECKPOINT_MACRO_OUTCOMES.csv contains hindsight descriptions only.

The exact outstanding authenticated request manifest remains unexecuted; its hash is adjacent. Public acquisition manifest and ledger document actual public requests. Original reviewed Upstox scope, raw bodies and added-history coverage are under ../../er03/data-completion. Parent ER04 matrices and charts were regenerated, not replaced with another research project.

The compact ER04-MARKET-CONTEXT-review.zip includes this evidence, all new code/tests, actual provider responses and enough retained inputs to rerun ER04 and market-context calculations. It excludes redundant old previews and most full legacy baselines. From the extracted root, regenerate with `python3 -B -m experiments.earnings_reaction_v1.er04` and `python3 -B -m experiments.earnings_reaction_v1.market_context --compact-review`; run the new safeguards with `python3 -B -m unittest experiments.earnings_reaction_v1.tests.test_market_context experiments.earnings_reaction_v1.tests.test_er04`. The explicit compact mode checks only included preservation-baseline files, records how many original files are omitted, and does not claim full legacy preservation. Full repository suite results are retained, including its known six-file baseline discrepancy.
''')
    (ER04/'README.md').write_text('''# ER04 continuation and market context

The approved eleven-request ER03D acquisition has executed. Simple Volume colors now cover 66/72 checkpoints; SHILCTECH cannot obtain earlier NSE warmup because it first traded on NSE on 24 November 2025. See ../er03/data-completion/ACQUISITION_COMPLETION.md and indicator-coverage.csv.

The original 24-event/72-checkpoint matrices, numerical audits and charts have been rebuilt with actual acquired history. The bounded macro extension, exact missing-data inventory, timing rules, findings, sector hypotheses, annotated charts and compact review ZIP are in market-context/. Start with market-context/RESEARCH_FINDINGS.md.

US indices, timestamped pre-open futures, timestamped oil, live FX and India VIX remain unavailable where provenance/entitlement is insufficient. Dated weekly H10 currency releases and retained Nifty context support limited exploratory comparisons. No unsupported causal or predictive claim is made. Six pre-existing chart-preview hash discrepancies remain unchanged and keep one legacy preservation test failing; the continuation baseline passes.
''')

def package():
    preservation()
    code=OUT/'code';code.mkdir(exist_ok=True)
    names=('market_context.py','retrieve_market_context.py','verify_market_context.py','market_context_report.py','er04.py','retrieve_er03d.py')
    for n in names:shutil.copyfile(Path(__file__).with_name(n),code/n)
    for n in ('test_market_context.py','test_er04.py'):shutil.copyfile(Path(__file__).with_name('tests')/n,code/n)
    files={p for d in (OUT,COMPLETION) for p in d.rglob('*') if p.is_file() and p.suffix!='.zip' and p.name!='SHA256SUMS'}
    # Parent research matrices/evidence and chart examples, avoiding 72 redundant
    # complete chart payloads in a compact review; they are regenerated offline.
    files|={p for p in ER04.iterdir() if p.is_file() and p.suffix not in ('.zip',) and p.name!='SHA256SUMS'}
    files|={p for p in Path(__file__).parent.rglob('*.py') if '__pycache__' not in p.parts}
    sources=json.loads((ER04/'input-provenance.json').read_text())
    files|={ROOT/p for p in sources}
    files|={BASE/'er03'/n for n in ('pilot-companies.csv','approximate-event-windows.csv','market-data-inventory.json')}
    files|={BASE/'er03/chart-upgrade/TIPSMUSIC-data.json',BASE/'er03/chart-upgrade/PROFILE_APPROXIMATION.md'}
    # Retained renderer sources and dependencies of original indicator math.
    files|={p for p in (BASE/'er03/chart-upgrade/assets').iterdir() if p.is_file()}
    files|={p for p in (ROOT/'experiments/earnings_reaction_v1/er03_chart_assets').glob('*') if p.is_file()}
    files|={BASE/'ta02b/public-nse-instruments.json.gz',ROOT/'artifacts/subsector-ss02/evidence/existing-business-profiles.json'}
    drift=json.loads((COMPLETION/'preexisting-preservation-drift.json').read_text())
    files|={ROOT/r['path'] for r in drift['files']}
    # Existing package dependency list (no API keys or .env).
    for relative in ('simple-volume-reconstruction/apply_to_upstox.py','simple-volume-reconstruction/daily-listing-history/reference.csv',
      'simple-volume-reconstruction/daily-listing-history/result.json','simple-volume-reconstruction/SIMPLE_VOLUME_UPSTOX_PREDICTIONS.csv',
      'ta03r4/features/daily.csv','indicator-math-validation/live-dss-attempt/timeframe-validation/daily_L20/live-values.csv'):
        files.add(BASE/relative)
    files={p for p in files if p.exists()}
    sums=OUT/'SHA256SUMS';sums.write_text(''.join(f'{digest(p)}  {p.relative_to(ROOT)}\n' for p in sorted(files)))
    files.add(sums)
    dest=OUT/'ER04-MARKET-CONTEXT-review.zip'
    with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(files):z.write(p,str(p.relative_to(ROOT)))
    with zipfile.ZipFile(dest) as z:
        assert z.testzip() is None
        assert all(not n.endswith('.env') and 'token' not in Path(n).name.lower() for n in z.namelist())
    print(json.dumps(dict(zip=str(dest.relative_to(ROOT)),bytes=dest.stat().st_size,files=len(files),sha256=digest(dest))))

def main():
    acquisition_report();sector_notes();findings();documentation();package()

if __name__=='__main__':main()
