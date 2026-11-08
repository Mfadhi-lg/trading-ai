"""Bounded daily acceptance, evidence reconciliation and review packaging."""
from datetime import timedelta,timezone
from pathlib import Path
import csv,json,sqlite3,zipfile
from .prepare import ROOT,OUT,INPUT,V2,MACRO
from .build import read_csv,stamp,preserve,SERIES
from experiments.earnings_reaction_v1.er03 import digest,write_json

def outstanding():
    calendar=sorted({r['date'] for r in read_csv(OUT/'SUBSECTOR_ACTIVITY.csv')});windows=[]
    for day in calendar:
        for phase,h,m in [('PREOPEN',9,15),('POSTCLOSE',18,0)]:
            cutoff=stamp(day,h,m)
            for s in ('ES','NQ','GIFT_NIFTY'):
                windows.append(dict(series=s,indian_session=day,phase=phase,
                    window_start_ist=(cutoff-timedelta(minutes=15)).isoformat(),window_end_exclusive_ist=cutoff.isoformat(),
                    window_start_utc=(cutoff-timedelta(minutes=15)).astimezone(timezone.utc).isoformat(),window_end_exclusive_utc=cutoff.astimezone(timezone.utc).isoformat(),
                    selection='Latest actual published trade/quote strictly before cutoff; do not fabricate if exchange closed or no quote',
                    execution_status='NOT_EXECUTABLE_UNTIL_VENDOR_ENTITLEMENT_CONTRACT_AND_SESSION_CALENDAR_VERIFIED'))
    from experiments.subsector_index_v1.sources import write_rows
    write_rows(OUT/'FUTURES_ACQUISITION_WINDOWS.csv',windows)
    write_json(OUT/'OUTSTANDING_ACQUISITION_MANIFEST.json',dict(task='TECH-05',executable_request_count=0,requests=[],
        authorized_requests_completed=5,automatic_retries=0,status='PROVIDER_IDENTITY_AVAILABILITY_AND_ENTITLEMENT_BOUNDARY;NO_MORE_REQUESTS_AUTHORIZED',
        scope=dict(first_date=calendar[0],last_date=calendar[-1],indian_sessions=len(calendar),preopen='09:15 IST exclusive',postclose='18:00 IST exclusive'),
        unresolved_requirements=[
            dict(series=['SP500','DJIA'],requirement='Provider documentation or corrected history mapping every daily bucket to the actual US exchange session; unadjusted price-index close, NY observation close, historical publication/revision/availability timestamps',
                 current_evidence='Retained approved raw responses; 9 SP500 and8 DJIA bucket labels fall on US nontrading dates. No guessed date shifts.',provider='Upstox support or licensed timestamped index vendor',api_url=None),
            dict(series=['NASDAQCOM'],requirement='Nasdaq Composite price index, daily actual NY-session close and historical availability. Never substitute Nasdaq100/US Tech100.',
                 candidate_source='https://fred.stlouisfed.org/series/NASDAQCOM',candidate_daily_url='https://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQCOM&cosd=2025-12-01&coed=2026-10-30',
                 execution_status='NOT_REQUESTED;DATA_OWNER_TERMS_AND_HISTORICAL_AVAILABILITY_REVIEW_REQUIRED;URL_IS_A_CANDIDATE_NOT_APPROVAL'),
            dict(series=['ES','NQ','GIFT_NIFTY'],requirement='Contract-specific historical trade/quote observations in FUTURES_ACQUISITION_WINDOWS.csv, plus previous comparable same-contract close/trade timestamp; exchange calendar/roll policy',
                 required_fields=['exchange_contract_id','expiry','price','currency','observation_kind','observation_timestamp_with_timezone','historical_publication_timestamp','previous_comparable_close_and_timestamp','roll_policy','provider_entitlement_reference'],
                 provider='CME licensed archive for ES/NQ; NSE IX or entitled vendor for GIFT',api_url=None,
                 execution_status='Precise data-selection windows; no vendor SKU, contract key, entitlement or endpoint invented. Needs provider selection and separate authorization.'),
            dict(series=['NIFTY_CAPITAL_GOODS'],requirement='Official Nifty Capital Goods PR daily history and publication times; launch2026-06-15. Pre-launch history must remain retrospective.',
                 source='https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-capital-goods',api_url=None,
                 identity_warning='No confirmed key in retained current NSE master. Nifty Capital Markets explicitly rejected.'),
            dict(series=['BRENT','WTI'],requirement='Dated EIA publication vintages for the retained spot observations if decision-time features are needed. Settlement timing requires a separately identified futures contract; never relabel spot as settlement.',
                 current_evidence='Retained FRED current-vintage spot data are already reused. No redownload requested.'),
            dict(series=['NIFTY50','NIFTYMIDSML400','NIFTY_AUTO','NIFTY_CONSUMER_DURABLES','INDIA_VIX'],requirement='Historical final-close publication/provider-delivery attestation for strict decision-time features; existing daily prices already complete on observed grid.',current_evidence='No price-history gap on the227-session grid')],
        policy='Offline supported implementation complete. No unrestricted acquisition, retries, TradingView access or production changes. Futures gaps do not invalidate accepted daily components.'))

def legacy_evidence():
    from scrapper.research.subsector_ss04 import build as b
    snapshot=sqlite3.connect(f'file:{b.DB}?mode=ro',uri=True);current=sqlite3.connect(f'file:{b.ss03.SOURCE}?mode=ro',uri=True)
    frozen=json.loads((b.O/'validation.json').read_text())['original_table_fingerprints'];actual=b.fingerprint(snapshot);now=b.fingerprint(current)
    if actual!=frozen:raise ValueError('Frozen SS04 evidence mismatch')
    current_count=current.execute('select count(*) from ss_mentor_assessments').fetchone()[0]
    frozen_count=snapshot.execute('select count(*) from ss_mentor_assessments').fetchone()[0]
    differences=[dict(table=t,ss04_frozen=h,current_source=now.get(t)) for t,h in frozen.items() if now.get(t)!=h]
    snapshot.close();current.close()
    write_json(OUT/'LEGACY_SNAPSHOT_RECONCILIATION.json',dict(frozen_ss04_table_fingerprints_verified=len(frozen),all_frozen_tables_unchanged=True,
        ss04_mentor_rows=frozen_count,current_source_mentor_rows=current_count,differing_current_tables=differences,
        mentor_evidence='artifacts/subsector-ss01/q2-2027-addendum-20261103/q2-all-assessments.csv',
        mentor_source_sha256=digest(ROOT/'artifacts/subsector-ss01/q2-2027-addendum-20261103/q2-all-assessments.csv'),
        frozen_evidence='artifacts/subsector-ss04/validation.json:original_table_fingerprints',frozen_evidence_sha256=digest(b.O/'validation.json'),
        changes='SS03 test compares all120 complete source-bound assessment rows and null dates. SS04 test compares every frozen original table against its saved checksummed fingerprints. SS04 setup now opens the retained DB read-only; no builder is run.',
        not_changed='No research/master/taxonomy rows changed, no assertions disabled, no tables exempted from fingerprint comparison'))

def reports():
    inventory=read_csv(OUT/'MARKET_DATA_INVENTORY.csv');pres=json.loads((OUT/'preservation-verification.json').read_text())
    numerical=json.loads((OUT/'numerical-validation.json').read_text());browser=json.loads((OUT/'browser-validation.json').read_text())
    coverage=['# TECH-05 integration coverage','',
        'The observed Indian grid contains227 sessions from2025-12-01 through2026-10-30, including the retained Sunday Budget session. All original V2 datasets and charts remain preserved; revised charts live only here. Twelve core/broad formation series have activity panels and three original ER04 stock examples retain their validated indicator payloads.','',
        '## Per-series inventory','']
    for r in inventory:
        coverage.append(f"- **{r['series']}** — {r['integration_status']}; {r['retained_observations']} retained values; dated-release-bound PREOPEN/POSTCLOSE sessions {r['verified_preopen_sessions']}/{r['verified_postclose_sessions']}; conceptual prior Indian close coverage {r['conceptual_prior_close_sessions']}. {r['limitation']}.")
    coverage+=['','## Historical clock contract','',
        'PREOPEN is strictly before09:15 Asia/Kolkata. POSTCLOSE is strictly before18:00 Asia/Kolkata, after the15:30 Indian market close. No historical delivery at15:30 is assumed. CSVs deliberately separate verified_feature_value, conservative_prior_close_context_value, same_session_close_context_value and retrospective_value. Only the dated H10 release-bound fields populate the strict feature channel in this evidence set.','',
        'H10 rates are noon New York observations published in a later weekly release. Retained dated archives support an end-of-release-day NY availability upper bound; actual release-minute attestation remains unavailable. DST conversion uses zoneinfo, not a fixed UTC offset. At winter midnight NY the conservative bound is10:30 IST; in summer it is09:30 IST. This can exclude a release from Indian PREOPEN while admitting it at POSTCLOSE. Both change endpoints must be available. These are stale official reference rates, not live USD/INR or Indian closing FX.','',
        'Indian final-close bars are usable as daily historical context, with prior-close and same-session-close categories shown separately. Their conservative next-midnight provider bucket bound is not a proof of actual historical delivery. Same-session values at18:00 remain conceptual context, not certified features.','',
        'US cash-market calendars use America/New_York with observed holidays and early closes; expected prior completed closes are metadata only. Current global metadata advertises latency and fixed clock strings, which do not prove historical daily-bar session mapping. The acquired SP500 and DJIA histories are quarantined for as-of use because their IST bucket labels include US holidays. No correction, interpolation or date shift was fitted. Raw rows and17 date-label anomalies are retained. Same-date US daily closes never enter either Indian snapshot; prior provider labels appear only in the explicit retrospective channel.','',
        'ES, NQ and GIFT pre-open quotes remain unavailable. Generic DOW FUTURES and SGX-labelled GIFT series do not establish CME ES/NQ contracts, expiry, roll policy or actual historical quotes. Daily settlements never substitute for snapshots. FUTURES_ACQUISITION_WINDOWS.csv specifies15-minute windows ending strictly at each cutoff; a vendor/contract/calendar/entitlement check is required before any acquisition.','',
        'Brent and WTI are retained current-vintage FRED/EIA spot prices in USD/barrel, with date-only observation semantics and unknown historical release times. They remain retrospective context. No futures settlement timestamp or live quotation is invented. Source-null rows stay null.','',
        '## Official sector benchmarks','',
        'Nifty Auto supplies a broad automotive comparison for Tyres; Nifty Consumer Durables supplies a broader consumer-product contrast for EMS. Neither is the custom granular index. Official series use externally determined market-cap weights; custom indices use fixed equal daily-return weights. Historical constituent overlap is not inferred from current factsheets.','',
        'Nifty Capital Goods is an official sectoral index with launch2026-06-15 and back-history base2005-04-01. Its verified price key/history is unavailable here; pre-launch comparisons are explicitly retrospective. Nifty Capital Markets is a financial-services concept and was rejected as a substitute. No official tyre-only, pure EMS or transformer-only index is invented.','',
        'The exact completed five-request manifest and raw responses are retained. OUTSTANDING_ACQUISITION_MANIFEST.json has zero executable requests and precisely scoped unresolved data/entitlement requirements. URLs/contracts that cannot be verified are null rather than guessed.']
    (OUT/'INTEGRATION_COVERAGE.md').write_text('\n'.join(coverage)+'\n')
    acceptance=f'''# TECH-05 daily technical acceptance

**Supported daily research components accepted for a Document Intelligence research join, with provenance/availability gates. The complete verified global decision-time feature layer is not accepted. Document Intelligence was not started.**

1. Individual stock chart: original ER04 CEATLTD, PGEL and VOLTAMP Q1/S3 candlestick examples copied. Every original stock/indicator payload field remains exactly equal, including nested Simple Volume fields. Original outputs are unchanged.
2. Stock indicators: EMA10/20, DTR/ATR, DSS RVol, Simple Volume and stock RS55 calculations and their source code remain unchanged. No new indicator implementation.
3. Custom granular subsector: all12 core/broad formation series reuse original V2 closing levels, constituent definitions, EMA and RS; retrospective/provisional limitations remain.
4. Trading activity: a separate histogram shows sum(raw close INR × raw NSE traded equity shares)/1e7. This is estimated traded value in INR crore, not exact turnover, exchange share-volume, buy/sell volume or institutional flow. Green/red encode same-date custom-index rise/fall, neutral encodes flat/base. SMA20 includes current; relative GROUP activity uses the preceding20 completed sessions, excluding current. Median MEMBER RVOL20 and activity breadth remain separate. Reported turnover is null, explicitly unavailable, never filled by the proxy. Missing members/sessions block totals and relevant rolling windows.
5. Participation and independent strength: breadth remains separate; original V2 leave-one-out stock RS is added to the full stock chart. A stock is absent from its core peer set. Full-group activity is labelled separately from leave-one-out RS.
6. Benchmarks: Nifty50 and midsml400 are reused; Auto/Consumer Durables now have227 aligned daily observations each. Capital Goods exists but lacks verified provider key/history and did not launch until June15. No misleading substitute.
7. Global context: per-series state and selected-date provenance appear in charts and MARKET_DATA_INVENTORY.csv. SP500/DJIA are retrospective/quarantined, NasdaqComposite and contract-specific futures unavailable, USDINR has dated-release-bound reference context, crude is retrospective spot. Missing futures do not invalidate the accepted daily stock/subsector components.
8. Historical alignment: separate09:15/18:00 IST snapshots, DST-aware NY calendar, strict availability cutoffs, no same-date US close, no fake futures settlement snapshots, no future baseline observations. Conceptual closes and retrospective values are excluded from the strict feature columns.

Numerical evidence: {numerical['comparisons']:,} independent raw-candle/Fraction and cutoff comparisons, zero failures; {numerical['prefix_snapshots']} historical snapshot-prefix checks. MA and group-ratio warmups, missing-session handling, corporate-action price/volume invariance, direction coloring, DST/US holidays/early closes, H10 delayed publication, futures settlement rejection and original indicator payload preservation have tests. Main technical/retained regression suite:80 tests passed. Reconciled legacy subset:15 passed; no SS01–SS04 builder was run.

Functional evidence: local Chrome, {browser['charts']} charts, {browser['assertions']} assertions, no JavaScript errors or remote chart requests. Histogram values/colors, warmup references, separate breadth, original stock panes, normalized official benchmarks, selected-date context, navigation, crosshair synchronization and toggles checked. Representative index and stock screenshots were visually reviewed. Other browsers/mobile devices were not tested.

Preservation: {pres['original_files']} original-baseline paths checked; zero research-file differences. Finder .DS_Store is explicitly excluded as OS metadata; observed metadata changes: {len(pres.get('os_metadata_changes',[]))}. The original preservation baseline is retained. Compact replay certifies included outputs only, not omitted full-repository artifacts/databases.

Legacy tests were corrected against evidence rather than disabled: the current mentor source now has120 original rows, verified field-for-field against the checksummed November3 addendum export; the frozen SS04 snapshot still has100 and all38 saved original-table fingerprints match. Its test now uses the checksummed frozen validation evidence, not the evolving live source. LEGACY_SNAPSHOT_RECONCILIATION.json records both cohorts and changed-source fingerprints. Tests open the SS04 database read-only.

Remaining limitations: exact Session Volume Profile HD parity remains unavailable; existing fixed-bin minute approximations are not exact HD. Historical intraday and weekly coverage remain limited to previously validated slices; TECH-05 did not expand them. Primary corporate-action/provider-adjustment and exchange suspension certification remain incomplete. Constituents are retrospective descriptive, not certified point-in-time baskets. US bucket/session mapping, NasdaqComposite data, historical futures, current-vintage crude releases and actual Indian close delivery are unresolved. Unknown earnings boundaries and earlier indicator-reconstruction qualifiers remain. No exchange-announcement timing investigation, universe expansion, ML or trading strategy was started.

Stop after this bounded daily milestone. The daily charts can support Document Intelligence research context with these gates; do not feed quarantined/retrospective fields into historical decision features as though they were verified.
'''
    (OUT/'TECHNICAL_ACCEPTANCE.md').write_text(acceptance)
    (OUT/'README.md').write_text('''# TECH-05

Open charts/index.html, then TECHNICAL_ACCEPTANCE.md and INTEGRATION_COVERAGE.md. CSVs are authoritative. This is a bounded extension of existing ER03C/V2 charts and retained market-context data.

Offline reproduction from repository root, Python standard library:

    python3 -B -m experiments.technical_integration_v1.build
    python3 -B -m experiments.technical_integration_v1.verify
    python3 -B -m unittest experiments.technical_integration_v1.test_integration experiments.earnings_reaction_v1.tests.test_market_context experiments.earnings_reaction_v1.tests.test_er04 experiments.subsector_index_v1.tests.test_indices experiments.subsector_index_v2.test_v2

In a compact extraction use `build --compact-review`. Do not run prepare/acquire modules during replay. The original research databases and full preservation baseline are not bundled; legacy database tests are a full-workspace check, with their evidence and logs retained here. ZIP_REPLAY.json records actual isolated reproduction and deliverable hashes. ZIP_DETERMINISM.json records repeated identical packaging of unchanged members.

No credentials, cookies or live database copies are included. Raw responses, metadata snapshots and relative source paths/hashes are retained. All five approved requests completed; remaining requirements are in OUTSTANDING_ACQUISITION_MANIFEST.json. No further network activity is part of replay.
''')

def package():
    preserve(False)
    files={p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip' and '__pycache__' not in p.parts and p.name not in ('SHA256SUMS','ZIP_DETERMINISM.json') and p.name!='.DS_Store'}
    # Reuse the already reviewed compact V2 dependency inventory, not another
    # hand-built market loader or indicator copy. Existing paths are preserved.
    with zipfile.ZipFile(V2/'SS-INDEX-02-review.zip') as z:
        files|={ROOT/n for n in z.namelist() if not n.endswith('/') and (ROOT/n).is_file() and not n.endswith('.DS_Store')}
    files|={p for p in (ROOT/'experiments/technical_integration_v1').glob('*.py')}
    files|={MACRO/'MACRO_DAILY_CONTEXT.csv',MACRO/'public-acquisition-ledger.json'}
    files|={p for p in (MACRO/'raw').iterdir() if p.is_file()}
    stock=ROOT/'artifacts/earnings-reaction-v1/er04/charts'
    files|={stock/('FY2027-Q1-'+s+'-S3.html') for s in ('CEATLTD','PGEL','VOLTAMP')}
    files|={p for p in (stock/'assets').iterdir() if p.is_file()}
    files|={V2/'CONSTITUENT_DAILY_FEATURES.csv',V2/'BREADTH_AND_ACTIVITY.csv',V2/'INDEX_V2_DAILY.csv',V2/'LEAVE_ONE_OUT.csv'}
    files|={p for p in (V2/'charts/assets').iterdir() if p.is_file()}
    files|={p for p in (V2/'charts').glob('*-index.html')}
    files|={ROOT/'scrapper/tests'/n for n in ('test_subsector_ss03.py','test_subsector_ss04.py')}
    files|={ROOT/'artifacts/subsector-ss01/q2-2027-addendum-20261103'/n for n in ('q2-all-assessments.csv','SHA256SUMS')}
    files|={ROOT/'artifacts/subsector-ss04'/n for n in ('validation.json','SHA256SUMS')}
    sums=OUT/'SHA256SUMS';files.discard(sums)
    sums.write_text(''.join(f'{digest(p)}  {p.relative_to(ROOT)}\n' for p in sorted(files)));files.add(sums)
    path=OUT/'TECH-05-review.zip'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(files):
            name=str(p.relative_to(ROOT))
            if p.suffix in ('.sqlite3','.db') or p.name in ('.env','.screener_session','.screener_csrf'):raise ValueError('Unsafe review member')
            info=zipfile.ZipInfo(name,date_time=(2026,11,4,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            z.writestr(info,p.read_bytes(),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    with zipfile.ZipFile(path) as z:
        if z.testzip():raise ValueError('ZIP corruption')
    print(json.dumps(dict(archive=str(path.relative_to(ROOT)),files=len(files),bytes=path.stat().st_size,sha256=digest(path))))
    return path

if __name__=='__main__':outstanding();legacy_evidence();reports();package()
