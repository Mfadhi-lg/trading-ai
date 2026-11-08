
from scrapper.tests.retained_audits import retained_source_audit
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scrapper.research.document_intelligence import credit_two_stage as t
from scrapper.research.document_intelligence.evidence import canonical_hash
from scrapper.research.document_intelligence.schema import DocumentContext

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / 'artifacts/credit-rating-intelligence-v1/two-stage-v1'


class TwoStageTests(unittest.TestCase):
    def setUp(self):
        self.ctx = DocumentContext(document_id='test', company_name='Test',
            content_sha256='a'*64, document_type='credit_rating', report_date='2026-01-09')
        self.text = 'Agency assesses liquidity adequate, subject to sanctioned limits and drawing power.'
        self.extraction = {'extraction_status': 'ok', 'pages': [{'page_or_slide_number': 1,
            'selected_text': self.text, 'selected_text_origin': 'native', 'elements': []}]}
        self.mapping = {'S001': [{'page': 1, 'kind': 'page_text', 'element': None,
            'start': 0, 'end': len(self.text), 'quote': self.text, 'text_origin': 'native', 'bbox': None}]}
        self.candidate = {'id': 'a', 'statement': self.text, 'field': 'liquidity.assessment',
            'disclosure_category': 'liquidity', 'value': None, 'unit': None, 'period': None,
            'period_kind': 'unknown', 'accounting_scope': None, 'attribution': 'Agency',
            'statement_kind': 'assessment', 'conditions': ['subject to sanctioned limits and drawing power'],
            'qualifications': [], 'support_region_ids': ['S001']}
        self.parents, self.packet, self.invalid = t.propose([self.candidate], self.ctx,
            self.extraction, self.mapping, 'b'*64)
        self.review = {**t.review_binding(self.packet['candidates'][0], self.packet),
            'decision': 'SUPPORTED', 'reason': 'Explicit complete assertion in original passage.',
            'reviewer': 'test reviewer', 'reviewed_at': '2026-10-24', 'method': 'human_source_review',
            'checks': {d: {'verdict': 'PASS', 'basis': 'Checked entire source assertion and qualifiers.'} for d in t.DIMENSIONS}}

    def verify(self, review=None, packet=None):
        return t.verify(self.parents, packet or self.packet, self.ctx, self.extraction,
                        {'a': review or self.review})[0]

    def package(self, reviews=None):
        return t.trusted_package(self.parents, self.packet, self.ctx, self.extraction, reviews)

    def test_locators_do_not_establish_semantics(self):
        claim = t.verify(self.parents, self.packet, self.ctx, self.extraction)[0]
        self.assertTrue(claim['validation']['source_locators'])
        self.assertEqual(claim['evidence_decision'], 'NEEDS_REVIEW')
        self.assertFalse(claim['validation']['automatic_semantic_entailment'])
        self.assertEqual(self.package()['claims'], [])

    def test_explicit_complete_review_preserves_qualifications(self):
        p = self.package({'a': self.review})
        self.assertEqual(len(p['claims']), 1)
        self.assertEqual(p['claims'][0]['conditions'], self.candidate['conditions'])
        self.assertEqual(p['claims'][0]['source_references'][0]['quote'], self.text)
        self.assertEqual(self.candidate['statement'], self.text)

    def test_every_material_dimension_is_hash_bound(self):
        for field, value in [('statement', 'Unencumbered cash is available.'), ('value', '390'),
                             ('unit', 'lakh'), ('period', 'September 30, 2025'),
                             ('accounting_scope', 'consolidated'), ('attribution', 'Issuer'),
                             ('conditions', []), ('statement_kind', 'expectation')]:
            packet = deepcopy(self.packet)
            packet['candidates'][0][field] = value
            with self.subTest(field=field):
                self.assertEqual(self.verify(packet=packet)['evidence_decision'], 'WITHHELD')

    def test_stale_source_and_review_rejected(self):
        for key in ['source_sha256', 'canonical_sha256', 'support_sha256', 'candidate_sha256']:
            review = deepcopy(self.review)
            review[key] = 'c'*64
            self.assertEqual(self.verify(review)['evidence_decision'], 'WITHHELD')

    def test_incomplete_semantic_review_cannot_admit(self):
        for verdict in ['FAIL', 'UNRESOLVED']:
            review = deepcopy(self.review)
            review['checks']['complete_claim']['verdict'] = verdict
            self.assertEqual(self.verify(review)['evidence_decision'], 'NEEDS_REVIEW')
        review = deepcopy(self.review)
        del review['checks']['passage_relevance']
        self.assertEqual(self.verify(review)['evidence_decision'], 'WITHHELD')
        review = deepcopy(self.review)
        review['checks']['complete_claim']['verdict'] = 'NOT_APPLICABLE'
        self.assertEqual(self.verify(review)['evidence_decision'], 'NEEDS_REVIEW')

    def test_rejections_and_reviewer_answers_never_enter_stage2(self):
        review = deepcopy(self.review)
        review.update(decision='WITHHELD', reason='REVIEWER_REFERENCE_ANSWER')
        p = self.package({'a': review})
        result = t.reasoning_input(p, 'PROMPT', expected_package_sha256=canonical_hash(p))
        self.assertEqual(result['assessment_mode'], 'INSUFFICIENT_EVIDENCE')
        self.assertNotIn('REVIEWER_REFERENCE_ANSWER', json.dumps(result))
        self.assertNotIn(self.text, json.dumps(result))
        self.assertEqual(result['tools'], [])
        self.assertEqual(len(result['messages']), 2)

    def test_package_tampering_and_extra_input_rejected(self):
        p = self.package({'a': self.review})
        digest = canonical_hash(p)
        p['claims'][0]['statement'] = 'Invented funded capex.'
        with self.assertRaises(ValueError):
            t.reasoning_input(p, 'P', expected_package_sha256=digest)
        p = self.package({'a': self.review})
        p['reviewer_answers'] = 'Leak'
        with self.assertRaises(ValueError):
            t.reasoning_input(p, 'P', expected_package_sha256=canonical_hash(p))
        p = self.package({'a': self.review})
        p['claims'][0]['original_report'] = 'Leak'
        with self.assertRaises(ValueError):
            t.reasoning_input(p, 'P', expected_package_sha256=canonical_hash(p))

    def test_invalid_and_irrelevant_locators_remain_untrusted(self):
        candidate = deepcopy(self.candidate)
        candidate['support_region_ids'] = ['UNKNOWN']
        _, packet, invalid = t.propose([candidate], self.ctx, self.extraction, self.mapping, 'b'*64)
        self.assertEqual(packet['candidates'], [])
        self.assertEqual(invalid[0]['evidence_decision'], 'WITHHELD')
        # Valid but irrelevant citations need semantic review, never keyword admission.
        candidate['support_region_ids'] = ['S001']
        candidate['statement'] = 'There are high competitive entry barriers.'
        parents, packet, _ = t.propose([candidate], self.ctx, self.extraction, self.mapping, 'b'*64)
        self.assertEqual(t.verify(parents, packet, self.ctx, self.extraction)[0]['evidence_decision'], 'NEEDS_REVIEW')

    def test_damaged_source_cannot_be_admitted(self):
        extraction = deepcopy(self.extraction)
        extraction['extraction_status'] = 'partial'
        parents, packet, _ = t.propose([self.candidate], self.ctx, extraction, self.mapping, 'b'*64)
        review = {**self.review, **t.review_binding(packet['candidates'][0], packet)}
        self.assertEqual(t.verify(parents, packet, self.ctx, extraction, {'a': review})[0]['evidence_decision'], 'WITHHELD')

    def test_stage1_envelope_and_coverage_accounting(self):
        output = {'report': '[a] Liquidity assessment.', 'claim_ledger': [self.candidate],
                  'coverage_ledger': [{'region_id': 'S001', 'claim_ids': ['a'], 'status': 'REVIEWED', 'note': 'Liquidity assessment found.'}],
                  'ambiguities': []}
        self.assertTrue(t.validate_understanding(output, ['S001']))
        output['coverage_ledger'] = []
        with self.assertRaises(ValueError):
            t.validate_understanding(output, ['S001'])

    def test_citation_audit_does_not_claim_semantic_validation(self):
        p = self.package({'a': self.review})
        result = t.audit_reasoning_citations('Claim E-'+'d'*64, p)
        self.assertEqual(len(result['unknown_ids']), 1)
        self.assertTrue(result['semantic_review_required'])

    def test_complete_saved_stage1_handoff_preserves_raw_and_isolates_prose(self):
        output = {'report': 'UNVERIFIED_REPORT_MUST_NOT_LEAK', 'claim_ledger': [self.candidate],
            'coverage_ledger': [{'region_id': 'S001', 'claim_ids': ['a'], 'status': 'REVIEWED', 'note': 'Liquidity.'}],
            'ambiguities': ['PRIVATE_REVIEW_QUESTION']}
        raw = json.dumps(output).encode()
        unreviewed = t.handoff(raw, self.ctx, self.extraction, self.mapping, 'PROMPT')
        atom = unreviewed['admission_packet']['candidates'][0]
        review = {**self.review, **t.review_binding(atom, unreviewed['admission_packet'])}
        result = t.handoff(raw, self.ctx, self.extraction, self.mapping, 'PROMPT', reviews={'a': review})
        self.assertEqual(bytes.fromhex(result['original_response_hex']), raw)
        self.assertEqual(len(result['trusted_package']['claims']), 1)
        self.assertNotIn('UNVERIFIED_REPORT_MUST_NOT_LEAK', json.dumps(result['reasoning_input']))
        self.assertNotIn('PRIVATE_REVIEW_QUESTION', json.dumps(result['reasoning_input']))

    def test_model_cannot_supply_own_review_or_incomplete_response(self):
        raw = json.dumps({'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]}).encode()
        with self.assertRaises(ValueError):
            t.handoff(raw, self.ctx, self.extraction, self.mapping, 'P')
        candidate = deepcopy(self.candidate)
        candidate['decision'] = 'SUPPORTED'
        _, packet, invalid = t.propose([candidate], self.ctx, self.extraction, self.mapping, 'b'*64)
        self.assertEqual(packet['candidates'], [])
        self.assertEqual(len(invalid), 1)

    @retained_source_audit
    def test_saved_gna_regressions(self):
        cases = json.loads((ART/'offline/case-results.json').read_text())
        self.assertEqual(len(cases), 16)
        self.assertTrue(all(c['actual'] == c['expected'] for c in cases))
        self.assertTrue(all(c['structural_locators_valid'] for c in cases))
        summary = json.loads((ART/'offline/summary.json').read_text())
        self.assertEqual(summary['automatic_semantic_admissions'], 0)
        self.assertEqual(summary['admitted_claims'], 6)

    @retained_source_audit
    def test_offline_cli_on_frozen_future_source_with_empty_synthetic_response(self):
        source = ART/'future/source'
        sidecar = json.loads((source/'sidecar.json').read_text())
        # Synthetic contract smoke only; never recorded as a model discovery.
        output = {'report': 'Synthetic empty-evidence contract smoke.', 'claim_ledger': [],
            'coverage_ledger': [{'region_id': r['id'], 'claim_ids': [], 'status': 'UNRESOLVED',
                                'note': 'Synthetic test; no material review performed.'}
                               for r in sidecar['compact_regions']], 'ambiguities': []}
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)/'synthetic.json'
            raw.write_text(json.dumps(output))
            dest = Path(tmp)/'result'
            result = subprocess.run([sys.executable, '-B', '-m',
                'scrapper.research.document_intelligence.credit_two_stage', '--source', str(source),
                '--response', str(raw), '--prompt', str(ART/'prompts/stage2.md'), '--output', str(dest)],
                capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((dest/'response.raw.json').read_bytes(), raw.read_bytes())
            reasoning = json.loads((dest/'reasoning_input.json').read_text())
            self.assertEqual(reasoning['assessment_mode'], 'INSUFFICIENT_EVIDENCE')
            self.assertNotIn('Synthetic empty-evidence contract smoke.', json.dumps(reasoning))


if __name__ == '__main__':
    unittest.main()
