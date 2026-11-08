"""Offline provider-fixture and deterministic rendering checks; no HTTP calls."""
from datetime import datetime, time, timedelta
from decimal import Decimal as D
from dataclasses import replace
import unittest
import xml.etree.ElementTree as ET

from experiments.earnings_reaction_v1.candles import IST
from experiments.earnings_reaction_v1.events import Session
from experiments.earnings_reaction_v1.compare_ta03r3 import load_retained, render_chart
from experiments.earnings_reaction_v1.indicator_alternatives import (
    TIMEFRAMES,aggregate,volume_features,session_profile,intra_rvol)
from experiments.earnings_reaction_v1.tests.test_indicator_alternatives import fixture


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.batches=load_retained()
        cls.minute=tuple(sorted(cls.batches[1].candles,key=lambda c:c.start_ist))
        cls.daily=tuple(sorted(cls.batches[0].candles,key=lambda c:c.start_ist))
        cls.cutoff=min(b.retrieved_at_utc for b in cls.batches)
        cls.sessions=tuple(Session(datetime.fromisoformat(day).date(),
            datetime.combine(datetime.fromisoformat(day).date(),time(9,15),IST),
            datetime.combine(datetime.fromisoformat(day).date(),time(15,30),IST))
            for day in ('2026-07-30','2026-07-31','2026-08-03'))

    def test_real_four_timeframe_counts_and_volume_conservation(self):
        for tf,count in (('Daily',3),('4-hour',6),('75-minute',15),('1-hour',21)):
            bars=aggregate(self.minute,self.sessions,tf,cutoff=self.cutoff)
            self.assertEqual(len(bars),count)
            self.assertEqual(sum(c.volume for c in bars),sum(c.volume for c in self.minute))
            self.assertEqual(bars[-1].end_ist,self.sessions[-1].closes_at)

    def test_profile_real_distribution_conserves_volume(self):
        for s in self.sessions:
            bars=tuple(c for c in self.minute if c.start_ist.date()==s.day)
            profile=session_profile(bars,s,cutoff=self.cutoff)
            self.assertLess(abs(sum(profile['total'])-sum(c.volume for c in bars)),D('1e-18'))
            self.assertEqual(profile['available_at'],s.closes_at)
            self.assertTrue(profile['val'] <= profile['poc'] <= profile['vah'])

    def test_real_rvol_has_a_no_b_warmup_is_not_zero(self):
        rows=intra_rvol(self.minute,self.sessions,self.daily,cutoff=self.cutoff)
        self.assertEqual(sum(r['candidate_a'] is not None for r in rows),225)
        self.assertTrue(all(r['candidate_b'] is None for r in rows))

    def test_real_intraday_prefix_and_gap_failure(self):
        full=aggregate(self.minute,self.sessions,'75-minute',cutoff=self.cutoff)
        prefix=aggregate(self.minute[:15],self.sessions[:1],'75-minute',cutoff=self.minute[14].end_ist)
        self.assertEqual(prefix,(full[0],))
        with self.assertRaises(ValueError):
            aggregate(self.minute[:30]+self.minute[31:],self.sessions,'75-minute',cutoff=self.cutoff)

    def test_chart_features_are_repeatable(self):
        for tf in TIMEFRAMES:
            bars=self.daily if tf=='Daily' else aggregate(self.minute,self.sessions,tf,cutoff=self.cutoff)
            a=volume_features(bars,cutoff=self.cutoff,daily=tf=='Daily')
            b=volume_features(bars,cutoff=self.cutoff,daily=tf=='Daily')
            self.assertEqual(a,b)
            chart=render_chart(tf,bars,a,{},[],'unavailable')
            self.assertEqual(chart,render_chart(tf,bars,b,{},[],'unavailable'))
            self.assertIn('REFERENCE_MISSING',chart)
            self.assertIn('NIFTYMIDSML400',chart)
            self.assertNotIn('https://',chart)
            ET.fromstring(chart[chart.index('<svg'):])

    def test_daily_records_have_full_windows_not_lifetime_claim(self):
        sessions,bars=fixture(1)
        base=bars[0]
        midnight=base.start_ist.replace(hour=0,minute=0)
        daily=tuple(replace(base,start_ist=midnight+timedelta(days=i),
            end_ist=midnight+timedelta(days=i+1),completed_at=midnight+timedelta(days=i+1),
            volume=D(i+1)) for i in range(254))
        rows=volume_features(daily,cutoff=daily[-1].completed_at,daily=True)
        self.assertIsNone(rows[62]['record_high_63d'])
        self.assertTrue(rows[63]['record_high_63d'])
        self.assertIsNone(rows[251]['record_high_252d'])
        self.assertTrue(rows[252]['record_high_252d'])
        self.assertIsNone(rows[-1]['original_hve'])
        equal=daily[:-1]+(replace(daily[-1],volume=daily[-2].volume),)
        self.assertFalse(volume_features(equal,cutoff=equal[-1].completed_at,daily=True)[-1]['record_high_252d'])

    def test_missing_numeric_negative_and_bad_width_fail_closed(self):
        sessions,bars=fixture(1)
        for bad in (D('-1'),D('NaN')):
            with self.assertRaises(ValueError):
                volume_features((replace(bars[0],volume=bad),),cutoff=sessions[-1].closes_at)
        for width in (D(0),D('-1'),D('NaN')):
            with self.assertRaises(ValueError):
                session_profile(bars,sessions[0],cutoff=sessions[0].closes_at,bin_width=width)


if __name__=='__main__':
    unittest.main()
