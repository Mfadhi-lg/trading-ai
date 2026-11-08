"""Compatibility of the public CLI package and its module entry point."""
import importlib
import runpy
import unittest
from unittest.mock import patch
from scrapper import cli


class CliPackageTests(unittest.TestCase):
    def test_public_import_identity_and_package_resolution(self):
        self.assertIs(importlib.import_module('scrapper.cli'), cli)
        self.assertTrue(cli.__file__.endswith('/cli/__init__.py'))
        self.assertTrue(hasattr(cli, '__path__'))
        from scrapper.cli import main, _parser
        self.assertIs(main, cli.main)
        self.assertIs(_parser, cli._parser)

    def test_module_entrypoint_calls_public_main_and_preserves_exit_code(self):
        with patch.object(cli, 'main', return_value=17) as main:
            with self.assertRaises(SystemExit) as error:
                runpy.run_module('scrapper.cli', run_name='__main__')
            self.assertEqual(error.exception.code, 17)
            main.assert_called_once_with()

    def test_lazy_constant_owners_keep_scrapper_package_resolution(self):
        with patch.object(cli, 'load_attribute', return_value=('pnl',)) as load:
            self.assertEqual(cli.SECTION_CODES, ('pnl',))
            load.assert_called_once_with('.view.render', 'SECTION_CODES')
        with self.assertRaises(AttributeError):
            getattr(cli, 'unknown_cli_attribute')
