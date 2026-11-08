"""Legacy facade and independent import contracts for C11.5."""
import pickle
import subprocess
import sys
import unittest
from scrapper.sources.screener import company, growth, shareholding

class ScreenerGrowthShareholdingTests(unittest.TestCase):
    def test_facade_symbols_and_serialized_callables(self):
        groups = {growth: ['GROWTH_METRIC_CODES', 'GROWTH_PERIOD_CODES', '_parse_growth'], shareholding: ['SHAREHOLDING_HOLDER_CODES', 'SHAREHOLDING_REGIME_BOUNDARY', '_shareholding_period_end', '_shareholding_regime', 'is_cross_regime_comparison', '_shareholder_code', '_parse_shareholding_rows', '_shareholding_table', '_parse_shareholding']}
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
import scrapper.sources.screener.growth
import scrapper.sources.screener.shareholding
assert 'scrapper.sources.screener.company' not in sys.modules
# The existing package initializer imports result_screen (and bs4/config).
assert 'scrapper.db' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_growth_unknown_and_missing_contract(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup('<section id="profit-loss"><table class="ranges-table"><tr><th colspan="2">Custom Growth</th></tr><tr><td>Odd:</td><td>%</td></tr><tr><td>3 Years:</td><td>12.5%</td></tr></table></section>', 'html.parser')
        cells, obs = growth._parse_growth(soup)
        self.assertEqual(len(cells), 5)
        self.assertEqual(cells[0].colspan, 2)
        self.assertEqual((obs[0].metric_code, obs[0].period_code, obs[0].value_decimal, obs[0].missing_kind), (None, None, None, 'blank'))
        self.assertEqual((obs[1].period_code, obs[1].value_decimal, obs[1].unit_code), ('3y', '12.5', 'PERCENT_POINT'))

    def test_shareholding_regime_units_and_cells(self):
        from bs4 import BeautifulSoup
        table = BeautifulSoup("""<table><thead><tr><th></th><th>Aug 2022</th><th>Sep 2022</th><th>Odd</th></tr></thead><tbody><tr><td><button onclick="showShareholders('foreign_institutions')">Foreign +</button></td><td rowspan="2"><a href="/raw">12%</a></td><td>—</td><td>1 / 2</td><td>extra</td></tr><tr><td>No. of Shareholders</td><td>1,234</td><td></td><td>NA</td></tr></tbody></table>""", 'html.parser').table
        cells, obs = shareholding._parse_shareholding_rows(table, 'quarterly')
        self.assertEqual(len(cells), 13)
        first, boundary, unknown, count, blank, na = obs
        self.assertEqual((first.holder_code, first.period_end, first.unit_code, first.value_decimal), ('foreign_institutions', '2022-08-31', 'PERCENT_POINT', '12'))
        self.assertEqual(boundary.classification_regime, 'xbrl_sep_2022_plus')
        self.assertTrue(shareholding.is_cross_regime_comparison(first, boundary))
        self.assertFalse(shareholding.is_cross_regime_comparison(first, unknown))
        self.assertEqual((unknown.period_end, unknown.classification_regime, unknown.value_decimal), (None, 'unknown', None))
        self.assertEqual((count.holder_code, count.unit_code, count.value_decimal), ('no_of_shareholders', 'COUNT', '1234'))
        self.assertIsNone(blank.value_decimal)
        self.assertIsNone(na.value_decimal)
        self.assertEqual((cells[5].rowspan, cells[5].link_url), (2, '/raw'))
