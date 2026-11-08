"""Persistence/derivative ownership and compatible patch-point contracts."""
import ast
import inspect
import pickle
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from scrapper.documents import downloader, html_pdf
from scrapper.documents.download import recording, derivatives
from scrapper.db import document_download_repo, html_document_rendering_repo
from scrapper.db.documents import download, rendering


class PersistenceOwnershipTests(unittest.TestCase):
    def test_facades_forward_arguments_and_legacy_hooks(self):
        for facade, owner, names in (
            (downloader, recording, ('download_recording_to_store', 'cleanup_recording_temporary_files', '_hash_file', '_recording_stream_destination')),
            (document_download_repo, download, ('persist_download_result', 'store_response_blob', '_store_response_blob', '_store_streamed_response_blob', '_validated_sources', '_is_official_retrieval')),
            (html_document_rendering_repo, rendering, ('render_and_persist_html_pdf', '_source_content')),
            (html_pdf, derivatives, ('render_validated_html_to_pdf', '_clean_text', '_visible_blocks', '_paragraphs')),
        ):
            for name in names:
                with self.subTest(name=name):
                    fn=getattr(facade,name);sig=inspect.signature(fn)
                    args=[object() for p in sig.parameters.values() if p.kind==p.POSITIONAL_OR_KEYWORD]
                    kwargs={p.name:object() for p in sig.parameters.values() if p.kind==p.KEYWORD_ONLY}
                    token=object()
                    with patch.object(owner,name,return_value=token) as call:
                        self.assertIs(fn(*args,**kwargs),token)
                    self.assertEqual(call.call_args.args,tuple(args))
                    for key,value in kwargs.items():self.assertIs(call.call_args.kwargs[key],value)
                    for key in set(call.call_args.kwargs)-set(kwargs):self.assertIs(call.call_args.kwargs[key],getattr(facade,key))

    def test_contract_identity_and_pickle(self):
        for facade,owner,name in ((document_download_repo,download,'PersistedDownload'),(html_document_rendering_repo,rendering,'PersistedHtmlPdfRendering'),(html_pdf,derivatives,'HtmlPdfRender')):
            self.assertIs(getattr(facade,name),getattr(owner,name))
            self.assertIs(pickle.loads(pickle.dumps(getattr(owner,name))),getattr(facade,name))
        value=derivatives.render_validated_html_to_pdf(b'<html><p>A retained report</p></html>',source_url='https://example.com/report')
        self.assertEqual(pickle.loads(pickle.dumps(value)),value)
        self.assertEqual(value,html_pdf.render_validated_html_to_pdf(b'<html><p>A retained report</p></html>',source_url='https://example.com/report'))

    def test_layer_ownership_and_worker_imports(self):
        root=Path(__file__).parents[1]
        for path in [root/'db/documents/download.py',root/'db/documents/rendering.py']:
            for n in ast.walk(ast.parse(path.read_text())):
                if isinstance(n,ast.ImportFrom):self.assertFalse(set((n.module or '').split('.')) & {'commands','pipeline','view','operations','workflows'})
        worker=(root/'pipeline/run_document_download_worker.py').read_text()
        self.assertIn('from ..db.documents.download import persist_download_result',worker)
        self.assertIn('from ..db.documents.rendering import render_and_persist_html_pdf',worker)

    def test_transitive_imports_are_inert(self):
        script="import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode=True\ndef deny(*a, **k): raise AssertionError('forbidden side effect')\ndef audit(event,args):\n if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write during import')\n if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect=sqlite3.connect=subprocess.Popen=threading.Thread.start=deny\nimport scrapper.documents.downloader, scrapper.documents.html_pdf\nimport scrapper.documents.download.recording, scrapper.documents.download.derivatives\nimport scrapper.db.document_download_repo, scrapper.db.html_document_rendering_repo\nimport scrapper.db.documents.download, scrapper.db.documents.rendering\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
