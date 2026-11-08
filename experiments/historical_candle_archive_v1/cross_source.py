"""Independent retained TradingView OHLCV comparison; never adjusts provider data."""
import csv
from collections import Counter,defaultdict
from decimal import Decimal
import json
from .storage import ROOT,OUT,Archive,dump,sha
from .analyze import load,FIELDS
from .prepare import csvout

def main():
    archive=Archive();groups,_,_,_=load(archive)
    pilot={c['symbol']:c['company_id'] for c in json.loads((OUT/'pilot-companies.json').read_text())}
    base=ROOT/'artifacts/earnings-reaction-v1/simple-volume-reconstruction'
    summary=[];differences=[]
    for symbol,dirname in [('ICICIBANK','daily-icicibank-holdout'),('HDFCBANK','daily-hdfcbank-holdout'),('TIPSMUSIC','daily-listing-history')]:
        p=base/dirname/'reference.csv';result=json.loads((p.parent/'result.json').read_text())
        reference_symbol=result.get('symbol')
        if reference_symbol is None and dirname=='daily-listing-history':
            # Resolve the retained unnamed reference by the original verified
            # application constants and frozen manifest, never a name guess.
            source=ROOT/'artifacts/earnings-reaction-v1/simple-volume-reconstruction/apply_to_upstox.py'
            frozen=json.loads((base/'MANIFEST.json').read_text())
            reference_key=str(p.relative_to(ROOT))
            if frozen.get(reference_key)!=sha(p.read_bytes()):raise ValueError('unnamed reference checksum mismatch')
            if "TIPSMUSIC='NSE_EQ|INE716B01029'" not in (ROOT/'experiments/earnings_reaction_v1/retrieve_ta03r4.py').read_text():
                raise ValueError('original source identity changed')
            reference_symbol='NSE:TIPSMUSIC'
        if reference_symbol!='NSE:'+symbol: raise ValueError('reference identity mismatch')
        native={r['start'][:10]:r for r in groups.get((pilot[symbol],'Daily'),[]) if r['quality']=='OHLC_VALID'}
        counts=Counter();recent=Counter();actions=[]
        with p.open() as f:
            for r in csv.DictReader(f):
                ds=r['bar_start_ist'][:10]
                if ds not in native: continue
                counts['overlapping_dates']+=1
                for k in FIELDS:
                    delta=Decimal(native[ds][k])-Decimal(r[k])
                    tolerance=Decimal('1') if k=='volume' else max(Decimal('0.005'),abs(Decimal(r[k]))*Decimal('0.000001'))
                    equal=abs(delta)<=tolerance
                    counts[k+'_match' if equal else k+'_different']+=1
                    if ds>='2025-01-01': recent[k+'_match' if equal else k+'_different']+=1
                    if not equal:
                        differences.append(dict(company_id=pilot[symbol],symbol=symbol,date=ds,field=k,
                            upstox_minus_retained_reference=str(delta),tolerance=str(tolerance),
                            action='PRESERVE_BOTH; ADJUSTMENT_OR_PROVIDER_DIFFERENCE_UNRESOLVED'))
        summary.append(dict(company_id=pilot[symbol],symbol=symbol,reference=str(p.relative_to(ROOT)),
                            reference_sha256=sha(p.read_bytes()),reference_retrieved_at=result.get('finished_at'),
                            all_overlap_counts=dict(counts),since2025_counts=dict(recent),
                            price_tolerance='max(0.005 INR, reference*1e-6)',volume_tolerance='1 provider unit',
                            corporate_action_status='UNRESOLVED; agreement is bounded cross-feed evidence, never adjustment certification'))
    dump(OUT/'independent-price-volume-comparison.json',summary)
    csvout(OUT/'independent-price-volume-differences.csv',differences)
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
