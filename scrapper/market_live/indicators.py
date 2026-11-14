"""Receipt-derived intraday indicators using the maintained Decimal engines."""
from decimal import Decimal
from experiments.earnings_reaction_v1.indicators import pine_average, pine_rvol


def calculate(bars, *, price_period=20, atr_period=14, volume_period=20):
    if any(type(p) is not int or p<1 for p in (price_period,atr_period,volume_period)):
        raise ValueError('positive indicator periods required')
    # A missing completed bucket breaks the entire prior chain. Never bridge it.
    segment=[]
    for bar in sorted(bars,key=lambda r:r['start']):
        if bar['status']=='DEVELOPING':continue
        if bar['status']!='COMPLETED':segment=[];continue
        segment.append(bar)
    closes=[Decimal(r['close']) for r in segment]
    volume=[Decimal(r['volume']) for r in segment]
    tr=[None if i==0 else max(Decimal(r['high'])-Decimal(r['low']),
        abs(Decimal(r['high'])-closes[i-1]),abs(Decimal(r['low'])-closes[i-1])) for i,r in enumerate(segment)]
    sma=pine_average(closes,price_period,'SMA');ema=pine_average(closes,price_period,'EMA')
    atr=pine_average(tr,atr_period,'RMA');rvol,_=pine_rvol(volume,volume_period)
    return dict(version='LIVE_FRESH_INTRADAY_V1',status='FRESH' if len(segment)>max(price_period,atr_period,volume_period) else 'WARMUP',
        warmup_bars=len(segment),parameters=dict(price_period=price_period,atr_period=atr_period,volume_period=volume_period),
        values=[dict(start=r['start'],sma=sma[i],ema=ema[i],atr=atr[i],rvol=rvol[i]) for i,r in enumerate(segment)],
        adjustment_status='UNKNOWN_PROVIDER_ADJUSTMENT_BASIS',historical_delivery_verified=False)
