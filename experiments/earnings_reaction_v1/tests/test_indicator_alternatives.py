"""Hand-computed synthetic mathematics, separate from real-data comparisons."""
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal as D

from experiments.earnings_reaction_v1.candles import Candle, IST
from experiments.earnings_reaction_v1.events import Session
from experiments.earnings_reaction_v1.indicator_alternatives import (
    aggregate, intra_rvol, volume_features, volume_color, value_area,
    session_profile, latest_markers, session_bars)
from experiments.earnings_reaction_v1.indicators import pine_average


def fixture(days=3, minutes=10):
    sessions, bars = [], []
    for i in range(days):
        start = datetime(2026, 7, 1, 9, 15, tzinfo=IST)+timedelta(days=i)
        s = Session(start.date(),start,start+timedelta(minutes=minutes))
        sessions.append(s)
        t = start
        while t < s.closes_at:
            end = min(t+timedelta(minutes=5), s.closes_at)
            bars.append(Candle('NSE_EQ|INE716B01029',t.isoformat(),t,end,
                D(10),D(12),D(10),D(11),D(10),D(0),'test',end,f'NSE:{s.day}'))
            t = end
    return tuple(sessions),tuple(bars)


class AlternativeTests(unittest.TestCase):
    def test_four_boundaries_and_partial_close(self):
        sessions,bars = fixture(1,375)
        cutoff=sessions[-1].closes_at
        expected={'Daily':(1,375),'4-hour':(2,135),'75-minute':(5,75),'1-hour':(7,15)}
        for tf,(count,last) in expected.items():
            result=aggregate(bars,sessions,tf,cutoff=cutoff)
            self.assertEqual(len(result),count)
            self.assertEqual(result[0].start_ist,sessions[0].opens_at)
            self.assertEqual(result[-1].end_ist,cutoff)
            self.assertEqual(result[-1].end_ist-result[-1].start_ist,timedelta(minutes=last))
            self.assertEqual(sum(c.volume for c in result),D(750))
        self.assertEqual(aggregate(bars,sessions,'4-hour',cutoff=cutoff)[1].start_ist.hour,13)

    def test_developing_not_completed(self):
        sessions,bars=fixture(1,375)
        cutoff=bars[2].end_ist
        result=aggregate(bars[:3],sessions,'1-hour',cutoff=cutoff)
        self.assertEqual(result[0].volume,D(30))
        self.assertFalse(result[0].completed_by(cutoff))
        with self.assertRaises(ValueError):
            volume_features(result,cutoff=cutoff)

    def test_gap_missing_whole_session_and_duplicates(self):
        sessions,bars=fixture()
        for bad in (bars[1:],bars[:2]+bars[4:],bars+(bars[0],)):
            with self.assertRaises(ValueError):
                aggregate(bad,sessions,'1-hour',cutoff=sessions[-1].closes_at)

    def test_future_and_unsupported(self):
        sessions,bars=fixture()
        for tf in ('Weekly','90m','5-minute'):
            with self.assertRaises(ValueError):
                aggregate(bars,sessions,tf,cutoff=sessions[-1].closes_at)
        with self.assertRaises(ValueError):
            aggregate(bars,sessions,'1-hour',cutoff=bars[0].end_ist)

    def test_special_session_explicit_and_partial_base(self):
        sessions,bars=fixture(1,12)
        s=replace(sessions[0],day=date(2026,7,5),opens_at=sessions[0].opens_at+timedelta(days=4),
                  closes_at=sessions[0].closes_at+timedelta(days=4))
        shifted=tuple(replace(c,start_ist=c.start_ist+timedelta(days=4),end_ist=c.end_ist+timedelta(days=4),
                              completed_at=c.completed_at+timedelta(days=4)) for c in bars)
        result=aggregate(shifted,(s,),'Daily',cutoff=s.closes_at)
        self.assertEqual(result[0].end_ist,s.closes_at)

    def test_same_time_cumulative_hand_values_reset(self):
        sessions,bars=fixture()
        bars=tuple(replace(c,volume=D(v)) for c,v in zip(bars,[10,30,20,40,60,30]))
        result=intra_rvol(bars,sessions,(),cutoff=sessions[-1].closes_at,lookback=2)
        self.assertIsNone(result[3]['candidate_b'])
        self.assertEqual(result[4]['cumulative_volume'],D(60))
        self.assertEqual(result[4]['candidate_b'],D(4)) # 60 / mean(10,20)
        self.assertEqual(result[5]['candidate_b'],D('1.8')) # 90 / mean(40,60)

    def test_daily_candidate_and_no_final_day_leakage(self):
        sessions,bars=fixture()
        daily=[]
        for s,v in zip(sessions,[40,60,9999]):
            midnight=s.opens_at.replace(hour=0,minute=0)
            daily.append(replace(bars[0],start_ist=midnight,end_ist=midnight+timedelta(days=1),
                completed_at=s.closes_at,volume=D(v)))
        result=intra_rvol(bars,sessions,daily,cutoff=sessions[-1].closes_at,lookback=2)
        self.assertEqual(result[4]['candidate_a'],D('.2'))
        self.assertEqual(result[5]['candidate_a'],D('.4'))

    def test_prefix_rvol_invariance_and_twenty_warmup(self):
        sessions,bars=fixture(21)
        full=intra_rvol(bars,sessions,(),cutoff=sessions[-1].closes_at)
        self.assertIsNone(full[39]['candidate_b'])
        self.assertEqual(full[40]['candidate_b'],D(1))
        prefix=intra_rvol(bars[:-1],sessions,(),cutoff=bars[-2].end_ist)
        self.assertEqual(prefix,full[:-1])

    def test_zero_baseline_and_shorter_session_no_carry(self):
        sessions,bars=fixture()
        zero=tuple(replace(c,volume=D(0)) for c in bars)
        self.assertIsNone(intra_rvol(zero,sessions,(),cutoff=sessions[-1].closes_at,lookback=2)[-1]['candidate_b'])
        shortened=replace(sessions[0],closes_at=bars[0].end_ist)
        result=intra_rvol(bars[:1]+bars[2:],(shortened,)+sessions[1:],(),cutoff=sessions[-1].closes_at,lookback=2)
        self.assertIsNone(result[-1]['candidate_b'])

    def test_volume_hand_pivot_rvol_ud_turnover(self):
        sessions,bars=fixture(3)
        closes=[11,10,11,10,11,12]
        vols=[2,7,3,5,8,1]
        bars=tuple(replace(c,close=D(p),volume=D(v)) for c,p,v in zip(bars,closes,vols))
        result=volume_features(bars,cutoff=sessions[-1].closes_at,average=2,relative=2,pocket=2,up_down=2,turnover=2)
        r=result[4]
        self.assertTrue(r['pocket_pivot']) # 8 > previous down bar's 5
        self.assertEqual(r['rvol_ratio'],D(2)) # 8 / ((3+5)/2)
        self.assertEqual(r['volume_sma'],D('6.5'))
        self.assertEqual(r['up_down_ratio'],D('1.6'))
        self.assertEqual(r['turnover_sma_product_inr'],D('68.25')) # 10.5*6.5
        self.assertFalse(result[5]['dry']) # 1 >= 4.5/5
        drybars=bars[:-1]+(replace(bars[-1],volume=D('.1')),)
        self.assertTrue(volume_features(drybars,cutoff=sessions[-1].closes_at,average=2)[-1]['dry'])

    def test_no_down_day_no_vacuous_pivot(self):
        sessions,bars=fixture(3)
        bars=tuple(replace(c,high=D(30),close=D(11+i)) for i,c in enumerate(bars))
        result=volume_features(bars,cutoff=sessions[-1].closes_at,pocket=2)
        self.assertFalse(result[-1]['pocket_pivot'])

    def test_precedence_overlapping_events(self):
        flags=dict(pocket_pivot=True,dry=True,high_up=True,record_high_63d=True)
        self.assertEqual(volume_color(flags,1),'gold')
        self.assertEqual(volume_color(flags,-1),'dodgerblue')
        self.assertTrue(flags['dry'])
        self.assertEqual(volume_color({'dry':True,'high_down':True},-1),'orange')

    def test_event_prefix_latest_marker_not_backfilled(self):
        sessions,bars=fixture(3)
        rows=volume_features(bars,cutoff=sessions[-1].closes_at)
        prefix=volume_features(bars[:-1],cutoff=bars[-2].end_ist)
        self.assertEqual(rows[:-1],prefix)
        custom=[dict(observation_at=c.end_ist,record_high_63d=True) for c in bars]
        self.assertEqual(latest_markers(custom,cutoff=bars[1].end_ist)['record_high_63d'],1)

    def test_value_area_hand_no_overshoot_and_ties(self):
        # POC 40; remaining 30. Select upper20 then stop before lower20.
        self.assertEqual(value_area(tuple(map(D,[10,20,40,20,10]))),(2,2,3,D('.6')))
        self.assertEqual(value_area(tuple(map(D,[30,40,30]))),(1,1,2,D('.7')))
        self.assertEqual(value_area((D(50),D(50)))[0],0)
        self.assertEqual(value_area((D(0),D(0))),None)

    def test_profile_allocation_edges_and_up_down(self):
        sessions,bars=fixture(1)
        bars=(replace(bars[0],volume=D(100)),replace(bars[1],open=D(12),close=D(10),volume=D(40)))
        result=session_profile(bars,sessions[0],cutoff=sessions[0].closes_at)
        self.assertEqual(result['total'],(D(70),D(70),D(0)))
        self.assertEqual(result['up'],[D(50),D(50),D(0)])
        self.assertEqual(result['down'],[D(20),D(20),D(0)])
        self.assertEqual(result['poc'],D('10.5'))
        self.assertEqual((result['val'],result['vah']),(D(10),D(11)))
        self.assertEqual(sum(result['total']),D(140))

    def test_profile_zero_range_zero_volume_and_cutoff(self):
        sessions,bars=fixture(1)
        flat=tuple(replace(c,open=D(10),high=D(10),low=D(10),close=D(10)) for c in bars)
        result=session_profile(flat,sessions[0],cutoff=sessions[0].closes_at)
        self.assertEqual(result['total'],(D(20),))
        with self.assertRaises(ValueError):
            session_profile(flat,sessions[0],cutoff=flat[0].end_ist)
        with self.assertRaises(ValueError):
            session_profile(flat[1:],sessions[0],cutoff=sessions[0].closes_at)
        zero=tuple(replace(c,volume=D(0)) for c in flat)
        self.assertIsNone(session_profile(zero,sessions[0],cutoff=sessions[0].closes_at)['vah'])

    def test_ema_reuses_pine_seed(self):
        self.assertEqual(pine_average(tuple(map(D,[10,13,16])),2,'EMA'),(D(10),D(12),D(44)/3))


if __name__ == '__main__':
    unittest.main()
