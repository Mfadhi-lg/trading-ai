"""Compatibility and independent workflow execution contracts."""
import inspect
import unittest
from unittest.mock import patch
from scrapper.pipeline import screener_vertical as facade
from scrapper.workflows.screener import ingest as owner
from scrapper.core.http import FetchResponse

class WorkflowTests(unittest.TestCase):
    def test_result_classes_share_identity(self):
        self.assertIs(facade.VerticalSliceResult, owner.VerticalSliceResult)
        self.assertIs(facade.DocumentRefreshResult, owner.DocumentRefreshResult)

    def test_every_facade_argument_and_dependency_is_forwarded(self):
        for name in ('_market_cap_outside_scope','ingest_screener_response','scrape_screener_company','scrape_screener_company_path','reprocess_screener_blobs'):
            fn=getattr(facade,name)
            signature=inspect.signature(fn)
            values={k:object() for k in signature.parameters}
            positional=[values[k] for k,p in signature.parameters.items() if p.kind==p.POSITIONAL_OR_KEYWORD]
            keywords={k:values[k] for k,p in signature.parameters.items() if p.kind==p.KEYWORD_ONLY}
            expected=object()
            with patch.object(owner,name,return_value=expected) as delegated:
                self.assertIs(fn(*positional,**keywords),expected)
            args,kwargs=delegated.call_args
            self.assertEqual(args,tuple(positional))
            for k,v in keywords.items():self.assertIs(kwargs[k],v)
            for k in set(kwargs)-set(keywords):self.assertIs(kwargs[k],getattr(facade,k))

    def test_fetch_failure_keeps_ingest_hook_without_supplemental_requests(self):
        response=object()
        class Rejected:
            fetch_status='access_blocked'
        with patch.object(facade,'fetch_screener',return_value=Rejected()), patch.object(facade,'ingest_screener_response',return_value=response) as ingest, patch.object(facade,'fetch_screener_peers') as peers:
            self.assertIs(facade.scrape_screener_company('TEST'),response)
            self.assertEqual(ingest.call_args.args[1],'standalone')
            peers.assert_not_called()

    def test_direct_owner_refuses_consolidated_before_fetch(self):
        with patch.object(owner,'fetch_screener',side_effect=AssertionError('network')):
            with self.assertRaisesRegex(ValueError,'standalone-only'):owner.scrape_screener_company('TEST','consolidated')

    def test_owner_import_is_inert(self):
        import subprocess,sys
        script="import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode = True\ndef deny(*args, **kwargs): raise AssertionError('forbidden import side effect')\ndef audit(event, args):\n    if event == 'open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):\n        raise AssertionError('write during import')\n    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):\n        raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny\nimport scrapper.workflows.screener.ingest\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
