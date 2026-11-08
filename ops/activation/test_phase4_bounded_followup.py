"""Only finite follow-up duration validation; no production activation."""
import importlib.util,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
P=ROOT/'ops/activation/phase4_schedule.py'
spec=importlib.util.spec_from_file_location('followup_schedule',P)
schedule=importlib.util.module_from_spec(spec);spec.loader.exec_module(schedule)

class FollowupTests(unittest.TestCase):
    def config(self,hours):
        c=json.loads((ROOT/'ops/activation/phase4_schedule.json').read_text())
        start=datetime(2026,10,14,6,30,tzinfo=timezone.utc)
        c.update(enabled=True,activated_at_utc=start.isoformat(),expires_at_utc=(start+timedelta(hours=hours)).isoformat(),initial_free_bytes=375000000000)
        if hours!=24:c['verification_window_hours']=hours
        return c
    def settings(self,c):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';p.write_text(json.dumps(c))
            with patch.object(schedule,'CONFIG',p):return schedule.settings()
    def test_initial_duration_remains_valid(self):self.assertEqual(self.settings(self.config(24))['initial_duration_hours'],24)
    def test_only_bounded_followup_durations_are_accepted(self):
        for hours in (4,5,6):self.assertEqual(self.settings(self.config(hours))['verification_window_hours'],hours)
        for hours in (0,1,3,7,12,25):
            with self.subTest(hours=hours),self.assertRaises(RuntimeError):self.settings(self.config(hours))
    def test_duration_mismatch_fails(self):
        c=self.config(6);c['verification_window_hours']=4
        with self.assertRaises(RuntimeError):self.settings(c)
    def test_expiry_blocks_new_jobs_and_keeps_same_cron(self):
        c=self.config(6)
        with patch.object(schedule,'settings',return_value=c):
            self.assertEqual(len(schedule.jobs(datetime.fromisoformat(c['activated_at_utc']))),5)
            self.assertEqual(schedule.jobs(datetime.fromisoformat(c['expires_at_utc'])),[])
            self.assertEqual(schedule.jobs(datetime.fromisoformat(c['activated_at_utc'])-timedelta(seconds=1)),[])
    def test_prepared_configuration_is_inactive(self):
        c=json.loads((ROOT/'artifacts/phase4/routine/gap-closure-20261013/followup-config.json').read_text())
        with patch.object(schedule,'settings',return_value=c):self.assertEqual(schedule.jobs(),[])
