"""Validation ownership and safety contracts for C12.1."""
import subprocess
import sys
import unittest

from scrapper.validation import screener_rules as rules


class ScreenerValidationRuleTests(unittest.TestCase):
    def test_absent_identity_warns_but_mismatch_blocks(self):
        def identity(actual):
            return rules.identity_rules(actual_identifiers=actual, expected_scope='standalone',
                expected_symbol='ABC', manifest_errors=[], scope=('standalone',))
        self.assertEqual(identity(set())[1], ('identity_symbol', 'warning', False, 'ABC', None))
        self.assertEqual(identity({'XYZ'})[1], ('identity_symbol', 'error', False, 'ABC', 'XYZ'))
        self.assertTrue(identity({'ABC'})[1][2])
        self.assertEqual(identity(set())[0][1:3], ('warning', True))

    def test_scope_remains_hard_failure(self):
        for scope in (None, ('consolidated',)):
            result = rules.identity_rules(actual_identifiers={'ABC'}, expected_scope='standalone',
                expected_symbol='ABC', manifest_errors=[], scope=scope)
            self.assertEqual(result[2][1:3], ('error', False))

    def test_unknown_metric_remains_manual_review(self):
        self.assertEqual(rules.metric_review_rules(unknown_metrics='New Ratio'),
            [('known_ratio_labels', 'manual_review', False, 'none', 'New Ratio')])
        self.assertTrue(rules.metric_review_rules(unknown_metrics=None)[0][2])

    def test_explicit_shareholding_unavailability_does_not_mask_bad_values(self):
        context = dict(shareholding_bad_regime=0, shareholding_count=0,
            shareholding_total_anomalies=0, shareholding_total_detail=None,
            shareholding_untyped=1, shareholding_warning_captured=True,
            shareholding_warning_present=0, source_has_shareholding_warning=False,
            unavailable_frequencies={'quarterly', 'yearly'}, yearly_bad_regime=0,
            yearly_count=0, yearly_total_anomalies=0, yearly_total_detail=None, yearly_untyped=0)
        result = rules.shareholding_rules(**context)
        self.assertTrue(result[0][2]); self.assertFalse(result[1][2]); self.assertTrue(result[5][2])
        context['unavailable_frequencies'] = set()
        self.assertFalse(rules.shareholding_rules(**context)[0][2])
        self.assertFalse(rules.shareholding_rules(**context)[5][2])

    def test_rule_owner_import_is_inert(self):
        script = '''import sys, socket, sqlite3, subprocess, threading
sys.dont_write_bytecode = True
def deny(*args, **kwargs): raise AssertionError('forbidden import side effect')
def audit(event, args):
    if event == 'open' and ((isinstance(args[1], str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):
        raise AssertionError('write during import')
    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.connect','sqlite3.connect'):
        raise AssertionError(event)
sys.addaudithook(audit)
socket.socket = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny
import scrapper.validation.screener_rules
'''
        result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
