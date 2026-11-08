"""Failed download strategy boundary, refusal, and transaction checks."""
import ast
import inspect
import pickle
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from scrapper.pipeline import document_download_recovery as facade
from scrapper.documents.recovery import failed_download as owner, RecoverySourceType
from scrapper.db.documents.recovery import failed_download as persistence
from scrapper.tests import test_document_download_recovery as fixtures
NAMES = ('requeue_document_downloads','register_replacement_document_url','import_manual_document')
class FailedDownloadOwnershipTests(unittest.TestCase):
    setUp = fixtures.DocumentDownloadRecoveryTests.setUp
    tearDown = fixtures.DocumentDownloadRecoveryTests.tearDown
    _terminal_job = fixtures.DocumentDownloadRecoveryTests._terminal_job

    def test_facade_preserves_all_arguments_and_local_hooks(self):
        for name in NAMES:
            fn=getattr(facade,name);sig=inspect.signature(fn)
            args=[object() for p in sig.parameters.values() if p.kind==p.POSITIONAL_OR_KEYWORD]
            kwargs={p.name:object() for p in sig.parameters.values() if p.kind==p.KEYWORD_ONLY}
            token=object()
            with patch.object(owner,name,return_value=token) as mocked:self.assertIs(fn(*args,**kwargs),token)
            self.assertEqual(mocked.call_args.args,tuple(args));self.assertEqual(mocked.call_args.kwargs,dict(kwargs,_hooks=facade))

    def test_requeue_audit_failure_rolls_back_queue_and_history(self):
        job=self._terminal_job()
        self.conn.execute("CREATE TEMP TRIGGER deny_audit BEFORE INSERT ON document_download_recovery_actions BEGIN SELECT RAISE(ABORT,'audit fault'); END")
        self.conn.commit();before=list(self.conn.iterdump())
        for module in (facade,owner):
            with self.assertRaisesRegex(sqlite3.IntegrityError,'audit fault'):
                module.requeue_document_downloads(self.conn,queue_kind='generic',job_ids=[job['download_job_id']],reason_code='OPERATOR_VERIFIED',applied_at_utc=fixtures.NOW)
            self.assertEqual(before,list(self.conn.iterdump()));self.assertFalse(self.conn.in_transaction)

    def test_replacement_failure_rolls_back_source_queue_and_provenance(self):
        job=self._terminal_job()
        self.conn.execute("CREATE TEMP TRIGGER deny_audit BEFORE INSERT ON document_download_recovery_actions BEGIN SELECT RAISE(ABORT,'audit fault'); END")
        self.conn.commit();before=list(self.conn.iterdump())
        for module in (facade,owner):
            with self.assertRaisesRegex(sqlite3.IntegrityError,'audit fault'):
                module.register_replacement_document_url(self.conn,artifact_id=job['artifact_id'],replaces_artifact_source_id=job['artifact_source_id'],replacement_url='https://issuer.example/new.pdf',reason_code='SOURCE_REPUBLISHED',applied_at_utc=fixtures.NOW)
            self.assertEqual(before,list(self.conn.iterdump()));self.assertFalse(self.conn.in_transaction)

    def test_refusals_and_preview_do_not_mutate(self):
        job=self._terminal_job();before=list(self.conn.iterdump())
        for module in (facade,owner):
            for url in ('https://user:password@issuer.example/file.pdf','ftp://issuer.example/file.pdf',job['requested_url']):
                with self.assertRaises(ValueError):module.register_replacement_document_url(self.conn,artifact_id=job['artifact_id'],replaces_artifact_source_id=job['artifact_source_id'],replacement_url=url,reason_code='SOURCE_REPUBLISHED')
            result=module.register_replacement_document_url(self.conn,artifact_id=job['artifact_id'],replaces_artifact_source_id=job['artifact_source_id'],replacement_url='https://ISSUER.example/new.pdf#fragment',reason_code='SOURCE_REPUBLISHED',dry_run=True)
            self.assertEqual(result['new_url'],'https://issuer.example/new.pdf')
            with self.assertRaisesRegex(ValueError,'does not belong'):module.import_manual_document(self.conn,artifact_id=-1,artifact_source_id=job['artifact_source_id'],path=fixtures.PDF,official=True,reason_detail='operator')
            with self.assertRaisesRegex(ValueError,'byte limit'):module.import_manual_document(self.conn,artifact_id=job['artifact_id'],artifact_source_id=job['artifact_source_id'],path=fixtures.PDF,official=True,reason_detail='operator',max_bytes=1)
            self.assertEqual(before,list(self.conn.iterdump()))

    def test_clock_hook_is_local_and_recovery_types_keep_pickle_identity(self):
        job=self._terminal_job();clock=owner.utc_now
        with patch.object(facade,'utc_now',return_value=fixtures.NOW) as mocked:
            facade.requeue_document_downloads(self.conn,queue_kind='generic',job_ids=[job['download_job_id']],reason_code='OPERATOR_VERIFIED')
            mocked.assert_called_once_with()
        self.assertIs(owner.utc_now,clock)
        self.assertEqual(RecoverySourceType.__module__,'scrapper.documents.recovery')
        self.assertIs(pickle.loads(pickle.dumps(RecoverySourceType.BSE_DEBT_DOCUMENT)),RecoverySourceType.BSE_DEBT_DOCUMENT)

    def test_owner_layers_and_transitive_imports_are_inert(self):
        for node in ast.walk(ast.parse(Path(owner.__file__).read_text())):
            if isinstance(node,ast.ImportFrom):self.assertFalse(set((node.module or '').split('.')) & {'pipeline','workflows','operations','view','commands','exports'})
        for node in ast.walk(ast.parse(Path(persistence.__file__).read_text())):
            if isinstance(node,ast.ImportFrom):self.assertFalse(set((node.module or '').split('.')) & {'pipeline','workflows','operations','view','commands','exports'})
        self.assertFalse(any(isinstance(n,ast.Attribute) and n.attr in {'execute','executemany','executescript'} for n in ast.walk(ast.parse(Path(owner.__file__).read_text()))))
        script="import sys,socket,sqlite3,subprocess,threading\nsys.dont_write_bytecode=True\ndef deny(*a,**k):raise AssertionError('forbidden side effect')\ndef audit(event,args):\n if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write during import')\n if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect=sqlite3.connect=subprocess.Popen=threading.Thread.start=deny\nimport scrapper.documents.recovery\nimport scrapper.documents.recovery.failed_download\nimport scrapper.db.documents.recovery.failed_download\nimport scrapper.pipeline.document_download_recovery\nimport scrapper.cli\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stderr)
