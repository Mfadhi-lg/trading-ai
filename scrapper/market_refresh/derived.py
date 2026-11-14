"""Versioned, fail-closed refresh of existing daily indicator/index engines."""
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from experiments.historical_candle_archive_v1.storage import dump
from scrapper.indices.service import daily
from scrapper.research.features.technical import daily_indicators
from .planner import aware, digest, coverage_report


class VerifiedStore:
    """Read-only calendar overlay; never mutates historical raw/receipt lineage."""
    def __init__(self, store, calendar):
        self.store, self.calendar = store, calendar

    @property
    def root(self):
        return self.store.root

    def close(self):
        self.store.close()

    def read(self, company_id, timeframe, *, start, end, decision_time,
             mode='retrospective', revision_asof=None, definition='owner'):
        if mode not in ('retrospective','strict_delivery') or definition not in ('owner','native'):
            raise ValueError('explicit canonical access mode/definition required')
        if timeframe != 'Daily':
            return self.store.read(company_id, timeframe, start=start, end=end,
                                   decision_time=decision_time, mode=mode,
                                   revision_asof=revision_asof, definition=definition)
        cutoff = aware(decision_time)
        revision = min(aware(revision_asof or cutoff), cutoff).isoformat() if mode == 'strict_delivery' else revision_asof
        rows, receipts = self.store.native(company_id, 'Daily', revision, start, end)
        clean, excluded, present = [], [], set()
        for original in rows:
            row = dict(original)
            day = row['start'][:10]
            if not start <= day <= end:
                continue
            present.add(day)
            record = self.calendar(date.fromisoformat(day))
            if record.get('verified') is not True or not record.get('sessions'):
                excluded.append(dict(day=day, reason='UNVERIFIED_OR_CLOSED_SESSION'))
                continue
            completed = max(aware(s['closes_at']) for s in record['sessions'])
            if completed > cutoff:
                continue
            if row['quality'] != 'OHLC_VALID':
                excluded.append(dict(day=day, reason=row['quality']))
                continue
            if aware(row['observed_at']) < completed:
                excluded.append(dict(day=day, reason='DEVELOPING_SOURCE_RECEIPT'))
                continue
            if not row['instrument_key'].startswith('NSE_EQ|'):
                excluded.append(dict(day=day, reason='NSE_EQUITY_IDENTITY_REQUIRED'))
                continue
            row.update(end=completed.isoformat(), end_upper_bound=completed.isoformat(),
                       end_basis='LIVE_FRESH_REVIEWED_SESSION', status='COMPLETED_RETROSPECTIVE',
                       volume=Decimal(row['volume']),
                       data_quality_flags=['PROVIDER_ADJUSTMENT_BASIS_UNCERTIFIED'], adjusted_version=None)
            clean.append(row)
        day = date.fromisoformat(start)
        while str(day) <= end:
            record = self.calendar(day)
            if record.get('verified') is not True:
                excluded.append(dict(day=str(day), reason='UNVERIFIED_CALENDAR'))
            elif record.get('sessions') and str(day) not in present:
                if max(aware(s['closes_at']) for s in record['sessions']) <= cutoff:
                    excluded.append(dict(day=str(day), reason='MISSING_EXPECTED_SESSION'))
            day += timedelta(days=1)
        return dict(rows=clean, exclusions=excluded, source_revisions=receipts,
                    requested_window=dict(start=start, end=end), mode=mode)


def collect_coverage(store, companies, *, calendar, now, lookback_days=10, receipts=()):
    """Derive coverage from canonical validated source rows, never last-tick age."""
    at = aware(now).astimezone(ZoneInfo('Asia/Kolkata'))
    start = str(at.date()-timedelta(days=lookback_days))
    view = VerifiedStore(store, calendar)
    result = {}
    for company in companies:
        if company.get('kind', 'STOCK') != 'STOCK':
            continue
        source = view.read(company['company_id'], 'Daily', start=start, end=str(at.date()),
                           decision_time=at.isoformat())
        source['exclusions'].extend(dict(day=r['start'][:10],reason='UNRESOLVED_INSTRUMENT_CONTINUITY')
            for r in source['rows'] if r['instrument_key'] != company['instrument_key'])
        source['rows'] = [r for r in source['rows'] if r['instrument_key'] == company['instrument_key']]
        latest = [r for r in receipts if r.get('series') == company['instrument_key']]
        last = max(latest, key=lambda r:r.get('attempt_started_at', ''), default={})
        saved = dict(validated_sessions=[r['start'][:10] for r in source['rows']],
                     raw_response_evidence=source['source_revisions'],
                     excluded_sessions=[dict(day=r['day'], reason=r['reason'], evidence_ref='canonical archive exclusion')
                                        for r in source['exclusions'] if r['reason'] != 'MISSING_EXPECTED_SESSION'],
                     last_refresh_attempt=last.get('attempt_started_at'), last_result=last.get('status'))
        # Quarantined/unverified rows remain repairable missing sessions; they are
        # not authorized trading exclusions such as a documented suspension.
        saved['excluded_sessions'] = []
        result[company['instrument_key']] = saved
    return result


def refresh_outputs(store, companies, *, calendar, now, output, start, end,
                    groups=(), benchmark=None, actions=()):
    """Recompute full warmup windows on every source revision, retain old builds.

    Groups use existing index service contracts: {code,members:[symbol,...]}.
    Partial chains, corporate-action warnings and retrospective membership labels
    survive. No available-member reweighting or synthetic gap bridging is added.
    """
    view = VerifiedStore(store, calendar)
    at = aware(now).isoformat()
    prices, indicators, sources = {}, {}, {}
    for company in companies:
        if company.get('kind', 'STOCK') != 'STOCK':
            continue
        symbol = company.get('symbol', company['instrument_key'])
        data = view.read(company['company_id'], 'Daily', start=start, end=end, decision_time=at)
        if any(r['instrument_key'] != company['instrument_key'] for r in data['rows']):
            raise ValueError('canonical instrument continuity mismatch')
        sources[symbol] = {k:v for k,v in data.items() if k != 'rows'}
        prices[symbol] = [dict(symbol=symbol, instrument_key=r['instrument_key'], date=r['start'][:10],
                               **{'raw_'+k:Decimal(str(r[k])) for k in ('open','high','low','close','volume')},
                               source_response_sha256s=r.get('source_response_sha256s', [r['source_response_sha256']]))
                          for r in data['rows']]
        try:
            indicators[symbol] = daily_indicators(view, company['company_id'], start=start, end=end,
                                                  decision_time=at, mode='retrospective', reviewed_calendar=calendar)
        except ValueError:
            indicators[symbol] = dict(status='GAP', reason='SOURCE_WINDOW_HAS_EXCLUSIONS', source=data)
    grid = []
    day = date.fromisoformat(start)
    while str(day) <= end:
        record = calendar(day)
        if record.get('verified') is not True:
            raise ValueError('unverified derived-output calendar')
        if record.get('sessions') and max(aware(s['closes_at']) for s in record['sessions']) <= aware(now):
            grid.append(str(day))
        day += timedelta(days=1)
    indices = {}
    for group in groups:
        rows = daily(prices, grid, benchmark or {}, members=group['members'], actions=actions)
        indices[group['code']] = dict(rows=rows,
            status='COMPLETED_PROVISIONAL' if rows and all(r['index_close'] is not None for r in rows) else 'GAP',
            membership_status='RETROSPECTIVE_MEMBERSHIP', action_status='PROVIDER_ACTION_BASIS_UNCERTIFIED')
    content = dict(version='LIVE-FRESH-01-derived-v1', start=start, end=end,
                   sources=sources, indicators=indicators, indices=indices)
    ident = digest(content)
    base = Path(output)
    destination = base/'builds'/ident
    destination.mkdir(parents=True, exist_ok=True)
    path = destination/'derived.json'
    if path.exists() and digest(json.loads(path.read_text(), parse_float=Decimal)) != ident:
        raise ValueError('retained derived output checksum mismatch')
    if not path.exists():
        dump(path, content)
    dump(base/'latest.json', dict(build=ident, path=str(path.relative_to(base)), refreshed_at=at,
                                  status='PROVISIONAL', source_revision_digest=digest(sources)))
    return dict(build=ident, path=path, content=content)
