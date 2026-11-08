"""Legacy facade and independent import contracts for C11.3."""
import pickle
import subprocess
import sys
import unittest
from scrapper.sources.screener import company, profile, commentary

class ScreenerProfileCommentaryTests(unittest.TestCase):
    def test_facade_symbols_and_serialized_callables(self):
        groups = {profile: ['_parse_company_profile'],
                  commentary: ['parse_commentary_fragment', '_parse_pros_cons']}
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
import scrapper.sources.screener.profile
import scrapper.sources.screener.commentary
assert 'scrapper.sources.screener.company' not in sys.modules
# The existing package initializer imports result_screen (and bs4/config).
assert 'scrapper.db' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

