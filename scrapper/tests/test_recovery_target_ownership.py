"""Boundary and compatibility checks for the DA-06 target owner."""
import ast
import inspect
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from scrapper.db import document_recovery_repo as facade
from scrapper.db.documents.recovery import targets

NAMES = ('_originator', '_blocked_artifacts', '_identities', '_rating_context',
         '_already_satisfied', '_target_from_row', 'build_recovery_targets', 'load_recovery_target')

class RecoveryTargetOwnershipTests(unittest.TestCase):
    def test_facade_forwards_original_arguments_and_patch_points(self):
        for name in NAMES:
            with self.subTest(name=name):
                fn = getattr(facade, name)
                sig = inspect.signature(fn)
                args = [object() for p in sig.parameters.values() if p.kind == p.POSITIONAL_OR_KEYWORD]
                kwargs = {p.name: object() for p in sig.parameters.values() if p.kind == p.KEYWORD_ONLY}
                token = object()
                with patch.object(targets, name, return_value=token) as owner:
                    self.assertIs(fn(*args, **kwargs), token)
                self.assertEqual(owner.call_args.args, tuple(args))
                self.assertEqual(owner.call_args.kwargs, dict(kwargs, _hooks=facade))

    def test_empty_selection_preserves_clock_patch_and_default_owner(self):
        for module in (facade, targets):
            with self.subTest(module=module.__name__), patch.object(module, 'utc_now', return_value='fixed') as clock:
                report = module.build_recovery_targets(None, artifact_ids=[])
                self.assertEqual(report['accounted_total'], 0)
                clock.assert_called_once_with()
                with self.assertRaisesRegex(ValueError, 'only credit_rating'):
                    module.build_recovery_targets(None, artifact_type='annual_report')

    def test_helper_patch_is_forwarded_without_mutating_owner(self):
        original = targets._blocked_artifacts
        with patch.object(facade, '_blocked_artifacts', return_value=()) as blocked:
            conn = unittest.mock.Mock()
            conn.execute.return_value = ()
            report = facade.build_recovery_targets(conn, artifact_ids=[9, 9, 3], dry_run=True, observed_at_utc='fixed')
            blocked.assert_called_once_with(conn, (3, 9))
            self.assertEqual(report['total_blocked_artifacts'], 0)
            self.assertIs(targets._blocked_artifacts, original)

    def test_owner_has_no_reverse_dependencies(self):
        tree = ast.parse(Path(targets.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse(set((node.module or '').split('.')) & {'document_recovery_repo', 'pipeline', 'commands', 'view', 'workflows', 'operations'})

    def test_transitive_imports_are_inert(self):
        script = "import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode=True\ndef deny(*a, **k): raise AssertionError('forbidden side effect')\ndef audit(event,args):\n if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write during import')\n if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect=sqlite3.connect=subprocess.Popen=threading.Thread.start=deny\nimport scrapper.db.documents.recovery.targets\nimport scrapper.db.document_recovery_repo\nimport scrapper.pipeline.credit_rating_downloads\n"
        result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
