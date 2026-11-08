"""Schedule ownership, compatibility hooks and inert import contracts."""
import inspect
import unittest
from unittest.mock import patch
from scrapper.db import artifact_reconciliation_repo as facade
from scrapper.db.documents import expectations as owner
from scrapper.db import document_artifact_repo as workflow

NAMES = ('screener_market_cap_above_floor','canonical_json','register_artifact_event','ensure_artifact_expectation','ensure_result_artifact_expectations','backfill_result_artifact_expectations','ensure_discovered_artifact_expectations')

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
        for name in ('ensure_discovered_artifact_expectations',):
            self.assertIs(getattr(workflow, name), getattr(owner, name))

    def test_owner_import_is_inert(self):
        import subprocess,sys
        script="import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode = True\ndef deny(*args, **kwargs): raise AssertionError('forbidden import side effect')\ndef audit(event, args):\n    if event == 'open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):\n        raise AssertionError('write during import')\n    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):\n        raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny\nimport scrapper.db.documents.expectations; import scrapper.db.documents.readiness; import scrapper.documents.readiness.persisted\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)



class ReadinessOwnerTests(unittest.TestCase):
    def test_legacy_readiness_hooks(self):
        from scrapper.pipeline import reconcile_artifacts as old
        from scrapper.documents.readiness import persisted
        result=object();conn=object();expectation=object()
        with patch.object(persisted.PersistedArtifactAdapter,'check',return_value=result) as call:
            self.assertIs(old.PersistedArtifactAdapter().check(conn,expectation,'fixed'),result)
            self.assertIs(call.call_args.kwargs['_accepted_artifact'],old._accepted_artifact)
            self.assertIs(call.call_args.kwargs['_same_event'],old._same_event)
        with patch.object(old,'_same_event',return_value=True) as hook:
            from scrapper.db.documents import readiness
            with patch.object(readiness,'_accepted_artifact',return_value=result) as call:
                self.assertIs(old._accepted_artifact(conn,expectation),result)
                self.assertIs(call.call_args.kwargs['_same_event'],hook)
    def test_readiness_service_has_no_sql_or_reverse_imports(self):
        import ast
        from pathlib import Path
        for name in ('documents/readiness/persisted.py','db/documents/expectations.py','db/documents/readiness.py'):
            tree=ast.parse((Path(__file__).parents[1]/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):self.assertFalse(any(x in (node.module or '') for x in ('pipeline','cli','operations','view')))
                if name.startswith('documents/') and isinstance(node,ast.Attribute):self.assertNotIn(node.attr,('execute','executemany','executescript'))
