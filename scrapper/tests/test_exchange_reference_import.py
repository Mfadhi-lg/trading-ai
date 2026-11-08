"""C09.1 offline contract: explicit imports and inert UI reads on disposable DBs."""
import contextlib
import csv
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from rich.console import Console
from scrapper import cli
from scrapper.db.database import connect
from scrapper.sources.exchange.reference import COLUMNS, locate_reference_file, read_reference_rows
from scrapper.workflows.exchange.reference_import import import_exchange_reference
from scrapper.view import shell
from scrapper.tests.database_fixtures import prepare_database
from scrapper.tests.test_e01_exchange_universe import NSE_CURRENT, registry_thresholds
from scrapper.pipeline.exchange_universe import refresh_exchange_universe


class ReferenceImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / 'fixture.db'
        prepare_database(self.db)
        master = self.root / 'master.csv'; master.write_bytes(NSE_CURRENT)
        refresh_exchange_universe(dataset='nse_equity_master', file=master,
            effective_date='2026-08-01', db_path=self.db, snapshot_root=self.root/'snapshots',
            now_utc=lambda: '2026-08-01T12:00:00+00:00', thresholds=registry_thresholds('nse_equity_master'))
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        self.rows = [{c:r[c] for c in COLUMNS} for r in self.conn.execute('SELECT * FROM exchange_securities ORDER BY exchange_security_id')]
        # Controlled fixture transformation: retained source snapshots, no dangling versions.
        for row in self.rows: row['current_version_id'] = None
        with self.conn: self.conn.execute('DELETE FROM exchange_securities')
        self.file = self.root/'reference.csv'
        self.write_rows(self.rows)

    def write_rows(self, rows, header=COLUMNS):
        with self.file.open('w', newline='') as f:
            writer=csv.DictWriter(f,fieldnames=header); writer.writeheader(); writer.writerows(rows)

    def dump(self):
        return '\n'.join(self.conn.iterdump())

    def test_success_preserves_every_exported_value_and_repeat_refuses(self):
        before=self.dump()
        self.assertEqual(import_exchange_reference(self.conn,self.file),{'status':'imported','imported_rows':2})
        actual=[{c:r[c] for c in COLUMNS} for r in self.conn.execute('SELECT * FROM exchange_securities ORDER BY exchange_security_id')]
        self.assertEqual(actual,self.rows)
        self.assertNotEqual(before,self.dump())
        after=self.dump()
        with self.assertRaisesRegex(ValueError,'nonempty'): import_exchange_reference(self.conn,self.file)
        self.assertEqual(after,self.dump())
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_invalid_headers_rows_enums_and_duplicates_are_atomic(self):
        before=self.dump()
        cases=[('exchange_security_id','-1'),('is_present','2'),('segment','bad'),
               ('exchange','bad'),('first_observed_date','bad'),('created_at_utc','2026-01-01'),
               ('current_name_raw',''),('bse_scrip_code','123'),('current_status','bad')]
        for key,value in cases:
            rows=[dict(r) for r in self.rows]; rows[1][key]=value; self.write_rows(rows)
            with self.subTest(key=key),self.assertRaises(ValueError): import_exchange_reference(self.conn,self.file)
            self.assertEqual(before,self.dump())
        self.write_rows([self.rows[0],self.rows[0]])
        with self.assertRaisesRegex(ValueError,'duplicate'): import_exchange_reference(self.conn,self.file)
        self.write_rows([],header=['evil_column'])
        with self.assertRaisesRegex(ValueError,'header'): import_exchange_reference(self.conn,self.file)
        self.write_rows([])
        with self.assertRaisesRegex(ValueError,'no rows'): import_exchange_reference(self.conn,self.file)
        self.assertEqual(before,self.dump())

    def test_missing_source_snapshot_and_version_refuse_without_writes(self):
        before=self.dump()
        for key in ['first_observed_snapshot_id','current_version_id']:
            rows=[dict(r) for r in self.rows]; rows[1][key]=999999; self.write_rows(rows)
            with self.subTest(key=key), self.assertRaises(ValueError): import_exchange_reference(self.conn,self.file)
            self.assertEqual(before,self.dump())

    def test_constraint_failure_rolls_back_entire_batch(self):
        self.conn.execute("CREATE TRIGGER refuse_second BEFORE INSERT ON exchange_securities WHEN NEW.exchange_key='BETANEW' BEGIN SELECT RAISE(ABORT,'fixture refusal'); END")
        self.conn.commit(); before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError): import_exchange_reference(self.conn,self.file)
        self.assertEqual(before,self.dump())

    def test_read_only_connection_and_disabled_foreign_keys_refuse(self):
        with contextlib.closing(connect(self.db,read_only=True)) as reader:
            with self.assertRaisesRegex(ValueError,'writable'): import_exchange_reference(reader,self.file)
        self.conn.execute('PRAGMA foreign_keys=OFF')
        with self.assertRaisesRegex(ValueError,'foreign-key'): import_exchange_reference(self.conn,self.file)

    def test_empty_ui_search_cache_and_notice_are_inert(self):
        before=self.dump(); count=self.conn.total_changes
        with patch.object(shell,'connect',side_effect=AssertionError('no writer')):
            shell.ensure_exchange_securities_seeded(self.conn)
            shell.CompanySlashCompleter(self.conn)
            shell.search_companies(self.conn,'Alpha')
            self.assertIn('empty',shell.exchange_reference_notice(self.conn))
            output=io.StringIO(); shell.render_search_view(Console(file=output),self.conn,'absent')
            self.assertIn('exchange-import-reference',output.getvalue())
        self.assertEqual(before,self.dump()); self.assertEqual(count,self.conn.total_changes)

    def test_populated_queries_and_completions_work_read_only(self):
        import_exchange_reference(self.conn,self.file); before=self.dump()
        with contextlib.closing(connect(self.db,read_only=True)) as reader:
            self.assertIsNone(shell.exchange_reference_notice(reader))
            self.assertTrue(any(r['nse']=='ALPHA' for r in shell.search_companies(reader,'Alpha')))
            self.assertTrue(any(r['nse']=='ALPHA' for r in shell.CompanySlashCompleter(reader).company_cache))
        self.assertEqual(before,self.dump())

    def test_shell_missing_database_does_not_create_paths(self):
        missing=self.root/'absent'/'database.db'
        with patch.object(shell,'Console',return_value=Console(file=io.StringIO())):
            self.assertEqual(shell.run_interactive_shell(missing),2)
        self.assertFalse(missing.parent.exists())

    def test_shell_startup_search_help_exit_use_only_read_only_connection(self):
        original=shell.connect; calls=[]; before=self.dump()
        def opener(*args,**kwargs):
            calls.append(kwargs); return original(*args,**kwargs)
        with patch.object(shell,'connect',side_effect=opener),patch.object(shell,'HAS_PROMPT_TOOLKIT',False), patch.object(shell,'readline',Mock(),create=True), \
             patch('builtins.input',side_effect=['/search absent','/help','/exit']), \
             patch.object(shell,'Console',return_value=Console(file=io.StringIO())):
            self.assertEqual(shell.run_interactive_shell(self.db),0)
        self.assertEqual(calls,[{'read_only':True}]); self.assertEqual(before,self.dump())

    def test_explicit_shell_requeue_retains_writable_dispatch(self):
        original=shell.connect; calls=[]
        def opener(*args,**kwargs): calls.append(kwargs); return original(*args,**kwargs)
        def requeue(writer):
            self.assertEqual(writer.execute('PRAGMA query_only').fetchone()[0],0)
            return 0
        with patch.object(shell,'connect',side_effect=opener),patch.object(shell,'HAS_PROMPT_TOOLKIT',False), patch.object(shell,'readline',Mock(),create=True), \
             patch('builtins.input',side_effect=['/requeue-failed','/exit']), \
             patch.object(shell,'requeue_failed_screener_companies',side_effect=requeue) as action, \
             patch.object(shell,'Console',return_value=Console(file=io.StringIO())):
            self.assertEqual(shell.run_interactive_shell(self.db),0)
        self.assertEqual(calls,[{'read_only':True},{}]); action.assert_called_once()

    def test_cli_success_error_and_no_implicit_schema_creation(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(['exchange-import-reference','--db',str(self.db),'--file',str(self.file)]),0)
        self.assertEqual(json.loads(output.getvalue()),{'status':'imported','imported_rows':2})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['exchange-import-reference','--db',str(self.db),'--file',str(self.file)]),2)
            missing=self.root/'missing.db'
            self.assertEqual(cli.main(['exchange-import-reference','--db',str(missing),'--file',str(self.file)]),2)
        self.assertFalse(missing.exists())

    def test_status_and_uninitialized_shell_are_inert(self):
        before=self.dump()
        with patch.object(cli,'run_live_monitor',side_effect=lambda conn,**kw: self.assertEqual(conn.execute('PRAGMA query_only').fetchone()[0],1)):
            self.assertEqual(cli.main(['monitor','--db',str(self.db)]),0)
        self.assertEqual(before,self.dump())
        empty=self.root/'empty.db'; sqlite3.connect(empty).close()
        original=empty.read_bytes()
        with patch.object(shell,'Console',return_value=Console(file=io.StringIO())):
            self.assertEqual(shell.run_interactive_shell(empty),2)
        self.assertEqual(original,empty.read_bytes())

    def test_resource_locator_preference_and_legacy_fallback(self):
        self.assertEqual(locate_reference_file(self.root),self.root/'exchange_securities_master.csv')
        preferred=self.root/'resources/exchange/exchange_securities_master.csv'
        preferred.parent.mkdir(parents=True); preferred.write_text('resource')
        self.assertEqual(locate_reference_file(self.root),preferred)
        self.assertGreater(len(read_reference_rows(locate_reference_file())),1000)

    def test_import_and_help_have_no_writes_network_or_process_startup(self):
        script=r'''
import builtins, pathlib, sqlite3, socket, subprocess, threading
from unittest.mock import patch, Mock
original_open=builtins.open
original_path_open=pathlib.Path.open
def blocked(*a,**k): raise AssertionError('forbidden side effect')
def safe_open(file,mode='r',*a,**k):
    if any(c in mode for c in 'wax+'): blocked()
    return original_open(file,mode,*a,**k)
def safe_path_open(self,mode='r',*a,**k):
    if any(c in mode for c in 'wax+'): blocked()
    return original_path_open(self,mode,*a,**k)
with patch('builtins.open',safe_open),patch.object(pathlib.Path,'open',safe_path_open), \
     patch.object(pathlib.Path,'mkdir',blocked),patch.object(sqlite3,'connect',blocked), \
     patch.object(socket.socket,'connect',blocked),patch.object(subprocess,'Popen',blocked), \
     patch.object(threading.Thread,'start',blocked):
    import scrapper.view.shell
    import scrapper.workflows.exchange.reference_import
    from scrapper.cli import main
    try: main(['exchange-import-reference','--help'])
    except SystemExit as exc: assert exc.code==0
'''
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
