"""Schedule ownership, compatibility hooks and inert import contracts."""
import inspect
import unittest
from unittest.mock import patch
from scrapper.pipeline import exchange_universe as facade
from scrapper.workflows.exchange import universe as owner
from scrapper.pipeline import scheduler as workflow

NAMES = ('inspect_exchange_dataset', 'refresh_exchange_universe', 'discover_exchange_results')

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
        for name in ('refresh_exchange_universe', 'discover_exchange_results', 'DatasetNotPublished', 'ExchangeSourceAccessBlocked'):
            self.assertIs(getattr(workflow, name), getattr(owner, name))

    def test_owner_import_is_inert(self):
        import subprocess,sys
        script="import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode = True\ndef deny(*args, **kwargs): raise AssertionError('forbidden import side effect')\ndef audit(event, args):\n    if event == 'open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):\n        raise AssertionError('write during import')\n    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):\n        raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny\nimport scrapper.workflows.exchange.universe; import scrapper.sources.exchange.transport\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


class TransportOwnerTests(unittest.TestCase):
    def test_transport_legacy_hooks_and_signatures(self):
        from scrapper.core import http
        from scrapper.sources.exchange import transport
        for name in ('validate_exchange_dataset_url', '_classify_exchange_response', 'fetch_exchange_dataset', 'fetch_exchange_recovery_surface'):
            signature = inspect.signature(getattr(http,name))
            values = {key: object() for key in signature.parameters}
            args = [values[k] for k,p in signature.parameters.items() if p.kind == p.POSITIONAL_OR_KEYWORD]
            kwargs = {k:values[k] for k,p in signature.parameters.items() if p.kind == p.KEYWORD_ONLY}
            sentinel=object()
            with patch.object(transport,name,return_value=sentinel) as call:
                self.assertIs(getattr(http,name)(*args,**kwargs),sentinel)
            self.assertEqual(call.call_args.args,tuple(args))
            for k,v in kwargs.items():self.assertIs(call.call_args.kwargs[k],v)
            for k in set(call.call_args.kwargs)-set(kwargs):self.assertIs(call.call_args.kwargs[k],getattr(http,k))
    def test_cli_targets_new_owner(self):
        from pathlib import Path
        source=(Path(__file__).parents[1]/'cli/__init__.py').read_text()
        self.assertNotIn("'.pipeline.exchange_universe'",source)
        self.assertEqual(source.count("'.workflows.exchange.universe'"),5)
