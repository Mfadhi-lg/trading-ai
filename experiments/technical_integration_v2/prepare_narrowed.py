"""Prepare the owner's reduced TECH-06 manifest offline; never acquire data.

Run with python3 -m experiments.technical_integration_v2.prepare_narrowed.
Keep the original broad package intact as evidence. Do not run the broad build
to execute this scope: it regenerates the superseded acquisition proposals.
"""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'artifacts/technical-integration-v2'
OUT = BASE / 'narrowed-global'
NUMBERS = [1, 2, 3, 5, *range(6, 15), 18, 19, 20]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, obj):
    (OUT / name).write_text(json.dumps(obj, indent=2) + '\n')


def main():
    if (OUT / 'EXECUTION_LEDGER.json').exists():
        raise SystemExit('Executed scope is immutable; do not overwrite its manifest or preservation baseline.')
    original = BASE / 'UPSTOX_DIAGNOSTIC_MANIFEST.json'
    source = json.loads(original.read_text())
    assert source['request_count'] == len(source['requests']) == 28
    selected = [r for r in source['requests'] if r['number'] in NUMBERS]
    assert [r['number'] for r in selected] == NUMBERS
    assert len({r['url'] for r in selected}) == 16
    assert [r['number'] for r in selected if r['unit'] == 'days'] == [1, 2, 3, 5]
    assert sum(r['unit'] == 'minutes' for r in selected) == 12
    assert {r['series'] for r in selected} == {'USDINR', 'BRENT', 'WTI', 'US_TECH100'}
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {
        'task': 'TECH-06-NARROWED-GLOBAL',
        'status': 'PENDING_EXPLICIT_OWNER_AUTHORIZATION;NOT_EXECUTED',
        'original_manifest': str(original.relative_to(ROOT)),
        'original_manifest_sha256': sha(original),
        'metadata': source['metadata'],
        'request_count': 16,
        'approved_request_count': 0,
        'original_request_numbers': NUMBERS,
        'daily_first': [1, 2, 3, 5],
        'minute_second': NUMBERS[4:],
        'automatic_retries': 0,
        'max_authorized_spend_usd': 0,
        'cost_basis': source['cost_basis'],
        'execution_policy': {
            'authorization': 'Explicit owner approval of this reduced manifest required before credentials or historical GETs; record approval reference and manifest SHA256 separately.',
            'pacing': 'Serial GETs, at least one second apart; maximum 16 attempts, each original number at most once; no redirects, retries, expansions or substitutions.',
            'phases': 'Complete four daily tests first unless a global stop occurs; then process twelve proposed minute windows subject to per-series stops.',
            'series_stop': 'Explicit provider unsupported-instrument response stops all later requests for that key. Explicit unsupported interval stops later requests for that key and interval only. Continue independent supported series.',
            'empty_response': 'Record zero coverage for this window; no invented bars. Empty alone does not prove instrument-wide non-support or authorize changed dates.',
            'global_stop': '401, 403, 429, transport failure, unexpected HTTP or malformed schema: stop run with no retry; do not misclassify entitlement or transient failure as unsupported series.',
            'retention': 'Retain successful raw response bodies, receipt UTC, hash, request number and validation. For errors retain sanitized status/code/message and body hash, never credentials or headers.',
            'skips': 'Record every unattempted number and reason; 16 is an upper bound, not a promise of 16 calls.'
        },
        'validation_requirements': [
            'Validate finite OHLC, OHLC consistency, volume/open-interest semantics, duplicates/conflicts, ordering, offsets, interval alignment and requested-range membership; quarantine malformed rows.',
            'Report actual first/last candle, unique bars, observed dates, expected-session basis, missing sessions/windows and returned-versus-usable counts separately. Sample minute windows do not establish quarterly coverage.',
            'Keep exact provider candle timestamps; distinguish start, interval end, session date, receipt time and historical publication/availability. Convert with Asia/Kolkata and America/New_York, including DST.',
            'Verify price units independently: catalog price_quote_unit is blank. Do not infer spot/futures/CFD identity, USD per barrel or contract expiry from a symbol alone.',
            'For 09:15 IST checks require a completed bar strictly before cutoff and supporting historical availability evidence. A candle starting before cutoff or ending at cutoff is insufficient; refresh cadence is not delivery latency.',
            'If publication/receipt evidence is absent, label temporal eligibility unverified and keep descriptive observations out of validated preopen features. No forward fill or invented observations.',
            'US Tech 100 retains its provider name; never label it Nasdaq Composite, NQ or actual Nasdaq futures.',
            'Integrate only recovered validated context with source and availability labels; preserve validated stock indicator and subsector index payloads and formulas byte-for-byte.'
        ],
        'excluded': ['GIFT Nifty', 'S&P 500/Dow diagnostics', 'all 134 NSE index requests', 'NSE-sector discovery', 'TECH-05', 'Document Intelligence', 'ML'],
        'external_acquisition_authorized': False,
        'stop_after': 'Bounded recovered-data integration, actual coverage/gap report and compact review ZIP.',
        'requests': selected,
    }
    write('UPSTOX_REDUCED_MANIFEST.json', manifest)
    manifest_path = OUT / 'UPSTOX_REDUCED_MANIFEST.json'
    (OUT / 'UPSTOX_REDUCED_MANIFEST.sha256').write_text(sha(manifest_path) + '  UPSTOX_REDUCED_MANIFEST.json\n')
    preserved = sorted((BASE / 'charts').rglob('*'))
    preserved = [p for p in preserved if p.is_file()]
    # Existing indicator implementations and retained payloads are also frozen.
    for directory in ('experiments/technical_integration_v1', 'experiments/technical_integration_v2'):
        preserved.extend(sorted((ROOT / directory).glob('*.py')))
    preserved.append(ROOT / 'experiments/earnings_reaction_v1/indicators.py')
    preserved = [p for p in preserved if p != Path(__file__).resolve()]
    write('PRESERVATION_BASELINE.json', {str(p.relative_to(ROOT)): sha(p) for p in preserved})
    checks = {
        'status': 'PREPARATION_VALIDATED;EXECUTION_PENDING_OWNER_AUTHORIZATION',
        'manifest_sha256': sha(manifest_path),
        'exact_original_request_records': all(r == next(x for x in source['requests'] if x['number'] == r['number']) for r in selected),
        'daily_requests': 4, 'minute_requests': 12, 'nse_requests': 0,
        'authenticated_requests_executed': 0,
        'new_candles_recovered': 0,
        'historical_coverage': 'NOT_TESTED_BY_THIS_REDUCED_SCOPE',
        'integration_status': 'PENDING_AUTHORIZED_ACQUISITION_AND_VALIDATION',
        'preserved_files': len(preserved),
        'preservation_verified': all(sha(ROOT / p) == digest for p, digest in json.loads((OUT / 'PRESERVATION_BASELINE.json').read_text()).items()),
    }
    assert checks['exact_original_request_records'] and checks['preservation_verified']
    write('PREPARATION_VALIDATION.json', checks)
    (OUT / 'README.md').write_text('''# Narrowed TECH-06 authorization package

Status: prepared; awaiting explicit owner authorization. No historical requests executed and no new data integrated.

Approve only UPSTOX_REDUCED_MANIFEST.json, identified by its SHA256. Original request numbers 1, 2, 3, 5, 6–14 and 18–20 are retained verbatim, including URLs, keys, dates, identity records and reasons. The 28-request original remains untouched as provenance, not an execution target. Do not execute its broad build/acquisition proposals or the separate NSE manifest.

Daily histories: 1/2/3/5, 2025-12-01 through 2026-10-30. Minute windows for each of the same four series: 2026-03-06–10, 2026-06-15–19 and 2026-10-26–30. Daily tests precede minute tests. Unsupported keys/intervals stop individually; authentication, entitlement, throttling, transport and unexpected protocol failures stop the run. Skipped requests must remain visible in the execution ledger.

The existing retained global catalog has no verified exact Nasdaq Composite or CME ES/NQ contract identity. US Tech 100 is only US Tech 100; US 30 is not ES/NQ. This is a snapshot finding, not a permanent provider-wide absence claim. No guessed key is authorized. After approval, confirm provider documentation and record the minimum legitimate alternative and applicable cost/licensing: exact Nasdaq Composite daily closing index values from Nasdaq or its authorized distributor (FRED NASDAQCOM is a candidate subject to its permission terms); contract-specific ES/NQ history from CME DataMine or licensed CME vendor such as Databento GLBX.MDP3. Historical trades or quotes, contract definitions, event/capture timestamps, same-contract baselines and use/distribution rights are required. Exact price and rights remain unverified in this preparation; no paid estimate, purchase or external data download is authorized.

Do not treat lack of API testing as confirmed non-support. Actual usable coverage, timestamp semantics, units and historical preopen eligibility remain pending. Successful sample minutes alone cannot fill complete-quarter gaps. Blank unit metadata or missing historical availability evidence must be reported explicitly. The manifest records the acceptance rules for subsequent validation and chart integration.

PRESERVATION_BASELINE.json freezes existing chart assets and technical code. No validated stock indicators or subsector indices were changed. After authorized acquisition, verify those hashes (allowing only documented context/chart presentation additions), package evidence and the bounded integration, and STOP. NSE discovery, TECH-05, Document Intelligence and ML are excluded.

Reproduce preparation offline with `python3 -m experiments.technical_integration_v2.prepare_narrowed`. Reproduction does not authorize execution.
''')
    files = sorted(p for p in OUT.iterdir() if p.is_file() and p.suffix != '.zip' and p.name != 'SHA256SUMS')
    (OUT / 'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in files))
    archive = OUT / 'TECH-06-narrowed-preauthorization-review.zip'
    with ZipFile(archive, 'w', ZIP_DEFLATED) as z:
        for p in files + [OUT / 'SHA256SUMS']:
            z.write(p, p.name)
        z.write(Path(__file__), 'code/prepare_narrowed.py')
    with ZipFile(archive) as z:
        assert z.testzip() is None
    print(json.dumps({**checks, 'review_zip': str(archive.relative_to(ROOT)), 'zip_bytes': archive.stat().st_size}, indent=2))


if __name__ == '__main__':
    main()
