from pathlib import Path
from datetime import date
import gzip,json
from experiments.earnings_reaction_v1.er03 import ROOT,digest,write_json
from experiments.earnings_reaction_v1.retrieve_er03 import DailyRequest
from experiments.earnings_reaction_v1.upstox_candles import url_for

OUT=ROOT/'artifacts/technical-integration-v1';INPUT=OUT/'inputs'
V2=ROOT/'artifacts/subsector-index-v2'
MACRO=ROOT/'artifacts/earnings-reaction-v1/er04/market-context'

def main():
    INPUT.mkdir(parents=True,exist_ok=True)
    baseline=INPUT/'preservation-before.json'
    if not baseline.exists():
        roots=[ROOT/'artifacts'/n for n in ('subsector-index-v1','subsector-index-v2','earnings-reaction-v1/er03/chart-upgrade','earnings-reaction-v1/er04','subsector-ss01','subsector-ss02','subsector-ss03','subsector-ss04')]
        paths={p for root in roots for p in root.rglob('*') if p.is_file()}
        paths|={p for p in (ROOT/'experiments/earnings_reaction_v1').rglob('*.py')}
        write_json(baseline,{str(p.relative_to(ROOT)):digest(p) for p in sorted(paths)})
    globalmaster=json.loads(gzip.decompress((INPUT/'global.json.gz').read_bytes()))
    nse=json.loads(gzip.decompress((INPUT/'nse-current.json.gz').read_bytes()))
    specs=[('SP500','GLOBAL_INDEX|^GSPC'),('DJIA','GLOBAL_INDEX|^DJI'),('INDIA_VIX','NSE_INDEX|India VIX'),
        ('NIFTY_AUTO','NSE_INDEX|Nifty Auto'),('NIFTY_CONSUMER_DURABLES','NSE_INDEX|NIFTY CONSR DURBL')]
    requests=[]
    for s,key in specs:
        matches=[r for r in globalmaster+nse if r['instrument_key']==key]
        if len(matches)!=1:raise ValueError('Unambiguous instrument required')
        q=DailyRequest(key,'days',1,date(2025,12,1),date(2026,10,30))
        requests.append(dict(number=len(requests)+1,series=s,instrument_key=key,identity=matches[0],unit='days',interval=1,
            from_date=str(q.from_date),to_date=str(q.to_date),url=url_for(q),
            admission='Daily descriptive context; provider date/availability semantics must be reviewed. No automatic decision-time certification.'))
    manifest=OUT/'ACQUISITION_PROPOSAL.json'
    write_json(manifest,dict(task='TECH-05',status='PENDING_EXPLICIT_OWNER_APPROVAL',request_count=5,requests=requests,
        metadata={str(p.relative_to(ROOT)):digest(p) for p in (INPUT/'global.json.gz',INPUT/'nse-current.json.gz')},
        execution_policy='Serial >=1 second apart, one attempt each, zero automatic retries, stop on first failure; no substitutions or redirect following',
        existing_authorization='Earlier nine SS-INDEX-02 requests exhausted; no new approval inferred',
        not_requested=['Nifty50 and midsml400 already retained','US Tech100 is not Nasdaq Composite','DOW FUTURES is not ES/NQ','GIFT generic index lacks dated contract identity','Capital Mkt is not Capital Goods','Oil/FX validated retained sources reused'],
        documentation='https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/'))
    (OUT/'ACQUISITION_PROPOSAL.sha256').write_text(digest(manifest)+'\n')
    write_json(INPUT/'source-review.json',dict(reviewed_at='2026-11-04',
        metadata_sources=['https://assets.upstox.com/market-quote/instruments/exchange/global.json.gz','https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz'],
        global_support_effective='2026-05-11',global_metadata_warning='Advertised latency and fixed start/end clock strings do not establish historical daily-bar observation/publication times or US DST handling.',
        sources=[dict(url='https://www.niftyindices.com/Factsheet/Factsheet_Nifty_Capital_Goods.pdf',publication='2026-10-30 factsheet',finding='Official sectoral index; launch June15 2026, base April1 2005. Base history predates launch and is retrospective. No unambiguous Upstox key found; do not use Capital Markets.'),
        dict(url='https://www.niftyindices.com/Factsheet/Factsheet_nifty_consumer_durables.pdf',publication='2026-10-30 factsheet',finding='Official consumer-durables sectoral benchmark, launch January15 2020; broader than EMS and not equivalent to core electronics contract manufacturing.'),
        dict(url='https://niftyindices.com/indices/equity/sectoral-indices/nifty-auto',publication='Current page; historical methodology in retained 2023 official method',finding='Broad automotive exposure includes vehicle makers, ancillaries and tyres; not tyre-only.'),
        dict(url='https://www.nyse.com/publicdocs/nyse/ICE_NYSE_2026_Yearly_Trading_Calendar.pdf',publication='2025-12-10 calendar vintage',finding='Use New York timezone/US holidays and early closes; calendar does not create index observations.')]))
    print(json.dumps(dict(requests=5,sha256=digest(manifest))))

if __name__=='__main__':main()
