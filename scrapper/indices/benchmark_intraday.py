"""Offline completed owner-grid benchmarks from retained five-minute receipts.

Receipt delivery times are unknown: completion permits retrospective comparison
only. Missing, conflicting or invalid source buckets never become filled bars.
"""
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from .timeframes import aware, expected_grid, _validated

KEY = 'NSE_INDEX|NIFTY MIDSML 400'
ROOT = Path(__file__).resolve().parents[2]
INVENTORY = Path('artifacts/earnings-reaction-v1/er03/market-data-inventory.json')


def load_benchmarks(start, end, decision_time, root=ROOT):
    """Return exact completed 1-hour/4-hour rows; omitted buckets are unavailable.

    Every expected five-minute start and end must be present. Equal duplicates
    retain all hashes, while any conflicting/invalid duplicate poisons its bucket.
    No files are written and no provider or credential interface is used.
    """
    root = Path(root)
    cutoff = aware(decision_time)
    rows, invalid = {}, set()
    inventory = json.loads((root / INVENTORY).read_text())
    for spec in inventory['datasets']:
        if (spec['instrument_key'], spec['unit'], spec['interval']) != (KEY, 'minutes', 5):
            continue
        source = spec['source']
        path = (root / source['original_path']).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('Benchmark source escapes retained root')
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != source['sha256']:
            raise ValueError('Retained benchmark SHA256 mismatch')
        for bar in json.loads(raw, parse_float=Decimal)['data']['candles']:
            stamp = aware(bar[0])
            if not start <= stamp.date().isoformat() <= end:
                continue
            key = stamp.isoformat()
            try:
                r = _validated(dict(start=key, end=(stamp+timedelta(minutes=5)).isoformat(),
                                    **dict(zip(('open','high','low','close','volume'), bar[1:6]))))
            except (ValueError, KeyError, ArithmeticError):
                invalid.add(key)
                continue
            values = tuple(r[k] for k in ('open','high','low','close','volume'))
            if key in rows and rows[key][0] != values:
                invalid.add(key)
            hashes = rows[key][2] if key in rows else set()
            hashes.add(digest)
            rows[key] = values, r, hashes
    output = {}
    for timeframe in ('1-hour', '4-hour'):
        output[timeframe] = []
        for grid in expected_grid(start, end, timeframe, decision_time=cutoff):
            first, last = aware(grid['start']), aware(grid['end'])
            stamps, s = [], first
            while s < last:
                stamps.append(s.isoformat())
                s += timedelta(minutes=5)
            if s != last or not stamps or any(k not in rows or k in invalid for k in stamps):
                continue
            parts = [rows[k][1] for k in stamps]
            if any(aware(p['end']) > cutoff for p in parts):
                continue
            output[timeframe].append(dict(instrument_key=KEY, timeframe=timeframe,
                start=grid['start'], end=grid['end'], open=parts[0]['open'],
                high=max(p['high'] for p in parts), low=min(p['low'] for p in parts),
                close=parts[-1]['close'], volume=sum(p['volume'] for p in parts),
                quality='OHLC_VALID', status='COMPLETED_RETROSPECTIVE',
                availability_basis='COMPLETED_BUCKET;HISTORICAL_DELIVERY_UNKNOWN',
                source_response_sha256s=sorted({h for k in stamps for h in rows[k][2]})))
    return output
