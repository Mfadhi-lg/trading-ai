"""ER-01 real retained-evidence checks and explicitly synthetic timing tests."""
import collections
import csv
import json
import unittest
from datetime import date, datetime
from experiments.earnings_reaction_v1 import event_isolation as er
from experiments.earnings_reaction_v1.events import Session, TradingCalendar
from experiments.earnings_reaction_v1.verification import Check, Outcome, publication_time, filing_relationship, evidenced_calendar


def t(day,clock):return datetime.fromisoformat(f'2026-{day}T{clock}:00+05:30')

def calendar():
    # Synthetic: May 1 closure and May 2 special live session, no weekday inference.
    sessions=tuple(Session(t(d,'09:15').date(),t(d,'09:15'),t(d,'15:30')) for d in ['04-30','05-02','05-04'])
    return TradingCalendar('NSE','synthetic-er01','test:calendar','Asia/Kolkata',date(2026,4,30),date(2026,5,4),sessions)


class Boundaries(unittest.TestCase):
    def bars(self,release,minutes=60,next_release=None,observed=None):
        return er.first_candles(release,next_release,calendar(),minutes,observed,cutoff=t('05-04','15:30'))
    def test_preopen(self):
        self.assertEqual(self.bars(t('04-30','08:00'))[0]['start'],t('04-30','09:15').isoformat())
    def test_intraday_excludes_overlapping_hour(self):
        self.assertEqual(self.bars(t('04-30','10:20'))[0]['start'],t('04-30','11:15').isoformat())
    def test_daily_intraday_next_session_and_holiday(self):
        self.assertEqual(self.bars(t('04-30','10:20'),None)[0]['start'],t('05-02','09:15').isoformat())
    def test_after_close_special_live_saturday(self):
        self.assertEqual(self.bars(t('04-30','15:30'))[0]['start'],t('05-02','09:15').isoformat())
    def test_nontrading_day(self):
        self.assertEqual(self.bars(t('05-01','12:00'))[0]['start'],t('05-02','09:15').isoformat())
    def test_75m_intraday(self):
        self.assertEqual(self.bars(t('04-30','10:20'),75)[0]['start'],t('04-30','10:30').isoformat())
    def test_4h_intraday(self):
        b=self.bars(t('04-30','10:20'),240)[0]
        self.assertEqual(b['start'],t('04-30','13:15').isoformat());self.assertTrue(b['truncated'])
    def test_first_completed_distinct(self):
        b=self.bars(t('04-30','09:15'))[0]
        self.assertEqual(b['completed_at'],t('04-30','10:15').isoformat())
    def test_missing_and_suspended_not_filled(self):
        self.assertEqual(self.bars(t('04-30','09:15'),observed=set()),[])
    def test_next_release_inside_candle(self):
        self.assertFalse(er.candle_window(t('04-30','10:15'),t('04-30','11:15'),t('04-30','09:15'),t('04-30','10:45')))
    def test_next_release_on_candle_close(self):
        self.assertTrue(er.candle_window(t('04-30','09:15'),t('04-30','10:15'),t('04-30','09:15'),t('04-30','10:15')))
        self.assertFalse(er.candle_window(t('04-30','10:15'),t('04-30','11:15'),t('04-30','09:15'),t('04-30','10:15')))
    def test_censored_cutoff(self):
        self.assertFalse(er.candle_window(t('04-30','10:15'),t('04-30','11:15'),t('04-30','09:15'),cutoff=t('04-30','10:45')))
    def test_verified_next_does_not_override_observation_cutoff(self):
        self.assertFalse(er.candle_window(t('04-30','10:15'),t('04-30','11:15'),t('04-30','09:15'),
                                         next_release=t('05-04','15:30'),cutoff=t('04-30','10:45')))
    def test_cutoff_required(self):
        self.assertEqual(er.first_candles(t('04-30','09:15'),None,calendar(),60),[])
    def test_naive_rejected(self):
        with self.assertRaises(ValueError):self.bars(datetime(2026,4,30,10,20))
    def test_invalid_interval(self):
        with self.assertRaises(ValueError):self.bars(t('04-30','09:15'),0)
    def test_no_calendar_no_guess(self):
        self.assertEqual(er.first_candles(t('04-30','09:15'),None,None,60),[])
    def test_all_intervals_no_future_leakage(self):
        for interval in [None,240,75,60]:
            for b in self.bars(t('04-30','10:20'),interval,t('05-02','11:11')):
                self.assertGreaterEqual(datetime.fromisoformat(b['start']),t('04-30','10:20'))
                self.assertLessEqual(datetime.fromisoformat(b['end']),t('05-02','11:11'))
    def test_status_axes(self):
        self.assertEqual(er.research_status(True,True,False,False),'VERIFIED_RIGHT_CENSORED')
        self.assertEqual(er.research_status(True,True,True,False),'DATA_INCOMPLETE')
        self.assertEqual(er.research_status(True,True,True,True),'VERIFIED_COMPLETE')
        self.assertEqual(er.research_status(False,False,False,False),'IDENTITY_UNRESOLVED')
        self.assertEqual(er.research_status(True,False,False,False),'START_UNVERIFIED')
    def test_corrections_need_parent(self):
        _,check=filing_relationship('Revision',evidence=Check(Outcome.VERIFIED,'test','synthetic'))
        self.assertNotEqual(check.outcome,Outcome.VERIFIED)
    def test_broadcast_or_creation_not_dissemination(self):
        stamp,_=publication_time({'Fld_CreateDate':'2026-10-31T00:09:26.26'},field_evidence=Check(Outcome.VERIFIED,'test','synthetic'))
        self.assertIsNone(stamp)


class RetainedEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=er.load();cls.ev=er.evidence_records(cls.p)
    def csv(self,name):
        with (er.OUT/name).open() as f:return list(csv.DictReader(f))
    def test_original_source_bytes_hashes(self):
        self.assertEqual(len(self.p['source_manifests']),2)
    def test_counts_and_identity_resolution(self):
        ledger=self.csv('EVENT_LEDGER.csv')
        self.assertEqual(collections.Counter(r['quarter'] for r in ledger),{'FY2026-Q4':239,'FY2027-Q1':389})
        self.assertEqual(len({r['company_id'] for r in ledger}),500)
        self.assertEqual(len({r['event_id'] for r in ledger}),628)
    def test_no_fabricated_original_or_next_event(self):
        for r in self.csv('EVENT_LEDGER.csv'):
            self.assertFalse(r['announcement_at_ist']);self.assertFalse(r['next_announcement_at_ist'])
            self.assertEqual(r['eligibility'],'START_UNVERIFIED')
        self.assertEqual(self.csv('EVENT_WINDOWS.csv'),[])
    def test_real_publication_fields(self):
        valid=[r for r in self.ev if r['retained_field_dissemination_ist']]
        self.assertGreater(len(valid),0)
        for r in valid:
            self.assertTrue(r['source']);self.assertIsNotNone(r['raw_dissemination'])
            self.assertIn('+05:30',r['retained_field_dissemination_ist'])
            self.assertIsNone(r['filing_dissemination_ist'])
    def test_real_missing_sources_never_verified(self):
        for r in self.ev:
            if not r['source']:self.assertIsNone(r['filing_dissemination_ist'])
    def test_duplicate_sources_do_not_multiply_events(self):
        self.assertGreater(len(self.ev),len({r['same_filing_identity'] for r in self.ev}))
        self.assertEqual(len(self.csv('EVENT_LEDGER.csv')),628)
    def test_pilot_both_quarters_and_conditions(self):
        pilot=json.loads((er.OUT/'PILOT.json').read_text())['events']
        self.assertEqual(collections.Counter(r['quarter'] for r in pilot),{'FY2026-Q4':6,'FY2027-Q1':6})
        self.assertTrue(any(r['filing_type']=='Revision' for r in pilot))
        self.assertTrue(any(r['clock_phase']=='INTRADAY_CLOCK_ONLY' for r in pilot))
        self.assertTrue(any(r['clock_phase']=='AFTER_CLOSE_CLOCK_ONLY' for r in pilot))
    def test_original_membership_and_tutor_labels_preserved(self):
        members=self.csv('COHORT_MEMBERSHIP.csv');self.assertEqual(len(members),628)
        for r in members:self.assertNotEqual(r['explicit_selection_decision'],'REJECTED')
        self.assertEqual({r['row_id'] for r in members},{r['row_id'] for r in self.p['cohort']})
    def test_classification_never_backdated(self):
        for r in self.csv('EVENT_LEDGER.csv'):
            self.assertEqual(r['classification_event_date_availability'],'UNKNOWN_RETROSPECTIVE_DO_NOT_BACKDATE')
    def test_actual_calendar_remains_blocked(self):
        rec=next(r for r in self.p['ancillary'] if r['original_path'].endswith('/calendar-evidence.json'))
        for v in json.loads((er.OUT/rec['retained_path']).read_text()).values():
            cal,check=evidenced_calendar(v);self.assertIsNone(cal);self.assertNotEqual(check.outcome,Outcome.VERIFIED)
    def test_retained_prices_are_outside_cohort(self):
        self.assertEqual({r['daily_rows'] for r in self.csv('DATA_COVERAGE.csv')},{'0'})
        self.assertEqual({r['intraday_source_rows'] for r in self.csv('DATA_COVERAGE.csv')},{'0'})
        self.assertEqual({d['instrument_key'] for d in self.p['candle_datasets'] if d['instrument_key'].startswith('NSE_EQ')},
                         {'NSE_EQ|INE0BWX01014','NSE_EQ|INE716B01029'})
    def test_historical_master_ids_not_fuzzy_joined(self):
        self.assertEqual(len(self.p['identity_audit']),500)
        self.assertTrue(all(r['agrees'] for r in self.p['identity_audit']))
    def test_source_replay_deterministic(self):
        self.assertEqual(er.J(self.ev),er.J(er.evidence_records(self.p)))
    def test_database_and_sidecars_preserved(self):
        from pathlib import Path
        for path,stats in self.p['databases'].items():
            p=Path(path)
            if not p.is_file():self.skipTest('Original large snapshot absent; frozen evidence replay remains available')
            self.assertEqual([p.stat().st_size,p.stat().st_mtime_ns],stats)

if __name__=='__main__':unittest.main()
