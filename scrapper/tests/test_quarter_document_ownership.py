"""Schedule ownership, compatibility hooks and inert import contracts."""
import inspect
import unittest
from unittest.mock import patch
from scrapper.commands import quarter_documents as facade
from scrapper.db.documents import quarter as owner
from scrapper.workflows.documents import quarter as workflow
from scrapper.documents.readiness import quarter as rules


NAMES = ('predates_quarter','call_months','build_inventory','repair_period_tags','prioritize')

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
            target=rules if name in ('predates_quarter','call_months') else workflow if name=='build_inventory' else owner
            with patch.object(target, name, return_value=result) as call:
                self.assertIs(getattr(facade, name)(*args, **kwargs), result)
            self.assertEqual(call.call_args.args, tuple(args))
            for key, value in kwargs.items():
                self.assertIs(call.call_args.kwargs[key], value)
            for key in set(call.call_args.kwargs) - set(kwargs):
                self.assertIs(call.call_args.kwargs[key], getattr(facade, key))

    def test_selected_callers_use_new_owners(self):
        from scrapper.commands import quarter_extract, recover_quarter_document_links
        self.assertIs(quarter_extract.build_inventory,workflow.build_inventory)
        self.assertIs(recover_quarter_document_links.call_months,rules.call_months)

    def test_layer_boundaries(self):
        import ast
        from pathlib import Path
        for name in ('commands/quarter_documents.py','workflows/documents/quarter.py','db/documents/quarter.py','documents/readiness/quarter.py'):
            tree=ast.parse((Path(__file__).parents[1]/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom) and not name.startswith('commands/'):
                    self.assertFalse(any(x in (node.module or '') for x in ('commands','pipeline','cli','view','operations')))
                    if name.startswith(('db/','documents/')):self.assertNotIn('workflows',node.module or '')
                if not name.startswith('db/') and isinstance(node,ast.Attribute):
                    self.assertNotIn(node.attr,('execute','executemany','executescript'))

    def test_owner_import_is_inert(self):
        import subprocess,sys
        script="import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode = True\ndef deny(*args, **kwargs): raise AssertionError('forbidden import side effect')\ndef audit(event, args):\n    if event == 'open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):\n        raise AssertionError('write during import')\n    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):\n        raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny\nimport scrapper.db.documents.quarter; import scrapper.workflows.documents.quarter; import scrapper.documents.readiness.quarter\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
