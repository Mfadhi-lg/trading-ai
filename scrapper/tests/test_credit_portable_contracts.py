"""Portable counterparts to retained source audits: invented evidence only."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scrapper.tests.credit_test_sources import create_source
from scrapper.tests import test_credit_questions as question_tests
from scrapper.research.document_intelligence import credit_questions as q
from scrapper.research.document_intelligence import credit_v1 as v
from scrapper.research.document_intelligence.credit_atomic import derive_claims


class PortableQuestionReplayTests(question_tests.SourceAndReplayTests):
    # The parent class is the opt-in real-source audit. Its generic admission
    # regressions are executed here against a freshly generated synthetic PDF.
    __unittest_skip__ = False

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.source = Path(cls.tmp.name) / 'source'
        cls.ctx, cls.extraction, cls.sidecar = create_source(cls.source)
        cls.retrieval = q.retrieve(cls.sidecar)
        region = cls.retrieval['questions']['Q1']['selected'][0]
        cls.saved = {'claim_ledger': [{'id': 'synthetic-claim', 'statement': region['text'],
            'field': 'liquidity.assessment', 'disclosure_category': 'liquidity',
            'value': None, 'unit': None, 'period': None, 'period_kind': 'unknown',
            'accounting_scope': None, 'attribution': 'Synthetic agency',
            'statement_kind': 'assessment', 'conditions': [], 'qualifications': [],
            'support_region_ids': [region['id']]}]}

    def test_all_real_source_ids_resolve_and_freeze_replays(self):
        prepared = Path(self.tmp.name) / 'prepared'
        if prepared.exists():
            q.verify_freeze(prepared)
        else:
            q.prepare(self.source / 'original.pdf', self.source, prepared)
            q.verify_freeze(prepared)
        for question in self.retrieval['questions'].values():
            for region in question['selected']:
                original = next(r for r in self.sidecar['compact_regions'] if r['id'] == region['id'])
                self.assertTrue(v.attach_evidence(self.ctx, self.extraction, original['evidence']))

    def test_known_failure_categories_present_in_real_retrieval(self):
        checks = {'Q4': ['Revenue rose', 'volumes increased'], 'Q7': ['sanctioned limits', 'drawing power'], 'Q8': ['internal accruals'], 'Q10': ['does not conduct an audit']}
        for question, terms in checks.items():
            text = '\n'.join(q.region_text(r) for r in self.retrieval['questions'][question]['selected'])
            for term in terms:
                self.assertIn(term, text, (question, term))

    def test_invalid_json_and_legacy_response_retained_withheld(self):
        for raw in [b'not-json', b'{"report":"Synthetic obsolete answer envelope"}']:
            result = q.replay(raw, ('Q1',), self.ctx, self.extraction, self.sidecar, self.retrieval)
            self.assertEqual(result['status'], 'WITHHELD')
            self.assertEqual(bytes.fromhex(result['raw_response_hex']), raw)


class PortableSourceRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ctx, self.ext, self.sidecar = create_source(self.root)

    def build(self):
        return v.build_record(self.root / 'manifest.json', root=self.root)

    def test_original_pdf_canonical_and_inventory_hashes_fail_closed(self):
        for name in ('original.pdf', 'canonical.json', 'inventory.json', 'reviews.json'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                try:
                    with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                        self.build()
                finally:
                    path.write_bytes(original)

    def test_record_replay_preserves_inputs_and_runs_offline(self):
        before = {p.name: v.digest(p) for p in self.root.iterdir()}
        first = self.build()
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('network')), patch.object(sqlite3, 'connect', side_effect=AssertionError('database')), patch.object(v, 'extract_local', side_effect=AssertionError('fresh extraction')):
            self.assertEqual(self.build(), first)
        self.assertEqual(before, {p.name: v.digest(p) for p in self.root.iterdir()})
        self.assertEqual(first['execution']['inference_calls'], 0)
        self.assertEqual(first['source_coverage']['physical_pages'], 2)
        self.assertTrue(all(c['evidence_decision'] == 'SUPPORTED' for c in first['claims']))
        self.assertFalse(first['source_coverage']['full_document_semantic_review'])

    def test_cover_identity_and_page_coverage_are_required(self):
        for mutate, message in [(lambda ext: ext['pages'].pop(), 'page coverage'), (lambda ext: ext['pages'][0].update(selected_text='Other issuer'), 'Company identity')]:
            manifest = json.loads((self.root / 'manifest.json').read_text())
            changed = deepcopy(self.ext)
            mutate(changed)
            (self.root / 'changed.json').write_text(json.dumps(changed))
            manifest['canonical'] = {'path': 'changed.json', 'sha256': v.digest(self.root / 'changed.json')}
            (self.root / 'changed-manifest.json').write_text(json.dumps(manifest))
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                v.build_record(self.root / 'changed-manifest.json', root=self.root)

    def test_two_stage_cli_retains_synthetic_empty_response_without_admission(self):
        response = {'report': 'Synthetic empty-evidence contract smoke.', 'claim_ledger': [], 'coverage_ledger': [{'region_id': r['id'], 'claim_ids': [], 'status': 'UNRESOLVED', 'note': 'Synthetic source; no material review.'} for r in self.sidecar['compact_regions']], 'ambiguities': []}
        raw = self.root / 'response.json'
        raw.write_text(json.dumps(response))
        prompt = self.root / 'prompt.md'
        prompt.write_text('Synthetic offline test; no inference request authorized.')
        output = self.root / 'result'
        result = subprocess.run([sys.executable, '-B', '-m', 'scrapper.research.document_intelligence.credit_two_stage', '--source', str(self.root), '--response', str(raw), '--prompt', str(prompt), '--output', str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((output / 'response.raw.json').read_bytes(), raw.read_bytes())
        reasoning = json.loads((output / 'reasoning_input.json').read_text())
        self.assertEqual(reasoning['assessment_mode'], 'INSUFFICIENT_EVIDENCE')
        self.assertNotIn(response['report'], json.dumps(reasoning))


class PortableAtomicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx, self.ext, _ = create_source(self.tmp.name)
        self.parent = {'claim_id': 'synthetic-parent', 'original_candidate': {'statement': 'Synthetic retained proposal'}}
        page = self.ext['pages'][1]
        quote = 'Agency expects capex of Rs. 30 crore in FY2027, only if internal accruals suffice.'
        self.atom = {'atomic_key': 'synthetic.capex', 'parent_claim_id': self.parent['claim_id'], 'parent_candidate_sha256': v.canonical_hash(self.parent['original_candidate']), 'statement': quote, 'value': '30', 'unit': 'Rs. crore', 'period': 'FY2027', 'period_kind': 'expectation_horizon', 'accounting_scope': None, 'attribution': 'Synthetic agency', 'statement_kind': 'agency_assessment', 'qualifications': [], 'conditions': ['only if internal accruals suffice'], 'derivation_basis': 'source_review_addition', 'fields': ['capex.plans'], 'support': [v.text_evidence(page, quote)]}
        # Bind to the canonical field name rather than an invented taxonomy.
        self.atom['fields'] = [next(f for f in v.FIELD_NAMES if 'capex' in f)]
        self.packet = {'source_sha256': self.ctx.content_sha256, 'canonical_sha256': v.canonical_hash(self.ext), 'candidates': [self.atom], 'reviews': {self.atom['atomic_key']: {'candidate_sha256': v.canonical_hash(self.atom), 'decision': 'SUPPORTED', 'reason': 'Synthetic test-only source review'}}}

    def derive(self, packet=None, ext=None):
        return derive_claims(packet or self.packet, [self.parent], self.ctx, ext or self.ext)

    def test_period_unit_scope_and_conditions_remain_local(self):
        claim = self.derive()[0]
        self.assertEqual(claim['evidence_decision'], 'SUPPORTED')
        self.assertEqual((claim['value'], claim['unit'], claim['period']), ('30', 'Rs. crore', 'FY2027'))
        self.assertIsNone(claim['accounting_scope'])
        self.assertEqual(claim['field_completeness']['accounting_scope'], 'UNRESOLVED')
        self.assertEqual(claim['conditions'], self.atom['conditions'])
        self.assertEqual(claim['evidence'][0]['quote'], self.atom['statement'])

    def test_every_material_dimension_and_locator_is_review_bound(self):
        for field, value in [('value', '999'), ('period', 'FY2099'), ('accounting_scope', 'consolidated'), ('conditions', []), ('qualifications', ['invented'])]:
            packet = deepcopy(self.packet)
            packet['candidates'][0][field] = value
            with self.subTest(field=field):
                self.assertEqual(self.derive(packet)[0]['evidence_decision'], 'WITHHELD')
        packet = deepcopy(self.packet)
        packet['candidates'][0]['support'][0]['start'] += 1
        self.assertEqual(self.derive(packet)[0]['evidence_decision'], 'WITHHELD')

    def test_unreviewed_changed_parent_source_and_duplicate_rejected(self):
        packet = deepcopy(self.packet)
        packet['reviews'] = {}
        self.assertEqual(self.derive(packet)[0]['evidence_decision'], 'NEEDS_REVIEW')
        for key in ('source_sha256', 'canonical_sha256'):
            packet = deepcopy(self.packet)
            packet[key] = '0' * 64
            self.assertEqual(self.derive(packet)[0]['evidence_decision'], 'WITHHELD')
        self.parent['original_candidate']['statement'] = 'changed'
        self.assertEqual(self.derive()[0]['evidence_decision'], 'WITHHELD')
        packet = deepcopy(self.packet)
        packet['candidates'].append(deepcopy(self.atom))
        with self.assertRaisesRegex(ValueError, 'Duplicate atomic key'):
            self.derive(packet)

    def test_multiple_passages_remain_ordered_and_wholly_review_bound(self):
        packet = deepcopy(self.packet)
        second = self.ext['pages'][0]
        packet['candidates'][0]['support'].append(v.text_evidence(second, 'Synthetic Limited'))
        packet['reviews'][self.atom['atomic_key']]['candidate_sha256'] = v.canonical_hash(packet['candidates'][0])
        claim = self.derive(packet)[0]
        self.assertEqual(claim['evidence_decision'], 'SUPPORTED')
        self.assertEqual([e['page'] for e in claim['evidence']], [2, 1])
        packet['candidates'][0]['support'].pop()
        self.assertEqual(self.derive(packet)[0]['evidence_decision'], 'WITHHELD')

    def test_duplicate_child_suppresses_only_covered_parent_field(self):
        child = self.derive()[0]
        field = child['fields'][0]
        parent = {'claim_id': child['origin']['parent_claim_id'], 'fields': [field, 'assessment.revenue_explanations'], 'evidence_decision': 'SUPPORTED'}
        duplicate = deepcopy(child)
        duplicate['claim_id'] = 'synthetic-duplicate'
        duplicate['atomic_key'] = 'synthetic-duplicate'
        self.assertEqual(v.coverage_claim_ids([parent, child, duplicate], field), [child['claim_id']])
        self.assertEqual(v.coverage_claim_ids([parent, child], 'assessment.revenue_explanations'), [parent['claim_id']])
