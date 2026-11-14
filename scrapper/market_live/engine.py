"""Append-only live evidence and separate minute/developing chart projection.

This store is independent of the HCD archive; accepted historical files are
never opened. Provider one-minute snapshots replace by event time, never sum.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import fcntl
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import sqlite3
import shutil

from experiments.historical_candle_archive_v1.observations import boundaries
from experiments.historical_candle_archive_v1.storage import atomic, dump
from scrapper.market_refresh.planner import aware
from .protocol import decode


def _number(value, integral=False):
    if isinstance(value,bool): raise ValueError('invalid candle number')
    result = Decimal(str(value))
    if not result.is_finite() or result < 0 or integral and result != int(result):
        raise ValueError('invalid candle number')
    return result


class LiveStore:
    def __init__(self, root, calendar):
        self.root = Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.calendar = calendar
        if not (self.root/'chart.html').exists():
            shutil.copyfile(Path(__file__).with_name('chart.html'),self.root/'chart.html')
        self.db = sqlite3.connect(self.root/'live.sqlite3')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS frames(
            id INTEGER PRIMARY KEY, provider_ms INTEGER, received_at TEXT, sha256 TEXT, raw_path TEXT);
            CREATE TABLE IF NOT EXISTS observations(
            frame_id INTEGER, instrument TEXT, start TEXT, provider_ms INTEGER,
            received_at TEXT, completed INTEGER, payload TEXT,
            PRIMARY KEY(frame_id,instrument,start));''')
        self.db.execute('CREATE INDEX IF NOT EXISTS observation_lookup ON observations(instrument,start,provider_ms)')
        self.db.commit()

    def close(self):
        self.db.close()

    def ingest(self, raw, received_at, allowed, *, _rest_frame=None):
        frame = decode(raw) if _rest_frame is None else _rest_frame
        received = aware(received_at)
        provider = datetime.fromtimestamp(frame['currentTs']/1000,timezone.utc)
        if not frame['currentTs'] or provider > received + timedelta(seconds=30):
            raise ValueError('invalid/future provider timestamp')
        digest = hashlib.sha256(raw).hexdigest()
        path = self.root/'raw'/(digest+('.pb' if _rest_frame is None else '.json'))
        path.parent.mkdir(exist_ok=True)
        if not path.exists(): atomic(path,raw)
        elif hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('raw evidence checksum mismatch')
        with self.db:
            cursor = self.db.execute('INSERT INTO frames(provider_ms,received_at,sha256,raw_path) VALUES(?,?,?,?)',
                (frame['currentTs'] if _rest_frame is None else None,received.isoformat(),digest,str(path.relative_to(self.root))))
            seen = set()
            for key, feed in frame['feeds'].items():
                if key not in allowed: continue
                if feed.get('ltpc'):
                    price=_number(feed['ltpc'].get('ltp',0))
                    trade_time=feed['ltpc'].get('ltt',0)
                    if price<=0 or type(trade_time) is not int or trade_time<0 or trade_time>frame['currentTs']+30000:
                        raise ValueError('invalid LTPC price/timestamp')
                for candle in feed.get('ohlc',[]):
                    if candle.get('interval') != 'I1': continue
                    start = datetime.fromtimestamp(candle['ts']/1000,timezone.utc)
                    if start.second or start.microsecond or start > provider:
                        raise ValueError('invalid minute timestamp')
                    record = self.calendar(start.astimezone(ZoneInfo('Asia/Kolkata')).date())
                    if record.get('verified') is not True:
                        raise ValueError('unverified minute calendar')
                    if not any(aware(s['opens_at']) <= start and start+timedelta(minutes=1)<=aware(s['closes_at']) for s in record['sessions']):
                        raise ValueError('minute outside reviewed session')
                    values = {k:_number(candle.get(k,0),k=='vol') for k in ('open','high','low','close','vol')}
                    if not 0 < values['low'] <= min(values['open'],values['close']) <= max(values['open'],values['close']) <= values['high']:
                        raise ValueError('invalid OHLC')
                    if (key,start) in seen: raise ValueError('duplicate minute in frame')
                    seen.add((key,start))
                    complete = start+timedelta(minutes=1) <= min(provider,received)
                    payload = dict(start=start.isoformat(),**{k:str(v) for k,v in values.items()},
                        source_sha256=digest,provider_timestamp=frame['currentTs'] if _rest_frame is None else None,
                        completion_bound_ms=frame['currentTs'],provider_candle_timestamp=(
                            candle.get('provider_candle_timestamp') or candle['ts']),received_at=received.isoformat(),
                        provider='Upstox Feed V3' if _rest_frame is None else 'Upstox Candle V3 reconciliation')
                    self.db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',
                        (cursor.lastrowid,key,start.isoformat(),frame['currentTs'],received.isoformat(),int(complete),json.dumps(payload)))
        return frame

    def ingest_rest(self, raw, received_at, key, expected_day):
        payload = json.loads(raw,parse_float=Decimal)
        if payload.get('status') != 'success':
            raise ValueError('unsuccessful reconciliation response')
        candles = []
        for row in payload['data']['candles']:
            if not isinstance(row,list) or len(row)!=7:
                raise ValueError('invalid reconciliation candle')
            stamp=aware(row[0])
            if str(stamp.astimezone(ZoneInfo('Asia/Kolkata')).date())!=str(expected_day):
                raise ValueError('reconciliation outside requested date')
            # Preserve original response bytes; no tick/cumulative reconstruction.
            candles.append(dict(interval='I1',ts=int(stamp.timestamp()*1000),
                open=row[1],high=row[2],low=row[3],close=row[4],vol=row[5],provider_candle_timestamp=row[0]))
        return self.ingest(raw,received_at,{key},_rest_frame=dict(type=1,
            currentTs=int(aware(received_at).timestamp()*1000),feeds={key:dict(ohlc=candles)}))

    def minutes(self, key, cutoff=None, day=None):
        # Completed evidence wins over a late partial snapshot. Newer completed
        # revisions remain distinct observations and rebuild dependent views.
        selected = {}
        query='SELECT payload,completed,provider_ms,frame_id FROM observations WHERE instrument=?'
        params=[key]
        if day is not None:
            query+=' AND start>=? AND start<?'
            params.extend([str(day)+'T00:00:00+00:00',str(day+timedelta(days=1))+'T00:00:00+00:00'])
        rows=self.db.execute(query+' ORDER BY completed,provider_ms,frame_id',params)
        for payload,completed,_,_ in rows:
            row = json.loads(payload); row['completed']=bool(completed)
            if cutoff is not None and (aware(row['received_at'])>cutoff or row['completion_bound_ms']>cutoff.timestamp()*1000): continue
            selected[row['start']] = row
        return sorted(selected.values(),key=lambda r:r['start'])

    def chart(self, key, day, now):
        record = self.calendar(day)
        if record.get('verified') is not True:
            return dict(instrument_key=key,status='UNKNOWN',completed={},developing={},missing_minutes=[])
        cutoff = aware(now)
        rows = self.minutes(key,cutoff,day)
        completed, developing, missing = {}, {}, []
        lookup = {aware(r['start']):r for r in rows}
        for session in record.get('sessions',[]):
            for start,end in boundaries(session['opens_at'],session['closes_at'],1):
                if end <= cutoff and (start not in lookup or not lookup[start]['completed']):
                    missing.append(start.isoformat())
        for label, size in (('1-hour',60),('4-hour',240),('Daily',None)):
            complete_bars, partial_bars = [], []
            grids = [(aware(s['opens_at']),aware(s['closes_at'])) for s in record.get('sessions',[])]
            if label=='Daily' and grids:
                buckets=[(grids[0][0],grids[-1][1],grids)]
            else:
                buckets=[(a,b,[(a,b)]) for s in record.get('sessions',[]) for a,b in boundaries(s['opens_at'],s['closes_at'],size)]
            for start,end,segments in buckets:
                expected = [a for x,y in segments for a,b in boundaries(x,y,1)]
                part = [lookup[a] for a in expected if a in lookup]
                if not part or start>cutoff: continue
                valid = len(part)==len(expected) and all(r['completed'] for r in part) and end<=cutoff
                bar = dict(start=start.isoformat(),end=end.isoformat(),open=part[0]['open'],
                    high=str(max(Decimal(r['high']) for r in part)),low=str(min(Decimal(r['low']) for r in part)),
                    close=part[-1]['close'],volume=str(sum(Decimal(r['vol']) for r in part)),
                    status='COMPLETED' if valid else 'GAP' if end<=cutoff else 'DEVELOPING',missing_constituents=len(expected)-len(part),
                    source_sha256s=sorted({r['source_sha256'] for r in part}))
                (complete_bars if valid else partial_bars).append(bar)
            completed[label],developing[label]=complete_bars,partial_bars
        return dict(instrument_key=key,day=str(day),status='GAP' if missing else 'FRESH',
            completed=completed,developing=developing,missing_minutes=missing,
            latest_completed_minute=max((r['start'] for r in rows if r['completed'] and aware(r['start']).astimezone(ZoneInfo('Asia/Kolkata')).date()==day),default=None),
            adjustment_status='UNKNOWN_PROVIDER_ADJUSTMENT_BASIS',historical_delivery_verified=False)

    def indicators(self, key, day, now):
        from .indicators import calculate
        first=self.db.execute('SELECT min(start) FROM observations WHERE instrument=?',(key,)).fetchone()[0]
        bars={'1-hour':[], '4-hour':[]}
        if first:
            cursor=aware(first).astimezone(ZoneInfo('Asia/Kolkata')).date()
            while cursor<=day:
                record=self.calendar(cursor)
                if record.get('verified') is not True:
                    for values in bars.values():values.clear()
                elif record.get('sessions'):
                    chart=self.chart(key,cursor,now)
                    for timeframe in bars:
                        current=chart['completed'][timeframe]+chart['developing'][timeframe]
                        present={aware(r['start']) for r in current}
                        size=60 if timeframe=='1-hour' else 240
                        current += [dict(start=a.isoformat(),status='GAP')
                            for session in record['sessions']
                            for a,b in boundaries(session['opens_at'],session['closes_at'],size)
                            if b<=aware(now) and a not in present]
                        # Entire absent sessions also break indicator continuity.
                        if not current:
                            bars[timeframe].clear()
                        else:
                            bars[timeframe].extend(current)
                cursor+=timedelta(days=1)
        return {timeframe:calculate(values) for timeframe,values in bars.items()}

    def publish(self, keys, day, now):
        instruments=[]
        for key in sorted(keys):
            chart=self.chart(key,day,now)
            chart['indicators']=self.indicators(key,day,now)
            instruments.append(chart)
        value = dict(schema_version=1,observed_at=aware(now).isoformat(),instruments=instruments)
        with (self.root/'.chart.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            dump(self.root/'chart.json',value)
            script='window.FOCUS_CHART='+json.dumps(value,default=str,sort_keys=True).replace('<','\\u003c')+';\n'
            atomic(self.root/'chart.js',script.encode())
        return value
