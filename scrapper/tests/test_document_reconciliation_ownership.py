"""Schedule ownership, compatibility hooks and inert import contracts."""
import inspect
import unittest
from unittest.mock import patch
from scrapper.db import artifact_reconciliation_repo as facade
from scrapper.db.documents import due as owner
from scrapper.workflows.documents import reconciliation as workflow

NAMES = ('preview_due_expectations','claim_due_expectations','complete_artifact_check','release_claims')

class DueOwnerTests(unittest.TestCase):
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
        for name in NAMES:
            self.assertIs(getattr(workflow, name), getattr(owner, name))

    def test_owner_import_is_inert(self):
        import subprocess,sys
        script="import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode = True\ndef deny(*args, **kwargs): raise AssertionError('forbidden import side effect')\ndef audit(event, args):\n    if event == 'open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):\n        raise AssertionError('write during import')\n    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):\n        raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny\nimport scrapper.db.documents.due; import scrapper.workflows.documents.reconciliation; import scrapper.domain.artifact_reconciliation\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)



class WorkflowOwnerTests(unittest.TestCase):
    def test_workflow_facade_forwards_hooks(self):
        from scrapper.pipeline import reconcile_artifacts as old
        for name in ('_source_refresh_targets','_default_screener_refresher','_default_output','_write','reconcile_artifacts'):
            target = owner if name == '_source_refresh_targets' else workflow
            signature=inspect.signature(getattr(old,name))
            values={key:object() for key in signature.parameters}
            args=[values[key] for key,param in signature.parameters.items() if param.kind==param.POSITIONAL_OR_KEYWORD]
            kwargs={key:values[key] for key,param in signature.parameters.items() if param.kind==param.KEYWORD_ONLY}
            result=object()
            with patch.object(target,name,return_value=result) as call:
                self.assertIs(getattr(old,name)(*args,**kwargs),result)
            self.assertEqual(call.call_args.args,tuple(args))
            for key,value in kwargs.items():self.assertIs(call.call_args.kwargs[key],value)
            for key in set(call.call_args.kwargs)-set(kwargs):self.assertIs(call.call_args.kwargs[key],getattr(old,key))

    def test_shared_type_identity(self):
        from scrapper.domain.artifact_reconciliation import ClaimedExpectation, ScreenerRefreshTarget
        from scrapper.db.documents import readiness
        from scrapper.documents.readiness import persisted
        from scrapper.pipeline import reconcile_artifacts as old
        self.assertIs(facade.ClaimedExpectation,ClaimedExpectation)
        self.assertIs(readiness.ClaimedExpectation,ClaimedExpectation)
        self.assertIs(persisted.ClaimedExpectation,ClaimedExpectation)
        self.assertIs(old.ScreenerRefreshTarget,ScreenerRefreshTarget)
        self.assertIs(workflow.ScreenerRefreshTarget,ScreenerRefreshTarget)

    def test_layer_imports(self):
        import ast
        from pathlib import Path
        for name in ('domain/artifact_reconciliation.py','db/documents/due.py','workflows/documents/reconciliation.py'):
            tree=ast.parse((Path(__file__).parents[1]/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):
                    self.assertFalse(any(x in (node.module or '') for x in ('pipeline','cli','operations','view')))
                    if name.startswith(('domain/','db/')):self.assertNotIn('workflows',node.module or '')
                if name.startswith(('domain/','workflows/')) and isinstance(node,ast.Attribute):self.assertNotIn(node.attr,('execute','executemany','executescript'))

    def test_default_refresh_is_document_only_and_identity_bounded(self):
        from scrapper.core import http
        from scrapper.workflows.screener import ingest
        from scrapper.domain.artifact_reconciliation import ScreenerRefreshTarget
        from pathlib import Path
        target=ScreenerRefreshTarget(7,11,'/company/FIXTURE/','standalone','2026-06',(1,2))
        with patch.object(http,'ScreenerHostClient') as client, patch.object(ingest,'scrape_screener_company_path',return_value='ok') as scrape:
            refresh=workflow._default_screener_refresher(db_path=Path('/tmp/isolated.db'))
            self.assertEqual(refresh(target),'ok');self.assertEqual(refresh(target),'ok')
            client.assert_called_once_with()
            scrape.assert_called_with('/company/FIXTURE/','standalone',expected_result_period='2026-06',db_path=Path('/tmp/isolated.db'),host_client=client.return_value,document_only=True,expected_company_id=7)
