"""Write the acceptance record and bounded evidence needs from local results."""
import json, csv
from collections import Counter
from .repair import ROOT, OUT, T, A, read, csvout, save

def main():
    issues=[
      ('D01','FAILED','QUARANTINED;MAPPING_UNRESOLVED','Midnight IST provider bucket labels were interpretable as US cash dates; 8 Dow/9 S&P holiday labels contradict that interpretation.', 'Explicit cash-session quarantine in current metadata/screens; retain all 466 original observations without redating.', 'US_SESSION_RECONCILIATION.csv; evidence/US_REFERENCE_CHECKS.csv','Provider global bucket/underlying attestation plus independently licensed, dated history; no price-fitted shift.'),
      ('D02','SOURCE_FAILED;EXCLUSION_VERIFIED','SOURCE_FAILED;EXCLUSION_REVERIFIED','Two original OHLC constraint failures, not indicator errors.', 'No OHLC edits. Revalidate source hashes, quarantine and exclusion from accepted plots/checkpoints/counts.', 'evidence/SOURCE_REVALIDATION.csv; evidence/QUARANTINED_CANDLES.json; test_regressions.py','Only an authoritative separately versioned corrected observation can replace a rejection.'),
      ('D03','FAILED','REPAIRED','Old global rows survived beneath appended recovered context; sector page used the old context; status retained a contradictory body.', 'Remove superseded global rows while preserving distinct H10/NSE channels; sector bridge uses recovered context; derive current inventory/status from accepted data.', 'CURRENT_DATA_INVENTORY.json; CHART_VALIDATION.md; CHART_VALUE_RECONCILIATION.csv','Generic quotation only; historical reports remain evidence. Run offline refresh and tests after acquisitions.'),
      ('G01','PARTIAL','PARTIAL;METADATA_IMPROVED','Catalog keys and refresh clocks do not define denomination, underlying price construction or historical delivery.', 'Six exact provider definitions, original source paths/hashes, explicit unverified units and product limits.', 'GLOBAL_INSTRUMENT_DEFINITIONS.csv; evidence/EXTERNAL_SOURCE_REVIEW.md','Written provider unit, upstream source, session, correction/vintage and delivery attestation.'),
      ('G02','MISSING_134_OF_139','RECONCILED;7_REQUEST_PROPOSAL_UNEXECUTED','Catalog discovery was wider than retained history; 22 official identities also lack confirmed keys.', 'Reconcile 139 keys and 22 official unmatched entries; independently read raw linked histories; prioritize Focus/Blue crosswalk; reuse all five existing histories.', 'NSE_INDEX_COVERAGE.csv; PRIORITY_NSE_ACQUISITION_MANIFEST.json','Authorize only the seven exact URLs if desired; obtain per-index launch/backcast and full-history source evidence. No execution in TECH-FIX.'),
      ('G03','PARTIAL','PARTIAL;FAIL_CLOSED_GATE_ADDED','Historical retrieval and candle completion do not certify publication or delivery; daily global bucket completion remains ambiguous.', 'Audit full recovered and distinct legacy source ledgers; gate verified values on identity, completion, publication and actual historical delivery evidence.', 'HISTORICAL_AVAILABILITY_AUDIT.csv; policy.py; test_regressions.py','Zero decision-time-certified values. Retrospective fields must never become certified decision inputs.'),
      ('G04','PARTIAL','PARTIAL;17_INPUT_REVIEWS','Incomplete primary action and security records; provisional provider adjustment basis.', 'Review V1/V2 action evidence and original price discontinuities; preserve factors/formula; record 17 per-security gaps.', 'CORPORATE_ACTION_GAPS.csv; evidence/DISCONTINUITY_CANDIDATES.csv','Complete ex-date split/bonus/rights/demerger and identity/suspension archive plus provider adjustment attestation.'),
      ('G05','PARTIAL','PARTIAL;UNCHANGED','Original DSS/Simple Volume author sources and optional features incomplete.', 'Preserve reconstructed indicator mathematics; rerun independent arithmetic.', 'independent-recheck/evidence/RESULTS.json','Original-author startup/options parity remains a separate task.'),
      ('G06','PARTIAL','PARTIAL;UNCHANGED','Fixed-bin profile approximation and partial timeframe inventory.', 'Preserve existing approximation and timeframe limits; no rescaling or parity claims.', '../technical-audit-v1/evidence/PROFILE_AUDIT.csv; ../technical-audit-v1/evidence/TIMEFRAME_INVENTORY.csv','Exact SVP HD and complete multi-timeframe integration remain separate.'),
      ('G07','PARTIAL','PARTIAL;UNCHANGED','Full independent historical prices and action basis not certified.', 'Recheck 20 official NSE OHLC fields and retained cross-feed evidence; inspect limited public US reference closes without downloading a restricted history.', 'independent-recheck/evidence/RESULTS.json; evidence/US_REFERENCE_CHECKS.csv','One-date NSE parity and limited US comparisons do not establish full-history certification.'),
      ('G08','RETROSPECTIVE_ONLY','RETROSPECTIVE_ONLY;UNCHANGED','Approximate event dates, censored next-result boundaries and later-known membership.', 'Preserve outcome-chart labeling and prefix arithmetic; no strategy or causal claims.', '../subsector-index-v2/METHODOLOGY.md; independent-recheck/evidence/RESULTS.json','Historical event-publication knowledge remains unavailable.'),
    ]
    fields=['audit_issue','before','after','root_cause','change','verification','unresolved_evidence']
    csvout('DEFECT_RESOLUTION.csv',[dict(zip(fields,row)) for row in issues])
    ca=read(OUT/'CORPORATE_ACTION_GAPS.csv')
    save('SOURCE_EVIDENCE_REQUEST_MANIFEST.json',dict(task='TECH-FIX-01',authorized=False,executed_requests=0,
       automatic_retries=0,max_authorized_spend_usd=0,
       requests=[dict(id='GLOBAL-DEFINITION-01',provider='Upstox',instrument_keys=[r['instrument_key'] for r in read(OUT/'GLOBAL_INSTRUMENT_DEFINITIONS.csv')]+['GLOBAL_INDEX|^GSPC'],
         window_start='2025-12-01',window_end='2026-10-30',request_kind='PROVIDER_DEFINITION_ATTESTATION;NO_MARKET_API_CALL',
         required_fields=['upstream_quote_product','currency','denomination','spot_index_indicative_or_contract','global_daily_bucket_start_end_timezone','holiday_handling','historical_revisions','historical_publication_delivery'],
         anomaly_evidence='US_SESSION_RECONCILIATION.csv; evidence/QUARANTINED_CANDLES.json'),
         dict(id='US-REFERENCE-01',provider='S&P Dow Jones Indices / licensed distributor',series=['DJIA','SP500'],window_start='2025-12-01',window_end='2026-10-30',
         request_kind='RIGHTS_AND_DATED_REFERENCE_EVIDENCE;NO_DOWNLOAD_AUTHORIZED',required_fields=['daily_cash_session_OHLC','official_session_date','close_timestamp','correction_vintage','research_and_artifact_rights']),
         dict(id='CA-SECURITY-01',provider='NSE/issuers and Upstox adjustment attestation',securities=[dict(symbol=r['symbol'],instrument_key=r['instrument_key']) for r in ca],
         window_start='2025-12-01',window_end='2026-10-30',request_kind='BOUNDED_PRIMARY_ACTION_AND_SECURITY_ARCHIVE;NO_REQUEST_AUTHORIZED',
         required_fields=['split_bonus_rights_demerger_ex_dates_and_terms','issuer_ISIN_chain','listing_and_suspension_history','provider_adjusted_or_unadjusted_basis']),
         dict(id='NSE-PRIORITY-01',request_kind='EXACT_AUTHENTICATED_URLS_SEPARATELY_BOUNDED',manifest='PRIORITY_NSE_ACQUISITION_MANIFEST.json',max_requests=7)]))
    # Small public-page spot checks; neither downloaded full histories nor mappings.
    refs=[]
    retained=read(OUT/'US_SESSION_RECONCILIATION.csv')
    for series,day,value in [('DJIA','2026-10-29',52342.56),('DJIA','2026-10-30',51770.31),('SP500','2026-10-29',7720.39),('SP500','2026-10-30',7580.07)]:
        r=next(r for r in retained if r['series']==series and r['provider_bucket_date']==day)
        refs.append(dict(series=series,printed_date=day,provider_close=r['close'],independent_reference_close=value,
          difference=float(r['close'])-value,reference_url='https://fred.stlouisfed.org/series/'+series,
          reference_definition='S&P Dow Jones Indices daily cash close via FRED',
          evidence_form='PUBLIC_PAGE_VISIBLE_OBSERVATION_TRANSCRIBED_2026_11_05;NOT_RAW_API_BODY',
          outcome='DIFFERENT_SAME_DATE_VALUES;NO_DATE_SHIFT_OR_PRODUCT_IDENTITY_ESTABLISHED'))
    csvout('evidence/US_REFERENCE_CHECKS.csv',refs)
    states=Counter(r['acceptance_state'] for r in read(OUT/'HISTORICAL_AVAILABILITY_AUDIT.csv'))
    table='\n'.join('| '+i[0]+' | '+i[1]+' | '+i[2]+' |' for i in issues)
    (OUT/'TECHNICAL_ACCEPTANCE_STATUS.md').write_text(f'''# TECH-FIX-01 acceptance status

**Complete technical block: NOT ACCEPTED for Document Intelligence integration or historical decision-time use.** Bounded retrospective daily research remains usable under its documented limits. TECH-FIX-01 repair and validation scope is complete; no next phase is started.

D03 is repaired in the isolated current implementation. GIFT has **237 accepted daily / 13,216 sampled minute observations** for the provider generic `GLOBAL_INDEX|SGX NIFTY` quotation. No futures contract, expiry or historical delivery is certified. Two invalid raw daily candles remain quarantined and unchanged.

D01 is contained, not solved by redating: **227 Dow and 239 S&P original provider observations** retain their timestamps and prices. Eight Dow and nine S&P labels fall on official cash holidays. Their cash-session interpretation is explicitly quarantined. Four limited public reference checks also differ on the same printed dates; they do not justify a shift. Midnight IST maps to the preceding New York date at 13:30 standard time or 14:30 daylight time. A conceptual same-label US close is 02:30/01:30 IST the next day, after the assumed midnight bucket end. Neither timezone conversion nor that assumed bound identifies the actual product/session. Provider attestation and independently dated licensed history are still needed.

NSE reconciliation covers **139 catalog indices and 22 unmatched official-directory identities**. Five histories remain integrated, 134 unacquired; only two sectoral histories can be ranked. Existing histories are unchanged. The seven-request priority manifest is unauthorized and unexecuted. Counts are based on the retained catalog snapshot, not a claim about the provider's permanent coverage.

The complete timing ledger has **{sum(states.values()):,} source/checkpoint rows**: {states['RETROSPECTIVE_ONLY']:,} RETROSPECTIVE_ONLY, {states['CONSERVATIVE_TIME_BOUNDED']:,} CONSERVATIVE_TIME_BOUNDED, {states['UNAVAILABLE']:,} UNAVAILABLE, and zero VERIFIED_DECISION_TIME. Different source channels coexist; these are not distinct dates or independent observations. The conservative H10 references remain separate from provider FX quotes. All 2,838 nonempty global rows with explicit ends pass strict completion cutoffs; global daily ends remain provider bounds, not verified exchange completion. Current verified-value outputs fail closed on absent identity/publication/delivery evidence.

Seventeen constituent inputs retain provisional factor 1 and retrospective membership. Retained V1/V2 action records and original-price discontinuities were reviewed. KAYNES 14 May 2026 falls 20.1489% close-to-close; this is a review candidate, not an inferred corporate-action factor. Known pre-base PGEL/Shilchar/TARIL actions are not reapplied. No authoritative in-window correction was established. No index or indicator mathematics changed.

| Issue | Before TECH-FIX | After TECH-FIX |
| --- | --- | --- |
{table}

Validation: 10 targeted regression tests passed; 642,437 independent arithmetic/linkage checks had zero mismatches. Browser reconciliation has 933 rows, zero mismatches, zero JavaScript errors and zero external requests over 17 captured states. These checks reuse candles and are not statistically independent evidence. The original audit's immutable-all-files test now fails on intentional presentation repairs; its other 11 tests pass. The replacement preservation test verifies the exact seven-file change allowlist and every other frozen file.

Changes are isolated on `codex/tech-fix-01` in `/private/tmp/trading-ai-tech-fix-01`. The main checkout, HCD-01, Passport archive, migrations, schemas, production acquisition settings and original market responses were not modified. No credentials, authenticated requests, databases, ML or trading strategies were used. Baseline technical research is untracked in the original checkout; the review package supplies exact before hashes and a patch against that retained baseline, rather than merging or overwriting concurrent work.

See DEFECT_RESOLUTION.csv for the evidence still required for each issue, CHART_VALIDATION.md for actual screen checks, REGRESSION_TEST_RESULTS.md for reproducible commands, and PATCH_MANIFEST.json for the review delta. Stop here.
''')
    (OUT/'REGRESSION_TEST_RESULTS.md').write_text('''# Regression verification

10 targeted tests pass. They exercise future inventory increments, actual live counts, raw OHLC quarantine and exclusion, US holiday/session separation, DST, strict 09:15/18:00 boundaries, adversarial future values, historical delivery failure, complete NSE discovery versus history, corporate-action classification, and preservation of all files outside the seven reviewed presentation/source changes.

Run from the isolated worktree:

```sh
python3 -B -m experiments.technical_fix_v1.repair
python3 -B -m unittest experiments.technical_fix_v1.test_regressions -v
node experiments/technical_fix_v1/browser.mjs
python3 -B -m experiments.technical_fix_v1.reconcile
```

Chrome requires local GUI-process permission on this host. The harness uses its own temporary profile and blocks external HTTP/S chart requests. No API token is needed or read. Browser checks captured 17 states and 13 screenshots. Reconciliation reports 933 rows with zero mismatches; global plotted OHLC is additionally checked against the original response bodies, not just browser-provided metadata.

The independent audit equations were rerun without altering the audit evidence: import `artifacts/technical-audit-v1/code/audit.py`, set its `OUT` to `artifacts/technical-fix-v1/independent-recheck`, create `OUT/evidence`, and call `main()`. It imports no calculation implementation and produced 642,437 checks, zero mismatches. Its input hash manifest and results are retained. Its large detailed comparison CSV stays in the worktree and is omitted from the compact ZIP; the same command recreates it.

The original `test_audit.py` reports 11 passes and one expected legacy failure: `test_all_original_research_and_code_preserved` freezes all files, including the status/chart files this task explicitly repairs. Its original log is retained without editing the old audit test or baseline. The TECH-FIX replacement preservation test enumerates exactly the seven authorized changed paths; all other frozen research/raw/formula/acquisition files match. This is an explicit scope change, not an unreported green original suite.

Logs: evidence/REGRESSION_TESTS.log, evidence/ORIGINAL_AUDIT_TESTS.log, evidence/RECONCILIATION_RESULTS.json, independent-recheck/evidence/RESULTS.json. No broader production suite was run: production code/configuration was untouched.
''')
    (OUT/'CHART_VALIDATION.md').write_text('''# Current chart validation

Headless Chrome opened the actual local HTML using the existing local Lightweight Charts engine. Five stock/subsector pages, sector rotation, six daily global provider series and five sampled-minute states were captured: 17 states total. All external HTTP/S requests were blocked; zero attempts and zero JavaScript exceptions were recorded. Chart API series, selected-date readouts, controls, crosshairs, source paths, units and screenshots are retained in evidence/BROWSER_RESULTS.json and screenshots/.

Representative stock dates: original TIPSMUSIC 18 August 2026 and latest PGEL 11 August 2026. Subsector dates: Tyres 30 October, EMS 19 June and Transformers 10 March 2026. Sector rotation default 30 October retains Nifty Auto, Nifty 50 and MidSmallcap 400, with only the available sectors ranked. All six global daily series and USDINR/Brent/WTI/US Tech 100/GIFT sampled-minute plots were opened. The harness does not certify every possible date/control combination.

933 comparison rows match with zero mismatches. Global OHLC in actual plotted data is checked against original retained response rows. Sector rebasing is independently recomputed from retained closes. The separate independent equation rerun reconciles the original price and indicator/index inputs against payloads, so a browser/payload agreement alone is not the sole numerical evidence.

Current stock/subsector DOM checks require the derived GIFT coverage of 237 daily / 13,216 minute observations and reject superseded GIFT_NIFTY rows. Distinct H10/NSE channels remain. Sector context uses the same recovered source and removes obsolete generic GIFT rows; a missing minute window still displays UNAVAILABLE. Missing quote delivery is not described as missing acquired history. Generic GIFT contract/expiry status remains unverified.

Global axes retain provider IST bucket dates and unverified provider quote units. Original timestamps and source files remain inspectable; US cash-session interpretation is explicitly quarantined. No Nasdaq Composite label is applied to US Tech 100. Oil is not labeled verified spot/futures or USD/barrel. Treasury percent-per-annum yields and basis-point spreads retain their separate definitions. Rejected WTI 18 June and GIFT 19 July daily bars are absent; accepted minute observations at the same calendar date are distinct observations, not replacements.

All latest stock/subsector embedded chart-data payloads, their indicator engine and constituent arithmetic are byte-identical to baseline. Numeric warmups, missing fields and absent histogram classes remain missing. Prior reports and original chart versions remain historical evidence; only current TECH-06 presentation is repaired. The old status body is preserved separately as evidence/TECH06_STATUS_BEFORE.md.

Visual inspection of the global GIFT screenshot confirms generic identity, actual counts, retrospective labels, units, plotted sparse history and visible quarantine notes. The screenshots are bounded evidence; no complete historical product identity or decision-time certification is inferred from appearance.
''')
    (OUT/'evidence/EXTERNAL_SOURCE_REVIEW.md').write_text('''# Official-source review — 5 November 2026

[Upstox global instruments](https://upstox.com/developer/api-documentation/announcements/global-instruments/) establishes provider segments, instrument-key usage and API support effective 11 May 2026. Its latency field means refresh interval, not historical delivery. Reviewed documentation does not establish the six products' currency/denomination, upstream price construction, historical revision policy or cash-session bucket mapping. Keys/names are cross-checked against the retained 4 November catalog; no new catalog request was necessary.

[Upstox historical V3](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/) describes a candle timestamp as the interval start. Generic API historical availability limits are not per-instrument or historical publication certificates. The retained global daily responses start at midnight IST; their exchange-session aggregation is unresolved.

[NYSE calendar and hours](https://www.nyse.com/trade/hours-calendars) independently confirms the 2026 holiday dates and 16:00 New York core close. The in-window 2025 Christmas closure is also in retained official calendar evidence. ZoneInfo tests convert March/November DST transitions; no date shift or product mapping is inferred from those clocks. In-window regular cash closes use 16:00; post-window early closes are not projected into the data.

[FRED Dow](https://fred.stlouisfed.org/series/DJIA) and [FRED S&P](https://fred.stlouisfed.org/series/SP500) identify S&P Dow Jones Indices daily cash closes. Four visible 29/30 October observations were compared with retained provider values (US_REFERENCE_CHECKS.csv). Same-date differences neither prove a product identity nor identify an alternative session. No full reference history was downloaded. The pages' reproduction restrictions remain recorded in the earlier source review; licensed historical reference evidence is still unresolved.

Retained EIA spot and Federal Reserve noon-fixing comparisons remain separate products/times. No provider value was fitted to those references. No provider contact, authenticated acquisition or purchase occurred. Browser-visible observations here are explicitly transcribed page evidence, not retained raw API bodies or historical receipts.
''')
    print('Acceptance and review records written.')
if __name__=='__main__':main()
