import io
import sqlite3
import unittest
from rich.console import Console
from scrapper.view.browser_rating_monitor import fetch_rating_data, render_rating_panel


class RatingMonitorTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE browser_rating_recovery_jobs(target_host TEXT,status TEXT,content_id INTEGER,
            company_name TEXT,updated_at_utc TEXT,lease_expires_at_utc TEXT,error TEXT,
            evidence_json TEXT NOT NULL DEFAULT '{}');
          CREATE TABLE artifact_contents(content_id INTEGER,created_at_utc TEXT);
          CREATE TABLE browser_rating_recovery_hosts(target_host TEXT,blocked_until_utc TEXT);
          CREATE TABLE browser_rating_recovery_workers(worker_id TEXT,status TEXT,heartbeat_at_utc TEXT);
          INSERT INTO artifact_contents VALUES(1,'2000-01-01T00:00:00Z');
          INSERT INTO browser_rating_recovery_jobs VALUES('www.icra.in','SUCCEEDED',1,'Example',datetime('now'),NULL,NULL,'{}');
          INSERT INTO browser_rating_recovery_workers VALUES('host','DOWNLOADING','2000-01-01T00:00:00Z');
        ''')

    def tearDown(self):
        self.db.close()

    def test_processing_updates_do_not_count_as_new_downloads(self):
        data = fetch_rating_data(self.db)
        self.assertEqual(data['hosts'][0]['saved'], 1)
        self.assertEqual(data['hosts'][0]['recent'], 0)
        self.assertEqual(data['hosts'][0]['last_saved'], '2000-01-01T00:00:00Z')

    def test_stale_heartbeat_is_not_shown_as_running(self):
        output = io.StringIO()
        Console(file=output, width=160).print(render_rating_panel(fetch_rating_data(self.db)))
        self.assertIn('STALE', output.getvalue())

    def test_missing_publisher_file_is_visible_separately_from_errors(self):
        self.db.execute('''INSERT INTO browser_rating_recovery_jobs VALUES(
            'www.icra.in','FAILED',NULL,'Missing',datetime('now'),NULL,'NO FILE TO VIEW',
            '{"resolution":"PUBLISHER_SOURCE_UNAVAILABLE"}')''')
        self.db.execute('''INSERT INTO browser_rating_recovery_jobs VALUES(
            'www.icra.in','FAILED',NULL,'Retry',datetime('now'),NULL,'HTTP 503','{}')''')
        host = fetch_rating_data(self.db)['hosts'][0]
        self.assertEqual(host['unavailable'], 1)
        self.assertEqual(host['failed'], 1)

    def test_monitor_handles_database_without_backfill(self):
        db = sqlite3.connect(':memory:')
        try:
            self.assertEqual(fetch_rating_data(db)['hosts'], [])
        finally:
            db.close()
