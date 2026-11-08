from datetime import datetime
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock
from .schedule import tick
from experiments.historical_candle_archive_v1.storage import dump,sha

class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.out=Path(self.tmp.name)
        self.plan=dict(first_day='2026-11-09',last_day='2026-11-13',max_requests=1,requests=[dict(request_id='fixture',kind='STOCK',day='2026-11-09',checkpoint='09:30',intraday=True)])
        dump(self.out/'PROPOSED_MANIFEST.json',self.plan)
        self.runner=Mock(return_value=dict(status='COMPLETE'))
    def grant(self):
        dump(self.out/'OWNER_GRANT.json',dict(manifest_sha256=sha((self.out/'PROPOSED_MANIFEST.json').read_bytes()),max_requests=1))
    def run_tick(self,at,**kw):return tick(datetime.fromisoformat(at),output=self.out,runner=self.runner,readiness=lambda:True,**kw)
    def test_no_grant_never_calls_acquirer(self):
        self.assertEqual(self.run_tick('2026-11-09T14:00:00+05:30')['status'],'OWNER_AUTHORIZATION_REQUIRED');self.runner.assert_not_called()
    def test_changed_manifest_rejected_before_acquisition(self):
        self.grant();self.plan['max_requests']=2;dump(self.out/'PROPOSED_MANIFEST.json',self.plan)
        with self.assertRaises(PermissionError):self.run_tick('2026-11-09T14:00:00+05:30')
        self.runner.assert_not_called()
    def test_holiday_and_expiry_make_no_requests(self):
        self.grant()
        self.assertEqual(self.run_tick('2026-11-10T16:00:00+05:30')['status'],'NONREGULAR_SESSION_NO_FETCH')
        self.assertEqual(self.run_tick('2026-11-16T16:00:00+05:30')['status'],'OUTSIDE_APPROVED_DATES');self.runner.assert_not_called()
    def test_only_due_batches_and_parent_digest(self):
        self.grant();r=self.run_tick('2026-11-09T09:30:00+05:30')
        self.assertEqual(r['status'],'DUE_CHECKPOINTS_PROCESSED')
        calls=self.runner.call_args_list
        plans=[json.loads(c.kwargs['manifest_path'].read_text()) for c in calls]
        self.assertEqual(sum(len(p['requests']) for p in plans),1)
        self.assertTrue(all(p['parent_manifest_sha256']==sha((self.out/'PROPOSED_MANIFEST.json').read_bytes()) for p in plans))
        with self.assertRaisesRegex(RuntimeError,'EXPIRED'):
            calls[-1].kwargs['fetch'](self.plan['requests'][0],'never-sent')
    def test_busy_writer_defers_before_provider(self):
        self.grant();r=tick(datetime.fromisoformat('2026-11-09T09:30:00+05:30'),output=self.out,runner=self.runner,readiness=lambda:False)
        self.assertEqual(r['status'],'ACQUISITION_BUSY');self.runner.assert_not_called()

if __name__=='__main__':unittest.main()
