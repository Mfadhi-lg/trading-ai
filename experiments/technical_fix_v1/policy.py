"""Explicit research admission rules; no indicator calculations."""
from collections import Counter
from datetime import datetime

STATES = frozenset(('VERIFIED_DECISION_TIME', 'CONSERVATIVE_TIME_BOUNDED', 'RETROSPECTIVE_ONLY', 'UNAVAILABLE'))
US_CASH = frozenset(('DJIA', 'SP500'))

def inventory(bars):
    counts = Counter((r['series'], r['unit']) for r in bars)
    result = []
    for key in sorted({r['series'] for r in bars}):
        rows = [r for r in bars if r['series'] == key]
        result.append(dict(series=key, accepted_daily=counts[key, 'days'], accepted_minute=counts[key, 'minutes'],
            first=min(r['provider_timestamp'] for r in rows), last=max(r['provider_timestamp'] for r in rows),
            status='ACQUIRED_RETROSPECTIVE_PROVIDER_QUOTATION', identity_status='PARTIAL',
            cash_session_status='QUARANTINED_INTERPRETATION' if key in US_CASH else 'NOT_CERTIFIED',
            daily_first=min((r['provider_timestamp'] for r in rows if r['unit']=='days'),default=None),
            daily_last=max((r['provider_timestamp'] for r in rows if r['unit']=='days'),default=None),
            minute_first=min((r['provider_timestamp'] for r in rows if r['unit']=='minutes'),default=None),
            minute_last=max((r['provider_timestamp'] for r in rows if r['unit']=='minutes'),default=None),
            source_files=sorted({r['source_file'] for r in rows})))
    return result

def eligible_decision_value(row, cutoff):
    """Fail closed: a complete price, product identity and historical delivery proof are required.

    November retrieval time and provider refresh cadence never substitute for delivery.
    A receipt after cutoff must fail even if an earlier advertised publication bound exists.
    """
    if row.get('category') != 'VERIFIED_DECISION_TIME' or row.get('identity_status') != 'VERIFIED':
        return None
    if row.get('series') in US_CASH and row.get('cash_session_status') != 'VERIFIED':
        return None
    if not row.get('publication_evidence') or not row.get('delivery_evidence'):
        return None
    try:
        stamps = [datetime.fromisoformat(row[k]) for k in ('bar_end_ist', 'historical_available_at', 'delivered_at')]
        if cutoff.tzinfo is None or any(s.tzinfo is None or s >= cutoff for s in stamps):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return row.get('retrospective_value')


def refresh_status(status, bars):
    """Derive counts AND coverage dates on every refresh; preserve non-provider channels."""
    by = {r['series']: r for r in inventory(bars)}
    provider_keys = {'USDINR', 'BRENT', 'WTI', 'US_TECH100', 'DJIA', 'GIFT_NIFTY'}
    result = []
    for original in status:
        row = dict(original)
        if row['series'] in provider_keys:
            found = by.get(row['series'])
            row.update(accepted_daily_observations=found['accepted_daily'] if found else 0,
                accepted_minute_observations=found['accepted_minute'] if found else 0,
                first_date=(found['daily_first'] or found['minute_first'])[:10] if found else '',
                last_date=(found['daily_last'] or found['minute_last'])[:10] if found else '',
                status=found['status'] if found else 'UNAVAILABLE')
        result.append(row)
    return result
