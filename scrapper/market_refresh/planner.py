"""Pure incremental planning over explicit, verified exchange-session evidence.

The caller supplies current verified identities and calendar records. This module
never infers a live subscription from historical focus labels, opens a database,
or requests data. Unknown calendars cannot confer freshness.
"""
from datetime import date, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from experiments.earnings_reaction_v1.upstox_candles import url_for

VERSION = 'LIVE-FRESH-01-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def aware(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if value.utcoffset() is None:
        raise ValueError('aware timestamp required')
    return value


def sessions(calendar, first, last, now):
    """Return completed dates and unresolved dates; closed dates stay distinct."""
    expected, unknown = [], []
    day = first
    while day <= last:
        record = calendar(day)
        if record.get('verified') is not True:
            unknown.append(str(day))
        elif record.get('sessions'):
            end = max(aware(s['closes_at']) for s in record['sessions'])
            if end <= now:
                expected.append(str(day))
        elif record.get('status') != 'CLOSED':
            unknown.append(str(day))
        day += timedelta(days=1)
    return expected, unknown


def coverage_report(company, saved, *, calendar, now, lookback_days=10):
    now = aware(now).astimezone(ZoneInfo('Asia/Kolkata'))
    first = max(date.fromisoformat(company.get('eligible_since', '2022-01-01')),
                now.date() - timedelta(days=lookback_days))
    expected, unknown = sessions(calendar, first, now.date(), now)
    validated = set(saved.get('validated_sessions', []))
    excluded = {r['day']: r for r in saved.get('excluded_sessions', [])
                if r.get('reason') and r.get('evidence_ref')}
    missing = [day for day in expected if day not in validated and day not in excluded]
    latest = max(validated, default=None)
    latest_expected = max(expected, default=None)
    blocked = saved.get('last_result') in ('BLOCKED', 'HTTP_401', 'HTTP_403', 'HTTP_429', 'UNCERTAIN_DELIVERY')
    identity = company.get('identity_verified') is True and bool(company.get('identity_ref'))
    state = ('BLOCKED' if blocked or not identity else 'UNKNOWN' if unknown or latest_expected is None
             else 'GAP' if missing and latest == latest_expected else 'STALE' if missing
             else 'FRESH' if latest_expected in validated else 'GAP')
    return dict(instrument_key=company['instrument_key'], company_id=company['company_id'],
                status=state, latest_validated_daily=latest,
                latest_validated_intraday=saved.get('latest_validated_intraday'),
                expected_latest_completed_session=latest_expected,
                missing_sessions=missing, excluded_sessions=list(excluded.values()),
                unresolved_calendar_dates=unknown, coverage_window_start=str(first),
                last_refresh_attempt=saved.get('last_refresh_attempt'),
                last_result=saved.get('last_result'), provider='Upstox V3',
                identity_ref=company.get('identity_ref'),
                raw_response_evidence=saved.get('raw_response_evidence', []),
                historical_coverage='NOT_CERTIFIED_BY_RECENT_REFRESH')


def _priority(company):
    return (0 if company.get('focus') else 1 if company.get('index_constituent')
            else 2 if company.get('kind') == 'NSE_INDEX' else 3)


def _ranges(days):
    """Combine consecutive calendar dates only, with a conservative 28-day cap."""
    runs = []
    for value in sorted(set(days)):
        day = date.fromisoformat(value)
        if runs and day == runs[-1][-1] + timedelta(days=1) and len(runs[-1]) < 28:
            runs[-1].append(day)
        else:
            runs.append([day])
    return [(str(run[0]), str(run[-1])) for run in runs]


def plan_refresh(companies, coverage, *, calendar, now, budget=100,
                 lookback_days=10, revision_dates=None, revision_id='',
                 history_idle=False, historical_gaps=None):
    """Prioritize recent missing dates without launching/restarting a backfill.

    Explicit source-revision dates require a new revision_id and retain old
    receipts. Ordinary plans never re-download a validated date. Fresh focus
    one-minute gaps are supplied independently through reconciliation_requests.
    """
    if type(budget) is not int or not 0 <= budget <= 1800:
        raise ValueError('bounded request budget 0..1800 required')
    if type(lookback_days) is not int or not 1 <= lookback_days <= 366:
        raise ValueError('bounded recent window 1..366 days required')
    if revision_dates and not revision_id:
        raise ValueError('explicit source revision identity required')
    now = aware(now).astimezone(ZoneInfo('Asia/Kolkata'))
    keys = [c['instrument_key'] for c in companies]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate instrument identities')
    requests, reports = [], []
    for company in sorted(companies, key=lambda c: (_priority(c), c['instrument_key'])):
        key = company['instrument_key']
        kind = company.get('kind', 'STOCK')
        if kind not in ('STOCK', 'NSE_INDEX') or not key.startswith('NSE_INDEX|' if kind == 'NSE_INDEX' else 'NSE_EQ|'):
            raise ValueError('verified NSE equity/index identities required')
        report = coverage_report(company, coverage.get(key, {}), calendar=calendar,
                                 now=now, lookback_days=lookback_days)
        reports.append(report)
        if report['status'] in ('BLOCKED', 'UNKNOWN'):
            continue
        selected = set(report['missing_sessions'])
        for day in (revision_dates or {}).get(key, []):
            expected, unknown = sessions(calendar, date.fromisoformat(day), date.fromisoformat(day), now)
            if unknown or not expected:
                raise ValueError('source revisions require completed verified sessions')
            selected.add(day)
        for start, end in _ranges(selected):
            scope = dict(company_id=company['company_id'], instrument_key=key,
                         unit='days', interval=1, timeframe='Daily', from_date=start,
                         to_date=end, kind=kind, identity_ref=company['identity_ref'],
                         acquisition_day=str(now.date()),
                         revision_id=revision_id if any(start <= d <= end for d in (revision_dates or {}).get(key, [])) else '')
            request = dict(scope, request_id='LIVE_FRESH_' + digest(scope),
                           priority=_priority(company), series=key)
            request['url'] = url_for(SimpleNamespace(instrument_key=key, unit='days', interval=1,
                                                    from_date=date.fromisoformat(start), to_date=date.fromisoformat(end)))
            requests.append(request)
    if history_idle and historical_gaps and not requests:
        # A finite caller-supplied gap list is not a new historical campaign.
        # Never service it while any recent request remains, even beyond budget.
        repair = plan_refresh(companies, coverage, calendar=calendar, now=now,
                              budget=budget, lookback_days=lookback_days,
                              revision_dates=historical_gaps, revision_id='EXPLICIT_HISTORICAL_GAP_REPAIR_V1')
        for query in repair['requests']:
            query['priority'] = 4
        repair['repair_lane'] = 'IDLE_EXPLICIT_GAPS_ONLY'
        return repair
    return dict(version=VERSION, created_at=now.isoformat(), requests=requests[:budget],
                deferred_requests=len(requests[budget:]), coverage=reports,
                max_requests=budget, historical_backfill_started=False,
                calendar_contract='EXPLICIT_VERIFIED_RECORDS_ONLY')
