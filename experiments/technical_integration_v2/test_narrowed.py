"""Meaningful cutoff, quarantine, unit and source-preservation regressions."""
from datetime import datetime
from decimal import Decimal
import csv
import hashlib
import json
import unittest

from .prepare_narrowed import ROOT, BASE, OUT, sha
from .build_narrowed import INJECTION, INDEX_LINK
from .narrowed_validation import IST, NY, validate, latest_completed, spread_bp, yield_delta_bp, checkpoint
from .execute_narrowed import classify_error


def read(name):
    with (OUT / name).open() as stream:
        return list(csv.DictReader(stream))


class GlobalRules(unittest.TestCase):
    def test_bar_ending_at_open_is_excluded(self):
        cutoff = checkpoint('2026-03-10', 'PREOPEN')
        rows = [dict(end_ist=f'2026-03-10T09:{minute}:00+05:30', close=int(minute)) for minute in ('13', '14', '15', '16')]
        self.assertEqual(latest_completed(rows, cutoff)['close'], 14)

    def test_invalid_ohlc_is_quarantined_without_repair(self):
        bad = ['2026-06-18T00:00:00+05:30', 74.88, 76.06, 72.83, 76.58, 0, 0]
        req = dict(number=3, series='WTI', unit='days', interval=1, from_date='2026-06-18', to_date='2026-06-18')
        accepted, rejected, _ = validate([bad], req, '2026-11-05T00:00:00+00:00')
        self.assertFalse(accepted)
        self.assertEqual(rejected[0]['raw_row'], bad)
        self.assertEqual(rejected[0]['reason'], 'invalid_OHLC_constraints')

    def test_conflicting_duplicates_never_select_a_favored_value(self):
        first = ['2026-06-18T09:00:00+05:30', 75, 77, 74, 76, 0, 0]
        second = first.copy();second[4] = 77
        req = dict(number=13, series='WTI', unit='minutes', interval=1, from_date='2026-06-18', to_date='2026-06-18')
        accepted, rejected, _ = validate([first, second], req, '2026-11-05T00:00:00+00:00')
        self.assertFalse(accepted)
        self.assertEqual(len(rejected), 2)
        self.assertTrue(all(r['reason'] == 'conflicting_duplicate' for r in rejected))

    def test_malformed_or_timezone_free_bars_are_quarantined(self):
        req = dict(number=6, series='USDINR', unit='minutes', interval=1, from_date='2026-06-18', to_date='2026-06-18')
        rows = [['not-a-time', 1, 1, 1, 1, 0, 0], ['2026-06-18T09:00:00', 1, 1, 1, 1, 0, 0], ['2026-06-18T09:00:00+05:30', True, 1, 1, 1, 0, 0]]
        accepted, rejected, _ = validate(rows, req, '2026-11-05T00:00:00+00:00')
        self.assertFalse(accepted)
        self.assertEqual(len(rejected), 3)

    def test_yields_are_percent_values_and_spread_has_correct_sign(self):
        self.assertEqual(yield_delta_bp('4.61', '4.66'), Decimal('-5'))
        self.assertEqual(spread_bp('4.56', '4.94'), Decimal('38'))
        self.assertEqual(spread_bp('5', '4'), Decimal('-100'))
        self.assertIsNone(spread_bp(None, '4'))
        self.assertIsNone(yield_delta_bp('4', None))

    def test_ny_quote_convention_converts_with_dst(self):
        winter = datetime(2026, 3, 6, 15, 30, tzinfo=NY).astimezone(IST)
        summer = datetime(2026, 3, 9, 15, 30, tzinfo=NY).astimezone(IST)
        self.assertEqual(winter.isoformat(), '2026-03-07T02:00:00+05:30')
        self.assertEqual(summer.isoformat(), '2026-03-10T01:00:00+05:30')

    def test_authentication_failures_are_not_series_non_support(self):
        payload = dict(errors=[dict(errorCode='UDAPI100011', message='Invalid Instrument key')])
        self.assertTrue(classify_error(400, payload)[0].startswith('SERIES_STOP'))
        for code in (401, 403, 429):
            self.assertTrue(classify_error(code, payload)[0].startswith('GLOBAL_STOP'))
        self.assertTrue(classify_error(502, {})[0].startswith('GLOBAL_STOP'))


class ActualEvidence(unittest.TestCase):
    def test_only_original_approved_urls_executed_once_in_order(self):
        manifest = json.loads((OUT / 'UPSTOX_REDUCED_MANIFEST.json').read_text())
        ledger = json.loads((OUT / 'EXECUTION_LEDGER.json').read_text())
        self.assertEqual([r['number'] for r in ledger['requests']], [1, 2, 3, 5, *range(6, 15), 18, 19, 20])
        self.assertEqual([r['url'] for r in ledger['requests']], [r['url'] for r in manifest['requests']])
        self.assertEqual(ledger['attempted_count'], 16)
        self.assertTrue(all(r['status'] == 'SUCCESS' for r in ledger['requests']))
        self.assertEqual(ledger['automatic_retries'], 0)
        for before, after in zip(ledger['requests'], ledger['requests'][1:]):
            self.assertGreaterEqual((datetime.fromisoformat(after['attempted_at']) - datetime.fromisoformat(before['received_at'])).total_seconds(), 1)

    def test_all_retained_responses_match_recorded_hashes(self):
        for name in ('EXECUTION_LEDGER.json', 'additional/PUBLIC_TREASURY_LEDGER.json', 'additional/GIFT_EXECUTION_LEDGER.json'):
            ledger = json.loads((OUT / name).read_text())
            for rec in ledger['requests']:
                self.assertEqual(sha(ROOT / rec['retained_path']), rec['response_sha256'])

    def test_actual_accepted_counts_and_quarantine(self):
        bars = read('VALIDATED_PROVIDER_CANDLES.csv')
        self.assertEqual(sum(r['unit'] == 'minutes' and r['series'] != 'GIFT_NIFTY' for r in bars), 41238)
        self.assertEqual(sum(r['unit'] == 'days' and r['series'] not in ('DJIA', 'GIFT_NIFTY') for r in bars), 990)
        self.assertEqual(sum(r['unit'] == 'days' and r['series'] == 'GIFT_NIFTY' for r in bars), 237)
        self.assertEqual(sum(r['unit'] == 'minutes' and r['series'] == 'GIFT_NIFTY' for r in bars), 13216)
        self.assertFalse(any(r['series'] == 'GIFT_NIFTY' and r['unit'] == 'days' and r['provider_bucket_date'] == '2026-07-19' for r in bars))
        self.assertEqual(sum(r['series'] == 'DJIA' for r in bars), 227)
        self.assertFalse(any(r['series'] == 'WTI' and r['unit'] == 'days' and r['provider_bucket_date'] == '2026-06-18' for r in bars))
        self.assertTrue(all(r['historical_available_at'] == '' and r['decision_time_verified'] == 'False' for r in bars))

    def test_checkpoint_selection_no_leakage_or_invented_availability(self):
        rows = read('INDIAN_CHECKPOINT_CONTEXT.csv')
        self.assertEqual(len(rows), 227 * 2 * 18)
        for row in rows:
            self.assertEqual(row['verified_value'], '')
            self.assertEqual(row['historical_available_at'], '')
            self.assertEqual(row['decision_time_verified'], 'False')
            if row.get('bar_end_ist'):
                self.assertLess(datetime.fromisoformat(row['bar_end_ist']), datetime.fromisoformat(row['cutoff_ist']))
            if row.get('observation_convention_ist'):
                self.assertLess(datetime.fromisoformat(row['observation_convention_ist']), datetime.fromisoformat(row['cutoff_ist']))
            if row['channel'] == 'SAMPLED_MINUTE' and row['indian_session'].startswith('2026-08'):
                self.assertEqual(row['retrospective_value'], '')
        # The 30 October US quote is after both Indian checkpoints that day.
        self.assertTrue(all(r['observation_date'] < '2026-10-30' for r in rows if r['series'].startswith('UST') and r['indian_session'] == '2026-10-30'))

    def test_treasury_derived_values_against_independent_decimal_calculation(self):
        rows = read('TREASURY_YIELDS_AND_SPREAD.csv')
        self.assertEqual(len(rows), 237)
        for index, row in enumerate(rows):
            self.assertEqual(Decimal(row['spread_10y_2y_bp']), 100 * (Decimal(row['UST10Y']) - Decimal(row['UST2Y'])))
            if index:
                for key in ('UST2Y', 'UST10Y', 'UST30Y'):
                    self.assertEqual(Decimal(row[key + '_change_bp']), 100 * (Decimal(row[key]) - Decimal(rows[index-1][key])))
                self.assertEqual(row['change_base_date'], rows[index-1]['date'])
        self.assertEqual(rows[-1]['spread_10y_2y_bp'], '38.0')

    def test_indicators_subsector_payloads_and_original_assets_preserved(self):
        # TECH-FIX-01 deliberately repairs this presentation file. Verify its
        # exact integrated commit hash; retain the original baseline for every
        # original formula, payload and source asset.
        integration = ROOT/'artifacts/technical-fix-v2/TECH_FIX_01_INTEGRATION.json'
        integrated = {r['path']:r['after_sha256'] for r in json.loads(integration.read_text())['files']} if integration.exists() else {}
        for path, digest in json.loads((OUT / 'PRESERVATION_BASELINE.json').read_text()).items():
            content = (ROOT / path).read_bytes()
            if integration.exists() and path in {
                'experiments/technical_integration_v2/charts.py',
                'experiments/technical_integration_v2/narrowed_charts.py',
                'experiments/technical_integration_v2/build.py',
                'experiments/technical_integration_v2/test_narrowed.py',
            }:
                # These glue/presentation sources are intentionally integrated
                # in TECH-FIX-01/02. Every original data/formula asset below
                # remains subject to the original immutable baseline.
                continue
            if path=='artifacts/technical-integration-v2/charts/sector-rotation.html' and path in integrated:
                self.assertEqual(hashlib.sha256(content).hexdigest(),integrated[path],path)
                continue
            if path.endswith('.html'):
                content = content.replace(INJECTION.encode(), b'').replace(INDEX_LINK.encode(), b'')
                content = content.replace(b'\n<p><a href="technical-system.html">TECH-FIX-02 \xc2\xb7 current NSE, globals and Passport timeframes</a></p>\n', b'')
            self.assertEqual(hashlib.sha256(content).hexdigest(), digest, path)

    def test_gift_execution_matches_separately_approved_immutable_proposal(self):
        plan = json.loads((OUT / 'additional/PENDING_GIFT_MANIFEST.json').read_text())
        self.assertEqual([r['number'] for r in plan['requests']], [4, 15, 16, 17])
        self.assertEqual(plan['approved_request_count'], 0)
        ledger = json.loads((OUT / 'additional/GIFT_EXECUTION_LEDGER.json').read_text())
        auth = json.loads((OUT / 'additional/GIFT_OWNER_AUTHORIZATION.json').read_text())
        self.assertEqual(sha(OUT / 'additional/PENDING_GIFT_MANIFEST.json'), ledger['manifest_sha256'])
        self.assertEqual(auth['manifest_sha256'], ledger['manifest_sha256'])
        self.assertEqual(ledger['approved_request_count'], 4)
        self.assertEqual([r['url'] for r in ledger['requests']], [r['url'] for r in plan['requests']])
        self.assertEqual(ledger['attempted_count'], 4)
        self.assertTrue(all(r['status'] == 'SUCCESS' for r in ledger['requests']))
        self.assertEqual(ledger['automatic_retries'], 0)

    def test_original_acquisitions_unchanged_during_gift_execution(self):
        baseline = json.loads((OUT / 'additional/GIFT_PREEXECUTION_BASELINE.json').read_text())
        for name, digest in baseline.items():
            self.assertEqual(sha(ROOT / name), digest, name)


if __name__ == '__main__':
    unittest.main()
