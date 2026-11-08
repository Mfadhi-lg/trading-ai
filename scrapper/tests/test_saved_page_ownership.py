"""Saved-page repair ownership and compatibility boundaries."""
import ast
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from scrapper.commands import recover_quarter_document_links as facade
from scrapper.documents.recovery import saved_screener_page as owner
from scrapper.db.documents.recovery import saved_screener_page as queries


class SavedPageOwnershipTests(unittest.TestCase):
    def test_selection_facade_preserves_arguments_and_local_hooks(self):
        conn=object();token=object()
        with patch.object(owner,'candidates',return_value=token) as delegate:
            self.assertIs(facade.candidates(conn,period='2026-06-30',limit=7),token)
        self.assertEqual(delegate.call_args.args,(conn,))
        self.assertEqual(delegate.call_args.kwargs,{'period':'2026-06-30','limit':7,'_hooks':facade})
        original=owner.call_months
        with patch.object(facade,'call_months',return_value=['fixture-month']),patch.object(facade,'PROMOTER_VERSION','fixture-version'),patch.object(owner,'list_repair_candidates',return_value=[]) as query:
            self.assertEqual(facade.candidates(conn,period='2026-06-30',limit=7),[])
        self.assertEqual(query.call_args.kwargs,{'period':'2026-06-30','limit':7,'months':['fixture-month'],'promoter_version':'fixture-version'})
        self.assertIs(owner.call_months,original)

    def test_direct_batch_uses_exact_existing_acquisition_contract(self):
        conn=object()
        enqueue=Mock(side_effect=[ValueError('fixture failure'),{'queue':{'created_job_count':2},'promotions':{'ppt':{'status_counts':{'PROMOTED':1,'skip':2}},'recording':{'status_counts':{'PROMOTED':1}}}}])
        result=owner.repair_selected(conn,selected=[(10,20),(11,21)],period='2026-06-30',enqueue=enqueue)
        self.assertEqual(result,{'companies':1,'created_jobs':2,'promoted_links':2,'errors':[{'company_id':10,'parse_id':20,'error':'ValueError: fixture failure'}]})
        self.assertEqual([x.kwargs for x in enqueue.call_args_list],[{'parse_id':p,'verified_company_id':c,'resolved_result_period':'2026-06','include_queue_counts':False} for c,p in [(10,20),(11,21)]])
        self.assertTrue(all(x.args==(conn,) for x in enqueue.call_args_list))

    def test_layers_and_transitive_import_help_are_inert(self):
        for module in (owner,queries):
            tree=ast.parse(Path(module.__file__).read_text())
            if module is owner:
                self.assertFalse(any(isinstance(n,ast.Attribute) and n.attr in {'execute','executemany','executescript'} for n in ast.walk(tree)))
            for n in ast.walk(tree):
                if isinstance(n,ast.ImportFrom):
                    self.assertFalse(set((n.module or '').split('.')) & {'pipeline','operations','view','commands','cli','workflows'})
        script='''import sys,socket,sqlite3,subprocess,threading,runpy
sys.dont_write_bytecode=True
def deny(*a,**k):raise AssertionError('forbidden side effect')
def audit(event,args):
 if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write during import/help')
 if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):raise AssertionError(event)
sys.addaudithook(audit)
socket.socket.connect=sqlite3.connect=subprocess.Popen=threading.Thread.start=deny
import scrapper.documents.recovery.saved_screener_page
import scrapper.db.documents.recovery.saved_screener_page
import scrapper.commands.recover_quarter_document_links as command
sys.argv=['repair','--help']
command.main()
'''
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        script=script.replace('import scrapper.commands.recover_quarter_document_links as command\nsys.argv=[\'repair\',\'--help\']\ncommand.main()',"sys.argv=['repair','--help']\nrunpy.run_module('scrapper.commands.recover_quarter_document_links',run_name='__main__')")
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
