"""Generic retry boundaries, ownership, and compatible helper contracts."""
import ast
import importlib
import inspect
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from scrapper.documents.download import retry
from scrapper.documents.download.contracts import DownloadResult
from scrapper.db.documents import host_health
from scrapper.tests.database_fixtures import prepare_database
worker = importlib.import_module('scrapper.pipeline.run_document_download_worker')
NOW = '2026-10-04T00:00:00+00:00'


def result(status='rejected_http_status', code=429, header=None):
    return DownloadResult('https://example.com/report', None, code,
                          {} if header is None else {'retry-after': header},
                          (), None, None, None, None, status, 0)


class RetryOwnershipTests(unittest.TestCase):
    def test_facades_forward_arguments_and_legacy_hooks(self):
        for owner, names in [(retry, ('_retryable', '_blocks_host', '_transport_host_failure', '_iso_after', '_retry_after_seconds', '_error_text')),
                             (host_health, ('_record_host_block', '_record_host_success', '_record_transport_host_failure'))]:
            for name in names:
                with self.subTest(name=name):
                    fn = getattr(worker, name)
                    sig = inspect.signature(fn)
                    args = [object() for p in sig.parameters.values() if p.kind == p.POSITIONAL_OR_KEYWORD]
                    kwargs = {p.name: object() for p in sig.parameters.values() if p.kind == p.KEYWORD_ONLY}
                    token = object()
                    with patch.object(owner, name, return_value=token) as call:
                        self.assertIs(fn(*args, **kwargs), token)
                    self.assertEqual(call.call_args.args, tuple(args))
                    for key, value in kwargs.items():
                        self.assertIs(call.call_args.kwargs[key], value)
                    for key in set(call.call_args.kwargs) - set(kwargs):
                        self.assertIs(call.call_args.kwargs[key], getattr(worker, key))

    def test_retry_after_and_http_boundaries(self):
        for raw, expected in [(None, None), ('bad-date', None), ('-1', 60), ('0', 60), (' 61 ', 61), ('999999', 86400),
                              ('Sun, 04 Oct 2026 00:02:00 GMT', 120), ('Sat, 03 Oct 2026 00:00:00 GMT', 60)]:
            with self.subTest(raw=raw):
                self.assertEqual(retry._retry_after_seconds(result(header=raw), now_utc=NOW), expected)
                self.assertEqual(worker._retry_after_seconds(result(header=raw), now_utc=NOW), expected)
        for code in (200, 400, 401, 403, 404, 408, 425, 429, 499, 500, 503):
            with self.subTest(code=code):
                self.assertEqual(retry._retryable(result(code=code)), code in (401, 403, 408, 425, 429, 500, 503))
                self.assertEqual(retry._blocks_host(result(code=code)), code in (401, 403, 429))
                self.assertEqual(retry._transport_host_failure(result(code=code)), code >= 500)
        for status in ('network_error', 'timed_out', 'rejected_unexpected_html'):
            self.assertTrue(retry._retryable(result(status, None)))
            self.assertTrue(retry._transport_host_failure(result(status, None)))
            self.assertFalse(retry._blocks_host(result(status, None)))
        self.assertFalse(retry._retryable(result('validated', 200)))
        self.assertEqual(retry._iso_after('2026-10-04T00:00:00', 60), '2026-10-04T00:01:00+00:00')

    def test_persisted_cooldown_reset_and_repeated_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.db'
            prepare_database(path)
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            try:
                host_health._record_host_success(conn, host='absent', result=result('validated', 200), now_utc=NOW)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM artifact_download_host_health').fetchone()[0], 0)
                for code, expected in [(401, '2026-10-05T00:00:00+00:00'), (403, '2026-10-05T00:00:00+00:00'), (429, '2026-10-04T06:00:00+00:00')]:
                    self.assertEqual(host_health._record_host_block(conn, host=str(code), result=result(code=code), scope_id=None, now_utc=NOW), expected)
                host_health._record_host_block(conn, host='429', result=result(header='120'), scope_id='ignored-compatible-scope', now_utc=NOW)
                row = conn.execute("SELECT * FROM artifact_download_host_health WHERE host='429'").fetchone()
                self.assertEqual((row['consecutive_blocks'], row['retry_after_raw']), (2, '120'))
                host_health._record_host_success(conn, host='429', result=result('validated', 200), now_utc=NOW)
                row = conn.execute("SELECT * FROM artifact_download_host_health WHERE host='429'").fetchone()
                self.assertEqual((row['consecutive_blocks'], row['retry_after_raw'], row['blocked_until_utc']), (0, None, None))
                failure = result('network_error', None)
                for expected in (None, None, '2026-10-04T01:00:00+00:00'):
                    self.assertEqual(host_health._record_transport_host_failure(conn, host='429', result=failure, scope_id=None, now_utc=NOW), expected)
                with self.assertRaises(ValueError):
                    host_health._record_transport_host_failure(conn, host='429', result=failure, scope_id=None, now_utc=NOW, threshold=0)
                self.assertFalse(conn.in_transaction)
            finally:
                conn.close()

    def test_layers(self):
        for module in (retry, host_health):
            tree = ast.parse(Path(module.__file__).read_text())
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom):
                    self.assertFalse(set((n.module or '').split('.')) & {'pipeline', 'commands', 'view', 'operations', 'workflows'})
                    if module is retry:
                        self.assertNotIn('db', (n.module or '').split('.'))
                if module is retry and isinstance(n, ast.Attribute):
                    self.assertNotIn(n.attr, {'execute', 'executemany', 'commit'})

    def test_transitive_imports_are_inert(self):
        script = "import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode=True\ndef deny(*a, **k): raise AssertionError('forbidden side effect')\ndef audit(event,args):\n if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write during import')\n if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect=sqlite3.connect=subprocess.Popen=threading.Thread.start=deny\nimport scrapper.documents.download.retry\nimport scrapper.db.documents.host_health\nimport scrapper.pipeline.run_document_download_worker\n"
        completed = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
