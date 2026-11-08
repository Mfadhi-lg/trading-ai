"""Compatibility, transaction refusal and lease/attempt history contracts."""
import ast
import inspect
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from scrapper.db import document_recovery_repo as facade
from scrapper.db.documents.recovery import attempts
from scrapper.tests import test_da06_recovery_registry as registry
NOW = registry.NOW

NAMES = ('_recovery_source_group', '_remaining_source_status', '_after',
         'claim_recovery_target', 'finish_recovery_attempt')

class RecoveryAttemptOwnershipTests(unittest.TestCase):
    setUp = registry.RecoveryRegistryTests.setUp
    tearDown = registry.RecoveryRegistryTests.tearDown
    _blocked_artifact = registry.RecoveryRegistryTests._blocked_artifact

    def target(self):
        self._blocked_artifact('Lease Limited', 'LEASE', 'https://www.icra.in/Rationale/lease.pdf')
        facade.build_recovery_targets(self.conn, observed_at_utc=NOW)

    def test_facade_preserves_arguments_and_patch_points(self):
        for name in NAMES:
            fn = getattr(facade, name)
            sig = inspect.signature(fn)
            args = [object() for p in sig.parameters.values() if p.kind == p.POSITIONAL_OR_KEYWORD]
            kwargs = {p.name: object() for p in sig.parameters.values() if p.kind == p.KEYWORD_ONLY}
            token = object()
            with patch.object(attempts, name, return_value=token) as owner:
                self.assertIs(fn(*args, **kwargs), token)
            self.assertEqual(owner.call_args.args, tuple(args))
            self.assertEqual(owner.call_args.kwargs, dict(kwargs, _hooks=facade))

    def test_clock_hook_is_local_to_facade_call(self):
        original = attempts.datetime
        with patch.object(facade, 'datetime') as clock:
            clock.fromisoformat.return_value.tzinfo = object()
            clock.fromisoformat.return_value.__add__.return_value.isoformat.return_value = 'patched'
            self.assertEqual(facade._after(NOW, 60), 'patched')
            self.assertIs(attempts.datetime, original)
        self.assertEqual(attempts._after('2026-08-16T00:00:00', 60), '2026-08-16T00:01:00+00:00')

    def test_invalid_claim_and_finish_refuse_before_writes(self):
        for module in (facade, attempts):
            with self.assertRaises(ValueError):
                module.claim_recovery_target(None, source='invalid', worker='w', now_utc=NOW)
            with self.assertRaises(ValueError):
                module.claim_recovery_target(None, source='nse', worker='w', now_utc=NOW, lease_seconds=0)
            with self.assertRaises(ValueError):
                module.finish_recovery_attempt(None, recovery_attempt_id=1, worker='w', status='invalid', completed_at_utc=NOW)

    def test_expiry_retains_attempts_and_wrong_owner_finish_rolls_back(self):
        self.target()
        first = facade.claim_recovery_target(self.conn, source='nse', worker='old', now_utc=NOW, lease_seconds=1)
        self.assertIsNone(attempts.claim_recovery_target(self.conn, source='bse', worker='other', now_utc=NOW))
        second = attempts.claim_recovery_target(self.conn, source='nse', worker='new', now_utc='2026-08-16T00:00:02+00:00')
        self.assertEqual(first[0].recovery_target_id, second[0].recovery_target_id)
        self.assertNotEqual(first[1], second[1])
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT attempt_number,status FROM artifact_recovery_attempts ORDER BY recovery_attempt_id')], [(1,'SEARCHING'),(2,'SEARCHING')])
        before = list(self.conn.iterdump())
        with self.assertRaisesRegex(RuntimeError, 'no longer owned'):
            facade.finish_recovery_attempt(self.conn, recovery_attempt_id=first[1], worker='old', status='NO_OFFICIAL_SOURCE_FOUND', completed_at_utc=NOW)
        self.assertEqual(before, list(self.conn.iterdump()))
        attempts.finish_recovery_attempt(self.conn, recovery_attempt_id=second[1], worker='new', status='RETRYABLE_FAILURE', completed_at_utc='2026-08-16T00:00:02+00:00', retry_seconds=60)
        row = self.conn.execute('SELECT status,attempt_count,lease_owner,next_attempt_at_utc FROM artifact_recovery_targets').fetchone()
        self.assertEqual(tuple(row), ('RETRYABLE_FAILURE',2,None,'2026-08-16T00:01:02+00:00'))
        with self.assertRaisesRegex(RuntimeError, 'no longer active'):
            attempts.finish_recovery_attempt(self.conn, recovery_attempt_id=second[1], worker='new', status='TERMINAL_FAILURE', completed_at_utc=NOW)

    def test_source_exhaustion_and_immutable_completed_attempts(self):
        self.target()
        for source in attempts.RECOVERY_SOURCE_GROUPS:
            target, attempt_id = attempts.claim_recovery_target(self.conn, source=source, worker='w', now_utc=NOW)
            attempts.finish_recovery_attempt(self.conn, recovery_attempt_id=attempt_id, worker='w', status='NO_OFFICIAL_SOURCE_FOUND', completed_at_utc=NOW, result_evidence={'source':source})
            self.assertIsNone(facade.claim_recovery_target(self.conn, source=source, worker='w', now_utc=NOW))
        self.assertEqual(self.conn.execute('SELECT status,attempt_count FROM artifact_recovery_targets').fetchone()[:], ('NO_OFFICIAL_SOURCE_FOUND',3))
        self.assertEqual(attempts._remaining_source_status(self.conn, target.recovery_target_id), 'NO_OFFICIAL_SOURCE_FOUND')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM artifact_recovery_attempts WHERE completed_at_utc=?', (NOW,)).fetchone()[0],3)

    def test_owner_dependencies_and_transitive_imports_are_inert(self):
        tree = ast.parse(Path(attempts.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse(set((node.module or '').split('.')) & {'document_recovery_repo','pipeline','commands','view','workflows','operations'})
        script = "import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode=True\ndef deny(*a, **k): raise AssertionError('forbidden side effect')\ndef audit(event,args):\n if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write during import')\n if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect=sqlite3.connect=subprocess.Popen=threading.Thread.start=deny\nimport scrapper.db.documents.recovery.attempts\nimport scrapper.db.document_recovery_repo\nimport scrapper.pipeline.alternate_source_recovery\n"
        result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
