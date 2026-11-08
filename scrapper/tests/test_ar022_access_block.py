"""AR-02.2 mixed-source acceptance and operator guidance, entirely offline."""
from types import SimpleNamespace
from unittest.mock import patch

from scrapper.db.database import connect
from scrapper.db.exchange import tasks
from scrapper.operations.coordinator import exchange as coordinator
from scrapper.tests.database_fixtures import prepare_database
from scrapper.tests.test_e06_scheduler import SchedulerCase, REJECTED, RESULT_FEED
from scrapper.workflows.exchange import schedule


class MixedSourceAccessTests(SchedulerCase):
    def test_bse_block_alert_does_not_prevent_nse_result_acceptance_or_retry_bse(self):
        prepare_database(self.db)
        calls = []
        bse = self.fetcher(REJECTED)
        nse = self.fetcher(RESULT_FEED)
        def fetch(url, exchange):
            calls.append(exchange)
            return (bse if exchange == 'BSE' else nse)(url, exchange)
        def run(**kw):
            return schedule.run_attempt(**kw,
                datasets=('bse_financial_results', 'nse_financial_results'))
        api = SimpleNamespace(run_attempt=run, alert=lambda payload: None)
        clock = lambda: '2026-08-06T14:00:00+00:00'
        def dispatch(day, attempt):
            conn = connect(self.db)
            try:
                tasks.enqueue(conn, kind='attempt', scheduled_for=day,
                              attempt=attempt, priority=0, now=clock())
            finally:
                conn.close()
            coordinator.run_pending(db_path=self.db, snapshot_root=self.snapshots,
                now_utc=clock, fetcher=fetch, workflow=api, limit=1)
        with patch.object(api, 'alert') as alert:
            dispatch('2026-08-06', 1)
            payload = alert.call_args.args[0]
            self.assertEqual(payload['status'], 'access_blocked')
            self.assertEqual(payload['access_blocked_datasets'], ['bse_financial_results'])
            self.assertIn('BSE-only coverage remains unproven', payload['access_blocked_action'])
            self.assertEqual(self.outcomes(payload)['nse_financial_results'], 'published_changed')
            self.assertFalse(payload['retry_scheduled'])
            self.assertTrue(self.rows('SELECT * FROM exchange_result_filings'))
            self.assertEqual(calls.count('BSE'), 1)
            self.assertGreaterEqual(calls.count('NSE'), 1)
            before = self.rows('SELECT * FROM exchange_source_access_events')
            # A later rung and a new day use the persisted hold; NSE is still fetched.
            dispatch('2026-08-06', 2)
            dispatch('2026-08-07', 1)
            self.assertEqual(calls.count('BSE'), 1)
            self.assertGreaterEqual(calls.count('NSE'), 2)
            self.assertEqual(self.rows('SELECT * FROM exchange_source_access_events'), before)
            self.assertEqual(alert.call_count, 3)

    def test_unblocked_result_has_no_block_operator_action(self):
        payload = self.attempt(2, RESULT_FEED,
                               datasets=('nse_financial_results',))
        self.assertEqual(payload['status'], 'ok')
        self.assertIsNone(payload['access_blocked_action'])
