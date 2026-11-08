"""Atomic admission and EKC liquidity regression, using only retained local sources."""
from scrapper.tests.retained_audits import retained_source_audit

import copy
import json
import socket
import sqlite3
import unittest
from unittest.mock import patch
from pathlib import Path
from scrapper.research.document_intelligence import credit_v1 as v
from scrapper.research.document_intelligence.credit_atomic import derive_claims
from scrapper.research.document_intelligence.schema import DocumentContext

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / 'docs/credit-rating-intelligence-v1'

@retained_source_audit
class AtomicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = v.read_json(DEV/'ekc-phase2-manifest.json')
        cls.packet = v.read_json(ROOT/cls.manifest['atomic_candidates']['path'])
        cls.extraction = v.read_json(ROOT/cls.manifest['canonical']['path'])
        cls.context = DocumentContext(**cls.manifest['context'])
        cls.baseline = v.build_record(DEV/'ekc-manifest.json')
        cls.parents = cls.baseline['claims'] + cls.baseline['evidence_review_queue']
        cls.record = v.build_record(DEV/'ekc-phase2-manifest.json')
        cls.atoms = {c['atomic_key']:c for c in cls.record['claims'] if 'atomic_key' in c}

    def derive(self, packet):
        return derive_claims(packet, self.parents, self.context, self.extraction)

    def test_independent_periods_and_units(self):
        a=self.atoms
        self.assertEqual(a['A16.cash']['period'],'March 31, 2024')
        self.assertEqual(a['A16.utilisation']['period'],'last 12 months ended June 30, 2024')
        self.assertEqual(a['A16.accruals']['period'],'medium term')
        self.assertEqual(a['A16.accruals']['value'],'above 130')
        self.assertEqual(a['A16.cash']['unit'],'₹ crore')
        self.assertEqual(a['A16.utilisation']['unit'],'%')
        self.assertIsNone(a['A16.liquidity-rationale']['period'])

    def test_current_future_and_conditions(self):
        a=self.atoms
        self.assertEqual(a['A16.current-repayments']['period'],'currently')
        self.assertEqual(a['A16.future-repayments']['period'],'FY26-FY27 onwards')
        self.assertIn('FY26-FY27',a['A16.current-repayments']['conditions'][0])
        self.assertEqual(a['A16.future-repayments']['derivation_basis'],'source_review_addition')
        self.assertIn('non-fund-based',a['A16.utilisation']['qualifications'][0])
        self.assertIn('inventory',a['A16.liquidity-rationale']['conditions'][0])

    def test_unknown_scope_and_original_preserved(self):
        for a in self.atoms.values():
            self.assertIsNone(a['accounting_scope'])
            self.assertEqual(a['field_completeness']['accounting_scope'],'UNRESOLVED')
        self.assertEqual(self.baseline['evidence_review_queue'],self.record['evidence_review_queue'])
        self.assertEqual(len(self.baseline['claims']),17)
        self.assertEqual(len(self.record['claims']),23)
        self.assertEqual(self.record['claims'][:17],self.baseline['claims'])

    def test_exact_paragraph_not_page_or_model_region(self):
        for a in self.atoms.values():
            e=a['evidence'][0]; p=self.extraction['pages'][2]['selected_text']
            self.assertEqual(e['page'],3)
            self.assertEqual(p[e['start']:e['end']],e['quote'])
            self.assertTrue(e['quote'].startswith('Liquidity: Adequate'))
            self.assertNotIn('Applicable criteria',e['quote'])
            self.assertLess(len(e['quote']),len(p))
            self.assertTrue(e['region_id'].startswith('p3:eNone:'))
            self.assertEqual(e['source_document_sha256'],self.context.content_sha256)

    def test_unreviewed_and_unsupported_never_promoted(self):
        p=copy.deepcopy(self.packet); p['reviews']={}
        self.assertTrue(all(c['evidence_decision']=='NEEDS_REVIEW' for c in self.derive(p)))
        for field,value in [('value','999'),('accounting_scope','consolidated'),('period','FY99'),('conditions',[])]:
            p=copy.deepcopy(self.packet); index = 3 if field == 'conditions' else 0; p['candidates'][index][field]=value
            self.assertEqual(self.derive(p)[index]['evidence_decision'],'WITHHELD')
        p=copy.deepcopy(self.packet); p['candidates'][0]['support'][0]['start']=0
        self.assertEqual(self.derive(p)[0]['evidence_decision'],'WITHHELD')
        for field in ['source_sha256','canonical_sha256']:
            p=copy.deepcopy(self.packet); p[field]='0'*64
            self.assertTrue(all(c['evidence_decision']=='WITHHELD' for c in self.derive(p)))

    def test_multiple_passages_preserved_and_pinned(self):
        p=copy.deepcopy(self.packet); a=p['candidates'][0]
        page=self.extraction['pages'][0]
        a['support'].append(v.text_evidence(page,'Press Release'))
        # A test-only review validates transport, not the new passage's semantics.
        p['reviews'][a['atomic_key']]['candidate_sha256']=v.canonical_hash(a)
        c=self.derive(p)[0]; self.assertEqual(len(c['evidence']),2)
        self.assertEqual([e['page'] for e in c['evidence']],[3,1])
        a['support'].pop()
        self.assertEqual(self.derive(p)[0]['evidence_decision'],'WITHHELD')

    def test_determinism_offline_and_input_preservation(self):
        paths=[ROOT/d['path'] for d in self.record['provenance']['inputs']]
        before=[v.digest(p) for p in paths]
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network')), patch.object(sqlite3,'connect',side_effect=AssertionError('database')), patch.object(v,'extract_local',side_effect=AssertionError('fresh extraction')):
            again=v.build_record(DEV/'ekc-phase2-manifest.json')
        self.assertEqual(again,self.record)
        self.assertEqual(before,[v.digest(p) for p in paths])
        self.assertEqual(again['execution']['inference_calls'],0)
        self.assertEqual(len(v.build_record(DEV/'vimta-manifest.json')['claims']),27)
        self.assertTrue(all(v['completeness']=='PARTIAL' for v in again['groups']['liquidity'].values()))

if __name__=='__main__': unittest.main()
