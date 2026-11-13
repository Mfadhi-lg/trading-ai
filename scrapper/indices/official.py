"""Read-only official NSE histories from retained, checksum-verified receipts.

No provider requests and no substitutions. Retrospective receipt selection is
not a claim of historical delivery or verified index launch/backcalculation.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import csv
import gzip
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from experiments.historical_candle_archive_v1.calendar import day_record

ROOT = Path(__file__).resolve().parents[2]
IST = ZoneInfo('Asia/Kolkata')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    return json.loads(raw, parse_float=Decimal)


def _read(path):
    with Path(path).open(newline='') as stream:
        return list(csv.DictReader(stream))


def _inside(root, relative):
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('Retained source escapes its root')
    return path


def _aware(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Explicit timestamp timezone required')
    return result


def _daily(raw_rows, query, receipt, *, asof):
    """Validate source values exactly, quarantine all duplicated bucket dates."""
    rows, excluded = [], []
    received_at = receipt['received_at']
    try:
        receipt_time = _aware(received_at)
    except (ValueError, TypeError):
        if not receipt.get('allow_unknown_original_receipt'):
            raise ValueError('Invalid acquired receipt timestamp')
        receipt_time = None
    dates = defaultdict(list)
    raw_dates = defaultdict(int)
    for i, raw in enumerate(raw_rows):
        day = None
        try:
            if not isinstance(raw, list) or len(raw) != 7:
                raise ValueError('SEVEN_FIELDS_REQUIRED')
            stamp = _aware(raw[0]).astimezone(IST)
            day = stamp.date().isoformat()
            raw_dates[day] += 1
            if stamp.hour or stamp.minute or stamp.second or stamp.microsecond:
                raise ValueError('NON_MIDNIGHT_DAILY_BUCKET')
            if not query['from_date'] <= day <= query['to_date']:
                raise ValueError('OUTSIDE_REQUEST_WINDOW')
            if any(isinstance(v, bool) or not isinstance(v, (int, float, Decimal)) for v in raw[1:]):
                raise ValueError('NUMERIC_SOURCE_VALUES_REQUIRED')
            o, h, l, c, volume, oi = (Decimal(str(v)) for v in raw[1:])
            if not all(v.is_finite() for v in (o,h,l,c,volume,oi)) or not 0 < l <= min(o,c) <= max(o,c) <= h:
                raise ValueError('INVALID_OHLC')
            if volume < 0 or oi < 0 or volume != volume.to_integral_value() or oi != oi.to_integral_value():
                raise ValueError('INVALID_VOLUME_OR_OPEN_INTEREST')
            record = day_record(day)
            if not record['sessions']:
                raise ValueError('NO_VERIFIED_NSE_SESSION')
            completion = max(_aware(s['closes_at']) for s in record['sessions'])
            if receipt_time is not None and completion > receipt_time:
                raise ValueError('UNFINISHED_AT_RECEIPT')
            if completion > asof:
                continue
            row = dict(instrument_key=query['instrument_key'], date=day, open=o, high=h, low=l, close=c,
                       provider_volume=volume, provider_open_interest=oi, unit='index points',
                       volume_semantics='NOT_INDEX_SHARE_VOLUME_OR_INSTITUTIONAL_FLOW',
                       provider_timestamp=raw[0], available_at=completion.isoformat(),
                       observed_at=receipt_time.isoformat() if receipt_time is not None else None,
                       original_receipt_label=received_at, receipt_timestamp_verified=receipt_time is not None,
                       completion_at_receipt_verified=receipt_time is not None,
                       source_file=receipt['source_file'],
                       source_sha256=receipt['sha256'], request_id=receipt.get('request_id'),
                       historical_delivery_verified=False,
                       history_basis='RETROSPECTIVE_PROVIDER_PRICE_INDEX;LAUNCH_BACKCALCULATION_UNVERIFIED')
            rows.append(row)
            dates[day].append(row)
        except (ValueError, TypeError, ArithmeticError) as error:
            issue = dict(instrument_key=query['instrument_key'], raw_row_index=i,
                         reason=str(error), source_file=receipt['source_file'])
            if day is not None:
                issue['date'] = day
            excluded.append(issue)
    duplicated = {day for day, count in raw_dates.items() if count>1}
    excluded.extend(dict(instrument_key=query['instrument_key'], date=d, reason='DUPLICATED_DAILY_BUCKET',
                         source_file=receipt['source_file']) for d in sorted(duplicated))
    return [r for r in rows if r['date'] not in duplicated], excluded


def _ledgers(root):
    base = root/'artifacts/technical-fix-v2'
    manifest, ledger = base/'BOUNDED_ACQUISITION_MANIFEST.json', base/'UPDATE_LEDGER.json'
    if ledger.exists():
        yield manifest, ledger
    recurring = base/'recurring'
    grant, proposal = recurring/'OWNER_GRANT.json', recurring/'PROPOSED_MANIFEST.json'
    if grant.exists() and proposal.exists():
        parent = _hash(proposal.read_bytes())
        if _json(grant.read_bytes()).get('manifest_sha256') != parent:
            raise ValueError('Recurring grant hash mismatch')
        for saved in sorted((recurring/'batches').glob('*/UPDATE_LEDGER.json')):
            plan = saved.parent/'manifest.json'
            if _json(plan.read_bytes()).get('parent_manifest_sha256') != parent:
                raise ValueError('Recurring parent manifest mismatch')
            yield plan, saved


def load_official(store, *, root=ROOT, asof=None, start='2025-12-01', end=None):
    """Return catalog and exact-key daily histories without mutating any source.

    `store.root` is the canonical HistoricalStore archive root. `asof` limits
    completed bars; it does not receipt-gate retrospective historical delivery.
    Exclusions and missing source files stay explicit. Corrupt hashes fail closed.
    """
    root = Path(root).resolve()
    cutoff = _aware(asof) if isinstance(asof, str) else asof or datetime.now(timezone.utc)
    if cutoff.tzinfo is None:
        raise ValueError('Aware asof required')
    catalog_path = root/'artifacts/technical-integration-v2/UPSTOX_ALL_INDICES.csv'
    inventory = _read(catalog_path)
    lookup = {r['instrument_key']: r for r in inventory}
    if len(lookup) != len(inventory) or any(not k.startswith('NSE_INDEX|') for k in lookup):
        raise ValueError('Invalid/duplicate official instrument key')
    bars, exclusions, failures = {}, [], defaultdict(list)
    # Original raw responses are read once; preserve their actual receipt basis.
    provenance = root/'artifacts/technical-integration-v2/MARKET_DATA_PROVENANCE.csv'
    originals = {}
    if provenance.exists():
        for r in _read(provenance):
            if r.get('instrument_key') in lookup and r.get('value') and r.get('source_file','').endswith('.json'):
                originals[r['instrument_key'], r['source_file'], r['source_sha256']] = r
    for (key, source, digest), row in sorted(originals.items()):
        path = _inside(root, source)
        if not path.is_file():
            exclusions.append(dict(instrument_key=key,reason='MISSING_RETAINED_SOURCE',source_file=source))
            continue
        raw = path.read_bytes()
        if _hash(raw) != digest:
            raise ValueError('Original NSE source checksum mismatch')
        candles = _json(raw)['data']['candles']
        query = dict(instrument_key=key,from_date='0001-01-01',to_date='9999-12-31')
        receipt = dict(received_at=row['retrieved_at'],source_file=source,sha256=digest,
                       allow_unknown_original_receipt=True)
        accepted, bad = _daily(candles, query, receipt, asof=cutoff)
        exclusions.extend(bad)
        for r in accepted:
            bars[key,r['date']] = r
    records = []
    for manifest, ledger in _ledgers(root):
        raw_manifest = manifest.read_bytes()
        state = _json(ledger.read_bytes())
        if state['manifest_sha256'] != _hash(raw_manifest):
            raise ValueError('NSE ledger manifest checksum mismatch')
        queries = {r['request_id']:r for r in _json(raw_manifest)['requests']}
        for ident, receipt in state['requests'].items():
            q = queries[ident]
            if q['kind'] == 'NSE_INDEX':
                if q['instrument_key'] not in lookup:
                    raise ValueError('Acquired NSE key absent from retained catalog')
                records.append((receipt.get('received_at',''), ident, q, receipt))
    for _, ident, q, rec in sorted(records):
        key = q['instrument_key']
        if q['unit'] != 'days' or q['interval'] != 1:
            continue
        if rec.get('kind') != 'NSE_INDEX' or rec.get('url') != q['url'] or rec.get('series') != q['series']:
            raise ValueError('NSE receipt identity mismatch')
        if rec['status'] not in ('SUCCESS','EMPTY'):
            failures[key].append(dict(status=rec['status'],request_id=ident,http_status=rec.get('http_status')))
            continue
        path = _inside(store.root, rec['raw_path'])
        raw = gzip.decompress(path.read_bytes())
        if _hash(raw) != rec['sha256']:
            raise ValueError('Acquired NSE source checksum mismatch')
        receipt = dict(rec,source_file=str(path),request_id=ident)
        accepted, bad = _daily(_json(raw)['data']['candles'],q,receipt,asof=cutoff)
        exclusions.extend(bad)
        # A later corrupted/duplicated observation invalidates the earlier day.
        for r in bad:
            if 'date' in r:
                bars.pop((key,r['date']),None)
        for r in accepted:
            bars[key,r['date']] = r
    through = end or max((day for _,day in bars),default=start)
    calendar = []
    d = date.fromisoformat(start)
    while str(d) <= through:
        if day_record(d)['sessions']:
            calendar.append(str(d))
        d += timedelta(days=1)
    grouped = {key: sorted((r for (k,d),r in bars.items() if k==key and start<=d<=through),key=lambda r:r['date']) for key in lookup}
    for r in inventory:
        part = grouped[r['instrument_key']]
        seen = {x['date'] for x in part}
        last = max(seen,default=None)
        r['original_catalog_snapshot'] = {k: r.get(k) for k in
            ('history_status','missing_data_reason','next_action','key_resolves_in_retained_response','source_file')}
        r.update(retained_daily_observations=len(part),daily_history_verified=bool(part),
                 key_resolves_in_retained_response=bool(part),
                 source_file=';'.join(sorted({x['source_file'] for x in part})),
                 source_sha256s=sorted({x['source_sha256'] for x in part}),
                 missing_data_reason='' if part else 'NO_ACCEPTED_RETAINED_HISTORY',
                 next_action='Review historical delivery and launch/backcalculation evidence' if part else 'Resolve unavailable history; no automatic acquisition',
                 first_verified_provider_date=min(seen,default=None),last_verified_provider_date=last,
                 missing_dates=[d for d in calendar if d not in seen],
                 age_calendar_days=(cutoff.astimezone(IST).date()-date.fromisoformat(last)).days if last else None,
                 history_status='AVAILABLE_RETROSPECTIVE' if part else 'UNAVAILABLE_NO_ACCEPTED_HISTORY',
                 acquisition_failures=failures[r['instrument_key']],unit='index points',
                 warning='Historical delivery and launch/backcalculation unverified; gaps never filled')
    return dict(inventory=inventory,bars=grouped,exclusions=exclusions,calendar=calendar,
                catalog_sha256=_hash(catalog_path.read_bytes()),asof=cutoff.isoformat())
