"""Compact review package; no raw candle values or credentials enter ZIP."""
from collections import Counter
from datetime import datetime,timezone
import csv
import json
from pathlib import Path
import subprocess
import zipfile
from .storage import ROOT,OUT,dump,sha

def main():
    progress=json.loads((OUT/'pilot-verification-progress.json').read_text())
    scaling=json.loads((OUT/'full-universe-acquisition-plan.json').read_text())
    measured=json.loads((OUT/'storage-throughput-measurements.json').read_text())
    universe=json.loads((OUT/'universe-summary.json').read_text())
    pilot=json.loads((OUT/'pilot-companies.json').read_text())
    coverage=list(csv.DictReader((OUT/'historical-coverage-inventory.csv').open()))
    indicators=list(csv.DictReader((OUT/'indicator-compatibility.csv').open()))
    before=json.loads((OUT/'preservation-before.json').read_text())
    after={p:sha((ROOT/p).read_bytes()) for p in before}
    changed=[p for p in before if before[p]!=after[p]]
    dump(OUT/'preservation-result.json',dict(checked_files=len(before),changed_files=changed,passed=not changed))
    result=subprocess.run(['python3','-m','unittest','experiments.historical_candle_archive_v1.test_archive',
                           'experiments.historical_candle_archive_v1.test_identity','-v'],
                          capture_output=True,text=True)
    (OUT/'tests.log').write_text(result.stdout+result.stderr)
    if result.returncode: raise RuntimeError('tests failed')
    dump(OUT/'tests.json',dict(command='python3 -m unittest experiments.historical_candle_archive_v1.test_archive experiments.historical_candle_archive_v1.test_identity -v',
        exit_code=result.returncode,synthetic_safety_tests=25,
        real_data_boundary_checks=progress['boundary_sample'],
        raw_and_parquet_checksums='see storage-throughput-measurements.json',
        scope='Tests verify safety/causality contracts; data-validation failures remain explicit'))
    pilot_complete=progress['successful_requests']==progress['expected_requests'] and progress['companies_with_all_four_timeframes']==20
    status=dict(pilot_acquisition_complete=pilot_complete,pilot_acceptance='NOT_ACCEPTED_FOR_UNATTENDED_PRODUCTION_OR_HISTORICAL_PIT_BACKTESTS',
        full_universe_executed=False,schedules_enabled=False,document_intelligence_started=False,
        previous_technical_audit_modified=bool(changed),created_at=datetime.now(timezone.utc).isoformat(),
        blocking_gaps=['Provider OHLC constraints and cross-resolution/feed differences require review; source rows preserved',
            'Corporate-action adjustment conventions, complete delisting/suspension and historical capitalization evidence unresolved',
            'Calendar exception review partial; pre2022 sessions uncertified; NSE reference bhavcopy download failed',
            '120 native special-session grid differences retained; owner special-session bars require further minute evidence',
            'Weekly original DSS/RS/source integration remains unsupported by preserved engines',
            'Historical delivery receipts cannot be manufactured; reconstructed14h prefixes are not live observations',
            f'{universe["unresolved_companies"]} current eligible companies have unresolved provider identity; '
            f'{universe["supported_exact_bse_companies"]} BSE mappings require BSE-specific coverage/calendar validation'])
    dump(OUT/'HCD-01-status.json',status)
    lines=[
        '# HCD-01 — Passport historical candle archive',
        '',
        f'Pilot acquisition complete: **{pilot_complete}**. Production/PIT-backtest acceptance: **NOT ACCEPTED**. '
        'The pilot has been checked and its remaining evidence/data defects are explicit. No full-universe backfill or unattended schedule was enabled.',
        '',
        f'Passport was discovered and verified at `{measured["archive"]}` on external USB/APFS volume UUID '
        '`60E1204B-CB98-48C4-9398-1D2FBAC9B44A`. The initial write/read/fsync/delete probe passed; '
        '373,981,483,008 bytes were free. The scan found no existing market-data candidates. '
        'The existing document store and technical research were preserved.',
        '',
        f'The latest read-only acquisition-volume projection contains **{universe["eligible_canonical_companies"]}** '
        'unique canonical companies with observed market capitalization strictly above ₹580 crore. '
        'One source row labeled eligible at exactly ₹580 crore was excluded by the strict cutoff. '
        f'{universe["supported_exact_nse_companies"]} map to retained NSE identities and '
        f'{universe["supported_exact_bse_companies"]} to current BSE ISIN/scrip-code identities; '
        f'{universe["unresolved_companies"]} remain unresolved. Capitalization dates and source references are preserved. '
        'This is the latest acquired scope, not an assertion that every company was eligible in2022. '
        'The fixed pilot retains its dated historical eligibility; all-scope observations and security versions retain historical failures/delisted/suspended identities.',
        f'An additional **{universe.get("historically_eligible_additional_companies",0)}** companies with retained above-cutoff observations remain in the acquisition manifest despite absence from today\'s eligible set. '
        'Their historical eligibility dates and current/delisted/suspended evidence are separate. Exact identifier reconciliation uses existing identifiers and provider records; changed ISINs remain explicitly unresolved for historical continuity.',
        '',
        'Pilot: '+', '.join(c['symbol'] for c in pilot)+'.',
        '',
        f'{progress["successful_requests"]}/{progress["expected_requests"]} planned successful requests; '
        f'{progress["companies_with_all_four_timeframes"]}/20 companies have all four requested timeframes. '
        'Weekly/daily were requested from the documented January2000 provider floor; hourly/4h from January2022 through4November2026. '
        'The coverage CSV gives actual first/last observations and listing evidence per company/timeframe. Earlier listing history unsupported by the provider remains a gap.',
        '',
        f'The three-company five-session minute sample yields {dict(progress["boundary_sample"])}. '
        'Native1h/4h values agree with independent minute aggregation on that sample. '
        'Native and owner-session definitions remain separate. Ordinary4h owner bars end13:15 and15:30; '
        'hourly bars end10:15,11:15,12:15,13:15,14:15,15:15 and15:30. '
        'The derived owner-series covers ordinary regular sessions using exact session-grid native1h constituents, with traceable source hashes. '
        'Special-session owner bars remain an explicit coverage gap;120 native grid differences are listed in `special-session-boundary-gaps.csv`. '
        'Special-session grid differences, sparse days and source defects are retained and do not inherit sample-wide certification.',
        '',
        f'{progress["invalid_ohlc_rows"]} source OHLC violations were found; quarantined records retain original bytes. '
        f'{progress.get("ambiguous_daily_session_dates",0)} ambiguous daily session dates are also excluded rather than silently selecting a timestamp. '
        'Cross-resolution and retained TradingView price/volume comparison results are delivered separately. '
        'No price adjustment, volume rescaling, forward fill or invented trade was applied. '
        'Official NSE annual/special-session sources were reviewed through the web tool; direct circular/bhavcopy downloads timed out and their receipts are retained. '
        'The calendar is an explicit partial review, not a certified complete historical exchange calendar.',
        '',
        'Completed bars, provisional prefix reconstructions, quarantined source records and revisions have separate status/definition fields and Parquet partitions. '
        'A strict as-of selector requires receipt time at or before the decision and interval completion at or before the decision. '
        'The14:00 prefix excludes later minute observations. No historical live snapshot or delivery receipt is fabricated. '
        'All historical backfill observations have their actual2026 retrieval receipts, so they cannot enter earlier PIT simulations.',
        'The additional bounded live Kabra snapshot, when captured during the open session, is documented in `live-provisional-snapshot.json`. '
        'Its actual receipt time and available minute prefix are preserved; receipt-gated queries expose nothing before that receipt.',
        '',
        'EMA10/20, DTR/ATR, DSS, Simple Volume and RS compatibility calls reuse the existing modules unchanged. '
        f'Compatibility status counts: {dict(Counter(r["status"] for r in indicators))}. '
        'Weekly original DSS/RS/source integration and any failed strict alignment remain unresolved; no replacement indicator was written. '
        'The final compatibility pass uses explicitly bounded post2022 daily context; full-history seed parity is not asserted. The initial full-history contract failures remain in a separate CSV. '
        'Feature-check values are retained only on Passport.',
        '',
        f'Physical archive bytes: {measured["physical_bytes"]:,}; compressed raw evidence: {measured["raw_gzip_bytes"]:,}. '
        f'Indexed Parquet checksum validations: {measured["checksum_verified_files"]}. '
        'Storage timings identify their cache conditions. Raw data, exact-decimal Zstandard Parquet, source checksums and SQLite request/observation indexes all reside on Passport. '
        'Interrupted requests resume from saved raw evidence; completed hashes are skipped. '
        'The initial inventory insert defect and subsequent read/write lock contention were fixed; saved data were reused. '
        'Transport recovery is bounded and a single-writer lock prevents competing mutation passes.',
        '',
        'The disabled incremental schedule uses Asia/Kolkata and explicit session rows. It includes09:15, optional09:30,13:20,14:00,15:45 and final-week-session reconciliation. '
        'Additional13:17/14:17 hourly refreshes help a monitored subset; the rate-limit model does not promise two-minute freshness for the entire universe. '
        'The two-day Kabra overlap test and restart/idempotency evidence are in the delivered JSON/CSV records.',
        '',
        f'The deterministic mapped-universe manifest contains **{scaling["mapped_native_requests"]:,}** native requests. '
        f'Published-limit throughput lower bound: {scaling["theoretical_limit_hours"]:.2f} hours. '
        f'Serial pacing floor: {scaling["chosen_pacing_minimum_hours"]:.2f} hours; '
        f'active-cycle projection: {scaling["active_serial_projection_hours"]:.2f} hours; '
        f'gross measured pilot projection: {scaling["gross_measured_projection_hours"]:.2f} hours. '
        'These are conditional estimates for mapped identities; unsupported identities, calendar/corporate-action remediation, retries and additional provider evidence are explicit exclusions. '
        'Storage estimates use actual pilot bytes and a longest-pilot-history upper scenario, not a claimed measurement of unfetched stocks.',
        '',
        'Required before production acceptance: resolve source defects and adjustment policy; finish exchange calendars and historical security/membership evidence; '
        'resolve remaining provider mappings and BSE support; review unsupported indicator contexts; accept the pilot. '
        'A full-universe backfill additionally requires explicit owner approval. HCD-01 stops here.',
        '',
        'Start review with `HCD-01-status.json`, `historical-coverage-inventory.csv`, `candle-boundary-reconciliation.csv`, '
        '`independent-price-volume-comparison.json`, `indicator-compatibility.csv`, `storage-throughput-measurements.json` and `full-universe-acquisition-plan.json`.',
        '',
        '[Upstox V3 limits](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/) · '
        '[Upstox rate limits](https://upstox.com/developer/api-documentation/rate-limiting/) · '
        '[NSE market timings](https://www.nseindia.com/static/market-data/market-timings)',
    ]
    (OUT/'HCD-01-review.md').write_text('\n'.join(lines)+'\n')
    paths=[p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip' and p.name!='SHA256SUMS']
    code=[p for p in (ROOT/'experiments/historical_candle_archive_v1').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    (OUT/'SHA256SUMS').write_text(''.join(sha(p.read_bytes())+'  '+str(p.relative_to(ROOT))+'\n' for p in sorted(paths+code)))
    with zipfile.ZipFile(OUT/'HCD-01-review.zip','w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(paths+code+[OUT/'SHA256SUMS']): z.write(p,p.relative_to(ROOT))
    print(json.dumps(dict(status=status,review_zip_bytes=(OUT/'HCD-01-review.zip').stat().st_size),indent=2))

if __name__=='__main__':main()
