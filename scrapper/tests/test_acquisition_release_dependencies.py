"""Release imports stay on one reviewed code snapshot; no runtime activation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('release_dependencies_under_test', ROOT/'ops/activation/acquisition_release.py')
releases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(releases)


class ReleaseDependencyTests(unittest.TestCase):
    def fixture(self, root):
        for directory in ('scrapper/db','ops/activation','ops/host','experiments/historical_candle_archive_v1'):
            (root/directory).mkdir(parents=True)
        for name in ('scrapper/__init__.py','scrapper/db/__init__.py','experiments/__init__.py','experiments/historical_candle_archive_v1/__init__.py'):
            (root/name).write_text('')
        (root/'experiments/historical_candle_archive_v1/storage.py').write_text("VALUE = 'old-compatible-storage'\n")
        for name in ('.scheduler-admission.lock','.nse-owner.lock','.monitor-readers.lock'):
            (root/'ops/activation'/name).touch()

    def storage_import(self, release):
        code = 'import sys; sys.path.insert(0,sys.argv[1]); from experiments.historical_candle_archive_v1 import storage; print(storage.VALUE)'
        return subprocess.run([sys.executable,'-I','-B','-c',code,str(release)],capture_output=True,text=True,timeout=20)

    def test_workspace_experiment_edit_cannot_break_pinned_old_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace=Path(temporary).resolve()/'workspace';self.fixture(workspace)
            release=releases.prepare_release(workspace,workspace/'artifacts/acquisition-releases/pinned')
            before=self.storage_import(release)
            self.assertEqual(before.returncode,0,before.stderr)
            # Exact dependency introduced in #114, absent in the older package.
            (workspace/'experiments/historical_candle_archive_v1/storage.py').write_text(
                'from scrapper.db.candle_inventory import backend, open_inventory\nVALUE = backend()\n')
            after=self.storage_import(release)
            self.assertEqual(after.returncode,0,after.stderr)
            self.assertEqual(after.stdout,before.stdout)
            self.assertFalse((release/'experiments').is_symlink())
            self.assertTrue((release/'artifacts').is_symlink())

    def test_reviewed_source_supplies_both_sides_of_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace=Path(temporary).resolve()/'workspace';source=Path(temporary).resolve()/'reviewed'
            self.fixture(workspace);self.fixture(source)
            (source/'experiments/historical_candle_archive_v1/storage.py').write_text(
                'from scrapper.db.candle_inventory import backend\nVALUE = backend()\n')
            (source/'scrapper/db/candle_inventory.py').write_text("def backend(): return 'reviewed-native-repository'\n")
            release=releases.prepare_release(workspace,workspace/'artifacts/acquisition-releases/reviewed',source=source)
            result=self.storage_import(release)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'reviewed-native-repository')
            self.assertEqual(json.loads((release/'.acquisition-release.json').read_text())['source_root'],str(source))
            self.assertTrue((release/'ops/activation/.nse-owner.lock').samefile(workspace/'ops/activation/.nse-owner.lock'))

    def test_linked_experiment_source_is_refused_before_preparing_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace=Path(temporary).resolve()/'workspace';source=Path(temporary).resolve()/'reviewed'
            self.fixture(workspace)
            for directory in ('scrapper','ops/activation','ops/host'):
                (source/directory).mkdir(parents=True)
            (source/'experiments').symlink_to(workspace/'experiments',target_is_directory=True)
            release=workspace/'artifacts/acquisition-releases/invalid'
            with self.assertRaisesRegex(ValueError,'complete source snapshot'):
                releases.prepare_release(workspace,release,source=source)
            self.assertFalse(release.exists())

    def test_old_mixed_release_cannot_be_activated(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace=Path(temporary).resolve()/'workspace';self.fixture(workspace)
            release=workspace/'artifacts/acquisition-releases/legacy';release.mkdir(parents=True)
            (release/'.acquisition-release.json').write_text(json.dumps({'workspace_root':str(workspace)}))
            for directory in ('scrapper','ops/activation','ops/host'):
                (release/directory).mkdir(parents=True)
            (release/'experiments').symlink_to(workspace/'experiments',target_is_directory=True)
            # Refuse before scheduler imports, launchctl, pointer changes or locks.
            with self.assertRaisesRegex(RuntimeError,'Mixed-version release refused'):
                releases.activate_release(release)
            self.assertFalse((release.parent/'active.json').exists())
