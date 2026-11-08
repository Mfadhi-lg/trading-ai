"""Candidate facade compatibility and attachment transaction boundaries."""
import ast
import inspect
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from scrapper.db import document_recovery_repo as facade
from scrapper.db.documents.recovery import candidates
from scrapper.tests import test_da06_recovery_attachment as attachment
NAMES = ('store_recovery_discovery_blob','persist_recovery_candidates','attach_recovery_candidate')
class RecoveryCandidateOwnershipTests(unittest.TestCase):
    setUp = attachment.RecoveryAttachmentTests.setUp
    tearDown = attachment.RecoveryAttachmentTests.tearDown
    _seed_blob = attachment.RecoveryAttachmentTests._seed_blob
    _seed_blocked_artifact = attachment.RecoveryAttachmentTests._seed_blocked_artifact
    _accepted_candidate = attachment.RecoveryAttachmentTests._accepted_candidate

    def test_facade_preserves_arguments_and_patch_points(self):
        for name in NAMES:
            fn = getattr(facade, name)
            sig = inspect.signature(fn)
            args = [object() for p in sig.parameters.values() if p.kind == p.POSITIONAL_OR_KEYWORD]
            kwargs = {p.name: object() for p in sig.parameters.values() if p.kind == p.KEYWORD_ONLY}
            token = object()
            with patch.object(candidates, name, return_value=token) as owner:
                self.assertIs(fn(*args, **kwargs), token)
            self.assertEqual(owner.call_args.args, tuple(args))
            self.assertEqual(owner.call_args.kwargs, dict(kwargs, _hooks=facade))

    def test_attachment_queue_hook_preserves_post_commit_boundary(self):
        candidate_id = self._accepted_candidate()
        observed = []
        def queue(conn, **kwargs):
            observed.append(conn.in_transaction)
            self.assertIsNotNone(conn.execute('SELECT attached_source_id FROM artifact_recovery_candidates WHERE candidate_id=?', (candidate_id,)).fetchone()[0])
            raise RuntimeError('queue handoff fault')
        with patch.object(facade, 'ensure_recovery_download_scope', side_effect=queue):
            with self.assertRaisesRegex(RuntimeError, 'queue handoff fault'):
                facade.attach_recovery_candidate(self.conn, candidate_id=candidate_id, attached_at_utc=attachment.NOW)
        self.assertEqual(observed, [False])
        result = candidates.attach_recovery_candidate(self.conn, candidate_id=candidate_id, attached_at_utc=attachment.NOW)
        self.assertEqual(result['artifact_id'], self.artifact_id)

    def test_invalid_candidate_refuses_without_mutation(self):
        before = list(self.conn.iterdump())
        for module in (facade, candidates):
            with self.assertRaisesRegex(ValueError, 'Unknown recovery candidate'):
                module.attach_recovery_candidate(self.conn, candidate_id=-1, attached_at_utc=attachment.NOW)
            self.assertEqual(before, list(self.conn.iterdump()))

    def test_clock_hook_is_local(self):
        original = candidates.utc_now
        with patch.object(facade, 'utc_now', return_value=attachment.NOW) as clock:
            facade.persist_recovery_candidates(self.conn, recovery_target_id=self.target_id, candidates=(), discovery_blob_id=1)
            clock.assert_called_once_with()
        self.assertIs(candidates.utc_now, original)
    def test_owner_dependencies_and_transitive_imports_are_inert(self):
        tree = ast.parse(Path(candidates.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse(set((node.module or '').split('.')) & {'document_recovery_repo','pipeline','commands','view','workflows','operations'})
        script = "import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode=True\ndef deny(*a, **k): raise AssertionError('forbidden side effect')\ndef audit(event,args):\n if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write during import')\n if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect=sqlite3.connect=subprocess.Popen=threading.Thread.start=deny\nimport scrapper.db.documents.recovery.candidates\nimport scrapper.db.document_recovery_repo\nimport scrapper.pipeline.alternate_source_recovery\n"
        result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
