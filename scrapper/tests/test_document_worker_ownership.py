"""Generic worker ownership, legacy hooks and reconciliation ordering."""
import ast
import importlib
import inspect
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from scrapper.workflows.documents import download, download_reconciliation as reconciliation
from scrapper.db.documents import download_reporting
worker = importlib.import_module('scrapper.pipeline.run_document_download_worker')


class WorkerOwnershipTests(unittest.TestCase):
    def test_facade_forwards_exact_arguments_and_hooks(self):
        sig = inspect.signature(worker.run_document_download_worker)
        conn = object()
        kwargs = {p.name: object() for p in sig.parameters.values() if p.kind == p.KEYWORD_ONLY}
        token = object()
        with patch.object(download, 'run_document_download_worker', return_value=token) as call:
            self.assertIs(worker.run_document_download_worker(conn, **kwargs), token)
        self.assertEqual(call.call_args.args, (conn,))
        self.assertEqual(call.call_args.kwargs, dict(kwargs, _hooks=worker))
        for name in ('_scope_policy', 'build_download_queue_report'):
            with patch.object(download_reporting, name, return_value=token) as call:
                self.assertIs(getattr(worker, name)(conn, scope_id='scope'), token)
            self.assertEqual(call.call_args.args, (conn, 'scope') if name == '_scope_policy' else (conn,))
            if name != '_scope_policy':self.assertEqual(call.call_args.kwargs['scope_id'], 'scope')

    def test_direct_and_legacy_empty_batches_and_health_refusal(self):
        health = SimpleNamespace(to_dict=lambda: {'ready': True})
        for fn in (worker.run_document_download_worker, download.run_document_download_worker):
            with self.subTest(fn=fn.__module__), patch.object(worker, 'claim_download_job', return_value=None) as legacy_claim, patch.object(download, 'claim_download_job', return_value=None) as direct_claim, patch.object(worker, 'queue_counts', return_value={}), patch.object(download, 'queue_counts', return_value={}):
                result = fn(object(), worker='frozen', throttle_seconds=0, health_check=lambda _:health)
                self.assertEqual(result['processed_job_count'], 0)
                self.assertEqual(result['document_store'], {'ready': True})
                self.assertEqual((legacy_claim.call_count, direct_claim.call_count), (1, 0) if fn is worker.run_document_download_worker else (0, 1))
                with self.assertRaisesRegex(RuntimeError, 'storage refused'):
                    fn(object(), health_check=Mock(side_effect=RuntimeError('storage refused')))
                self.assertEqual(legacy_claim.call_count + direct_claim.call_count, 1)

    def test_success_reconciles_each_source_with_original_content(self):
        conn=object(); persisted=SimpleNamespace(content_id=7, artifact_source_ids=(11,12)); result=SimpleNamespace(is_validated=True)
        reconcile=Mock(); clock=Mock(side_effect=('first','second'))
        reconciliation.reconcile_download_success(conn, persisted=persisted, result=result, now=clock, reconcile_recovery_for_source=reconcile)
        self.assertEqual([c.kwargs for c in reconcile.call_args_list],[{'artifact_source_id':11,'content_id':7,'reconciled_at_utc':'first'},{'artifact_source_id':12,'content_id':7,'reconciled_at_utc':'second'}])
        for validated,content in ((False,7),(True,None)):
            reconcile.reset_mock(); result.is_validated=validated; persisted.content_id=content
            reconciliation.reconcile_download_success(conn,persisted=persisted,result=result,now=clock,reconcile_recovery_for_source=reconcile)
            reconcile.assert_not_called()

    def test_terminal_failure_reopens_each_source_only(self):
        conn=object(); persisted=SimpleNamespace(artifact_source_ids=(11,12)); result=SimpleNamespace(http_status=404, detected_media_type=None)
        for status in ('SUCCEEDED','RETRYABLE_FAILURE','TERMINAL_FAILURE'):
            reopen=Mock(); clock=Mock(return_value='frozen')
            reconciliation.reconcile_download_outcome(conn,job={},persisted=persisted,result=result,status=status,now=clock,reopen_recovery_after_download_failure=reopen)
            self.assertEqual(reopen.call_count,2 if status=='TERMINAL_FAILURE' else 0)

    def test_landing_attachment_keeps_provenance_before_failure_reopen(self):
        conn=object(); job={'artifact_id':3,'requested_url':'https://issuer.example/page','artifact_type':'presentation'}
        persisted=SimpleNamespace(artifact_source_ids=(11,12),download_attempt_id=19)
        result=SimpleNamespace(http_status=200,detected_media_type='text/html',body=b'page',final_url=None)
        order=[]
        def registered(*args,**kwargs):
            order.append('replacement')
            self.assertEqual(kwargs['replaces_artifact_source_id'],11)
            self.assertEqual(kwargs['artifact_id'],3)
            self.assertEqual(kwargs['evidence'],{'landing_url':None,'download_attempt_id':19})
        with patch('scrapper.documents.landing_page.issuer_pdf_link',return_value='https://issuer.example/file.pdf') as resolve, patch('scrapper.pipeline.document_download_recovery.register_replacement_document_url',side_effect=registered):
            reconciliation.reconcile_download_outcome(conn,job=job,persisted=persisted,result=result,status='TERMINAL_FAILURE',now=lambda:'frozen',blocked_terms_hosts=lambda _:('blocked.example',),reopen_recovery_after_download_failure=lambda *a,**k:order.append(k['artifact_source_id']))
        self.assertEqual(order,['replacement',11,12])
        self.assertEqual(resolve.call_args.kwargs,{'page_url':job['requested_url'],'artifact_type':'presentation','blocked_hosts':('blocked.example',)})
        with patch('scrapper.documents.landing_page.issuer_pdf_link',return_value=None), patch('scrapper.pipeline.document_download_recovery.register_replacement_document_url') as register:
            reconciliation.reconcile_download_outcome(conn,job=job,persisted=persisted,result=result,status='RETRYABLE_FAILURE',now=lambda:'frozen',blocked_terms_hosts=lambda _:())
            register.assert_not_called()

    def test_layers_and_inert_imports(self):
        tree=ast.parse(Path(download_reporting.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):self.assertFalse(set((node.module or '').split('.')) & {'pipeline','commands','view','operations','workflows'})
        for module in (download,reconciliation):
            tree=ast.parse(Path(module.__file__).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Attribute):self.assertNotIn(node.attr, {'execute','executemany','commit'})
        script = "import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode=True\ndef deny(*a, **k): raise AssertionError('forbidden side effect')\ndef audit(event,args):\n if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write during import')\n if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect=sqlite3.connect=subprocess.Popen=threading.Thread.start=deny\nimport scrapper.workflows.documents.download\nimport scrapper.workflows.documents.download_reconciliation\nimport scrapper.db.documents.download_reporting\nimport scrapper.pipeline.run_document_download_worker\n"
        completed=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stderr)
