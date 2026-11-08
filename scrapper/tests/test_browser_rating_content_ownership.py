"""Dependency boundaries and command hook compatibility for C18.3."""
import ast,inspect,unittest,subprocess,sys
from unittest.mock import Mock,patch
from scrapper.commands import browser_rating_recovery as command
from scrapper.documents.recovery.browser_rating import persistence,processing
from scrapper.db.documents.recovery import browser_rating_content as repository
class BrowserContentOwnershipTests(unittest.TestCase):
 def test_local_hooks_and_public_signatures(self):
  self.assertEqual(list(inspect.signature(command.import_report).parameters),['conn','job','result','evidence','run_id'])
  self.assertEqual(list(inspect.signature(command.process_reports).parameters),['conn','args','owner'])
  self.assertEqual(list(inspect.signature(command.repair_pdf_derivatives).parameters),['conn','args'])
  with patch.object(persistence,'import_report',return_value='ok') as owner,patch.object(command,'utc_now',Mock()) as clock:
   self.assertEqual(command.import_report('conn','job','result','evidence','run'),'ok');self.assertIs(owner.call_args.kwargs['utc_now'],clock)
  with patch.object(processing,'process_reports') as owner,patch.object(command,'STOP',Mock()) as stop:
   command.process_reports('conn','args','worker');self.assertIs(owner.call_args.kwargs['STOP'],stop)
 def test_sql_and_dependencies(self):
  for module in [persistence,processing]:
   tree=ast.parse(inspect.getsource(module))
   self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ['execute','executescript'] for n in ast.walk(tree)))
  for module in [persistence,processing,repository]:
   for n in ast.walk(ast.parse(inspect.getsource(module))):
    if isinstance(n,ast.ImportFrom):self.assertFalse(any(x in (n.module or '').split('.') for x in ['commands','view','operations']))
  for n in ast.walk(ast.parse(inspect.getsource(repository))):
   if isinstance(n,ast.ImportFrom):self.assertFalse(any(x in (n.module or '').split('.') for x in ['pipeline','documents','sources','workflows']))
 def test_transitive_inert_imports(self):
  code='''
import sqlite3,socket,threading,subprocess,builtins,os
from unittest.mock import patch
def forbidden(*a,**k):raise AssertionError('unexpected import side effect')
original=builtins.open
def read_only(file,mode='r',*a,**k):
 if any(x in mode for x in 'wax+'):forbidden()
 return original(file,mode,*a,**k)
with patch.object(sqlite3,'connect',forbidden),patch.object(socket.socket,'connect',forbidden),patch.object(threading.Thread,'start',forbidden),patch.object(subprocess,'Popen',forbidden),patch.object(builtins,'open',read_only),patch.object(os,'mkdir',forbidden):
 from scrapper.documents.recovery.browser_rating import persistence,processing
 from scrapper.db.documents.recovery import browser_rating_content
'''
  result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stderr)
