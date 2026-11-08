"""Write the bounded completion report and unexecuted external-source proposal."""
import csv
import json
from .prepare_narrowed import ROOT, OUT, BASE, sha
from .execute_narrowed import save


def read(name):
    with (OUT / name).open() as stream:
        return list(csv.DictReader(stream))


def main():
    summary = json.loads((OUT / 'RESULT_SUMMARY.json').read_text())
    components = summary['components']
    gift = next(c for c in components if c['series'] == 'GIFT_NIFTY')
    gift_done = summary.get('gift_executed_requests') == 4
    with (BASE / 'FUTURES_REQUEST_WINDOWS.csv').open() as stream:
        windows = [r for r in csv.DictReader(stream) if r['series'] in ('ES', 'NQ')]
    path = OUT / 'additional/ES_NQ_QUOTE_WINDOWS.csv'
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(windows[0]))
        writer.writeheader(); writer.writerows(windows)
    save(OUT / 'additional/PENDING_EXTERNAL_SOURCE_PLAN.json', dict(
        task='TECH-06-EXACT-NASDAQ-AND-US-FUTURES', status='NOT_AUTHORIZED;NOT_EXECUTED',
        approved_authenticated_requests=0, approved_paid_requests=0,
        max_authorized_spend_usd=0,
        es_nq=dict(provider_candidates=['Databento GLBX.MDP3', 'CME DataMine'],
            definition='Actual ES and NQ outright contracts, expiry/price definition verified, trades with exchange-event and capture timestamps; top-of-book only if quote features require it.',
            study_dates=['2025-12-01', '2026-10-30'], indian_checkpoints=227,
            quote_windows_file=str(path.relative_to(ROOT)), quote_windows_sha256=sha(path), quote_window_count=len(windows),
            requested_history_status='QUOTE_SCOPE_ONLY;NO_DOWNLOAD_AUTHORIZATION',
            dependencies='Definitions plus same-contract previous-checkpoint/pre-study baselines and roll verification must be included in a full quote; no unbounded download authorized.',
            cost_usd=None, cost_status='EXACT_FULL_SCOPE_QUOTE_REQUIRED',
            licensing='Confirm dataset-specific internal-use and exported-chart/ZIP redistribution rights before purchase.',
            estimate_stage=dict(status='PENDING_EXPLICIT_AUTHORIZATION', request_count=1,
                method='POST', endpoint='https://hist.databento.com/v0/metadata.get_cost',
                parameters=dict(dataset='GLBX.MDP3', schema='trades', symbols=['ES.FUT', 'NQ.FUT'],
                    stype_in='parent', start='2026-03-10T03:30:00Z', end='2026-03-10T03:45:00Z'),
                note='One sample estimate is not the full study price or permission to download; parent roots include multiple contracts and require definitions/filtering. This is an unexecuted proposal, not a verified current endpoint contract.')),
        nasdaq_composite=dict(status='SOURCE_RIGHTS_AND_PUBLISH_TIME_BLOCKER;NO_EXECUTABLE_DOWNLOAD_REQUEST',
            exact_candidate='Nasdaq Composite daily price-index close, FRED NASDAQCOM or authorized Nasdaq distributor',
            source_page='https://fred.stlouisfed.org/series/NASDAQCOM',
            from_date='2025-11-28', to_date='2026-10-30',
            required_fields=['US session date', 'unadjusted closing price-index value', 'publication timestamp or vintage evidence', 'research/chart/ZIP use rights'],
            cost_usd=None, cost_status='PUBLIC_VIEWABLE_PAGE_DOES_NOT_ESTABLISH_REPRODUCTION_LICENSE_COST',
            prerequisite='Obtain permission/terms and exact delivery product before preparing an executable licensed download manifest.'),
        dow=dict(new_requests=0, action='Reuse retained response; seek definition/session/publication attestation if decision-time certification is needed.'),
        treasury=dict(new_authenticated_requests=0, new_paid_requests=0, public_retrieval_complete=True,
            remaining_requirement='Archived original releases or timestamped historical captures/vintages; a release schedule is insufficient.'),
        gift_manifest='PENDING_GIFT_MANIFEST.json',
        gift_execution_status='COMPLETED_SEPARATELY_AUTHORIZED_FOUR_REQUESTS' if gift_done else 'PENDING_AUTHORIZATION',
        excluded=['all NSE index acquisition', 'technical-indicator development', 'subsector classification', 'TECH-05 restart', 'Document Intelligence', 'ML']))
    lines = [
        '# TECH-06 bounded execution and supported integration', '',
        'The owner-approved reduced manifest completed once: 16 successful HTTP 200 requests, four daily first and then twelve minute windows. No request was modified or repeated, no retries occurred, and no unsupported series was reported. The subsequent additional-indicator instruction was handled without changing this completed run. No GIFT, Dow, Treasury or external-vendor authenticated request was added to it.', '',
        'Scope identity: `ad779a7b5b26ea5bd0e3854ef7bdf04a8be6ea06d493eef17b9768117b737686`. Original request numbers: 1, 2, 3, 5, 6–14, 18–20. All exact URLs, keys, dates and identity records match the original proposal. Successful raw bodies, receipt UTC and SHA256 are retained in EXECUTION_LEDGER.json. The original broad manifest is preserved as evidence and is not an execution target.', '',
        '## Actual usable coverage', '',
        '- Original 16-request scope daily bars: **991 returned**, **990 accepted**. One WTI daily bar is quarantined: 18 June 2026, open 74.88, high 76.06, low 72.83, close 76.58. The close exceeds the high; no field was repaired or substituted. Separate GIFT coverage is reported above.',
        '- Original 16-request scope minute bars: **41,238**, all structurally accepted. They cover only 6–10 March, 15–19 June and 26–30 October 2026. These are capability samples, not complete-quarter intraday histories.',
        '- Retained Dow: **227** daily bars reused; no new Dow acquisition. Eight provider labels fall on non-US-cash-session dates. The newly acquired US Tech 100 history has nine such labels. All affected series remain quarantined for exchange-session/as-of interpretation; there is no guessed date shift.',
        '- Official Treasury: **237** complete same-date 2Y/10Y/30Y observation sets from 20 November 2025 through 30 October 2026: **231 study dates** and six baseline dates. The spread and bp changes are calculated from these observations.',
        f"- **227 Indian sessions**, two checkpoints each: pre-open strictly before **09:15 IST**, and the existing post-close convention strictly before **18:00 IST**. The checkpoint ledger has {summary['checkpoint_rows']:,} rows across all required components and source channels.",
        '- **Zero historical delivery-verified pre-open or post-close values** were established in this new block. Accepted numeric candles are usable as labeled retrospective provider quotes; their units/underlying definition remain unverified. Treasury units and yield definitions are verified, but publication/vintage evidence remains insufficient for historical decision-time certification.', '',
        'Daily numeric coverage by exact provider series: USD/INR 250, Brent 251, WTI 250 accepted of 251 returned, US Tech 100 239. All four reach 1 December 2025–30 October 2026. Retained Dow reaches the same endpoints. These counts are provider date buckets, not a claim of that many verified exchange sessions.', '',
        'Accepted minute totals: USD/INR 6,350; Brent 14,484; WTI 15,533; US Tech 100 4,871. Across the 13 Indian sessions in the sampled windows, completed-bar candidates exist at 13 pre-open/post-close checkpoints for each of FX/Brent/WTI and 11 for US Tech 100. The latter has no earlier completed bar within the June 15 and October 26 windows at either checkpoint. On March 9 its candidate is the March 7 01:28 IST bar, more than two days old; it is explicitly stale. None of these candidates has verified historical delivery.', '',
        'No minute values are carried into unrequested dates such as August. Missing minutes, boundary gaps and holidays are not synthesized. CANDLE_DATE_GAPS.csv and MINUTE_AND_DAILY_COVERAGE_BY_DATE.csv record actual observed dates, absent calendar dates and absent minute-grid positions. An absent grid position does not prove a missing transaction: sparse quotes, closures and missing provider data cannot be separated without a verified instrument/session specification. WTI/US Tech 100 March samples contain no March 8 date bucket; this is recorded, not filled. Full-quarter intraday coverage remains a gap requiring a new separately authorized scope.', '',
        '## Component status', '',
    ]
    for c in components:
        lines.append(f"- **{c['label']}: {c['status']}** — {c['accepted_daily_observations']} accepted daily observations; {c['accepted_minute_observations']} sampled minute observations. {c['limitation']}")
    lines.extend(['', 'A PARTIAL status means supported data has been integrated with its limitations visible. It does not assert full instrument/session/decision-time certification. No component is marked fully INTEGRATED while required verification is missing. Exact Nasdaq Composite and NQ are reported independently from the recovered US Tech 100 provider series.', '',
        '## Time, units and calculations', '',
        'Original timestamps and source bodies are preserved; normalized UTC, Asia/Kolkata and America/New_York times are exported. All accepted candle timestamps have explicit +05:30 offsets and the provider returned descending order. A one-minute candle is represented by its start and exclusive end. A bar ending exactly at a checkpoint is excluded. Daily midnight labels identify provider buckets, not verified market closes. The prior completed provider bucket is only a dated retrospective reference; its end is never promoted to a historical publication timestamp.', '',
        'All acquired candle volume/open-interest fields are zero. No volume indicator or institutional-activity interpretation is built from them. The Upstox catalog leaves quote units and upstream definitions blank, so no unsupported USD/barrel, spot, CFD, currency-denomination or futures-expiry assertion is added.', '',
        'Treasury nominal par yields are percent per annum on a bond-equivalent basis. Yield changes are `100 × (yield_percent − previous_source_yield_percent)` bp. The spread is `100 × (10Y_percent − 2Y_percent)` bp using both tenors on the same source date. Missing tenors would leave the derived value missing. Actual source observation dates and change-base dates are retained. The 30 October spread is 38 bp, versus 37 bp on 29 October; this source observation is after both Indian 30 October checkpoints and is excluded from them.', '',
        'Treasury indicative quote timing near 15:30 New York is recorded as an approximate convention, not an exact observation or publication timestamp. DST conversion gives 01:00 IST the following day in daylight time and 02:00 in standard time. Atom updated values are current-feed metadata. The present H.15 16:15 release schedule does not certify historical publication of each Treasury observation. All current-vintage Treasury values and changes remain retrospective, with source hashes/receipt times available in the ledger.', '',
        '## Chart integration and preservation', '',
        'Open ../charts/global-market.html for provider daily/minute history, official Treasury yields, the bp spread, individual component statuses and selected Indian checkpoints. The same recovered block appears in all 15 existing stock/subsector charts and follows their selected date. Source body/hash, observed date, age, unit status, retrospective value, unavailable verified value, change base and missing-data reason remain inspectable. Descriptive provider direction is the change versus the preceding accepted bucket of that exact series; it is not a certified exchange-session return or a Nasdaq Composite signal.', '',
        'The 59-file pre-execution baseline was checked. Existing technical code, chart engines, indicator values, subsector index rows and pre-existing assets are unchanged. Chart HTML only adds presentation scripts before the existing chart engine; the original bytes are reconstructable by removing those exact tags. The chart index adds a global-market link. No broad build, NSE discovery, TECH-05 acquisition, indicator development, subsector classification, Document Intelligence or ML was started.', '',
        '## Remaining authorization and evidence blockers', '',
        '1. **GIFT generic quotation:** the owner separately authorized the four exact requests in additional/PENDING_GIFT_MANIFEST.json, SHA256 `64a0b961a8eb29015026844d4de1ee895137fe29abff7c46e5ea24c088fffef8`. All four completed once. The immutable proposal retains its preparation-time status; GIFT_OWNER_AUTHORIZATION.json and GIFT_EXECUTION_LEDGER.json record the subsequent approval/execution. No identified futures expiry or historical delivery certification was recovered.' if gift_done else '1. **GIFT generic quotation:** four requests require separate authorization.',
        '2. **Exact Nasdaq Composite:** no verified Upstox equivalent. FRED NASDAQCOM is an exact daily source candidate with copyrighted/pre-approval-required terms. Permission or licensed-distributor terms for research, charts and ZIP exports, plus a precise delivery product, are needed before an executable licensed acquisition manifest. No purchase or external contact was made.',
        '3. **Genuine ES/NQ:** no verified Upstox contract equivalent. Minimum alternative: CME DataMine or Databento GLBX.MDP3 contract definitions and bounded trades/event+capture timestamps. additional/PENDING_EXTERNAL_SOURCE_PLAN.json filters the existing proposal to 908 ES/NQ checkpoint windows, with baseline/roll dependencies disclosed. Its single sample cost-estimate proposal also requires authorization and is not a full-study quote or download approval. Exact price and intended-use/redistribution rights remain unknown. No authenticated estimate or paid retrieval occurred.',
        '4. **Historical availability/definition evidence:** dated original releases/captures and provider unit/session definitions are needed to promote current retrospective observations. These are evidence gaps, not permission to infer publication times or change instrument identities.', '',
        'Public Treasury retrieval was a separate two-GET, zero-payment manifest permitted by the additional-indicator instruction. It required no credentials. No paid upgrade or chargeable external acquisition was authorized. Existing Upstox subscription/entitlement billing was not audited; successful access is not a claim that the account has no existing fees.', '',
        'See SOURCE_AND_LICENSE_REVIEW.md for primary source links and minimum legitimate alternatives. Tests and BROWSER_VALIDATION.json record the cutoff, DST, arithmetic, quarantine, preservation and offline browser checks. The compact review ZIP includes supported charts, source evidence, exact manifests, ledgers and replayable offline integration code. Acquisition runners are one-shot and must not be rerun.', '',
        '**STOP:** supported integration, including the separately approved GIFT quotations, is complete. Exact Nasdaq/US-futures acquisitions and any expanded minute history remain unexecuted pending their separate authorizations/rights.', ''])
    if gift_done:
        lines[5:5] = [f"The owner subsequently approved the separate four-request GIFT quotation manifest. Original numbers 4, 15, 16 and 17 completed once, daily first and then three minute windows: 238 daily bars returned, **{gift['accepted_daily_observations']} accepted**, and **{gift['accepted_minute_observations']:,} minute bars accepted**. One daily bar, 19 July 2026, is quarantined because open 24,373.5 exceeds high 24,371.5. There were no retries or skipped requests. The original 16-request ledger, its raw bodies and Treasury sources remain byte-for-byte unchanged against GIFT_PREEXECUTION_BASELINE.json. This brings accepted new quotation history to **1,227 daily bars and 54,454 minute bars**, with two quarantined daily bars overall. Retained Dow remains additional to those totals.", '',
            'GIFT is **PARTIAL**: the generic quotation is integrated into daily/minute history and both checkpoint channels. Completed minute candidates exist on all 13 Indian sessions sampled at both checkpoints; none has verified historical delivery. Current catalog units and exact contract/expiry are unverified. No GIFT quote is promoted to a specific futures contract. Nasdaq Composite, ES and NQ remain UNAVAILABLE. Treasury, Dow and the original four provider series retain their earlier PARTIAL classifications.', '']
    (OUT / 'EXECUTION_REPORT.md').write_text('\n'.join(lines))
    (OUT / 'README.md').write_text('''# Completed bounded TECH-06 global integration

Read EXECUTION_REPORT.md and SOURCE_AND_LICENSE_REVIEW.md. Open ../charts/global-market.html or any existing stock/subsector chart. The approved 16 Upstox GETs and separately approved four GIFT GETs completed once, without retries. Supported Treasury/Dow/GIFT integration is complete. Exact Nasdaq Composite/ES/NQ rights and acquisition blockers remain explicit.

Rebuild only the supported offline integration: `python3 -m experiments.technical_integration_v2.build_narrowed`. Then run `python3 -m unittest experiments.technical_integration_v2.test_narrowed -v` and, if installed Chrome/Node are available, `node experiments/technical_integration_v2/browser_narrowed.mjs`. Rebuilding is local and does not retrieve data or read credentials.

Do not run the older broad build or acquisition proposals. Do not rerun prepare_narrowed, execute_narrowed, execute_gift, supplemental preparation or public retrieval: the approved manifests, pre-execution baselines and attempt ledgers are immutable. The GIFT proposal's original pending status is historical; its separate owner authorization and completed execution ledger are current. The external-source plan is NOT approved and does not authorize a download.

The review archive preserves repository-relative paths. After extracting, open artifacts/technical-integration-v2/charts/global-market.html. Original chart bytes can be reconstructed for preservation checking by removing the exact presentation additions recorded by the offline integration code. Broader historic reports/manifests remain provenance, not current execution instructions.
''')
    banner = '''> Current TECH-06 result: the reduced 16-request scope and separate four-request GIFT quotation scope completed once, with supported Dow/Treasury integration. Open [global market charts](charts/global-market.html) and [bounded execution report](narrowed-global/EXECUTION_REPORT.md). Exact Nasdaq Composite/ES/NQ alternatives remain blocked on rights/acquisition terms. Earlier broad 28/134-request proposals and the older TECH-06-review.zip are superseded for execution; do not run them.\n\n'''
    for name in ('README.md', 'TECHNICAL_BLOCK_STATUS.md'):
        path = BASE / name
        text = path.read_text()
        if text.startswith('> Current TECH-06 result:'):
            text = text.split('\n\n', 1)[1]
        if not text.startswith(banner):
            path.write_text(banner + text)
    print(json.dumps(dict(report=str((OUT/'EXECUTION_REPORT.md').relative_to(ROOT)), pending_gift_requests=0 if gift_done else 4, external_quote_only_windows=len(windows))))


if __name__ == '__main__':
    main()
