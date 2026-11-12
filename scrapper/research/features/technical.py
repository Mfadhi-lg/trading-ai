"""Read-only daily indicators over the canonical historical candle access API."""
from datetime import date, datetime
from decimal import Decimal

from experiments.earnings_reaction_v1.indicators import pine_average, daily_rvol
from experiments.historical_candle_archive_v1.access import (
    CALENDAR_REVIEW_THROUGH, indicator_segments,
)

VERSION = 'historical-daily-indicators-v1'


def daily_indicators(store, company_id, *, start, end, decision_time,
                     mode='strict_delivery', revision_asof=None,
                     price_period=20, atr_period=14, volume_period=20):
    """Caller owns store lifetime; never opens a database or writes output.

    Returns source rows and receipt metadata alongside Decimal indicator values.
    Known exclusions fail the whole window; warmup and zero denominators are null.
    """
    for period in (price_period, atr_period, volume_period):
        if type(period) is not int or period <= 0:
            raise ValueError('positive integer periods required')
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    cutoff = datetime.fromisoformat(decision_time)
    if cutoff.utcoffset() is None:
        raise ValueError('aware decision_time required')
    if not date(2022, 1, 1) <= first <= last <= CALENDAR_REVIEW_THROUGH:
        raise ValueError('window outside reviewed archive calendar')
    result = store.read(company_id, 'Daily', start=start, end=end, mode=mode,
                        decision_time=decision_time, revision_asof=revision_asof,
                        definition='owner')
    rows = indicator_segments(result)
    previous_start = None
    instrument = None
    for row in rows:
        stamp = datetime.fromisoformat(row['start'])
        completion = datetime.fromisoformat(row.get('end') or row['end_upper_bound'])
        if (stamp.utcoffset() is None or completion.utcoffset() is None
                or completion > cutoff or completion <= stamp
                or not first <= stamp.date() <= last
                or (previous_start is not None and stamp <= previous_start)
                or row['quality'] != 'OHLC_VALID' or row['timeframe'] != 'Daily'
                or not row['instrument_key'].startswith('NSE_')
                or (instrument is not None and instrument != row['instrument_key'])):
            raise ValueError('invalid, mixed, unordered or incomplete daily rows')
        for field in ('open', 'high', 'low', 'close', 'volume'):
            value = row[field]
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError('finite nonnegative Decimal OHLCV required')
        if (min(row['open'], row['close']) < row['low']
                or max(row['open'], row['close']) > row['high']
                or row['low'] <= 0):
            raise ValueError('positive consistent OHLC required')
        previous_start, instrument = stamp, row['instrument_key']
    closes = [r['close'] for r in rows]
    trs = [None if i == 0 else max(r['high']-r['low'],
           abs(r['high']-rows[i-1]['close']), abs(r['low']-rows[i-1]['close']))
           for i, r in enumerate(rows)]
    sma = pine_average(closes, price_period, 'SMA')
    ema = pine_average(closes, price_period, 'EMA')
    atr = pine_average(trs, atr_period, 'RMA')
    rvol = daily_rvol([r['volume'] for r in rows], volume_period)
    return dict(source=result, version=VERSION,
                parameters=dict(price_period=price_period, atr_period=atr_period,
                                volume_period=volume_period),
                indicators=[dict(start=r['start'], sma=sma[i], ema=ema[i],
                                 true_range=trs[i], atr=atr[i], rvol=rvol[i])
                            for i, r in enumerate(rows)])
