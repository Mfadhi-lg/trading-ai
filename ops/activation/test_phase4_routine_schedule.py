import unittest,json
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import phase4_schedule as schedule

class ScheduleTests(unittest.TestCase):
    def test_disabled_execution_stops_before_owner_and_does_not_shadow_jobs(self):
        with patch.dict('sys.modules',{'phase4_documents':SimpleNamespace(storage_preflight=None,STORE=None,IMAGE=None),
                'production_ownership':SimpleNamespace(owner_gate=None)}),\
                patch.object(schedule,'settings',return_value={}),patch.object(schedule,'jobs',return_value=[]):
            with self.assertRaisesRegex(RuntimeError,'Document scheduling disabled'):
                schedule.execute_once('DOCUMENT_DOWNLOAD',Path('/unused'))
    def test_disabled_schedule_has_no_jobs(self):
        c=schedule.settings();c['enabled']=False
        with patch.object(schedule,'settings',return_value=c):self.assertEqual(schedule.jobs(),[])
    def config(self):
        c=schedule.settings();c.update(authorization_mode='finite',enabled=True,activated_at_utc='2026-10-12T00:00:00+00:00',expires_at_utc='2026-10-13T00:00:00+00:00',initial_free_bytes=375000000000);return c
    def test_exact_initial_slots_and_expiry(self):
        c=self.config()
        with patch.object(schedule,'settings',return_value=c):
            jobs=schedule.jobs(datetime(2026,10,12,12,tzinfo=timezone.utc))
            self.assertEqual(len(jobs),5)
            self.assertEqual(schedule.jobs(datetime(2026,10,13,0,tzinfo=timezone.utc)),[])
            self.assertEqual(schedule.jobs(datetime(2026,10,11,23,tzinfo=timezone.utc)),[])
            self.assertIn('minute=\'35\'',str(jobs[2][2]))
    def test_prepared_dispatcher_preserves_financial_code(self):
        installed=(schedule.HERE/'automatic_acquisition.py').read_text()
        candidate=(schedule.HERE/'automatic_acquisition.phase4-candidate.py').read_text()
        self.assertIn("elif exchange in ('FINANCIAL','CAP'):",candidate)
        self.assertIn(' + document_jobs()',installed)
        self.assertIn(' + document_jobs()',candidate)
        self.assertIn("if exchange in ('FINANCIAL','CAP') or exchange.startswith('DOCUMENT_')",candidate)
    def test_budget_matches_daily_physical_maximum(self):
        c=schedule.settings()
        self.assertEqual(4*4+24+2,c['source_requests_max_per_day'])
        self.assertEqual((4*4*2+24*10+2*10)*1024**2,c['source_bytes_max_per_day'])
        self.assertEqual(c['historical_limit'],0)
        self.assertEqual(c['processing_limit'],1)

    def test_redirect_requests_consume_two_units_and_cannot_overdraw(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);day='2026-10-30';now=datetime(2026,10,30,10,tzinfo=timezone.utc)
            dispatcher=SimpleNamespace(STATE=state,save=lambda p,d:p.write_text(json.dumps(d,default=str)))
            previous=state/(day+'-old');previous.mkdir()
            (previous/'daily-budget-reservation.json').write_text(json.dumps({'stage':'DOCUMENT_DOWNLOAD','request_units':23}))
            current=state/(day+'-current');current.mkdir()
            with patch.dict('sys.modules',{'automatic_acquisition':dispatcher}):
                self.assertFalse(schedule.reserve_daily_stage(current,'DOCUMENT_DOWNLOAD',now))
                (previous/'daily-budget-reservation.json').write_text(json.dumps({'stage':'DOCUMENT_DOWNLOAD','request_units':22}))
                self.assertTrue(schedule.reserve_daily_stage(current,'DOCUMENT_DOWNLOAD',now))
                self.assertEqual(json.loads((current/'daily-budget-reservation.json').read_text())['request_units'],2)

    def test_browser_requests_share_daily_source_budgets(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);now=datetime(2026,10,30,10,tzinfo=timezone.utc)
            dispatcher=SimpleNamespace(STATE=state,save=lambda p,d:p.write_text(json.dumps(d,default=str)))
            previous=state/'2026-10-30-earlier';previous.mkdir()
            current=state/'2026-10-30-rating';current.mkdir()
            prior=previous/'daily-budget-reservation.json'
            with patch.dict('sys.modules',{'automatic_acquisition':dispatcher}):
                prior.write_text(json.dumps({'stage':'DOCUMENT_DISCOVERY','source_request_units':31,'source_byte_units':1}))
                self.assertFalse(schedule.reserve_daily_stage(current,'DOCUMENT_RATING',now))
                prior.write_text(json.dumps({'stage':'DOCUMENT_DOWNLOAD','source_request_units':20,'source_byte_units':200*1024**2}))
                self.assertFalse(schedule.reserve_daily_stage(current,'DOCUMENT_RATING',now))
                prior.write_text(json.dumps({'stage':'DOCUMENT_DISCOVERY','source_request_units':4,'source_byte_units':8*1024**2}))
                self.assertTrue(schedule.reserve_daily_stage(current,'DOCUMENT_RATING',now))
                receipt=json.loads((current/'daily-budget-reservation.json').read_text())
                self.assertEqual(receipt['source_request_units'],12)
                self.assertEqual(receipt['source_byte_units'],120*1024**2)
                self.assertEqual(receipt['request_units'],1)
