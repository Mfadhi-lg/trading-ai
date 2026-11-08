"""Compatibility hooks, shared outcomes and inert dependency boundaries."""
import ast
import inspect
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
from scrapper.commands import browser_rating_recovery as command
from scrapper.documents.recovery.browser_rating import validation, errors
from scrapper.sources.documents import browser_rating as publisher

class BrowserPublisherOwnershipTests(unittest.TestCase):
 def test_shared_exceptions_and_compatible_local_hooks(self):
  self.assertIs(command.PublisherRateLimit,errors.PublisherRateLimit)
  self.assertIs(command.PublisherFileUnavailable,errors.PublisherFileUnavailable)
  self.assertEqual(list(inspect.signature(command.validate_report).parameters),['body','media_type','final_url','company_name','aliases'])
  self.assertEqual(list(inspect.signature(command.fetch_report).parameters),['browser','url','company_name','timeout_ms','aliases'])
  browser=Mock()
  with patch.object(command,'validate_report') as hook,patch.object(publisher,'fetch_report',return_value='ok') as owner:
   self.assertEqual(command.fetch_report(browser,'url','company'),'ok')
   self.assertIs(owner.call_args.kwargs['validate_report'],hook)
  with patch.object(command,'match_company') as hook,patch.object(validation,'validate_report',return_value='ok') as owner:
   self.assertEqual(command.validate_report(b'body','media','url','company'),'ok')
   self.assertIs(owner.call_args.kwargs['match_company'],hook)
 def test_owner_dependency_boundaries(self):
  for module in [validation,errors,publisher]:
   tree=ast.parse(inspect.getsource(module))
   for node in ast.walk(tree):
    if isinstance(node,ast.ImportFrom):
     self.assertFalse(set((node.module or '').split('.')) & {'commands','view','operations','pipeline','workflows','db'})
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
     self.assertNotIn(node.func.attr,['execute','executescript'])
 def test_transitive_inert_import(self):
  code='''
import sqlite3,socket,threading,subprocess,builtins,os
from unittest.mock import patch

def forbidden(*a,**k):raise AssertionError('unexpected import side effect')
original=builtins.open
def read_only(file,mode='r',*a,**k):
 if any(x in mode for x in 'wax+'):forbidden()
 return original(file,mode,*a,**k)
with patch.object(sqlite3,'connect',forbidden),patch.object(socket.socket,'connect',forbidden),patch.object(threading.Thread,'start',forbidden),patch.object(subprocess,'Popen',forbidden),patch.object(builtins,'open',read_only),patch.object(os,'mkdir',forbidden):
 from scrapper.documents.recovery.browser_rating import validation,errors
 from scrapper.sources.documents import browser_rating
'''
  result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
