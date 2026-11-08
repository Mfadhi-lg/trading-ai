"""Offline checks of the exact finite authorization; no production execution."""
import json,unittest
from datetime import timedelta
from pathlib import Path
from phase4_followup import validate_window,START,END,PREPARED

class FollowupAuthorizationTests(unittest.TestCase):
    def setUp(self):self.config=json.loads(PREPARED.read_text())
    def test_exact_fixed_window_and_preflight_interval(self):
        validate_window(self.config,START-timedelta(minutes=2))
        validate_window(self.config,START+timedelta(minutes=3))
    def test_early_or_late_activation_cannot_replay_or_reschedule(self):
        for now in (START-timedelta(days=1),START+timedelta(minutes=4),END):
            with self.subTest(now=now),self.assertRaises(RuntimeError):validate_window(self.config,now)
    def test_duration_or_date_change_is_denied(self):
        for key,value in [('verification_window_hours',24),('expires_at_utc',(END+timedelta(hours=1)).isoformat()),('activated_at_utc',(START+timedelta(days=1)).isoformat())]:
            config=dict(self.config);config[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):validate_window(config,START)
    def test_explicit_restart_keeps_fixed_expiry(self):
        validate_window(self.config,START+timedelta(minutes=20),authorized_restart=True)
        for now in (START-timedelta(days=1),END,END+timedelta(seconds=1)):
            with self.subTest(now=now),self.assertRaises(RuntimeError):
                validate_window(self.config,now,authorized_restart=True)
        config=dict(self.config,expires_at_utc=(END+timedelta(hours=1)).isoformat())
        with self.assertRaises(RuntimeError):validate_window(config,START,authorized_restart=True)
    def test_prepared_scopes_have_no_historical_or_concurrency_expansion(self):
        self.assertEqual(self.config['historical_limit'],0)
        self.assertEqual(self.config['concurrency'],1)
        self.assertEqual(self.config['download_limit'],1)
        self.assertEqual(self.config['processing_limit'],1)
