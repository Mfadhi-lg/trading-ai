"""Three document lanes; source metadata supplies period, never the selector."""
from calendar import monthrange
from ..sources.exchange.reporting_periods import priority_result_periods
from .availability import parse_utc


def current_period(now):
    period = priority_result_periods(now=parse_utc(now))[0]
    year, month = map(int, period.split('-'))
    return period + '-%02d' % monthrange(year, month)[1]


def bounded_lanes(rows, *, limit, historical_limit, epoch, now):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Document batch limit must be 1–100')
    if type(historical_limit) is not int or not 0 <= historical_limit <= min(1, limit):
        raise ValueError('Historical capacity must be zero or one within total limit')
    activated = parse_utc(epoch)
    period = current_period(now)
    lanes = {1: [], 2: [], 3: []}
    for row in rows:
        fresh = parse_utc(row['first_observed_at_utc']) >= activated
        lane = (1 if row['period_end'] == period else 2) if fresh else 3
        lanes[lane].append(row)
    # Reserve late/revised capacity as Phase 3 does, within the hard total.
    selected = []
    def take(lane):
        if lanes[lane] and len(selected) < limit:
            selected.append((lanes[lane].pop(0), lane))
    take(1 if lanes[1] else 2)
    if historical_limit: take(3)
    take(2)
    while len(selected) < limit and (lanes[1] or lanes[2]):
        take(1 if lanes[1] else 2)
    return selected
