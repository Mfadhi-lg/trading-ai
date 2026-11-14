"""Offline financial entry-point contracts; no runtime or provider access."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from ops.host import diagnose_financial_pending as pending
from ops.host import financial_queue_eligibility as eligibility
from ops.host import runtime_status as status
import financial_diagnostic_reader as reader
from scrapper.tests.test_postgresql_schema import synthetic_sqlite


class FinancialDiagnosticTests(unittest.TestCase):
    def output(self, main, args, code=0):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(args), code, out.getvalue())
        return json.loads(out.getvalue())

    def test_native_error_never_touches_sqlite_or_its_ownership_gate(self):
        with patch.dict(os.environ, {'TRADING_AI_DB_BACKEND':'postgresql'}), \
             patch.object(reader.postgresql_runtime, 'read_connection', side_effect=RuntimeError('secret')), \
             patch('sqlite3.connect', side_effect=AssertionError('SQLite opened')) as sqlite_open, \
             patch.object(reader, '_assert_safe_empty_wal', side_effect=AssertionError('SQLite guard')) as sqlite_guard, \
             patch.object(reader.fcntl, 'flock', side_effect=AssertionError('SQLite lock')) as sqlite_lock:
            for main in (pending.main, eligibility.main):
                report = self.output(main, [], 1)
                self.assertEqual(report['status'], 'unknown')
                self.assertNotIn('secret', json.dumps(report))
            self.assertEqual(status.native_migrations()['status'], 'unknown')
            sqlite_open.assert_not_called()
            sqlite_guard.assert_not_called()
            sqlite_lock.assert_not_called()

    def test_explicit_snapshot_is_labeled_and_preserved_under_native_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/'source #?.db'
            source = synthetic_sqlite()
            with sqlite3.connect(db) as disk:
                source.backup(disk)
            source.close(); disk.close()
            before = db.read_bytes()
            with patch.dict(os.environ, {'TRADING_AI_DB_BACKEND':'postgresql'}), \
                 patch.object(reader.postgresql_runtime, 'read_connection', side_effect=AssertionError('Native opened')):
                for main in (pending.main, eligibility.main):
                    report = self.output(main, ['--sqlite-snapshot', str(db)])
                    self.assertEqual(report['database_source']['mode'], 'frozen_sqlite_snapshot')
                schema = status.migrations(status.ROOT, db, sqlite_snapshot=True)
                self.assertEqual(schema['value']['mode'], 'frozen_sqlite_snapshot')
                self.assertEqual(schema['status'], 'warning')  # Synthetic source has an empty ledger.
            self.assertEqual(db.read_bytes(), before)
            self.assertEqual(list(db.parent.iterdir()), [db])
            # Sidecars cannot be ignored even in explicitly frozen mode.
            Path(str(db)+'-wal').write_bytes(b'not a consistent snapshot')
            self.assertEqual(self.output(pending.main, ['--sqlite-snapshot',str(db)], 1)['status'], 'unknown')

    def test_native_flag_requires_configuration_and_default_never_probes_native(self):
        with patch.dict(os.environ, {'TRADING_AI_DB_BACKEND':'postgresql'}), \
             patch.object(status, 'native_migrations') as probe, \
             patch.object(status, 'repository', return_value=status.claim()):
            self.output(status.main, [])
            probe.assert_not_called()
            probe.return_value = status.claim(status='unknown')
            self.output(status.main, ['--native'])
            probe.assert_called_once()
        with patch.dict(os.environ, {'TRADING_AI_DB_BACKEND':'sqlite'}), \
             patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            status.main(['--native'])

    def test_pending_period_uses_reporting_calendar(self):
        source = synthetic_sqlite(); source.row_factory = sqlite3.Row
        self.addCleanup(source.close)
        now = '2027-02-15T12:00:00+00:00'
        self.assertEqual(pending.inspect(source, now)['period'], '2026-12')

    def test_wall_clock_budget_interrupts_and_restores_handler(self):
        from ops.host.acquisition_live_reader import wall_clock_budget
        import signal
        old = signal.getsignal(signal.SIGALRM)
        with self.assertRaises(TimeoutError), wall_clock_budget(.01):
            time.sleep(.1)
        self.assertEqual(signal.getsignal(signal.SIGALRM), old)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)

    def test_native_helper_refuses_unselected_backend_before_open(self):
        with patch.dict(os.environ, {'TRADING_AI_DB_BACKEND':'sqlite'}), \
             patch('sqlite3.connect', side_effect=AssertionError('SQLite opened')):
            self.assertEqual(status.native_migrations()['status'], 'unknown')

    def test_financial_scripts_import_in_mount_layout_without_ops_package(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            layout = Path(directory)
            for leaf in ('host','activation','app'):
                (layout/leaf).mkdir()
            for name in ('diagnose_financial_pending.py','financial_queue_eligibility.py',
                         'financial_diagnostic_reader.py','acquisition_live_reader.py'):
                shutil.copyfile(root/'ops/host'/name, layout/'host'/name)
            for name in ('postgresql_runtime.py','sqlite_sidecar_guard.py'):
                shutil.copyfile(root/'ops/activation'/name, layout/'activation'/name)
            (layout/'app/scrapper').symlink_to(root/'scrapper', target_is_directory=True)
            environment = {**os.environ, 'PYTHONPATH':str(layout/'app')}
            for name in ('diagnose_financial_pending.py','financial_queue_eligibility.py'):
                result = subprocess.run([sys.executable, '-B', str(layout/'host'/name), '--help'],
                                        cwd=layout, env=environment, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--sqlite-snapshot', result.stdout)
