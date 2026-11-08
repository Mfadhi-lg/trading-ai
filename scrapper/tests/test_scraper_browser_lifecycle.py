import os
import subprocess
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from scrapper.commands import browser_rating_host as host


class HostControlTests(unittest.TestCase):
    def test_stale_pid_cannot_target_a_different_process(self):
        with tempfile.TemporaryDirectory() as directory:
            pid = Path(directory) / 'pid'
            pid.write_text('123\n')
            with patch.object(host, 'PID_FILE', pid), patch.object(host.subprocess, 'run', return_value=MagicMock(returncode=0, stdout='/usr/bin/unrelated')):
                self.assertIsNone(host.running_pid())

    def test_start_is_idempotent(self):
        with patch.object(host, 'running_pid', return_value=123), patch.object(host.subprocess, 'Popen') as launch:
            host.start()
            launch.assert_not_called()


class WrapperLifecycleTests(unittest.TestCase):
    """Exercise the current bounded lifecycle against isolated state and mocks."""
    def setUp(self):
        import importlib
        from types import SimpleNamespace
        from contextlib import ExitStack

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.activation = self.root / 'activation'
        self.state.mkdir()
        self.activation.mkdir()
        for name in ('.scheduler-admission.lock', '.nse-owner.lock'):
            (self.activation / name).touch()
        # A synthetic session marker satisfies only the file-presence check;
        # all Docker/source/LaunchAgent operations remain mocked below.
        (self.root / '.screener_session').write_text('synthetic-offline-session')
        repo = Path(__file__).resolve().parents[2]
        with patch.object(sys, 'path', [str(repo / 'ops/host'), str(repo / 'ops/activation'), *sys.path]):
            self.lifecycle = importlib.import_module('ops.host.acquisition_lifecycle')
            documents = importlib.import_module('phase4_documents')
            nse = importlib.import_module('nse_results')
        self.stack = self.enterContext(ExitStack())
        lifecycle = self.lifecycle
        replacements = dict(ROOT=self.root, STATE=self.state,
            ACTIVATION=self.activation, PAUSE=self.state/'lifecycle-paused.json',
            PREVIOUS=self.state/'previous.json', config=MagicMock(return_value={'manifest':'fixture'}),
            loaded=MagicMock(return_value=True),
            quiescent=MagicMock(return_value={'database':{'leases':{'status':'PASS'}}}),
            external_handles=MagicMock(return_value=[]),
            schedule_status=MagicMock(return_value={'document_window_expired':True}),
            missed_slots=MagicMock(return_value=[{'missed_slots':3,'replayed':False}]),
            candles=SimpleNamespace(pause=MagicMock(return_value={'status':'PAUSED'}),
                wake=MagicMock(return_value={'status':'RUNNING'}),
                status=MagicMock(return_value={'status':'IDLE'})),
            run=MagicMock(return_value=SimpleNamespace(stdout='{}',returncode=0)))
        for key,value in replacements.items():
            self.stack.enter_context(patch.object(lifecycle,key,value))
        self.stack.enter_context(patch.object(documents,'storage_preflight',return_value={'free_bytes':400*1024**3}))
        self.stack.enter_context(patch.object(nse,'check_configuration',return_value={'oci_index_digest':'sha256:'+'a'*64}))
        original_stat = os.stat
        def stat(path,*args,**kwargs):
            if str(path) == '/dev/console':
                return SimpleNamespace(st_uid=os.getuid())
            return original_stat(path,*args,**kwargs)
        self.stack.enter_context(patch.object(os,'stat',side_effect=stat))
        original_is_file = Path.is_file
        self.stack.enter_context(patch.object(Path,'is_file',lambda path: True if str(path)=='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' else original_is_file(path)))

    def test_shutdown_then_wake_preserves_schedule_without_legacy_restore(self):
        lifecycle=self.lifecycle
        legacy=self.state/'.scraper_shutdown_state'
        legacy.write_text('@browser-rating-icra\ntrading-ai-daemon-1\n')
        result=lifecycle.shutdown(0)
        self.assertTrue(result['safe_to_shut_down_mac'])
        self.assertFalse(result['safe_to_disconnect_drive'])
        previous=lifecycle.PREVIOUS.read_bytes()
        self.assertEqual(lifecycle.shutdown(0)['status'],'PASS')
        self.assertEqual(lifecycle.PREVIOUS.read_bytes(),previous)
        result=lifecycle.wake()
        self.assertFalse(result['replayed'])
        self.assertTrue(result['legacy_shutdown_state_ignored'])
        self.assertEqual(result['missed_schedule_opportunities'][0]['missed_slots'],3)
        self.assertEqual(legacy.read_text(),'@browser-rating-icra\ntrading-ai-daemon-1\n')
        self.assertFalse(lifecycle.PAUSE.exists())
        for call in lifecycle.run.call_args_list:
            self.assertNotIn(call.args[0][:2],(['docker','start'],['docker','stop'],['docker','compose']))

    def test_shutdown_persists_pause_before_draining_and_syncs_after(self):
        lifecycle=self.lifecycle
        events=[]
        def drain(**kwargs):
            self.assertTrue(lifecycle.PAUSE.is_file())
            self.assertTrue(lifecycle.PREVIOUS.is_file())
            events.append('drained')
            return {'status':'PAUSED'}
        lifecycle.candles.pause.side_effect=drain
        lifecycle.quiescent.side_effect=lambda: events.append('quiescent') or {}
        lifecycle.run.side_effect=lambda args,**kwargs:events.append(args[0])
        lifecycle.shutdown(0)
        self.assertEqual(events,['drained','quiescent','/bin/sync'])

    def test_failed_drain_keeps_pause_without_claiming_safe(self):
        lifecycle=self.lifecycle
        lifecycle.candles.pause.side_effect=RuntimeError('owned writer still active')
        with self.assertRaisesRegex(RuntimeError,'owned writer still active'):
            lifecycle.shutdown(0)
        self.assertTrue(lifecycle.PAUSE.exists())
        self.assertTrue(lifecycle.PREVIOUS.exists())
        self.assertFalse((self.state/'shutdown-verification.json').exists())
        lifecycle.run.assert_not_called()

    def test_changed_configuration_refuses_wake_before_reopening(self):
        lifecycle=self.lifecycle
        lifecycle.shutdown(0)
        lifecycle.config.return_value={'manifest':'changed'}
        with self.assertRaisesRegex(RuntimeError,'configuration/authorization changed'):
            lifecycle.wake()
        self.assertTrue(lifecycle.PAUSE.exists())
        lifecycle.candles.wake.assert_not_called()

    def test_missing_session_refuses_wake_before_runtime_commands(self):
        lifecycle=self.lifecycle
        lifecycle.shutdown(0)
        lifecycle.run.reset_mock()
        (self.root/'.screener_session').unlink()
        with self.assertRaisesRegex(RuntimeError,'session file is missing/empty'):
            lifecycle.wake()
        self.assertTrue(lifecycle.PAUSE.exists())
        lifecycle.run.assert_not_called()

    def test_failed_candle_wake_recloses_admissions(self):
        lifecycle=self.lifecycle
        lifecycle.shutdown(0)
        original=lifecycle.PAUSE.read_bytes()
        lifecycle.candles.wake.side_effect=RuntimeError('storage identity changed')
        with self.assertRaisesRegex(RuntimeError,'storage identity changed'):
            lifecycle.wake()
        self.assertEqual(lifecycle.PAUSE.read_bytes(),original)
        self.assertFalse((self.state/'wake-verification.json').exists())
        self.assertEqual(lifecycle.candles.pause.call_count,2)

    def test_settings_help_and_import_are_inert_without_identity(self):
        repo=Path(__file__).resolve().parents[2]
        env={key:value for key,value in os.environ.items()
             if not key.startswith(('TRADING_AI_','SCRAPER_'))}
        env['SCRAPER_SHUTDOWN_STATE_PATH']=str(self.root/'absent'/'state')
        for args in (['-B','ops/host/lifecycle_config.py','--help'],
                     ['-B','-c','import ops.host.lifecycle_config']):
            result=subprocess.run([sys.executable,*args],cwd=repo,env=env,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.root/'absent').exists())


if __name__ == '__main__':
    unittest.main()
