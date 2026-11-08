"""Bounded Phase 3 source, period, qualification and replay regressions."""
from scrapper.tests.retained_audits import retained_source_audit

import copy
import socket
import sqlite3
import unittest
from unittest.mock import patch
from pathlib import Path
from scrapper.research.document_intelligence import credit_v1 as v
from scrapper.research.document_intelligence.credit_atomic import derive_claims
from scrapper.research.document_intelligence.schema import DocumentContext

ROOT=Path(__file__).resolve().parents[2]
D=ROOT/'docs/credit-rating-intelligence-v1'

@retained_source_audit
class Phase3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records={co:v.build_record(D/f'{co}-phase3-manifest.json') for co in ['ekc','vimta']}
        cls.atoms={co:{c['atomic_key']:c for c in r['claims'] if 'atomic_key' in c} for co,r in cls.records.items()}

    def test_periods_and_inventory_comparisons(self):
        a=self.atoms['ekc']
        for key,value,period in [('inventory-fy24','186','FY24'),('inventory-fy23','163','FY23'),('cycle-fy24','221','FY24'),('cycle-fy23','198','FY23')]:
            c=a['A15.'+key]; self.assertEqual((c['value'],c['unit'],c['period']),(value,'days',period))
        for key,value,period in [('cycle-fy23','99','FY23'),('cycle-fy21','122','FY21')]:
            c=self.atoms['vimta']['A24.'+key]; self.assertEqual((c['value'],c['period']),(value,period))
        self.assertIn('end-FY21',self.atoms['vimta']['A24.debtor-explanation']['statement'])
        self.assertEqual(a['A15.inventory-policy']['unit'],'months')
        self.assertIsNone(a['A15.raw-material-cost']['period'])

    def test_scope_is_local(self):
        for co in self.atoms:
            for key,c in self.atoms[co].items():
                if key!='A18.capex-assumption':
                    self.assertIsNone(c['accounting_scope'])
                    self.assertEqual(c['field_completeness']['accounting_scope'],'UNRESOLVED')
        self.assertEqual(self.atoms['vimta']['A18.capex-assumption']['accounting_scope'],'consolidated')

    def test_capex_history_funding_and_assumptions(self):
        a=self.atoms['ekc']
        self.assertEqual(a['A13.capex-estimate']['value'],'~307.00')
        self.assertIsNone(a['A13.capex-estimate']['period'])
        self.assertIn('not completed',a['A13.capex-estimate']['qualifications'][0])
        self.assertEqual(a['A13.bank-funding']['period'],'FY25-FY26')
        self.assertIn('nil as on March 31, 2024',a['A13.net-debt-expectation']['evidence'][0]['quote'])
        self.assertNotIn('A13.net-debt-baseline',a)
        self.assertEqual(a['A13.net-debt-expectation']['period_kind'],'expectation_horizon')
        self.assertEqual(a['A13.egypt-commercialisation']['period'],'by FY26')
        c=self.atoms['vimta']['A18.capex-assumption']
        self.assertIn('absence of any significant debt-funded capex',c['conditions'][0])
        self.assertIn('not a company capex plan',c['qualifications'][0])
        self.assertEqual(c['derivation_basis'],'source_review_addition')

    def test_forex_hedging_attribution_and_multi_passage(self):
        a=self.atoms['ekc']; c=a['A15.forex-risk']
        self.assertEqual(c['attribution'],'CARE Ratings')
        self.assertIn('US Dollar/Indian rupee',c['conditions'][0])
        self.assertIn('selectively',a['A15.selective-hedging']['statement'])
        self.assertEqual(a['A15.selective-hedging']['derivation_basis'],'source_review_addition')
        self.assertEqual([e['page'] for e in a['A13.egypt-country-risk']['evidence']],[2,3])
        for co,r in self.records.items():
            m=v.read_json(D/f'{co}-phase3-manifest.json'); ext=v.read_json(ROOT/m['canonical']['path'])
            for c in self.atoms[co].values():
                for ev in c['evidence']:
                    self.assertEqual(ext['pages'][ev['page']-1]['selected_text'][ev['start']:ev['end']],ev['quote'])

    def test_invalid_locators_changed_hashes_and_reviews(self):
        for co in self.records:
            m=v.read_json(D/f'{co}-phase3-manifest.json'); packet=v.read_json(ROOT/m['atomic_candidates']['path']); ext=v.read_json(ROOT/m['canonical']['path'])
            baseline=v.build_record(D/f'{co}-manifest.json'); parents=baseline['claims']+baseline['evidence_review_queue']; ctx=DocumentContext(**m['context'])
            def derive(p): return derive_claims(p,parents,ctx,ext)[-1]['evidence_decision']
            for field,val in [('accounting_scope','unsupported scope'),('period','FY99'),('conditions',['tampered qualification']),('parent_candidate_sha256','0'*64)]:
                p=copy.deepcopy(packet); p['candidates'][-1][field]=val
                self.assertEqual(derive(p),'WITHHELD')
            p=copy.deepcopy(packet); a=p['candidates'][-1]; a['support'][0]['page']=999
            p['reviews'][a['atomic_key']]['candidate_sha256']=v.canonical_hash(a)
            self.assertEqual(derive(p),'WITHHELD')
            p=copy.deepcopy(packet); p['reviews']={}; self.assertEqual(derive(p),'NEEDS_REVIEW')
            changed=copy.deepcopy(parents)
            for parent in changed:
                if parent['claim_id']==packet['candidates'][-1]['parent_claim_id']: parent['original_candidate']['statement']='changed'
            self.assertEqual(derive_claims(packet,changed,ctx,ext)[-1]['evidence_decision'],'WITHHELD')

    def test_duplicate_coverage_and_parent_suppression(self):
        child=copy.deepcopy(self.atoms['vimta']['A24.cycle-fy23']); field=child['fields'][0]
        parent=next(c for c in self.records['vimta']['evidence_review_queue'] if c['claim_id']==child['origin']['parent_claim_id'])
        parent=copy.deepcopy(parent); parent['fields']=[field]; parent['evidence_decision']='SUPPORTED'
        duplicate=copy.deepcopy(child); duplicate['claim_id']='duplicate'; duplicate['atomic_key']='other-key'
        self.assertEqual(v.coverage_claim_ids([parent,child,duplicate],field),[child['claim_id']])
        parent['fields'].append('assessment.revenue_explanations')
        self.assertEqual(v.coverage_claim_ids([parent,child],'assessment.revenue_explanations'),[parent['claim_id']])

    def test_replay_preserves_baselines_offline(self):
        for co,r in self.records.items():
            before={x['path']:v.digest(ROOT/x['path']) for x in r['provenance']['inputs']}
            baseline=v.build_record(D/('ekc-phase2-manifest.json' if co=='ekc' else 'vimta-manifest.json'))
            with patch.object(socket.socket,'connect',side_effect=AssertionError('network')),patch.object(sqlite3,'connect',side_effect=AssertionError('db')),patch.object(v,'extract_local',side_effect=AssertionError('fresh extraction')):
                self.assertEqual(v.build_record(D/f'{co}-phase3-manifest.json'),r)
            self.assertEqual(baseline['claims'],r['claims'][:len(baseline['claims'])])
            self.assertEqual(baseline['evidence_review_queue'],r['evidence_review_queue'])
            self.assertEqual(before,{p:v.digest(ROOT/p) for p in before})
            self.assertEqual(r['execution']['inference_calls'],0)
            self.assertTrue(all(x['completeness']=='PARTIAL' for x in r['coverage_ledger'].values() if x['state']=='DISCLOSED'))

if __name__=='__main__': unittest.main()
