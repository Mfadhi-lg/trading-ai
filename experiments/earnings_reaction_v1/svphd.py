"""SVP HD reconstruction primitives; automatic HD resolution remains unresolved.

Explicit price edges and underlying candles are required. Reference-supported
range allocation uses unrounded volumes for level selection. Callers must use
only completed observations and pass the appropriate lower timeframe feed.
"""
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

from .indicator_alternatives import completed, value_area

D = Decimal
VERSION = 'svphd-explicit-grid-candidate-v2'


def coarse_edges(low, high, *, tick_size, rows=25):
    """Session-low anchored grid; closest row count, smaller width wins ties.

    Matches 134 retained KPL rough grids. Other instruments require validation.
    Explicit tick size is required; never infer it from displayed precision.
    """
    low, high, tick_size = (D(str(v)) for v in (low, high, tick_size))
    if not all(v.is_finite() for v in (low, high, tick_size)) or tick_size <= 0 or high < low:
        raise ValueError('invalid price range or tick')
    if type(rows) is not int or not 1 <= rows <= 100000:
        raise ValueError('invalid row target')
    low = (low/tick_size).to_integral_value(rounding=ROUND_FLOOR)*tick_size
    span = (high-low)/tick_size
    ideal = span/rows
    candidates = {max(1, int(ideal.to_integral_value(rounding=r))) for r in (ROUND_FLOOR, ROUND_CEILING)}
    count = lambda n: max(1, int((span/n).to_integral_value(rounding=ROUND_CEILING)))
    ticks = min(candidates, key=lambda n: (abs(count(n)-rows), n))
    n = count(ticks)
    if n > 100000:
        raise ValueError('grid exceeds bounded row limit')
    return tuple(low+i*ticks*tick_size for i in range(n+1))


def detailed_edges(low, high, *, range_low, range_high, tick_size, target_rows=550):
    """Candidate HD grid from an explicit calculation range, anchored at zero.

    The 550-row target fits two controlled KPL ranges; other settings and
    instruments remain unverified. The caller must supply the actual lower
    timeframe data range used by the original study, including partial sessions.
    """
    low, high, range_low, range_high, tick = (D(str(x)) for x in (low, high, range_low, range_high, tick_size))
    if not all(x.is_finite() for x in (low,high,range_low,range_high,tick)) or tick <= 0 or high < low or range_high < range_low:
        raise ValueError('invalid price range or tick')
    if type(target_rows) is not int or not 1 <= target_rows <= 100000:
        raise ValueError('invalid row target')
    from decimal import ROUND_HALF_UP
    width=max(tick,((range_high-range_low)/D(target_rows)/tick).to_integral_value(rounding=ROUND_HALF_UP)*tick)
    start=(low/width).to_integral_value(rounding=ROUND_FLOOR)*width
    count=max(1,int(((high-start)/width).to_integral_value(rounding=ROUND_CEILING)))
    if count>100000:
        raise ValueError('grid exceeds bounded row limit')
    return tuple(start+i*width for i in range(count+1))


def coarse_profile(candles, *, cutoff, tick_size, rows=25):
    """Calculate a coarse candidate from completed lower-timeframe candles.

    Supply exactly one session and the appropriate source resolution. Source
    resolution selection is not inferred by this interface.
    """
    bars = completed(candles, cutoff)
    if not bars:
        raise ValueError('empty profile')
    if len({c.session_id for c in bars}) != 1 or bars[0].session_id is None:
        raise ValueError('one explicit session required')
    edges = coarse_edges(min(c.low for c in bars), max(c.high for c in bars), tick_size=tick_size, rows=rows)
    result = profile(bars, cutoff=cutoff, edges=edges)
    result['status'] = 'AUTOMATIC_COARSE_GRID_CANDIDATE'
    return result


def profile(candles, *, cutoff, edges, value_area_fraction=D('.7')):
    """Calculate volume rows and levels on a caller-supplied contiguous grid.

This function does not infer TradingView's viewport-dependent grid or source
resolution. POC ties select the lowest row, an unresolved reference boundary.
    """
    bars = completed(candles, cutoff)
    edges = tuple(D(str(e)) for e in edges)
    if not bars:
        raise ValueError('empty profile')
    if not 2 <= len(edges) <= 100001 or any(not e.is_finite() for e in edges):
        raise ValueError('invalid bounded price grid')
    if any(a >= b for a, b in zip(edges, edges[1:])):
        raise ValueError('price edges must increase')
    if min(c.low for c in bars) < edges[0] or max(c.high for c in bars) > edges[-1]:
        raise ValueError('grid must cover every candle')
    up, down = [D(0)] * (len(edges)-1), [D(0)] * (len(edges)-1)
    previous_close, direction = None, True
    for c in bars:
        if c.close != c.open:
            direction = c.close > c.open
        elif previous_close is not None and c.close != previous_close:
            direction = c.close > previous_close
        previous_close = c.close
        target = up if direction else down
        if c.high == c.low:
            index = next((i for i, high in enumerate(edges[1:]) if c.low <= high), len(target)-1)
            target[index] += c.volume
            continue
        for i, (low, high) in enumerate(zip(edges, edges[1:])):
            overlap = max(D(0), min(c.high, high)-max(c.low, low))
            target[i] += c.volume * overlap / (c.high-c.low)
    total = tuple(a+b for a,b in zip(up,down))
    area = value_area(total, value_area_fraction)
    p,lo,hi,coverage = area if area else (None,None,None,None)
    return dict(version=VERSION, status='EXPLICIT_GRID_CANDIDATE', edges=edges,
                up=tuple(up), down=tuple(down), total=total,
                poc=(edges[p]+edges[p+1])/2 if area else None,
                val=edges[lo] if area else None, vah=edges[hi+1] if area else None,
                actual_value_area_fraction=coverage,
                available_at=max(c.completed_at for c in bars))
