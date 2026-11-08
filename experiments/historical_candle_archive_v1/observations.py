"""Bitemporal candle observations: interval completion never implies delivery."""
from datetime import datetime, timedelta
from decimal import Decimal

def aware(value):
    result=datetime.fromisoformat(value) if isinstance(value,str) else value
    if result.tzinfo is None: raise ValueError('aware time required')
    return result

def boundaries(opens_at,closes_at,minutes):
    start,end=aware(opens_at),aware(closes_at)
    if start>=end or minutes not in (1,60,240): raise ValueError('invalid session or interval')
    while start<end:
        stop=min(start+timedelta(minutes=minutes),end)
        yield start,stop
        start=stop

def aggregate_minutes(rows,session,minutes,decision_time,observed_at):
    """Aggregate complete minute prefixes only; never slice a completed hourly bar.

    This independent aggregation reconciles the existing indicator aggregate;
    it does not duplicate indicator mathematics. Explicit split sessions must
    be passed separately, so a maintenance break never becomes traded time.
    """
    cutoff=aware(decision_time);receipt=aware(observed_at)
    if receipt<cutoff: raise ValueError('receipt cannot precede requested snapshot')
    output=[]
    for start,end in boundaries(session['opens_at'],session['closes_at'],minutes):
        prefix_end=min(end,cutoff)
        if start>=prefix_end: continue
        prefix=[r for r in rows if start<=aware(r['start']) and aware(r['start'])+timedelta(minutes=1)<=prefix_end]
        expected=int((prefix_end-start).total_seconds()//60)
        expected_starts=[start+timedelta(minutes=i) for i in range(expected)]
        if [aware(r['start']) for r in prefix]!=expected_starts:
            raise ValueError('source minute gap or duplicate; no fill')
        if not prefix: continue
        complete=end<=cutoff
        r=dict(prefix[0]);r.update(start=start.isoformat(),end=end.isoformat(),
            end_upper_bound=end.isoformat(),end_basis=session['evidence_ref'],
            timeframe=f'{minutes//60}-hour',definition='OWNER_SESSION_ANCHORED',
            high=max(Decimal(r['high']) for r in prefix),low=min(Decimal(r['low']) for r in prefix),
            close=Decimal(prefix[-1]['close']),open=Decimal(prefix[0]['open']),
            volume=sum(r['volume'] for r in prefix),observed_at=receipt.isoformat(),
            prefix_through=prefix[-1]['start'],
            status='COMPLETED_RETROSPECTIVE' if complete else 'PROVISIONAL',
            historical_delivery_verified=False)
        output.append(r)
    return output

def select_asof(rows,decision_time,include_provisional=False):
    """Strict historical delivery mode. A fresh backfill cannot enter an old test.

    The caller must supply stored receipt observations. Later revisions never
    overwrite old receipts. A developing observation is usable only in its
    explicitly requested provisional channel.
    """
    cutoff=aware(decision_time);selected={};daily_starts={}
    for r in rows:
        if r['timeframe']=='Daily' and aware(r['observed_at'])<=cutoff:
            daily_starts.setdefault((r['instrument_key'],r['definition'],r['start'][:10]),set()).add(r['start'])
    for r in rows:
        if aware(r['observed_at'])>cutoff or r.get('quality')!='OHLC_VALID': continue
        if r['timeframe']=='Daily' and len(daily_starts[(r['instrument_key'],r['definition'],r['start'][:10])])>1:
            continue
        if r['status']=='PROVISIONAL':
            if not include_provisional: continue
        elif r['status'] not in ('COMPLETED','COMPLETED_RETROSPECTIVE'):
            continue
        elif aware(r.get('end') or r['end_upper_bound'])>cutoff:
            continue
        key=(r['instrument_key'],r['timeframe'],r['definition'],r['start'])
        if key not in selected or aware(selected[key]['observed_at'])<aware(r['observed_at']):
            selected[key]=r
    return sorted(selected.values(),key=lambda r:r['start'])

def incremental_windows(expected_sessions,completed_dates,watermark,through,max_days=32):
    """Explicit exchange session list; old gaps and a bounded revision overlap."""
    if max_days<1 or max_days>32: raise ValueError('recovery bound 1..32')
    expected=sorted(set(d for d in expected_sessions if d<=through))
    missing=[d for d in expected if d not in completed_dates]
    recent=[d for d in expected if d>=watermark][-5:]
    selected=sorted(set(missing+recent))[:max_days]
    return [(d,d) for d in selected]
