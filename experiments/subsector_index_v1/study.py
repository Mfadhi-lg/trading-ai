"""Retrospective joins to frozen ER04 events. Features never read future rows."""
from decimal import Decimal as D
from statistics import median
from .sources import MEMBERS,CUTOFFS

CP=(1,3,5,20,40);HORIZONS=(5,10,20,40)

def group_for(symbol):
    return next((g for g,syms in MEMBERS.items() if symbol in syms),None)

def snapshot(event,session,stocks,indices,loo,calendar):
    s=event['symbol'];g=group_for(s);quarter=event['quarter'];cutoff=CUTOFFS[quarter]
    result=dict(event_id=event['event_id'],symbol=s,quarter=quarter,formation_cutoff=cutoff,checkpoint=session,
        results_date=event['reported_results_date'],next_results_date=event['next_results_date'],subsector=g or '',
        next_results_date_role='OUTCOME_ISOLATION_AND_CENSORING_ONLY;NOT_CHECKPOINT_SIGNAL',
        checkpoint_date='',status='UNSUPPORTED_BUSINESS_OR_OUTSIDE_THREE_GROUPS',
        membership_status='RETROSPECTIVE_DESCRIPTIVE',prospective_use_status='CAUSAL_PRICE_PREFIX_ONLY;RETROSPECTIVE_MEMBERSHIP_NOT_HISTORICAL_SELECTION',
        available_at='',availability_basis='SESSION_CLOSE_CONCEPTUAL;PROVIDER_BOUND_NEXT_IST_MIDNIGHT',
        stock_close=None,stock_early_return_pct=None,stock_rs55=None,stock_rs55_change_1s=None,stock_rs55_change_5s=None,
        index_close=None,index_return_5s_pct=None,index_return_10s_pct=None,index_return_20s_pct=None,index_return_40s_pct=None,index_rs55=None,index_ema10=None,index_ema20=None,
        peer_count=0,peer_members='',peer_index_close=None,peer_return_5s_pct=None,peer_return_10s_pct=None,peer_return_20s_pct=None,peer_return_40s_pct=None,
        peer_rs55=None,peer_rs55_change_1s=None,peer_rs55_change_5s=None,peer_ema10=None,peer_ema20=None,peer_ema20_change=None,
        peer_pct_above_ema20=None,peer_pct_rising=None,peer_median_rvol20=None,peer_pct_rvol_gt1=None,peer_traded_value_proxy_crore=None,
        peer_state_20s='UNAVAILABLE',nifty_benchmark_close=None,
        business_mix_warning='EXTENDED_DIVERSIFIED_EMS_EXPOSURE;NOT_PURE_CORE' if g=='EMS' else '')
    eligible=[d for d in calendar if d>event['reported_results_date'] and (not event['next_results_date'] or d<event['next_results_date'])]
    if len(eligible)<session:
        result['status']='CHECKPOINT_CENSORED_BY_NEXT_RESULTS_OR_DATA_END';return result
    day=eligible[session-1];result['checkpoint_date']=day
    if not g:return result
    if cutoff>=day:raise ValueError('Formation must precede research checkpoint')
    pos=calendar.index(day);stock=stocks[s][pos];sector=indices[quarter,g][pos];peer=loo[quarter,s][pos]
    if peer['index_close'] is None:
        result['status']='INSUFFICIENT_OR_MISSING_PEERS';return result
    prior=next((r for r in reversed(stocks[s][:pos]) if r['date']<=event['reported_results_date']),None)
    def delta(field,n):
        p=stocks[s][pos-n] if pos>=n else None
        return stock[field]-p[field] if p and p[field] is not None and stock[field] is not None else None
    result.update(status='AVAILABLE_RETROSPECTIVE',available_at=peer['available_at'],stock_close=stock['adjusted_close'],
        stock_early_return_pct=100*(stock['adjusted_close']/prior['adjusted_close']-1) if prior and prior['adjusted_close'] is not None else None,
        stock_rs55=stock['rs55'],stock_rs55_change_1s=delta('rs55',1),stock_rs55_change_5s=delta('rs55',5),
        index_close=sector['index_close'],index_rs55=sector['rs55'],index_ema10=sector['ema10'],index_ema20=sector['ema20'],
        peer_count=peer['expected_constituents'],peer_members=peer['members'],peer_index_close=peer['index_close'],
        peer_rs55=peer['rs55'],peer_rs55_change_1s=peer['rs55_change_1s'],peer_rs55_change_5s=peer['rs55_change_5s'],
        peer_ema10=peer['ema10'],peer_ema20=peer['ema20'],peer_ema20_change=peer['ema20_change'],
        peer_pct_above_ema20=peer['pct_above_ema20'],peer_pct_rising=peer['pct_rising'],
        peer_median_rvol20=peer['median_constituent_rvol20'],peer_pct_rvol_gt1=peer['pct_rvol_gt1'],
        peer_traded_value_proxy_crore=peer['traded_value_proxy_crore'],
        peer_state_20s='STRENGTHENING' if peer['return_20s_pct'] is not None and peer['return_20s_pct']>0 else 'WEAKENING' if peer['return_20s_pct'] is not None and peer['return_20s_pct']<0 else 'UNCHANGED' if peer['return_20s_pct']==0 else 'UNAVAILABLE')
    for n in HORIZONS:
        result[f'index_return_{n}s_pct']=sector[f'return_{n}s_pct']
        result[f'peer_return_{n}s_pct']=peer[f'return_{n}s_pct']
    return result

def outcome(event,cp,horizon,stocks,loo,calendar,benchmark,er04):
    result=dict(event_id=event['event_id'],symbol=event['symbol'],quarter=event['quarter'],checkpoint=cp['checkpoint'],
        checkpoint_date=cp['checkpoint_date'],horizon=horizon,first_later_date='',exit_date='',observed_sessions=0,
        horizon_complete=False,censored=True,censor_reason='UNSUPPORTED_CHECKPOINT',
        stock_close_return_pct=None,peer_close_return_pct=None,benchmark_close_return_pct=None,
        stock_minus_peer_pp=None,stock_minus_benchmark_pp=None,peer_minus_benchmark_pp=None,
        stock_partial_close_return_pct=None,peer_partial_close_return_pct=None,
        early_rally=None,continuation=None,fade=None,stock_underperformed_strong_peers=None,
        return_basis='CHECKPOINT_CLOSE_TO_NTH_LATER_CLOSE_ON_IDENTICAL_DATES;DESCRIPTIVE_NOT_EXECUTABLE_NEXT_OPEN',
        er04_next_open_return_pct=None,er04_next_open_relative_return_pp=None,
        er04_basis='ORIGINAL_ER04_NEXT_SESSION_OPEN_TO_NTH_CLOSE;DO_NOT_SUBTRACT_CLOSE_INDEX_RETURN',
        membership_status='RETROSPECTIVE_DESCRIPTIVE')
    original=er04.get((event['event_id'],cp['checkpoint'],horizon))
    if original:
        result.update(er04_next_open_return_pct=D(original['return_pct']) if original['return_pct'] else None,
            er04_next_open_relative_return_pp=D(original['relative_return_pp']) if original['relative_return_pp'] else None)
    if cp['status']!='AVAILABLE_RETROSPECTIVE':return result
    i=calendar.index(cp['checkpoint_date']);later=[d for d in calendar[i+1:] if not event['next_results_date'] or d<event['next_results_date']][:horizon]
    full=len(later)==horizon
    result.update(first_later_date=later[0] if later else '',exit_date=later[-1] if later else '',observed_sessions=len(later),horizon_complete=full,
        censored=not full or not bool(event['next_results_date']),
        censor_reason=('' if full else 'NEXT_RESULTS_OR_DATA_END;')+('NEXT_RESULTS_BOUNDARY_UNKNOWN' if not event['next_results_date'] else ''))
    if not later:return result
    end=calendar.index(later[-1]);s=event['symbol'];quarter=event['quarter']
    stock_base=stocks[s][i]['adjusted_close'];stock_end=stocks[s][end]['adjusted_close']
    peer_base=loo[quarter,s][i]['index_close'];peer_end=loo[quarter,s][end]['index_close']
    if stock_end is None or peer_end is None or any(stocks[s][j]['adjusted_close'] is None or loo[quarter,s][j]['index_close'] is None for j in range(i,end+1)):
        result.update(horizon_complete=False,censored=True,censor_reason='MISSING_EXACT_FORWARD_SESSION');return result
    sr=100*(stock_end/stock_base-1);pr=100*(peer_end/peer_base-1);br=100*(benchmark[later[-1]]/benchmark[cp['checkpoint_date']]-1)
    early=cp['stock_early_return_pct'] is not None and cp['stock_early_return_pct']>0
    if full:
        result.update(stock_close_return_pct=sr,peer_close_return_pct=pr,benchmark_close_return_pct=br,
            stock_minus_peer_pp=sr-pr,stock_minus_benchmark_pp=sr-br,peer_minus_benchmark_pp=pr-br,
            early_rally=early,continuation=early and sr>0,fade=early and sr<=0,
            stock_underperformed_strong_peers=pr>0 and sr<pr)
    else:result.update(stock_partial_close_return_pct=sr,peer_partial_close_return_pct=pr)
    return result

PREDICATES={
 'peer_return20_positive':lambda f:None if f['peer_return_20s_pct'] is None else f['peer_return_20s_pct']>0,
 'peer_ema20_rising':lambda f:None if f['peer_ema20_change'] is None else f['peer_ema20_change']>0,
 'peer_rs55_deteriorating_5s':lambda f:None if f['peer_rs55_change_5s'] is None else f['peer_rs55_change_5s']<0,
 'stock_rs55_deteriorating_5s':lambda f:None if f['stock_rs55_change_5s'] is None else f['stock_rs55_change_5s']<0,
 'peer_return20_negative_and_rs_deteriorating':lambda f:None if f['peer_return_20s_pct'] is None or f['peer_rs55_change_5s'] is None else f['peer_return_20s_pct']<0 and f['peer_rs55_change_5s']<0}

def comparisons(features,outcomes):
    by={(f['event_id'],f['checkpoint']):f for f in features};summaries=[];cases=[]
    for cp in CP:
      for horizon in HORIZONS:
       for group in ('ALL','TYRES','EMS','TRANSFORMERS'):
        pool=[o for o in outcomes if o['checkpoint']==cp and o['horizon']==horizon and o['horizon_complete'] and
              (group=='ALL' or group_for(o['symbol'])==group)]
        for boundary in ('ALL_FULL_WINDOWS','KNOWN_NEXT_RESULTS_ONLY'):
         eligible=[o for o in pool if boundary=='ALL_FULL_WINDOWS' or not o['censored']]
         for name,predicate in PREDICATES.items():
          for condition in (True,False):
            chosen=[o for o in eligible if predicate(by[o['event_id'],cp]) is condition]
            rallies=[o for o in chosen if o['early_rally']]
            vals=[o['stock_close_return_pct'] for o in chosen];rel=[o['stock_minus_peer_pp'] for o in chosen]
            summaries.append(dict(checkpoint=cp,horizon=horizon,subsector=group,boundary_scope=boundary,pattern=name,condition=condition,
                events=len(chosen),early_rally_events=len(rallies),continuations=sum(o['continuation'] for o in rallies),fades=sum(o['fade'] for o in rallies),
                positive_stock_returns=sum(v>0 for v in vals),negative_stock_returns=sum(v<0 for v in vals),
                outperformed_peers=sum(v>0 for v in rel),underperformed_peers=sum(v<0 for v in rel),
                median_stock_close_return_pct=median(vals) if vals else None,median_stock_minus_peer_pp=median(rel) if rel else None,
                unknown_boundary_events=sum(o['censored'] for o in chosen),
                inference='EXPLORATORY;OVERLAPPING_WINDOWS_AND_COMPANIES;NO_CAUSAL_OR_SIGNIFICANCE_CLAIM',
                membership_status='RETROSPECTIVE_DESCRIPTIVE'))
        if group=='ALL':
            for o in pool:
                f=by[o['event_id'],cp]
                label='STOCK_UNDERPERFORMED_DESPITE_POSITIVE_EARLIER_PEERS' if f['peer_state_20s']=='STRENGTHENING' and o['stock_minus_peer_pp']<0 else 'STOCK_GAINED_DESPITE_NEGATIVE_EARLIER_PEERS' if f['peer_state_20s']=='WEAKENING' and o['stock_close_return_pct']>0 else 'OTHER_DESCRIPTIVE_CASE'
                cases.append(dict(event_id=o['event_id'],symbol=o['symbol'],subsector=group_for(o['symbol']),checkpoint=cp,checkpoint_date=f['checkpoint_date'],horizon=horizon,
                    peer_members=f['peer_members'],peer_return20_at_checkpoint_pct=f['peer_return_20s_pct'],peer_rs55_change5_at_checkpoint=f['peer_rs55_change_5s'],
                    stock_rs55_change5_at_checkpoint=f['stock_rs55_change_5s'],early_stock_return_pct=f['stock_early_return_pct'],
                    stock_forward_close_return_pct=o['stock_close_return_pct'],peer_forward_close_return_pct=o['peer_close_return_pct'],stock_minus_peer_pp=o['stock_minus_peer_pp'],
                    continuation=o['continuation'],fade=o['fade'],unknown_next_results_boundary=o['censored'],case_type=label))
    return summaries,cases

def fixed_checkpoint_trajectories(features):
    """S1/S3/S5 observations versus event-session S20/S40 outcomes.

    This differs from '20 sessions after S3'. Late peer measurements are
    explicitly outcome descriptions and are never early pattern inputs.
    """
    by={(f['event_id'],f['checkpoint']):f for f in features};rows=[]
    for event_id in sorted({f['event_id'] for f in features}):
      for early_n in (1,3,5):
       for late_n in (20,40):
        early=by[event_id,early_n];late=by[event_id,late_n]
        valid=early['status']==late['status']=='AVAILABLE_RETROSPECTIVE'
        sr=100*(late['stock_close']/early['stock_close']-1) if valid else None
        pr=100*(late['peer_index_close']/early['peer_index_close']-1) if valid else None
        rally=early['stock_early_return_pct']>0 if valid else None
        rows.append(dict(event_id=event_id,symbol=early['symbol'],subsector=early['subsector'],early_checkpoint=early_n,later_event_session=late_n,
            early_date=early['checkpoint_date'],later_date=late['checkpoint_date'],status='AVAILABLE_RETROSPECTIVE' if valid else 'UNSUPPORTED_OR_CENSORED',
            early_stock_return_from_pre_results_pct=early['stock_early_return_pct'],early_peer_return20_pct=early['peer_return_20s_pct'],
            early_stock_rs55_change5=early['stock_rs55_change_5s'],early_peer_rs55_change5=early['peer_rs55_change_5s'],
            later_stock_return_from_pre_results_pct=late['stock_early_return_pct'] if valid else None,
            stock_return_early_to_late_pct=sr,peer_return_early_to_late_pct=pr,
            stock_minus_peer_early_to_late_pp=sr-pr if valid else None,
            early_positive_rally=rally,continuation=rally and sr>0 if valid else None,fade=rally and sr<=0 if valid else None,
            gain_state='UNAVAILABLE' if not valid else 'NO_POSITIVE_EARLY_RALLY' if not rally else 'RALLY_CONTINUED' if sr>0 else 'EARLY_GAIN_LOST' if late['stock_early_return_pct']<=0 else 'GAIN_RETAINED_BUT_REDUCED',
            later_peer_return20_outcome_only_pct=late['peer_return_20s_pct'] if valid else None,
            later_peer_rs55_outcome_only=late['peer_rs55'] if valid else None,
            later_macro_role='AFTER_EARLY_CHECKPOINT_OUTCOME_DESCRIPTION_ONLY;NOT_PREDICTOR',
            unknown_next_results_boundary=not bool(early['next_results_date']),membership_status='RETROSPECTIVE_DESCRIPTIVE'))
    summaries=[]
    for early_n in (1,3,5):
      for late_n in (20,40):
       for group in ('ALL','TYRES','EMS','TRANSFORMERS'):
        for scope in ('ALL_SUPPORTED','KNOWN_NEXT_RESULTS_ONLY'):
         for pattern in ('early_peer_return20_positive','early_peer_rs55_deteriorating'):
          for present in (True,False):
            chosen=[]
            for r in rows:
                if r['status']!='AVAILABLE_RETROSPECTIVE' or r['early_checkpoint']!=early_n or r['later_event_session']!=late_n:continue
                if group!='ALL' and r['subsector']!=group:continue
                if scope=='KNOWN_NEXT_RESULTS_ONLY' and r['unknown_next_results_boundary']:continue
                v=r['early_peer_return20_pct'] if pattern=='early_peer_return20_positive' else r['early_peer_rs55_change5']
                condition=(v>0 if pattern=='early_peer_return20_positive' else v<0) if v is not None else None
                if condition is present:chosen.append(r)
            rallies=[r for r in chosen if r['early_positive_rally']]
            summaries.append(dict(early_checkpoint=early_n,later_event_session=late_n,subsector=group,boundary_scope=scope,
                pattern=pattern,condition=present,events=len(chosen),early_rallies=len(rallies),continuations=sum(r['continuation'] for r in rallies),
                fades=sum(r['fade'] for r in rallies),early_gain_lost=sum(r['gain_state']=='EARLY_GAIN_LOST' for r in rallies),
                gain_reduced_but_retained=sum(r['gain_state']=='GAIN_RETAINED_BUT_REDUCED' for r in rallies),
                median_stock_return_early_to_late_pct=median([r['stock_return_early_to_late_pct'] for r in chosen]) if chosen else None,
                unknown_boundary_events=sum(r['unknown_next_results_boundary'] for r in chosen),inference='DESCRIPTIVE_FIXED_EVENT_SESSIONS;SMALL_DEPENDENT_SAMPLE;NO_CAUSALITY'))
    return rows,summaries
