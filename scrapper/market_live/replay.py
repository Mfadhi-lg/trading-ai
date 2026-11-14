"""Deterministic synthetic V3 wire replay, independent of credentials/network."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import struct

from .engine import LiveStore

KEY='NSE_EQ|INE000A01000'
KEYS=[f'NSE_EQ|INE{i:03d}A01000' for i in range(20)]
DAY='2026-11-13'
OPEN=datetime.fromisoformat(DAY+'T09:15:00+05:30')


def varint(value):
    value &= (1<<64)-1
    result=bytearray()
    while value>=128:
        result.append((value&127)|128);value>>=7
    result.append(value)
    return bytes(result)


def field(number, value, wire=2):
    key=varint(number*8+wire)
    if wire==0:return key+varint(value)
    if wire==1:return key+struct.pack('<d',value)
    value=value.encode() if isinstance(value,str) else value
    return key+varint(len(value))+value


def frame(candles, provider, key=KEY, cumulative=999999):
    ohlc=b''
    for start,volume,close in candles:
        row=field(1,'I1')+b''.join(field(n,v,1) for n,v in ((2,100.),(3,105.),(4,99.),(5,close)))
        row+=field(6,volume,0)+field(7,int(start.timestamp()*1000),0)
        ohlc+=field(1,row)
    market=field(1,field(1,102.,1)+field(2,int(provider.timestamp()*1000),0))+field(4,ohlc)+field(6,cumulative,0)
    feed=field(2,field(1,market))
    return field(1,1,0)+field(2,field(1,key)+field(2,feed))+field(3,int(provider.timestamp()*1000),0)


def calendar(day):
    return dict(day=str(day),verified=str(day)==DAY,status='REGULAR_SCHEDULED',
        sessions=[dict(opens_at=OPEN.isoformat(),closes_at=(OPEN+timedelta(minutes=375)).isoformat(),
                       evidence_ref='SYNTHETIC_OFFLINE_SESSION')])


def run(output):
    output=Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('replay requires a new empty output directory')
    store=LiveStore(output,calendar)
    try:
        for index in range(375):
            start=OPEN+timedelta(minutes=index)
            provider=start+timedelta(minutes=1,seconds=1)
            # One real protobuf map entry per explicit instrument, one response.
            from .protocol import fields
            raw=field(1,1,0)+b''.join(field(2,value) for key in KEYS
                for n,w,value in fields(frame([(start,100,102.)],provider,key)) if n==2)
            raw+=field(3,int(provider.timestamp()*1000),0)
            store.ingest(raw,provider,set(KEYS))
            if index%10==0:store.ingest(raw,provider,set(KEYS)) # duplicate snapshots
        # A later arrival of older event data cannot alter accepted values.
        store.ingest(frame([(OPEN,10,101.)],OPEN+timedelta(seconds=30)),OPEN+timedelta(hours=7),{KEY})
        result=store.publish(set(KEYS),OPEN.date(),OPEN+timedelta(hours=7))
        row=result['instruments'][0]
        assert row['status']=='FRESH'
        assert len(row['completed']['1-hour'])==7
        assert len(row['completed']['4-hour'])==2
        assert all(r['completed']['Daily'][0]['volume']=='37500' for r in result['instruments'])
        shutil.copyfile(Path(__file__).with_name('chart.html'),output/'chart.html')
        return dict(status='PASS',synthetic=True,chart=str(output/'chart.html'),data=str(output/'chart.json'),
                    instruments=20,hourly=7,four_hour=2,daily=1,volume_per_instrument='37500',production_touched=False)
    finally:store.close()
