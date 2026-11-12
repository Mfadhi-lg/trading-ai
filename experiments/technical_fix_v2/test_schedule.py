from datetime import datetime
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock
from .schedule import tick
from experiments.historical_candle_archive_v1.storage import dump,sha

class ScheduleFixture:
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.out=Path(self.tmp.name)
        self.plan=dict(first_day='2026-11-09',last_day='2026-11-13',max_requests=1,requests=[dict(request_id='fixture',kind='STOCK',day='2026-11-09',checkpoint='09:30',intraday=True)])
        dump(self.out/'PROPOSED_MANIFEST.json',self.plan)
        self.runner=Mock(return_value=dict(status='COMPLETE'))
    def grant(self):
        dump(self.out/'OWNER_GRANT.json',dict(manifest_sha256=sha((self.out/'PROPOSED_MANIFEST.json').read_bytes()),max_requests=1))
    def ledger(self,folder,record):
        plan=json.loads((self.out/'PROPOSED_MANIFEST.json').read_text())
        dump(folder/'manifest.json',dict(requests=plan['requests'],
            parent_manifest_sha256=sha((self.out/'PROPOSED_MANIFEST.json').read_bytes())))
        record['manifest_sha256']=sha((folder/'manifest.json').read_bytes())
        dump(folder/'UPDATE_LEDGER.json',record)
    def run_tick(self,at,**kw):return tick(datetime.fromisoformat(at),output=self.out,runner=self.runner,readiness=lambda:True,**kw)

class ScheduleTests(ScheduleFixture, unittest.TestCase):
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


class ScheduledStatusTests(ScheduleFixture, unittest.TestCase):
    def test_status_reports_missing_daily_data_and_failed_batch(self):
        from .schedule import status
        self.grant()
        self.plan['requests'][0].update(checkpoint='16:00', intraday=False)
        dump(self.out/'PROPOSED_MANIFEST.json',self.plan);self.grant()
        now=datetime.fromisoformat('2026-11-09T16:30:00+05:30')
        report=status(now,output=self.out)
        self.assertEqual(report['batches'][0]['status'],'NOT_STARTED')
        self.assertEqual(report['freshness'],'MISSING')
        folder=self.out/'batches/2026-11-09T1600'
        self.ledger(folder,dict(status='STOPPED',last_error='normalization failed',requests={
            'fixture':dict(status='RAW_SAVED',http_status=200,received_at='2026-11-09T16:01:00+05:30')}))
        report=status(now,output=self.out)
        self.assertEqual(report['batches'][0]['recovery'],'RESUME_SAVED_RESPONSE')
        self.assertEqual(report['batches'][0]['last_error'],'normalization failed')
        self.assertEqual(report['freshness'],'MISSING')

    def test_status_does_not_treat_empty_or_quarantined_batch_as_fresh(self):
        from .schedule import status
        self.grant()
        folder=self.out/'batches/2026-11-09T0930'
        self.ledger(folder,dict(status='COMPLETE',requests={
            'fixture':dict(status='EMPTY',accepted=0,quarantined=4)}))
        report=status(datetime.fromisoformat('2026-11-09T16:30:00+05:30'),output=self.out)
        self.assertEqual(report['freshness'],'MISSING')
        self.assertEqual(report['batches'][0]['quarantined'],4)

    def test_status_rejects_mismatched_receipt_and_marks_yesterday_expired(self):
        from .schedule import status
        self.grant()
        folder=self.out/'batches/2026-11-09T0930'
        self.ledger(folder,dict(status='STOPPED',requests={
            'fixture':dict(status='RAW_SAVED',http_status=200)}))
        report=status(datetime.fromisoformat('2026-11-11T16:30:00+05:30'),output=self.out)
        self.assertEqual(report['batches'][0]['recovery'],'EXPIRED_CHECKPOINT_OWNER_REVIEW_REQUIRED')
        dump(folder/'manifest.json',dict(requests=[]))
        report=status(datetime.fromisoformat('2026-11-11T16:30:00+05:30'),output=self.out)
        self.assertEqual(report['batches'][0]['status'],'SCOPE_MISMATCH')
        self.assertEqual(report['freshness'],'MISSING')
        with self.assertRaisesRegex(RuntimeError,'SAVED_BATCH_SCOPE_CHANGED'):
            self.run_tick('2026-11-09T16:30:00+05:30')
        self.runner.assert_not_called()

    def test_http_failure_requires_review_and_outer_status_agrees_on_grant(self):
        from .schedule import status
        from ops.host.technical_market_updates import status as host_status
        self.grant()
        folder=self.out/'batches/2026-11-09T0930'
        self.ledger(folder,dict(status='STOPPED',requests={
            'fixture':dict(status='STOP_HTTP_401',http_status=401)}))
        now=datetime.fromisoformat('2026-11-09T16:30:00+05:30')
        self.assertEqual(status(now,output=self.out)['batches'][0]['recovery'],'OWNER_REVIEW_REQUIRED')
        recurring=self.out/'recurring'
        dump(recurring/'PROPOSED_MANIFEST.json',self.plan)
        dump(recurring/'OWNER_GRANT.json',dict(manifest_sha256=sha((recurring/'PROPOSED_MANIFEST.json').read_bytes()),max_requests=99))
        dump(recurring/'TIMER_STATUS.json',dict(loaded=True))
        report=host_status(now,output=self.out)
        self.assertFalse(report['recurring_acquisition_enabled'])
        self.assertFalse(report['recurring_trial_authorized'])

    def test_scheduler_lock_prevents_manifest_rewrite_and_duplicate_dispatch(self):
        import fcntl
        self.grant()
        with (self.out/'.schedule.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=self.run_tick('2026-11-09T16:30:00+05:30')
        self.assertEqual(result['status'],'ACQUISITION_BUSY')
        self.runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
