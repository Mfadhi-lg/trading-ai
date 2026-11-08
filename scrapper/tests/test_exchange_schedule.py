"""Schedule ownership, compatibility hooks and inert import contracts."""
import inspect
import unittest
from unittest.mock import patch
from scrapper.db import exchange_repo as facade
from scrapper.db.exchange import schedule as owner
from scrapper.pipeline import scheduler as workflow

NAMES = ('record_schedule_run', 'datasets_published_on', 'schedule_day')

class ScheduleOwnerTests(unittest.TestCase):
    def test_facade_forwards_arguments_and_legacy_hooks(self):
        for name in NAMES:
            signature = inspect.signature(getattr(facade, name))
            values = {key: object() for key in signature.parameters}
            args = [values[key] for key, param in signature.parameters.items()
                    if param.kind == param.POSITIONAL_OR_KEYWORD]
            kwargs = {key: values[key] for key, param in signature.parameters.items()
                      if param.kind == param.KEYWORD_ONLY}
            result = object()
            with patch.object(owner, name, return_value=result) as call:
                self.assertIs(getattr(facade, name)(*args, **kwargs), result)
            self.assertEqual(call.call_args.args, tuple(args))
            for key, value in kwargs.items():
                self.assertIs(call.call_args.kwargs[key], value)
            for key in set(call.call_args.kwargs) - set(kwargs):
                self.assertIs(call.call_args.kwargs[key], getattr(facade, key))

    def test_scheduler_uses_schedule_owner(self):
        for name in ('record_schedule_run', 'datasets_published_on', 'schedule_day', 'SATISFYING_OUTCOMES'):
            self.assertIs(getattr(workflow, name), getattr(owner, name))

    def test_owner_import_is_inert(self):
        import subprocess,sys
        script="import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode = True\ndef deny(*args, **kwargs): raise AssertionError('forbidden import side effect')\ndef audit(event, args):\n    if event == 'open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):\n        raise AssertionError('write during import')\n    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):\n        raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny\nimport scrapper.db.exchange.schedule\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

class ScheduleLedgerContractTests(unittest.TestCase):
    def test_upsert_roles_outcomes_and_read_only_queries(self):
        import sqlite3
        from scrapper.tests.database_fixtures import prepare_database
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'ledger.db'
            prepare_database(db)
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            def record(api, **override):
                fields = dict(dataset='nse_equity_master', scheduled_for='2026-10-04',
                              attempt=1, outcome='not_published', snapshot_id=None,
                              error='first', started_at_utc='2026-10-04T01:00:00+00:00',
                              finished_at_utc='2026-10-04T01:01:00+00:00')
                fields.update(override)
                api.record_schedule_run(conn, **fields)
            record(facade)
            record(owner, role='revision', outcome='published_changed', result_period='31-Mar-2026')
            self.assertEqual(owner.datasets_published_on(conn, '2026-10-04'), set())
            record(facade, outcome='published_unchanged', error='updated', result_period='30-Jun-2026')
            rows = owner.schedule_day(conn, '2026-10-04')
            self.assertEqual(len(rows), 2)
            primary = next(row for row in rows if row['role'] == 'primary')
            self.assertEqual((primary['error'], primary['result_period']), ('updated', '30-Jun-2026'))
            before = conn.total_changes
            for outcome in owner.SCHEDULE_OUTCOMES:
                record(owner, dataset=outcome, outcome=outcome)
            expected = {'nse_equity_master', *owner.SATISFYING_OUTCOMES}
            self.assertEqual(facade.datasets_published_on(conn, '2026-10-04'), expected)
            before = conn.total_changes
            self.assertEqual(facade.schedule_day(conn, '2026-10-04'), owner.schedule_day(conn, '2026-10-04'))
            self.assertEqual(conn.total_changes, before)
            conn.close()

    def test_invalid_values_and_legacy_constant_patch_write_nothing(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        fields = dict(dataset='nse_equity_master', scheduled_for='2026-10-04',
                      attempt=1, outcome='published_changed', snapshot_id=None,
                      error='', started_at_utc='start', finished_at_utc='finish')
        for api in (facade, owner):
            for override in ({'outcome': 'unknown'}, {'role': 'unknown'}):
                with self.assertRaises(ValueError):
                    api.record_schedule_run(conn, **{**fields, **override})
        with patch.object(facade, 'SCHEDULE_OUTCOMES', ()):
            with self.assertRaises(ValueError):
                facade.record_schedule_run(conn, **fields)
        self.assertFalse(conn.in_transaction)
        self.assertEqual(conn.total_changes, 0)
        conn.close()
