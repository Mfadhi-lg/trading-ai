"""C17.5 compatibility adapters, dependency direction and inert imports."""
import ast, inspect, subprocess, sys, unittest
from pathlib import Path
from unittest.mock import patch
from scrapper.commands import diagnose_retryable_failures as diagnostic
from scrapper.documents.recovery import diagnostics
from scrapper.exports import document_recovery, retryable_failure_diagnosis
from scrapper.pipeline import document_download_recovery as facade
from scrapper.db.documents.recovery import reports
class RecoveryReportOwnershipTests(unittest.TestCase):
 def test_compatible_arguments_and_local_hooks(self):
  for old,owner,name in [(diagnostic,diagnostics,'diagnose'),(facade,document_recovery,'build_document_recovery_report')]:
   fn=getattr(old,name);sig=inspect.signature(fn);args=[object() for p in sig.parameters.values() if p.kind==p.POSITIONAL_OR_KEYWORD];kwargs={p.name:object() for p in sig.parameters.values() if p.kind==p.KEYWORD_ONLY};token=object()
   with patch.object(owner,name,return_value=token) as call:self.assertIs(fn(*args,**kwargs),token)
   self.assertEqual(call.call_args.args,tuple(args));self.assertEqual(call.call_args.kwargs,dict(kwargs,_hooks=old))
  self.assertIs(diagnostic.render_markdown,retryable_failure_diagnosis.render_markdown)
 def test_layers_and_sql_ownership(self):
  for module in [diagnostic,diagnostics,document_recovery,retryable_failure_diagnosis]:
   tree=ast.parse(Path(module.__file__).read_text())
   self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in {'execute','executemany','executescript'} for n in ast.walk(tree)))
  for module in [diagnostics,document_recovery,reports]:
   for n in ast.walk(ast.parse(Path(module.__file__).read_text())):
    if isinstance(n,ast.ImportFrom):self.assertFalse(set((n.module or '').split('.')) & {'pipeline','commands','view','workflows','operations'})
 def test_transitive_imports_and_help_are_inert(self):
  script="""import sys,socket,sqlite3,subprocess,threading
sys.dont_write_bytecode=True
def deny(*a,**k):raise AssertionError('forbidden side effect')
def audit(event,args):
 if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write during import')
 if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):raise AssertionError(event)
sys.addaudithook(audit)
socket.socket.connect=sqlite3.connect=subprocess.Popen=threading.Thread.start=deny
import scrapper.db.documents.recovery.reports
import scrapper.documents.recovery.diagnostics
import scrapper.exports.document_recovery
import scrapper.exports.retryable_failure_diagnosis
import scrapper.commands.diagnose_retryable_failures as command
import scrapper.pipeline.document_download_recovery
sys.argv=['diagnose','--help']
command.main()
"""
  result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stderr);self.assertIn('--scope-id',result.stdout)
