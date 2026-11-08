"""Global candle and Treasury validation without NSE/INR assumptions."""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
NY = ZoneInfo('America/New_York')


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError('numeric_json_required')
    n = Decimal(str(value))
    if not n.is_finite():
        raise ValueError('finite_numeric_required')
    return n


def validate(rows, request, received_at):
    accepted, rejected = [], []
    duplicate_groups = defaultdict(list)
    stamps = []
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, list) or len(row) != 7:
                raise ValueError('seven_fields_required')
            start = datetime.fromisoformat(row[0])
            if start.tzinfo is None or start.utcoffset() is None:
                raise ValueError('explicit_timezone_required')
            start_ist = start.astimezone(IST)
            if not request['from_date'] <= start_ist.date().isoformat() <= request['to_date']:
                raise ValueError('outside_requested_IST_date_range')
            if start_ist > datetime.fromisoformat(received_at):
                raise ValueError('future_timestamp')
            if start_ist.second or start_ist.microsecond:
                raise ValueError('misaligned_minute_timestamp')
            if request['unit'] == 'days' and start_ist.time() != time():
                raise ValueError('non_midnight_daily_bucket_unresolved')
            o, h, l, c, volume, oi = map(number, row[1:])
            if min(o, h, l, c) <= 0 or not l <= min(o, c) <= max(o, c) <= h:
                raise ValueError('invalid_OHLC_constraints')
            if volume < 0 or oi < 0 or volume != volume.to_integral_value() or oi != oi.to_integral_value():
                raise ValueError('invalid_volume_or_open_interest_numeric')
            end = start_ist + (timedelta(days=1) if request['unit'] == 'days' else timedelta(minutes=request['interval']))
            if end > datetime.fromisoformat(received_at):
                raise ValueError('incomplete_provider_bucket_at_receipt')
            item = dict(number=request['number'], series=request['series'], unit=request['unit'],
                provider_timestamp=row[0], start_ist=start_ist.isoformat(), end_ist=end.isoformat(),
                start_utc=start_ist.astimezone(timezone.utc).isoformat(),
                start_ny=start_ist.astimezone(NY).isoformat(), provider_bucket_date=start_ist.date().isoformat(),
                open=float(o), high=float(h), low=float(l), close=float(c), volume=float(volume), open_interest=float(oi),
                historical_available_at=None, decision_time_verified=False,
                timestamp_semantics='PROVIDER_IST_DAY_BUCKET;EXCHANGE_SESSION_UNRESOLVED' if request['unit'] == 'days' else 'PROVIDER_MINUTE_START;END_EXCLUSIVE',
                raw_row_index=index)
            accepted.append(item)
            duplicate_groups[start_ist].append((item, row))
            stamps.append(start_ist)
        except (ValueError, TypeError, OverflowError) as error:
            rejected.append(dict(number=request['number'], series=request['series'], raw_row_index=index,
                                 reason=str(error), raw_row=row))
    # Quarantine every duplicated timestamp; do not select a favored observation.
    duplicate_ids = {id(item) for group in duplicate_groups.values() if len(group) > 1 for item, _ in group}
    for group in duplicate_groups.values():
        if len(group) > 1:
            conflict = len({json.dumps(raw) for _, raw in group}) > 1
            for item, raw in group:
                rejected.append(dict(number=request['number'], series=request['series'], raw_row_index=item['raw_row_index'],
                                     reason='conflicting_duplicate' if conflict else 'duplicate_timestamp', raw_row=raw))
    accepted = [r for r in accepted if id(r) not in duplicate_ids]
    ordering = 'DESCENDING' if all(a > b for a, b in zip(stamps, stamps[1:])) else 'ASCENDING' if all(a < b for a, b in zip(stamps, stamps[1:])) else 'UNORDERED_OR_DUPLICATED'
    offsets = Counter(datetime.fromisoformat(row['provider_timestamp']).strftime('%z') for row in accepted)
    accepted.sort(key=lambda r: r['start_utc'])
    return accepted, rejected, dict(returned=len(rows), accepted=len(accepted), quarantined=len(rejected),
        provider_order=ordering, timezone_offsets=dict(offsets), zero_volume_rows=sum(r['volume'] == 0 for r in accepted),
        zero_open_interest_rows=sum(r['open_interest'] == 0 for r in accepted))


def latest_completed(rows, cutoff):
    # Every row supplied by caller belongs to the exact requested window/source.
    return max((r for r in rows if datetime.fromisoformat(r['end_ist']) < cutoff),
               key=lambda r: r['end_ist'], default=None)


def yield_delta_bp(current, previous):
    return None if current is None or previous is None else (Decimal(str(current)) - Decimal(str(previous))) * 100


def spread_bp(two, ten):
    return None if two is None or ten is None else (Decimal(str(ten)) - Decimal(str(two))) * 100


def checkpoint(day, phase):
    return datetime.combine(date.fromisoformat(day), time(9, 15) if phase == 'PREOPEN' else time(18), IST)
