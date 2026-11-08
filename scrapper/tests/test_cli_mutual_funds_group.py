"""Compatibility contracts for the selected MF/Trendlyne CLI adapters."""
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from scrapper import cli

class MutualFundsCliTests(unittest.TestCase):
    def invoke(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = cli.main(argv)
        return status, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_derive_writer_clock_closure_and_propagated_errors(self):
        conn = Mock()
        with patch.object(cli, 'connect', return_value=conn) as connect, patch.object(cli, 'init_db') as init, patch.object(cli, 'utc_now', return_value='fixed'), patch.object(cli, 'derive_stock_month_features_through', return_value=[Mock(to_dict=lambda: {'month': '2026-06'})]) as owner:
            status, payload = self.invoke(['derive-mf-features', '--publisher', 'ppfas', '--through-month', '2026-06'])
            self.assertEqual(status, 0)
            self.assertEqual(payload[0]['feature_sets'], [{'month': '2026-06'}])
            connect.assert_called_once_with(cli.DB_PATH); init.assert_called_once_with(conn)
            owner.assert_called_once_with(conn, publisher='ppfas', through_month='2026-06', generated_at_utc='fixed')
            conn.close.assert_called_once_with()
        for exc in (RuntimeError('schema'), sqlite3.OperationalError('db')):
            conn = Mock()
            with patch.object(cli, 'connect', return_value=conn), patch.object(cli, 'init_db', side_effect=exc):
                with self.assertRaises(type(exc)): self.invoke(['derive-mf-features', '--publisher', 'ppfas', '--through-month', '2026-06'])
                conn.close.assert_called_once_with()

    def test_exports_defaults_and_read_only_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for command, handler, flags, expected in [
                ('export-mf-features', 'write_mf_feature_export', ['--scope', 'all_scheme'], root/'all_publishers/all_scheme/through-2026-06.json'),
                ('mf-coverage-report', 'write_mf_coverage_report', ['--publisher', 'ppfas', '--months', '2'], root/'ppfas/through-2026-06.json')]:
                for coverage, code in [('complete', 0), ('incomplete', 1)]:
                    conn = Mock(); written=root/'report.json'; written.write_text(json.dumps({'coverage_status':coverage,'summary':{'accepted':1}}))
                    with patch.object(cli, 'MF_FEATURE_EXPORTS', root), patch.object(cli, 'MF_COVERAGE_EXPORTS', root), patch.object(cli, 'connect', return_value=conn) as connect, patch.object(cli, 'init_db') as init, patch.object(cli, handler, return_value=written) as owner:
                        status, payload = self.invoke([command, '--through-month', '2026-06', *flags])
                        self.assertEqual(status, code if command=='mf-coverage-report' else 0)
                        self.assertEqual(owner.call_args.kwargs['output'], expected)
                        self.assertEqual(payload[0]['output'], str(written.resolve()))
                        connect.assert_called_once_with(cli.DB_PATH, read_only=True); init.assert_called_once_with(conn); conn.close.assert_called_once_with()

    def test_crosscheck_delegation_and_caught_exception_boundary(self):
        argv=['crosscheck-mfdata','--publisher','ppfas','--scheme-code','active_a','--family-id','1','--month','2026-06','--path','/tmp/fixture.json']
        with patch.object(cli,'execute_mfdata_crosscheck',return_value={'conflicts':1}) as owner:
            self.assertEqual(self.invoke(argv),(0,[{'status':'ok','conflicts':1}]))
            self.assertEqual(owner.call_args.kwargs['disclosure_month'],'2026-06')
            self.assertEqual(owner.call_args.kwargs['path'],Path('/tmp/fixture.json'))
        for exc in (OSError('missing'),ValueError('bad'),RuntimeError('refused')):
            with patch.object(cli,'execute_mfdata_crosscheck',side_effect=exc): self.assertEqual(self.invoke(argv),(2,[{'status':'error','error':str(exc)}]))
        with patch.object(cli,'execute_mfdata_crosscheck',side_effect=sqlite3.OperationalError('db')):
            with self.assertRaises(sqlite3.OperationalError): self.invoke(argv)

    def test_import_modes_quarantine_rejection_and_errors(self):
        for command, planner, flags in [('import-mf','plan_import',['--publisher','ppfas','--month','2026-06']),('import-mf-directory','plan_directory_import',[])]:
            for dry in (True,False):
                for action,state,code in [('inventory','accepted',0),('quarantine','quarantine',1),('inventory','rejected',0 if dry else 1)]:
                    payload={'files':[{'proposed_action':action,'import_state':state}]}
                    with patch.object(cli,planner,return_value=['proposal']) as plan, patch.object(cli,'import_plan_payload',return_value=payload) as preview, patch.object(cli,'execute_import',return_value=payload) as execute:
                        self.assertEqual(self.invoke([command,'--path','/tmp/inbox',*flags,*(['--dry-run'] if dry else [])]),(code,[{'status':'ok',**payload}]))
                        self.assertEqual(plan.call_args.kwargs['path'],Path('/tmp/inbox'))
                        if dry: preview.assert_called_once_with(['proposal'],dry_run=True); execute.assert_not_called()
                        else: execute.assert_called_once_with(['proposal'],db_path=cli.DB_PATH,export_root=cli.MF_WORKBOOK_EXPORTS); preview.assert_not_called()
            with patch.object(cli,planner,side_effect=ValueError('bad input')): self.assertEqual(self.invoke([command,'--path','/tmp/inbox',*flags])[0],2)

    def test_trendlyne_plan_read_only_persist_and_lazy_symbols(self):
        for persist in (False,True):
            conn=Mock(); plan=Mock(to_dict=lambda:{'requests':2})
            with patch.object(cli,'connect',return_value=conn) as connect, patch.object(cli,'init_db') as init, patch.object(cli,'build_trendlyne_pilot_plan',return_value=plan) as owner, patch.object(cli,'register_trendlyne_pilot_plan',return_value=(7,False)) as register, patch.object(cli,'load_attribute',return_value=('RBA',)):
                status,payload=self.invoke(['trendlyne-pilot-plan','--end-period','2026-06',*(['--persist'] if persist else [])])
                self.assertEqual(status,0); self.assertEqual(payload[0],{'event':'request_plan','requests':2})
                connect.assert_called_once_with(cli.DB_PATH,read_only=not persist); init.assert_called_once_with(conn); conn.close.assert_called_once_with()
                self.assertEqual(owner.call_args.kwargs['symbols'],('RBA',))
                self.assertEqual(register.call_count,int(persist))
                if persist: self.assertEqual(payload[1],{'event':'plan_registered','plan_id':7})

    def test_trendlyne_batch_callback_failure_code_and_explicit_symbols(self):
        for failed in (0,1):
            def run(**kwargs):
                kwargs['on_plan'](Mock(to_dict=lambda:{'requests':1}))
                return Mock(failed_request_count=failed,to_dict=lambda:{'failed_request_count':failed})
            with patch.object(cli,'run_trendlyne_pilot_batch',side_effect=run) as owner, patch.object(cli,'load_attribute') as lazy:
                status,payload=self.invoke(['trendlyne-pilot-backfill','--end-period','2026-06','--symbol','RBA','--batch-size','1'])
                self.assertEqual(status,failed); self.assertEqual(payload[0]['event'],'request_plan'); self.assertEqual(payload[1]['event'],'batch_result')
                self.assertEqual(owner.call_args.kwargs['symbols'],('RBA',)); lazy.assert_not_called()

    def test_scrape_one_trendlyne_route_and_period_output(self):
        result=SimpleNamespace(run_id=1,fetch_id=2,blob_id=3,parse_id=4,company_id=5,reused_parse=True,export_path=Path('/tmp/result.json'),requested_source_period='2026-05',resolved_source_period='2026-05')
        with patch.object(cli,'scrape_trendlyne_company',return_value=result) as owner, patch.object(cli,'scrape_screener_company') as screener:
            status,payload=self.invoke(['scrape-one','--source','trendlyne','--symbol','RBA','--period','2026-05'])
            self.assertEqual(status,0); self.assertEqual(payload[0]['resolved_source_period'],'2026-05')
            owner.assert_called_once_with('RBA',requested_source_period='2026-05',db_path=cli.DB_PATH); screener.assert_not_called()

    def test_output_guard_precedes_adapter(self):
        with patch.object(cli,'assert_configured_writable_targets',side_effect=RuntimeError('production refused')), patch.object(cli,'write_mf_feature_export') as owner:
            with self.assertRaisesRegex(RuntimeError,'production refused'): self.invoke(['export-mf-features','--scope','all_scheme','--through-month','2026-06','--output','/tmp/refused.json'])
            owner.assert_not_called()
