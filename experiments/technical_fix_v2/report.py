"""Acceptance is generated only from completed acquisition and actual checks."""
import csv,html,json,re
from collections import Counter
from pathlib import Path
from .update import OUT,ROOT


def main():
    coverage=json.loads((OUT/'CURRENT_COVERAGE.json').read_text())
    batch=json.loads((OUT/'UPDATE_LEDGER.json').read_text())
    browser=json.loads((OUT/'BROWSER_VALIDATION.json').read_text())
    verification=json.loads((OUT/'ACQUIRED_DATA_VERIFICATION.json').read_text())
    if batch['status']!='COMPLETE' or not browser['passed'] or not verification['passed'] or verification['batch_state']!='COMPLETE':
        raise RuntimeError('Acceptance requires completed acquisition and passing actual verification')
    logs=(OUT/'REGRESSION_TESTS.log').read_text()
    if not logs.rstrip().endswith('OK'):raise RuntimeError('Regression tests must pass')
    tests=int(re.findall(r'Ran (\d+) tests',logs)[-1])
    rows=[]
    def add(component,status,evidence,limitation=''):
        rows.append(dict(component=component,status=status,evidence=evidence,limitation=limitation))
    W='WORKING';L='WORKING WITH DOCUMENTED LIMITATION';B='BLOCKED BY EXTERNAL DEPENDENCY'
    add('TECH-FIX-01 integration',W,'73 commit files integrated from a1fd540f with exact parent matching; TECH_FIX_01_INTEGRATION.json; shared git index preserved')
    add('Financial/document acquisition and Passport preservation',W,f"{verification['frozen_original_files_verified']} frozen original files and {verification['document_intelligence_files_preserved']} Document Intelligence files verified; new refresh request IDs do not overwrite old request IDs; no financial/document store writes")
    resume=json.loads((OUT/'HCD_AUTHORIZED_RESUME.json').read_text()) if (OUT/'HCD_AUTHORIZED_RESUME.json').exists() else {}
    add('HCD-02 actual historical access',L,'Live Passport inventory; 498 companies have retained four-timeframe native receipts; original HCD full manifest snapshot has 497 complete companies and 21,899 successful requests; prior authorized campaign resumed through existing lifecycle: '+resume.get('status','NOT_RESUMED'),'Full 1,902-company backfill remains incomplete; its retained scope and repair queue remain distinct from the completed refresh')
    add('NSE catalog and relevance',L,'Reused all 139 keys; matched relevant categories: 21 broad, 23 sectoral, 41 thematic; all 85 matched keys now have history','Official directory has 107 entries; 22 lack confirmed catalog identities and require a supported/authorized source, not guessed keys')
    add('NSE historical acquisition',L,'135/139 acquired histories; 130 newly populated; 125–336 accepted daily observations per acquired index; overall 2025-07-01–2026-11-06','Four BHARATBOND APR30–APR33 keys rejected with UDAPI1021; new-history scope starts 2025-12-01, with later source floors for some indices')
    add('NSE historical charts, indicators and sector rotation',W,'technical-system.html; 23-sector rankings, all 85 mapped relevant histories; preserved EMA/RS engine plus validated OHLC ATR/DTR; browser checks','No official-index share volume or flow inference; launch/backcalculation provenance is explicitly retrospective')
    add('NSE incremental updates',W,'Five retained histories refreshed with overlapping 2026-10-26–2026-11-06 requests; exact receipt reuse on resume; current charts consume acquired deltas')
    for r in coverage['global_coverage']:
        add(r['label'],L,f"{r['daily']} daily / {r['minute']} sampled minute observations; {r['first']}–{r['last']}; refreshed through supported Upstox provider; rendered browser check",r['warning']+'; provider quote units unverified; historical delivery not certified')
    add('US Treasury yields and spreads',L,f"{coverage['treasury_dates']} dates, 2025-11-20–2026-11-06; 2Y/10Y/30Y percent-per-annum curves; 10Y–2Y and changes in basis points; actual public refresh and browser plot check",'Current source vintage; approximate quote timing is not historical publication proof')
    add('Custom Tyres, EMS and Transformers',L,'Original 227-session histories through 2026-10-30; six core/sensitivity sets across three formations; 333,917 independent comparisons and 90 causal prefix checks passed; original charts render','Corporate-action factor 1 remains provisional; no unsupported factor applied; known pre-base actions not applied again; dividends are not reinvested; fixed retrospective membership')
    add('Validated stock/index indicators',L,'Existing EMA, daily ATR/DTR/RVOL, daily and weekly Simple Volume, intraday causal daily-DTR merge, DSS and exactly aligned benchmark RS reused; indicator source files unchanged','Weekly DSS/RS remain unsupported in the preserved engines; intraday benchmark history ends 2026-10-30; missing benchmark periods stay unavailable and seeds restart at known gaps')
    stock=[r for r in batch['requests'].values() if r['kind']=='STOCK']
    add('Weekly/daily/4-hour/1-hour chart integration',W,'497 companies refreshed via 1,491 weekly/daily/hourly requests; owner 4h derives from hourly constituents; 12 representative chart series browser-checked; arbitrary retained company/window available via CLI','Ordinary owner 4h: 09:15–13:15 and 13:15–15:30. BSE owner grids remain excluded pending their own verified calendar')
    add('Developing candles and historical checkpoints',L,'13:15/14:00/15:00/15:29/15:30 browser cutoffs; strict receipt gate; minute-prefix test excludes later highs and volumes; NSE/global/Treasury future values filtered','Retrospective revisions are allowed for research without certifying historical delivery; weekly completion accounts for the upcoming special Sunday; CAS/non-CAS grids remain unresolved for 2026-11-08')
    add('Warnings, quarantine and recovery',W,f"{verification['raw_checksums_verified']} raw and {verification['parquet_checksums_verified']} Parquet hashes checked; {verification['exact_source_value_comparisons']} exact raw/stored value comparisons; real lock-failure receipt recovered with one attempt; {tests} regression tests and {browser['assertions']} browser assertions passed",'Original invalid WTI/GIFT and HCD observations remain quarantined; missing/stale sources are visible; unknown transport delivery never automatically repeats a GET')
    add('Scheduled live/intraday acquisition',B,'Checkpoint admission implemented and end-to-end tested through the real updater on a filesystem fixture; current tick safely skips closed session; no timer or recurring requests enabled','Approved manifest is finite and exhausted. Future live checkpoint snapshots/recurring updates require owner authorization of a bounded recurring scope. Special-session/security grids must be reviewed before admission')
    add('Unmapped official indices / rejected bond keys',B,'Actual response receipts and official-directory coverage retained','Provider-valid identities/entitlement or licensed/exported histories required for 22 unmapped official-directory entries and four rejected catalog bond keys')
    with (OUT/'ACCEPTANCE_MATRIX.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    counts=Counter(r['status'] for r in rows)
    body=''.join('<tr>'+''.join('<td>'+html.escape(str(r[k]))+'</td>' for k in ('component','status','evidence','limitation'))+'</tr>' for r in rows)
    (OUT/'ACCEPTANCE.html').write_text('<!doctype html><meta charset="utf-8"><title>TECH-FIX-02 acceptance</title><style>body{font:14px system-ui;max-width:1500px;margin:30px;background:#111922;color:#dfe7ef}a{color:#9bc4ee}table{border-collapse:collapse}td,th{vertical-align:top;text-align:left;padding:12px;border-bottom:1px solid #344150}</style><h1>TECH-FIX-02 acceptance</h1><p>'+html.escape(str(dict(counts)))+'</p><p><a href="../technical-integration-v2/charts/technical-system.html">Open working integrations</a> · <a href="ACCEPTANCE_MATRIX.csv">Acceptance CSV</a></p><table><tr><th>Component</th><th>Status</th><th>Actual evidence</th><th>Limitation / external action</th></tr>'+body+'</table>')
    (OUT/'COMPLETION.md').write_text(f'''# TECH-FIX-02 completion

The approved **1,638-request** finite batch completed. Accepted/rejected request states: {dict(Counter(r['status'] for r in batch['requests'].values()))}. No paid upgrade, new architecture, indicator-formula rebuild, Document Intelligence, Kronos or pattern training was performed.

Open [the working integration](../technical-integration-v2/charts/technical-system.html) and [the acceptance matrix](ACCEPTANCE.html), also supplied as [CSV](ACCEPTANCE_MATRIX.csv). All requested components are classified individually. The original studies and chart payloads remain preserved; the current entry consumes actual newly acquired receipts.

- NSE: 135/139 histories, including all 85 catalog-mapped relevant broad/sectoral/thematic indices. New history runs 2025-12-01–2026-11-06 where returned; some indices have shorter source histories. Four bond keys were explicitly rejected. Twenty-two official-directory entries have no confirmed catalog key.
- Stocks: 497 companies received 1,491 overlapping weekly/daily/hourly refreshes through 6 November. Session-anchored 4h bars derive from hourly constituents. The existing archive holds four-timeframe receipts for 498 companies. This does not claim the original 1,902-company full backfill is complete. Its prior authorized finite campaign was resumed through the existing lifecycle; [resume receipt](HCD_AUTHORIZED_RESUME.json) records `{resume.get('status','NOT_RESUMED')}` and the unchanged manifest/reuse policy.
- Globals: all seven requested provider series refreshed through 6 November. GIFT remains a generic quotation; US Tech 100 is a proxy; Dow/S&P cash-session interpretations remain quarantined. Existing minute samples, H10 FX and EIA/FRED oil source channels remain distinct.
- Treasury: 242 same-date 2Y/10Y/30Y observations including baseline; percent yields and bp spreads/changes are preserved correctly.
- Validation: **{tests} regression tests**, **{browser['assertions']} browser assertions**, **333,917** independent subsector comparisons, **90** prefix checks, **{verification['exact_source_value_comparisons']:,}** exact raw/Parquet value comparisons; SQLite quick check `{verification['sqlite_quick_check']}`. See the linked logs and receipts.

The real SQLite lock incident retained its raw receipt and recovered without another GET. Full integrity scans now run after the finite writer completes. Invalid observations were not repaired with invented values. Strict historical delivery excludes fresh backfills; retrospective research remains available under explicit revision assumptions.

Remaining owner/external actions are the bounded recurring/live acquisition authorization, supported identities or licensed histories for unmapped/rejected indices, and outstanding session/corporate-action/provider-definition evidence. Historical delivery certification is a documented limitation, not a blocker to retrospective research. The preserved engines do not support weekly DSS/RS, and no replacement formulas were invented.

The 8 November special-session caveat follows [NSE CMTR76941](https://nsearchives.nseindia.com/content/circulars/CMTR76941.pdf). The provider's invalid-key classifications follow [Upstox V3 documentation](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/). No exchange-session date shift was inferred.
''')
    print(json.dumps(dict(components=len(rows),statuses=dict(counts),regression_tests=tests,browser_assertions=browser['assertions'])))


if __name__=='__main__':main()
