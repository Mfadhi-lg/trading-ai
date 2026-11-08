"""Security ownership, compatibility hooks and inert import contracts."""
import inspect
import unittest
from unittest.mock import patch
from scrapper.db import exchange_repo as facade
from scrapper.db.exchange import securities as owner
from scrapper.pipeline import exchange_universe as workflow

NAMES = ('sweep_scope', 'absence_preview', '_record_event', '_insert_version', '_open_conflict', '_link_isin', '_link_isin_value', '_already_absent_on', '_link_or_conflict', 'apply_security_master', 'open_identity_conflicts', 'resolve_identity_conflict', 'apply_corporate_changes')

class SecurityOwnerTests(unittest.TestCase):
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

    def test_workflow_uses_security_owner(self):
        for name in ('absence_preview', 'apply_corporate_changes', 'apply_security_master'):
            self.assertIs(getattr(workflow, name), getattr(owner, name))

    def test_owner_import_is_inert(self):
        import subprocess,sys
        script="import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode = True\ndef deny(*args, **kwargs): raise AssertionError('forbidden import side effect')\ndef audit(event, args):\n    if event == 'open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):\n        raise AssertionError('write during import')\n    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):\n        raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny\nimport scrapper.db.exchange.securities\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
