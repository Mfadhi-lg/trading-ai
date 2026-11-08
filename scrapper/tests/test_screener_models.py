"""Compatibility contracts for the source record extraction."""
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
import pickle
import subprocess
import sys
import unittest

from scrapper.sources.screener import company, models


class ScreenerModelsTests(unittest.TestCase):
    def test_facade_identity_and_pickle_round_trip(self):
        names = [name for name, obj in vars(models).items()
                 if isinstance(obj, type) and hasattr(obj, '__dataclass_fields__')]
        self.assertEqual(len(names), 11)
        for name in names:
            self.assertIs(getattr(company, name), getattr(models, name))
            self.assertEqual(getattr(models, name).__module__, company.__name__)
        fixture = Path(__file__).parent / 'fixtures/screener/LAURUSLABS_standalone.html'
        record = company.parse_company_page(fixture.read_bytes(), '2026-07-24T10:00:00Z')
        restored = pickle.loads(pickle.dumps(record))
        self.assertEqual(asdict(record), asdict(restored))
        self.assertIs(type(restored), models.ParsedCompany)
        with self.assertRaises(FrozenInstanceError):
            record.quote.display_price = 'changed'
        other = models.ParsedCompany(record.company_name, record.nse_symbol, record.bse_code,
                                     record.reporting_scope, record.source_company_id,
                                     record.source_warehouse_id, record.canonical_url, record.quote)
        self.assertIsNot(record.document_links, other.document_links)
        self.assertEqual(other.document_links, [])

    def test_models_import_is_inert_and_independent_of_parser(self):
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
import scrapper.sources.screener.models
assert 'scrapper.sources.screener.company' not in sys.modules
# The existing package initializer imports result_screen (and bs4/config).
assert 'scrapper.db' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
