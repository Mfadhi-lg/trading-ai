"""TA-03R source-derived daily calculations plus preserved TA-03 research proxies.

Relative Strength calculations derived from bharatTrader, MPL-2.0:
https://mozilla.org/MPL/2.0/. DTR vs ATR source: © ssksubam; supplied file
contains no explicit license grant. Originals and notices retained under ta03r/sources.
No TradingView export parity has been established.

No adjustment, filling, signals, calendar inference or historical availability claims.
Numerical arithmetic uses Decimal except the RS atan angle (float).
None means unavailable, never a synthetic zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .candles import Candle
from .events import aware

VERSION = 'ta03r-source-derived-v1'
LEGACY_VERSION = 'ta03-independent-v1'
SPECIAL_SESSION = '2026-02-01'
SPECIAL_SESSION_SOURCE = 'https://nsearchives.nseindia.com/content/circulars/FAOP72352.pdf'


def chronological_completed(candles, *, cutoff):
    """Return original Candle objects sorted; reject incomplete, mixed or duplicate bars.

    Caller supplies daily bars only. Midnight-to-midnight bucket semantics must
    match the retained contract; cutoff is conservative bucket completion.
    Weekends are not rejected. A missing entire session requires calendar evidence.
    """
    aware(cutoff)
    result = tuple(sorted(candles, key=lambda c: c.start_ist))
    if len({c.instrument_key for c in result}) > 1:
        raise ValueError('mixed instruments')
    if len({c.start_ist.date() for c in result}) != len(result):
        raise ValueError('duplicate daily sessions')
    for c in result:
        if (c.end_ist-c.start_ist).total_seconds() != 86400 or c.start_ist.hour != 0:
            raise ValueError('daily buckets required')
        if not c.completed_by(cutoff):
            raise ValueError('future or incomplete daily candle')
    return result


def true_range(high, low, previous_close=None):
    """First observed TR = H-L (prior close unknown); thereafter include gaps."""
    return high-low if previous_close is None else max(high-low, abs(high-previous_close), abs(low-previous_close))


def wilder_rma(values, period=14):
    """Seed at index period-1 with arithmetic mean; then (prev*(n-1)+x)/n.

    Missing input breaks the chain and requires a fresh consecutive seed.
    """
    if type(period) is not int or period <= 0:
        raise ValueError('positive integer period required')
    seed, previous, out = [], None, []
    for value in values:
        if value is None:
            seed, previous = [], None
        elif previous is None:
            seed.append(value)
            if len(seed) == period:
                previous = sum(seed, Decimal(0))/period
        else:
            previous = (previous*(period-1)+value)/period
        out.append(previous)
    return tuple(out)


def daily_rvol(volumes, period=20):
    """V[t]/mean(V[t-period:t]); missing in that exact window => None.

    Zero current volume gives zero if baseline > 0; zero baseline => None.
    Final session volume is usable only after completion.
    """
    if type(period) is not int or period <= 0:
        raise ValueError('positive integer period required')
    volumes = tuple(volumes)
    if any(v is not None and (not v.is_finite() or v < 0) for v in volumes):
        raise ValueError('finite nonnegative volume required')
    out = []
    for i, volume in enumerate(volumes):
        history = volumes[max(0, i-period):i]
        if i < period or volume is None or any(v is None for v in history):
            out.append(None)
        else:
            total = sum(history, Decimal(0))
            out.append(volume*period/total if total else None)
    return tuple(out)


@dataclass(frozen=True)
class DailyIndicators:
    stock: Candle
    benchmark: Candle
    true_range: Decimal
    atr14: Decimal | None
    previous_atr14: Decimal | None
    rvol20: Decimal | None
    normalized_price_ratio_research_proxy: Decimal
    relative_return55_pp_research_proxy: Decimal | None
    dtr_previous_atr14: Decimal | None
    availability: str


def calculate_daily_research_proxies(stock, benchmark, *, cutoff):
    """Strict identical session sets; never inner-join away a missing benchmark day.

    Ratio = 100*(S[t]/B[t])/(S[0]/B[0]); fixed first-session anchor.
    RS55 = 100*((S[t]/S[t-55]-1)-(B[t]/B[t-55]-1)), percentage points.
    A 55-session return needs 56 observations. Both are Nifty 50 proxies only.
    """
    stock = chronological_completed(stock, cutoff=cutoff)
    benchmark = chronological_completed(benchmark, cutoff=cutoff)
    if tuple(c.start_ist.date() for c in stock) != tuple(c.start_ist.date() for c in benchmark):
        raise ValueError('exact benchmark session alignment required; no dropped sessions')
    if not stock:
        return ()
    if any(not c.close.is_finite() or c.close <= 0 for c in stock+benchmark):
        raise ValueError('positive finite closes required for ratios')
    trs = tuple(true_range(c.high, c.low, stock[i-1].close if i else None) for i, c in enumerate(stock))
    atr = wilder_rma(trs)
    rvol = daily_rvol(c.volume for c in stock)
    anchor = stock[0].close/benchmark[0].close
    out = []
    for i, (s, b) in enumerate(zip(stock, benchmark)):
        prior = atr[i-1] if i else None
        dtr = (s.high-s.low)/prior if prior is not None and prior > 0 else None
        rs = (100*(s.close/stock[i-55].close-b.close/benchmark[i-55].close) if i >= 55 else None)
        values = {'atr14': atr[i], 'rvol20': rvol[i], 'rs55': rs, 'dtr_previous_atr14': dtr}
        status = ';'.join(f'{k}=' + ('available' if v is not None else
                            'warmup' if i < {'atr14': 13, 'rvol20': 20, 'rs55': 55, 'dtr_previous_atr14': 14}[k]
                            else 'unavailable_input_or_zero_denominator') for k, v in values.items())
        out.append(DailyIndicators(s, b, trs[i], atr[i], prior, rvol[i],
                   100*(s.close/b.close)/anchor, rs, dtr, status))
    return tuple(out)


def _period(value):
    if type(value) is not int or value < 1:
        raise ValueError('positive integer period required')


def pine_average(values, period, smoothing='SMA'):
    """Pine non-na sampling; RMA seeds with SMA, EMA with first non-na.

    Unlike the preserved wilder_rma proxy, na does not reset accumulated state.
    WMA weights the newest of the last period non-na observations most heavily.
    """
    _period(period)
    if smoothing not in ('RMA', 'EMA', 'SMA', 'WMA'):
        raise ValueError('smoothing must be RMA, EMA, SMA or WMA')
    history, previous, out = [], None, []
    for value in values:
        if value is not None:
            if not value.is_finite():
                raise ValueError('finite input required')
            history.append(value)
            history = history[-period:]
            if smoothing == 'EMA':
                alpha = Decimal(2)/(period+1)
                previous = value if previous is None else alpha*value+(1-alpha)*previous
            elif smoothing == 'RMA':
                if previous is not None:
                    previous = (previous*(period-1)+value)/period
                elif len(history) == period:
                    previous = sum(history, Decimal(0))/period
            elif len(history) == period:
                previous = (sum(history, Decimal(0))/period if smoothing == 'SMA' else
                            sum((v*w for w, v in enumerate(history, 1)), Decimal(0))
                            /Decimal(period*(period+1)//2))
        out.append(previous)
    return tuple(out)


def pine_round(value, decimals=0):
    """Pine round: nearest, ties toward positive infinity (not Python ties-even)."""
    from decimal import ROUND_FLOOR
    if value is None:
        return None
    scale = Decimal(10)**decimals
    return (value*scale+Decimal('0.5')).to_integral_value(rounding=ROUND_FLOOR)/scale


def pine_rvol(volumes, period=20):
    """Source volume / SMA(volume[1], period), raw ratio and rounded percent."""
    _period(period)
    volumes = tuple(volumes)
    if any(v is not None and (not v.is_finite() or v < 0) for v in volumes):
        raise ValueError('finite nonnegative volume required')
    baseline = pine_average((None,)+volumes[:-1], period)
    ratio = tuple(v/b if v is not None and b is not None and b > 0 else None
                  for v, b in zip(volumes, baseline))
    return ratio, tuple(pine_round(v*100) if v is not None else None for v in ratio)


def _extreme(values, i, rising, length=3):
    # ta.rising/falling tests the current value against previous values, not
    # three consecutive positive/negative differences. Ignore na observations.
    current = values[i]
    history = [v for v in values[:i] if v is not None][-length:]
    return (current is not None and bool(history) and
            (current > max(history) if rising else current < min(history)))


@dataclass(frozen=True)
class SourceDailyIndicators:
    stock: Candle
    benchmark: Candle
    true_range: Decimal | None
    atr: Decimal | None
    dtr: Decimal
    dtr_atr_ratio: Decimal | None
    dtr_pct: Decimal | None
    rvol_ratio: Decimal | None
    rvol_pct: Decimal | None
    rs: Decimal | None
    rs_mean: Decimal | None
    rs_trend_radians: float | None
    price_mean: Decimal | None
    positive_confirmation: bool
    negative_confirmation: bool
    ma_rising: bool
    ma_falling: bool
    atr_color: str | None
    rs_color: str
    ma_color: str
    reference_date: str | None


def calculate_daily(stock, benchmark, *, cutoff, atr_length=14, atr_smoothing='RMA',
                    volume_length=20, rs_length=123, rs_mean_length=50,
                    price_mean_length=50, trend_base=5, source='close'):
    """Supplied ssksubam/bharatTrader arithmetic for completed DAILY bars only.

    Defaults are source defaults, including NSE:NIFTY supplied by caller as the
    benchmark. Exact observed-date alignment is required; security gap merging,
    intraday/weekly/monthly contexts and live table behavior are not emulated.
    tr is the Pine variable, NOT tr(true): first bar is na. ATR uses current bar.
    RS is dimensionless: (S/S[length])/(B/B[length])-1, not excess return in pp.
    UI plot toggles do not alter underlying arithmetic/alertconditions.
    """
    from math import atan
    for n in (atr_length, volume_length, rs_length, rs_mean_length, price_mean_length, trend_base):
        _period(n)
    if source not in ('open', 'high', 'low', 'close'):
        raise ValueError('source must be open, high, low or close')
    stock = chronological_completed(stock, cutoff=cutoff)
    benchmark = chronological_completed(benchmark, cutoff=cutoff)
    if tuple(c.start_ist.date() for c in stock) != tuple(c.start_ist.date() for c in benchmark):
        raise ValueError('exact benchmark session alignment required; no dropped sessions')
    return _source_arithmetic(stock, benchmark, atr_length=atr_length, atr_smoothing=atr_smoothing,
        volume_length=volume_length, rs_length=rs_length, rs_mean_length=rs_mean_length,
        price_mean_length=price_mean_length, trend_base=trend_base, source=source)


def _source_arithmetic(stock, benchmark, *, atr_length, atr_smoothing, volume_length,
                       rs_length, rs_mean_length, price_mean_length, trend_base, source):
    """Shared original-source arithmetic; callers validate context and completion.

    Extracted unchanged from calculate_daily for TA-03R4 chart-context RS reuse.
    ATR fields here always describe the supplied sequence, not an HTF merge.
    """
    from math import atan
    svalues = tuple(getattr(c, source) for c in stock)
    bvalues = tuple(getattr(c, source) for c in benchmark)
    if any(not v.is_finite() or v <= 0 for v in svalues+bvalues):
        raise ValueError('positive finite source prices required')
    if any(not c.high.is_finite() or not c.low.is_finite() or c.high < c.low for c in stock):
        raise ValueError('finite ordered high/low required')
    trs, atr, rvol, volpct = _dtr_arithmetic(stock, atr_length, atr_smoothing, volume_length)
    rs = tuple(s/svalues[i-rs_length]/(b/bvalues[i-rs_length])-1 if i >= rs_length else None
               for i, (s, b) in enumerate(zip(svalues, bvalues)))
    means = pine_average(rs, rs_mean_length)
    price_means = pine_average(svalues, price_mean_length)
    out = []
    for i, (s, b) in enumerate(zip(stock, benchmark)):
        dtr = s.high-s.low
        ratio = dtr/atr[i] if atr[i] is not None and atr[i] > 0 else None
        pct = pine_round(ratio*100) if ratio is not None else None
        angle = (atan(float((rs[i]-rs[i-trend_base])/trend_base))
                 if i >= trend_base and rs[i] is not None and rs[i-trend_base] is not None else None)
        pos = _extreme(price_means, i, True) and svalues[i] >= price_means[i]
        neg = _extreme(price_means, i, False) and svalues[i] < price_means[i]
        rising, falling = _extreme(means, i, True), _extreme(means, i, False)
        out.append(SourceDailyIndicators(s, b, trs[i], atr[i], dtr, ratio, pct, rvol[i], volpct[i],
                   rs[i], means[i], angle, price_means[i], pos, neg, rising, falling,
                   None if pct is None else 'green' if pct <= 70 else 'red' if pct >= 90 else 'orange',
                   'green' if rs[i] is not None and rs[i] > 0 else 'red',
                   'green' if rising else 'red' if falling else 'gray',
                   str(stock[i-rs_length].start_ist.date()) if i >= rs_length else None))
    return tuple(out)


def _dtr_arithmetic(stock, atr_length=14, atr_smoothing='RMA', volume_length=20):
    """Original daily/source-period primitives, extracted without formula changes."""
    trs = tuple(true_range(c.high, c.low, stock[i-1].close) if i else None
                for i, c in enumerate(stock))
    atr = pine_average(trs, atr_length, atr_smoothing)
    rvol, volpct = pine_rvol((c.volume for c in stock), volume_length)
    return trs, atr, rvol, volpct


def calculate_chart_rs(stock, benchmark, *, cutoff, timeframe, rs_length=55,
                       benchmark_key='NSE_INDEX|NIFTY MIDSML 400'):
    """Reuse bharatTrader arithmetic on exactly aligned display-timeframe closes.

    No benchmark substitution, carried values or inner join. This returns only
    RS fields: source-sequence ATR must not be mistaken for the daily DTR table.
    """
    from .indicator_alternatives import completed, TIMEFRAMES
    _period(rs_length)
    if timeframe not in TIMEFRAMES:
        raise ValueError('unsupported chart context; weekly integration deferred')
    validate = (lambda values: chronological_completed(values, cutoff=cutoff)) if timeframe == 'Daily' else (
        lambda values: completed(values, cutoff))
    stock, benchmark = validate(stock), validate(benchmark)
    if not benchmark or any(c.instrument_key != benchmark_key for c in benchmark):
        raise ValueError('configured NIFTYMIDSML400 benchmark required')
    if tuple((c.start_ist,c.end_ist) for c in stock) != tuple((c.start_ist,c.end_ist) for c in benchmark):
        raise ValueError('exact chart benchmark boundaries required; no dropped bars')
    result = _source_arithmetic(stock,benchmark,atr_length=14,atr_smoothing='RMA',
        volume_length=20,rs_length=rs_length,rs_mean_length=50,price_mean_length=50,trend_base=5,source='close')
    names=('rs','rs_mean','rs_trend_radians','positive_confirmation','negative_confirmation',
           'ma_rising','ma_falling','rs_color','ma_color','reference_date')
    return tuple(dict(observation_at=max(r.stock.completed_at,r.benchmark.completed_at),
                      **{n:getattr(r,n) for n in names}) for r in result)


def dtr_source_timeframe(timeframe):
    """Actual Pine selector; weekly/monthly calculations remain deferred."""
    if timeframe == 'Weekly':
        return 'W'
    if timeframe == 'Monthly':
        return 'M'
    return 'D'


def merge_daily_dtr(display, daily_stock, *, cutoff, timeframe):
    """Separate confirmed daily research values from historical Pine display.

    Research uses latest daily completed_at <= chart close. Historical display
    uses current day's final ATR (lookahead_on), with confirmed/carry-forward
    daily range and RVOL (lookahead_off). Historical fields explicitly identify
    future dependencies and MUST NOT enter causal research. Live/replay merging
    is not asserted equivalent. Daily completion comes from the candle contract;
    a verified calendar is needed to replace conservative midnight completion.
    """
    from .indicator_alternatives import completed, TIMEFRAMES
    if timeframe not in TIMEFRAMES or dtr_source_timeframe(timeframe) != 'D':
        raise ValueError('weekly/monthly source aggregation not implemented')
    display = (chronological_completed(display,cutoff=cutoff) if timeframe=='Daily' else completed(display,cutoff))
    daily = chronological_completed(daily_stock,cutoff=cutoff)
    if display and daily and display[0].instrument_key != daily[0].instrument_key:
        raise ValueError('DTR instrument mismatch')
    _,atr,rv,pct = _dtr_arithmetic(daily)
    out=[]
    def values(index, atr_index):
        if index is None or atr_index is None:
            return dict(dtr=None,atr14=None,dtr_atr_pct=None,rvol20_ratio=None,rvol20_pct=None)
        dtr=daily[index].high-daily[index].low
        a=atr[atr_index]
        return dict(dtr=dtr,atr14=a,dtr_atr_pct=pine_round(100*dtr/a) if a is not None and a > 0 else None,
                    rvol20_ratio=rv[index],rvol20_pct=pct[index])
    for c in display:
        confirmed=[i for i,d in enumerate(daily) if d.completed_at <= c.completed_at]
        safe=confirmed[-1] if confirmed else None
        current=next((i for i,d in enumerate(daily) if d.start_ist.date()==c.start_ist.date()),None)
        out.append(dict(observation_at=c.completed_at,source_timeframe='D',
            causal_last_completed_daily=values(safe,safe),
            causal_dependency_at=daily[safe].completed_at if safe is not None else None,
            historical_display=values(safe,current),
            historical_atr_dependency_at=daily[current].completed_at if current is not None else None,
            historical_uses_future=current is not None and daily[current].completed_at > c.completed_at,
            historical_display_status='SOURCE_MERGE_HYPOTHESIS;LIVE_REPLAY_PARITY_UNVERIFIED'))
    return tuple(out)
