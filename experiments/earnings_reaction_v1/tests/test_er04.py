import unittest
from decimal import Decimal as D
from types import SimpleNamespace
from experiments.earnings_reaction_v1.er04 import forward, events, comparisons, HYPOTHESES
from experiments.earnings_reaction_v1.er03_chart_upgrade import load_inputs, CUTOFF
from experiments.earnings_reaction_v1.simple_volume import simple_volume_features
from experiments.earnings_reaction_v1.er03 import BENCH

class ER04Tests(unittest.TestCase):
    def setUp(self):
        self.event=dict(event_id='Q:S',symbol='S',next_results_date='2026-01-05')
        self.rows=[dict(date=f'2026-01-0{i}',open=D(100+i),close=D(102+i),high=D(105+i),low=D(98+i)) for i in range(1,7)]
        self.bm={r['date']:SimpleNamespace(open=D(100),close=D(101)) for r in self.rows}

    def test_unique_events_not_horizon_rows(self):
        es=events()
        self.assertEqual(len(es),24)
        self.assertEqual(len({e['event_id'] for e in es}),24)

    def test_entry_next_open_and_exact_nth_close(self):
        r=forward(self.event,self.rows,0,self.bm,2)
        self.assertEqual(r['entry_date'],'2026-01-02')
        self.assertEqual(r['entry_open'],D(102))
        self.assertEqual(r['exit_date'],'2026-01-03')
        self.assertEqual(r['return_pct'],100*(D(105)/102-1))
        self.assertEqual(r['relative_return_pp'],r['return_pct']-1)

    def test_known_next_result_date_excluded(self):
        r=forward(self.event,self.rows,0,self.bm,5)
        self.assertEqual(r['observed_sessions'],3)
        self.assertEqual(r['exit_date'],'2026-01-04')
        self.assertFalse(r['horizon_complete'])
        self.assertIsNone(r['return_pct'])
        self.assertIsNotNone(r['partial_return_pct'])

    def test_unknown_boundary_censored_even_when_full(self):
        self.event['next_results_date']=''
        r=forward(self.event,self.rows,0,self.bm,2)
        self.assertTrue(r['horizon_complete'])
        self.assertTrue(r['censored'])
        self.assertIn('UNKNOWN',r['censor_reason'])

    def test_benchmark_gap_never_carried(self):
        del self.bm['2026-01-03']
        r=forward(self.event,self.rows,0,self.bm,3)
        self.assertIsNone(r['relative_return_pp'])
        self.assertEqual(r['benchmark_status'],'MISSING_EXACT_BENCHMARK_SESSION')

    def test_empty_forward_window(self):
        r=forward(self.event,self.rows,5,self.bm,5)
        self.assertEqual(r['observed_sessions'],0)
        self.assertIsNone(r['mfe_pct'])
        self.assertIsNone(r['return_pct'])

    def test_excursions_use_actual_high_low(self):
        r=forward(self.event,self.rows,0,self.bm,2)
        self.assertEqual(r['mfe_pct'],100*(D(108)/102-1))
        self.assertEqual(r['mae_pct'],100*(D(100)/102-1))

    def test_final_color_needs_252_inclusive_not_252_prior(self):
        data,_=load_inputs()
        bars=next(v for k,v in data.items() if k!=BENCH and len(v)>252)
        a=simple_volume_features(bars[:251],cutoff=CUTOFF,daily=True)
        b=simple_volume_features(bars[:252],cutoff=CUTOFF,daily=True)
        self.assertTrue(all(r['original_final_color'] is None for r in a))
        self.assertIsNotNone(b[-1]['original_final_color'])
        self.assertIsNotNone(a[10]['pocket_pivot'])
        self.assertIsNone(a[9]['pocket_pivot'])

if __name__=='__main__':unittest.main()
