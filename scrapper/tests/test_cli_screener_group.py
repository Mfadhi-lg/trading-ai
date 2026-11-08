"""Screener adapter contracts through the compatible CLI facade."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from scrapper import cli
from scrapper.cli_groups.screener import COMMANDS

class ScreenerCliTests(unittest.TestCase):
    def invoke(self, argv):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = cli.main(argv)
        return code, json.loads(stream.getvalue())

    def test_membership(self):
        names = cli._parser()._subparsers._group_actions[0].choices
        self.assertEqual(set(COMMANDS), {n for n in names if n.startswith('screener-')} | {'reprocess-screener', 'financial-coverage-audit'})

    def test_discovery_and_worker_outcomes_arguments_and_errors(self):
        cases = [(['screener-discover-results','--query-role','results','--query','sales','--fixture','/tmp/screen.html','--dry-run'], 'discover_screener_results', [({'coverage_status':'complete'},0),({'coverage_status':'incomplete'},1)], dict(query_role='results',query='sales',fixture=Path('/tmp/screen.html'),dry_run=True,db_path=cli.DB_PATH)),
                 (['screener-company-worker','--due','--limit','2','--dry-run'], 'run_screener_company_worker', [({'summary':{'outcome_counts':{'accepted':2}}},0),({'summary':{'outcome_counts':{'failed_terminal':0}}},0),({'summary':{'outcome_counts':{'failed_retryable':1}}},1)],dict(due=True,limit=2,dry_run=True,db_path=cli.DB_PATH))]
        for argv, owner, outcomes, kwargs in cases:
            for payload, expected in outcomes:
                with self.subTest(argv=argv,payload=payload), patch.object(cli,owner,return_value=payload) as call:
                    self.assertEqual(self.invoke(argv),(expected,{'status':'ok',**payload}))
                    call.assert_called_once_with(**kwargs)
            for error in (OSError('offline'),ValueError('invalid'),RuntimeError('refused')):
                with patch.object(cli,owner,side_effect=error):
                    self.assertEqual(self.invoke(argv),(2,{'status':'error','error':str(error)}))
            with patch.object(cli,owner,side_effect=KeyError('propagate')):
                with self.assertRaises(KeyError): self.invoke(argv)

    def test_requeues_keep_default_and_explicit_scope(self):
        for command,owner in [('screener-requeue-failed','requeue_failed_screener_companies'),('screener-requeue-incomplete-metrics','requeue_incomplete_metric_screener_companies')]:
            for flags in ([],['--path','/company/A/','--path','/company/B/','--limit','3','--dry-run']):
                kw=dict(paths=('/company/A/','/company/B/') if flags else (),limit=3 if flags else None,dry_run=bool(flags),db_path=cli.DB_PATH)
                if command=='screener-requeue-failed': kw['statuses']=('failed_terminal',)
                with patch.object(cli,owner,return_value={'queued':2}) as call:
                    self.assertEqual(self.invoke([command,*flags]),(0,{'status':'ok','queued':2}))
                    call.assert_called_once_with(**kw)
            for error in (OSError('offline'),ValueError('invalid'),RuntimeError('refused')):
                with patch.object(cli,owner,side_effect=error): self.assertEqual(self.invoke([command])[0],2)
        with patch.object(cli,'requeue_failed_screener_companies',return_value={}) as call:
            self.invoke(['screener-requeue-failed','--status','failed_retryable','--status','failed_terminal'])
            self.assertEqual(call.call_args.kwargs['statuses'],('failed_retryable','failed_terminal'))

    def test_report_is_read_only_closes_and_guard_runs_first(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'coverage.json'
            for state, expected in [('complete',0),('incomplete',1)]:
                output.write_text(json.dumps({'coverage_status':state,'summary':{'pending':2}}))
                conn=Mock()
                with patch.object(cli,'connect',return_value=conn) as connect, patch.object(cli,'init_db') as init, patch.object(cli,'write_screener_discovery_coverage',return_value=output) as owner:
                    self.assertEqual(self.invoke(['screener-discovery-coverage','--output',str(output)]),(expected,{'status':'ok','coverage_status':state,'summary':{'pending':2},'output':str(output.resolve())}))
                    connect.assert_called_once_with(cli.DB_PATH,read_only=True); init.assert_called_once_with(conn); owner.assert_called_once_with(conn,output); conn.close.assert_called_once_with()
            for error in (OSError('offline'),ValueError('invalid'),RuntimeError('refused')):
                conn=Mock()
                with patch.object(cli,'connect',return_value=conn), patch.object(cli,'init_db'), patch.object(cli,'write_screener_discovery_coverage',side_effect=error):
                    self.assertEqual(self.invoke(['screener-discovery-coverage','--output',str(output)])[0],2); conn.close.assert_called_once_with()
        with patch.object(cli,'assert_configured_writable_targets',side_effect=RuntimeError('refused')), patch.object(cli,'connect') as connect:
            with self.assertRaisesRegex(RuntimeError,'refused'): self.invoke(['screener-discovery-coverage','--output','/tmp/refused.json'])
            connect.assert_not_called()

    def test_period_coverage_keeps_owner_exit_codes_and_exceptions(self):
        for payload, expected in [({'missing_count':0,'refresh_failures':0},0),({'missing_count':1,'refresh_failures':0},1),({'missing_count':0,'refresh_failures':1},1)]:
            with patch('scrapper.commands.screener_period_coverage.run_period_coverage',return_value=payload) as owner:
                self.assertEqual(self.invoke(['screener-period-coverage','--period','2026-06-30','--symbol','A','--repair','--refresh','--limit','2']),(expected,payload))
                owner.assert_called_once_with(period='2026-06-30',symbols=['A'],repair=True,refresh=True,limit=2,db_path=cli.DB_PATH)
        with patch('scrapper.commands.screener_period_coverage.run_period_coverage',side_effect=ValueError('unchanged')):
            with self.assertRaisesRegex(ValueError,'unchanged'): self.invoke(['screener-period-coverage','--period','2026-06-30'])

    def test_scrape_reprocess_and_trendlyne_fallback_fields(self):
        result=SimpleNamespace(run_id=1,fetch_id=2,blob_id=3,parse_id=4,company_id=5,reused_parse=True,export_path=Path('/tmp/result.json'),requested_source_period='2026-06',resolved_source_period='2026-06')
        cases=[(['scrape-one','--source','screener','--symbol','A'],'scrape_screener_company',('A','standalone'),dict(db_path=cli.DB_PATH)),(['reprocess-screener','--symbol','A','--blob-id','3','--peer-blob-id','4'],'reprocess_screener_blobs',('A','consolidated',3,4),dict(db_path=cli.DB_PATH)),(['scrape-one','--source','trendlyne','--symbol','A','--period','2026-06'],'scrape_trendlyne_company',('A',),dict(requested_source_period='2026-06',db_path=cli.DB_PATH))]
        for argv,owner,args,kw in cases:
            with patch.object(cli,owner,return_value=result) as call:
                status,output=self.invoke(argv); self.assertEqual(status,0); call.assert_called_once_with(*args,**kw)
                expected={'status':'ok','run_id':1,'fetch_id':2,'blob_id':3,'parse_id':4,'company_id':5,'reused_parse':True,'export_path':'/tmp/result.json'}
                if owner=='scrape_trendlyne_company': expected.update(requested_source_period='2026-06',resolved_source_period='2026-06')
                self.assertEqual(output,expected)
            with patch.object(cli,owner,side_effect=RuntimeError('unchanged')):
                with self.assertRaisesRegex(RuntimeError,'unchanged'): self.invoke(argv)
