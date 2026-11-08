import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scrapper.commands import browser_rating_host as host
from scrapper.commands import browser_rating_host_config as settings


class HostConfigurationTests(unittest.TestCase):
    def reload_settings(self, env):
        with patch.dict(os.environ, env, clear=True):
            import scrapper.config as config
            importlib.reload(config)
            return importlib.reload(settings)

    def tearDown(self):
        import scrapper.config as config
        importlib.reload(config)
        importlib.reload(settings)

    def test_existing_defaults_and_bridge_are_exactly_preserved(self):
        s = self.reload_settings({})
        self.assertEqual(s.PID_FILE, s.LEGACY_FOLDER / 'icra-recovery.pid')
        self.assertEqual(s.LOCK_FILE, s.LEGACY_FOLDER / 'icra-lifecycle.lock')
        self.assertEqual(s.LOG_FILE, s.LEGACY_FOLDER / 'icra-recovery.log')
        self.assertEqual(s.PYTHON, s.LEGACY_FOLDER / '.venv/bin/python')
        self.assertEqual(s.bridge_command()[3], 'trading-ai-document-download-worker-1')
        self.assertEqual(s.bridge_command()[8], 'browser-ratings-20261001')

    def test_split_roots_and_exact_overrides_share_the_same_bridge(self):
        s = self.reload_settings({'SCRAPER_BROWSER_STATE_PATH':'/tmp/state',
            'SCRAPER_LOGS_PATH':'/tmp/logs', 'SCRAPER_BROWSER_PYTHON':'/tmp/env/bin/python',
            'SCRAPER_BROWSER_RUN_ID':'selected-run', 'SCRAPER_BROWSER_BRIDGE_CONTAINER':'selected-container'})
        self.assertEqual(s.PID_FILE, Path('/tmp/state/icra-recovery.pid'))
        self.assertEqual(s.LOCK_FILE, Path('/tmp/state/icra-lifecycle.lock'))
        self.assertEqual(s.LOG_FILE, Path('/tmp/logs/icra-recovery.log'))
        self.assertEqual(s.bridge_command()[3], 'selected-container')
        self.assertEqual(s.bridge_command()[8], 'selected-run')
        s = self.reload_settings({'SCRAPER_BROWSER_PID_PATH':'/tmp/pid',
            'SCRAPER_BROWSER_LOCK_PATH':'/tmp/lock', 'SCRAPER_BROWSER_LOG_PATH':'/tmp/log'})
        self.assertEqual((s.PID_FILE,s.LOCK_FILE,s.LOG_FILE), tuple(map(Path, ['/tmp/pid','/tmp/lock','/tmp/log'])))

    def test_every_host_write_override_refuses_production_before_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for key in ('SCRAPER_BROWSER_PID_PATH','SCRAPER_BROWSER_LOCK_PATH',
                        'SCRAPER_BROWSER_LOG_PATH','PLAYWRIGHT_BROWSERS_PATH'):
                env = {'TRADING_AI_ENV':'test','SCRAPER_STORE_PATH':str(root/'store'),
                       'SCRAPER_DB_PATH':str(root/'db'), 'SCRAPER_DOCUMENT_STORE_PATH':str(root/'docs'),
                       key:'/data/forbidden'}
                s = self.reload_settings(env)
                with patch.dict(os.environ,env,clear=True), self.assertRaises(RuntimeError):
                    s.assert_host_targets()
                self.assertEqual(list(root.iterdir()), [])

    def test_start_uses_selected_environment_log_and_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = root/'env/bin/python'; python.parent.mkdir(parents=True); python.touch()
            pid, log = root/'state/pid', root/'logs/log'
            process = MagicMock(pid=456); process.poll.return_value=None
            with patch.object(host,'assert_host_targets'), patch.object(host,'running_pid',return_value=None), \
                 patch.object(host,'PYTHON',python), patch.object(host,'PID_FILE',pid), patch.object(host,'LOG_FILE',log), \
                 patch.object(host.subprocess,'Popen',return_value=process) as launch, patch.object(host.time,'sleep'):
                host.start()
            self.assertEqual(pid.read_text(),'456\n')
            self.assertTrue(log.is_file())
            self.assertEqual(launch.call_args.args[0], [str(python),str(host.WORKER),'--background'])
            self.assertTrue(launch.call_args.kwargs['start_new_session'])

    def test_stop_waits_for_browser_then_releases_same_configured_bridge(self):
        events=[]
        with tempfile.TemporaryDirectory() as directory:
            pid = Path(directory)/'pid'; pid.write_text('456\n')
            def run(*args,**kwargs):
                events.append('release'); self.assertEqual(args[0],['selected-bridge'])
                self.assertEqual(kwargs['input'],'{"action": "release"}\n')
                return MagicMock(returncode=0,stdout='{}')
            with patch.object(host,'assert_host_targets'), patch.object(host,'PID_FILE',pid), \
                 patch.object(host,'running_pid',side_effect=[456,456,None]), \
                 patch.object(host.os,'kill',side_effect=lambda *a:events.append('signal')), \
                 patch.object(host.time,'sleep',side_effect=lambda *a:events.append('wait')), \
                 patch.object(host,'bridge_command',return_value=['selected-bridge']), patch.object(host.subprocess,'run',side_effect=run):
                host.stop()
            self.assertEqual(events,['signal','wait','release'])
            self.assertFalse(pid.exists())

    def test_failed_claim_release_retains_pid_and_prevents_success(self):
        with tempfile.TemporaryDirectory() as directory:
            pid=Path(directory)/'pid'; pid.write_text('456\n')
            with patch.object(host,'assert_host_targets'), patch.object(host,'PID_FILE',pid), \
                 patch.object(host,'running_pid',side_effect=[456,None]), patch.object(host.os,'kill'), \
                 patch.object(host.subprocess,'run',return_value=MagicMock(returncode=1,stderr='offline')), \
                 self.assertRaisesRegex(RuntimeError,'Could not release'):
                host.stop()
            self.assertTrue(pid.exists())

    def test_help_status_and_import_create_no_host_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','TRADING_AI_ENV':'',
                 'SCRAPER_BROWSER_STATE_PATH':str(root/'state'),'SCRAPER_LOGS_PATH':str(root/'logs')}
            env.pop('PYTHONPATH',None)
            for args in (['-m','scrapper.commands.browser_rating_host','--help'],
                         [str(host.WORKER.with_name('browser_rating_host.py')),'--help'],
                         ['-m','scrapper.commands.browser_rating_host','status'],
                         ['-c','import scrapper.commands.browser_rating_host']):
                result=subprocess.run([sys.executable,*args],env=env,capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(list(root.iterdir()),[])

    def test_configured_environment_check_is_before_worker_launch(self):
        with patch.object(host,'assert_host_targets'), patch.object(host,'running_pid',return_value=None), \
             patch.object(host,'PYTHON',Path(sys.executable)), patch.dict(os.environ,{'SCRAPER_BROWSER_PYTHON':sys.executable}), \
             patch.object(host.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['check'])), \
             patch.object(host.subprocess,'Popen') as launch, self.assertRaises(subprocess.CalledProcessError):
            host.start()
        launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
