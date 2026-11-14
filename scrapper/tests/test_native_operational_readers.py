"""Operational reader adapters on UUID PostgreSQL schemas, with SQLite forbidden."""
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scrapper.tests.postgresql_fixtures import DisposablePostgreSQLTestCase


class NativeOperationalReaderTests(DisposablePostgreSQLTestCase):
    def test_campaign_snapshot_reader_uses_native_factory_and_repeatable_read(self):
        from scrapper.operations.workers import processing_campaign as campaign
        def factory(*args, **kwargs):
            self.assertTrue(kwargs['read_only'])
            return self.connection(read_only=True)
        with patch.object(campaign, 'selected_backend', return_value='postgresql'), \
             patch.object(campaign, 'connect', side_effect=factory), \
             patch('sqlite3.connect', side_effect=AssertionError('SQLite fallback reached')):
            with closing(campaign.read_connection(Path('/logical/unused.db'))) as conn:
                self.assertEqual(conn.execute('SHOW transaction_isolation').fetchone()[0], 'repeatable read')
                self.assertEqual(conn.execute('SHOW transaction_read_only').fetchone()[0], 'on')
                self.assertEqual(conn.execute('SELECT count(*) FROM artifact_contents').fetchone()[0], 0)

    def test_recovery_diagnostic_reads_native_retryable_jobs_without_sqlite(self):
        from scrapper.documents.recovery import diagnostics
        connections = []
        def factory(*args, **kwargs):
            self.assertTrue(kwargs['read_only'])
            conn = self.connection(read_only=True)
            self.addCleanup(conn.close)
            connections.append(conn)
            return conn
        with patch('scrapper.db.database.selected_backend', return_value='postgresql'), \
             patch('scrapper.db.database.connect', side_effect=factory), \
             patch('sqlite3.connect', side_effect=AssertionError('SQLite fallback reached')):
            report = diagnostics.diagnose('/logical/unused.db', None)
        self.assertIsInstance(report, dict)
        self.assertEqual(len(connections), 1)
        self.assertEqual(report['total_retryable'], 0)
        self.assertTrue(all(not conn.in_transaction for conn in connections))
