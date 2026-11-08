"""Subprocess contracts for path configuration, compatibility and H03 refusal."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class RuntimePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = Path(__file__).resolve().parents[2]
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(('SCRAPER_', 'TRADING_AI_', 'DOCUMENT_'))}
        self.env.update(PYTHONDONTWRITEBYTECODE='1', TRADING_AI_ENV='test')

    def tearDown(self):
        self.temp.cleanup()

    def run_config(self, code, **values):
        return subprocess.run([sys.executable, '-B', '-c',
                               'from scrapper import config as c\n' + code],
                              cwd=self.repo, env={**self.env, **values},
                              text=True, capture_output=True, timeout=30)

    def test_default_paths_and_existing_names_are_unchanged(self):
        result = self.run_config(
            'import json\nfrom pathlib import Path\n'
            'print(json.dumps({k: str(v) for k,v in vars(c).items() if isinstance(v, Path)}))')
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = json.loads(result.stdout)
        store = self.repo / 'scrapper' / 'store'
        expected = {'ROOT': str(self.repo / 'scrapper'), 'STORE': str(store),
                    'FILES': str(store/'files'), 'SNAPSHOTS': str(store/'snapshots'),
                    'EXPORTS': str(store/'exports'), 'DB_PATH': str(store/'scraper.db'),
                    'DOCUMENT_STORE': str(store/'snapshots'),
                    'DOCUMENT_CONTENT_SNAPSHOTS': str(store/'snapshots'/'artifact_content')}
        for key, value in expected.items():
            self.assertEqual(paths[key], value, key)
        self.assertEqual(paths['RUNTIME_ROOT'], paths['STORE'])
        self.assertEqual(paths['DOCUMENTS'], paths['DOCUMENT_STORE'])
        self.assertEqual(paths['DATABASE_DIR'], paths['STORE'])

    def test_split_roots_route_existing_derivatives_without_eager_state_creation(self):
        env = {f'SCRAPER_{key}_PATH': str(self.root/key.lower()) for key in
               ('STORE', 'FILES', 'SNAPSHOTS', 'EXPORTS', 'CAMPAIGNS', 'LOGS',
                'BROWSER_STATE', 'BACKUPS')}
        env['SCRAPER_DB_PATH'] = str(self.root/'database'/'scraper.db')
        env['SCRAPER_DOCUMENT_STORE_PATH'] = str(self.root/'documents')
        result = self.run_config('c.ensure_dirs()', **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root/'files').is_dir())
        self.assertTrue((self.root/'snapshots'/'artifact_discovery'/'recovery').is_dir())
        self.assertTrue((self.root/'snapshots'/'amc_workbook').is_dir())
        self.assertTrue((self.root/'exports'/'credit_rating').is_dir())
        self.assertTrue((self.root/'store'/'mf_import'/'inbox').is_dir())
        for name in ('database', 'documents', 'campaigns', 'logs', 'browser_state', 'backups'):
            self.assertFalse((self.root/name).exists(), name)
        self.assertFalse((self.root/'store'/'exports').exists())

    def test_import_is_inert_even_with_production_locations_and_no_identity(self):
        result = self.run_config('print(c.EXPORTS)', TRADING_AI_ENV='',
                                 SCRAPER_STORE_PATH=str(self.root/'store'),
                                 SCRAPER_EXPORTS_PATH='/data/exports')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root/'store').exists())

    def test_every_new_root_refuses_production_before_any_directory_creation(self):
        for name in ('FILES', 'SNAPSHOTS', 'EXPORTS', 'CAMPAIGNS', 'LOGS',
                     'BROWSER_STATE', 'BACKUPS'):
            with self.subTest(name=name):
                result = self.run_config('c.ensure_dirs()',
                    SCRAPER_STORE_PATH=str(self.root/'store'),
                    **{f'SCRAPER_{name}_PATH': '/data/escape'})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('targets production path', result.stderr)
                self.assertFalse((self.root/'store').exists())

    def test_symlink_to_production_is_refused(self):
        alias = self.root/'alias'
        alias.symlink_to('/documents', target_is_directory=True)
        result = self.run_config('c.ensure_dirs()',
                                SCRAPER_STORE_PATH=str(self.root/'store'),
                                SCRAPER_EXPORTS_PATH=str(alias/'exports'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('targets production path', result.stderr)
        self.assertFalse((self.root/'store').exists())

    def test_production_split_root_requires_exact_additional_authorization(self):
        store, documents, exports = (self.root/name for name in ('store','documents','exports'))
        for role, path in (('store',store), ('documents',documents)):
            path.mkdir()
            (path/'.trading_ai_runtime_identity.json').write_text(json.dumps({
                'environment':'production', 'role':role, 'path':str(path), 'release_id':'fixture'}))
        identity = self.root/'identity.json'
        manifest = {'environment':'production', 'authorized':True, 'release_id':'fixture',
                    'git_commit':'fixture', 'image_id':'fixture', 'targets':{
                        'store':str(store), 'db':str(store/'scraper.db'), 'documents':str(documents)}}
        identity.write_text(json.dumps(manifest))
        values = {'TRADING_AI_ENV':'production', 'TRADING_AI_RUNTIME_IDENTITY_FILE':str(identity),
                  'SCRAPER_STORE_PATH':str(store), 'SCRAPER_DOCUMENT_STORE_PATH':str(documents),
                  'SCRAPER_EXPORTS_PATH':str(exports)}
        result = self.run_config('c.ensure_dirs()', **values)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('extra writable target is not authorized', result.stderr)
        self.assertFalse(exports.exists())
        self.assertFalse((store/'files').exists())
        manifest['additional_writable_roots'] = [str(exports)]
        identity.write_text(json.dumps(manifest))
        result = self.run_config('c.ensure_dirs()', **values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((exports/'credit_rating').is_dir())


if __name__ == '__main__':
    unittest.main()
