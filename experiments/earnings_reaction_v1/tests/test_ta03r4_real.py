"""Offline real-provider checks; no credential reads or HTTP calls.

Requires the nine retained responses. Review ZIP intentionally omits those raw
archives; tests skip explicitly if the approved retrieval ledger is absent.
"""
import csv
from datetime import date,datetime
from decimal import Decimal as D
from fractions import Fraction
import json
import unittest

from experiments.earnings_reaction_v1.compare_ta03r4 import load_provider,OUT,TIPSMUSIC,BENCHMARK
from experiments.earnings_reaction_v1.events import Session
from experiments.earnings_reaction_v1.indicator_alternatives import session_bars,aggregate,intra_rvol


@unittest.skipUnless((OUT/'retrieval-execution.json').is_file(),'approved raw-provider fixture not retained here')
class RealTIPSMUSICTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.datasets,cls.ledger=load_provider()
        cls.daily=cls.datasets[(TIPSMUSIC,'days',1)]
        cls.minute=cls.datasets[(TIPSMUSIC,'minutes',1)]
        cls.benchmark=cls.datasets[(BENCHMARK,'days',1)]
        cls.cutoff=datetime.fromisoformat(cls.ledger['finished_at'])
        cls.sessions=tuple(Session(date.fromisoformat(s['day']),datetime.fromisoformat(s['opens_at']),
            datetime.fromisoformat(s['closes_at'])) for s in json.loads((OUT/'session-schedule-proposal.json').read_text())['sessions'])

    def test_exact_nine_requests_hashes_and_no_errors(self):
        self.assertTrue(self.ledger['complete'])
        self.assertIsNone(self.ledger['failure'])
        self.assertEqual(len(self.ledger['requests']),9)
        self.assertTrue(all(r['http_status']==200 for r in self.ledger['requests']))
        self.assertEqual((len(self.daily),len(self.benchmark),len(self.minute)),(392,184,26625))

    def test_full_regular_session_coverage_and_all_boundaries(self):
        groups=session_bars(self.minute,self.sessions,cutoff=self.cutoff,base_minutes=1)
        self.assertEqual(len(groups),71)
        self.assertTrue(all(len(g)==375 for g in groups))
        for tf,count in [('Daily',71),('4-hour',142),('75-minute',355),('1-hour',497)]:
            bars=aggregate(self.minute,self.sessions,tf,cutoff=self.cutoff,base_minutes=1)
            self.assertEqual(len(bars),count)
            self.assertEqual(sum(c.volume for c in bars),sum(c.volume for c in self.minute))
            self.assertTrue(all(c.completed_at<=self.cutoff for c in bars))
        bench_sessions=tuple(s for s in self.sessions if s.day>=date(2026,8,17))
        bgroups=session_bars(self.datasets[(BENCHMARK,'minutes',5)],bench_sessions,cutoff=self.cutoff,base_minutes=5)
        self.assertEqual(len(bgroups),52)
        self.assertTrue(all(len(g)==75 for g in bgroups))

    def test_aug18_independent_fraction_rvol_and_rs(self):
        day=date(2026,8,18)
        stock=next(c for c in self.daily if c.start_ist.date()==day)
        prior=[c for c in self.daily if c.start_ist.date()<day][-20:]
        a=Fraction(stock.volume)*20/sum(Fraction(c.volume) for c in prior)
        preceding_sessions=[s for s in self.sessions if s.day<day][-20:]
        old=sum(Fraction(c.volume) for c in self.minute if c.start_ist.date() in {s.day for s in preceding_sessions})
        current=sum(Fraction(c.volume) for c in self.minute if c.start_ist.date()==day)
        b=current*20/old
        with (OUT/'features/daily.csv').open() as stream:
            row=next(r for r in csv.DictReader(stream) if r['bar_date']==str(day))
        self.assertLess(abs(Fraction(D(row['candidate_a']))-a),Fraction(1,10**24))
        self.assertLess(abs(Fraction(D(row['candidate_b']))-b),Fraction(1,10**24))
        stock_by_day={c.start_ist.date():c for c in self.daily}
        aligned=tuple(c for c in self.benchmark if c.start_ist.date() in stock_by_day)
        index=next(i for i,c in enumerate(aligned) if c.start_ist.date()==day)
        old_day=aligned[index-55].start_ist.date()
        rs=(Fraction(stock.close)/Fraction(stock_by_day[old_day].close))/(
            Fraction(aligned[index].close)/Fraction(aligned[index-55].close))-1
        self.assertLess(abs(Fraction(D(row['rs55']))-rs),Fraction(1,10**24))
        self.assertEqual(D(row['each_bar_rvol20_pct']),D(18))

    def test_real_rvol_prefix_invariance(self):
        sessions=self.sessions[:21]
        subset=tuple(c for c in self.minute if c.start_ist.date()<=sessions[-1].day)
        end=sessions[-1].closes_at
        daily=tuple(c for c in self.daily if c.completed_at<=end)
        full=intra_rvol(subset,sessions,daily,cutoff=end,base_minutes=1)
        prefix=intra_rvol(subset[:-1],sessions,daily,cutoff=subset[-2].end_ist,base_minutes=1)
        self.assertEqual(full[:-1],prefix)
        self.assertEqual(full[-1]['session_id'],'NSE:2026-08-18')
