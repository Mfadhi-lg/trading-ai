"""Host ownership, compatible per-call hooks and transitive inert imports."""
import ast,inspect,subprocess,sys,unittest
from unittest.mock import patch
from scrapper.commands import browser_rating_host as host
from ops.host import browser_rating_lifecycle as lifecycle,browser_rating_runner as runner

class HostOwnershipTests(unittest.TestCase):
 def test_old_host_functions_keep_signatures_and_hooks(self):
  for name in ['running_pid','start','stop','main']:
   self.assertEqual(list(inspect.signature(getattr(host,name)).parameters),[])
   with patch.object(lifecycle,name,return_value=123) as owner,patch.object(host,'PID_FILE','selected-pid'):
    self.assertEqual(getattr(host,name)(),123)
    self.assertEqual(owner.call_args.kwargs['hooks'].PID_FILE,'selected-pid')
    self.assertIs(owner.call_args.kwargs['hooks'].running_pid,host.running_pid)
 def test_host_owners_have_no_sql_or_higher_layer_imports(self):
  for module in [lifecycle,runner]:
   tree=ast.parse(inspect.getsource(module))
   self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ['execute','executescript','connect'] for n in ast.walk(tree)))
   self.assertFalse(any(isinstance(n,(ast.Import,ast.ImportFrom)) for n in tree.body))
 def test_transitive_imports_and_legacy_headed_exports_are_inert(self):
  code='''
import sqlite3,socket,threading,subprocess,builtins,os,sys,types
from unittest.mock import patch
api=types.ModuleType('playwright.sync_api');api.sync_playwright=lambda:None;api.TimeoutError=TimeoutError
sys.modules['playwright']=types.ModuleType('playwright');sys.modules['playwright.sync_api']=api
def forbidden(*a,**k):raise AssertionError('unexpected import side effect')
original=builtins.open
def read_only(file,mode='r',*a,**k):
 if any(x in mode for x in 'wax+'):forbidden()
 return original(file,mode,*a,**k)
with patch.object(sqlite3,'connect',forbidden),patch.object(socket.socket,'connect',forbidden),patch.object(threading.Thread,'start',forbidden),patch.object(subprocess,'Popen',forbidden),patch.object(subprocess,'run',forbidden),patch.object(builtins,'open',read_only),patch.object(os,'mkdir',forbidden),patch.object(os,'kill',forbidden):
 from scrapper.commands import browser_rating_host,browser_rating_headed
 from ops.host import browser_rating_lifecycle,browser_rating_runner,lifecycle_config
 assert browser_rating_headed.PublisherRateLimit is browser_rating_runner.PublisherRateLimit
 assert list(__import__('inspect').signature(browser_rating_headed.main).parameters)==[]
 assert list(__import__('inspect').signature(browser_rating_headed.hide_mac_browser).parameters)==['browser']
 from unittest.mock import Mock
 original=browser_rating_runner.main;browser_rating_runner.main=Mock(return_value=42)
 assert browser_rating_headed.main()==42
 assert browser_rating_runner.main.call_args.kwargs['hooks'].bridge_exchange is browser_rating_headed.bridge_exchange
 browser_rating_runner.main=original
'''
  result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
