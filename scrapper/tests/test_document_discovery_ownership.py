"""Schedule ownership, compatibility hooks and inert import contracts."""
import inspect
import unittest
from unittest.mock import patch
from scrapper.pipeline import promote_document_links as facade
from scrapper.documents.discovery import promotion as owner
from scrapper.pipeline import accepted_document_downloads as workflow

NAMES = ('_exact_event_date','_result_period_end','call_matches_result_window','url_fiscal_year_conflicts','_normalized_http_url','_accepted_links','_record_promotion','promote_document_links')

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
        for name in ('promote_document_links',):
            self.assertIs(getattr(workflow, name), getattr(owner, name))

    def test_owner_import_is_inert(self):
        import subprocess,sys
        script="import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode = True\ndef deny(*args, **kwargs): raise AssertionError('forbidden import side effect')\ndef audit(event, args):\n    if event == 'open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):\n        raise AssertionError('write during import')\n    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):\n        raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny\nimport scrapper.documents.discovery.promotion; import scrapper.workflows.documents.discovery\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


class DiscoveryOwnerTests(unittest.TestCase):
    def test_discovery_legacy_hooks(self):
        from scrapper.pipeline import discover_documents as facade
        from scrapper.workflows.documents import discovery as owner
        signature=inspect.signature(facade.discover_documents)
        kwargs={k:object() for k in signature.parameters}
        result=object()
        with patch.object(owner,'discover_documents',return_value=result) as call:
            self.assertIs(facade.discover_documents(**kwargs),result)
        for k,v in kwargs.items():self.assertIs(call.call_args.kwargs[k],v)
        for k in set(call.call_args.kwargs)-set(kwargs):self.assertIs(call.call_args.kwargs[k],getattr(facade,k))
    def test_cli_targets_and_policy_constants(self):
        from pathlib import Path
        source=(Path(__file__).parents[1]/'cli/__init__.py').read_text()
        self.assertNotIn("'.pipeline.discover_documents'",source)
        self.assertNotIn("'.pipeline.promote_document_links'",source)
        self.assertEqual(facade.PROMOTABLE,owner.PROMOTABLE)
        self.assertEqual(facade.PROMOTER_VERSION,owner.PROMOTER_VERSION)
