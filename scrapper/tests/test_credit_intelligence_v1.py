"""Offline vertical slice regression and fail-closed admission tests."""
from scrapper.tests.retained_audits import retained_source_audit

import copy
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scrapper.research.document_intelligence import credit_v1 as v1
from scrapper.research.document_intelligence.schema import DocumentContext

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / 'docs/credit-rating-intelligence-v1'


@retained_source_audit
class CreditV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = {s: v1.build_record(DEV / f'{s}-manifest.json') for s in ['vimta', 'ekc']}
        cls.manifest = v1.read_json(DEV / 'vimta-manifest.json')
        cls.extraction = v1.read_json(ROOT / cls.manifest['canonical']['path'])
        cls.context = DocumentContext(**cls.manifest['context'])
        cls.review = v1.read_json(ROOT / cls.manifest['reviews']['path'])
        cls.claim = next(c for c in cls.records['vimta']['claims'] if c['origin']['mode'] == 'replayed_ai')

    def test_both_original_identities_and_dates(self):
        self.assertEqual(self.records['vimta']['identity']['report_date'], '2023-07-11')
        self.assertEqual(self.records['ekc']['identity']['report_date'], '2024-09-26')
        self.assertEqual(self.records['vimta']['source_coverage']['physical_pages'], 6)
        self.assertEqual(self.records['ekc']['source_coverage']['physical_pages'], 7)
        self.assertTrue(all(r['identity']['publication_date'] is None for r in self.records.values()))

    def test_original_and_canonical_hash_tampering(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as td:
            for key in ['pdf', 'canonical', 'responses', 'reviews']:
                m = copy.deepcopy(self.manifest)
                target = m[key][0] if key == 'responses' else m[key]
                target['sha256'] = '0' * 64
                p = Path(td) / 'manifest.json'; p.write_text(json.dumps(m))
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    v1.build_record(p)

    def test_facility_tenor_split_and_no_double_counting(self):
        claims = [c for c in self.records['vimta']['claims'] if c['statement_kind'] == 'RATING_EVENT']
        self.assertEqual(len(claims), 4)
        both = [c['value'] for c in claims if '/' in c['value']['facility']]
        self.assertEqual({c['tenor'] for c in both}, {'LONG_TERM', 'SHORT_TERM'})
        self.assertEqual(len({c['facility_id'] for c in both}), 1)
        self.assertEqual({c['facility_amount']['value'] for c in both}, {'34.00'})

    def test_upgrade_is_not_withdrawal_or_outlook_change(self):
        ekc = self.records['ekc']
        upgrade = next(c for c in ekc['claims'] if c['statement_kind'] == 'RATING_EVENT' and c['value']['action'] == 'upgraded')
        self.assertEqual(upgrade['value']['previous_grade'], 'CARE A3+')
        self.assertEqual(upgrade['value']['grade'], 'CARE A2')
        self.assertFalse(upgrade['value']['withdrawal'])
        self.assertEqual(ekc['coverage_ledger']['rating.withdrawal']['state'], 'UNRESOLVED')
        self.assertTrue(any('multiple_rating_actions' in c['validation']['reason'] for c in ekc['evidence_review_queue']))

    def test_accounting_scope_units_periods_preserved(self):
        c = next(c for c in self.records['ekc']['claims'] if 'top five customers' in c['statement'])
        self.assertEqual(c['accounting_scope'], 'standalone')
        self.assertEqual(c['period'], 'FY24')
        cash = next(c for c in self.records['vimta']['claims'] if 'free liquidity' in c['statement'])
        self.assertEqual(cash['accounting_scope'], 'consolidated')
        self.assertEqual(cash['unit'], '₹ crore')
        self.assertIn('approximately', cash['statement'])
        self.assertEqual(cash['period'], 'March 31, 2023')

    def test_explanations_expectations_and_conditions(self):
        r = self.records['vimta']
        self.assertEqual(r['groups']['liquidity']['assessment']['state'], 'DISCLOSED')
        self.assertTrue(any('better fixed cost management' in c['statement'] for c in r['claims']))
        expectation = next(c for c in r['claims'] if 'CARE Ratings expects' in c['statement'])
        self.assertEqual(expectation['statement_kind'], 'agency_assessment')
        self.assertEqual(expectation['attribution'], 'CARE Ratings')
        self.assertTrue(any('sustained basis' in c['statement'] for c in r['claims']))
        omitted = next(c for c in r['evidence_review_queue'] if '0.30x' in c['statement'])
        self.assertEqual(omitted['evidence_decision'], 'WITHHELD')
        self.assertIn('absence of any significant debt-funded capex', omitted['evidence'][0]['quote'])

    def test_unsupported_liquidity_never_enters_trusted_records(self):
        r = self.records['ekc']
        self.assertFalse(any('64.9' in c['statement'] for c in r['claims']))
        withheld = next(c for c in r['evidence_review_queue'] if '64.9' in c['statement'])
        self.assertEqual(withheld['evidence_decision'], 'WITHHELD')
        self.assertTrue(any('FY26-FY27' in e['quote'] for e in withheld['evidence']))
        self.assertEqual(r['groups']['liquidity']['cash_and_liquid_balances']['state'], 'UNRESOLVED')

    def test_no_keyword_or_reference_match_admission(self):
        c = self.claim; origin = c['origin']; key = origin['response_id'] + ':' + origin['json_pointer']
        review = self.review['claims'][key]
        for field, value in [('statement', 'Company has no debt.'), ('reported_value', '999'),
                             ('unit', 'USD million'), ('period', 'FY99'), ('accounting_scope', 'standalone')]:
            candidate = copy.deepcopy(c['original_candidate']); candidate[field] = value
            result = v1.replay_candidate(candidate, review, self.context, self.extraction, origin)
            self.assertEqual(result['evidence_decision'], 'WITHHELD')
        candidate = {**c['original_candidate'], 'reference_match': True}
        result = v1.replay_candidate(candidate, None, self.context, self.extraction, origin)
        self.assertEqual(result['evidence_decision'], 'NEEDS_REVIEW')

    def test_source_region_integrity(self):
        ev = copy.deepcopy(self.claim['evidence'])
        for field, value in [('page', 999), ('start', 9), ('quote', 'fabricated'), ('text_origin', 'ocr')]:
            bad = copy.deepcopy(ev); bad[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                v1.attach_evidence(self.context, self.extraction, bad)
        altered = copy.deepcopy(self.extraction); altered['pages'][0]['selected_text'] += ' forged'
        origin = self.claim['origin']; key = origin['response_id'] + ':' + origin['json_pointer']
        result = v1.replay_candidate(self.claim['original_candidate'], self.review['claims'][key], self.context, altered, origin)
        self.assertEqual(result['evidence_decision'], 'WITHHELD')

    def test_missing_and_unprocessed_are_not_not_disclosed(self):
        self.assertEqual(v1.field_state([]), 'UNRESOLVED')
        self.assertEqual(v1.field_state([], explicit_absence=True), 'UNRESOLVED')
        self.assertEqual(v1.field_state([], complete_source_review=True, explicit_absence=True), 'NOT_DISCLOSED')
        self.assertFalse(any(v['state'] == 'NOT_DISCLOSED' for r in self.records.values() for v in r['coverage_ledger'].values()))
        origin = self.claim['origin']; key = origin['response_id'] + ':' + origin['json_pointer']
        extraction = copy.deepcopy(self.extraction); extraction['pages'][0]['selected_text_origin'] = 'ocr'
        review = {**self.review['claims'][key], 'canonical_sha256': v1.canonical_hash(extraction)}
        result = v1.replay_candidate(self.claim['original_candidate'], review, self.context, extraction, origin)
        self.assertEqual(result['evidence_decision'], 'WITHHELD')

    def test_partial_extraction_cannot_promote_deterministic_findings(self):
        partial = copy.deepcopy(self.extraction); partial['extraction_status'] = 'partial'
        claims = v1.deterministic_ratings(self.context, partial)
        self.assertEqual(claims, [])

    def test_deterministic_replay_and_inputs_unchanged(self):
        for stem, first in self.records.items():
            before = {d['path']: v1.digest(ROOT / d['path']) for d in first['provenance']['inputs']}
            self.assertEqual(first, v1.build_record(DEV / f'{stem}-manifest.json'))
            self.assertEqual(before, {p: v1.digest(ROOT / p) for p in before})

    def test_network_and_database_denied(self):
        with v1.offline_guard():
            with self.assertRaises(RuntimeError): socket.create_connection(('example.com', 443))
            with self.assertRaises(RuntimeError): socket.getaddrinfo('example.com', 443)
            with self.assertRaises(RuntimeError): sqlite3.connect(':memory:')
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('network attempt')), patch.object(sqlite3, 'connect', side_effect=AssertionError('DB attempt')):
            v1.build_record(DEV / 'vimta-manifest.json')

    def test_output_contract_and_write_bounds(self):
        for r in self.records.values(): v1.validate_record(r)
        bad = copy.deepcopy(self.records['vimta']); bad['claims'][0]['evidence_decision'] = 'WITHHELD'
        with self.assertRaises(ValueError): v1.validate_record(bad)
        with tempfile.TemporaryDirectory(dir="/tmp") as td:
            output = Path(td) / 'new'; v1.export_record(self.records['vimta'], output, 'vimta')
            self.assertTrue((output / 'vimta-credit-intelligence.json').is_file())
            with self.assertRaises(ValueError): v1.export_record(self.records['vimta'], output, 'vimta')
        with self.assertRaises(ValueError): v1.export_record(self.records['vimta'], ROOT / 'scrapper' / 'store' / 'forbidden', 'vimta')

    def test_merged_financial_table_audit_status_is_not_lost(self):
        r = self.records['ekc']
        c = next(c for c in r['evidence_review_queue'] if c['statement'].startswith('Brief Financials:'))
        self.assertEqual(c['evidence_decision'], 'NEEDS_REVIEW')
        self.assertTrue(any('Q1FY25 (UA)' in e['quote'] for e in c['evidence']))


if __name__ == '__main__':
    unittest.main()
