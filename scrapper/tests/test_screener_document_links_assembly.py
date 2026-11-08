"""Legacy facade and independent import contracts for C11.6."""
import pickle
import subprocess
import sys
import unittest
from scrapper.sources.screener import company, document_links, assembly

class ScreenerDocumentLinksAssemblyTests(unittest.TestCase):
    def test_facade_symbols_and_serialized_callables(self):
        groups = {document_links: ['CONCALL_LINK_TYPES', '_direct_text', '_split_publisher', '_parse_documents'], assembly: ['parse_company_page']}
        for module, names in groups.items():
            for name in names:
                value = getattr(module, name)
                self.assertIs(getattr(company, name), value)
                if callable(value):
                    self.assertIs(pickle.loads(pickle.dumps(value)), value)

    def test_new_modules_import_is_inert_and_independent_of_facade(self):
        code = '''
import sys

def guard(event, args):
    if event == 'open' and (isinstance(args[1], str) and any(c in args[1] for c in 'wax+')
                          or isinstance(args[2], int) and args[2] & 3):
        raise AssertionError(('write', args))
    if event.startswith(('socket.', 'subprocess.', 'sqlite3.connect', 'os.mkdir', 'os.rename', 'os.remove')):
        raise AssertionError(event)
sys.addaudithook(guard)
import threading, _thread
_thread.start_new_thread = lambda *a, **k: (_ for _ in ()).throw(AssertionError('thread'))
threading.Thread.start = _thread.start_new_thread
import scrapper.sources.screener.document_links
import scrapper.sources.screener.assembly
assert 'scrapper.sources.screener.company' not in sys.modules
# The existing package initializer imports result_screen (and bs4/config).
assert 'scrapper.db' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_document_metadata_and_unavailable_slots(self):
        from bs4 import BeautifulSoup
        html = '<section id="documents"><h2>Documents</h2><div class="credit-ratings"><ul class="list-links"><li><a href="/rating">Rating <span class="ink-600">3 Jul from CARE</span></a></li></ul></div><div class="concalls"><ul class="list-links"><li><div class="nowrap">Jul 2026</div><a class="concall-link" href="/transcript">Transcript</a><button class="concall-link" data-url="/modal">AI Summary</button><span class="concall-link">Rec</span><a class="concall-link" href="/other">Unknown</a></li></ul></div></section>'
        heading, fields, links = document_links._parse_documents(BeautifulSoup(html, 'html.parser'))
        self.assertEqual(heading, 'Documents')
        self.assertEqual([x.position for x in links], list(range(5)))
        self.assertEqual((links[0].title_raw, links[0].period_label_raw, links[0].publisher), ('Rating', '3 Jul', 'CARE'))
        self.assertEqual((links[2].document_type, links[2].publisher, links[2].source_url), ('concall_notes', 'screener', '/modal'))
        self.assertEqual((links[3].document_type, links[3].availability_status, links[3].source_url), ('concall_recording', 'unavailable', None))
        self.assertEqual(links[4].document_type, 'concall_other')
        self.assertEqual(fields[0].link_url, '/rating')
        self.assertIn('3 Jul from CARE', fields[0].raw_text)

    def test_document_section_errors(self):
        from bs4 import BeautifulSoup
        for html, error in [('', 'Documents section is missing'), ('<section id="documents"></section>', 'Documents section has no links')]:
            with self.subTest(html=html), self.assertRaisesRegex(ValueError, error):
                document_links._parse_documents(BeautifulSoup(html, 'html.parser'))

    def test_current_button_chips_and_mixed_markup(self):
        from bs4 import BeautifulSoup
        html = '''<section id="documents"><div class="concalls"><ul class="list-links"><li>
          <div class="nowrap">Oct 2026</div>
          <a class="button-chip concall-link" href="https://issuer.example/t.pdf" title="Raw Transcript">Transcript</a>
          <button class="button-chip" data-url="/concalls/summary/1/">AI Summary</button>
          <a class="button-chip" href="https://issuer.example/p.pdf">PPT</a>
          <div class="button-chip">Rec</div></li></ul></div></section>'''
        _, _, links = document_links._parse_documents(BeautifulSoup(html, 'html.parser'))
        self.assertEqual([l.document_type for l in links],
            ['concall_transcript','concall_notes','concall_ppt','concall_recording'])
        self.assertEqual([l.period_label_raw for l in links], ['Oct 2026']*4)
        self.assertEqual(links[3].availability_status, 'unavailable')
