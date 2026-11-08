"""Legacy facade and independent import contracts for C11.4."""
import pickle
import subprocess
import sys
import unittest
from scrapper.sources.screener import company, statements

class ScreenerStatementsTests(unittest.TestCase):
    def test_facade_symbols_and_serialized_callables(self):
        groups = {statements: ['LINE_ITEM_CODES', '_fiscal_year', '_canonical_line_label', '_expandable_marker', '_quarter_period', '_annual_period', '_parse_statement_table', '_parse_quarters', '_parse_profit_loss', '_parse_balance_sheet', '_parse_cash_flow', '_parse_annual_ratios']}
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
import scrapper.sources.screener.statements
assert 'scrapper.sources.screener.company' not in sys.modules
# The existing package initializer imports result_screen (and bs4/config).
assert 'scrapper.db' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_irregular_statement_preserves_cells_periods_and_missing_values(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("""<section id="profit-loss"><table><thead><tr><th></th><th data-date-key="2024-03-31">Mar 2024</th><th>TTM</th></tr></thead><tbody><tr><td><button onclick="Company.showSchedule('Sales', 1)">Sales +</button></td><td rowspan="2"><a href="/raw">1,234</a></td><td>—</td><td>extra</td></tr><tr><td>Custom</td><td>1 / 2</td><td></td></tr></tbody></table></section>""", 'html.parser')
        heading, cells, observations = statements._parse_profit_loss(soup)
        self.assertEqual(heading, 'Profit & Loss')
        self.assertEqual(len(cells), 10)
        self.assertEqual(len(observations), 4)
        first, ttm, unknown, blank = observations
        self.assertEqual((first.line_item_code, first.period_end, first.fiscal_year, first.value_decimal), ('sales', '2024-03-31', 2024, '1234'))
        self.assertEqual((first.is_expandable, first.schedule_key), (True, 'Sales'))
        self.assertEqual((ttm.period_type, ttm.period_end, ttm.fiscal_year, ttm.value_decimal), ('ttm', None, None, None))
        self.assertIsNone(unknown.line_item_code)
        self.assertIsNone(unknown.value_decimal)
        self.assertIsNone(blank.value_decimal)
        self.assertEqual(cells[4].rowspan, 2)
        self.assertEqual(cells[4].link_url, '/raw')
