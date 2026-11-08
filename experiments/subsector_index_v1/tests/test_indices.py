import copy
import unittest
from decimal import Decimal as D
from experiments.subsector_index_v1.core import normalise_prices,constituent_features,equal_weight_index
from experiments.subsector_index_v1.build import read_prices,calculate,verify_inputs
from experiments.subsector_index_v1.study import snapshot,outcome,fixed_checkpoint_trajectories
from experiments.subsector_index_v1.sources import MEMBERS,INPUT,CUTOFFS
import json

def fixture(symbol,prices,volumes=None):
    return [dict(symbol=symbol,date=f'2026-01-{i+1:02}',raw_close=D(p),raw_volume=D((volumes or [100]*len(prices))[i])) for i,p in enumerate(prices)]

def build_fixture(raw,actions=()):
    cal=[r['date'] for r in next(iter(raw.values()))];bm={d:D(100) for d in cal};features={}
    for s,rows in raw.items():features[s]=constituent_features(normalise_prices(rows,[a for a in actions if a['symbol']==s]),cal)
    return equal_weight_index(features,cal,bm,members=tuple(raw)),features,cal,bm

class IndexArithmeticTests(unittest.TestCase):
    def test_daily_equal_weight_not_raw_price_average(self):
        raw={'A':fixture('A',['1','1.1']),'B':fixture('B',['100000','100000'])}
        rows,_,_,_=build_fixture(raw)
        self.assertEqual(rows[0]['index_close'],1000)
        self.assertEqual(rows[1]['index_close'],1050)

    def test_daily_rebalance_differs_from_initial_holdings(self):
        raw={'A':fixture('A',[100,200,100]),'B':fixture('B',[100,100,200])}
        rows,_,_,_=build_fixture(raw)
        self.assertEqual(rows[-1]['index_close'],1875)
        self.assertNotEqual(rows[-1]['index_close'],1500)

    def test_split_is_neutral_and_volume_comparable(self):
        action=dict(symbol='A',ex_date='2026-01-02',kind='SPLIT',new_shares_per_old_share='2',provider_basis='UNADJUSTED_CONFIRMED')
        raw={'A':fixture('A',[100,50,50],[100,200,200]),'B':fixture('B',[100,100,100])}
        rows,features,_,_=build_fixture(raw,[action])
        self.assertEqual(rows[-1]['index_close'],1000)
        self.assertEqual([r['comparable_volume'] for r in features['A']],[100,100,100])
        self.assertEqual(features['A'][1]['traded_value_proxy_crore'],features['A'][0]['traded_value_proxy_crore'])

    def test_bonus_and_split_use_same_share_unit_logic(self):
        action=dict(symbol='A',ex_date='2026-01-02',kind='BONUS',new_shares_per_old_share='1.5',provider_basis='UNADJUSTED_CONFIRMED')
        rows=normalise_prices(fixture('A',[150,100],[100,150]),[action])
        self.assertEqual(rows[0]['adjusted_close'],rows[1]['adjusted_close'])

    def test_unknown_adjustment_basis_blocks_double_adjustment(self):
        action=dict(symbol='A',ex_date='2026-01-02',kind='SPLIT',new_shares_per_old_share='2')
        rows=normalise_prices(fixture('A',[100,100]),[action])
        self.assertIsNone(rows[-1]['adjusted_close'])

    def test_rights_without_theoretical_reference_is_unavailable(self):
        rows=normalise_prices(fixture('A',[100,80]),[dict(ex_date='2026-01-02',kind='RIGHTS')])
        self.assertIsNone(rows[-1]['adjusted_close'])

    def test_ordinary_dividend_not_reinvested_in_price_index(self):
        action=dict(symbol='A',ex_date='2026-01-02',kind='ORDINARY_CASH_DIVIDEND',amount_inr='10')
        rows,_,_,_=build_fixture({'A':fixture('A',[100,90]),'B':fixture('B',[100,100])},[action])
        self.assertEqual(rows[-1]['index_close'],950)

    def test_future_split_does_not_rewrite_earlier_prices(self):
        raw=fixture('A',[100,100,50]);action=dict(ex_date='2026-01-03',kind='SPLIT',new_shares_per_old_share='2',provider_basis='UNADJUSTED_CONFIRMED')
        self.assertEqual(normalise_prices(raw,[action])[:2],normalise_prices(raw[:2],[]))

    def test_missing_member_does_not_renormalize_or_bridge(self):
        raw={'A':fixture('A',[100,110,120]),'B':fixture('B',[100,110,120])}
        rows,features,cal,bm=build_fixture(raw)
        features['B'][1]['adjusted_close']=None
        rows=equal_weight_index(features,cal,bm,members=('A','B'))
        self.assertIsNone(rows[1]['index_close']);self.assertIsNone(rows[2]['index_close'])
        self.assertEqual(rows[1]['observed_constituents'],1)
        self.assertEqual(rows[1]['expected_constituents'],2)
        self.assertEqual(rows[1]['price_coverage_pct'],50)

    def test_one_peer_insufficient(self):
        raw={'A':fixture('A',[100,110])}
        rows,_,_,_=build_fixture(raw)
        self.assertEqual(rows,[])

    def test_rvol_uses_previous_twenty_excludes_current(self):
        raw=fixture('A',[100]*21,[100]*20+[400]);cal=[r['date'] for r in raw]
        rows=constituent_features(normalise_prices(raw),cal)
        self.assertIsNone(rows[19]['rvol20']);self.assertEqual(rows[20]['rvol20'],4)

    def test_corporate_action_on_missing_date_fails(self):
        raw=[fixture('A',[100,100,100])[i] for i in (0,2)]
        with self.assertRaises(ValueError):normalise_prices(raw,[dict(ex_date='2026-01-02',kind='SPLIT',new_shares_per_old_share='2')])

class HistoricalStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candles=read_prices();cls.stocks,cls.indices,cls.loo,cls.cal,cls.bm=calculate(cls.candles)
        cls.events=json.loads((INPUT/'EVENTS.json').read_text())

    def test_frozen_input_hashes_and_two_cutoffs(self):
        verify_inputs();self.assertEqual(set(CUTOFFS.values()),{'2026-03-31','2026-06-30'})

    def test_three_real_series_with_complete_fixed_counts(self):
        for rows in self.indices.values():
            self.assertEqual(len(rows),227);self.assertEqual(rows[0]['index_close'],1000)
            self.assertTrue(all(r['expected_constituents']==r['observed_constituents']==3 for r in rows))
            self.assertTrue(all(r['membership_status']=='RETROSPECTIVE_DESCRIPTIVE' for r in rows))
            self.assertTrue(all(not any(k in r for k in ('open','high','low','volume')) for r in rows))

    def test_grp_recycler_excluded_without_overwriting_tutor(self):
        self.assertNotIn('GRPLTD',MEMBERS['TYRES'])
        event=next(e for e in self.events if e['symbol']=='GRPLTD')
        f=snapshot(event,3,self.stocks,self.indices,self.loo,self.cal)
        self.assertEqual(f['status'],'UNSUPPORTED_BUSINESS_OR_OUTSIDE_THREE_GROUPS')
        self.assertEqual(event['subsector'],'TYRES')  # original tutor-era research fact retained

    def test_leave_one_out_excludes_target_for_all_dates(self):
        for (q,s),rows in self.loo.items():self.assertTrue(all(s not in r['members'].split(';') and r['expected_constituents']==2 for r in rows))

    def test_target_perturbation_cannot_improve_own_peer_benchmark(self):
        changed=copy.deepcopy(self.candles)
        for r in changed:
            if r['symbol']=='CEATLTD' and r['date']=='2026-06-01':r['raw_close']*=2
        _,_,peer,_,_=calculate(changed)
        for q in CUTOFFS:self.assertEqual(self.loo[q,'CEATLTD'],peer[q,'CEATLTD'])

    def test_study_checkpoint_strictly_after_results(self):
        for e in self.events:
            f=snapshot(e,1,self.stocks,self.indices,self.loo,self.cal)
            self.assertGreater(f['checkpoint_date'],e['reported_results_date'])

    def test_unknown_next_result_remains_censored(self):
        e=next(e for e in self.events if e['symbol']=='CEATLTD' and not e['next_results_date'])
        cp=snapshot(e,3,self.stocks,self.indices,self.loo,self.cal)
        r=outcome(e,cp,20,self.stocks,self.loo,self.cal,self.bm,{})
        self.assertTrue(r['horizon_complete']);self.assertTrue(r['censored'])

    def test_outcome_boundaries_exclude_next_result_day(self):
        e=next(e for e in self.events if e['symbol']=='CEATLTD' and e['next_results_date'])
        cp=snapshot(e,40,self.stocks,self.indices,self.loo,self.cal)
        r=outcome(e,cp,40,self.stocks,self.loo,self.cal,self.bm,{})
        self.assertLess(r['exit_date'],e['next_results_date']);self.assertFalse(r['horizon_complete'])
        self.assertIsNone(r['stock_close_return_pct']);self.assertIsNotNone(r['stock_partial_close_return_pct'])

    def test_exact_matched_close_returns_not_er04_open_returns(self):
        e=next(e for e in self.events if e['symbol']=='MRF');cp=snapshot(e,3,self.stocks,self.indices,self.loo,self.cal)
        r=outcome(e,cp,5,self.stocks,self.loo,self.cal,self.bm,{})
        i=self.cal.index(cp['checkpoint_date']);expected=100*(self.stocks['MRF'][i+5]['adjusted_close']/self.stocks['MRF'][i]['adjusted_close']-1)
        self.assertEqual(r['stock_close_return_pct'],expected)
        self.assertEqual(r['stock_minus_peer_pp'],r['stock_close_return_pct']-r['peer_close_return_pct'])

    def test_no_aggregate_raw_share_volume_measure(self):
        for r in self.indices['FY2026-Q4','TYRES']:
            self.assertNotIn('volume',r);self.assertIn('PROXY_NOT_EXACT_TURNOVER',r['traded_value_basis'])

    def test_fixed_session_twenty_is_not_twenty_after_session_three(self):
        e=next(e for e in self.events if e['symbol']=='PGEL' and e['quarter']=='FY2027-Q1')
        fs=[snapshot(e,n,self.stocks,self.indices,self.loo,self.cal) for n in (1,3,5,20,40)]
        rows,_=fixed_checkpoint_trajectories(fs)
        r=next(r for r in rows if r['early_checkpoint']==3 and r['later_event_session']==20)
        expected=100*(fs[3]['stock_close']/fs[1]['stock_close']-1)
        self.assertEqual(r['stock_return_early_to_late_pct'],expected)
        later=[d for d in self.cal if d>e['reported_results_date']]
        self.assertEqual(r['later_date'],later[19])

    def test_later_peer_state_is_outcome_only_not_early_group_input(self):
        e=next(e for e in self.events if e['symbol']=='PGEL' and e['quarter']=='FY2027-Q1')
        fs=[snapshot(e,n,self.stocks,self.indices,self.loo,self.cal) for n in (1,3,5,20,40)]
        _,before=fixed_checkpoint_trajectories(fs)
        changed=copy.deepcopy(fs)
        for f in changed:
            if f['checkpoint']>=20:f['peer_return_20s_pct']=D(999);f['peer_rs55']=D(-999)
        _,after=fixed_checkpoint_trajectories(changed)
        self.assertEqual(before,after)

if __name__=='__main__':unittest.main()
