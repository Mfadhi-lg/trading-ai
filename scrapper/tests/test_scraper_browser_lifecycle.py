import os
import shutil
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
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        repo = Path(__file__).parents[2]
        shutil.copy(repo / 'scraper', self.root / 'scraper')
        shutil.copytree(repo / 'ops', self.root / 'ops')
        shutil.copytree(repo / 'scrapper', self.root / 'scrapper',
                        ignore=shutil.ignore_patterns('__pycache__', 'store'))
        (self.root / '.env').write_text(f'TRADING_AI_DOCUMENT_HOST_PATH={self.root}\n')
        (self.root / '.trading_ai_document_store').touch()
        self.events = self.root / 'events'
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        self.env = {**os.environ, 'PATH': str(bin_dir) + ':' + os.environ['PATH'], 'EVENTS': str(self.events), 'REAL_PYTHON': sys.executable,
                    'TRADING_AI_ENV': 'test', 'PYTHONDONTWRITEBYTECODE': '1'}
        programs = {
            'python3': '''#!/bin/bash
if [ "$1" = "-B" ]; then exec "$REAL_PYTHON" "$@"; fi
echo "host:$2" >> "$EVENTS"
case "$2" in
is-running) [ ! -f "$EVENTS.host-stopped" ]; exit $?;;
stop) if [ "${FAIL_HOST_STOP:-0}" = 1 ]; then exit 1; fi; touch "$EVENTS.host-stopped";;
start) rm -f "$EVENTS.host-stopped";;
esac
''',
            'docker': '''#!/bin/bash
echo "docker:$*" >> "$EVENTS"
case "$1" in
ps) case "$*" in *status=running*) [ -f "$EVENTS.stopped" ] || echo trading-ai-document-download-worker-1;; esac;;
inspect) case "$*" in *oneoff*) echo False;; *State.Running*) echo true;; esac;;
stop) touch "$EVENTS.stopped";;
start) rm -f "$EVENTS.stopped";;
esac
''',
        }
        for name, code in programs.items():
            path = bin_dir / name
            path.write_text(code)
            path.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, action, **extra):
        return subprocess.run(['bash', str(self.root / 'scraper'), action], env={**self.env, **extra}, capture_output=True, text=True)

    def test_shutdown_then_wake_orders_host_and_containers(self):
        self.assertEqual(self.invoke('shutdown').returncode, 0)
        state = (self.root / '.scraper_shutdown_state').read_text()
        self.assertIn('@browser-rating-icra', state)
        events = self.events.read_text()
        self.assertLess(events.index('host:stop'), events.index('docker:stop'))
        self.events.write_text('')
        self.assertEqual(self.invoke('wake').returncode, 0)
        events = self.events.read_text()
        self.assertLess(events.index('docker:start'), events.index('host:start'))

    def test_failed_host_stop_prevents_container_shutdown_and_keeps_resume_state(self):
        self.assertNotEqual(self.invoke('shutdown', FAIL_HOST_STOP='1').returncode, 0)
        self.assertNotIn('docker:stop', self.events.read_text())
        self.assertIn('trading-ai-document-download-worker-1', (self.root / '.scraper_shutdown_state').read_text())

    def test_configured_state_with_spaces_preserves_legacy_format_and_order(self):
        state = self.root / 'external state' / 'resume file'
        result = self.invoke('shutdown', SCRAPER_SHUTDOWN_STATE_PATH=str(state),
                             SCRAPER_BROWSER_RUN_ID='selected-run',
                             SCRAPER_BROWSER_BRIDGE_CONTAINER='selected-bridge')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state.read_text(),
                         '@browser-rating-icra\ntrading-ai-document-download-worker-1\n')
        self.assertFalse((self.root / '.scraper_shutdown_state').exists())
        self.events.write_text('')
        self.assertEqual(self.invoke('wake', SCRAPER_SHUTDOWN_STATE_PATH=str(state)).returncode, 0)
        events = self.events.read_text()
        self.assertLess(events.index('docker:start'), events.index('host:start'))

    def test_refused_state_and_symlink_have_no_lifecycle_side_effects(self):
        alias = self.root / 'production-alias'
        alias.symlink_to('/data', target_is_directory=True)
        for action in ('shutdown', 'wake'):
            for state in ('/data/refused-resume', str(alias / 'refused-resume')):
                result = self.invoke(action, SCRAPER_SHUTDOWN_STATE_PATH=state)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('refused', result.stderr)
                self.assertFalse(self.events.exists())

    def test_status_uses_selected_bridge_without_creating_state(self):
        state = self.root / 'absent' / 'resume'
        result = self.invoke('status', SCRAPER_SHUTDOWN_STATE_PATH=str(state),
                             SCRAPER_BROWSER_BRIDGE_CONTAINER='selected-bridge')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('docker:exec selected-bridge python -m scrapper.view.browser_rating_monitor --once',
                      self.events.read_text())
        self.assertFalse(state.parent.exists())

    def test_failed_stop_retains_configured_state_for_retry(self):
        state = self.root / 'external' / 'resume'
        result = self.invoke('shutdown', FAIL_HOST_STOP='1', SCRAPER_SHUTDOWN_STATE_PATH=str(state))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('@browser-rating-icra', state.read_text())
        self.assertNotIn('docker:stop', self.events.read_text())

    def test_missing_store_prevents_wake_even_with_configured_state(self):
        state = self.root / 'resume'
        state.write_text('@browser-rating-icra\ncontainer\n')
        (self.root / '.trading_ai_document_store').unlink()
        self.assertNotEqual(self.invoke('wake', SCRAPER_SHUTDOWN_STATE_PATH=str(state)).returncode, 0)
        self.assertFalse(self.events.exists())

    def test_invalid_line_separator_refuses_before_actions(self):
        self.assertNotEqual(self.invoke('shutdown', SCRAPER_SHUTDOWN_STATE_PATH='bad\npath').returncode, 0)
        self.assertNotEqual(self.invoke('shutdown', SCRAPER_SHUTDOWN_STATE_PATH='').returncode, 0)
        self.assertFalse(self.events.exists())

    def test_settings_help_and_import_are_inert_without_identity(self):
        env = {key: value for key, value in self.env.items()
               if not key.startswith(('TRADING_AI_', 'SCRAPER_'))}
        env['SCRAPER_SHUTDOWN_STATE_PATH'] = str(self.root / 'absent' / 'state')
        for args in (['-B', 'ops/host/lifecycle_config.py', '--help'],
                     ['-B', '-c', 'import ops.host.lifecycle_config']):
            result = subprocess.run([sys.executable, *args], cwd=self.root, env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'absent').exists())
        self.assertFalse(self.events.exists())


if __name__ == '__main__':
    unittest.main()
