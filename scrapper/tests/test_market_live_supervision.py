"""Deterministic host supervision tests; no launchd, sockets or provider calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from ops.market_live.service import (SessionWatchdog, active_session, authorized_config,
                                     launchagent, ownership, run_service, status, parent_admission)


def dt(value):
    return datetime.fromisoformat(value)


def calendar():
    return {'days': [
        {'day': '2026-11-13', 'verified': True, 'status': 'REGULAR_SCHEDULED',
         'sessions': [{'opens_at': '2026-11-13T09:15:00+05:30', 'closes_at': '2026-11-13T15:30:00+05:30'}]},
        {'day': '2026-11-14', 'verified': True, 'status': 'CLOSED', 'sessions': []},
        {'day': '2026-11-08', 'verified': False, 'status': 'SPECIAL_LIVE_SECURITY_GRID_UNRESOLVED', 'sessions': []},
    ]}


class SupervisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calendar = self.root/'calendar.json'
        self.calendar.write_text(json.dumps(calendar()))
        self.config_path = self.root/'config.json'
        self.identity = self.root/'identity.json'
        self.identity.write_text('{"synthetic":true}')
        self.config = dict(state_dir=str(self.root/'state'), calendar_path=str(self.calendar),
                           calendar_sha256=hashlib.sha256(self.calendar.read_bytes()).hexdigest(),
                           identity_path=str(self.identity),
                           identity_sha256=hashlib.sha256(self.identity.read_bytes()).hexdigest(),
                           provider_quota_path=str(self.root/'shared-quota.json'),
                           owner_approved=True, activation_ref='synthetic-test-only',
                           active_from='2026-11-01T00:00:00Z', active_until='2026-12-01T00:00:00Z')
        self.write_config()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config))
        self.digest = hashlib.sha256(self.config_path.read_bytes()).hexdigest()

    def test_reviewed_calendar_and_special_sessions(self):
        self.assertEqual(active_session(calendar(), dt('2026-11-13T09:15:00+05:30'))[0], 'ACTIVE')
        self.assertEqual(active_session(calendar(), dt('2026-11-13T15:30:00+05:30'))[0], 'IDLE')
        self.assertEqual(active_session(calendar(), dt('2026-11-14T10:00:00+05:30'))[0], 'IDLE')
        self.assertEqual(active_session(calendar(), dt('2026-11-08T18:00:00+05:30'))[0], 'BLOCKED')
        self.assertEqual(active_session(calendar(), dt('2027-01-01T10:00:00+05:30'))[0], 'BLOCKED')

    def test_sleep_recovery_records_missing_boundary_without_fabricating_rows(self):
        watchdog = SessionWatchdog()
        before = dt('2026-11-13T10:00:00+05:30')
        self.assertIsNone(watchdog.step(calendar(), before)['recovery_after'])
        after = watchdog.step(calendar(), dt('2026-11-13T11:00:00+05:30'))
        self.assertEqual(after['recovery_after'], before.isoformat())
        self.assertEqual(after['state'], 'ACTIVE')

    def test_duplicate_worker_and_refresh_locks_are_independent(self):
        with ownership(self.root):
            with self.assertRaises(BlockingIOError):
                with ownership(self.root):
                    self.fail('Duplicate admitted')
            with ownership(self.root, '.refresh.lock'):
                pass

    def test_authorization_exact_bytes_and_expiry(self):
        now = dt('2026-11-13T10:00:00+05:30')
        self.assertEqual(authorized_config(self.config_path, self.digest, now), self.config)
        with self.assertRaises(ValueError):
            authorized_config(self.config_path, 'wrong', now)
        with self.assertRaises(ValueError):
            authorized_config(self.config_path, self.digest, dt('2026-12-01T01:00:00Z'))
        self.config_path.write_text(self.config_path.read_text()+'\n')
        with self.assertRaises(ValueError):
            authorized_config(self.config_path, self.digest, now)

    def test_identity_snapshot_replacement_requires_new_approval(self):
        self.identity.write_text('{"synthetic":false}')
        with self.assertRaises(ValueError):
            authorized_config(self.config_path, self.digest, dt('2026-11-13T10:00:00+05:30'))

    def test_different_quota_ledgers_block_before_workers(self):
        child = self.root/'refresh-config.json'
        child.write_text(json.dumps(dict(provider_quota_path=str(self.root/'other-quota.json'))))
        self.config.update(refresh_config_path=str(child),
                           refresh_config_sha256=hashlib.sha256(child.read_bytes()).hexdigest(),
                           provider_quota_path=str(self.root/'shared-quota.json'))
        self.write_config()
        with self.assertRaisesRegex(ValueError, 'share the provider quota'):
            authorized_config(self.config_path, self.digest, dt('2026-11-13T10:00:00+05:30'))

    def test_per_request_parent_admission_rechecks_stop_expiry_and_cancellation(self):
        now = lambda: dt('2026-11-13T10:00:00+05:30')
        self.assertTrue(parent_admission(self.config_path, self.digest, now))
        root = Path(self.config['state_dir']); root.mkdir()
        (root/'stop.request').write_text('{}')
        self.assertFalse(parent_admission(self.config_path, self.digest, now))
        (root/'stop.request').unlink()
        self.assertFalse(parent_admission(self.config_path, self.digest,
                                         lambda: dt('2026-12-01T01:00:00Z')))
        stop = threading.Event(); stop.set()
        self.assertFalse(parent_admission(self.config_path, self.digest, now, stop))
        self.calendar.write_text(self.calendar.read_text()+'\n')
        self.assertFalse(parent_admission(self.config_path, self.digest, now))

    def test_closed_session_never_connects(self):
        calls = []
        result = run_service(self.config_path, self.digest, connect=lambda *args: calls.append(args),
                             clock=lambda: dt('2026-11-14T10:00:00+05:30'), wait=lambda _: None, max_ticks=2)
        self.assertEqual(result['status'], 'IDLE')
        self.assertFalse(calls)

    def test_changed_calendar_blocks_stream(self):
        self.calendar.write_text(self.calendar.read_text()+'\n')
        calls = []
        result = run_service(self.config_path, self.digest, connect=lambda *args: calls.append(args),
                             clock=lambda: dt('2026-11-13T10:00:00+05:30'), wait=lambda _: None, max_ticks=1)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertFalse(calls)

    def test_session_close_stops_stream_and_sleep_restarts_with_recovery(self):
        times = iter([dt('2026-11-13T10:00:00+05:30'),
                      dt('2026-11-13T10:00:00+05:30'),
                      dt('2026-11-13T11:00:00+05:30'),
                      dt('2026-11-13T15:30:00+05:30')])
        calls = []
        entered = threading.Event()

        def connect(config, stop, recovery):
            calls.append(recovery)
            entered.set()
            stop.wait(3)

        def wait(_):
            self.assertTrue(entered.wait(1))

        result = run_service(self.config_path, self.digest, connect=connect,
                             clock=lambda: next(times), wait=wait, max_ticks=3)
        self.assertEqual(result['status'], 'IDLE')
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], '2026-11-13T10:00:00+05:30')

    def test_exhausted_stream_is_not_restarted_each_poll(self):
        calls = []
        exited = threading.Event()

        def connect(*args):
            calls.append(1)
            exited.set()

        result = run_service(self.config_path, self.digest, connect=connect,
                             clock=lambda: dt('2026-11-13T10:00:00+05:30'),
                             wait=lambda _: exited.wait(1), max_ticks=3)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'BLOCKED')

    def test_retained_stop_survives_launchd_restart(self):
        state = Path(self.config['state_dir'])
        state.mkdir()
        (state/'stop.request').write_text('{}')
        calls = []
        result = run_service(self.config_path, self.digest, connect=lambda *args: calls.append(args),
                             clock=lambda: dt('2026-11-13T10:00:00+05:30'), wait=lambda _: None, max_ticks=2)
        self.assertFalse(calls)
        self.assertEqual(result['status'], 'STOPPED')
        self.assertTrue((state/'stop.request').exists())

    def test_background_refresh_does_not_block_stream_watchdog(self):
        child = self.root/'refresh-config.json'
        quota = str(self.root/'shared-quota.json')
        child.write_text(json.dumps(dict(provider_quota_path=quota)))
        self.config.update(refresh_config_path=str(child),
                           refresh_config_sha256=hashlib.sha256(child.read_bytes()).hexdigest(),
                           provider_quota_path=quota, stop_timeout_seconds=.01)
        self.write_config()
        streaming = threading.Event()
        refreshing = threading.Event()
        release_refresh = threading.Event()
        self.addCleanup(release_refresh.set)

        def connect(config, stop, recovery):
            streaming.set()
            stop.wait(3)

        def refresh(*args, **kwargs):
            refreshing.set()
            release_refresh.wait(3)
            return {'status': 'COMPLETE'}

        waits = []
        def wait(_):
            self.assertTrue(streaming.wait(1))
            self.assertTrue(refreshing.wait(1))
            waits.append(1)
            if len(waits) == 2:
                release_refresh.set()

        result = run_service(self.config_path, self.digest, connect=connect, refresh=refresh,
                             clock=lambda: dt('2026-11-13T10:00:00+05:30'), wait=wait, max_ticks=2)
        self.assertEqual(result['status'], 'ACTIVE')
        self.assertTrue(result['worker_running'])
        self.assertTrue(result['refresh_running'])
        release_refresh.set()

    def test_status_does_not_create_state_or_claim_missing_observations_fresh(self):
        result = status(self.config_path)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertFalse(Path(self.config['state_dir']).exists())

    def test_monitor_reports_message_age_and_completed_candle_receipts(self):
        root = Path(self.config['state_dir']); root.mkdir()
        now = dt('2026-11-13T10:00:00+05:30')
        (root/'supervisor.json').write_text(json.dumps(dict(observed_at=now.isoformat(), status='ACTIVE')))
        (root/'stream_status.json').write_text(json.dumps(dict(status='LIVE', last_received_at='2026-11-13T09:58:00+05:30')))
        (root/'chart.json').write_text(json.dumps(dict(instruments=[dict(instrument_key='synthetic', day='2026-11-13',
            status='GAP', missing_minutes=['synthetic-minute'], completed={'1-hour': []}, latest_completed_minute=None)])))
        with ownership(root):
            result = status(self.config_path, now)
        self.assertEqual(result['status'], 'STALE')
        self.assertEqual(result['last_message_age_seconds'], 120)
        self.assertEqual(result['chart_coverage'][0]['missing_minutes'], 1)
        self.assertIsNone(result['chart_coverage'][0]['latest_completed_candles']['1-hour'])

    def test_launchd_template_is_independent_and_has_no_credentials(self):
        value = launchagent('/python', '/repo', '/config.json', 'digest', '/logs')
        self.assertEqual(value['Label'], 'com.trading-ai.market-live')
        self.assertEqual(value['LimitLoadToSessionType'], 'Aqua')
        self.assertIn('supervise', value['ProgramArguments'])
        self.assertEqual(value['EnvironmentVariables'], {'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertNotIn('automatic-acquisition', json.dumps(value))


if __name__ == '__main__':
    unittest.main()
