"""Hand arithmetic, causality and original Simple Volume reference checks."""
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
import runpy
import sys
import unittest

from experiments.earnings_reaction_v1.simple_volume import simple_volume_features, simple_volume_labels, aggregate_weekly_daily
from experiments.earnings_reaction_v1.events import Session
from experiments.earnings_reaction_v1.tests.test_indicator_alternatives import fixture

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT/'artifacts/earnings-reaction-v1/simple-volume-reconstruction'

class SimpleVolumeChecks(unittest.TestCase):
    def test_weekly_uses_weekly_rvol_and_suppresses_daily_records(self):
        _,fixture_bars=fixture()
        template=fixture_bars[0]
        start=template.start_ist.replace(day=6,hour=0,minute=0)
        bars=tuple(replace(template,start_ist=start+timedelta(weeks=i),
            end_ist=start+timedelta(weeks=i,days=4,hours=15,minutes=30),
            completed_at=start+timedelta(weeks=i,days=4,hours=15,minutes=30),volume=D(v))
            for i,v in enumerate([100,200,300,400]))
        rows=simple_volume_features(bars,cutoff=bars[-1].completed_at,weekly=True,relative=2,average=2,pocket=2)
        self.assertEqual(rows[-1]['rvol_pct'],D(160))
        self.assertEqual(rows[-1]['volume_sma'],D(350))
        self.assertTrue(all(r['original_hve'] is False and r['original_hvy'] is False and r['original_hvq'] is False for r in rows))
        self.assertEqual(simple_volume_labels(rows,cutoff=bars[-1].completed_at),dict(high=[],low=[]))
        with self.assertRaises(ValueError):
            simple_volume_features(bars,cutoff=bars[-1].completed_at,weekly=True,daily=True)

    def test_weekly_aggregation_partial_edges_and_missing_session(self):
        _,fixture_bars=fixture()
        template=fixture_bars[0];monday=template.start_ist.replace(day=6,hour=0,minute=0)
        sessions=[];bars=[]
        for i in [-4,-3,0,1,2,3,4]:
            start=monday+timedelta(days=i);end=start+timedelta(hours=15,minutes=30)
            bars.append(replace(template,start_ist=start,end_ist=start+timedelta(days=1),
                completed_at=end,volume=D((i+1)*10 if i>=0 else 5)))
            if i>=0:sessions.append(Session(start.date(),start+timedelta(hours=9,minutes=15),end))
        cutoff=bars[-1].completed_at
        weekly=aggregate_weekly_daily(bars,cutoff=cutoff,sessions=sessions)
        self.assertEqual(len(weekly),1)
        self.assertEqual(weekly[0].start_ist,monday)
        self.assertEqual(weekly[0].volume,D(150))
        self.assertEqual(weekly[0].end_ist,cutoff)
        self.assertEqual(aggregate_weekly_daily(bars,cutoff=cutoff),())
        self.assertEqual(aggregate_weekly_daily(bars,cutoff=monday+timedelta(days=7)),weekly)
        with self.assertRaises(ValueError):
            aggregate_weekly_daily(bars[:4]+bars[5:],cutoff=cutoff,sessions=sessions)

    def test_flat_close_volume_is_in_down_sum(self):
        sessions,bars=fixture()
        closes=[10,11,11,10,12,12]
        bars=tuple(replace(c,close=D(price),volume=D(v)) for c,price,v in zip(bars,closes,[10,20,30,40,50,60]))
        rows=simple_volume_features(bars,cutoff=sessions[-1].closes_at,up_down=3)
        self.assertEqual(rows[-1]['up_down_ratio'],D(50)/100)
        self.assertEqual(rows[-1]['up_down_table_value'],D('.5'))

    def test_exact_half_point_display_is_invariant_under_uniform_volume_scaling(self):
        sessions,bars = fixture(11)
        bars=bars[:21]
        for volumes,expected in [([D(5)]*19+[D(18),D(7)],D(23)),
                                  ([D(5)]*19+[D(20),D(5)],D(-12))]:
            for factor in (D(1),D('1.001'),D('1.005'),D('1.01')):
                scaled=tuple(replace(c,volume=v*factor) for c,v in zip(bars,volumes))
                rows=simple_volume_features(scaled,cutoff=sessions[-1].closes_at)
                with self.subTest(expected=expected,factor=factor):
                    self.assertEqual(rows[-1]['highest_volume_change_pct'],expected)

    def test_no_down_window_and_ud_direction_is_independent_of_colors(self):
        sessions,bars = fixture()
        # Every candle is up vs open, but closes alternate vs previous close.
        closes = [12,11,13,12,14,13]
        bars = tuple(replace(c,open=D(10),close=D(price),high=D(20),volume=D(v))
                     for c,price,v in zip(bars,closes,[10,20,30,40,50,60]))
        rows = simple_volume_features(bars,cutoff=sessions[-1].closes_at,pocket=2,up_down=3,average=2)
        self.assertTrue(rows[-1]['pocket_pivot'])
        self.assertEqual(rows[-1]['up_down_ratio'],D(50)/100)
        self.assertEqual(rows[-1]['up_down_table_value'],D('.5'))
        self.assertEqual(rows[-1]['original_final_color'],'#2196F3')

    def test_record_history_provenance_and_latest_labels_are_causal(self):
        _,fixture_bars = fixture()
        template = fixture_bars[0]
        start = template.start_ist.replace(hour=0,minute=0)
        bars = tuple(replace(template,start_ist=start+timedelta(days=i),
            end_ist=start+timedelta(days=i+1),completed_at=start+timedelta(days=i+1),
            open=D(10),close=D(11),volume=D(10 if i<252 else 20 if i==252 else 5))
            for i in range(254))
        rows = simple_volume_features(bars,cutoff=bars[-1].completed_at,daily=True,
                                      record_history_complete=True)
        self.assertTrue(rows[252]['original_hve'])
        self.assertFalse(rows[252]['original_hvy'])
        self.assertFalse(rows[252]['original_hvq'])
        self.assertTrue(rows[-1]['original_lvy'])
        self.assertEqual(rows[-1]['lowest_volume_kind'],'Y')
        self.assertEqual(rows[-1]['original_final_color'],'#2196F3') # PP wins over low orange.
        before = simple_volume_labels(rows,cutoff=bars[251].completed_at)
        after = simple_volume_labels(rows,cutoff=bars[252].completed_at)
        self.assertEqual(before['high'][-1]['observation_at'],bars[251].completed_at)
        self.assertEqual(after['high'][-1]['observation_at'],bars[252].completed_at)
        prefix = simple_volume_features(bars[:252],cutoff=bars[251].completed_at,
                                        daily=True,record_history_complete=True)
        self.assertEqual(rows[:252],prefix)
        unknown = simple_volume_features(bars,cutoff=bars[-1].completed_at,daily=True)
        self.assertIsNone(unknown[252]['original_hve'])
        # Equal default record colors make final color identifiable without an
        # all-history label classification after a complete rolling window.
        self.assertEqual(unknown[252]['original_final_color'],'#2196F3')
        seeded = simple_volume_features(bars[252:],cutoff=bars[-1].completed_at,daily=True,
            record_history=bars[:252],record_history_complete=True)
        self.assertTrue(seeded[0]['original_hve'])
        with self.assertRaises(ValueError):
            simple_volume_features(bars[252:],cutoff=bars[-1].completed_at,daily=True,
                                   record_history=bars[:253],record_history_complete=True)

    def test_sma_dry_and_doji_pocket_pivot(self):
        sessions,bars = fixture()
        bars = tuple(replace(c,open=D(11),close=D(10),volume=D(v))
                     for c,v in zip(bars,[10,20,30,40,50,100]))
        bars = bars[:-1]+(replace(bars[-1],open=D(10),close=D(10)),)
        rows = simple_volume_features(bars,cutoff=sessions[-1].closes_at,average=2,pocket=2)
        self.assertTrue(rows[-1]['pocket_pivot'])
        self.assertEqual(rows[-1]['volume_sma'],D(75))
        self.assertFalse(rows[-1]['dry'])
        self.assertIsNone(rows[-1]['rvol_pct']) # No daily source supplied.
        equal = bars[:-1]+(replace(bars[-1],volume=D(50)),)
        self.assertFalse(simple_volume_features(equal,cutoff=sessions[-1].closes_at,pocket=2)[-1]['pocket_pivot'])

    def test_daily_rvol_uses_confirmed_source_without_future_leak(self):
        sessions,bars = fixture()
        midnight = sessions[0].opens_at.replace(hour=0,minute=0)
        history = []
        for i,v in enumerate([100,200,300]):
            start = midnight+timedelta(days=i-2)
            history.append(replace(bars[0],start_ist=start,end_ist=start+timedelta(days=1),
                completed_at=start.replace(hour=9,minute=25),volume=D(v)))
        rows = simple_volume_features(bars,cutoff=sessions[-1].closes_at,
                                     relative=1,daily_history=history)
        self.assertEqual(rows[0]['rvol_pct'],D(200))
        self.assertEqual(rows[1]['rvol_pct'],D(150))
        self.assertEqual(rows[1]['rvol_pct_unrounded'],D(150))
        # Current session's final daily volume affects only its close and later.
        changed = history[:-1]+[replace(history[-1],volume=D(600))]
        altered = simple_volume_features(bars,cutoff=sessions[-1].closes_at,
                                        relative=1,daily_history=changed)
        self.assertEqual(altered[0]['rvol_pct'],rows[0]['rvol_pct'])
        self.assertEqual(altered[1]['rvol_pct'],D(300))

    def test_prefix_and_invalid_data(self):
        sessions,bars = fixture()
        whole = simple_volume_features(bars,cutoff=sessions[-1].closes_at)
        prefix = simple_volume_features(bars[:-1],cutoff=bars[-2].end_ist)
        self.assertEqual(prefix,whole[:-1])
        self.assertIsNone(whole[-1]['original_final_color'])
        for bad in (-1,D('NaN')):
            with self.assertRaises(ValueError):
                simple_volume_features((replace(bars[0],volume=D(bad)),),cutoff=sessions[-1].closes_at)

    def test_original_reference_core(self):
        module = runpy.run_path(str(ART/'apply_to_upstox.py'))
        for directory,n in [('daily-default-colors',1440),('hourly-default-colors',60),
                            ('four-hour-default-colors',240),('one-minute-direct-verification',1),
                            ('weekly-direct-verification',10080),('one-minute-icici-holdout',1),
                            ('weekly-icici-holdout',10080),('one-minute-hdfc-holdout',1),
                            ('one-minute-sbin-final-holdout',1)]:
            summary,_ = module['validate'](directory,'TEST',n)
            for field,result in summary.items():
                with self.subTest(directory=directory,field=field):
                    self.assertGreater(result['compared'],0)
                    self.assertEqual(result['failures'],0)

    def test_original_drawings_colors_and_ud(self):
        sys.path.insert(0,str(ART))
        try:
            module = runpy.run_path(str(ART/'validate_details.py'))
            for directory,n,complete in [('daily-listing-history',1440,True),
                    ('four-hour-labels-table',240,False),('four-hour-ud3-check',240,False),
                    ('daily-hdfcbank-holdout',1440,True),('daily-icicibank-holdout',1440,True),
                    ('one-minute-direct-verification',1,False),('weekly-direct-verification',10080,False),
                    ('one-minute-icici-holdout',1,False),('weekly-icici-holdout',10080,False),
                    ('one-minute-hdfc-holdout',1,False),('one-minute-sbin-final-holdout',1,False)]:
                comparisons = module['validate_one'](directory,n,'TEST',complete)
                with self.subTest(directory=directory):
                    self.assertGreater(len(comparisons),0)
                    self.assertTrue(all(r['matched'] for r in comparisons))
        finally:
            sys.path.remove(str(ART))

if __name__ == '__main__': unittest.main()
