"""Optional v1 wire contract and bridge ownership boundaries."""
import ast,inspect,io,json,subprocess,sys,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from scrapper.commands import browser_rating_recovery as command
from scrapper.documents.recovery.browser_rating import bridge,protocol
from scrapper.db.documents.recovery import browser_rating_bridge as repository
class BrowserBridgeProtocolTests(unittest.TestCase):
 def dispatch(self,requests):
  replies=[];conn=Mock();hooks={n:Mock() for n in ['worker_state','release_owned_jobs','claim','cool_down','finish','validate_report','company_names','import_report','DownloadPolicy','DownloadResult','utc_now']}
  hooks['release_owned_jobs'].return_value=3
  bridge.serve(conn,SimpleNamespace(run_id='run'),lines=[json.dumps(r) for r in requests],emit=replies.append,**hooks)
  return replies,conn,hooks
 def test_v1_release_and_legacy_release(self):
  replies,conn,hooks=self.dispatch([{'action':'release','protocol_version':1},{'action':'release'}])
  self.assertEqual(replies,[{'released':3,'protocol_version':1},{'released':3}]);conn.execute.assert_not_called();hooks['worker_state'].assert_not_called()
 def test_invalid_versions_rejected_before_side_effects(self):
  replies,conn,hooks=self.dispatch([{'action':'claim','protocol_version':v} for v in [2,0,-1,'1',True,1.0,None,{},[]]])
  self.assertTrue(all(r=={'error':'ValueError: Unsupported bridge protocol version'} for r in replies));conn.execute.assert_not_called()
  for hook in hooks.values():hook.assert_not_called()
 def test_unknown_versioned_action_rejected_before_writes(self):
  replies,conn,hooks=self.dispatch([{'action':'unknown','protocol_version':1}]);self.assertEqual(replies,[{'error':'ValueError: Unknown bridge action'}]);conn.execute.assert_not_called()
  for hook in hooks.values():hook.assert_not_called()
 def test_client_legacy_v1_disconnect_errors_and_mismatch(self):
  def channel(reply):return SimpleNamespace(stdin=io.StringIO(),stdout=io.StringIO(reply))
  c=channel('{"released":3}\n');self.assertEqual(protocol.exchange(c,{'action':'release'}),{'released':3});self.assertEqual(json.loads(c.stdin.getvalue()),{'action':'release'})
  c=channel('{"released":3,"protocol_version":1}\n');protocol.exchange(c,{'action':'release'},version=1);self.assertEqual(json.loads(c.stdin.getvalue()),{'action':'release','protocol_version':1})
  for reply in ['{"released":3}\n','{"protocol_version":true}\n','{"protocol_version":2}\n']:
   with self.assertRaisesRegex(ValueError,'response protocol'):protocol.exchange(channel(reply),{'action':'release'},version=1)
  with self.assertRaisesRegex(RuntimeError,'disconnected'):protocol.exchange(channel(''),{'action':'claim'})
  with self.assertRaisesRegex(ValueError,'owned'):protocol.exchange(channel('{"error":"not owned"}\n'),{'action':'finish'},version=1)
  c=channel('');request={'action':'release'}
  with self.assertRaises(ValueError):protocol.exchange(c,request,version=2)
  self.assertEqual(c.stdin.getvalue(),'');self.assertEqual(request,{'action':'release'})
 def test_local_hooks_and_unchanged_signatures(self):
  self.assertEqual(list(inspect.signature(command._serve_bridge).parameters),['conn','args'])
  with patch.object(bridge,'serve') as owner,patch.object(command,'finish',Mock()) as finish:
   command._serve_bridge('conn','args');self.assertIs(owner.call_args.kwargs['finish'],finish);self.assertIs(owner.call_args.kwargs['lines'],sys.stdin)
 def test_sql_dependencies_and_inert_imports(self):
  for module in [bridge,protocol]:
   tree=ast.parse(inspect.getsource(module));self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ['execute','executescript'] for n in ast.walk(tree)))
  for module in [bridge,protocol,repository]:
   for n in ast.walk(ast.parse(inspect.getsource(module))):
    if isinstance(n,ast.ImportFrom):self.assertFalse(any(x in (n.module or '').split('.') for x in ['commands','view','operations','pipeline','workflows','sources']))
  code='''
import sqlite3,socket,threading,subprocess,builtins,os
from unittest.mock import patch
def forbidden(*a,**k):raise AssertionError('unexpected import side effect')
original=builtins.open
def read_only(file,mode='r',*a,**k):
 if any(x in mode for x in 'wax+'):forbidden()
 return original(file,mode,*a,**k)
with patch.object(sqlite3,'connect',forbidden),patch.object(socket.socket,'connect',forbidden),patch.object(threading.Thread,'start',forbidden),patch.object(subprocess,'Popen',forbidden),patch.object(builtins,'open',read_only),patch.object(os,'mkdir',forbidden):
 from scrapper.documents.recovery.browser_rating import bridge,protocol
 from scrapper.db.documents.recovery import browser_rating_bridge
'''
  result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stderr)
