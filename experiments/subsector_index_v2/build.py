"""Offline SS-INDEX-02. Reuses V1 math, event study and ER03C renderer."""
from decimal import Decimal as D
from collections import defaultdict
import json, shutil
from experiments.earnings_reaction_v1.er03 import ROOT, read_csv, digest, write_json
from experiments.subsector_index_v1 import core, build as v1, study
from experiments.subsector_index_v1.sources import CUTOFFS, BUSINESS, write_rows
from .prepare import OUT, INPUT, V1, PEERS

SETS={
 'TYRES':('CEATLTD','MRF','APOLLOTYRE','JKTYRE','BALKRISIND'),
 'TYRES_WITH_RETREAD':('CEATLTD','MRF','APOLLOTYRE','JKTYRE','BALKRISIND','TOLINS'),
 'EMS':('SYRMA','DIXON','AVALON','KAYNES'),
 'EMS_BROAD':('SYRMA','DIXON','AVALON','KAYNES','CENTUM','PGEL'),
 'TRANSFORMERS':('TARIL','VOLTAMP','SHILCTECH','ATLANTAELE'),
 'TRANSFORMERS_BROAD':('TARIL','VOLTAMP','SHILCTECH','ATLANTAELE','POWERINDIA'),
}
PRIMARY=('TYRES','EMS','TRANSFORMERS')
NAMES={g:g+' V2 '+('core manufacturer subset' if g in PRIMARY else 'separate broader exposure sensitivity') for g in SETS}
EXTRA_BUSINESS={
 'APOLLOTYRE':('TYRE_MANUFACTURER','Vehicle tyre manufacture; geography and vehicle mix differ from peers.','https://nsearchives.nseindia.com/annual_reports/AR_26787_APOLLOTYRE_2024_2025_A_08072025151529.pdf','2025-07-08 filing'),
 'JKTYRE':('TYRE_MANUFACTURER','Vehicle tyre manufacture; Cavendish subsidiary amalgamation changes business perimeter, not an assumed share-unit split.','https://www.jktyre.com/investor/annual-reports-notices','FY2025 report; publication timestamp unresolved'),
 'BALKRISIND':('OFF_HIGHWAY_TYRE_MANUFACTURER','Agricultural, construction and mining off-highway tyres; carbon-black exposure; different demand cycle from road tyres.','https://www.bkt-tires.com/downloads/16163/12443/Final_Integrated_Annual_Report_2024-25.pdf','FY2025 report; AGM 2025-07-26; first-publication timestamp unresolved'),
 'DIXON':('CORE_EMS_CONSUMER_ELECTRONICS','Contract electronic manufacturing across product families; consumer mix explicitly retained.','https://dixoninfo.com/?Itemid=492&id=71&option=com_content&view=article','SS03B current overview; historical source-publication timestamp unresolved'),
 'AVALON':('CORE_EMS_BOX_BUILD','Integrated electronic contract manufacturing and box-build; global operations.','https://www.avalontec.com/investors/','FY2025 annual report; NSE quote shows filing 2025-09-03 19:25:01; capture is retrospective'),
 'KAYNES':('CORE_EMS_DESIGN_LED','Design-led integrated electronics manufacturing; end-markets differ from Dixon.','https://www.kaynestechnology.co.in/doc/Stock-Exchange-Disclosures/Annual%20Report%20FY2023-24.pdf','2024-08-17 notice in SS03B'),
 'POWERINDIA':('DIVERSIFIED_GRID_EQUIPMENT','Power transformers plus grid equipment and systems; broad comparison only.','https://www.hitachienergy.com/us/en/news-and-events/press-releases/2025/06/hitachi-energy-india-to-deliver-765-kv-transformers-to-power-grid-corporation-of-india-limited-to-advance-country-s-grid-expansion','2025-06-26 issuer announcement'),
 'ATLANTAELE':('POWER_TRANSFORMER_MANUFACTURER','Power transformer manufacture; short post-IPO trading history; no pre-listing request.','https://nsearchives.nseindia.com/corporate/ATLANTAIPO123_17102025102830_Outcome_of_Board_Meeting17102025.pdf','2025-10-17 issuer results; registry listing 2025-09-29'),
}

def load():
    rows=v1.read_prices()
    ledger=json.loads((OUT/'retrieval-execution.json').read_text())
    if not ledger['complete'] or ledger['scope_sha256']!='1006e76d75c6292678d24b62903e26a247222d7f01db13ead76ad3eb07a37262':raise ValueError('Approved acquisition incomplete')
    plan=json.loads((OUT/'OUTSTANDING_ACQUISITION_MANIFEST.json').read_text())
    if digest(OUT/'OUTSTANDING_ACQUISITION_MANIFEST.json')!=ledger['scope_sha256'] or len(ledger['requests'])!=8:raise ValueError('Acquisition scope mismatch')
    for r in ledger['requests']:
        expected=plan['requests'][r['number']-1]
        if any(r[k]!=expected[k] for k in ('symbol','instrument_key','from_date','to_date','url','unit','interval')):raise ValueError('Response identity differs from plan')
        p=ROOT/r['retained_path']
        if digest(p)!=r['response_sha256']:raise ValueError('Raw peer hash mismatch')
        for b in json.loads(p.read_text(),parse_float=D)['data']['candles']:
            if not r['from_date']<=b[0][:10]<=r['to_date']:raise ValueError('Out-of-bounds price date')
            rows.append(dict(symbol=r['symbol'],instrument_key=r['instrument_key'],date=b[0][:10],original_timestamp=b[0],
                raw_open=D(b[1]),raw_high=D(b[2]),raw_low=D(b[3]),raw_close=D(b[4]),raw_volume=D(b[5]),
                original_source_file=r['retained_path'],original_source_sha256=r['response_sha256']))
    # Nifty50 is reused only on exact retained sessions. Its missing tail stays missing.
    inv=json.loads((INPUT/'retained-candle-inventory.json').read_text())
    n=next(r for r in inv if r['instrument_key']=='NSE_INDEX|Nifty 50')
    p=ROOT/n['source']
    if digest(p)!=n['sha256']:raise ValueError('Nifty raw hash mismatch')
    nifty={b[0][:10]:D(b[4]) for b in json.loads(p.read_text(),parse_float=D)['data']['candles']}
    tail=OUT/'nifty-tail-execution.json'
    if tail.exists():
        receipt=json.loads(tail.read_text())
        if receipt.get('complete'):
            if receipt['manifest_sha256']!='dc07c957443e2a6f526e3dbfc0fbf185d2fcc9094d351be9540b9cc64fb8788e':raise ValueError('Nifty tail scope mismatch')
            source=ROOT/receipt['retained_path']
            if digest(source)!=receipt['response_sha256']:raise ValueError('Nifty tail hash mismatch')
            for b in json.loads(source.read_text(),parse_float=D)['data']['candles']:
                day=b[0][:10]
                if not '2026-08-17'<=day<='2026-10-30' or day in nifty:raise ValueError('Overlapping or out-of-range Nifty tail')
                nifty[day]=D(b[4])
    return rows,nifty

def calculate(candles, calendar=None):
    if calendar is None:calendar=sorted(r['date'] for r in candles if r['symbol']=='NIFTYMIDSML400' and '2025-12-01'<=r['date']<='2026-10-30')
    if len(calendar)!=len(set(calendar)):raise ValueError('Duplicate calendar')
    benchmark={r['date']:r['raw_close'] for r in candles if r['symbol']=='NIFTYMIDSML400' and r['date'] in calendar}
    actions=json.loads((V1/'inputs/corporate-action-review.json').read_text())
    peer_review=json.loads((INPUT/'peer-action-review.json').read_text())
    if {r['symbol'] for r in peer_review['members']}!=set(PEERS):raise ValueError('Missing peer action review')
    events=actions['in_window_share_unit_events']+actions['ordinary_cash_dividends']+peer_review.get('confirmed_in_window_events',[])
    stocks={}
    for s in sorted(set(sum((list(x) for x in SETS.values()),[]))):
        raw=[r for r in candles if r['symbol']==s and r['date'] in calendar]
        normal=core.normalise_prices(raw,[a for a in events if a['symbol']==s and a['ex_date'] in calendar])
        stocks[s]=core.stock_rs(core.constituent_features(normal,calendar),calendar,benchmark)
        if s in PEERS:
            for r in stocks[s]:
                if r.get('adjusted_close') is not None:r['adjustment_status']='PROVISIONAL_PROVIDER_CLOSE;FACTOR_1;INCOMPLETE_PRIMARY_ACTION_AUDIT'
    indices={};loo={}
    for q,cutoff in CUTOFFS.items():
        for g,members in SETS.items():
            rows=core.equal_weight_index(stocks,calendar,benchmark,members=members,min_members=3)
            for r in rows:r.update(subsector=g,quarter=q,formation_cutoff=cutoff,version='V2',membership_status='RETROSPECTIVE_DESCRIPTIVE_PROVISIONAL',
                period_role='PRE_FORMATION_BACKCAST_WARMUP' if r['date']<=cutoff else 'POST_FORMATION_FIXED_MEMBERSHIP',
                base_date=calendar[0],base_value=1000,rebalancing='DAILY_1/N_RETURN_WEIGHTS;FIXED_MEMBERSHIP',
                adjustment_certification='PROVISIONAL_FACTOR_1;NO_CONFIRMED_IN_WINDOW_SHARE_UNIT_EVENT;PRIMARY_FULL_FEED_AND_PROVIDER_BASIS_UNATTESTED')
            indices[q,g]=rows
            # Core comparisons for broader stocks (e.g. PGEL) are independently useful.
            targets=members if g not in PRIMARY else SETS['EMS_BROAD'] if g=='EMS' else SETS['TYRES_WITH_RETREAD'] if g=='TYRES' else SETS['TRANSFORMERS_BROAD']
            for target in targets:
                peers=tuple(s for s in members if s!=target)
                rs=core.equal_weight_index(stocks,calendar,benchmark,members=peers,min_members=2)
                for r in rs:r.update(subsector=g,quarter=q,formation_cutoff=cutoff,excluded_symbol=target,target_is_member=target in members,
                    membership_status='RETROSPECTIVE_DESCRIPTIVE_PROVISIONAL')
                loo[q,g,target]=rs
    return stocks,indices,loo,calendar,benchmark

def reconciliation(candles,calendar):
    registry=read_csv(INPUT/'registry.csv');companies={r['company_id']:r['current_legal_name'] for r in read_csv(INPUT/'companies.csv')}
    pilots={r['symbol']:r for r in read_csv(V1/'inputs/pilot-companies.csv')}
    rec=[];elig=[];coverage=[]
    for s in sorted(set(sum((list(x) for x in SETS.values()),[]))):
        matches=[r for r in registry if r['symbol']==s and r['exchange']=='NSE' and r['segment']=='equity']
        if len(matches)!=1:raise ValueError('Ambiguous registry '+s)
        reg=matches[0];cid=reg['existing_registry_company_id'];key='NSE_EQ|'+reg['isin']
        raw=sorted((r for r in candles if r['symbol']==s),key=lambda r:r['date']);seen={r['date'] for r in raw}
        if any(r['instrument_key']!=key for r in raw):raise ValueError('ISIN continuity mismatch')
        role,note,url,basis=EXTRA_BUSINESS.get(s,BUSINESS.get(s))
        listing='2025-11-24' if s=='SHILCTECH' else reg['listing_date']
        for q,cutoff in CUTOFFS.items():
            before=[d for d in seen if d<=cutoff];after=[d for d in seen if d>cutoff]
            if not listing or listing>cutoff or not before or not after:raise ValueError('Historical eligibility unresolved '+s)
            rec.append(dict(symbol=s,company_id=cid,company_name=companies[cid],instrument_key=key,quarter=q,formation_cutoff=cutoff,
                business_role=role,business_exposure=note,series_membership=';'.join(g for g,m in SETS.items() if s in m),
                core_group=next((g for g in PRIMARY if s in SETS[g]),''),
                inclusion_reason='Reviewed manufacture; pre-cutoff listing and synchronized same-ISIN prices. Fixed retrospective selection; not selected by returns.',
                source_url=url,source_publication_basis=basis,researched_at='2026-11-04',point_in_time_verified=False,
                original_tutor_unchanged=True,membership_status='RETROSPECTIVE_DESCRIPTIVE_PROVISIONAL'))
            elig.append(dict(symbol=s,company_id=cid,quarter=q,formation_cutoff=cutoff,listing_date=listing,instrument_key=key,
                listing_source='V1 dated issuer review' if s=='SHILCTECH' else 'SS02 retained exchange registry',
                registry_known_at=reg['version_valid_from'],first_observed=raw[0]['date'],last_observed_before_cutoff=max(before),
                same_isin_pre_and_post_cutoff=True,instrument_continuity='Same ISIN in all retained candles; no BSE splicing; full issuer-chain audit incomplete',
                source_publication_basis=basis,adjustment_policy='PRICE_INDEX;FACTOR_1_PROVISIONAL;CASH_DIVIDENDS_NOT_REINVESTED',
                corporate_action_source='V1 inputs/corporate-action-review.json' if s not in PEERS else 'inputs/peer-action-review.json',
                corporate_action_certified=False,suspension_status='NO_MISSING_OBSERVED_GRID_SESSIONS' if set(calendar)<=seen else 'MISSING_SESSIONS_REQUIRE_REVIEW',
                suspension_limit='Observed grid completeness does not independently certify exchange suspensions or omitted exchange sessions',membership_status='RETROSPECTIVE_DESCRIPTIVE_PROVISIONAL'))
        coverage.append(dict(symbol=s,instrument_key=key,first_date=raw[0]['date'],last_date=raw[-1]['date'],
            observed_common_sessions=len(set(calendar)&seen),expected_sessions=len(calendar),missing_sessions=';'.join(sorted(set(calendar)-seen)),
            source_files=';'.join(sorted({r['original_source_file'] for r in raw})),
            source_sha256=';'.join(sorted({r['original_source_sha256'] for r in raw})),status='AVAILABLE_PROVISIONAL_ACTION_BASIS'))
    write_rows(OUT/'CONSTITUENT_RECONCILIATION.csv',rec);write_rows(OUT/'HISTORICAL_ELIGIBILITY.csv',elig);write_rows(OUT/'DATA_COVERAGE.csv',coverage)

def measures(stocks,indices,loo,calendar,benchmark,nifty):
    strengths=[];breadths=[];comparisons=[]
    for (q,g),rows in indices.items():
        for i,r in enumerate(rows):
            parts=[stocks[s][i] for s in SETS[g]];rets=[p['return_1s_pct'] for p in parts]
            absolute=sum(abs(v) for v in rets if v is not None)
            largest=max((abs(v) for v in rets if v is not None),default=D(0))
            row=dict(r,nifty50_close=nifty.get(r['date']),above_ema10=r['index_close']>r['ema10'] if r['index_close'] else None,
                above_ema20=r['index_close']>r['ema20'] if r['index_close'] else None,
                outperforming_midsml400=r['rs55']>0 if r['rs55'] is not None else None,
                rs55_direction='RISING' if r['rs55_change_5s'] is not None and r['rs55_change_5s']>0 else 'FALLING' if r['rs55_change_5s'] is not None and r['rs55_change_5s']<0 else 'WARMUP_OR_FLAT',
                activity_expanding=r['median_constituent_traded_value_ratio20']>1 if r['median_constituent_traded_value_ratio20'] is not None else None,
                largest_absolute_return_contribution_pct=100*largest/absolute if absolute else None,
                concentration_basis='MAX_ABS_DAILY_MEMBER_RETURN / SUM_ABS_MEMBER_RETURNS;NOT_INSTITUTIONAL_FLOWS')
            row['pct_declining']=100*D(sum(v<0 for v in rets))/len(rets) if all(v is not None for v in rets) else None
            row['pct_unchanged']=100*D(sum(v==0 for v in rets))/len(rets) if all(v is not None for v in rets) else None
            for n in core.LOOKBACKS:
                dates=calendar[i-n:i+1] if i>=n else []
                nr=100*(nifty[r['date']]/nifty[dates[0]]-1) if len(dates)==n+1 and all(d in nifty for d in dates) else None
                row[f'nifty50_return_{n}s_pct']=nr
                row[f'index_minus_nifty50_{n}s_pp']=r[f'return_{n}s_pct']-nr if nr is not None and r[f'return_{n}s_pct'] is not None else None
            peers=[indices[q,k][i] for k in PRIMARY]
            row['primary_rs55_rank']=1+sum(p['rs55']>r['rs55'] for p in peers) if g in PRIMARY and r['rs55'] is not None else None
            row['primary_momentum20_rank']=1+sum(p['return_20s_pct']>r['return_20s_pct'] for p in peers) if g in PRIMARY and r['return_20s_pct'] is not None else None
            strengths.append(row)
            breadths.append({k:v for k,v in row.items() if k in ('quarter','subsector','date','members','expected_constituents','observed_constituents','missing_data_flag','activity_expanding','largest_absolute_return_contribution_pct','concentration_basis','median_constituent_rvol20','median_constituent_traded_value_ratio20','traded_value_proxy_crore','membership_status') or k.startswith('pct_')})
    for (q,g,s),rows in loo.items():
        for i,r in enumerate(rows):
            stock=stocks[s][i];sector=indices[q,g][i];day=r['date']
            out=dict(quarter=q,subsector=g,symbol=s,date=day,formation_cutoff=CUTOFFS[q],stock_close=stock['adjusted_close'],
                stock_rs55=stock['rs55'],subsector_rs55=sector['rs55'],peer_rs55=r['rs55'],peer_index_close=r['index_close'],
                peer_members=r['members'],peer_count=r['expected_constituents'],target_is_member=s in SETS[g],target_in_peer_set=s in r['members'].split(';'),
                benchmark_close=benchmark[day],nifty50_close=nifty.get(day),status='AVAILABLE_SMALL_RETROSPECTIVE_PEER_SET',
                peer_pct_above_ema20=r['pct_above_ema20'],peer_pct_advancing=r['pct_rising'],peer_pct_rvol_gt1=r['pct_rvol_gt1'],
                peer_value_proxy_crore=r['traded_value_proxy_crore'],membership_status='RETROSPECTIVE_DESCRIPTIVE_PROVISIONAL')
            for n in core.LOOKBACKS:
                sr=100*(stock['adjusted_close']/stocks[s][i-n]['adjusted_close']-1) if i>=n else None
                pr=r[f'return_{n}s_pct'];br=100*(benchmark[day]/benchmark[calendar[i-n]]-1) if i>=n else None
                dates=calendar[i-n:i+1] if i>=n else []
                nr=100*(nifty[day]/nifty[dates[0]]-1) if len(dates)==n+1 and all(d in nifty for d in dates) else None
                out.update({f'stock_return_{n}s_pct':sr,f'peer_return_{n}s_pct':pr,f'midsml400_return_{n}s_pct':br,f'nifty50_return_{n}s_pct':nr,
                    f'stock_minus_peer_{n}s_pp':sr-pr if sr is not None and pr is not None else None})
            excess=out['stock_minus_peer_20s_pp']
            out['relative_leadership_20s']='LEADING_PEERS' if excess is not None and excess>0 else 'LAGGING_PEERS' if excess is not None and excess<0 else 'WARMUP_OR_TIED'
            comparisons.append(out)
    write_rows(OUT/'SUBSECTOR_STRENGTH.csv',strengths);write_rows(OUT/'BREADTH_AND_ACTIVITY.csv',breadths);write_rows(OUT/'LEAVE_ONE_OUT.csv',comparisons)
    return strengths

def reassess(stocks,indices,loo,calendar,benchmark):
    events=json.loads((V1/'inputs/EVENTS.json').read_text());features=[];rows=[]
    old={(r['event_id'],int(r['checkpoint'])):r for r in read_csv(V1/'ER04_SUBSECTOR_CHECKPOINTS.csv')}
    for e in events:
        g=study.group_for(e['symbol'])
        # Existing event study is unchanged; the primary V2 core set supplies new context.
        compatible={(q,s):rs for (q,group,s),rs in loo.items() if group==g}
        for cp in study.CP:
            f=study.snapshot(e,cp,stocks,indices,compatible,calendar);features.append(f)
            o=old[e['event_id'],cp]
            if f['status']=='AVAILABLE_RETROSPECTIVE':
                for field in ('stock_close','stock_early_return_pct','stock_rs55'):
                    if o.get(field) and f.get(field)!=D(o[field]):raise ValueError('Original stock checkpoint changed: '+field)
            sensitivity={}
            if f['status']=='AVAILABLE_RETROSPECTIVE':
                broad={'TYRES':'TYRES_WITH_RETREAD','EMS':'EMS_BROAD','TRANSFORMERS':'TRANSFORMERS_BROAD'}[g]
                b=loo[e['quarter'],broad,e['symbol']][calendar.index(f['checkpoint_date'])]
                sensitivity=dict(broad_peer_members=b['members'],broad_peer_rs55=b['rs55'],broad_peer_rs55_change5=b['rs55_change_5s'],
                    broad_peer_breadth_above20=b['pct_above_ema20'],broad_peer_return20_pct=b['return_20s_pct'])
            rows.append(dict(f,**sensitivity,comparison_scope='V2_CORE_PEERS;PGEL_AND_CENTUM_ARE_EXTERNAL_CORE_COMPARISONS',
                v1_peer_members=o.get('peer_members',''),v1_peer_rs55=o.get('peer_rs55',''),v1_peer_return20_pct=o.get('peer_return_20s_pct',''),
                v2_change_in_peer_rs55=f.get('peer_rs55')-D(o['peer_rs55']) if f.get('peer_rs55') is not None and o.get('peer_rs55') else None,
                stock_outcome_unchanged=True,early_information_role='CANDLE_PREFIX_THROUGH_CHECKPOINT_ONLY',
                limitation='V2 retrospective constituent choice; not point-in-time-certified; unknown next-results boundaries retained'))
    write_rows(OUT/'ER04_REASSESSMENT.csv',rows)
    trajectories,summary=study.fixed_checkpoint_trajectories(features)
    by={(f['event_id'],f['checkpoint']):f for f in features}
    for t in trajectories:
        a=by[t['event_id'],t['early_checkpoint']];b=by[t['event_id'],t['later_event_session']]
        if t['status']!='AVAILABLE_RETROSPECTIVE':continue
        for field in ('peer_rs55','peer_pct_above_ema20','peer_return_20s_pct'):
            t[field+'_early']=a[field];t[field+'_later_outcome_only']=b[field]
            t[field+'_change_after_early_checkpoint']=b[field]-a[field] if a[field] is not None and b[field] is not None else None
    write_rows(OUT/'ER04_FIXED_TRAJECTORIES.csv',trajectories);write_rows(OUT/'ER04_FIXED_SUMMARY.csv',summary)
    original={(r['event_id'],r['early_checkpoint'],r['later_event_session']):r for r in read_csv(V1/'EVENT_FIXED_CHECKPOINT_TRAJECTORIES.csv')}
    for t in trajectories:
        orig=original[t['event_id'],str(t['early_checkpoint']),str(t['later_event_session'])]
        t['v1_gain_state']=orig['gain_state'];t['gain_state_preserved']=t['gain_state']==orig['gain_state']
    write_rows(OUT/'ER04_FIXED_TRAJECTORIES.csv',trajectories)
    write_rows(OUT/'EVENT_COUNTEREXAMPLES.csv',[t for t in trajectories if t['status']=='AVAILABLE_RETROSPECTIVE' and t['early_checkpoint']==3 and t['later_event_session']==20 and (t['symbol'] in ('PGEL','VOLTAMP','SYRMA','SHILCTECH'))])
    return features

def expansion(candles):
    focus=read_csv(INPUT/'corrected-current-q2.csv');assert len(focus)==117
    final={r['company_id']:r for r in read_csv(INPUT/'classifications.csv')}
    regs=read_csv(INPUT/'registry.csv');token_map={r['exchange']+':'+r['symbol']:r for r in regs if r['symbol']}
    overlay=read_csv(OUT/'BLUE_IDENTITY_OVERLAY.csv');blue={r['corrected_token'] for r in overlay};assert len(blue)==27
    counts=defaultdict(list);mapping=[];seen=set()
    cohort=[dict(r,blue_member=r['identity_key'] in blue,in_focus=True) for r in focus]
    for token in sorted(blue-{r['identity_key'] for r in focus}):
        reg=token_map.get(token,{})
        cohort.append(dict(company_id=reg.get('existing_registry_company_id',''),identity_key=token,display_stock_raw=reg.get('name',''),blue_member=True,in_focus=False))
    priced={r['instrument_key'] for r in candles};inventory=json.loads((INPUT/'retained-candle-inventory.json').read_text())
    priced.update(r['instrument_key'] for r in inventory)
    priced_ids={r['existing_registry_company_id'] for r in regs if 'NSE_EQ|'+r['isin'] in priced}
    for r in cohort:
        cid=r['company_id'];f=final.get(cid,{})
        code=f.get('primary_code') or 'REVIEW_'+f.get('status','UNLINKED')
        row=dict(company_id=cid,identity_key=r['identity_key'],company_name=r['display_stock_raw'],in_focus=r['in_focus'],blue_member=r['blue_member'],
            granular_subsector=code,classification_status=f.get('status','UNLINKED'),unranked_exposures_json=f.get('exposures_json','[]'),
            known_at=f.get('known_at',''),retained_daily_prices=cid in priced_ids,
            original_results=r.get('q2_results_raw',''),original_rating=r.get('rating_raw',''),original_guidance=r.get('guidance_raw',''),
            assessment_source='inputs/corrected-current-q2.csv; all 120 original rows separately frozen',
            membership_interpretation='Blue membership only; absence is not rejection; no performance label')
        mapping.append(row);counts[code].append(row)
    priorities=[]
    for code,rows in counts.items():
        verified=[f for f in final.values() if f['primary_code']==code and f['status'].startswith('VERIFIED')]
        verified_prices=sum(f['company_id'] in priced_ids for f in verified)
        coherent=not code.startswith('REVIEW_')
        readiness='RETAINED_PRICES_REVIEW_ACTIONS_AND_HISTORICAL_MEMBERSHIP' if coherent and verified_prices>=3 else 'NEEDS_PEER_PRICES' if coherent and len(verified)>=3 else 'NEEDS_BUSINESS_REVIEW'
        priorities.append(dict(granular_subsector=code,focus_count=sum(r['in_focus'] for r in rows),blue_count=sum(r['blue_member'] for r in rows),
            canonical_companies=';'.join(r['company_id'] for r in rows),verified_available_peers=len(verified),verified_peers_with_retained_prices=verified_prices,
            cohort_companies_with_prices=sum(r['retained_daily_prices'] for r in rows),business_coherence='NARROW_EXISTING_CODE_REQUIRES_HISTORICAL_REVIEW' if coherent else 'UNRESOLVED_OR_MULTI_ACTIVITY',
            readiness=readiness,ranking_purpose='ENGINEERING_RESEARCH_COVERAGE_ONLY;NOT_RETURN_FORECAST',
            priority_rule='Coherent codes first; descending Focus count, Blue count, verified peer price coverage, verified peers; code tie-break'))
    priorities.sort(key=lambda r:(r['granular_subsector'].startswith('REVIEW_'),-r['focus_count'],-r['blue_count'],-r['verified_peers_with_retained_prices'],-r['verified_available_peers'],r['granular_subsector']))
    for i,r in enumerate(priorities,1):r['research_priority']=i
    write_rows(OUT/'Q2_COHORT_MAPPING.csv',mapping);write_rows(OUT/'Q2_EXPANSION_PRIORITY.csv',priorities)

def preserve(compact=False):
    before=json.loads((OUT/'preservation-before.json').read_text());changed=[];missing=[]
    for p,h in before.items():
        f=ROOT/p
        if not f.exists():missing.append(p)
        elif digest(f)!=h:changed.append(p)
    if changed or (missing and not compact):raise ValueError('Original research changed')
    write_json(OUT/'preservation-verification.json',dict(baseline_files=len(before),changed=changed,missing_count=len(missing),full_workspace_verified=not changed and not missing,compact=compact))

def main():
    compact='--compact-review' in __import__('sys').argv
    preserve(compact)
    # Freeze study inputs separately from human-readable reports and verification outputs.
    frozen=INPUT/'frozen-input-hashes.json'
    hashes={p.name:digest(p) for p in INPUT.iterdir() if p.is_file() and p!=frozen}
    if frozen.exists() and json.loads(frozen.read_text())!=hashes:raise ValueError('Frozen V2 inputs changed')
    if not frozen.exists():write_json(frozen,hashes)
    candles,nifty=load();stocks,indices,loo,calendar,benchmark=calculate(candles)
    reconciliation(candles,calendar)
    coverage=read_csv(OUT/'DATA_COVERAGE.csv')
    inventory=json.loads((INPUT/'retained-candle-inventory.json').read_text())
    nsource=next(r for r in inventory if r['instrument_key']=='NSE_INDEX|Nifty 50')
    tail=json.loads((OUT/'nifty-tail-execution.json').read_text()) if (OUT/'nifty-tail-execution.json').exists() else {}
    coverage.append(dict(symbol='NIFTY50',instrument_key=nsource['instrument_key'],first_date=min(nifty),last_date=max(nifty),
        observed_common_sessions=sum(d in nifty for d in calendar),expected_sessions=len(calendar),missing_sessions=';'.join(d for d in calendar if d not in nifty),
        source_files=nsource['source']+(';'+tail['retained_path'] if tail.get('complete') else ''),source_sha256=nsource['sha256']+(';'+tail['response_sha256'] if tail.get('complete') else ''),status='COMPLETE_EXACT_OBSERVED_GRID' if all(d in nifty for d in calendar) else 'PARTIAL_NO_FILLING;TAIL_UNAUTHORIZED_OR_UNAVAILABLE'))
    write_rows(OUT/'DATA_COVERAGE.csv',coverage)
    write_rows(OUT/'INDEX_V2_DAILY.csv',[r for rs in indices.values() for r in rs])
    baseline=[]
    for g in PRIMARY:
        old={(r['quarter'],r['date']):r for r in read_csv(V1/(g+'_DAILY_INDEX.csv'))}
        for q in CUTOFFS:
            for r in indices[q,g]:
                b=old[q,r['date']]
                baseline.append(dict(quarter=q,subsector=g,date=r['date'],v1_members=b['members'],v2_members=r['members'],
                    v1_close=b['index_close'],v2_close=r['index_close'],v2_minus_v1_points=r['index_close']-D(b['index_close']),
                    v1_rs55=b['rs55'],v2_rs55=r['rs55'],v1_breadth_above20=b['pct_above_ema20'],v2_breadth_above20=r['pct_above_ema20'],
                    interpretation='Membership sensitivity; same daily-return architecture. V1 files preserved.'))
    write_rows(OUT/'V1_V2_COMPARISON.csv',baseline)
    write_rows(OUT/'CONSTITUENT_DAILY_FEATURES.csv',[r for rs in stocks.values() for r in rs])
    measures(stocks,indices,loo,calendar,benchmark,nifty)
    features=reassess(stocks,indices,loo,calendar,benchmark);expansion(candles)
    from .charts import render
    charts=render(candles,stocks,indices,loo,calendar,benchmark,nifty,features)
    preserve(compact)
    write_json(OUT/'status.json',dict(task='SS-INDEX-02',status='PROVISIONAL_RETROSPECTIVE_V2;NOT_CERTIFIED_HISTORICAL_BENCHMARK',
        sessions=len(calendar),primary_members={g:SETS[g] for g in PRIMARY},sensitivity_series=[g for g in SETS if g not in PRIMARY],
        formations=CUTOFFS,supported_events=len({f['event_id'] for f in features if f['status']=='AVAILABLE_RETROSPECTIVE'}),
        focus_count=117,blue_count=27,authenticated_peer_successes=8,authenticated_successes=9 if tail.get('complete') else 8,outstanding_peer_price_requests=0,
        nifty50_missing_sessions=[d for d in calendar if d not in nifty],chart_count=len(charts),
        remaining_limit='Complete primary action/suspension audit and historical constituent-selection evidence unavailable.',stop_after_ss_index_02=True))
    missing=[d for d in calendar if d not in nifty]
    from experiments.earnings_reaction_v1.retrieve_er03 import DailyRequest
    from experiments.earnings_reaction_v1.upstox_candles import url_for
    from datetime import date
    if missing:
        q=DailyRequest('NSE_INDEX|Nifty 50','days',1,date.fromisoformat(missing[0]),date.fromisoformat(missing[-1]))
        write_json(OUT/'REMAINING_ACQUISITION_MANIFEST.json',dict(status='NOT_AUTHORIZED_NOT_EXECUTED',request_count=1,automatic_retries=0,
            requests=[dict(symbol='NIFTY50',instrument_key=q.instrument_key,exchange='NSE_INDEX',unit='days',interval=1,from_date=str(q.from_date),to_date=str(q.to_date),url=url_for(q),
                reason='Complete Nifty50 comparison tail only; no impact on primary RS55 benchmark or V2 construction')],
            peer_requests_remaining=0,non_price_evidence_gaps=['Complete primary action/suspension feed','Historical first-publication archives and selection records'],
            execution_policy='Requires separate explicit approval. Serial one attempt, zero retries, stop on failure. No global-market expansion.'))
    write_json(OUT/'ACQUISITION_RESOLUTION.json',dict(peer_requests_completed=8,peer_requests_remaining=0,nifty50_missing_sessions=len(missing),
        nifty50_request_status='COMPLETE_SEPARATELY_APPROVED' if not missing else 'PENDING_SEPARATE_APPROVAL',
        original_manifests='Immutable proposals; execution receipts and this resolution report actual state',
        outstanding_price_requests=1 if missing else 0))
    write_json(OUT/'CURRENT_OUTSTANDING_ACQUISITION_MANIFEST.json',dict(request_count=1 if missing else 0,
        requests=json.loads((OUT/'REMAINING_ACQUISITION_MANIFEST.json').read_text())['requests'] if missing else [],
        status='NIFTY50_PENDING' if missing else 'ALL_APPROVED_PRICE_REQUESTS_COMPLETE',
        immutable_approved_proposals=['OUTSTANDING_ACQUISITION_MANIFEST.json','REMAINING_ACQUISITION_MANIFEST.json'],
        non_price_evidence_gaps=['Complete primary corporate-action/provider-adjustment attestation','Exhaustive exchange suspension/calendar audit','Historical source-publication archives and constituent-selection records']))
    print(json.dumps(dict(sessions=len(calendar),series=len(indices),charts=len(charts))))

if __name__=='__main__':main()
