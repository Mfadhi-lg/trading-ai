"""Read-only reporting-period filters. Publication/download dates are never periods."""
from calendar import monthrange, month_abbr
from datetime import date
import re


def normalize(value):
    value = str(value or '').lower().strip()
    if value in ('all', 'unknown'):
        return value
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value):
        raise ValueError('Use YYYY-MM, all, or unknown for --quarter')
    if value == '1970-01' or int(value[:4]) < 1900:
        raise ValueError('Invalid reporting year')
    month=int(value[5:])
    if month not in (3,6,9,12):
        end=((month-1)//3+1)*3
        raise ValueError(f'Choose a quarter-end month: March, June, September or December. For this calendar quarter use {value[:4]}-{end:02d}; September 2026 is 2026-09.')
    return value


def default_quarter(today=None):
    today = today or date.today()
    month = ((today.month - 1) // 3) * 3
    return f'{today.year if month else today.year-1:04d}-{month or 12:02d}'


def label(value, *, fiscal_year_end_month=3, fiscal_year=None, fiscal_quarter=None):
    if value == 'all':
        return 'All Quarters'
    if value == 'unknown' or not value:
        return 'Unknown/Unassigned'
    year, month = map(int, value[:7].split('-'))
    # Explicit company fiscal metadata takes precedence over the March convention.
    quarter = fiscal_quarter or ((month-fiscal_year_end_month-1) % 12)//3+1
    end_year = fiscal_year or year + (month > fiscal_year_end_month)
    return f'FY {end_year-1}-{end_year%100:02d} Q{quarter} — {month_abbr[month]} {year}'


def predicate(column, quarter):
    quarter = normalize(quarter)
    if quarter == 'all':
        return '1=1', ()
    if quarter == 'unknown':
        return f'({column} IS NULL OR {column}=\'\')', ()
    year, month = map(int, quarter.split('-'))
    end = f'{quarter}-{monthrange(year, month)[1]:02d}'
    return f'({column}=? OR {column}=?)', (quarter, end)
