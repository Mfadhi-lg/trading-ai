"""Date-driven query priorities; source metadata remains the filing authority."""
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')


def current_result_period(scheduled_for: str, *, date=date, timedelta=timedelta) -> str:
    """Latest completed Indian quarter, prioritised during its publication season.

    Q1 June: July/August; Q2 September: October/November;
    Q3 December: January/February; Q4 March: April/May. Keep polling in
    the remaining month too, because publication dates are not acceptance rules.
    """
    day = date.fromisoformat(scheduled_for)
    start = ((day.month - 1) // 3) * 3 + 1
    end = date(day.year, start, 1) - timedelta(days=1)
    return end.strftime('%Y-%m')


def previous_result_period(period: str) -> str:
    if not re.fullmatch(r'\d{4}-(03|06|09|12)', period):
        raise ValueError('Reporting period must be YYYY-MM with a quarter-end month')
    year, month = map(int, period.split('-'))
    months = year * 12 + month - 1 - 3
    return f'{months // 12:04d}-{months % 12 + 1:02d}'


def priority_result_periods(*, now: datetime | None = None,
                            scheduled_for: str | None = None,
                            override: str | None = None) -> tuple[str, ...]:
    """Primary plus daily late/revision sweep; explicit recovery selects one query.

    scheduled_for is an IST scheduler ledger day, including bounded catch-up.
    These periods select requests, never assign or restrict source filing periods.
    """
    if override is not None:
        previous_result_period(override)  # Validate before any access.
        return (override,)
    if scheduled_for is None:
        moment = now if now is not None else datetime.now(IST)
        if moment.tzinfo is None:
            raise ValueError('An aware datetime is required')
        scheduled_for = moment.astimezone(IST).date().isoformat()
    primary = current_result_period(scheduled_for)
    return primary, previous_result_period(primary)
