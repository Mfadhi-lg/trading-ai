"""Company worker compatibility and direct ownership contracts."""
import inspect
import unittest
from pathlib import Path
from unittest.mock import patch
from scrapper.pipeline import screener_discovery as facade
from scrapper.workflows.screener import company_worker as owner

class CompanyWorkflowTests(unittest.TestCase):
    def test_all_arguments_and_patch_hooks_forwarded(self):
        for name in ('run_screener_company_worker','_safe_error','_failure_policy'):
            signature=inspect.signature(getattr(facade,name))
            values={k:object() for k in signature.parameters}
            args=[values[k] for k,p in signature.parameters.items() if p.kind==p.POSITIONAL_OR_KEYWORD]
            kwargs={k:values[k] for k,p in signature.parameters.items() if p.kind==p.KEYWORD_ONLY}
            result=object()
            with patch.object(owner,name,return_value=result) as call:
                self.assertIs(getattr(facade,name)(*args,**kwargs),result)
            self.assertEqual(call.call_args.args,tuple(args))
            for k,v in kwargs.items():self.assertIs(call.call_args.kwargs[k],v)
            for k in set(call.call_args.kwargs)-set(kwargs):self.assertIs(call.call_args.kwargs[k],getattr(facade,k))

    def test_direct_owner_dry_run_never_opens_writer_or_host(self):
        def deny(*args,**kwargs):raise AssertionError('writer/host')
        result=owner.run_screener_company_worker(due=True,limit=1,dry_run=True,db_path=Path('/tmp/nonexistent-c125.db'),now_utc=lambda:'2026-10-04T00:00:00+00:00',connect=deny,ScreenerHostClient=deny)
        self.assertEqual(result['summary']['claimed_count'],0)
        self.assertIsNone(result['worker_id'])

    def test_exchange_lag_retry_policy_and_redaction_preserved(self):
        exc=ValueError('screen result period is absent')
        self.assertEqual(owner._failure_policy(exc,'exchange_filing'),(True,False))
        self.assertEqual(owner._failure_policy(exc),(False,False))
        self.assertEqual(owner._failure_policy(ValueError('status=403')),(True,True))
        self.assertNotIn('secret',owner._safe_error(ValueError('cookie=secret'))['message'])

    def test_logger_retains_legacy_category(self):
        calls=[]
        owner.run_screener_company_worker(due=True,limit=1,dry_run=True,db_path=Path('/tmp/nonexistent-c125.db'),get_logger=lambda name:calls.append(name))
        self.assertEqual(calls,['scrapper.pipeline.screener_discovery'])

    def test_owner_import_is_inert(self):
        import subprocess,sys
        script="import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode = True\ndef deny(*args, **kwargs): raise AssertionError('forbidden import side effect')\ndef audit(event, args):\n    if event == 'open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):\n        raise AssertionError('write during import')\n    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):\n        raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny\nimport scrapper.workflows.screener.company_worker\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
