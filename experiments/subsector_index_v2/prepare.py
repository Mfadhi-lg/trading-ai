"""Freeze existing evidence and propose bounded requests; never authenticate."""
from pathlib import Path
from datetime import date
import csv, gzip, json, shutil
from experiments.earnings_reaction_v1.er03 import ROOT, read_csv, digest, write_json
from experiments.subsector_index_v1.sources import write_rows
from experiments.earnings_reaction_v1.retrieve_er03 import DailyRequest
from experiments.earnings_reaction_v1.upstox_candles import url_for

OUT=ROOT/'artifacts/subsector-index-v2'
INPUT=OUT/'inputs'
V1=ROOT/'artifacts/subsector-index-v1'
PEERS=('APOLLOTYRE','JKTYRE','BALKRISIND','DIXON','AVALON','KAYNES','POWERINDIA','ATLANTAELE')

def main():
    INPUT.mkdir(parents=True,exist_ok=True)
    sources={
        'corrected-current-q2.csv':'artifacts/subsector-ss01/q2-atherenerg-correction-20261104/corrected-current-q2.csv',
        'focus.txt':'artifacts/subsector-ss01/q2-atherenerg-correction-20261104/2026_Q2_FOCUS_LIST.txt',
        'all-q2-assessments.csv':'artifacts/subsector-ss01/q2-2027-addendum-20261103/q2-all-assessments.csv',
        'registry.csv':'artifacts/subsector-ss02/existing-exchange-security-inventory.csv',
        'companies.csv':'artifacts/subsector-ss02/existing-company-inventory.csv',
        'classifications.csv':'artifacts/subsector-ss04/COMPANY_SUBSECTOR_FINAL.csv',
        'peer-review.csv':'artifacts/subsector-ss03/pilot-baskets/PEER_REVIEW.csv',
    }
    ledger={}
    for name,src in sources.items():
        p=ROOT/src; target=INPUT/name
        if not target.exists():shutil.copyfile(p,target)
        if digest(p)!=digest(target):raise ValueError('Frozen evidence changed: '+src)
        ledger[name]=dict(original=src,sha256=digest(target))
    blue=INPUT/'blue-original.txt'
    if not blue.exists():shutil.copyfile(Path('/Users/mohamedfadhi/Downloads/BLUE LIST.txt'),blue)
    ledger[blue.name]=dict(original='Owner Downloads/BLUE LIST.txt',sha256=digest(blue))
    write_json(INPUT/'source-provenance.json',ledger)
    # Preserve original source tokens; the explicit task correction is an overlay.
    tokens=[s.strip() for s in blue.read_text().split(',') if s.strip()]
    assert len(set(tokens))==27
    write_rows(OUT/'BLUE_IDENTITY_OVERLAY.csv',[dict(original_token=t,corrected_token='NSE:SAMBHV' if t=='NSE:SAMBHAAV' else t,
        authority='SS-INDEX-02 explicit owner correction' if t=='NSE:SAMBHAAV' else 'Original mentor membership',
        membership_preserved=True) for t in tokens])
    master=json.loads(gzip.decompress((V1/'inputs/public-nse-instruments.json.gz').read_bytes()))
    registry={r['symbol']:r for r in read_csv(INPUT/'registry.csv') if r['exchange']=='NSE' and r['segment']=='equity'}
    old=json.loads((V1/'OUTSTANDING_ACQUISITION_MANIFEST.json').read_text())
    oldby={r['symbol']:r for r in old['requests']}
    requests=[]
    for s in PEERS:
        found=[r for r in master if r.get('trading_symbol')==s and r.get('segment')=='NSE_EQ' and r.get('instrument_type')=='EQ']
        assert len(found)==1
        m=found[0];reg=registry[s]
        assert reg['isin']==m['isin'] and reg['listing_date']<='2025-12-01'
        q=DailyRequest(m['instrument_key'],'days',1,date(2025,12,1),date(2026,10,30))
        r=dict(number=len(requests)+1,symbol=s,exchange='NSE',instrument_key=q.instrument_key,unit='days',interval=1,
            from_date=str(q.from_date),to_date=str(q.to_date),url=url_for(q),identity=m,
            listing_date=reg['listing_date'],listing_source='Retained exchange registry; later metadata snapshot, not historical universe proof',
            subsector='TYRES' if s in PEERS[:3] else 'EMS_CORE' if s in PEERS[3:6] else 'TRANSFORMERS_BROAD' if s=='POWERINDIA' else 'TRANSFORMERS_CORE',
            prior_request_unchanged=s in oldby)
        if s in oldby:
            for k in ('instrument_key','from_date','to_date','url','unit','interval'):assert r[k]==oldby[s][k]
        requests.append(r)
    # Inventory every retained acquisition ledger under the research candle area.
    evidence=[];available={};duplicates=set()
    inv=ROOT/'artifacts/earnings-reaction-v1/er03/market-data-inventory.json'
    items=json.loads(inv.read_text())['datasets']
    for item in items:
        if item['unit']!='days':continue
        evidence.append(dict(instrument_key=item['instrument_key'],source=item['source']['original_path'],sha256=item['source']['sha256'],rows=item['rows'],ledger=str(inv.relative_to(ROOT))))
    for p in (ROOT/'artifacts/earnings-reaction-v1').rglob('retrieval-execution.json'):
        data=json.loads(p.read_text())
        for r in data.get('requests',[]):
            src=r.get('retained_path')
            if not src or r.get('unit')!='days':continue
            raw=ROOT/src
            if raw.exists():evidence.append(dict(instrument_key=r['instrument_key'],source=src,sha256=digest(raw),rows=r.get('rows'),ledger=str(p.relative_to(ROOT))))
    for r in evidence:
        key=(r['instrument_key'],r['sha256'])
        if key in duplicates:continue
        duplicates.add(key)
        raw=ROOT/r['source']
        if digest(raw)!=r['sha256']:raise ValueError('Raw response hash mismatch')
        bars=json.loads(raw.read_text())['data']['candles']
        dates=sorted(b[0][:10] for b in bars)
        r.update(first_date=dates[0],last_date=dates[-1])
        available.setdefault(r['instrument_key'],set()).update(dates)
    write_json(INPUT/'retained-candle-inventory.json',evidence)
    missing=[r for r in requests if not available.get(r['instrument_key'])]
    if len(missing)!=8:raise ValueError('Existing peer data found; reconcile before proposing acquisition')
    plan=dict(task='SS-INDEX-02',status='PENDING_EXPLICIT_OWNER_APPROVAL;NOT_EXECUTED',request_count=len(missing),requests=missing,
        original_seven_manifest_sha256=digest(V1/'OUTSTANDING_ACQUISITION_MANIFEST.json'),
        metadata_path='artifacts/subsector-index-v1/inputs/public-nse-instruments.json.gz',metadata_sha256=digest(V1/'inputs/public-nse-instruments.json.gz'),
        automatic_retries=0,execution_policy='Serial at least 1 second apart; one attempt each; stop on first failure; no redirects or substitutions; reuse existing secure supplier and Upstox parser',
        admission_policy='Acquisition alone does not admit a company. Review adjustment basis, actions, same-ISIN coverage and date alignment first.',
        authorization='Prior ER03D authorization exhausted. No approval for these eight requests inferred.',
        endpoint_validation='Official V3 documentation: days/1, inclusive dates, maximum daily range ten years; requested range is eleven months.',
        retained_inventory_scope='All ER research acquisition ledgers plus ER03 historical inventory; generic quote stores are not certified daily candles',
        excluded_request='Nifty50 missing tail remains separately disclosed; no macro acquisition in this manifest')
    write_json(OUT/'OUTSTANDING_ACQUISITION_MANIFEST.json',plan)
    (OUT/'OUTSTANDING_ACQUISITION_MANIFEST.sha256').write_text(digest(OUT/'OUTSTANDING_ACQUISITION_MANIFEST.json')+'\n')
    # Freeze preservation baseline once. No existing research output is rewritten.
    baseline=OUT/'preservation-before.json'
    if not baseline.exists():
        roots=[ROOT/'artifacts'/n for n in ('subsector-index-v1','subsector-ss01','subsector-ss02','subsector-ss03','subsector-ss04','earnings-reaction-v1/er04')]
        write_json(baseline,{str(p.relative_to(ROOT)):digest(p) for root in roots for p in root.rglob('*') if p.is_file()})
    print(json.dumps(dict(request_count=len(missing),manifest_sha256=digest(OUT/'OUTSTANDING_ACQUISITION_MANIFEST.json'),blue_members=len(tokens))))

if __name__=='__main__':main()
