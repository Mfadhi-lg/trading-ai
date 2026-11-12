"""Existing exchange task repository parity and isolated concurrent claims."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import threading
import unittest
import uuid

from scrapper.db.exchange import tasks

NOW = '2026-11-09T00:00:00+00:00'
LATER = '2026-11-09T00:10:00+00:00'


class ExchangeTaskContract:
    def enqueue(self, conn, *, day='2026-11-09', attempt=1, priority=10):
        return tasks.enqueue(conn, kind='attempt', scheduled_for=day,
                             attempt=attempt, priority=priority, now=NOW)

    def test_idempotency_priority_and_exact_payload(self):
        with self.session() as conn:
            first = self.enqueue(conn)
            again = self.enqueue(conn, priority=0)
            last = self.enqueue(conn)
            self.assertEqual(first['task_id'], again['task_id'])
            self.assertEqual(last['priority'], 0)
            self.assertEqual(last['generation'], 0)
            self.assertEqual(tasks.pending_count(conn), 1)
            claim = tasks.claim(conn, owner='worker', now=NOW)
            tasks.finish(conn, claim, now=NOW,
                         result={'exact': '0.00000000000000000001', 'raw': '\u20b9 1,000'})
            self.assertEqual(tasks.pending_count(conn), 0)
            row = self.load(conn, claim['task_id'])
            self.assertIn('0.00000000000000000001', row['result_json'])
            self.assertEqual(row['state'], 'DONE')
            self.assertIsNone(row['owner'])
            self.assertIsNone(row['lease_until_utc'])
            self.assertIsNone(tasks.claim(conn, owner='worker', now=NOW))

    def test_ordering_and_expired_generation_fence(self):
        with self.session() as conn:
            low = self.enqueue(conn)
            high = self.enqueue(conn, day='2026-11-08', priority=0)
            first = tasks.claim(conn, owner='old', now=NOW, seconds=1)
            self.assertEqual(first['task_id'], high['task_id'])
            second = tasks.claim(conn, owner='other', now=NOW)
            self.assertEqual(second['task_id'], low['task_id'])
            replacement = tasks.claim(conn, owner='new', now=LATER)
            self.assertEqual(replacement['task_id'], high['task_id'])
            self.assertEqual(replacement['generation'], first['generation'] + 1)
            with self.assertRaises(tasks.TaskOwnershipLost):
                tasks.finish(conn, first, now=LATER, result={'wrong': True})
            # The failed operation remains recoverable in the same connection.
            tasks.finish(conn, replacement, now=LATER, error='controlled failure')
            row = self.load(conn, replacement['task_id'])
            self.assertEqual((row['state'], row['error']), ('FAILED', 'controlled failure'))
            with self.assertRaises(tasks.TaskOwnershipLost):
                tasks.finish(conn, replacement, now=LATER)

    def test_invalid_inputs_do_not_enqueue(self):
        with self.session() as conn:
            for kwargs in ({'attempt': 0}, {'priority': 20}, {'day': 'bad'}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    self.enqueue(conn, **kwargs)
            for seconds in (0, -1, 86401):
                with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                    tasks.claim(conn, owner='worker', now=NOW, seconds=seconds)
            self.assertEqual(tasks.pending_count(conn), 0)


class SQLiteExchangeTaskTests(ExchangeTaskContract, unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        migration = Path(__file__).resolve().parents[1] / 'db/migrations/073_exchange_coordinator_tasks.sql'
        self.conn.executescript(migration.read_text())

    @contextmanager
    def session(self):
        yield self.conn

    def load(self, conn, task_id):
        return conn.execute('SELECT * FROM exchange_coordinator_tasks WHERE task_id=?',
                            (task_id,)).fetchone()


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'),
                     'requires an explicitly disposable PostgreSQL 16 DSN')
class PostgreSQLExchangeTaskTests(ExchangeTaskContract, unittest.TestCase):
    def setUp(self):
        from psycopg import sql
        from scrapper.db.postgresql import PostgreSQLSettings, connect
        from scrapper.db.postgresql_schema import migrate
        self.sql = sql
        self.connect = lambda: connect(PostgreSQLSettings(os.environ['TRADING_AI_POSTGRESQL_TEST_DSN']))
        self.schema = 'exchange_task_test_' + uuid.uuid4().hex
        self.addCleanup(self.drop_schema)
        with self.connect() as conn:
            migrate(conn, self.schema)

    def drop_schema(self):
        with self.connect() as conn:
            conn.execute(self.sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(
                self.sql.Identifier(self.schema)))

    @contextmanager
    def session(self):
        with self.connect() as conn:
            conn.execute(self.sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(
                self.sql.Identifier(self.schema)))
            yield conn

    def load(self, conn, task_id):
        return conn.execute('SELECT * FROM exchange_coordinator_tasks WHERE task_id=%s',
                            (task_id,)).fetchone()

    def test_caller_rollback_covers_enqueue_claim_and_finish(self):
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with self.session() as conn:
                self.enqueue(conn)
                claim = tasks.claim(conn, owner='worker', now=NOW)
                tasks.finish(conn, claim, now=NOW, result={})
                raise RuntimeError('rollback')
        with self.session() as conn:
            self.assertEqual(tasks.pending_count(conn), 0)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM exchange_coordinator_tasks').fetchone()['n'], 0)

    def test_transferred_lease_offsets_compare_instants(self):
        with self.session() as conn:
            self.enqueue(conn)
            original = tasks.claim(conn, owner='old', now=NOW)
            # 05:31 IST is 00:01 UTC, before LATER despite its lexical value.
            conn.execute("UPDATE exchange_coordinator_tasks SET lease_until_utc=%s WHERE task_id=%s",
                         ('2026-11-09T05:31:00+05:30', original['task_id']))
        with self.session() as conn:
            replacement = tasks.claim(conn, owner='new', now=LATER)
            self.assertEqual(replacement['task_id'], original['task_id'])
            self.assertEqual(replacement['generation'], original['generation'] + 1)

    def test_idle_native_mutations_require_managed_transaction(self):
        import psycopg
        with psycopg.connect(os.environ['TRADING_AI_POSTGRESQL_TEST_DSN'], autocommit=True) as conn:
            for operation in (lambda: self.enqueue(conn),
                              lambda: tasks.claim(conn, owner='worker', now=NOW),
                              lambda: tasks.finish(conn, {'task_id': 1, 'owner': 'worker', 'generation': 1}, now=NOW)):
                with self.assertRaisesRegex(ValueError, 'managed PostgreSQL transaction'):
                    operation()

    def test_overlapping_claims_skip_locked_rows_and_never_duplicate(self):
        with self.session() as conn:
            ids = {self.enqueue(conn, attempt=attempt)['task_id'] for attempt in (1, 2, 3)}
        claimed = threading.Barrier(3)

        def worker(number):
            with self.session() as conn:
                task = tasks.claim(conn, owner='worker-' + str(number), now=NOW)
                # Hold each transaction open until all other workers claimed.
                claimed.wait(timeout=10)
                return task

        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(worker, range(3)))
        self.assertEqual({task['task_id'] for task in results}, ids)
        self.assertEqual({task['generation'] for task in results}, {1})
        with self.session() as conn:
            self.assertIsNone(tasks.claim(conn, owner='extra', now=NOW))
            self.assertEqual(tasks.pending_count(conn), 3)

    def test_concurrent_enqueues_share_identity_and_keep_highest_priority(self):
        ready = threading.Barrier(2)

        def worker(priority):
            with self.session() as conn:
                ready.wait(timeout=10)
                return self.enqueue(conn, priority=priority)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(worker, (10, 0)))
        self.assertEqual(len({row['task_id'] for row in results}), 1)
        with self.session() as conn:
            self.assertEqual(tasks.pending_count(conn), 1)
            self.assertEqual(self.load(conn, results[0]['task_id'])['priority'], 0)

    def test_failed_claim_transaction_releases_row_lock(self):
        with self.session() as conn:
            original = self.enqueue(conn)
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with self.session() as conn:
                task = tasks.claim(conn, owner='aborted', now=NOW)
                self.assertEqual(task['generation'], 1)
                raise RuntimeError('rollback')
        with self.session() as conn:
            replacement = tasks.claim(conn, owner='next', now=NOW)
            self.assertEqual(replacement['task_id'], original['task_id'])
            self.assertEqual(replacement['generation'], 1)
