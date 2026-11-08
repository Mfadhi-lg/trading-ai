"""Offline argv tests; do not import application modules or invoke Docker."""
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('launcher', HERE / 'launcher.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        # A launch would fail this test rather than touch Docker/production.
        self.patchers = [patch(name, side_effect=AssertionError('External execution forbidden'))
                         for name in ('subprocess.run', 'subprocess.Popen', 'os.system')]
        for p in self.patchers:p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patchers)])
        self.argv = launcher.build_docker_argv()
        self.code = launcher.CODE_PATH.read_text()

    def test_pinned_image_exact_positional_boundary(self):
        self.assertEqual(self.argv[37:41], ['--entrypoint', 'python', launcher.IMAGE, '-c'])
        self.assertEqual(self.argv.index(launcher.IMAGE), 39)
        self.assertEqual(len(self.argv), 42)

    def test_no_stray_line_arguments(self):
        self.assertFalse(any('\n' in a or '\r' in a or not a.strip() for a in self.argv[:40]))

    def test_code_intact_one_argument(self):
        self.assertEqual(self.argv[41], self.code)
        self.assertIn('\n', self.argv[41])
        self.assertEqual(hashlib.sha256(self.argv[41].encode()).hexdigest(), launcher.CODE_SHA256)

    def test_only_bad_continuations_removed_from_failed_vector(self):
        failed = json.loads((launcher.ROOT / 'docs/acquisition-readiness/baselines/AR-01.3C/execution/reviewed-command.json').read_text())
        self.assertEqual([a for a in failed if a != '\n'], self.argv)
        self.assertEqual(sum(a == '\n' for a in failed), 11)

    def test_network_and_isolation_preserved(self):
        for flag in ('--rm', '--no-healthcheck', '--read-only'):
            self.assertIn(flag, self.argv[:39])
        for flag, value in (('--pull','never'),('--network','none'),('--cap-drop','ALL'),
                            ('--security-opt','no-new-privileges'),('--cpus','0.5'),('--memory','1536m')):
            self.assertEqual(self.argv[self.argv.index(flag)+1], value)

    def test_reject_injected_newline(self):
        bad = self.argv.copy();bad[18] += '\n'
        with self.assertRaises(ValueError):launcher.validate_argv(bad, self.code)

    def test_reject_wrong_image(self):
        bad = self.argv.copy();bad[39] = 'trading-ai:latest'
        with self.assertRaises(ValueError):launcher.validate_argv(bad, self.code)

    def test_reject_split_code(self):
        bad = self.argv[:41] + self.code.splitlines()
        with self.assertRaises(ValueError):launcher.validate_argv(bad, self.code)

    def test_reject_code_change(self):
        changed = self.code + '\n# altered\n';bad = self.argv.copy();bad[41] = changed
        with self.assertRaises(ValueError):launcher.validate_argv(bad, changed)

    def test_json_transport_preserves_arguments(self):
        transported = json.loads(json.dumps(self.argv))
        self.assertEqual(transported, self.argv)
        launcher.validate_argv(transported, self.code)


if __name__ == '__main__':
    unittest.main(verbosity=2)
