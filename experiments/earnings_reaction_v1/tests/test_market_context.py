"""Time and vintage safeguards use real retained data plus calendar edge cases."""
import copy
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
import unittest
from experiments.earnings_reaction_v1 import market_context as m

class MarketContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.groups,cls.retro,cls.data,_=m.observations()
        m.groups_to_calendar=cls.data[m.BENCH]

    def test_us_dst_uses_new_york_timezone(self):
        self.assertEqual(m.previous_us_session('2026-03-09').astimezone(m.IST).isoformat(),'2026-03-07T02:30:00+05:30')
        self.assertEqual(m.previous_us_session('2026-03-10').astimezone(m.IST).isoformat(),'2026-03-10T01:30:00+05:30')
        self.assertEqual(m.previous_us_session('2026-11-03').astimezone(m.IST).isoformat(),'2026-11-03T02:30:00+05:30')

    def test_us_weekend_and_independence_holiday(self):
        self.assertEqual(str(m.previous_us_session('2026-07-06').date()),'2026-07-02')
        self.assertEqual(str(m.previous_us_session('2026-05-26').date()),'2026-05-22')

    def test_us_early_close(self):
        self.assertEqual(m.previous_us_session('2026-11-30').hour,13)

    def test_h10_actual_archive_observation_and_release_are_distinct(self):
        rows=m.h10_rows(m.OUT/'raw/H10-2026-05-18.html')
        self.assertEqual(rows[-1]['value'],D('95.9700'))
        self.assertEqual(rows[-1]['observation_timestamp'],'2026-05-15T12:00:00-04:00')
        self.assertEqual(rows[-1]['scheduled_release_at'],'2026-05-18T16:15:00-04:00')
        self.assertEqual(rows[-1]['available_at'],'2026-05-19T00:00:00-04:00')
        self.assertEqual(rows[-1]['actual_publication_timestamp'],'')

    def test_h10_friday_rate_not_backdated_to_monday_india(self):
        monday=next(r for r in m.context(self.groups,'2026-05-18','PREOPEN') if r['series']=='USDINR')
        tuesday=next(r for r in m.context(self.groups,'2026-05-19','PREOPEN') if r['series']=='USDINR')
        self.assertEqual(monday['observation_date'],'2026-05-08')
        self.assertEqual(tuesday['observation_date'],'2026-05-08')
        wednesday=next(r for r in m.context(self.groups,'2026-05-20','PREOPEN') if r['series']=='USDINR')
        self.assertEqual(wednesday['observation_date'],'2026-05-15')

    def test_h10_holiday_release_not_assumed_monday(self):
        tuesday=next(r for r in m.context(self.groups,'2026-05-26','PREOPEN') if r['series']=='USDINR')
        wednesday=next(r for r in m.context(self.groups,'2026-05-27','PREOPEN') if r['series']=='USDINR')
        self.assertEqual(tuesday['observation_date'],'2026-05-15')
        self.assertEqual(wednesday['observation_date'],'2026-05-15')
        thursday=next(r for r in m.context(self.groups,'2026-05-28','PREOPEN') if r['series']=='USDINR')
        self.assertEqual(thursday['observation_date'],'2026-05-22')

    def test_futures_and_oil_never_invented_preopen(self):
        ctx=m.context(self.groups,'2026-05-19','PREOPEN')
        for r in ctx:
            if r['series'] in ('ES','NQ','BRENT','WTI','GIFT_NIFTY','SP500','NASDAQCOM','DJIA'):
                self.assertIsNone(r['value']);self.assertTrue(r['missing_data_flag'])
            self.assertIsNone(r['preopen_overnight_change_pct'])

    def test_no_stale_nifty_after_retained_coverage(self):
        first=next(r for r in m.context(self.groups,'2026-08-17','PREOPEN') if r['series']=='NIFTY50')
        second=next(r for r in m.context(self.groups,'2026-08-18','PREOPEN') if r['series']=='NIFTY50')
        self.assertEqual(first['observation_date'],'2026-08-14')
        self.assertIsNone(second['value'])

    def test_nifty_today_excluded_preopen_and_close_marked_conceptual(self):
        a=next(r for r in m.context(self.groups,'2026-05-19','PREOPEN') if r['series']=='NIFTY50')
        b=next(r for r in m.context(self.groups,'2026-05-19','BY_CLOSE') if r['series']=='NIFTY50')
        self.assertLess(a['observation_date'],'2026-05-19')
        self.assertEqual(b['observation_date'],'2026-05-19')
        self.assertIn('CONCEPTUAL',b['availability_basis'])

    def test_future_releases_removed_no_effect_on_earlier_context(self):
        before=m.context(self.groups,'2026-05-19','PREOPEN')
        prefix={s:[r for r in rows if datetime.fromisoformat(r['available_at'])<m.stamp('2026-05-19',9,15)] for s,rows in self.groups.items()}
        self.assertEqual(before,m.context(prefix,'2026-05-19','PREOPEN'))

    def test_1_5_20_arithmetic_and_no_imputation(self):
        for s in ('USDINR','NIFTY50'):
            rows=self.groups[s]
            for i,r in enumerate(rows):
                for n in (1,5,20):
                    expected=100*(r['value']/rows[i-n]['value']-1) if i>=n else None
                    self.assertEqual(r[f'change_{n}s_pct'],expected)
        missing=[r for r in self.retro if r['value'] is None]
        self.assertTrue(missing)
        self.assertTrue(all(r['change_1s_pct'] is None for r in missing))

    def test_rupee_direction_is_inverse_rate(self):
        self.assertEqual(m.direction('USDINR',D(1)),'RUPEE_DEPRECIATING')
        self.assertEqual(m.direction('USDINR',D(-1)),'RUPEE_APPRECIATING')

if __name__=='__main__':unittest.main()
