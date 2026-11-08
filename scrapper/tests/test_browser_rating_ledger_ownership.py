"""Architecture and local hook contracts for the browser ledger extraction."""
import ast
import inspect
import unittest
from unittest.mock import Mock, patch
from scrapper.commands import browser_rating_recovery as command
from scrapper.documents.recovery.browser_rating import ledger
from scrapper.db.documents.recovery import browser_rating as repository

class BrowserLedgerOwnershipTests(unittest.TestCase):
    def test_compatible_signatures_and_local_clock_hooks(self):
        conn=Mock()
        with patch.object(command,'utc_now',return_value='fixed') as clock, patch.object(ledger,'worker_state',return_value='result') as owner:
            self.assertEqual(command.worker_state(conn,'run','worker','WAITING'),'result')
            owner.assert_called_once_with(conn,'run','worker','WAITING',utc_now=clock)
        self.assertEqual(list(inspect.signature(command.claim).parameters),['conn','run_id','hosts','owner','job_ids'])
        self.assertEqual(list(inspect.signature(command.finish).parameters),['conn','job','status','content_id','final_url','error','evidence'])
    def test_sql_and_dependencies_have_selected_owners(self):
        for module in [ledger]:
            tree=ast.parse(inspect.getsource(module))
            self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ['execute','executescript'] for n in ast.walk(tree)))
        tree=ast.parse(inspect.getsource(command))
        selected={'worker_state','release_owned_jobs','seed','report','claim','finish','cool_down','retry_delay'}
        for n in tree.body:
            if isinstance(n,ast.FunctionDef) and n.name in selected:
                self.assertFalse(any(isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute) and x.func.attr=='execute' for x in ast.walk(n)))
        for module in [ledger,repository]:
            tree=ast.parse(inspect.getsource(module))
            for n in ast.walk(tree):
                if isinstance(n,ast.ImportFrom):
                    self.assertFalse(any(p in (n.module or '').split('.') for p in ['commands','view','operations','pipeline']))
    def test_new_owners_import_without_side_effects(self):
        import subprocess,sys
        code='''
import sqlite3,socket,threading,subprocess,builtins,os
from unittest.mock import patch

def forbidden(*a,**k):raise AssertionError('unexpected import side effect')
original=builtins.open
def read_only(file,mode='r',*a,**k):
    if any(x in mode for x in 'wax+'):forbidden()
    return original(file,mode,*a,**k)
with patch.object(sqlite3,'connect',forbidden), patch.object(socket.socket,'connect',forbidden), patch.object(threading.Thread,'start',forbidden), patch.object(subprocess,'Popen',forbidden), patch.object(builtins,'open',read_only), patch.object(os,'mkdir',forbidden):
    from scrapper.documents.recovery.browser_rating import ledger
    from scrapper.db.documents.recovery import browser_rating
'''
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
