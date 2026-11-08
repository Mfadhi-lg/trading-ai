"""Safety and causal-contract tests; fixtures are synthetic, never provider evidence."""
import json
from datetime import datetime,timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from .observations import aggregate_minutes,boundaries,select_asof,incremental_windows
from .acquire import normalize
from .calendar import day_record
from .storage import verify,sha
from . import acquire,storage
from .membership import eligibility_asof

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.day='2026-10-30'
        self.session=day_record(self.day)['sessions'][0]
    def test_owner_boundaries(self):
        hourly=list(boundaries(self.session['opens_at'],self.session['closes_at'],60))
        four=list(boundaries(self.session['opens_at'],self.session['closes_at'],240))
        self.assertEqual([s.strftime('%H:%M') for s,e in hourly],['09:15','10:15','11:15','12:15','13:15','14:15','15:15'])
        self.assertEqual([(e-s).total_seconds()/60 for s,e in four],[240,135])
        self.assertEqual((hourly[-1][1]-hourly[-1][0]).seconds,900)
    def test_holidays_and_special_sessions(self):
        self.assertFalse(day_record('2023-06-29')['sessions'])
        self.assertTrue(day_record('2023-06-28')['sessions'])
        self.assertFalse(day_record('2024-11-20')['sessions'])
        self.assertEqual(len(day_record('2024-03-02')['sessions']),2)
        self.assertEqual(day_record('2025-10-21')['sessions'][0]['opens_at'][11:16],'13:45')
        self.assertEqual(day_record('2026-02-01')['status'],'SPECIAL_LIVE')
        self.assertEqual(day_record('2004-01-01')['status'],'UNRESOLVED_PRE2022')
    def minute_rows(self):
        start=datetime.fromisoformat(self.session['opens_at'])
        return [dict(start=(start+timedelta(minutes=i)).isoformat(),open=Decimal(10),high=Decimal(12),
            low=Decimal(9),close=Decimal(11),volume=1,quality='OHLC_VALID',instrument_key='NSE_EQ|SYNTHETIC',
            timeframe='1-minute',definition='FIXTURE',end_upper_bound=(start+timedelta(minutes=i+1)).isoformat(),
            observed_at='2026-11-05T00:00:00+05:30',status='COMPLETED_RETROSPECTIVE') for i in range(375)]
    def test_14h_developing_snapshot_does_not_include_final_close(self):
        rows=self.minute_rows()
        result=aggregate_minutes(rows,self.session,240,self.day+'T14:00:00+05:30',self.day+'T14:00:01+05:30')
        self.assertEqual([r['status'] for r in result],['COMPLETED_RETROSPECTIVE','PROVISIONAL'])
        self.assertEqual([r['volume'] for r in result],[240,45])
        self.assertEqual(result[1]['prefix_through'][11:16],'13:59')
        self.assertEqual(select_asof(result,self.day+'T14:00:00+05:30',True),[])
        self.assertEqual(len(select_asof(result,self.day+'T14:00:02+05:30',True)),2)
        self.assertEqual(len(select_asof(result,self.day+'T14:00:02+05:30')),1)
    def test_minute_gap_rejected(self):
        with self.assertRaises(ValueError):
            aggregate_minutes(self.minute_rows()[1:],self.session,60,self.day+'T15:30:00+05:30',self.day+'T16:00:00+05:30')
    def test_later_revision_does_not_replace_old_observation(self):
        r=self.minute_rows()[0];r['observed_at']=self.day+'T09:16:01+05:30'
        revised=dict(r,close=Decimal(100),observed_at=self.day+'T16:00:00+05:30')
        self.assertEqual(select_asof([r,revised],self.day+'T14:00:00+05:30')[0]['close'],Decimal(11))
        self.assertEqual(select_asof([r,revised],self.day+'T16:00:00+05:30')[0]['close'],Decimal(100))
    def test_backfill_has_no_historical_delivery(self):
        self.assertEqual(select_asof(self.minute_rows(),self.day+'T15:30:00+05:30'),[])
    def test_duplicate_and_nonfinite_provider_payloads_fail(self):
        q=dict(company_id=1,instrument_key='NSE_EQ|FIXTURE',timeframe='1-hour',unit='hours',interval=1,from_date=self.day,to_date=self.day)
        row=[self.day+'T09:15:00+05:30',10,12,9,11,100,0]
        raw=json.dumps(dict(status='success',data=dict(candles=[row,row]))).encode()
        with self.assertRaises(ValueError): normalize(raw,q,'2026-11-05T00:00:00+00:00')
        with self.assertRaises(ValueError): normalize(b'{"status":"success","data":{"candles":[NaN]}}',q,'2026-11-05T00:00:00+00:00')
    def test_ohlc_source_defect_retained_and_quarantined(self):
        q=dict(company_id=1,instrument_key='NSE_EQ|FIXTURE',timeframe='Daily',unit='days',interval=1,from_date=self.day,to_date=self.day)
        raw=json.dumps(dict(status='success',data=dict(candles=[[self.day+'T00:00:00+05:30',10,9,8,11,100,0]]))).encode()
        rows,issues=normalize(raw,q,'2026-11-05T00:00:00+00:00')
        self.assertEqual(rows[0]['quality'],'QUARANTINED_OHLC');self.assertEqual(len(issues),1)
    def test_missing_passport_never_falls_back(self):
        with self.assertRaises(RuntimeError): verify(Path('/definitely-missing-passport'),123)
    def test_incremental_is_gap_aware_and_bounded(self):
        expected=['2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30']
        result=incremental_windows(expected,set(expected)-{'2026-10-27'},'2026-10-30','2026-10-30',2)
        self.assertEqual(result,[('2026-10-27','2026-10-27'),('2026-10-30','2026-10-30')])
        with self.assertRaises(ValueError): incremental_windows(expected,set(),'2022-01-01','2026-10-30',100)
    def test_auth_failure_does_not_retry(self):
        q=dict(instrument_key='NSE_EQ|INE900B01029',unit='weeks',interval=1,from_date='2000-01-01',to_date='2026-11-04',request_id='fixture')
        completed=Mock(returncode=0,stdout=b'error\n401')
        with tempfile.TemporaryDirectory() as tmp,patch.object(acquire,'OUT',Path(tmp)),patch.object(acquire.subprocess,'run',return_value=completed) as run:
            raw,status=acquire.fetch(q,'synthetic-secret-token-for-test-only')
            self.assertEqual(status,401);self.assertEqual(run.call_count,1)
    def test_raw_checksum_corruption_fails_recovery(self):
        import gzip
        with tempfile.TemporaryDirectory() as tmp:
            a=storage.Archive.__new__(storage.Archive);a.root=Path(tmp);a.guard=Mock()
            raw=b'{"synthetic":true}'
            digest,path=a.raw(raw)
            (a.root/path).write_bytes(gzip.compress(b'corrupt'))
            with self.assertRaises(RuntimeError): a.raw(raw)
    def test_eligibility_never_backdates_todays_universe(self):
        rows=[dict(company_path='/company/FIXTURE/',observed_at_utc='2026-11-05T00:00:00+00:00',market_cap_crore='1000',eligibility='eligible')]
        self.assertIsNone(eligibility_asof(rows,{'/company/FIXTURE/'},'2022-01-01T14:00:00+05:30')['eligible'])
    def test_exact_580_is_excluded(self):
        rows=[dict(company_path='/company/FIXTURE/',observed_at_utc='2026-11-01T00:00:00+00:00',market_cap_crore='580',eligibility='eligible')]
        self.assertFalse(eligibility_asof(rows,{'/company/FIXTURE/'},'2026-11-05T14:00:00+05:30')['eligible'])
    def test_historical_failure_is_not_removed_by_new_observation(self):
        rows=[dict(company_path='/company/FIXTURE/',observed_at_utc='2026-09-01T00:00:00+00:00',market_cap_crore='1000',eligibility='eligible'),
              dict(company_path='/company/FIXTURE/',observed_at_utc='2026-10-01T00:00:00+00:00',market_cap_crore='100',eligibility='below_floor')]
        self.assertTrue(eligibility_asof(rows,{'/company/FIXTURE/'},'2026-09-15T14:00:00+05:30')['eligible'])
        self.assertFalse(eligibility_asof(rows,{'/company/FIXTURE/'},'2026-10-15T14:00:00+05:30')['eligible'])
    def test_ambiguous_daily_buckets_fail_closed(self):
        row=self.minute_rows()[0]
        row.update(timeframe='Daily',start=self.day+'T00:00:00+05:30',end_upper_bound=self.day+'T23:59:59+05:30',
                   observed_at='2026-11-01T00:00:00+05:30')
        other=dict(row,start=self.day+'T09:15:00+05:30')
        self.assertEqual(select_asof([row,other],'2026-11-05T00:00:00+05:30'),[])
    def test_later_ambiguity_evidence_does_not_backdate(self):
        row=self.minute_rows()[0]
        row.update(timeframe='Daily',start=self.day+'T00:00:00+05:30',end_upper_bound=self.day+'T23:59:59+05:30',
                   observed_at='2026-11-01T00:00:00+05:30')
        other=dict(row,start=self.day+'T09:15:00+05:30',observed_at='2026-11-03T00:00:00+05:30')
        self.assertEqual(len(select_asof([row,other],'2026-11-02T00:00:00+05:30')),1)
    def test_nullable_parquet_end_uses_conservative_bound(self):
        row=self.minute_rows()[0]
        row.update(end=None,observed_at=self.day+'T09:16:01+05:30')
        self.assertEqual(len(select_asof([row],self.day+'T14:00:00+05:30')),1)

if __name__=='__main__': unittest.main()
