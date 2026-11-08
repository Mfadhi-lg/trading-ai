"""DSS mature-state hand arithmetic and independent live-reference validation."""
import csv
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import unittest

from experiments.earnings_reaction_v1.candles import Candle
from experiments.earnings_reaction_v1.events import Session
from experiments.earnings_reaction_v1.indicator_alternatives import dss_rvol_reconstruction
from experiments.earnings_reaction_v1.indicators import daily_rvol
from experiments.earnings_reaction_v1.tests.test_indicator_alternatives import fixture

ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'artifacts/earnings-reaction-v1/indicator-math-validation/live-dss-attempt'

class DSSHandChecks(unittest.TestCase):
    def setUp(self):
        self.sessions,bars=fixture()
        self.bars=tuple(replace(c,volume=D(v)) for c,v in zip(bars,[10,20,30,40,50,60]))

    def test_first_bar_twice_in_both_numerator_and_history(self):
        rows=dss_rvol_reconstruction(self.bars,self.sessions,cutoff=self.sessions[-1].closes_at,
                                     lookback=2,bar_minutes=5)
        self.assertTrue(all(r['dss_pct'] is None for r in rows[:4]))
        # Previous totals: (10+20)+10=40; (30+40)+30=100. Mean=70.
        self.assertEqual(rows[4]['dss_baseline'],D(70))
        self.assertEqual(rows[4]['dss_numerator'],D(100))
        self.assertEqual(rows[5]['dss_numerator'],D(160))
        self.assertEqual(rows[4]['dss_ratio'],D(100)/70)
        self.assertEqual(rows[5]['dss_ratio'],D(160)/70)

    def test_prefix_invariance_and_no_future_final_denominator(self):
        cutoff=self.bars[-2].end_ist
        partial=dss_rvol_reconstruction(self.bars[:-1],self.sessions,cutoff=cutoff,lookback=2,bar_minutes=5)
        full=dss_rvol_reconstruction(self.bars,self.sessions,cutoff=self.sessions[-1].closes_at,lookback=2,bar_minutes=5)
        self.assertEqual(partial,full[:-1])

    def test_zero_history_is_unavailable_and_gaps_rejected(self):
        bars=tuple(replace(c,volume=D(0)) for c in self.bars)
        rows=dss_rvol_reconstruction(bars,self.sessions,cutoff=self.sessions[-1].closes_at,lookback=2,bar_minutes=5)
        self.assertIsNone(rows[-1]['dss_pct'])
        with self.assertRaises(ValueError):
            dss_rvol_reconstruction(self.bars[1:],self.sessions,cutoff=self.sessions[-1].closes_at,lookback=2,bar_minutes=5)

    def test_cumulative_reset_uses_current_first_bar_only(self):
        rows=dss_rvol_reconstruction(self.bars,self.sessions,cutoff=self.sessions[-1].closes_at,lookback=1,bar_minutes=5)
        self.assertEqual(rows[2]['dss_numerator'],D(60))
        self.assertEqual(rows[2]['first_chart_bar_volume'],D(30))


@unittest.skipUnless((ART/'DSS_FOUR_HOUR_HOLDOUT.csv').is_file(),'live owner-authorized reference not retained')
class DSSRealReferenceChecks(unittest.TestCase):
    def test_existing_engine_against_untuned_four_hour_holdout(self):
        schedule=ROOT/'artifacts/earnings-reaction-v1/ta03r4/session-schedule-proposal.json'
        sessions=tuple(Session(date.fromisoformat(s['day']),datetime.fromisoformat(s['opens_at']),
                               datetime.fromisoformat(s['closes_at'])) for s in json.loads(schedule.read_text())['sessions'])
        dates={s.day:s for s in sessions}
        candles=[];reference={}
        with (ART/'DSS_FOUR_HOUR_HOLDOUT.csv').open() as stream:
            for row in csv.DictReader(stream):
                t=datetime.fromisoformat(row['bar_start_ist'])
                if t.date() not in dates or not row['volume']:continue
                end=min(t+timedelta(minutes=240),dates[t.date()].closes_at)
                candles.append(Candle('NSE_EQ|INE716B01029',str(t),t,end,*[D(row[k]) for k in ('open','high','low','close','volume')],
                                      D(0),'LIVE_TV_OWNER_AUTHORIZED',end,f'NSE:{t.date()}'))
                reference[end]=D(row['dss_relative_volume'])
        result=dss_rvol_reconstruction(candles,sessions,cutoff=sessions[-1].closes_at,bar_minutes=240)
        eligible=[r for r in result if r['dss_pct'] is not None]
        self.assertEqual(len(eligible),102)
        for r in eligible:
            self.assertLess(abs(r['dss_pct']-reference[r['observation_at']]),D('1e-8'))

@unittest.skipUnless((ART/'timeframe-validation/daily_L20/live-values.csv').is_file(),
                     'additional owner-authorized reference not retained')
class DSSAdditionalReferenceChecks(unittest.TestCase):
    def test_daily_cancellation_against_original_daily_plot(self):
        with (ART/'timeframe-validation/daily_L20/live-values.csv').open() as f:
            rows=[r for r in csv.DictReader(f) if r['volume']]
        expected=daily_rvol(tuple(D(r['volume']) for r in rows),20)
        compared=0
        for row,value in zip(rows,expected):
            if value is not None:
                self.assertLess(abs(100*value-D(row['dss_relative_volume'])),D('1e-8'))
                compared+=1
        self.assertEqual(compared,480)

    def test_hourly_lookbacks_1_20_21_on_known_regular_sessions(self):
        schedule=ROOT/'artifacts/earnings-reaction-v1/ta03r4/session-schedule-proposal.json'
        sessions=tuple(Session(date.fromisoformat(s['day']),datetime.fromisoformat(s['opens_at']),
                               datetime.fromisoformat(s['closes_at'])) for s in json.loads(schedule.read_text())['sessions'])
        dates={s.day:s for s in sessions}
        for n in (1,20,21):
            with self.subTest(lookback=n):
                candles=[];reference={}
                with (ART/f'timeframe-validation/hourly_L{n}/live-values.csv').open() as f:
                    for r in csv.DictReader(f):
                        t=datetime.fromisoformat(r['bar_start_ist'])
                        if t.date() not in dates or not r['volume']:continue
                        end=min(t+timedelta(minutes=60),dates[t.date()].closes_at)
                        candles.append(Candle('NSE_EQ|INE716B01029',str(t),t,end,
                            *[D(r[k]) for k in ('open','high','low','close','volume')],D(0),
                            'LIVE_TV_OWNER_AUTHORIZED',end,f'NSE:{t.date()}'))
                        reference[end]=D(r['dss_relative_volume'])
                result=dss_rvol_reconstruction(candles,sessions,cutoff=sessions[-1].closes_at,
                                               bar_minutes=60,lookback=n)
                eligible=[r for r in result if r['dss_pct'] is not None]
                self.assertEqual(len(eligible),(71-n)*7)
                for r in eligible:
                    self.assertLess(abs(r['dss_pct']-reference[r['observation_at']]),D('1e-8'))

    def test_partial_first_hourly_session_is_rejected(self):
        path=ART/'timeframe-validation/hourly_L20/live-values.csv'
        with path.open() as f:rows=[r for r in csv.DictReader(f) if r['volume']]
        first=datetime.fromisoformat(rows[0]['bar_start_ist'])
        session=Session(first.date(),first.replace(hour=9,minute=15),first.replace(hour=15,minute=30))
        candles=[]
        for r in rows:
            t=datetime.fromisoformat(r['bar_start_ist'])
            if t.date()!=first.date():continue
            end=min(t+timedelta(minutes=60),session.closes_at)
            candles.append(Candle('NSE_EQ|INE716B01029',str(t),t,end,
                *[D(r[k]) for k in ('open','high','low','close','volume')],D(0),
                'LIVE_TV_OWNER_AUTHORIZED',end,f'NSE:{t.date()}'))
        with self.assertRaises(ValueError):
            dss_rvol_reconstruction(candles,(session,),cutoff=session.closes_at,bar_minutes=60)

    def test_short_price_request_does_not_define_study_reset(self):
        with (ART/'DSS_ONE_MINUTE_SELECTION.csv').open() as f:
            original={r['bar_start_ist']:r['dss_relative_volume'] for r in csv.DictReader(f)}
        with (ART/'timeframe-validation/minute_short_L20/live-values.csv').open() as f:
            short=list(csv.DictReader(f))
        compared=0
        for r in short:
            if r['dss_relative_volume'] and original.get(r['bar_start_ist']):
                self.assertEqual(D(r['dss_relative_volume']),D(original[r['bar_start_ist']]))
                compared+=1
        self.assertEqual(compared,1100)

if __name__=='__main__':unittest.main()
