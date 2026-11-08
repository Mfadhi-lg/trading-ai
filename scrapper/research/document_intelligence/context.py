"""Local claim and table context. Dates never become publication certificates."""
from __future__ import annotations

import calendar
from datetime import datetime
import re

PERIOD = re.compile(r"\b(?:(?:Q[1-4]|[1-4]Q|H[12]|[169]M)\s*(?:FY\s*)?['’]?(?:20\s*\d\s*\d|\d{2,4})|FY\s*['’]?(?:20\s*\d\s*\d|\d{2,4}))\b", re.I)
UNIT = re.compile(r"(?:INR|Rs\.?|₹|USD|\$)\s*(?:in\s+)?(?:crores?|crs?\.?|lakhs?|millions?|mn|billions?|bn)|%|percent|times|\bx\b|\b(?:MTPA|MW|GW|MT|tonnes|units)\b", re.I)
DATE = r"(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?[A-Za-z]+\s+['’]?\d{2,4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})"


def period_name(text):
    m = PERIOD.search(text)
    if not m:
        spoken = re.search(r'\b(first|second|third|fourth) quarter of (?:the )?financial year (20\d{2})\b', text, re.I)
        if spoken:
            return f"Q{['first','second','third','fourth'].index(spoken[1].lower())+1}FY{spoken[2][-2:]}"
        return None
    raw = re.sub(r"[\s'’]", "", m.group()).upper()
    raw = re.sub(r"^([1-4])Q", r"Q\1", raw)
    if "FY" not in raw:
        m = re.fullmatch(r'(Q[1-4]|H[12]|[169]M)(\d{2,4})', raw)
        raw = m[1] + 'FY' + m[2] if m else raw
    return raw[:-4] + raw[-2:] if re.search(r"FY\d{4}$", raw) else raw


def date_name(text):
    raw = re.sub(r"(\d)(?:st|nd|rd|th)\b|\bof\s+", r"\1", text, flags=re.I).strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%d %B '%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def time_context(text):
    asof = re.search(r"\b(?:as of|as on|at end of)\s+(" + DATE + r")", text, re.I)
    periods = {period_name(m.group()) for m in PERIOD.finditer(text)}
    period = next(iter(periods)) if len(periods) == 1 else None
    # Duration-bearing financial headers are supported; a bare cover date isn't.
    duration = re.search(r"\b(quarter|three months|half.year|six months|nine months|year|twelve months)\s+ended\s+(" + DATE + r")", text, re.I)
    if duration:
        end = date_name(duration[2])
        if end:
            year, month, _ = map(int, end.split('-'))
            fy = (year + (month > 3)) % 100
            prefix = {'quarter': f'Q{((month-4)%12)//3+1}', 'three months': f'Q{((month-4)%12)//3+1}',
                      'six months': 'H1' if month == 9 else 'H2', 'half-year': 'H1' if month == 9 else 'H2',
                      'nine months': '9M', 'year': '', 'twelve months': ''}.get(duration[1].lower())
            if prefix is not None:
                period = f'{prefix}FY{fy:02}'
    return period, date_name(asof[1]) if asof else None


def table_period(text, *, annual=False):
    period = period_name(text)
    if period:
        return period
    match = re.search(DATE, text)
    date = date_name(match.group()) if match else None
    # Financial annual summaries explicitly ending March 31; never arbitrary dates.
    if annual and date and date.endswith('-03-31'):
        return 'FY' + date[2:4]
    return time_context(text)[0]


def clauses(text):
    # Split only at real sentence/clause boundaries, preserving decimal dots and
    # connecting descriptions such as "Dharavi project, as we call it".
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z])|;\s+|\s+(?:while|whereas|but)\s+|,\s+(?=the new capacit)|\s+and\s+(?=(?:we\s+(?:expect|plan|target)|(?:our\s+)?(?:revenue|PAT|EBITDA|profit)\b))", text) if x.strip()]


def entity_scope(text):
    if re.search(r'\bconsolidated before exceptional items\b', text, re.I):
        return 'consolidated_before_exceptional_items'
    if re.search(r'\bconsolidated\b', text, re.I):
        return 'consolidated'
    if re.search(r'\bstandalone\b', text, re.I):
        return 'standalone'
    return None
