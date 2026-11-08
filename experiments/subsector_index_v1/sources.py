"""Frozen SS-INDEX-01 inputs and independently reviewed membership evidence.

Original SS and ER files are read only. Current Screener grouping is a cross-check
and never supplies historical membership. Missing peers stay out of this named
retained subset and in a separate exact acquisition proposal.
"""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal as D
import gzip
import json
import shutil
from experiments.earnings_reaction_v1.er03 import ROOT,BASE,BENCH,read_csv,write_csv,write_json,digest

OUT=ROOT/'artifacts/subsector-index-v1';INPUT=OUT/'inputs'
CUTOFFS={'FY2026-Q4':'2026-03-31','FY2027-Q1':'2026-06-30'}
MEMBERS={'TYRES':('CEATLTD','MRF','TOLINS'),'EMS':('SYRMA','CENTUM','PGEL'),
         'TRANSFORMERS':('TARIL','VOLTAMP','SHILCTECH')}
NAMES={'TYRES':'Tyre manufacturers — retained three-company subset',
       'EMS':'EMS — diversified contract-manufacturing exposure subset',
       'TRANSFORMERS':'Transformer manufacturers — retained three-company subset'}
MISSING_PEERS={'TYRES':('APOLLOTYRE','JKTYRE'),'EMS':('DIXON','AVALON','KAYNES'),
               'TRANSFORMERS':('POWERINDIA','ATLANTAELE')}
BUSINESS={
 'CEATLTD':('TYRE_MANUFACTURER','Tyres for multiple vehicle categories; actual manufacturer.',
    'https://www.rpggroup.com/assets/investors/pdfs/ceat-annual-report-fy241743584614.pdf','FY2024 issuer-group report; prior peer review'),
 'MRF':('TYRE_MANUFACTURER','Manufactures tyres; high rupee share price does not increase its equal index weight.',
    'https://www.mrftyres.com/downloads/Annual-Report-2024-25.pdf','FY2025 issuer report; business predates cutoffs'),
 'TOLINS':('TYRE_AND_RETREAD_MANUFACTURER','Manufactures new tyres and precured tread rubber; not a rubber-recycling business. Retread-product mix differs from CEAT/MRF.',
    'https://nsearchives.nseindia.com/corporate/FP_INE0RWQ01014_13SEP2024.pdf','September 2024 prospectus; NSE quote lists 16 September 2024 listing'),
 'SYRMA':('CORE_EMS','Electronic design and contract manufacturing, diversified end-markets.',
    'https://syrmasgs.com/new-milestone-syrma-sgs-sets-up-the-largest-electronics-manufacturing-facility-in-pune/','Issuer announcement dated 2024-11-19; SS03B retained source'),
 'CENTUM':('DIVERSIFIED_ESDM_EMS_EXPOSURE','Issuer FY2025 report describes operational PCB assembly, box-build and system integration EMS; ER&D/BTS and defence exposure distinguish it from a pure broad-market EMS peer.',
    'https://www.centumelectronics.com/wp-content/uploads/2025/07/ANNUAL-REPORT-FY-2024-25.pdf','FY2025 report; NSE quote identifies original submission 2025-07-10; independent historical archive of first publication not retained'),
 'PGEL':('APPLIANCE_ODM_OEM_EMS_EXPOSURE','Issuer FY2025 report documents ODM/OEM for customer brands and plastic moulding. Included in extended manufacturing exposure only; appliance/resin seasonality differs from electronics assembly.',
    'https://pgel.in/assets/images/annula_report/PGEL_Annual_Report_2024-25.pdf','Issuer cover dated 2025-08-30; pages 17/25 describe business'),
 'TARIL':('POWER_TRANSFORMER_MANUFACTURER','Actual power, furnace and rectifier transformer manufacturer; ancillary-only suppliers excluded.',
    'https://www.transformerindia.com/wp-content/uploads/2024/05/30thAGMReport01052024Final.pdf','SS03B retained dated issuer report'),
 'VOLTAMP':('POWER_INDUSTRIAL_TRANSFORMER_MANUFACTURER','Manufactures power and industrial transformers rather than merely trading or supplying bushings.',
    'https://www.voltamptransformers.com/userfile/pdf/1751635237_ANNUAL_REPORT_2024_25.pdf','FY2025 issuer report describes transformer manufacture'),
 'SHILCTECH':('POWER_DISTRIBUTION_TRANSFORMER_MANUFACTURER','Power/distribution and electronics transformers. NSE listed 24 November 2025, before both cutoffs; no earlier BSE volumes are spliced.',
    'https://shilchar.com/upload/power_investor_relations/annual-report-2024-2025.pdf','FY2025 issuer report; NSE listing date corroborated in May-2026 issuer results note 5')}
CA_SOURCES={
 'CEATLTD':'https://www.etmoney.com/stocks/ceat-ltd/corporate-actions/899',
 'MRF':'https://trendlyne.com/equity/corporate-actions/MRF/883/mrf-ltd/',
 'TOLINS':'https://trendlyne.com/equity/corporate-actions/TOLINS/2610661/tolins-tyres-ltd/',
 'SYRMA':'https://trendlyne.com/equity/corporate-actions/SYRMA/995074/syrma-sgs-technology-ltd/',
 'CENTUM':'https://trendlyne.com/equity/corporate-actions/CENTUM/251/centum-electronics-ltd/',
 'PGEL':'https://trendlyne.com/equity/corporate-actions/PGEL/1031/pg-electroplast-ltd/',
 'TARIL':'https://www.nseindia.com/companies-listing/corporate-filings-actions?symbol=TARIL',
 'VOLTAMP':'https://trendlyne.com/equity/corporate-actions/VOLTAMP/1499/voltamp-transformers-ltd/',
 'SHILCTECH':'https://trendlyne.com/equity/corporate-actions/SHILCTECH/3495/shilchar-technologies-ltd/'}
# Known price-unit events precede the deliberately bounded common history.
PRIOR_ACTIONS=[dict(symbol='PGEL',ex_date='2024-07-10',kind='SPLIT',new_shares_per_old_share='10',source_url=BUSINESS['PGEL'][2]),
 dict(symbol='SHILCTECH',ex_date='2025-06-06',kind='BONUS',new_shares_per_old_share='1.5',source_url='https://shilchar.com/upload/power_investor_relations/record-date.pdf'),
 dict(symbol='TARIL',ex_date='2025-02-14',kind='BONUS',new_shares_per_old_share='2',source_url=CA_SOURCES['TARIL'])]
DIVIDENDS=[('CEATLTD','2026-07-31','35'),('MRF','2026-02-13','3'),('MRF','2026-07-17','229'),
 ('SYRMA','2026-08-18','1.5'),('CENTUM','2026-07-31','5'),('PGEL','2026-09-18','0.25'),
 ('TARIL','2026-09-18','0.25'),('VOLTAMP','2026-07-24','100'),('SHILCTECH','2026-07-31','12.5')]

def write_rows(path,rows):
    if not rows:return
    fields=list(dict.fromkeys(k for r in rows for k in r))
    write_csv(path,[{k:r.get(k) for k in fields} for r in rows])

def prepare():
    from experiments.earnings_reaction_v1.er04 import load_data,events
    INPUT.mkdir(parents=True,exist_ok=True)
    data,providers=load_data()
    inventory=json.loads((BASE/'er03/market-data-inventory.json').read_text())['datasets']
    paths={}
    for item in inventory:
        if item['unit']=='days':paths[item['source']['original_path']]=item['instrument_key']
    completion=json.loads((BASE/'er03/data-completion/retrieval-execution.json').read_text())
    paths.update({r['retained_path']:r['instrument_key'] for r in completion['requests']})
    original={}
    for path,key in paths.items():
        for r in json.loads((ROOT/path).read_text())['data']['candles']:
            original[key,r[0]]=path
    pilots=read_csv(BASE/'er03/pilot-companies.csv');symbols={r['instrument_key']:r['symbol'] for r in pilots}
    symbols[BENCH]='NIFTYMIDSML400'
    rows=[]
    for key,bars in data.items():
        if key not in symbols:continue
        for c in bars:
            src=original[key,c.start_ist.isoformat()]
            rows.append(dict(symbol=symbols[key],instrument_key=key,date=str(c.start_ist.date()),
                original_timestamp=c.start_ist.isoformat(),raw_open=c.open,raw_high=c.high,raw_low=c.low,raw_close=c.close,raw_volume=c.volume,
                original_source_file=src,original_source_sha256=digest(ROOT/src)))
    write_rows(INPUT/'RETAINED_DAILY_CANDLES.csv',rows)
    write_json(INPUT/'EVENTS.json',events())
    originals=[BASE/'er03/pilot-companies.csv',BASE/'er04/early-checkpoint-features.csv',BASE/'er04/forward-returns-drawdowns.csv',
       ROOT/'artifacts/subsector-ss03/pilot-baskets/PEER_REVIEW.csv',ROOT/'artifacts/subsector-ss03/pilot-baskets/CANDIDATE_BASKETS.csv',
       ROOT/'artifacts/subsector-ss04/COMPANY_SUBSECTOR_FINAL.csv',ROOT/'artifacts/subsector-ss04/MAJOR_STOCK_UNIVERSE.csv',
       BASE/'ta02b/public-nse-instruments.json.gz']
    for p in originals:shutil.copyfile(p,INPUT/p.name)
    write_json(INPUT/'source-provenance.json',dict(original_files={str(p.relative_to(ROOT)):digest(p) for p in originals},
       original_provider_files=providers,snapshot_sha256=digest(INPUT/'RETAINED_DAILY_CANDLES.csv'),
       capture_basis='Existing ER03/ER03D actual raw responses; no new authenticated requests'))
    freeze_evidence()

def freeze_evidence():
    write_json(INPUT/'business-review.json',dict(research_date='2026-11-04',membership_status='RETROSPECTIVE_DESCRIPTIVE',
        independent_members={s:dict(role=x[0],business=x[1],source_url=x[2],source_date_basis=x[3]) for s,x in BUSINESS.items()},
        ems_boundary='Extended actual manufacturing exposure, not a pure contract-EMS universe. Only SYRMA from the four SS03B core candidates has retained prices; pure-core index and core leave-one-out remain insufficient.',
        retrospective_limit='The selection and major universe were researched after the cutoffs; dated business reports do not make this selection point-in-time verified.',
        excluded={'GRPLTD':'Rubber/nylon recycling, not tyre manufacture','EBGNG':'Device refurbishment, not demonstrated contract manufacture','E2E':'Cloud services','RPTECH':'ICT distribution','STOVEKRAFT':'Branded kitchen products; contract EMS not demonstrated','MARINE':'Marine electrical integration; direct contract EMS not demonstrated','YASHHV':'Transformer bushings, not complete transformer manufacture'}))
    facts=[]
    for s,url in CA_SOURCES.items():
        facts.append(dict(symbol=s,source_url=url,review_date='2026-11-04',window_start='2025-12-01',window_end='2026-10-30',
            finding='No in-window split/bonus/rights identified in reviewed listed-security action histories',
            authority='PRIMARY_NSE_TABLE' if s=='TARIL' else 'SECONDARY_PUBLIC_ACTION_HISTORY;CORROBORATE_WITH_ISSUER_WHERE_AVAILABLE',
            within_window_share_unit_factor='1',complete_primary_feed_verified=False,
            limitation='Absence in visible records is not an exhaustive issuer/exchange certification. Missing public NSE action feed retained as acquisition evidence.',
            specific_note='Pre-IPO October-2023 split/bonus exists in issuer prospectus; aggregator no-history statement is not adopted outside listed study window.' if s=='TOLINS' else 'Known June-2025 bonus predates NSE listing and study base.' if s=='SHILCTECH' else 'July-2024 split predates study base.' if s=='PGEL' else ''))
    write_json(INPUT/'corporate-action-review.json',dict(action_review=facts,prior_known_share_unit_events=PRIOR_ACTIONS,
        in_window_share_unit_events=[],ordinary_cash_dividends=[dict(symbol=s,ex_date=d,amount_inr=a,kind='ORDINARY_CASH_DIVIDEND',source_url=CA_SOURCES[s]) for s,d,a in DIVIDENDS],
        price_index_policy='Ordinary cash dividends remain ex-dividend price moves; no reinvestment; not total return. Split/bonus forward normalization supported only with confirmed provider basis; unresolved actions block series.',
        rejected_claims=[dict(claim='MRF 4:1 bonus March 2026 in generic broker card',decision='REJECTED_UNVERIFIED_CROSS_COMPANY_OR_TEMPLATE_CONTENT',basis='No matching MRF issuer/exchange action; disagrees with dedicated MRF action histories. Never infer action from an isolated search card.')]))
    write_json(INPUT/'screener-current-crosscheck.json',dict(observed_date='2026-11-04',purpose='CURRENT_GROUPING_CROSSCHECK_ONLY;NOT_HISTORICAL_MEMBERSHIP',
        sources=[dict(url='https://www.screener.in/market/',fact='Current industry overview; categories are broader than the three analytical groups.'),
          dict(url='https://www.screener.in/market/IN02/IN0201/IN020102/IN020102006/',fact='Current tyre/rubber group includes CEAT, MRF, Tolins, Apollo, JK and ancillary rubber businesses; tyre manufacture requires independent screening.'),
          dict(url='https://www.screener.in/market/IN07/IN0702/IN070203/IN070203001/',fact='Current heavy-electrical group includes transformer makers and many other products; not a transformer-only index.'),
          dict(url='https://www.screener.in/company/SYRMA/consolidated/',fact='Current peer path is Industrial Products; actual issuer business supports EMS activity.'),
          dict(url='https://www.screener.in/company/CENTUM/consolidated/',fact='Current peer path is Aerospace and Defense; prevents calling extended subset pure EMS.'),
          dict(url='https://www.screener.in/company/PGEL/consolidated/',fact='Current peer path is Consumer Electronics; issuer report supports ODM/OEM activity.')],
        price_values_from_screener_used=False,historical_baskets_backdated=False))

def acquisition_manifest():
    from experiments.earnings_reaction_v1.retrieve_er03 import DailyRequest
    from experiments.earnings_reaction_v1.upstox_candles import url_for
    master=json.loads(gzip.decompress((INPUT/'public-nse-instruments.json.gz').read_bytes()))
    requests=[]
    for group,syms in MISSING_PEERS.items():
        for symbol in syms:
            found=[r for r in master if r.get('trading_symbol')==symbol and r.get('segment')=='NSE_EQ' and r.get('instrument_type')=='EQ']
            if len(found)!=1:raise ValueError('Unique peer instrument identity missing: '+symbol)
            from datetime import date
            q=DailyRequest(found[0]['instrument_key'],'days',1,date(2025,12,1),date(2026,10,30))
            requests.append(dict(number=len(requests)+1,subsector=group,symbol=symbol,instrument_key=q.instrument_key,unit='days',interval=1,
                from_date=str(q.from_date),to_date=str(q.to_date),url=url_for(q),identity=found[0],
                eligibility='PRICE_ACQUISITION_ONLY;DOES_NOT_AUTOMATICALLY_CERTIFY_BUSINESS_OR_HISTORICAL_MEMBERSHIP'))
    path=OUT/'OUTSTANDING_ACQUISITION_MANIFEST.json'
    write_json(path,dict(status='NOT_EXECUTED;EXACT_NEW_SCOPE_REQUIRES_OWNER_AUTHORIZATION',scope='SS-INDEX-01 three-subsector pilot; seven previously researched peers only',
        request_count=len(requests),requests=requests,metadata_file='inputs/public-nse-instruments.json.gz',metadata_sha256=digest(INPUT/'public-nse-instruments.json.gz'),
        automatic_retries=0,execution_policy='Serial >=1s apart; validate instrument identity/hash, date bounds, raw responses and corporate-action basis; stop on first failure; no substitutions or automatic basket enlargement',
        provider_adjustment_requirement='Confirm split/bonus/rights/demerger basis and historical issuer listing before index admission. Current instrument metadata is identity evidence only.',
        existing_authorization='Earlier ER03D exact eleven-request approval exhausted. This proposal is separate and unexecuted.'))
    (OUT/'OUTSTANDING_ACQUISITION_MANIFEST.sha256').write_text(digest(path)+'\n')

def membership_evidence(candles):
    final={r['company_id']:r for r in read_csv(INPUT/'COMPANY_SUBSECTOR_FINAL.csv')}
    pilots=read_csv(INPUT/'pilot-companies.csv');reviews=read_csv(INPUT/'PEER_REVIEW.csv')
    by_symbol={p['symbol']:p for p in pilots};rows=[]
    for quarter,cutoff in CUTOFFS.items():
        for group,syms in MEMBERS.items():
            for s in syms:
                p=by_symbol[s];prior=final[p['company_id']];seen=[r for r in candles if r['symbol']==s and r['date']<=cutoff]
                if prior['major_scope']!='1' and s!='TOLINS':raise ValueError('Unexpected non-major constituent requires explicit scope review')
                if not any(r['symbol']==s and r['date']>cutoff for r in candles):raise ValueError('No post-cutoff same-ISIN observations')
                dates=sorted(r['date'] for r in seen)
                if not dates:raise ValueError('No pre-cutoff actual listing observation')
                role,business,url,basis=BUSINESS[s]
                rows.append(dict(quarter=quarter,cutoff=cutoff,subsector=group,symbol=s,company_id=p['company_id'],company_name=p['company_name'],instrument_key=p['instrument_key'],
                    independent_membership='INCLUDED_FIXED_RETAINED_EXPOSURE_SUBSET',business_role=role,business_evidence=business,source_url=url,source_date_basis=basis,
                    listing_eligibility='ACTUAL_SAME_ISIN_NSE_DAILY_CANDLES_PRE_AND_POST_CUTOFF',first_retained_trade=dates[0],last_observed_on_or_before_cutoff=dates[-1],observed_pre_cutoff_sessions=len(dates),
                    listing_date='2025-11-24' if s=='SHILCTECH' else '2024-09-16' if s=='TOLINS' else '2007-10-05' if s=='CENTUM' else '',
                    listing_date_source='Issuer May-2026 results note5' if s=='SHILCTECH' else 'NSE public security quote' if s in ('CENTUM','TOLINS') else 'Exact initial listing date not needed; earlier actual NSE records establish listed-before-cutoff',
                    original_tutor_evidence_json=prior['original_tutor_evidence_json'],original_ss04_status=prior['status'],original_ss04_primary_code=prior['primary_code'],
                    original_ss04_known_at=prior['known_at'],historical_universe_filter='EXISTING_UNIVERSE_AND_ER04_PILOTS_REUSED;CURRENT_MAJOR_FLAG_PRESERVED;NO_HISTORICAL_CAP_OR_LIQUIDITY_FILTER',
                    current_major_scope=prior['major_scope'],major_scope_exception='EXISTING_ER04_TOLINS_PILOT;OUTSIDE_CURRENT_MAJOR_SCOPE;NOT_RECLASSIFIED' if s=='TOLINS' else '',
                    membership_status='RETROSPECTIVE_DESCRIPTIVE',known_at='2026-11-04',point_in_time_verified=False,
                    index_weight='1/3 EACH_DAILY_RETURN',selection_basis='Existing retained authorized candle coverage plus independently reviewed manufacture; no realized-return ranking',
                    coverage_scope='THREE_STOCK_SUBSET;NOT_COMPLETE_SECTOR',business_mix_warning='Extended diversified exposure; core-only peers insufficient' if group=='EMS' else 'Small heterogeneous product mix; retained subset'))
    write_rows(OUT/'HISTORICAL_CONSTITUENTS.csv',rows)
    preserved=[]
    for r in reviews:
        if r['label'] not in ('TYRE','EMS','TRANSFORMERS'):continue
        e=json.loads(r['evidence_json'])
        preserved.append(dict(r,independent_task_note='Prior review retained unchanged; inclusion in current subset must be read in HISTORICAL_CONSTITUENTS.csv',
                              original_tutor_provenance=json.dumps(e.get('tutor_source_rows',[]))))
    write_rows(OUT/'PRIOR_PEER_REVIEWS_AND_TUTOR_PROVENANCE.csv',preserved)
    return rows
