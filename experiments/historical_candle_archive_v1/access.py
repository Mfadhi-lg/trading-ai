"""Clean retrospective access to immutable HCD-01/HCD-02 provider receipts.

Retrospective mode uses the chosen receipt revision, NOT a claim that those
values were delivered at the historical bar time. Strict delivery mode retains
HCD-01 receipt gating. Unresolved rows and missing owner grids are returned as
exclusions; indicator_segments never bridges such exclusions.
"""
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
import gzip
import sqlite3

from .acquire import normalize
from .calendar import day_record
from .observations import aggregate_minutes, aware, boundaries, select_asof
from .storage import discover, verify, sha

FIELDS = ('open','high','low','close','volume','open_interest')
TIMEFRAMES = {'Weekly': ('weeks',1), 'Daily': ('days',1), '1-hour': ('hours',1),
              '4-hour': ('hours',4), '1-minute': ('minutes',1)}
CALENDAR_REVIEW_THROUGH = date(2026,11,6)


def values(r):
    return tuple(r[k] for k in FIELDS)


def completed_session_bucket(r):
    """Version interval semantics separately; never move the source receipt."""
    if not r['instrument_key'].startswith('NSE_') or r['timeframe'] not in ('Daily','Weekly'):
        return r
    day=aware(r['start']).date()
    if r['timeframe']=='Weekly':
        monday=day-timedelta(days=day.weekday())
        days=[monday+timedelta(days=i) for i in range(7)]
    else:
        days=[day]
    if min(days)<date(2022,1,1) or max(days)>CALENDAR_REVIEW_THROUGH:
        return r
    sessions=[s for d in days for s in day_record(d)['sessions']]
    if not sessions:
        return dict(r,quality='QUARANTINED_CLOSED_SESSION_BUCKET')
    end=max(s['closes_at'] for s in sessions)
    return dict(r,end=end,end_upper_bound=end,end_basis='HCD02_SESSION_COMPLETION_V1; partial calendar exception review',
                source_normalization_status=r['status'],
                status='COMPLETED_RETROSPECTIVE' if aware(end)<=aware(r['observed_at']) else 'PROVISIONAL')


def resolve_daily(rows):
    """Collapse only value-identical timestamp aliases; preserve every original."""
    days = defaultdict(list)
    for r in rows:
        days[r['start'][:10]].append(r)
    result = []
    for day, part in sorted(days.items()):
        if len(part) == 1:
            result.extend(part)
            continue
        equivalent = len({values(r) for r in part}) == 1
        chosen = dict(min(part, key=lambda r: r['start']))
        chosen['original_provider_values'] = [r for p in part for r in p['original_provider_values']]
        chosen['source_response_sha256s'] = sorted({s for p in part for s in p['source_response_sha256s']})
        if equivalent and all(r['quality'] in ('OHLC_VALID','AMBIGUOUS_DAILY_SESSION_BUCKET') for r in part):
            chosen.update(quality='OHLC_VALID', resolution='EXACT_VALUE_EQUIVALENT_DAILY_ALIASES_V1')
        else:
            chosen.update(quality='AMBIGUOUS_DAILY_SESSION_BUCKET', resolution='UNRESOLVED_DIFFERENT_VALUES')
        result.append(chosen)
    return result


def owner_day(hourly, minutes, record, interval, decision_time=None):
    """Use exact native grids or actual minute constituents, separately per session."""
    output = []
    for session in record['sessions']:
        start, end = aware(session['opens_at']), aware(session['closes_at'])
        through=min(end,aware(decision_time)) if decision_time else end
        grid = [(s,e) for s,e in boundaries(start, end, 60) if e<=through]
        expected={s.isoformat():e for s,e in grid}
        part = [r for r in hourly if r['start'] in expected and aware(r['observed_at'])>=expected[r['start']]]
        if ([r['start'] for r in part] == [s.isoformat() for s,e in grid]
                and all(r['quality'] == 'OHLC_VALID' for r in part)):
            canonical = [dict(r, end=e.isoformat(), end_upper_bound=e.isoformat()) for r,(s,e) in zip(part,grid)]
            # A native hourly bar may straddle a break even with the right start.
            # Split sessions therefore require minute evidence for their short bar.
            if len(record['sessions']) > 1:
                canonical = None
        else:
            canonical = None
        if canonical is None:
            source = [r for r in minutes if start <= aware(r['start']) < end]
            if through<=start:
                continue
            if not source or any(r['quality'] != 'OHLC_VALID' for r in source if aware(r['start'])<through):
                raise ValueError('MISSING_OR_INVALID_MINUTE_CONSTITUENTS')
            receipt=max(r['observed_at'] for r in source)
            prefix_end=min(through,aware(receipt).replace(second=0,microsecond=0))
            canonical = aggregate_minutes(source, session, 60, prefix_end.isoformat(), receipt)
            for r in canonical:
                used = [x for x in source if aware(r['start']) <= aware(x['start']) < aware(r['end'])]
                r['original_provider_values'] = [v for x in used for v in x['original_provider_values']]
                r['source_response_sha256s'] = sorted({v for x in used for v in x['source_response_sha256s']})
        for s,e in boundaries(start,end,interval):
            if e>through:
                continue
            used = [r for r in canonical if s <= aware(r['start']) < e]
            if not used or aware(used[-1]['end']) != e:
                raise ValueError('INCOMPLETE_OWNER_BUCKET')
            r = dict(used[0], start=s.isoformat(), end=e.isoformat(), end_upper_bound=e.isoformat(),
                     timeframe=f'{interval//60}-hour', definition='OWNER_SESSION_ANCHORED_HCD02_V1',
                     end_basis=';'.join(record['source_refs']), high=max(x['high'] for x in used),
                     low=min(x['low'] for x in used), close=used[-1]['close'],
                     volume=sum(x['volume'] for x in used), observed_at=max(x['observed_at'] for x in used),
                     quality='OHLC_VALID', status='COMPLETED_RETROSPECTIVE',
                     original_provider_values=[v for x in used for v in x['original_provider_values']],
                     source_response_sha256s=sorted({v for x in used for v in x['source_response_sha256s']}))
            output.append(r)
    return output


class HistoricalStore:
    def __init__(self):
        self.mount, self.info = discover()
        self.device = self.mount.stat().st_dev
        self.root = self.mount / 'passport/trading-ai/market-data/historical-candle-archive-v1'
        verify(self.mount, self.device)
        if self.root.is_symlink() or self.root.resolve().stat().st_dev != self.device:
            raise RuntimeError('STOP_ACCESS_ARCHIVE_NOT_ON_PASSPORT')
        self.db = sqlite3.connect(f'file:{self.root}/inventory.sqlite3?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=30000')
        self.db.execute('PRAGMA query_only=ON')

    def close(self):
        self.db.close()

    def developing(self, company_id, timeframe, *, decision_time, mode='retrospective', revision_asof=None):
        """A separate minute-evidenced prefix; never slice a later hourly candle.

        Retrospective reconstruction is explicitly distinct from a saved live
        observation. Missing minutes return an exclusion, never a filled prefix.
        """
        if timeframe not in ('1-hour','4-hour') or mode not in ('retrospective','strict_delivery'):
            raise ValueError('developing requires 1-hour/4-hour and an explicit access mode')
        cutoff=aware(decision_time)
        day=str(cutoff.date())
        revision=min(aware(revision_asof or cutoff),cutoff).isoformat() if mode=='strict_delivery' else revision_asof
        minutes,receipts=self.native(company_id,'1-minute',revision,day,day)
        result=dict(rows=[],exclusions=[],source_revisions=receipts,mode=mode,
                    assumptions='Minute prefix reconstruction; not a historical live snapshot or completed bar')
        interval=60 if timeframe=='1-hour' else 240
        for session in day_record(day)['sessions']:
            for start,end in boundaries(session['opens_at'],session['closes_at'],interval):
                if not start<cutoff<end:
                    continue
                if minutes and not minutes[0]['instrument_key'].startswith('NSE_'):
                    result['exclusions'].append(dict(day=day,reason='BSE_SESSION_CALENDAR_UNVERIFIED'));return result
                part=[r for r in minutes if start<=aware(r['start'])<cutoff]
                try:
                    if not part or any(r['quality']!='OHLC_VALID' for r in part):
                        raise ValueError('MISSING_OR_INVALID_MINUTE_CONSTITUENTS')
                    active=dict(session,opens_at=start.isoformat(),closes_at=end.isoformat())
                    receipt=max(r['observed_at'] for r in part)
                    prefix_cutoff=min(cutoff,aware(receipt).replace(second=0,microsecond=0))
                    part=[r for r in part if aware(r['start'])<prefix_cutoff]
                    rows=aggregate_minutes(part,active,interval,prefix_cutoff.isoformat(),receipt)
                    for r in rows:
                        r.update(original_provider_values=[v for x in part for v in x['original_provider_values']],
                                 source_response_sha256s=sorted({v for x in part for v in x['source_response_sha256s']}),
                                 reconstruction_status='RETROSPECTIVE_MINUTE_PREFIX' if mode=='retrospective' else 'RECEIPT_GATED_PREFIX')
                    result['rows']=rows
                except ValueError as error:
                    result['exclusions'].append(dict(day=day,reason=str(error)))
        return result

    def native(self, company_id, timeframe, revision_asof=None, start=None, end=None):
        unit, interval = TIMEFRAMES[timeframe]
        observations = {}
        receipts = []
        # Release the SQLite read lock before decompressing potentially large files.
        sql="SELECT * FROM requests WHERE company_id=? AND unit=? AND interval=? AND status='SUCCESS'"
        args=[company_id,unit,interval]
        if start:
            sql+=' AND to_date>=?';args.append(start)
        if end:
            sql+=' AND from_date<=?';args.append(end)
        saved_requests=self.db.execute(sql+' ORDER BY received_at,request_id',args).fetchall()
        for saved in saved_requests:
            if revision_asof and aware(saved['received_at']) > aware(revision_asof):
                continue
            raw = gzip.decompress((self.root / saved['raw_path']).read_bytes())
            if sha(raw) != saved['raw_sha256']:
                raise RuntimeError('STOP_ACCESS_RAW_CHECKSUM')
            q = dict(saved, timeframe=timeframe)
            rows, _ = normalize(raw,q,saved['received_at'],quarantine_negative=True)
            receipts.append(dict(request_id=saved['request_id'],sha256=saved['raw_sha256'],received_at=saved['received_at']))
            for row in rows:
                row['original_provider_values'] = [{k:row[k] for k in ('provider_timestamp',*FIELDS,'source_response_sha256','observed_at')}]
                row['source_response_sha256s'] = [row['source_response_sha256']]
                observations[row['start']] = row
        rows = sorted(observations.values(), key=lambda r:r['start'])
        rows=resolve_daily(rows) if timeframe=='Daily' else rows
        return [completed_session_bucket(r) for r in rows], receipts

    def read(self, company_id, timeframe, *, start=None, end=None, mode='retrospective',
             decision_time=None, revision_asof=None, definition='owner', adjusted_version=None):
        if timeframe not in TIMEFRAMES or timeframe == '1-minute':
            raise ValueError('choose Weekly, Daily, 4-hour or 1-hour')
        if mode not in ('retrospective','strict_delivery') or definition not in ('owner','native'):
            raise ValueError('invalid mode or definition')
        if adjusted_version is not None:
            raise ValueError('no evidence-certified adjusted series is currently available')
        if mode == 'strict_delivery' and decision_time is None:
            raise ValueError('strict delivery requires decision_time')
        cutoff = decision_time or datetime.now(timezone.utc).isoformat()
        revision = min(aware(revision_asof or cutoff), aware(cutoff)).isoformat() if mode == 'strict_delivery' else revision_asof
        excluded = []
        if timeframe in ('1-hour','4-hour') and definition == 'owner':
            hourly, receipts = self.native(company_id,'1-hour',revision,start,end)
            minutes, minute_receipts = self.native(company_id,'1-minute',revision,start,end)
            receipts.extend(minute_receipts)
            byday, minday = defaultdict(list), defaultdict(list)
            for r in hourly: byday[r['start'][:10]].append(r)
            for r in minutes: minday[r['start'][:10]].append(r)
            rows = []
            for day in sorted(set(byday) | set(minday)):
                record = day_record(day)
                # NSE's calendar cannot certify a BSE session.
                if hourly and hourly[0]['instrument_key'].startswith('BSE_'):
                    excluded.append(dict(day=day,reason='BSE_SESSION_CALENDAR_UNVERIFIED'))
                    continue
                try:
                    if not record['sessions']:
                        raise ValueError('UNVERIFIED_OR_CLOSED_SESSION')
                    rows.extend(owner_day(byday[day],minday[day],record,60 if timeframe=='1-hour' else 240,cutoff))
                except ValueError as error:
                    excluded.append(dict(day=day,reason=str(error)))
        else:
            rows, receipts = self.native(company_id,timeframe,revision,start,end)
        # A source daily defect is also an unresolved constituent of its week.
        # Do not let a syntactically valid native weekly candle bypass that flag.
        if timeframe == 'Weekly':
            daily, daily_receipts = self.native(company_id,'Daily',revision,start,end)
            receipts.extend(daily_receipts)
            bad_weeks = {aware(r['start']).date() - timedelta(days=aware(r['start']).weekday())
                         for r in daily if r['quality'] != 'OHLC_VALID'}
            rows = [dict(r,quality='QUARANTINED_DAILY_CONSTITUENT')
                    if aware(r['start']).date() - timedelta(days=aware(r['start']).weekday()) in bad_weeks else r for r in rows]
        clean = []
        for r in rows:
            day = r['start'][:10]
            if (start and day < start) or (end and day > end):
                continue
            if r['quality'] != 'OHLC_VALID':
                excluded.append(dict(day=day,reason=r['quality'],original_provider_values=r['original_provider_values']))
                continue
            r = dict(r, data_quality_flags=['PROVIDER_ADJUSTMENT_BASIS_UNCERTIFIED','CALENDAR_EXCEPTION_REVIEW_PARTIAL'],
                     adjusted_version=None)
            if mode == 'strict_delivery':
                if not select_asof([r],cutoff):
                    continue
            elif r['status'] == 'PROVISIONAL' or aware(r.get('end') or r['end_upper_bound']) > aware(cutoff):
                continue
            clean.append(r)
        excluded = [r for r in excluded if (not start or r['day']>=start) and (not end or r['day']<=end)]
        if start and end and timeframe != 'Weekly':
            # An entirely absent session must be visible, not just bad stored rows.
            present = {r['start'][:10] for r in clean} | {r['day'] for r in excluded}
            instrument = next((r['instrument_key'] for r in rows), '')
            day = max(date.fromisoformat(start),date(2022,1,1))
            through = min(date.fromisoformat(end),aware(cutoff).date())
            while day <= through:
                sessions=day_record(day)['sessions']
                expected_ends=[aware(s['closes_at']) for s in sessions] if timeframe=='Daily' else [e
                    for s in sessions for _,e in boundaries(s['opens_at'],s['closes_at'],60 if timeframe=='1-hour' else 240)]
                if str(day) not in present and any(e<=aware(cutoff) for e in expected_ends):
                    excluded.append(dict(day=str(day),reason='BSE_CALENDAR_UNVERIFIED_OR_MISSING_SESSION' if instrument.startswith('BSE_') else 'MISSING_EXPECTED_SESSION'))
                day += timedelta(days=1)
        elif start and end and timeframe=='Weekly':
            day=max(date.fromisoformat(start),date(2022,1,1))
            while day.weekday()!=0:day+=timedelta(days=1)
            through=min(date.fromisoformat(end),aware(cutoff).date())
            present={aware(r['start']).date()-timedelta(days=aware(r['start']).weekday()) for r in clean}
            present|={date.fromisoformat(r['day'])-timedelta(days=date.fromisoformat(r['day']).weekday()) for r in excluded}
            while day<=through:
                days=[day+timedelta(days=i) for i in range(7)]
                if days[-1]<=CALENDAR_REVIEW_THROUGH:
                    sessions=[s for d in days for s in day_record(d)['sessions']]
                    if sessions and aware(max(s['closes_at'] for s in sessions))<=aware(cutoff) and day not in present:
                        excluded.append(dict(day=str(day),reason='MISSING_EXPECTED_WEEKLY_BUCKET'))
                day+=timedelta(days=7)
        return dict(company_id=company_id,timeframe=timeframe,definition=definition,mode=mode,rows=clean,
                    exclusions=excluded,source_revisions=receipts,available_adjusted_versions=[],
                    requested_window=dict(start=start,end=end),
                    assumptions='Latest selected provider receipts; historical completion is not historical delivery. '
                    'Native source preserved without local adjustment; provider adjustment basis unknown. '
                    'Retrospective minute prefixes are never historical live snapshots.')


def indicator_segments(result):
    """Refuse to silently bridge known defects; callers choose a certified window.

    Calendar completeness and adjustment flags must also be reviewed by callers.
    This guard intentionally rejects a requested window with any exclusion.
    """
    if result['exclusions']:
        raise ValueError('requested indicator window contains quarantined or missing owner sessions')
    window=result.get('requested_window',{})
    if not window.get('start') or not window.get('end'):
        raise ValueError('indicators require an explicit bounded historical window')
    return result['rows']
