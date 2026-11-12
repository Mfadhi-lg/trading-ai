"""Offline safety contracts and a small explicitly disposable native workload."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from scrapper.research import postgresql_benchmark as benchmark


class BenchmarkContracts(unittest.TestCase):
    def test_candidate_matches_reviewed_migration(self):
        path = Path(__file__).resolve().parents[1] / 'db/postgresql_migrations/002_acceptance_history_index.sql'
        statement = ' '.join(line for line in path.read_text().splitlines() if not line.startswith('--'))
        self.assertEqual(' '.join(statement.rstrip(';').split()), ' '.join(benchmark.INDEX_SQL.split()))

    def test_scale_is_bounded(self):
        for args in ((0, 10, 7, 12), (10001, 10, 7, 12), (1, 11, 7, 12),
                     (1, 10, 1, 12), (1, 10, 7, 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                benchmark.Scale(*args)

    def test_comparison_preserves_exact_text_duplicates_and_nulls(self):
        rows = [{'v': '0001.2300'}, {'v': None}, {'v': '0001.2300'}]
        self.assertEqual(benchmark.digest(rows), benchmark.digest(list(reversed(rows))))
        self.assertNotEqual(benchmark.digest(rows), benchmark.digest(rows[:-1]))
        self.assertNotEqual(benchmark.digest(rows), benchmark.digest([{'v': '1.23'}, *rows[1:]]))

    def test_cli_fails_before_connect_without_all_disposable_guards(self):
        for env, flag in (({}, True), ({'TRADING_AI_ENV': 'test'}, True),
                          ({'TRADING_AI_ENV': 'test', 'TRADING_AI_POSTGRESQL_TEST_DSN': 'postgresql://localhost/test'}, False)):
            with self.subTest(env=env, flag=flag), patch.dict(os.environ, env, clear=True), patch.object(benchmark, 'run') as run:
                args = ['--output', '/tmp/unused-issue56.json']
                if flag:
                    args.append('--disposable-database')
                with self.assertRaises(SystemExit):
                    benchmark.main(args)
                run.assert_not_called()

    def test_cli_never_falls_back_to_database_url(self):
        with patch.dict(os.environ, {'TRADING_AI_ENV': 'test', 'DATABASE_URL': 'postgresql://localhost/production'}, clear=True), patch.object(benchmark, 'run') as run:
            with self.assertRaises(SystemExit):
                benchmark.main(['--output', '/tmp/unused-issue56.json', '--disposable-database'])
            run.assert_not_called()


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'), 'requires explicitly disposable PostgreSQL 16 DSN')
class BenchmarkNativeTests(unittest.TestCase):
    def test_small_workload_plans_semantics_concurrency_and_cleanup(self):
        from scrapper.db.postgresql import PostgreSQLSettings, connect
        settings = PostgreSQLSettings(os.environ['TRADING_AI_POSTGRESQL_TEST_DSN'])
        with connect(settings) as conn:
            before = conn.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'benchmark_56_%' ORDER BY nspname").fetchall()
        result = benchmark.run(settings, benchmark.Scale(10, 1, 2, 1))
        self.assertEqual(result['candle_rows'], 2520)
        self.assertEqual(result['initial_row_counts']['financial_observations'], 800)
        for name in result['baseline']:
            self.assertEqual(result['baseline'][name]['result_sha256'], result['indexed'][name]['result_sha256'])
            self.assertEqual(len(result['baseline'][name]['plans']), 2)
            self.assertIn('Shared Hit Blocks', result['baseline'][name]['plans'][0]['Plan'])
        self.assertEqual(result['baseline_concurrency']['writer_commits']['samples'], 2)
        self.assertEqual(len(result['candle_ingestion_baseline']), 12)
        self.assertTrue(all(entry['rows_per_second'] > 0 for entry in result['candle_ingestion_baseline']))
        self.assertTrue(all(entry['verified_rows'] == 10 for entry in result['candle_ingestion_baseline']))
        with connect(settings) as conn:
            after = conn.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'benchmark_56_%' ORDER BY nspname").fetchall()
        self.assertEqual(before, after)

    def test_schema_collision_does_not_drop_existing_schema(self):
        from scrapper.db.postgresql import PostgreSQLSettings, connect
        from psycopg import errors, sql
        import uuid
        settings = PostgreSQLSettings(os.environ['TRADING_AI_POSTGRESQL_TEST_DSN'])
        identity = uuid.uuid4()
        name = 'benchmark_56_' + identity.hex
        with connect(settings) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
        try:
            with patch.object(benchmark.uuid, 'uuid4', return_value=identity):
                with self.assertRaises(errors.DuplicateSchema):
                    benchmark.run(settings, benchmark.Scale(10, 1, 2, 1))
            with connect(settings) as conn:
                self.assertIsNotNone(conn.execute('SELECT to_regnamespace(%s) AS n', (name,)).fetchone()['n'])
        finally:
            with connect(settings) as conn:
                conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))

    def test_failure_cleans_only_owned_schema(self):
        from scrapper.db.postgresql import PostgreSQLSettings, connect
        settings = PostgreSQLSettings(os.environ['TRADING_AI_POSTGRESQL_TEST_DSN'])
        with connect(settings) as conn:
            before = conn.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'benchmark_56_%' ORDER BY nspname").fetchall()
        with patch.object(benchmark, 'seed', side_effect=RuntimeError('controlled failure')):
            with self.assertRaisesRegex(RuntimeError, 'controlled failure'):
                benchmark.run(settings, benchmark.Scale(10, 1, 2, 1))
        with connect(settings) as conn:
            after = conn.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'benchmark_56_%' ORDER BY nspname").fetchall()
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
