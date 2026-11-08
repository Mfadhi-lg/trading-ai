"""TA-02B candle contract. No event promotion, filling, indicators or execution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .events import TradingCalendar, aware

IST = ZoneInfo('Asia/Kolkata')
UTC = timezone.utc


@dataclass(frozen=True)
class CandleRequest:
    instrument_key: str
    unit: str
    interval: int
    from_date: date
    to_date: date

    def __post_init__(self):
        import re
        if not re.fullmatch(r'NSE_EQ\|INE[A-Z0-9]{9}|NSE_INDEX\|Nifty 50', self.instrument_key):
            raise ValueError('expected an NSE equity ISIN key or Nifty 50 index key')
        if type(self.interval) is not int or (self.unit, self.interval) not in (('days', 1), ('minutes', 5)):
            raise ValueError('TA-02B supports only daily and five-minute bars')
        if self.from_date > self.to_date:
            raise ValueError('reversed range')
        if self.unit == 'minutes' and (self.to_date-self.from_date).days > 28:
            raise ValueError('bounded intraday range exceeds 28 days')


@dataclass(frozen=True)
class Candle:
    instrument_key: str
    provider_timestamp: str
    start_ist: datetime
    end_ist: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    open_interest: Decimal
    # Daily timestamp labels a calendar bucket, not the cash-market open.
    timestamp_semantics: str
    completed_at: datetime
    session_id: str | None

    @property
    def start_utc(self):
        return self.start_ist.astimezone(UTC)

    @property
    def end_utc(self):
        return self.end_ist.astimezone(UTC)

    def completed_by(self, cutoff: datetime) -> bool:
        """Descriptive completion only; does not establish historical availability."""
        return self.completed_at <= aware(cutoff)


@dataclass(frozen=True)
class CandleBatch:
    request: CandleRequest
    candles: tuple[Candle, ...]  # Provider ordering retained, even when invalid.
    issues: tuple[str, ...]
    missing_starts: tuple[str, ...]
    response_sha256: str
    retrieved_at_utc: datetime
    http_status: int
    raw_response_ref: str
    instrument_metadata_ref: str | None
    calendar_ref: str | None
    synthetic: bool
    adjustment_status: str = 'UNKNOWN_PROVIDER_ADJUSTMENT_BASIS'
    price_currency: str = 'INR'
    volume_units: str = 'provider_reported_units; index_volume_not_equity_turnover'
    provider: str = 'Upstox Historical Candle V3'
    parser_version: str = 'ta02b-v1'
    historical_availability: str = 'NOT_ESTABLISHED_BY_BACKFILLED_CANDLES'

    @property
    def candle_verified(self):
        return (not self.synthetic and not self.issues and bool(self.candles)
                and bool(self.instrument_metadata_ref) and bool(self.raw_response_ref))

    @property
    def event_status(self):
        return 'EVENT_DATE_PROVISIONAL'

    @property
    def backtest_admissible(self):
        # TA-02B deliberately has no path that certifies a trading event.
        return False


def numeric(value, *, integral=False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError('JSON numeric value required (no strings/bools)')
    result = Decimal(value)
    if not result.is_finite() or result < 0 or (integral and result != result.to_integral_value()):
        raise ValueError('invalid nonnegative numeric value')
    return result


def validate_rows(rows, request: CandleRequest, *, retrieved_at: datetime,
                  calendar: TradingCalendar | None = None,
                  calendar_verified: bool = False):
    """Keep every supplied bar. Report gaps only against an explicit calendar.

    Calendar verification is caller evidence, never inferred from weekdays or bars.
    A missing session or absent trade is a gap, not permission to synthesize data.
    """
    aware(retrieved_at)
    issues, candles, stamps = [], [], []
    expected = set()
    if calendar is None:
        issues.append('calendar_unresolved:gaps_and_session_completeness_unknown')
    else:
        if calendar.exchange != 'NSE' or calendar.timezone != 'Asia/Kolkata':
            raise ValueError('calendar venue/timezone mismatch')
        if not calendar_verified:
            issues.append('calendar_provisional')
        if not (calendar.covered_from <= request.from_date <= request.to_date <= calendar.covered_through):
            issues.append('calendar_coverage_gap')
        for session in calendar.sessions:
            if request.from_date <= session.day <= request.to_date and session.opens_at <= retrieved_at < session.closes_at:
                issues.append('incomplete_session_at_retrieval')
            if not request.from_date <= session.day <= request.to_date:
                continue
            cursor = (datetime.combine(session.day, datetime.min.time(), IST)
                      if request.unit == 'days' else session.opens_at.astimezone(IST))
            while cursor < session.closes_at:
                expected.add(cursor)
                if request.unit == 'days':
                    break
                cursor += timedelta(minutes=request.interval)
    sessions = {s.day: s for s in calendar.sessions} if calendar else {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 7:
            raise ValueError('expected [timestamp,open,high,low,close,volume,open_interest]')
        if not isinstance(row[0], str):
            raise ValueError('timestamp string required')
        start = aware(datetime.fromisoformat(row[0])).astimezone(IST)
        if not request.from_date <= start.date() <= request.to_date:
            issues.append('outside_requested_dates')
        o, h, l, c = [numeric(v) for v in row[1:5]]
        v, oi = [numeric(v, integral=True) for v in row[5:7]]
        if min(o, h, l, c) <= 0 or not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError('invalid OHLC constraints')
        session = sessions.get(start.date())
        if request.unit == 'days':
            if start.timetz().replace(tzinfo=None) != datetime.min.time():
                issues.append('invalid_daily_bucket_start')
            end = start + timedelta(days=1)
            completion = session.closes_at if session else end
            semantics = 'provider_calendar_day_start; end_exclusive_next_midnight'
        else:
            end = start + timedelta(minutes=request.interval)
            completion = end
            semantics = 'provider_bar_start; end_exclusive_start_plus_interval'
            if session:
                if not session.opens_at <= start < end <= session.closes_at:
                    issues.append('outside_or_straddling_session')
                if (start-session.opens_at).total_seconds() % (60*request.interval):
                    issues.append('misaligned_interval')
        if calendar and session is None:
            issues.append('non_session_candle')
        if start > retrieved_at:
            issues.append('future_candle')
        if completion > retrieved_at:
            issues.append('incomplete_candle')
        stamps.append(start)
        candles.append(Candle(request.instrument_key, row[0], start, end, o, h, l, c, v, oi,
                              semantics, completion, f'NSE:{start.date()}' if session else None))
    if len(stamps) != len(set(stamps)):
        issues.append('duplicate_candles')
    if len(stamps) > 1:
        if all(a > b for a, b in zip(stamps, stamps[1:])):
            issues.append('provider_descending_order:normalize_explicitly_before_analysis')
        elif not all(a < b for a, b in zip(stamps, stamps[1:])):
            issues.append('unordered_candles')
    missing = tuple(s.isoformat() for s in sorted(expected-set(stamps)))
    if missing:
        issues.append('missing_candles_or_sessions')
    if not candles:
        issues.append('empty_response')
    return tuple(candles), tuple(dict.fromkeys(issues)), missing
