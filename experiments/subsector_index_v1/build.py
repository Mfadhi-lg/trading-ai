"""SS-INDEX-01 offline build. Does not rewrite or execute ER/SS builders.

python3 -B -m experiments.subsector_index_v1.build
First run snapshots retained inputs; subsequent runs verify their frozen hashes.
"""
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime
import json
import shutil
from experiments.earnings_reaction_v1.er03 import ROOT,read_csv,write_json,digest
from .sources import OUT,INPUT,MEMBERS,NAMES,CUTOFFS,MISSING_PEERS,prepare,write_rows,membership_evidence,acquisition_manifest
from .core import normalise_prices,constituent_features,equal_weight_index,stock_rs,LOOKBACKS
from .study import CP,HORIZONS,snapshot,outcome,comparisons,group_for,fixed_checkpoint_trajectories

def verify_inputs():
    path=INPUT/'frozen-input-hashes.json'
    actual={p.name:digest(p) for p in INPUT.iterdir() if p.is_file() and p.name!='frozen-input-hashes.json'}
    if path.exists():
        before=json.loads(path.read_text())
        if actual!=before:raise ValueError('Frozen study inputs changed; review explicitly instead of silently updating')
    else:write_json(path,actual)

def read_prices():
    rows=read_csv(INPUT/'RETAINED_DAILY_CANDLES.csv')
    for r in rows:
        for k in ('raw_open','raw_high','raw_low','raw_close','raw_volume'):r[k]=D(r[k])
    return rows

def calculate(candles,calendar=None):
    if calendar is None:calendar=sorted(r['date'] for r in candles if r['symbol']=='NIFTYMIDSML400' and '2025-12-01'<=r['date']<='2026-10-30')
    if len(set(calendar))!=len(calendar):raise ValueError('Duplicate benchmark session')
    benchmark={r['date']:r['raw_close'] for r in candles if r['symbol']=='NIFTYMIDSML400' and r['date'] in calendar}
    review=json.loads((INPUT/'corporate-action-review.json').read_text())
    actions=review['in_window_share_unit_events']+review['ordinary_cash_dividends']
    stocks={}
    for s in sorted({s for syms in MEMBERS.values() for s in syms}):
        raw=[r for r in candles if r['symbol']==s and r['date'] in calendar]
        adjusted=normalise_prices(raw,[a for a in actions if a['symbol']==s and a['ex_date'] in calendar])
        stocks[s]=stock_rs(constituent_features(adjusted,calendar),calendar,benchmark)
    indices={};loo={}
    for quarter,cutoff in CUTOFFS.items():
        for group,syms in MEMBERS.items():
            rows=equal_weight_index(stocks,calendar,benchmark,members=syms,min_members=3)
            for r in rows:r.update(subsector=group,series_name=NAMES[group],quarter=quarter,formation_cutoff=cutoff,
                membership_status='RETROSPECTIVE_DESCRIPTIVE',period_role='PRE_FORMATION_MEMBERSHIP_BACKCAST_WARMUP' if r['date']<=cutoff else 'POST_FORMATION_FIXED_MEMBERSHIP',
                base_date=calendar[0],base_value=1000,rebalancing='DAILY_EQUAL_RETURN_WEIGHTS;1/N_AT_EACH_PREVIOUS_CLOSE;NO_CONSTITUENT_CHANGES')
            indices[quarter,group]=rows
            for s in syms:
                peers=tuple(p for p in syms if p!=s)
                rows=equal_weight_index(stocks,calendar,benchmark,members=peers,min_members=2)
                for r in rows:r.update(subsector=group,excluded_symbol=s,quarter=quarter,formation_cutoff=cutoff,
                    membership_status='RETROSPECTIVE_DESCRIPTIVE',small_peer_set_warning='TWO_RETAINED_PEERS_ONLY;NOT_COMPLETE_SUBSECTOR',
                    period_role='PRE_FORMATION_MEMBERSHIP_BACKCAST_WARMUP' if r['date']<=cutoff else 'POST_FORMATION_FIXED_MEMBERSHIP')
                loo[quarter,s]=rows
    return stocks,indices,loo,calendar,benchmark

def preserve(compact=False):
    path=OUT/'preservation-before.json'
    before=json.loads(path.read_text())
    present={p:h for p,h in before.items() if (ROOT/p).is_file()}
    changed=[p for p,h in present.items() if digest(ROOT/p)!=h]
    missing=[p for p in before if not (ROOT/p).is_file()]
    result=dict(files_in_original_baseline=len(before),files_present=len(present),changed=changed,missing_original_files=len(missing),
        compact_review=compact,full_preservation_verified=not changed and not missing,included_files_unchanged=not changed)
    write_json(OUT/('compact-preservation-verification.json' if compact else 'preservation-verification.json'),result)
    if changed or missing and not compact:raise ValueError('Prior SS/ER outputs changed or missing')
    return result

def retain_provider_evidence():
    ledger=json.loads((INPUT/'source-provenance.json').read_text());records=[]
    dest=OUT/'evidence/provider';dest.mkdir(parents=True,exist_ok=True)
    for src,sha in ledger['original_provider_files'].items():
        original=ROOT/src
        if not original.exists():continue  # compact review already contains retained copies
        if digest(original)!=sha:raise ValueError('Original raw provider response hash mismatch')
        target=dest/(sha+'.json')
        if not target.exists():shutil.copyfile(original,target)
        if digest(target)!=sha:raise ValueError('Copied raw response mismatch')
        records.append(dict(original_source=src,retained_copy=str(target.relative_to(OUT)),sha256=sha))
    if records:write_json(OUT/'raw-provider-evidence.json',records)

def loo_comparisons(stocks,indices,loo,calendar,benchmark):
    rows=[];coverage=[]
    pilots=read_csv(INPUT/'pilot-companies.csv')
    for quarter in CUTOFFS:
        for p in pilots:
            s=p['symbol'];group=group_for(s)
            coverage.append(dict(symbol=s,quarter=quarter,subsector=group or '',peer_count=2 if group else 0,
                status='AVAILABLE_TWO_PEER_RETAINED_SUBSET' if group else 'EXCLUDED_RUBBER_RECYCLER_NOT_TYRE_MANUFACTURER' if s=='GRPLTD' else 'OUTSIDE_THREE_SUBSECTORS',
                pure_core_ems_peer_count=0 if group=='EMS' else None,
                pure_core_ems_status='INSUFFICIENT_RETAINED_CORE_PEERS;EXTENDED_SUBSET_SEPARATE' if group=='EMS' else 'NOT_APPLICABLE'))
            if not group:continue
            for i,day in enumerate(calendar):
                stock=stocks[s][i];sector=indices[quarter,group][i];peer=loo[quarter,s][i]
                r=dict(quarter=quarter,formation_cutoff=CUTOFFS[quarter],subsector=group,symbol=s,date=day,stock_adjusted_close=stock['adjusted_close'],
                    stock_rs55=stock['rs55'],subsector_rs55=sector['rs55'],leave_one_out_rs55=peer['rs55'],
                    leave_one_out_index_close=peer['index_close'],full_index_close=sector['index_close'],benchmark_close=benchmark[day],
                    peer_count=peer['expected_constituents'],peer_members=peer['members'],target_in_peer_set=s in peer['members'].split(';'),
                    membership_status='RETROSPECTIVE_DESCRIPTIVE',small_peer_set_warning='TWO_PEERS_ONLY;EXTENDED_MIX_IN_EMS',
                    available_at=peer['available_at'],missing_data_flag=peer['missing_data_flag'])
                if r['target_in_peer_set']:raise ValueError('Own stock contamination')
                for n in LOOKBACKS:
                    base=stocks[s][i-n]['adjusted_close'] if i>=n else None
                    sr=100*(stock['adjusted_close']/base-1) if stock['adjusted_close'] is not None and base is not None else None
                    pr=peer[f'return_{n}s_pct'];br=100*(benchmark[day]/benchmark[calendar[i-n]]-1) if i>=n else None
                    r.update({f'stock_return_{n}s_pct':sr,f'peer_return_{n}s_pct':pr,f'benchmark_return_{n}s_pct':br,
                              f'stock_minus_peer_{n}s_pp':sr-pr if sr is not None and pr is not None else None})
                rows.append(r)
    write_rows(OUT/'LEAVE_ONE_OUT_DAILY_COMPARISONS.csv',rows)
    write_rows(OUT/'LEAVE_ONE_OUT_COVERAGE.csv',coverage)
    return rows

def event_study(stocks,indices,loo,calendar,benchmark):
    events=json.loads((INPUT/'EVENTS.json').read_text())
    er04={(r['event_id'],int(r['checkpoint']),int(r['horizon'])):r for r in read_csv(INPUT/'forward-returns-drawdowns.csv')}
    features=[];outcomes=[];causal=[]
    for event in events:
        for cp in CP:
            f=snapshot(event,cp,stocks,indices,loo,calendar)
            if f['checkpoint_date']:f['nifty_benchmark_close']=benchmark[f['checkpoint_date']]
            features.append(f)
            for horizon in HORIZONS:outcomes.append(outcome(event,f,horizon,stocks,loo,calendar,benchmark,er04))
    write_rows(OUT/'ER04_SUBSECTOR_CHECKPOINTS.csv',features)
    write_rows(OUT/'ER04_SUBSECTOR_FORWARD_COMPARISONS.csv',outcomes)
    summary,cases=comparisons(features,outcomes)
    write_rows(OUT/'CONTINUATION_FADING_COMPARISON.csv',summary)
    write_rows(OUT/'EVENT_COUNTEREXAMPLES.csv',cases)
    trajectories,trajectory_summary=fixed_checkpoint_trajectories(features)
    write_rows(OUT/'EVENT_FIXED_CHECKPOINT_TRAJECTORIES.csv',trajectories)
    write_rows(OUT/'FIXED_CHECKPOINT_CONTINUATION_FADING.csv',trajectory_summary)
    return features,outcomes,summary,cases

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    if not (INPUT/'RETAINED_DAILY_CANDLES.csv').exists():prepare()
    verify_inputs()
    compact='--compact-review' in __import__('sys').argv
    preserve(compact)
    candles=read_prices();retain_provider_evidence()
    membership_evidence(candles);acquisition_manifest()
    stocks,indices,loo,calendar,benchmark=calculate(candles)
    write_rows(OUT/'CONSTITUENT_DAILY_FEATURES.csv',[r for rs in stocks.values() for r in rs])
    for group in MEMBERS:write_rows(OUT/(group+'_DAILY_INDEX.csv'),[r for (q,g),rs in indices.items() if g==group for r in rs])
    write_rows(OUT/'BASKET_SCOPE_AND_COVERAGE.csv',[dict(quarter=q,cutoff=cutoff,subsector=g,
        fixed_retained_members=';'.join(MEMBERS[g]),fixed_retained_count=len(MEMBERS[g]),
        previously_researched_missing_price_candidates=';'.join(MISSING_PEERS[g]),missing_price_candidate_count=len(MISSING_PEERS[g]),
        scope='NAMED_RETAINED_SUBSET_ONLY;NEITHER_FULL_SECTOR_NOR_ALL_MAJOR_UNIVERSE_MEMBERS',
        admission_rule='Fixed before this study; no daily membership renormalization. Acquisition alone does not certify missing candidates.')
        for q,cutoff in CUTOFFS.items() for g in MEMBERS])
    write_rows(OUT/'LEAVE_ONE_OUT_INDICES.csv',[r for rs in loo.values() for r in rs])
    write_rows(OUT/'SESSION_ALIGNMENT.csv',[dict(date=d,benchmark_observed=True,stock_count=sum(any(r['date']==d for r in candles if r['symbol']==s) for syms in MEMBERS.values() for s in syms),expected_stock_count=9,
        special_session=d=='2026-02-01',calendar_basis='EXACT_RETAINED_NIFTYMIDSML400_OBSERVED_SESSIONS;NOT_AN_EXHAUSTIVE_EXCHANGE_CALENDAR',
        special_session_source='https://upstox.com/announcements/updates/announcement-20/' if d=='2026-02-01' else '') for d in calendar])
    write_rows(OUT/'EMS_CORE_SENSITIVITY_COVERAGE.csv',[dict(quarter=q,core_candidates='SYRMA;DIXON;AVALON;KAYNES',retained_core_members='SYRMA',
        retained_core_count=1,core_index_status='INSUFFICIENT_PEERS',extended_exposure_members=';'.join(MEMBERS['EMS']),
        extended_index_status='CALCULATED_RETROSPECTIVE_DIVERSIFIED_EXPOSURE_SUBSET;NOT_PURE_CORE') for q in CUTOFFS])
    loo_rows=loo_comparisons(stocks,indices,loo,calendar,benchmark)
    features,outcomes,summaries,cases=event_study(stocks,indices,loo,calendar,benchmark)
    from .charts import render
    names=render(candles,stocks,indices,loo,calendar,benchmark,features)
    preserve(compact)
    write_json(OUT/'status.json',dict(task='SS-INDEX-01',status='ACTUAL_THREE_DAILY_SUBSET_INDICES_WITH_EXPLICIT_LIMITS',
        analytical_membership='RETROSPECTIVE_DESCRIPTIVE',daily_sessions=len(calendar),start=calendar[0],end=calendar[-1],
        members=MEMBERS,formations=CUTOFFS,index_datasets=3,formation_series=6,leave_one_out_series=len(loo),
        supported_events=len({f['event_id'] for f in features if f['status']=='AVAILABLE_RETROSPECTIVE'}),all_existing_events=24,
        checkpoint_rows=len(features),supported_checkpoint_rows=sum(f['status']=='AVAILABLE_RETROSPECTIVE' for f in features),
        new_authenticated_requests=0,outstanding_peer_requests=7,chart_count=len(names),
        corporate_action_limit='No in-window share-unit actions identified; primary full action feed unavailable. Secondary action review and known pre-window events retained.',
        ems_limit='Extended diversified manufacturing exposure only; pure-core benchmark lacks three prices',stop_after_three_groups=True))
    print((OUT/'status.json').read_text())

if __name__=='__main__':main()
