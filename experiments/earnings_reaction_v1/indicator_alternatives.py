"""TA-03R3 independent research alternatives; no protected source recovered.

Only completed observations enter calculations. Session schedules are explicit
caller inputs, never inferred from weekdays. See TA03R3_CONTRACT.md for choices.
Reuses Candle, Session and the authoritative TA-03R averaging implementation.
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR

from .candles import Candle
from .events import aware
from .indicators import pine_average, daily_rvol, chronological_completed, _period

VERSION = 'ta03r4-independent-v1'
TIMEFRAMES = {'Daily': 1440, '4-hour': 240, '75-minute': 75, '1-hour': 60}
D = Decimal


def completed(candles, cutoff):
    aware(cutoff)
    bars = tuple(sorted(candles, key=lambda c: c.start_ist))
    if len({c.instrument_key for c in bars}) > 1:
        raise ValueError('mixed instruments')
    if len({c.start_ist for c in bars}) != len(bars):
        raise ValueError('duplicate observations')
    for c in bars:
        if not c.completed_by(cutoff) or c.end_ist > cutoff:
            raise ValueError('future or developing input')
        if c.start_ist >= c.end_ist or c.completed_at < c.end_ist:
            raise ValueError('invalid intraday completion')
        if any(not v.is_finite() for v in (c.open, c.high, c.low, c.close, c.volume)):
            raise ValueError('nonfinite OHLCV')
        if c.volume < 0 or c.low <= 0 or not c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high:
            raise ValueError('invalid OHLCV')
    return bars


def session_bars(candles, sessions, *, cutoff, base_minutes=5):
    """Validate contiguous coverage from each supplied open to min(close,cutoff).

    A developing session accepts completed base bars only; unfinished base bars
    are absent. Missing a complete session or a base candle is an error.
    """
    _period(base_minutes)
    bars = completed(candles, cutoff)
    sessions = tuple(sessions)
    if any(a.closes_at >= b.opens_at for a, b in zip(sessions, sessions[1:])):
        raise ValueError('unordered or overlapping sessions')
    groups, consumed = [], 0
    step = timedelta(minutes=base_minutes)
    for s in sessions:
        group = tuple(c for c in bars if s.opens_at <= c.start_ist < s.closes_at)
        expected, cursor = [], s.opens_at
        while cursor < s.closes_at and min(cursor+step, s.closes_at) <= cutoff:
            expected.append((cursor, min(cursor+step, s.closes_at)))
            cursor += step
        if [(c.start_ist, c.end_ist) for c in group] != expected:
            raise ValueError('historical data gap or invalid session boundary')
        groups.append(group)
        consumed += len(group)
    if consumed != len(bars):
        raise ValueError('candle outside supplied sessions')
    return tuple(groups)


def aggregate(candles, sessions, timeframe, *, cutoff, base_minutes=5):
    """Anchor to each exchange open, truncate final bar at explicit close.

    Developing output end is the scheduled end; completed_at is that end.
    Its OHLCV describes only the retained completed constituent prefix.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError('unsupported display timeframe; weekly remains deferred')
    _period(base_minutes)
    n = TIMEFRAMES[timeframe]
    if timeframe != 'Daily' and n % base_minutes:
        raise ValueError('base bars cannot be split across display boundaries')
    sessions = tuple(sessions)
    groups = session_bars(candles, sessions, cutoff=cutoff, base_minutes=base_minutes)
    result = []
    for s, group in zip(sessions, groups):
        cursor = s.opens_at
        while cursor < s.closes_at:
            end = min(cursor+timedelta(minutes=n), s.closes_at)
            part = tuple(c for c in group if cursor <= c.start_ist < end)
            if part:
                done = part[-1].end_ist == end
                result.append(Candle(part[0].instrument_key, cursor.isoformat(), cursor, end,
                    part[0].open, max(c.high for c in part), min(c.low for c in part), part[-1].close,
                    sum((c.volume for c in part), D(0)), part[-1].open_interest,
                    'session_anchored;completed' if done else 'session_anchored;developing_prefix',
                    end, f'NSE:{s.day}'))
            cursor = end
    return tuple(result)


def intra_rvol(candles, sessions, daily_history, *, cutoff, lookback=20, base_minutes=5):
    """A: cumulative / previous full-day mean. B: same-offset cumulative mean.

    Ratios, not percentages. Missing warmup gives None. B requires each of
    the previous n scheduled sessions and the exact elapsed-time endpoint.
    No interpolation, shortened-session carry or missing-session skipping.
    """
    _period(lookback)
    sessions = tuple(sessions)
    groups = session_bars(candles, sessions, cutoff=cutoff, base_minutes=base_minutes)
    daily = chronological_completed(daily_history, cutoff=cutoff)
    if any(not c.volume.is_finite() or c.volume < 0 for c in daily):
        raise ValueError('finite nonnegative daily volume required')
    key = next((c.instrument_key for g in groups for c in g), None)
    if daily and key and daily[0].instrument_key != key:
        raise ValueError('daily and intraday instrument mismatch')
    cumulative, out = [], []
    for i, (s, group) in enumerate(zip(sessions, groups)):
        running, offsets = D(0), {}
        prior = [c.volume for c in daily if c.start_ist.date() < s.day and c.completed_at <= s.opens_at][-lookback:]
        denom_a = sum(prior, D(0))/lookback if len(prior) == lookback else None
        for c in group:
            running += c.volume
            offset = c.end_ist-s.opens_at
            offsets[offset] = running
            old = [h.get(offset) for h in cumulative[max(0, i-lookback):i]]
            denom_b = (sum(old, D(0))/lookback if len(old) == lookback and None not in old else None)
            out.append(dict(observation_at=c.end_ist, session_id=f'NSE:{s.day}', cumulative_volume=running,
                baseline_a=denom_a, baseline_b=denom_b,
                candidate_a=running/denom_a if denom_a else None,
                candidate_b=running/denom_b if denom_b else None,
                candidate_a_pct=100*running/denom_a if denom_a else None,
                candidate_b_pct=100*running/denom_b if denom_b else None))
        cumulative.append(offsets)
    return tuple(out)


def dss_rvol_reconstruction(candles, sessions, *, cutoff, lookback=20, bar_minutes=1):
    """Reference-supported mature DSS behavior, separate from hypotheses A/B.

    Use completed chart bars at their actual display resolution. The first bar's
    volume is included twice in each session total, both numerator and history.
    This reproduces retained live observations on 1m/1h/4h at the tested
    lookbacks. Author Pine is unavailable. Other settings are generalizations.
    Require all preceding n complete sessions. Do not reproduce zero-padded or
    undefined cold-start state, whose outputs depend on chart history loading.
    Daily native buckets are outside this session-boundary interface.
    """
    _period(lookback)
    sessions = tuple(sessions)
    groups = session_bars(candles, sessions, cutoff=cutoff, base_minutes=bar_minutes)
    totals, out = [], []
    for i, (s, group) in enumerate(zip(sessions, groups)):
        prior = totals[max(0,i-lookback):i]
        baseline = sum(prior,D(0))/lookback if len(prior)==lookback and None not in prior else None
        running, first = D(0), group[0].volume if group else D(0)
        for c in group:
            running += c.volume
            numerator = running+first
            value = numerator/baseline if baseline else None
            out.append(dict(observation_at=c.end_ist,session_id=f'NSE:{s.day}',
                cumulative_volume=running,first_chart_bar_volume=first,
                dss_numerator=numerator,dss_baseline=baseline,
                dss_ratio=value,dss_pct=100*value if value is not None else None,
                status=('SUPPORTED_BY_REFERENCE' if (bar_minutes,lookback) in
                        ((1,20),(60,1),(60,20),(60,21),(240,20))
                        else 'INDEPENDENT_APPROXIMATION') if value is not None else 'UNRESOLVED',
                version='tv02-dss-mature-reconstruction-v1'))
        totals.append(running+first if group and group[-1].end_ist==s.closes_at else None)
    return tuple(out)


def volume_features(candles, *, cutoff, daily=False, average=21, relative=21,
                    pocket=10, up_down=20, turnover=50):
    """Auditable overlapping events; current-inclusive SMA, preceding-only RVOL.

    Direction = close vs previous close; equal closes are neutral. Extremes
    are daily-only strict records over previous 63/252 observations, NOT HVE.
    None means insufficient inputs, False means an evaluated non-event.
    """
    for n in (average, relative, pocket, up_down, turnover):
        _period(n)
    bars = chronological_completed(candles, cutoff=cutoff) if daily else completed(candles, cutoff)
    volumes = tuple(c.volume for c in bars)
    ma = pine_average(volumes, average)
    rv = daily_rvol(volumes, relative)
    prices = pine_average((c.close for c in bars), turnover)
    vma = pine_average(volumes, turnover)
    directions = [0]+[(c.close > bars[i-1].close)-(c.close < bars[i-1].close) for i,c in enumerate(bars) if i]
    out = []
    for i,c in enumerate(bars):
        down = [volumes[j] for j in range(max(1, i-pocket), i) if directions[j] < 0]
        pp = (directions[i] > 0 and bool(down) and c.volume > max(down)) if i >= pocket else None
        ud = None
        if i >= up_down:
            ix = range(i-up_down+1, i+1)
            u = sum((volumes[j] for j in ix if directions[j] > 0), D(0))
            d = sum((volumes[j] for j in ix if directions[j] < 0), D(0))
            ud = u/d if d else None
        flags = dict(pocket_pivot=pp, dry=c.volume < ma[i]/5 if ma[i] is not None else None,
            high_up=directions[i] > 0 and c.volume > ma[i] if ma[i] is not None and i else None,
            high_down=directions[i] < 0 and c.volume > ma[i] if ma[i] is not None and i else None)
        for n in (63,252):
            history = volumes[max(0,i-n):i]
            ready = daily and i >= n
            flags[f'record_high_{n}d'] = c.volume > max(history) if ready else None
            flags[f'record_low_{n}d'] = c.volume < min(history) if ready else None
        flags['record_observed_history'] = c.volume > max(volumes[:i]) if daily and i else None
        out.append(dict(observation_at=c.completed_at, volume=c.volume, volume_sma=ma[i],
            rvol_ratio=rv[i], rvol_pct=rv[i]*100 if rv[i] is not None else None,
            up_down_ratio=ud, turnover_sma_product_inr=prices[i]*vma[i] if prices[i] is not None else None,
            turnover_close_volume_inr=c.close*c.volume,
            highest_volume_pct_observed=c.volume/max(volumes[:i])*100 if i and max(volumes[:i]) else None,
            original_hve=None, original_hvy=None, original_hvq=None,
            **flags, color=volume_color(flags, directions[i])))
    return tuple(out)


def volume_color(flags, direction):
    """Independent presentation precedence; never mutates event flags."""
    if direction > 0 and any(flags.get(k) for k in ('record_high_252d','record_high_63d','record_observed_history')):
        return 'gold'
    if flags.get('pocket_pivot'):
        return 'dodgerblue'
    if any(flags.get(k) for k in ('dry','record_low_63d','record_low_252d')):
        return 'orange'
    if flags.get('high_up'):
        return 'limegreen'
    if flags.get('high_down'):
        return 'red'
    return 'gray'


def latest_markers(rows, *, cutoff, fields=('record_high_252d','record_high_63d','record_observed_history')):
    """Snapshot-only latest markers; earlier event booleans remain immutable."""
    aware(cutoff)
    return {k: next((i for i in range(len(rows)-1,-1,-1)
                    if rows[i]['observation_at'] <= cutoff and rows[i].get(k)), None) for k in fields}


def value_area(totals, fraction=D('.7')):
    """Documented TV greedy no-overshoot rule, not py-market-profile overshoot.

    POC ties choose lower price (independent choice). Expansion ties choose
    nearest POC then upper. Includes POC even if it alone exceeds target.
    """
    if not totals or not 0 < fraction <= 1 or any(v < 0 or not v.is_finite() for v in totals):
        raise ValueError('invalid distribution or fraction')
    total = sum(totals, D(0))
    if not total:
        return None
    poc = max(range(len(totals)), key=lambda i: (totals[i], -i))
    lo = hi = poc
    used, target = totals[poc], fraction*total
    while used < target and (lo > 0 or hi < len(totals)-1):
        candidates = [i for i in (lo-1,hi+1) if 0 <= i < len(totals)]
        j = max(candidates, key=lambda i:(totals[i], -abs(i-poc), i))
        if used+totals[j] > target:
            break
        used += totals[j]
        lo,hi = min(lo,j),max(hi,j)
    return poc, lo, hi, used/total


def session_profile(candles, session, *, cutoff, bin_width=D('1'), base_minutes=5):
    """Fixed INR bins anchored at zero; uniform range-overlap allocation.

    No trade-by-price claim. Full session required; final levels available
    only at session close. Zero-range bar goes to floor(price/width).
    """
    if not bin_width.is_finite() or bin_width <= 0:
        raise ValueError('positive finite width required')
    if cutoff < session.closes_at:
        raise ValueError('final profile unavailable before session close')
    bars, = session_bars(candles, (session,), cutoff=cutoff, base_minutes=base_minutes)
    if not bars:
        raise ValueError('empty session')
    floor = lambda p: int((p/bin_width).to_integral_value(rounding=ROUND_FLOOR))
    first, last = floor(min(c.low for c in bars)), floor(max(c.high for c in bars))
    if last-first > 100000:
        raise ValueError('profile exceeds bounded row limit')
    up,down = [D(0)]*(last-first+1), [D(0)]*(last-first+1)
    for c in bars:
        target = up if c.close >= c.open else down
        if c.high == c.low:
            target[floor(c.close)-first] += c.volume
        else:
            for k in range(floor(c.low),floor(c.high)+1):
                overlap = max(D(0),min(c.high,(k+1)*bin_width)-max(c.low,k*bin_width))
                target[k-first] += c.volume*overlap/(c.high-c.low)
    totals = tuple(a+b for a,b in zip(up,down))
    area = value_area(totals)
    poc,lo,hi,coverage = area if area else (None,None,None,None)
    return dict(session_id=f'NSE:{session.day}', available_at=session.closes_at,
        bin_width=bin_width, first_bin=first, up=up, down=down, total=totals,
        poc=(first+poc+D('.5'))*bin_width if area else None,
        val=(first+lo)*bin_width if area else None,
        vah=(first+hi+1)*bin_width if area else None, actual_value_area_fraction=coverage,
        underlying_minutes=base_minutes, method='uniform_range_overlap;fixed_zero_origin_bins;tv_no_overshoot')
