"""Recovery owner compatibility, immutable history and transaction refusal."""
import ast
import inspect
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
from scrapper.db import document_recovery_repo as facade
from scrapper.db.documents.recovery import reconciliation, reports
from scrapper.exports import document_alternate_source_recovery as report_facade
from scrapper.tests import test_da06_recovery_attachment as attachment
from scrapper.db.document_artifact_repo import register_artifact_content
NAMES = ('reconcile_recovery_for_source','classify_direct_rating_evidence','reopen_recovery_after_download_failure')
class RecoveryReconciliationOwnershipTests(unittest.TestCase):
    setUp = attachment.RecoveryAttachmentTests.setUp
    tearDown = attachment.RecoveryAttachmentTests.tearDown
    _seed_blob = attachment.RecoveryAttachmentTests._seed_blob
    _seed_blocked_artifact = attachment.RecoveryAttachmentTests._seed_blocked_artifact
    _accepted_candidate = attachment.RecoveryAttachmentTests._accepted_candidate
    _seed_extraction = attachment.RecoveryAttachmentTests._seed_extraction

    def _attached_content(self):
        attached = facade.attach_recovery_candidate(self.conn, candidate_id=self._accepted_candidate(), attached_at_utc=attachment.NOW)
        content_id = register_artifact_content(self.conn, artifact_id=self.artifact_id, blob_id=self._seed_blob(attachment.PDF_BODY,'application/pdf','pdf'), media_type='application/pdf', is_official=True, created_at_utc=attachment.NOW)
        self.conn.commit()
        return attached['artifact_source_id'], content_id

    def test_facade_preserves_arguments_and_patch_points(self):
        for module, owner_module, names in ((facade,reconciliation,NAMES),(report_facade,reports,('build_alternate_source_recovery_report',))):
            for name in names:
                fn = getattr(module, name); sig = inspect.signature(fn)
                args = [object() for p in sig.parameters.values() if p.kind == p.POSITIONAL_OR_KEYWORD]
                kwargs = {p.name: object() for p in sig.parameters.values() if p.kind == p.KEYWORD_ONLY}
                token = object()
                with patch.object(owner_module, name, return_value=token) as owner:
                    self.assertIs(fn(*args, **kwargs), token)
                self.assertEqual(owner.call_args.args, tuple(args))
                self.assertEqual(owner.call_args.kwargs, dict(kwargs, _hooks=module))

    def test_wrong_artifact_content_refuses_without_mutation(self):
        source_id, _ = self._attached_content(); before = list(self.conn.iterdump())
        for module in (facade, reconciliation):
            with self.assertRaisesRegex(ValueError, 'does not belong'):
                module.reconcile_recovery_for_source(self.conn,artifact_source_id=source_id,content_id=-1,reconciled_at_utc=attachment.NOW)
            self.assertEqual(before,list(self.conn.iterdump()))

    def test_reconciliation_failure_rolls_back_classification_and_candidate(self):
        source_id, content_id = self._attached_content()
        self._seed_extraction(content_id,'Downloaded filing without rating evidence')
        self.conn.execute("CREATE TEMP TRIGGER deny_target BEFORE UPDATE ON artifact_recovery_targets BEGIN SELECT RAISE(ABORT,'target fault'); END")
        self.conn.commit(); before = list(self.conn.iterdump())
        for module in (facade,reconciliation):
            with self.assertRaisesRegex(sqlite3.IntegrityError,'target fault'):
                module.reconcile_recovery_for_source(self.conn,artifact_source_id=source_id,content_id=content_id,reconciled_at_utc=attachment.NOW)
            self.assertFalse(self.conn.in_transaction)
            self.assertEqual(before,list(self.conn.iterdump()))

    def test_recovered_download_failure_preserves_all_history(self):
        source_id, _ = self._attached_content()
        with self.conn:self.conn.execute("UPDATE artifact_recovery_targets SET status='RECOVERED',basic_rating_coverage=1 WHERE recovery_target_id=?",(self.target_id,))
        before = list(self.conn.iterdump())
        for module in (facade,reconciliation):
            self.assertIsNone(module.reopen_recovery_after_download_failure(self.conn,artifact_source_id=source_id,failed_at_utc=attachment.NOW))
            self.assertEqual(before,list(self.conn.iterdump()))

    def test_report_projection_and_writer_preserve_read_only_rows(self):
        before = list(self.conn.iterdump())
        expected = report_facade.build_alternate_source_recovery_report(self.conn,generated_at_utc=attachment.NOW)
        self.assertEqual(expected,reports.build_alternate_source_recovery_report(self.conn,generated_at_utc=attachment.NOW))
        result,path = report_facade.write_alternate_source_recovery_report(self.conn,output=self.root/'report.json',generated_at_utc=attachment.NOW)
        import json
        self.assertEqual(result,expected); self.assertEqual(json.loads(path.read_text()),expected)
        self.assertEqual(before,list(self.conn.iterdump()))
        self.assertEqual(expected['summary']['accounted_total'],expected['summary']['total_blocked_logical_artifacts'])

    def test_clock_and_classifier_hooks_stay_local(self):
        source_id, content_id = self._attached_content()
        self._seed_extraction(content_id,'Downloaded filing without rating evidence')
        original = reconciliation.classify_rating_evidence
        with patch.object(facade,'utc_now',return_value=attachment.NOW) as clock, patch.object(facade,'classify_rating_evidence',wraps=original) as classifier:
            result=facade.reconcile_recovery_for_source(self.conn,artifact_source_id=source_id,content_id=content_id)
            clock.assert_called_once_with(); classifier.assert_called_once()
        self.assertFalse(result['basic_rating_coverage']); self.assertIs(reconciliation.classify_rating_evidence,original)
        with patch.object(report_facade,'utc_now',return_value=attachment.NOW) as clock:
            self.assertEqual(report_facade.build_alternate_source_recovery_report(self.conn)['generated_at_utc'],attachment.NOW)
            clock.assert_called_once_with()

    def test_owner_dependencies_and_transitive_imports_are_inert(self):
        for module in (reconciliation,reports):
            for node in ast.walk(ast.parse(Path(module.__file__).read_text())):
                if isinstance(node,ast.ImportFrom):self.assertFalse(set((node.module or '').split('.')) & {'document_recovery_repo','pipeline','commands','view','workflows','operations','exports'})
        script = "import sys,socket,sqlite3,subprocess,threading\nsys.dont_write_bytecode=True\ndef deny(*a,**k):raise AssertionError('forbidden side effect')\ndef audit(event,args):\n if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write during import')\n if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect=sqlite3.connect=subprocess.Popen=threading.Thread.start=deny\nimport scrapper.db.documents.recovery.reconciliation\nimport scrapper.db.documents.recovery.reports\nimport scrapper.db.document_recovery_repo\nimport scrapper.exports.document_alternate_source_recovery\nimport scrapper.pipeline.extract_document\nimport scrapper.pipeline.run_document_download_worker\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
