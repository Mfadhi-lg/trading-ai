"""Reusable refresh ownership, compatibility and import safety."""
import ast,importlib.util,subprocess,sys,unittest
from pathlib import Path
from unittest.mock import patch
from scrapper.documents.recovery import rating_metadata_refresh as owner
from scrapper.db.documents.recovery import rating_metadata_refresh as queries
P=Path(__file__).resolve().parents[2]/'artifacts/rating-link-refresh-20261001/refresh.py'
spec=importlib.util.spec_from_file_location('rating_refresh_ownership_campaign',P)
facade=importlib.util.module_from_spec(spec);sys.modules[spec.name]=facade;spec.loader.exec_module(facade)

class RatingMetadataOwnershipTests(unittest.TestCase):
    def test_dated_facade_supplies_exact_campaign_and_hooks(self):
        token=object()
        with patch.object(owner,'run_refresh',return_value=token) as run:
            self.assertIs(facade.main(),token)
        self.assertEqual(run.call_args.kwargs,dict(recovery_run=facade.RECOVERY_RUN,failed_hosts=facade.FAILED_HOSTS,current_hosts=facade.CURRENT_HOSTS,db_path=facade.DB_PATH,snapshots=facade.SNAPSHOTS,report=facade.REPORT,save_report=facade.save_report,_hooks=facade))
    def test_selection_facade_supplies_frozen_dated_filter(self):
        conn=object();token=object()
        with patch.object(queries,'selected_targets',return_value=token) as select:
            self.assertIs(facade.selected_targets(conn),token)
        self.assertEqual(select.call_args.args,(conn,))
        self.assertEqual(select.call_args.kwargs,dict(recovery_run=facade.RECOVERY_RUN,failed_hosts=facade.FAILED_HOSTS))
    def test_layers_and_transitive_campaign_import_are_inert(self):
        for module in (owner,queries):
            tree=ast.parse(Path(module.__file__).read_text())
            if module is owner:self.assertFalse(any(isinstance(n,ast.Attribute) and n.attr in {'execute','executemany','executescript'} for n in ast.walk(tree)))
            for n in ast.walk(tree):
                if isinstance(n,ast.ImportFrom):self.assertFalse(set((n.module or '').split('.')) & {'pipeline','commands','operations','view','cli','workflows'})
        script='''import sys,socket,sqlite3,subprocess,threading,runpy
sys.dont_write_bytecode=True
def deny(*a,**k):raise AssertionError('forbidden side effect')
def audit(event,args):
 if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write during import')
 if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):raise AssertionError(event)
sys.addaudithook(audit)
socket.socket.connect=sqlite3.connect=subprocess.Popen=threading.Thread.start=deny
import scrapper.documents.recovery.rating_metadata_refresh
import scrapper.db.documents.recovery.rating_metadata_refresh
runpy.run_path('artifacts/rating-link-refresh-20261001/refresh.py',run_name='inert_campaign')
'''
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
