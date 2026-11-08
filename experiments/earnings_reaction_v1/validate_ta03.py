"""Offline TA-03R validation; preserves TA-03 evidence and uses retained inputs only.

Run: python3 -B -m experiments.earnings_reaction_v1.validate_ta03
"""
from dataclasses import asdict, fields
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .indicators import VERSION, SourceDailyIndicators, calculate_daily, calculate_daily_research_proxies
from .upstox_candles import REQUESTS, parse_response

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'artifacts/earnings-reaction-v1/ta02b'
OUT = ROOT/'artifacts/earnings-reaction-v1/ta03r'
PINE = {
    'dtr_atr_rvol.pine': 'db62c588091b39fbbeb5c2aa5d8ebe902c63fc0905343514750c11660f27f116',
    'relative_strength.pine': '374479bf8badbfc88988c600263585f73b9c9e2dbfa63c45c45ab2f3211a909f',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(actual, expected):
    if expected is None:
        if actual is not None:
            raise ValueError('expected null')
    elif actual is None or abs(Fraction(actual)-expected) >= Fraction(1, 10**20):
        raise ValueError(f'independent arithmetic mismatch: {actual} vs {expected}')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pine = []
    for name, digest in PINE.items():
        for path in (ROOT/name, OUT/'sources'/name):
            if not path.is_file() or sha(path) != digest:
                raise ValueError(f'missing or changed owner-supplied Pine source: {path}')
        pine.append({'path': name, 'preserved_copy': str((OUT/'sources'/name).relative_to(ROOT)),
                     'sha256': digest, 'identity_verified': True})
    legacy = ROOT/'artifacts/earnings-reaction-v1/ta03'
    legacy_before = {str(p.relative_to(ROOT)): sha(p) for p in sorted(legacy.rglob('*')) if p.is_file()}
    for line in (legacy/'SHA256SUMS').read_text().splitlines():
        digest, relative = line.split(maxsplit=1)
        if sha(ROOT/relative.strip()) != digest:
            raise ValueError(f'legacy evidence checksum mismatch: {relative}')
    sources = []
    # Check all original response AND validation hashes before parsing any bytes.
    for name in ('SHA256SUMS', 'TA02D-SHA256SUMS'):
        checksum_file = BASE/name
        for line in checksum_file.read_text().splitlines():
            expected, relative = line.split(maxsplit=1)
            path = ROOT/relative.strip()
            if sha(path) != expected:
                raise ValueError(f'original checksum mismatch: {relative}')
            sources.append({'path': relative.strip(), 'sha256': expected, 'verified_against': str(checksum_file.relative_to(ROOT))})
        sources.append({'path': str(checksum_file.relative_to(ROOT)), 'sha256': sha(checksum_file)})
    batches = []
    datasets = []
    for number, (request, count) in enumerate(zip(REQUESTS, (279, 225, 279)), 1):
        raw_path, = (
            p for p in (BASE/'ta02d-retrieved').glob(f'{number}-*.json') if not p.name.endswith('.validation.json'))
        retained = json.loads(raw_path.with_suffix('.validation.json').read_text())
        batch = parse_response(raw_path.read_bytes(), request,
                  retrieved_at=datetime.fromisoformat(retained['retrieved_at_utc']),
                  raw_response_ref=retained['raw_response_ref'],
                  instrument_metadata_ref=retained['instrument_metadata_ref'],
                  instrument_metadata_verified=True)
        if batch.response_sha256 != retained['response_sha256'] or len(batch.candles) != count:
            raise ValueError('retained count/hash mismatch')
        if json.loads(json.dumps([asdict(c) for c in batch.candles], default=str)) != retained['candles']:
            raise ValueError('reparsed candles differ from original validation')
        if not all(a.start_ist > b.start_ist for a, b in zip(batch.candles, batch.candles[1:])):
            raise ValueError('expected strictly descending provider array')
        datasets.append({'instrument_key': request.instrument_key, 'unit': request.unit,
                         'count': count, 'raw_path': str(raw_path.relative_to(ROOT)),
                         'raw_sha256': batch.response_sha256, 'provider': batch.provider,
                         'parser_version': batch.parser_version, 'retrieved_at_utc': str(batch.retrieved_at_utc),
                         'adjustment_status': batch.adjustment_status,
                         'issues_preserved': batch.issues,
                         'original_validation_reproduced': True})
        batches.append(batch)
    cutoff = min(b.retrieved_at_utc for b in batches)
    rows = calculate_daily(batches[0].candles, batches[2].candles, cutoff=cutoff)
    old = calculate_daily_research_proxies(batches[0].candles, batches[2].candles, cutoff=cutoff)
    stock, bench = [r.stock for r in rows], [r.benchmark for r in rows]
    # Exact rational ATR in expanded closed form, independent of recurrence.
    tr = [None]+[max(Fraction(c.high-c.low), abs(Fraction(c.high-stock[i-1].close)),
                   abs(Fraction(c.low-stock[i-1].close))) for i, c in enumerate(stock) if i]
    seed = sum(tr[1:15])/14
    decay = Fraction(13, 14)
    atr = [None]*14+[seed*decay**(i-14)+sum(
        (tr[j]*decay**(i-j)/14 for j in range(15, i+1)), Fraction(0)) for i in range(14, len(rows))]
    rs = [Fraction(s.close)/Fraction(stock[i-123].close)/
          (Fraction(b.close)/Fraction(bench[i-123].close))-1 if i >= 123 else None
          for i, (s, b) in enumerate(zip(stock, bench))]
    for i, r in enumerate(rows):
        check(r.true_range, tr[i])
        check(r.atr, atr[i])
        check(r.dtr_atr_ratio, Fraction(r.dtr)/atr[i] if atr[i] else None)
        check(r.rs, rs[i])
        check(r.rs_mean, sum(rs[i-49:i+1])/50 if i >= 172 else None)
        ratio = Fraction(stock[i].volume)*20/sum(Fraction(c.volume) for c in stock[i-20:i]) if i >= 20 else None
        check(r.rvol_ratio, ratio)
        for actual, value in ((r.rvol_pct, ratio), (r.dtr_pct, Fraction(r.dtr)/atr[i] if atr[i] else None)):
            check(actual, Fraction((100*value+Fraction(1, 2)).numerator//
                  (100*value+Fraction(1, 2)).denominator) if value is not None else None)
    # Reproduce the old CSV exactly in value space; preserve it byte-for-byte.
    with (legacy/'daily-indicators.csv').open(newline='') as stream:
        original = list(csv.DictReader(stream))
    if len(original) != len(old):
        raise ValueError('legacy row count changed')
    for r, prior in zip(old, original):
        if str(r.stock.start_ist.date()) != prior['trading_date']:
            raise ValueError('legacy date mismatch')
        for name in ('atr14', 'rvol20', 'normalized_price_ratio_research_proxy',
                     'relative_return55_pp_research_proxy', 'dtr_previous_atr14', 'previous_atr14'):
            if getattr(r, name) != (Decimal(prior[name]) if prior[name] else None):
                raise ValueError(f'legacy value changed: {name}')
    test = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s',
                           'experiments/earnings_reaction_v1/tests', '-v'], cwd=ROOT,
                          capture_output=True, text=True)
    (OUT/'tests.log').write_text(test.stdout+test.stderr)
    if test.returncode:
        raise RuntimeError('tests failed; see ta03r/tests.log')
    names = [f.name for f in fields(SourceDailyIndicators) if f.name not in ('stock', 'benchmark')]
    headers = ['trading_date', 'stock_timestamp', 'benchmark_timestamp', 'stock_instrument_key',
               'benchmark_instrument_key', 'version', 'adjustment_status', 'validation_status']+names
    with (OUT/'daily-indicators.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(trading_date=str(r.stock.start_ist.date()),
                stock_timestamp=r.stock.provider_timestamp, benchmark_timestamp=r.benchmark.provider_timestamp,
                stock_instrument_key=r.stock.instrument_key, benchmark_instrument_key=r.benchmark.instrument_key,
                version=VERSION, adjustment_status=batches[0].adjustment_status,
                validation_status='SOURCE_DERIVED_DAILY;TRADINGVIEW_PARITY_UNVERIFIED',
                **{name: getattr(r, name) for name in names}))
    with (OUT/'proxy-comparison.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['trading_date', 'legacy_atr14_proxy', 'source_atr14', 'legacy_dtr_previous_atr_ratio_proxy',
                         'source_dtr_current_atr_ratio', 'source_dtr_rounded_pct', 'legacy_rvol_ratio_proxy',
                         'source_rvol_rounded_pct', 'legacy_excess_return55_pp_proxy', 'source_rs123_ratio'])
        for r, p in zip(rows, old):
            writer.writerow([r.stock.start_ist.date(), p.atr14, r.atr, p.dtr_previous_atr14, r.dtr_atr_ratio,
                             r.dtr_pct, p.rvol20, r.rvol_pct, p.relative_return55_pp_research_proxy, r.rs])
    after = {str(p.relative_to(ROOT)): sha(p) for p in sorted(legacy.rglob('*')) if p.is_file()}
    if legacy_before != after:
        raise ValueError('TA-03 evidence changed')
    counts = {name: sum(getattr(r, name) is not None for r in rows)
              for name in ('atr', 'dtr_pct', 'rvol_pct', 'rs', 'rs_mean', 'rs_trend_radians')}
    manifest = dict(implementation_version=VERSION, pine_sources=pine,
        classification='SOURCE_DERIVED_COMPLETED_DAILY;TRADINGVIEW_PARITY_UNVERIFIED',
        parameters=dict(atr_length=14, atr_smoothing='RMA', volume_length=20, rs_length=123,
                        rs_mean_length=50, price_mean_length=50, trend_base=5, source='close', benchmark='NSE:NIFTY'),
        rows=len(rows), valid_counts=counts, original_sources_verified=sources, datasets=datasets,
        independent_fraction_checks=dict(passed=True, rows=len(rows), tolerance='1e-20'),
        legacy_classification='HISTORICAL_RESEARCH_PROXIES_NOT_VALIDATED_REPRODUCTIONS',
        legacy_values_reproduced=True, legacy_files_unchanged=legacy_before,
        test_returncode=test.returncode,
        parity=dict(tradingview_export_available=False, passed=False,
                    reason='No owner-supplied TradingView output fixture; provider/history/adjustments may differ'),
        limitations=['completed daily context only', 'no security gap merging or intraday table emulation',
                     'weekly/monthly and custom external indicator source unsupported',
                     'UNKNOWN_PROVIDER_ADJUSTMENT_BASIS', 'calendar and historical availability unresolved'])
    (OUT/'source-checksum-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (OUT/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(ROOT)}\n' for p in sorted(OUT.rglob('*'))
        if p.is_file() and p.name != 'SHA256SUMS')+''.join(
        f'{sha(p)}  {p.relative_to(ROOT)}\n' for p in sorted((ROOT/'experiments/earnings_reaction_v1').rglob('*.py'))))
    print(json.dumps(dict(rows=len(rows), valid_counts=counts, tests_passed=True,
                         legacy_preserved=True, tradingview_parity='UNVERIFIED'), indent=2))


if __name__ == '__main__':
    main()
