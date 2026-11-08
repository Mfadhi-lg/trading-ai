"""CLI ownership contracts for operations and rendering."""
import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from scrapper import cli

class OperationsGroupTests(unittest.TestCase):
    def invoke(self, args):
        out=io.StringIO()
        with contextlib.redirect_stdout(out): code=cli.main(args)
        return code,out.getvalue()

    def test_init_is_explicit_and_closes_on_failure(self):
        conn=Mock()
        with patch.object(cli,'connect',return_value=conn) as connect, patch.object(cli,'init_db') as init:
            code,out=self.invoke(['init-db','--db','/tmp/fixture.db'])
            self.assertEqual(code,0); self.assertEqual(json.loads(out)['database'],'/tmp/fixture.db')
            connect.assert_called_once_with(Path('/tmp/fixture.db'));init.assert_called_once_with(conn,apply_migrations=True)
            init.side_effect=RuntimeError('refused')
            with self.assertRaisesRegex(RuntimeError,'refused'): self.invoke(['init-db','--db','/tmp/fixture.db'])
            self.assertEqual(conn.close.call_count,2)

    def test_monitor_routes_and_propagated_failure_close(self):
        for args,name in [(['monitor'],'run_live_monitor'),(['download-monitor'],'run_download_monitor'),(['monitor-downloads'],'run_download_monitor'),(['view','monitor'],'run_live_monitor'),(['view','downloads'],'run_download_monitor')]:
            conn=Mock()
            with patch.object(cli,'connect',return_value=conn) as connect,patch.object(cli,'init_db') as init,patch.object(cli,name) as monitor:
                self.assertEqual(self.invoke(args+['--refresh','2'])[0],0)
                connect.assert_called_once_with(cli.DB_PATH,read_only=True);init.assert_called_once_with(conn)
                monitor.assert_called_once_with(conn,refresh_interval=2.0);conn.close.assert_called_once()
                monitor.side_effect=RuntimeError('failed')
                if args[0]=='view': self.assertEqual(self.invoke(args)[0],2)
                else:
                    with self.assertRaisesRegex(RuntimeError,'failed'):self.invoke(args)
                self.assertEqual(conn.close.call_count,2)

    def test_render_modes_and_error_close(self):
        cases=[(['view','company','LAURUSLABS','--sections',' pnl, cash_flow ','--no-shareholding'],'render_company'),(['view','mf'],'render_mf'),(['view','coverage'],'render_coverage'),(['view','tables'],'render_tables'),(['view','browse','companies','--where','status = active','--limit','3'],'render_browse')]
        for args,name in cases:
            conn=Mock()
            with patch.object(cli,'connect',return_value=conn) as connect,patch.object(cli,'init_db'),patch.object(cli,'load_attribute',return_value=('pnl','cash_flow')),patch.object(cli,name,return_value='rendered') as render:
                self.assertEqual(self.invoke(args),(0,'rendered\n'));connect.assert_called_once_with(cli.DB_PATH,read_only=True);conn.close.assert_called_once()
                if name=='render_company':self.assertEqual(render.call_args.kwargs,dict(scope='standalone',periods=-1,sections=['pnl','cash_flow'],show_shareholding=False))
                render.side_effect=ValueError('invalid');code,out=self.invoke(args);self.assertEqual(code,2);self.assertEqual(json.loads(out)['error'],'invalid');self.assertEqual(conn.close.call_count,2)

    def test_unknown_section_and_unhandled_error(self):
        conn=Mock()
        with patch.object(cli,'connect',return_value=conn),patch.object(cli,'init_db'),patch.object(cli,'load_attribute',return_value=('pnl',)),patch.object(cli,'render_company') as render:
            code,out=self.invoke(['view','company','A','--sections','zzz']);self.assertEqual(code,2);self.assertIn('Unknown section(s): zzz',out);render.assert_not_called();conn.close.assert_called_once()
        with patch.object(cli,'connect',side_effect=KeyError('uncaught')):
            with self.assertRaises(KeyError):self.invoke(['view','tables'])

    def test_quality_output_guard_readonly_and_propagated_error(self):
        conn=Mock();output=Path('/tmp/report.json');events=[]
        with patch.object(cli,'assert_configured_writable_targets',side_effect=lambda *a,**k: events.append('guard')) as guard,patch.object(cli,'connect',side_effect=lambda *a,**k:events.append('connect') or conn) as connect,patch.object(cli,'init_db') as init,patch.object(cli,'write_quality_report',return_value=output) as write:
            code,out=self.invoke(['quality-report','--output',str(output)]);self.assertEqual(code,0);self.assertEqual(json.loads(out)['output'],str(output));self.assertEqual(events,['guard','connect']);guard.assert_called_once_with(output,db_path=cli.DB_PATH);connect.assert_called_once_with(cli.DB_PATH,read_only=True);init.assert_called_once_with(conn);write.assert_called_once_with(conn,output)
            write.side_effect=OSError('disk');
            with self.assertRaisesRegex(OSError,'disk'):self.invoke(['quality-report','--output',str(output)])
            self.assertEqual(conn.close.call_count,2)
