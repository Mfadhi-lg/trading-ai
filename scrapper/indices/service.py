"""Offline index construction. No acquisition, database writes or runtime activation."""
from datetime import date, timedelta
from decimal import Decimal as D
from . import core


def validate(rows, listing_date=None):
    """Require source OHLC, fixed identity, and unique ordered sessions."""
    seen = set()
    keys = set()
    for r in rows:
        day = r['date']
        if day in seen:
            raise ValueError('Duplicate observation')
        seen.add(day)
        keys.add(r['instrument_key'])
        if listing_date and day < listing_date:
            raise ValueError('Pre-listing observation')
        o, h, l, c, v = (D(str(r['raw_' + k])) for k in ('open', 'high', 'low', 'close', 'volume'))
        if not all(x.is_finite() for x in (o, h, l, c, v)) or l <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError('Invalid OHLCV')
    if len(keys) > 1:
        raise ValueError('Unresolved instrument continuity')


def daily(prices, calendar, benchmark, *, members, actions=(), listings=None):
    """Preserve V1 daily 1/N return weights, price-index and broken-chain rules.

    The explicit calendar must come from validated session evidence, not the
    intersection of available members. Retrospective membership is not PIT.
    """
    if len(set(members)) != len(members) or len(members) < 2:
        raise ValueError('At least two distinct fixed members required')
    if not calendar or calendar != sorted(set(calendar)):
        raise ValueError('Explicit unique ordered calendar required')
    benchmark = {k: D(str(v)) for k, v in benchmark.items()}
    if any(not v.is_finite() or v <= 0 for v in benchmark.values()):
        raise ValueError("Invalid benchmark")
    listings = listings or {}
    features = {}
    for symbol in members:
        rows = prices.get(symbol, [])
        validate(rows, listings.get(symbol))
        normalized = core.normalise_prices(rows, [a for a in actions if a['symbol'] == symbol])
        features[symbol] = core.constituent_features(normalized, calendar)
    result = core.equal_weight_index(features, calendar, benchmark, members=members)
    values = []
    for i, r in enumerate(result):
        parts = [features[s][i] for s in members]
        complete = all(p['return_1s_pct'] is not None for p in parts)
        r['rising_count'] = sum(p['return_1s_pct'] > 0 for p in parts) if complete else None
        r['falling_count'] = sum(p['return_1s_pct'] < 0 for p in parts) if complete else None
        value = r['traded_value_proxy_crore']
        prior = values[-20:]
        r['aggregate_activity_ratio20'] = value / (sum(prior) / 20) if value is not None and len(prior) == 20 and all(x is not None for x in prior) and sum(prior) else None
        r['adjustment_certification'] = 'UNVERIFIED_INPUT_ACTION_COMPLETENESS_AND_PROVIDER_BASIS'
        r['membership_status'] = 'RETROSPECTIVE;HISTORICAL_MEMBERSHIP_UNVERIFIED'
        r['rebalancing'] = 'DAILY_CLOSE_1/N_RETURN_WEIGHTS;FIXED_MEMBERS'
        values.append(value)
    return result


def weekly(daily_rows, calendar, *, completed_through):
    """Sample the daily-rebalanced price chain; sum daily activity, never shares.

    This is a weekly view of the daily strategy, not a weekly-rebalanced strategy.
    A week is emitted only after its Friday and every expected session is present.
    """
    groups = {}
    byday = {r['date']: r for r in daily_rows}
    for day in calendar:
        d = date.fromisoformat(day)
        monday = d - timedelta(days=d.weekday())
        groups.setdefault(monday, []).append(day)
    result = []
    previous = None
    for monday, days in sorted(groups.items()):
        if str(monday + timedelta(days=4)) > completed_through:
            continue
        parts = [byday.get(d) for d in days]
        valid = all(r and r['index_close'] is not None for r in parts)
        row = dict(parts[-1]) if parts[-1] else {'date': days[-1]}
        level = parts[-1]['index_close'] if valid else None
        row.update(index_close=level, return_1s_pct=100*(level/previous-1) if level is not None and previous is not None else None,
                   timeframe='Weekly', weekly_definition='DAILY_REBALANCED_CHAIN_SAMPLED_AT_WEEK_CLOSE',
                   traded_value_proxy_crore=sum(r['traded_value_proxy_crore'] for r in parts) if valid else None)
        row['daily_endpoint_metrics'] = {k: row.pop(k) for k in list(row)
            if k.startswith(('pct_', 'median_', 'aggregate_activity', 'rising_count', 'falling_count'))}
        # Daily indicators must not masquerade as weekly indicators.
        for key in list(row):
            if key.startswith(('ema', 'rs55', 'return_')) and key != 'return_1s_pct':
                row[key] = None
        result.append(row)
        previous = level
    return result


def leave_one_out(prices, calendar, benchmark, *, members, **kwargs):
    return {s: daily(prices, calendar, benchmark, members=[m for m in members if m != s], **kwargs)
            for s in members if len(members) >= 3}
