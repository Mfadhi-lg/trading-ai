"""Completed, session-aligned period indices using the maintained V1 arithmetic.

Daily, weekly and intraday series each rebalance 1/N at that period's close.
A weekly series is therefore explicitly a weekly-rebalanced strategy, rather
than an unlabelled sample of the daily-rebalanced strategy.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from experiments.historical_candle_archive_v1.calendar import day_record
from experiments.historical_candle_archive_v1.observations import boundaries
from . import core

IST = ZoneInfo('Asia/Kolkata')


def aware(value):
    value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError('Timezone-aware completed boundaries required')
    return value.astimezone(IST)


def expected_grid(start, end, timeframe, *, decision_time):
    """Use calendar evidence, never the intersection of observed constituents."""
    if timeframe not in ('Daily', 'Weekly', '1-hour', '4-hour'):
        raise ValueError('Unsupported index timeframe')
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    cutoff = aware(decision_time)
    days = []
    day = first
    while day <= last:
        sessions = day_record(day)['sessions']
        if timeframe in ('Daily', 'Weekly'):
            if sessions:
                days.append(dict(start=sessions[0]['opens_at'], end=sessions[-1]['closes_at']))
        else:
            for session in sessions:
                for s, e in boundaries(session['opens_at'], session['closes_at'], 60 if timeframe == '1-hour' else 240):
                    if e <= cutoff:
                        days.append(dict(start=s.isoformat(), end=e.isoformat()))
        day += timedelta(days=1)
    if timeframe != 'Weekly':
        return [g for g in days if aware(g['end']) <= cutoff]
    weeks = defaultdict(list)
    for r in days:
        d = aware(r['start']).date()
        weeks[d - timedelta(days=d.weekday())].append(r)
    result = []
    for monday, rows in sorted(weeks.items()):
        # Never certify a partial first/last requested calendar week.
        if monday < first or monday + timedelta(days=6) > last:
            continue
        if aware(rows[-1]['end']) <= cutoff:
            result.append(dict(start=monday.isoformat()+'T00:00:00+05:30', end=rows[-1]['end'],
                               daily_dates=[r['start'][:10] for r in rows]))
    return result


def _validated(row):
    r = dict(row)
    if r.get('quality', 'OHLC_VALID') != 'OHLC_VALID':
        raise ValueError('Quarantined source candle')
    start, end = aware(r['start']), aware(r['end'])
    if start >= end:
        raise ValueError('Nonpositive candle interval')
    o, h, l, c, v = (D(str(r[k])) for k in ('open', 'high', 'low', 'close', 'volume'))
    if not all(x.is_finite() for x in (o, h, l, c, v)) or l <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h:
        raise ValueError('Invalid OHLCV')
    r.update(start=start.isoformat(), end=end.isoformat(), open=o, high=h, low=l, close=c, volume=v)
    return r


def aggregate_weekly(daily_rows, daily_grid, weekly_grid):
    """Require every expected daily input; preserve value proxy as sum of days."""
    expected = {g['start'][:10]: g for g in daily_grid}
    byday = {}
    for row in daily_rows:
        r = _validated(row)
        day = r['start'][:10]
        if day in byday:
            raise ValueError('Duplicate daily bucket')
        if day not in expected:
            continue
        if aware(r['end']) != aware(expected[day]['end']):
            raise ValueError('Daily completion boundary mismatch')
        byday[day] = r
    result = []
    for week in weekly_grid:
        dates = week['daily_dates']
        if not all(d in byday for d in dates):
            continue  # Fixed expected weekly grid exposes this as a missing bucket.
        parts = [byday[d] for d in dates]
        result.append(dict(parts[0], start=week['start'], end=week['end'],
                           high=max(p['high'] for p in parts), low=min(p['low'] for p in parts),
                           close=parts[-1]['close'], volume=sum(p['volume'] for p in parts),
                           traded_value_proxy_crore=sum(p['close']*p['volume']/D(10_000_000) for p in parts),
                           source_response_sha256s=sorted({h for p in parts for h in p.get('source_response_sha256s', [])})))
    return result


def calculate(prices, grid, benchmark, *, members, timeframe, decision_time, actions=(), listings=None):
    """Build fixed membership from exact completed start/end pairs.

    Input rows are canonical HistoricalStore owner rows. No unfinished OHLC is
    sliced, and missing buckets stay on the expected grid. Benchmark rows must
    have exactly matching boundaries. Provider factor-1 remains provisional.
    """
    if timeframe not in ('Daily', 'Weekly', '1-hour', '4-hour'):
        raise ValueError('Unsupported index timeframe')
    if len(set(members)) != len(members) or len(members) < 2:
        raise ValueError('At least two distinct fixed members required')
    cutoff = aware(decision_time)
    completed = [dict(g, start=aware(g['start']).isoformat(), end=aware(g['end']).isoformat())
                 for g in grid if aware(g['end']) <= cutoff]
    if not completed:
        return []
    stamps = [g['start'] for g in completed]
    if stamps != sorted(set(stamps)):
        raise ValueError('Unique ordered expected grid required')
    expected = {g['start']: g['end'] for g in completed}
    features = {}
    daily_starts = {s[:10]: s for s in stamps}
    listings = listings or {}
    for symbol in members:
        rows, seen = [], set()
        for row in prices.get(symbol, []):
            r = _validated(row)
            if aware(r['end']) > cutoff:
                continue
            if timeframe == 'Daily' and r['start'][:10] in daily_starts:
                r['start'] = daily_starts[r['start'][:10]]
            if r['start'] not in expected:
                continue
            if r['start'] in seen:
                raise ValueError('Duplicate constituent bucket')
            seen.add(r['start'])
            if r['end'] != expected[r['start']]:
                raise ValueError('Constituent bucket is not synchronized')
            if listings.get(symbol) and r['start'][:10] < listings[symbol]:
                raise ValueError('Pre-listing observation')
            rows.append(dict(r, symbol=symbol, date=r['start'], raw_close=r['close'], raw_volume=r['volume']))
        events = []
        for action in actions:
            if action['symbol'] != symbol:
                continue
            if timeframe == 'Weekly' and action['kind'] != 'ORDINARY_CASH_DIVIDEND' and any(s[:10] <= action['ex_date'] <= expected[s][:10] for s in stamps):
                raise ValueError('Known weekly share-unit actions require daily normalization before aggregation')
            matches = [s for s in stamps if s[:10] == action['ex_date']]
            if matches:
                events.append(dict(action, ex_date=matches[0]))
        normalized = core.normalise_prices(rows, events)
        sources = {x['date']: x for x in rows}
        for r in normalized:
            source = sources[r['date']]
            if timeframe == 'Weekly' and 'traded_value_proxy_crore' in source:
                r['traded_value_proxy_crore'] = source['traded_value_proxy_crore']
        features[symbol] = core.constituent_features(normalized, stamps)
    bm = {}
    for row in benchmark:
        r = _validated(row)
        if r['start'] in expected and aware(r['end']) <= cutoff and r['end'] == expected[r['start']]:
            if r['start'] in bm:
                raise ValueError('Duplicate benchmark bucket')
            bm[r['start']] = r['close']
    result = core.equal_weight_index(features, stamps, bm, members=members)
    values = []
    for i, r in enumerate(result):
        parts = [features[s][i] for s in members]
        rets = [p['return_1s_pct'] for p in parts]
        complete = all(v is not None for v in rets)
        value = r['traded_value_proxy_crore']
        prior = values[-20:]
        r.update(timeframe=timeframe, start=r['date'], end=expected[r['date']],
                 available_at=expected[r['date']], availability_basis='COMPLETED_BUCKET_BOUNDARY;RETROSPECTIVE_NOT_HISTORICAL_DELIVERY',
                 rebalancing=timeframe.upper()+'_CLOSE_1/N_RETURN_WEIGHTS;FIXED_MEMBERSHIP',
                 membership_status='RETROSPECTIVE;HISTORICAL_MEMBERSHIP_UNVERIFIED',
                 adjustment_certification='PROVISIONAL_PROVIDER_BASIS_AND_ACTION_COMPLETENESS_UNCERTIFIED',
                 rising_count=sum(v>0 for v in rets) if complete else None,
                 falling_count=sum(v<0 for v in rets) if complete else None,
                 aggregate_activity_ratio20=value/(sum(prior)/20) if value is not None and len(prior)==20 and all(v is not None for v in prior) and sum(prior) else None)
        values.append(value)
    return result
