"""Registration compatibility and dependency isolation for the public CLI facade."""
import argparse
import contextlib
import importlib
import io
import os
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scrapper import cli
from scrapper.cli_groups import registration
from scrapper.cli_groups.dispatch import lazy_handler


class CliFacadeTests(unittest.TestCase):
    def test_registration_choices_match_existing_policy_owners(self):
        for name, owner in cli._CONSTANT_OWNERS.items():
            if hasattr(registration, name):
                actual = getattr(registration, name)
                expected = getattr(importlib.import_module(owner[0], 'scrapper'), owner[1])
                with self.subTest(name=name):
                    self.assertEqual(actual, tuple(expected) if isinstance(expected, dict) else expected)

    def test_alias_defaults_and_invalid_command_exit(self):
        for name in ('download-monitor', 'monitor-downloads'):
            args = cli._parser().parse_args([name])
            self.assertEqual(args.command, name)
            self.assertEqual(args.db, cli.DB_PATH)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            cli.main(['unknown-command'])
        self.assertEqual(error.exception.code, 2)

    def test_lazy_delegate_imports_only_requested_module_and_preserves_result_error(self):
        target = Mock(return_value=17)
        with patch('scrapper.cli_groups.dispatch.import_module', return_value=Mock(handler=target)) as loader:
            handler = lazy_handler('.pipeline.selected', 'handler')
            loader.assert_not_called()
            self.assertEqual(handler('arg', option=2), 17)
            loader.assert_called_once_with('.pipeline.selected', package='scrapper')
            target.assert_called_once_with('arg', option=2)
            target.side_effect = ValueError('original error')
            with self.assertRaisesRegex(ValueError, 'original error'):
                handler()

    def test_default_and_explicit_shell_preserve_return_value_and_patch_path(self):
        with patch.object(cli, 'run_interactive_shell', return_value=13) as shell:
            self.assertEqual(cli.main([]), 13)
            self.assertEqual(cli.main(['shell', '--db', '/tmp/custom.db']), 13)
            self.assertEqual(shell.call_args_list[0].args, (cli.DB_PATH,))
            self.assertEqual(shell.call_args_list[1].args, (Path('/tmp/custom.db'),))

    def test_read_only_alias_dispatch_keeps_connection_and_output_contract(self):
        for name in ('download-monitor', 'monitor-downloads'):
            conn = Mock()
            with patch.object(cli, 'connect', return_value=conn) as connect, patch.object(cli, 'init_db') as init, patch.object(cli, 'run_download_monitor') as monitor:
                self.assertEqual(cli.main([name, '--db', '/tmp/fixture.db']), 0)
                connect.assert_called_once_with(Path('/tmp/fixture.db'), read_only=True)
                init.assert_called_once_with(conn)
                monitor.assert_called_once()
                conn.close.assert_called_once()

    def test_module_entrypoint_and_wrapper_passthrough(self):
        expected = cli._parser().format_help()
        result = subprocess.run([sys.executable, '-B', '-m', 'scrapper.cli', '--help'], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, expected, ''))
        with tempfile.TemporaryDirectory() as directory:
            docker = Path(directory) / 'docker'
            docker.write_text('#!' + sys.executable + '\n' + '''import sys
import subprocess
assert sys.argv[1:7] == ['compose', '--profile', 'manual', 'run', '--rm', 'cli']
result = subprocess.run([sys.executable, '-B', '-m', 'scrapper.cli', *sys.argv[7:]])
sys.exit(result.returncode)
''')
            docker.chmod(0o700)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH'])
            wrapper = Path(__file__).resolve().parents[2] / 'scraper'
            # Root help belongs to the host operator CLI; manual subcommands
            # still pass through the existing Python application facade.
            for args in (['monitor-downloads', '--help'], ['unknown-command']):
                wrapped = subprocess.run([str(wrapper), *args], capture_output=True, text=True, env=env)
                direct = subprocess.run([sys.executable, '-B', '-m', 'scrapper.cli', *args], capture_output=True, text=True)
                self.assertEqual((wrapped.returncode, wrapped.stdout, wrapped.stderr), (direct.returncode, direct.stdout, direct.stderr))

    def test_init_db_runs_without_acquisition_document_or_view_dependencies(self):
        code = r'''
import importlib.abc, sys, tempfile, sqlite3, contextlib, io
from pathlib import Path
class RefuseOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('scrapper.pipeline', 'scrapper.documents', 'scrapper.exports', 'scrapper.view', 'scrapper.sources', 'scrapper.workflows')):
            raise AssertionError('unrelated subsystem: '+fullname)
sys.meta_path.insert(0, RefuseOptional())
from scrapper import cli
with tempfile.TemporaryDirectory() as directory:
    db=Path(directory)/'fixture.db'
    with contextlib.redirect_stdout(io.StringIO()):
        assert cli.main(['init-db','--db',str(db)]) == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 100
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_help_and_imports_are_inert_without_optional_subsystems(self):
        code = r'''
import sys, os, sqlite3, socket, subprocess, threading, builtins, io
from pathlib import Path
from unittest.mock import patch
import importlib.abc
class RefuseSubsystems(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('scrapper.pipeline', 'scrapper.db', 'scrapper.documents', 'scrapper.exports', 'scrapper.view', 'scrapper.sources', 'scrapper.workflows')):
            raise AssertionError('eager subsystem: '+fullname)
sys.meta_path.insert(0, RefuseSubsystems())
def fail(*a, **kw): raise AssertionError('forbidden side effect')
original_open = builtins.open
def guarded_open(file, mode='r', *a, **kw):
    if any(flag in mode for flag in 'wax+'): fail()
    return original_open(file, mode, *a, **kw)
with patch('sqlite3.connect', fail), patch('socket.socket', fail), patch('subprocess.Popen', fail), patch('threading.Thread.start', fail), patch('os.mkdir', fail), patch('pathlib.Path.write_text', fail), patch('pathlib.Path.write_bytes', fail), patch('builtins.open', guarded_open):
    from scrapper import cli
    import argparse, contextlib
    count=0
    def walk(parser, prefix):
        global count
        with contextlib.redirect_stdout(io.StringIO()):
            try: cli.main(prefix+['--help'])
            except SystemExit as e: assert e.code == 0
            else: raise AssertionError('help did not exit')
        count+=1
        for action in parser._actions:
            if isinstance(action,argparse._SubParsersAction):
                for name,child in action.choices.items(): walk(child,prefix+[name])
    walk(cli._parser(), [])
    assert count > 50, count
    assert not any(n.startswith(('scrapper.pipeline','scrapper.db','scrapper.documents','scrapper.view')) for n in sys.modules)
print(count)
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreater(int(result.stdout.strip()), 50)


if __name__ == '__main__':
    unittest.main()
