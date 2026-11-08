"""Legacy download APIs, transport hooks and inert layer ownership."""
import inspect
import pickle
import unittest
from unittest.mock import patch
from scrapper.documents import downloader as facade
from scrapper.documents.download import contracts, media, transport


class DownloadOwnershipTests(unittest.TestCase):
    def test_facade_preserves_arguments_and_patchable_hooks(self):
        signature = inspect.signature(facade.download_url)
        values = {name: object() for name in signature.parameters}
        result = object()
        with patch.object(transport, 'download_url', return_value=result) as call:
            self.assertIs(facade.download_url(values.pop('requested_url'), **values), result)
        for name, value in values.items():
            self.assertIs(call.call_args.kwargs[name], value)
        for name in set(call.call_args.kwargs) - set(values):
            self.assertIs(call.call_args.kwargs[name], getattr(facade, name))

    def test_shared_contract_identity_and_legacy_serialization(self):
        for name in ('DownloadPolicy', 'DownloadResult', 'RedirectHop', 'UnsafeRedirectError'):
            self.assertIs(getattr(facade, name), getattr(contracts, name))
        policy = facade.DownloadPolicy()
        self.assertEqual(pickle.loads(pickle.dumps(policy)), policy)
        self.assertIs(facade.detect_media_type, media.detect_media_type)

    def test_layers_do_not_import_persistence_or_orchestration(self):
        import ast
        from pathlib import Path
        root = Path(__file__).parents[1] / 'documents/download'
        for path in root.glob('*.py'):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(any(part in (node.module or '').split('.') for part in ('db', 'commands', 'pipeline', 'operations', 'view', 'workflows', 'downloader')))
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, ('execute', 'executemany', 'executescript'))

    def test_transitive_import_is_inert(self):
        import subprocess,sys
        script="import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode = True\ndef deny(*args, **kwargs): raise AssertionError('forbidden import side effect')\ndef audit(event, args):\n    if event == 'open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):\n        raise AssertionError('write during import')\n    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):\n        raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny\nimport scrapper.documents.downloader; import scrapper.documents.download.transport; import scrapper.documents.download.media; import scrapper.documents.download.contracts\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
