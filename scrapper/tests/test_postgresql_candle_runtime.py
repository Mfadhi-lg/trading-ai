"""Synthetic native archive consumer/cutover tests; never use a runtime DSN."""
from contextlib import ExitStack, closing
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import uuid

from scrapper.db import candle_inventory as repo, candle_inventory_transfer as transfer
from scrapper.db.postgresql import connect
from scrapper.tests.test_postgresql_backup import disposable_settings
from scrapper.tests.test_market_refresh import calendar, company, source, NOW, KEY
from experiments.historical_candle_archive_v1 import storage, access, rollout, acquire, control, repair_queue
from experiments.technical_fix_v2.update import persist_stock
from scrapper.market_refresh.planner import plan_refresh, digest
from scrapper.market_refresh.runner import execute_plan


class SelectionTests(unittest.TestCase):
    def test_unknown_selection_and_marker_refuse_before_sqlite_open(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            storage.dump(root / repo.MARKER, {'backend': 'postgresql'})
            with patch.dict(os.environ, {'TRADING_AI_CANDLE_BACKEND': 'sqlite'}), \
                 patch('sqlite3.connect', side_effect=AssertionError('must not open SQLite')):
                with self.assertRaisesRegex(RuntimeError, 'TARGET_MISMATCH'):
                    repo.open_inventory(root)
            with patch.dict(os.environ, {'TRADING_AI_CANDLE_BACKEND': 'invalid'}):
                with self.assertRaises(ValueError):
                    repo.open_inventory(root)


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'), 'requires disposable PostgreSQL 16')
class NativeArchiveTests(unittest.TestCase):
    def setUp(self):
        self.settings = disposable_settings(os.environ)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mount = Path(self.temp.name)
        self.root = self.mount / 'passport/trading-ai/market-data/historical-candle-archive-v1'
        self.evidence = self.mount / 'evidence'
        self.schema = 'runtime_' + uuid.uuid4().hex
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        for module in (storage, access):
            self.patches.enter_context(patch.object(module, 'discover', return_value=(self.mount, {'VolumeUUID': storage.UUID})))
            self.patches.enter_context(patch.object(module, 'verify'))
        with patch.dict(os.environ, {'TRADING_AI_CANDLE_BACKEND': 'sqlite'}):
            old = storage.Archive()
            old.db.close(); old.writer_lock.close()
        self.snapshot = self.mount / 'snapshot.sqlite3'
        self.report = transfer.export_snapshot(self.root / 'inventory.sqlite3', self.snapshot, self.root)
        with connect(self.settings) as conn:
            transfer.migrate(conn, self.schema)
        self.addCleanup(self.drop_schema)
        self.select('postgresql')
        self.patches.enter_context(patch.dict(os.environ, {
            'TRADING_AI_CANDLE_BACKEND': 'postgresql',
            'TRADING_AI_CANDLE_DATABASE_URL': self.settings.dsn,
            'TRADING_AI_CANDLE_SCHEMA': self.schema,
        }))
        self.patches.enter_context(patch.object(control, 'PAUSE', self.mount / 'pause'))
        self.patches.enter_context(patch.object(control, 'SHARED_PAUSE', self.mount / 'shared-pause'))
        self.patches.enter_context(patch.object(rollout, 'EVIDENCE', self.evidence))
        self.patches.enter_context(patch.object(acquire, 'OUT', self.evidence))
        self.patches.enter_context(patch.object(acquire, 'secure_supplier', return_value='synthetic-token'))
        self.patches.enter_context(patch.object(rollout, 'pause_aware_wait'))
        self.patches.enter_context(patch.object(repair_queue, 'refresh'))
        self.query = dict(company_id=1, instrument_key=KEY, unit='days', interval=1,
                          timeframe='Daily', from_date='2026-11-13', to_date='2026-11-13', role='TEST')
        self.query['request_id'] = storage.sha(json.dumps(self.query, sort_keys=True).encode())
        storage.dump(self.evidence / 'operation-manifest.json',
                     dict(companies=[company()], requests=[self.query]))
        self.fetch = Mock(side_effect=self.provider)
        self.patches.enter_context(patch.object(acquire, 'fetch', self.fetch))

    def drop_schema(self):
        from psycopg import sql
        with connect(self.settings) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def select(self, target):
        return repo.select_target(self.root, self.snapshot, expected_sha256=self.report['snapshot_sha256'],
                                  settings=self.settings, schema=self.schema, target=target)

    def provider(self, query, token, **kwargs):
        storage.dump(self.evidence / 'transport-receipts' / (query['request_id'] + '.json'), [{'transport_exit': 0}])
        return source(), 200

    def test_acquisition_recovery_access_refresh_revision_lifecycle_without_sqlite(self):
        from ops.host import historical_candle_lifecycle as lifecycle
        before = transfer.file_sha256(self.root / 'inventory.sqlite3')
        # Every runtime consumer must complete with the old database unavailable.
        with patch('sqlite3.connect', side_effect=AssertionError('native opened SQLite')):
            with self.assertRaisesRegex(RuntimeError, 'TEST_INTERRUPTION'):
                rollout.execute('operation', interrupt_after_raw_save=True)
            result = rollout.execute('operation')
            self.assertEqual(result['runs'][-1]['reused'], 1)
            self.fetch.assert_called_once()
            store = access.HistoricalStore()
            try:
                rows, receipts = store.native(1, 'Daily')
                self.assertEqual(len(rows), 1)
                self.assertEqual(str(rows[0]['close']), '101')
                self.assertEqual(receipts[0]['request_id'], self.query['request_id'])
                with self.assertRaises(repo.InventoryError):
                    store.db.execute("UPDATE requests SET attempts=9")
                store.db.rollback()
            finally:
                store.close()
            # Real incremental executor, provider mocked, canonical Parquet/index.
            plan = plan_refresh([company()], {KEY: {'validated_sessions': ['2026-11-11', '2026-11-12']}},
                                calendar=calendar, now=NOW)
            get = Mock(return_value=(source(), 200))
            clock = [NOW]
            def refresh(plan):
                return execute_plan(plan, self.mount / 'refresh', authorized_sha256=digest(plan),
                                    calendar=calendar, token_supplier=lambda: 'synthetic-token', fetch=get,
                                    clock=lambda: clock[0], sleep=lambda _: None)
            self.assertEqual(refresh(plan)['status'], 'COMPLETE')
            self.assertEqual(refresh(plan)['status'], 'COMPLETE')
            self.assertEqual(get.call_count, 1)
            revision = plan_refresh([company()], {KEY: {'validated_sessions': ['2026-11-11', '2026-11-12', '2026-11-13']}},
                                    calendar=calendar, now=NOW, revision_dates={KEY: ['2026-11-13']}, revision_id='review-1')
            clock[0] += timedelta(seconds=2)
            get.return_value = source(close=102), 200
            self.assertEqual(refresh(revision)['status'], 'COMPLETE')
            # Technical-update consumer persists an idempotent request separately.
            writer = storage.Archive()
            try:
                q = dict(self.query, request_id='technical-fixture')
                checksum, path = writer.raw(source())
                rec = dict(sha256=checksum, raw_path=path, received_at=NOW.isoformat())
                persist_stock(writer, q, rec, source())
                persist_stock(writer, q, rec, source())
                self.assertEqual(writer.db.execute('SELECT count(*) FROM requests').fetchone()[0], 4)
            finally:
                writer.db.close(); writer.writer_lock.close()
            with patch.object(lifecycle, 'discover', return_value=(self.mount, {})), \
                 patch.object(lifecycle, 'verify'), patch.object(lifecycle, 'pause', return_value={'status': 'PAUSED'}), \
                 patch.object(lifecycle, 'wake', return_value={'status': 'RUNNING'}), \
                 patch.object(lifecycle, 'dump'):
                self.assertEqual(lifecycle.verify_pause_resume()['status'], 'PASS')
        # Compare the complete public chart/coverage read result with a SQLite
        # metadata fixture using the exact same immutable provider files.
        native = access.HistoricalStore()
        mirror = access.HistoricalStore.__new__(access.HistoricalStore)
        mirror.root = self.root
        mirror.db = sqlite3.connect(':memory:')
        mirror.db.row_factory = sqlite3.Row
        try:
            mirror.db.executescript(transfer.DDL)
            for table in ('requests', 'datasets', 'revisions', 'candle_observations'):
                rows = native.db.execute('SELECT * FROM ' + table).fetchall()
                if rows:
                    mirror.db.executemany('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in rows[0]) + ')',
                                          [tuple(row) for row in rows])
            mirror.db.commit()
            self.assertEqual(native.read(1, 'Daily'), mirror.read(1, 'Daily'))
        finally:
            native.close(); mirror.close()
        self.assertEqual(before, transfer.file_sha256(self.root / 'inventory.sqlite3'))
        with self.assertRaisesRegex(RuntimeError, 'content mismatch'):
            self.select('sqlite')  # never discard native writes at rollback

    def test_uncertain_delivery_and_shutdown_retain_single_attempt(self):
        def fail(query, token, **kwargs):
            self.provider(query, token, **kwargs)
            raise OSError('synthetic transport interruption')
        self.fetch.side_effect = fail
        with self.assertRaises(OSError):
            rollout.execute('operation')
        with self.assertRaisesRegex(RuntimeError, 'UNCERTAIN_PROVIDER_DELIVERY'):
            rollout.execute('operation')
        self.assertEqual(self.fetch.call_count, 1)
        # Cooperative shutdown at a request boundary saves raw and metadata first.
        self.fetch.side_effect = self.provider
        control.PAUSE.touch()
        with self.assertRaises(control.PauseRequested):
            rollout.execute('operation')
        self.assertEqual(self.fetch.call_count, 1)

    def test_writer_fences_identity_drift_and_readonly_observer(self):
        writer = storage.Archive()
        try:
            writer.db.commit()  # server fence survives commits
            with self.assertRaisesRegex(RuntimeError, 'WRITER_BUSY'):
                storage.Archive()
            # A second archive root still cannot acquire the native target.
            other = self.mount / 'other'; other.mkdir()
            (other / repo.MARKER).write_bytes((self.root / repo.MARKER).read_bytes())
            with self.assertRaisesRegex(RuntimeError, 'WRITER_BUSY'):
                repo.NativeInventory(other, read_only=False, writer=True)
            observer = access.HistoricalStore()
            observer.close()
        finally:
            writer.db.close(); writer.writer_lock.close()
        marker = json.loads((self.root / repo.MARKER).read_text())
        marker['contract']['identity']['database'] = 'incorrect'
        storage.dump(self.root / repo.MARKER, marker)
        with patch('sqlite3.connect', side_effect=AssertionError('fallback')):
            with self.assertRaisesRegex(RuntimeError, 'IDENTITY_OR_SCHEMA'):
                storage.Archive()
        with self.assertRaises(ValueError):
            repo.NativeInventory(self.root, read_only=False, writer=False)

    def test_cutover_rollback_stale_snapshot_and_file_preservation(self):
        before = transfer.file_sha256(self.root / 'inventory.sqlite3'), transfer.file_inventory(self.root)
        self.select('sqlite')
        with patch.dict(os.environ, {'TRADING_AI_CANDLE_BACKEND': 'sqlite'}):
            reader = repo.open_inventory(self.root); reader.close()
        self.select('postgresql')
        self.assertEqual(before, (transfer.file_sha256(self.root / 'inventory.sqlite3'), transfer.file_inventory(self.root)))
        with closing(sqlite3.connect(self.root / 'inventory.sqlite3')) as old:
            old.execute("INSERT INTO requests(request_id,status) VALUES ('late','IN_FLIGHT')")
            old.commit()
        with self.assertRaisesRegex(RuntimeError, 'SQLITE_CHANGED'):
            self.select('postgresql')
        with self.assertRaisesRegex(RuntimeError, 'SQLITE_CHANGED'):
            self.select('sqlite')

    def test_bounded_query_parity_and_timings(self):
        rows = [(str(i), i % 20, 'SUCCESS', 1, '2026-11-13') for i in range(3000)]
        writer = storage.Archive()
        old = sqlite3.connect(self.root / 'inventory.sqlite3')
        try:
            insert = 'INSERT INTO requests(request_id,company_id,status,attempts,received_at) VALUES (?,?,?,?,?)'
            old.executemany(insert, rows); old.commit()
            writer.db.executemany(insert, rows); writer.db.commit()
            queries = [
                ("SELECT request_id,status,attempts FROM requests WHERE company_id=? AND status='SUCCESS' ORDER BY request_id LIMIT 100", (3,)),
                ('SELECT status,count(*),max(received_at) FROM requests GROUP BY status ORDER BY status', ()),
            ]
            timings = {}
            for index, (query, args) in enumerate(queries):
                results = []
                for name, db in [('sqlite', old), ('postgresql', writer.db)]:
                    start = time.perf_counter()
                    result = db.execute(query, args).fetchall()
                    timings[f'{index}:{name}'] = round((time.perf_counter() - start) * 1000, 3)
                    results.append(result)
                self.assertEqual(*results)
            print('Synthetic 3000-request bounded query milliseconds:', json.dumps(timings, sort_keys=True))
        finally:
            old.close(); writer.db.close(); writer.writer_lock.close()

    def test_database_failure_after_filesystem_receipt_recovers_without_get(self):
        original = repo.NativeInventory.commit
        injected = []
        def fail_once(db):
            receipt = self.root / 'receipts' / (self.query['request_id'] + '.json')
            if receipt.exists() and not injected:
                injected.append(True)
                # A real server statement failure leaves an aborted transaction.
                db.execute('SELECT nonexistent_column FROM requests')
            return original(db)
        with patch.object(repo.NativeInventory, 'commit', fail_once):
            with self.assertRaises(repo.InventoryError):
                rollout.execute('operation')
        self.assertTrue(injected)
        result = rollout.execute('operation')
        self.assertEqual(result['runs'][-1]['reused'], 1)
        self.assertEqual(self.fetch.call_count, 1)

    def test_cooperative_shutdown_drains_native_writer_without_signals(self):
        from ops.host import historical_candle_lifecycle as lifecycle
        storage.dump(self.evidence / 'owner-authorization.json', {})
        with patch.object(lifecycle, 'STATE', self.evidence), \
             patch.object(lifecycle, 'discover', return_value=(self.mount, {})), \
             patch.object(lifecycle, 'verify'), patch.object(lifecycle, 'legacy', return_value=[]), \
             patch.object(lifecycle, 'registered', return_value=None), patch('os.kill') as kill:
            writer = storage.Archive()
            try:
                with self.assertRaisesRegex(RuntimeError, 'not drained'):
                    lifecycle.pause(wait=0)
            finally:
                writer.db.close(); writer.writer_lock.close()
            self.assertEqual(lifecycle.pause(wait=0)['status'], 'PAUSED')
            with patch('sqlite3.connect', side_effect=AssertionError('native handoff opened SQLite')):
                self.assertEqual(lifecycle.handoff_legacy(wait=0)['status'], 'PAUSED')
            kill.assert_not_called()

    def test_selection_is_rechecked_after_acquiring_root_fence(self):
        original = Path.open
        switched = []
        def before_fence(path, *args, **kwargs):
            if path == self.root / '.writer.lock' and not switched:
                switched.append(True)
                self.select('sqlite')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', before_fence):
            with self.assertRaisesRegex(RuntimeError, 'TARGET_MISMATCH'):
                storage.Archive()
        self.assertTrue(switched)
