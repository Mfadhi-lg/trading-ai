"""Review report and deterministic compact ZIP. No market acquisition."""
from pathlib import Path
import json,re,zipfile,hashlib,shutil
from .build import OUT,INPUT,V1,ROOT,SETS,PRIMARY,read_csv,preserve
from experiments.earnings_reaction_v1.er03 import digest,write_json
from experiments.earnings_reaction_v1 import er03_chart_upgrade as chart

def reports():
    status=json.loads((OUT/'status.json').read_text())
    numerical=json.loads((OUT/'numerical-validation.json').read_text())
    prefix=json.loads((OUT/'checkpoint-prefix-validation.json').read_text())
    browser=json.loads((OUT/'browser-validation.json').read_text())
    preserved=json.loads((OUT/'preservation-verification.json').read_text())
    missing=status['nifty50_missing_sessions']
    priorities=read_csv(OUT/'Q2_EXPANSION_PRIORITY.csv')
    trajectory=read_csv(OUT/'ER04_FIXED_TRAJECTORIES.csv')
    (OUT/'README.md').write_text('''# SS-INDEX-02

Start with RESEARCH_FINDINGS.md and VALIDATION_REPORT.md. Open charts/index.html locally for 30 offline interactive comparisons using the existing ER03C renderer. CSVs are authoritative; charts show later outcomes as context.

Three improved manufacturer subsets plus three separately labelled broad sensitivities, each at both formation cutoffs. All are PROVISIONAL / RETROSPECTIVE_DESCRIPTIVE. No institutional inflows, buy/sell model or certified historical constituent selection is claimed.

Reproduce from repository root (Python standard library, no credentials):

    python3 -B -m experiments.subsector_index_v2.build
    python3 -B -m experiments.subsector_index_v2.verify
    python3 -B -m unittest experiments.subsector_index_v2.test_v2 experiments.subsector_index_v1.tests.test_indices experiments.earnings_reaction_v1.tests.test_er04

For an extracted compact review use `build --compact-review`. Original research databases and full legacy SS outputs are deliberately absent; full preservation is attested only in the original workspace. Do not run prepare or acquire modules during replay. Reports and chart browser evidence are retained; `verify` rebuilds arithmetic and all 90 checkpoint prefixes.

Optional local Chrome checks, using the pre-existing websocket-client installation:

    PYTHONPATH=/tmp/tv-dss-live-deps python3 -B -m experiments.subsector_index_v2.browser

`experiments.subsector_index_v2.report.package()` writes a ZIP with fixed timestamps and sorted paths; ZIP_REPLAY.json records replayed output hashes. No live credentials, cookies or database copies are included. Inputs retain original relative source paths and SHA256s.

Stop after SS-INDEX-02. Q2_EXPANSION_PRIORITY.csv is an engineering research inventory; no remaining Q2 indices were constructed.
''')
    (OUT/'METHODOLOGY.md').write_text('''# V2 definitions and limits

Reuse: SS01 historical tutor provenance and corrected Q2 exports; SS02 exchange/company inventory; SS03 canonical peer reviews; SS04 existing classifications; V1 core.py and study.py; ER04 event boundaries and stock observations; ER03C assets, synchronization and browser harness. No master, taxonomy, database or chart framework was recreated.

Primary sets:
- TYRES: CEATLTD, MRF, APOLLOTYRE, JKTYRE, BALKRISIND. Balkrishna has off-highway/export/carbon-black exposures; this is not identical demand to road tyres. TOLINS is separately retained in TYRES_WITH_RETREAD because new tyres and retread products have different exposure. GRPLTD rubber recycling is excluded.
- EMS: SYRMA, DIXON, AVALON, KAYNES. Contract manufacturing/box-build/design-led end-markets differ and are documented. CENTUM systems/defence/engineering and PGEL appliance ODM/OEM/plastics remain in EMS_BROAD. PGEL and CENTUM versus core EMS are external-stock comparisons, not claims of pure-core membership.
- TRANSFORMERS: TARIL, VOLTAMP, SHILCTECH, ATLANTAELE. POWERINDIA is in TRANSFORMERS_BROAD only because transformers sit within diversified grid-equipment exposure. Bushings, distribution and unrelated electrical equipment are excluded.

Both 2026-03-31 and 2026-06-30 cutoffs have fixed sets for their entire follow-up. Prices through the cutoff verify earlier listing and seed indicators. Base 1,000 on the first common observed session, 2025-12-01; pre-formation rows are retrospective membership-backcast warmup. The same fixed members produce identical levels across the two formation IDs. No quarterly splice or daily membership selection occurs. Selection was researched in November2026: none of these historical sets is point-in-time-verified. Later Screener categories are only cross-checks, never backdated inputs. Do not use this retrospective selection as an unbiased historical investment universe.

V1 exact arithmetic is reused: each daily return is the mean of the fixed members' synchronized close-to-close returns. Index_t = Index_(t-1) * (1 + mean(return_i,t)). Daily previous-close equal-return weights, not drifting holdings or average raw prices. Transaction costs are unmodelled. One missing member breaks the level chain; no filling, calendar compression or denominator shrinkage. Normalized index has no open/high/low. Stock chart lines use actual daily closes.

Corporate actions: price index, ordinary dividends not reinvested. Reviewed share-unit factor1 is provisional. Newly acquired candles are provider prices with no inferred jump adjustment. Existing V1 forward split/bonus normalization remains available only for confirmed unadjusted provider basis; unknown basis for a known action and unresolved rights/demergers block the series. Positive handling is tested in V1 fixtures. Primary action pages mostly rendered shells or incomplete tables, and the provider's complete adjustment convention is unattested. Limited secondary listed histories and dated issuer evidence are separately recorded in inputs/peer-action-review.json. JKTYRE's Cavendish subsidiary amalgamation changes perimeter and is a specific continuity limitation; no split factor is inferred for existing common shares. PreIPO actions and actions before the December base are not applied again. A future contradictory action record requires a new revision, never silent correction.

Registry listing dates are later-snapshot metadata, checked against the requested ranges and same-ISIN pre/post-cutoff observations. Atlanta registry date 2025-09-29 precedes the requested base; NSE preliminary circular CML70437 binds identity but explicitly defers the final listing date. Shilchar's NSE listing 2025-11-24 is supported by V1 issuer evidence; no BSE price/volume splicing. Complete exchange security-version/suspension archives are unavailable. Full observed-grid coverage is not a complete exchange-calendar or suspension certificate. Sunday Budget session 2026-02-01 is retained.

EMA10/20 use 2/(n+1), first-close seed. Constituent breadth waits for 20 observations. RS55 = (close_t/close_(t-55))/(NIFTYMIDSML400_t/NIFTYMIDSML400_(t-55))-1, requiring all56 synchronized observations. RS is dimensionless, not RSI. Returns span 5/10/20/40 observed sessions. Above-EMA breadth and advances/declines use all fixed members; ties are separate. RVOL20 divides today's comparable volume by the previous20 observations, excluding today. Activity breadth is RVOL20>1 and advancing-and-RVOL20>1; activity ratio is close-times-volume relative to its lagged20 mean. Group value is sum(close*shares)/1e7, an INR-crore proxy rather than exact traded turnover. Raw share volumes are never summed.

Concentration = largest absolute member daily-return contribution / sum of absolute contributions. A single-return concentration measure is descriptive, not evidence of institutional accumulation. Relative leadership ranks only the three core subsets by RS55 and 20-session returns; broader sensitivities are excluded from those ranks. Stock-versus-peer excess returns and labels are descriptive.

Leave-one-out removes the evaluated member from the relevant fixed set, including all dates and indicators. At least two independently priced peers are required; core sets leave three or four. External comparison stocks do not enter their core benchmark. Broad sensitivities have separate leave-one-out rows. No self-dominated comparator.

Earnings: reuse all24 original events;18 supported events at S1/3/5/20/40. Checkpoints require formation before the session and exclude known next-results sessions. Unknown next-results boundaries remain disclosed. Early fields come only from their price prefix (90 complete rebuilds). ER04_FIXED_TRAJECTORIES.csv compares S1/3/5 with fixed event S20/40; later peer RS/breadth/momentum are explicitly outcome-only. These differ from20 sessions after S3. CSVs preserve original event dates and stock outcomes. No causality, significance, optimized thresholds or universal sector explanation.

Q2:117 corrected Focus identities,120 untouched original mentor-assessment rows,27 Blue memberships. ATHERENERG is the current owner correction. Original Blue TXT says SAMBHAAV; explicit task instruction corrects this to SAMBHV in a separate overlay. Two verified securities (EMBASSY.RR and VARMORA) lack canonical IDs in the reused master and remain unlinked. Multi-activity primary codes remain unresolved; unranked activity candidates are retained, without assigning arbitrary revenue weights. Available verified peers in the priority inventory mean current SS04 evidence, not historical index eligibility. Prices are counted from retained authenticated daily sources, not generic quotes. Rank coherent existing codes first, then Focus count, Blue count, verified peer price coverage and verified peer count, with code as deterministic tie-break. It prioritizes engineering demand, not future returns. Blue absence never means rejection.
''')
    def num(v):return f'{float(v):.2f}' if v else 'unavailable'
    findings=['# SS-INDEX-02 findings','',
        'The three core subsets are expanded, with 227 synchronized daily observations each. Their broader business sensitivities are separate. All are provisional retrospective descriptions; passing arithmetic tests does not certify historical membership or adjustment completeness.','',
        'V1 remains byte-preserved. V1_V2_COMPARISON.csv shows every date side by side; changes are constituent-scope sensitivity using the same daily-return architecture.','',
        'All117 corrected Focus identities and27 Blue members reconcile. All120 original Q2 assessment rows are frozen independently. The source Blue TXT contains SAMBHAAV, corrected to SAMBHV only by the explicit owner instruction. Two master-unlinked securities remain unlinked.','',
        'The original seven price gaps plus Balkrishna were acquired only after exact manifest approval. Each returned227 rows. A first sandbox transport failure is separately retained; the explicit outside-sandbox recovery then completed. No provider-response retries, substitutions or pre-listing prices were used.','',
        '## Earnings evidence: fixed S3 to event S20/S40','']
    for t in trajectory:
        if t['early_checkpoint']!='3' or t['later_event_session'] not in ('20','40'):continue
        if t['event_id'] not in ('FY2027-Q1:PGEL','FY2027-Q1:VOLTAMP','FY2026-Q4:SYRMA','FY2026-Q4:SHILCTECH'):continue
        findings.append(f"- {t['event_id']} S3→S{t['later_event_session']}: stock {num(t['stock_return_early_to_late_pct'])}%, independent core peers {num(t['peer_return_early_to_late_pct'])}%. Early peer20-session momentum {num(t['early_peer_return20_pct'])}%; early5-session RS55 change {num(t['early_peer_rs55_change5'])}. Peer breadth {num(t['peer_pct_above_ema20_early'])}%→{num(t['peer_pct_above_ema20_later_outcome_only'])}% (later value is outcome context). Stock state {t['gain_state']}; V1 stock-state preserved={t['gain_state_preserved']}. Unknown next-results boundary={t['unknown_next_results_boundary']}.")
    findings+=['',
        'PGEL Q1 still loses its early gain. V2 changes the early sector interpretation: core EMS RS55 was already deteriorating at S3, unlike the V1 mixed-EMS peer RS improvement. Subsequent breadth deterioration coexists with positive peer price returns; it does not explain the full stock decline. PGEL is an appliance/ODM comparison against independent core EMS, with broad sensitivity exported alongside it.',
        'VOLTAMP Q1 continues at S20 and S40 despite negative early peer momentum. Its V2 peers rise slightly by event S20 and fall by S40; the V1 narrative of declining subsequent peers must therefore be horizon-specific. SYRMA Q4 recovery after weak earlier peers and SHILCTECH Q4 weakness despite positive earlier peer momentum remain counterexamples. Stock gain-state conclusions are preserved; peer-context conclusions can change.',
        '', '## Engineering expansion inventory','',
        'Highest-ranked existing codes: '+', '.join(f"{r['granular_subsector']} ({r['focus_count']} Focus, {r['blue_count']} Blue)" for r in priorities[:8])+'. This is research workload prioritization; historical membership and action audits are still required. No other indices were constructed.','',
        f'Nifty50 missing observed sessions: {len(missing)}. '+('The exact one-request remaining manifest is retained. Nifty50-dependent fields are blank on missing dates; primary indices and midsml400 RS55 are unaffected.' if missing else 'The separately approved Nifty50 tail completes the comparison grid.'),
        'Complete primary corporate-action, suspension and historical selection/publication audits remain unsupported. No benchmark certification, institutional inflow inference or trading-rule validation is claimed. Stop after SS-INDEX-02.']
    (OUT/'RESEARCH_FINDINGS.md').write_text('\n'.join(findings)+'\n')
    validation=f'''# SS-INDEX-02 validation

Three expanded core daily indices (5 tyre /4 core EMS /4 transformer members), three separate broad sensitivities, two fixed formations,227 actual synchronized sessions.18 supported earnings events,90 supported checkpoint snapshots,117 Focus companies,27 Blue memberships,120 preserved source-assessment rows.

The targeted V2 + prior V1 + ER04 regression suite passed41/41 tests. The broader SS01–SS04 suite run passed107/109. The two pre-existing snapshot mismatches are recorded verbatim in tests.log: SS03 test_original_assessments_and_unknown_dates expects100 rows while the corrected mentor table contains120; SS04 test_source_preservation compares a stale ss02_identity_overlay fingerprint with the later corrected source. These legacy tests were not weakened or the source reverted. Byte-preservation confirms their underlying artifacts were unchanged by SS-INDEX-02.

Independent Fraction audit: {numerical['comparisons']:,} comparisons, {len(numerical['failures'])} failures, tolerance1e-18. Checks daily equal-return weighting, normalized levels, EMA, RS55/date alignment, RS changes, horizons, member RVOL, breadth and activity. V1 synthetic tests verify split/bonus handling, dividends, unknown-basis/rights blocking and missing-member chain breaks. New tests verify core/broad boundaries, exact peer ISIN/listing dates, complete observed grid, actual target-price perturbation independence, insufficient peers, cohort corrections and explicit action uncertainty.

Prefix audit: {prefix['checks']} supported checkpoints rebuilt from truncated prices, zero feature differences. This proves arithmetic uses price prefixes, not that retrospective constituent choice was known historically.

Actual local Chrome: {browser['charts']} charts, {browser['assertions']} assertions, no JavaScript errors or HTTP/HTTPS chart requests. Checks actual close lines, normalized benchmark/subsector comparisons, sector EMAs, missing Nifty handling, independent peer names, earnings markers, selectable S3→S20/S40 outcome windows, navigation, crosshairs and toggles. Representative screenshots were visually inspected. Only the local desktop Chrome configuration was tested.

Preservation: {preserved['baseline_files']} original SS01–SS04, V1 and ER04 files hash-identical in the full workspace. V1 prices/results/renderer files and original tutor evidence remain intact. Generic production scrapers/services were not modified. Frozen V2 input hashes are verified before each build; new raw responses are bound to the exact approved manifest and original response hashes.

ZIP_REPLAY.json records actual clean-directory replay and output equality; package metadata uses fixed timestamps, sorted paths and fixed compression. Compact replay explicitly reports missing original preservation-baseline files instead of certifying the absent full workspace. Live acquisition must never run during replay.

Unresolved: complete primary corporate-action/provider-adjustment attestation, exhaustive suspension/calendar audit, historical first-publication archives and point-in-time constituent selection. All outputs remain PROVISIONAL / RETROSPECTIVE_DESCRIPTIVE. Nifty50 missing tail sessions: {len(missing)}; affected comparisons are blank and their exact remaining request is separately bounded. Numeric tests do not remove these limitations.
'''
    (OUT/'VALIDATION_REPORT.md').write_text(validation)

def package():
    preserve(False)
    files={p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip' and '__pycache__' not in p.parts and p.name not in ('SHA256SUMS','ZIP_REPLAY.json','ZIP_DETERMINISM.json')}
    files|={p for root in (ROOT/'experiments/subsector_index_v2',ROOT/'experiments/subsector_index_v1',ROOT/'experiments/earnings_reaction_v1') for p in root.rglob('*.py') if '__pycache__' not in p.parts}
    files|={p for p in (V1/'inputs').iterdir() if p.is_file()}
    files|={V1/n for n in ('TYRES_DAILY_INDEX.csv','EMS_DAILY_INDEX.csv','TRANSFORMERS_DAILY_INDEX.csv','ER04_SUBSECTOR_CHECKPOINTS.csv','EVENT_FIXED_CHECKPOINT_TRAJECTORIES.csv')}
    files|={p for p in chart.ASSETS.iterdir() if p.is_file()}
    files|={ROOT/'artifacts/earnings-reaction-v1/er03'/n for n in ('market-data-inventory.json','approximate-event-windows.csv')}
    provenance=json.loads((V1/'inputs/source-provenance.json').read_text())
    files|={ROOT/p for p in provenance['original_provider_files']}
    # Include exact original Nifty50 source and all observed-grid acquisition evidence.
    inv=json.loads((INPUT/'retained-candle-inventory.json').read_text())
    files|={ROOT/r['source'] for r in inv if r['instrument_key']=='NSE_INDEX|Nifty 50'}
    sums=OUT/'SHA256SUMS';sums.write_text(''.join(f'{digest(p)}  {p.relative_to(ROOT)}\n' for p in sorted(files)))
    files.add(sums)
    replay=OUT/'ZIP_REPLAY.json'
    if replay.exists():files.add(replay)
    target=OUT/'SS-INDEX-02-review.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(files):
            name=str(p.relative_to(ROOT))
            if any(x in name for x in ('.env','.screener_session','.screener_csrf')):raise ValueError('Credential path rejected')
            info=zipfile.ZipInfo(name,date_time=(2026,11,4,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            z.writestr(info,p.read_bytes(),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    with zipfile.ZipFile(target) as z:
        if z.testzip():raise ValueError('ZIP integrity failure')
    print(json.dumps(dict(archive=str(target.relative_to(ROOT)),files=len(files),bytes=target.stat().st_size,sha256=digest(target))))
    return target

if __name__=='__main__':reports();package()
