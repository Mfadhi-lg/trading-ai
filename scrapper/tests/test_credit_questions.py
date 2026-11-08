"""Offline retrieval and admission regressions; no expected answers enter prompts."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scrapper.research.document_intelligence import credit_questions as q
from scrapper.research.document_intelligence import credit_two_stage as gate

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'artifacts/credit-rating-intelligence-v1'
SOURCE=BASE/'two-stage-v1/future/source'
PREP=BASE/'question-driven-v1/prepared'
LEGACY=BASE/'two-stage-balkrishna-stage1'


def passage(rid,text,page=1,prev=None):
    return dict(id=rid,kind='passage',heading='Report context',page=page,text=text,continues_from=prev)


class RetrievalTests(unittest.TestCase):
    def retrieve(self,regions):
        return q.retrieve(dict(source_sha256='s',canonical_sha256='c',compact_regions=regions))

    def test_all_matches_across_sections_not_first(self):
        r=self.retrieve([passage('a','Liquidity depends on projected cash accruals.'),
            passage('b','Unrelated description.'),passage('c','Notes.'),
            passage('d','Bank limits available subject to covenants.',4)])
        self.assertIn('d',{x['id'] for x in r['questions']['Q7']['selected']})
        self.assertEqual(len(r['questions']['Q7']['selection_log']),4)

    def test_tariff_qualifications_across_pages_and_recursive_links(self):
        r=self.retrieve([passage('a','Tariff proposal announced.'),
            passage('b','Not yet effective.',2,'a'),passage('c','Subject to policy changes.',3,'b')])
        self.assertEqual({x['id'] for x in r['questions']['Q9']['selected']},{'a','b','c'})

    def test_integration_routes_strength_performance_and_growth(self):
        r=self.retrieve([passage('a','Backward integration reduces input and transportation costs; supports margins.')])
        for question in ['Q3','Q4','Q8']:
            self.assertEqual(r['questions'][question]['selected'][0]['id'],'a')

    def test_history_governance_and_limitations_outside_main_rationale(self):
        r=self.retrieve([passage('a','Annexure: rating history withdrawn.'),
            passage('b','Governance: independent directors.'),passage('c','Agency does not conduct an audit.')])
        self.assertIn('a',{x['id'] for x in r['questions']['Q1']['selected']})
        self.assertIn('b',{x['id'] for x in r['questions']['Q9']['selected']})
        self.assertIn('c',{x['id'] for x in r['questions']['Q10']['selected']})

    def test_table_headers_units_and_all_rows_preserved(self):
        rows=[['Measure (crore)','FY24 audited','FY25 unaudited'],['Income','10','11']]
        r=self.retrieve([passage('a','Financial performance.'),
            dict(id='t',kind='table',page=1,heading='Table',rows=rows,continues_from=None)])
        self.assertEqual(next(x['rows'] for x in r['questions']['Q4']['selected'] if x['id']=='t'),rows)

    def test_table_only_match_keeps_unmatched_footnote(self):
        r=self.retrieve([passage('a','Subject to approval, not yet effective.'),
            dict(id='t',kind='table',page=1,heading='Table',rows=[['Capex','Proposed']],continues_from=None)])
        self.assertIn('a',{x['id'] for x in r['questions']['Q8']['selected']})

    def test_missing_retrieval_is_unresolved(self):
        r=self.retrieve([passage('a','Unrelated text.')])['questions']['Q6']
        self.assertEqual(r['status'],'UNRESOLVED')
        self.assertFalse(r['absence_established'])
        self.assertFalse(r['selection_log'][0]['selected'])


class SourceAndReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx,cls.extraction,cls.sidecar=q.load_source(SOURCE/'original.pdf',SOURCE)
        cls.retrieval=q.retrieve(cls.sidecar)
        cls.saved=json.loads((LEGACY/'evaluation/understanding-parsed.json').read_bytes())

    def envelope(self,qids=('Q1',),candidate=None):
        c=deepcopy(candidate or self.saved['claim_ledger'][0])
        return dict(answers=[dict(question_id=qn,status='PARTIAL',answer=[dict(text=c['statement'],claim_ids=[c['id']])],
            claims=[c],classifications=[dict(claim_id=c['id'],kind='AGENCY ASSESSMENT')],numerical_claim_ids=[],
            qualifications=[],evidence_gaps=['Synthetic contract test, not a new model answer.']) for qn in qids],uncaptured_disclosures=[])

    def intake(self,body,reviews=None):
        return q.replay(json.dumps(body).encode(),('Q1',),self.ctx,self.extraction,self.sidecar,self.retrieval,reviews)

    def test_all_real_source_ids_resolve_and_freeze_replays(self):
        q.verify_freeze(PREP)
        for question in self.retrieval['questions'].values():
            for r in question['selected']:
                original=next(x for x in self.sidecar['compact_regions'] if x['id']==r['id'])
                self.assertTrue(q.v.attach_evidence(self.ctx,self.extraction,original['evidence']))

    def test_known_failure_categories_present_in_real_retrieval(self):
        checks={'Q1':['23-May-25','03-Jul-','04-Jul-','06-Jul-'],
            'Q3':['transportation costs'], 'Q4':['13%','Total operating income','PBILDT'],
            'Q7':['next four quarters','71%','January 2026'],
            'Q8':['mix of internal accruals and debt','phased manner'],
            'Q9':['not yet effective','temporary 10%','50% of its board'],
            'Q10':['independent verification','commercial transactions','rating-related trigger']}
        for question,terms in checks.items():
            text='\n'.join(q.region_text(r) for r in self.retrieval['questions'][question]['selected'])
            for term in terms:
                self.assertIn(term,text,(question,term))

    def test_tampered_passage_and_source_identity_rejected(self):
        s=deepcopy(self.sidecar);s['compact_regions'][0]['text']+='tamper'
        with self.assertRaises(ValueError): q.validate_source(self.ctx,self.extraction,s)
        s=deepcopy(self.sidecar);s['source_sha256']='0'*64
        with self.assertRaises(ValueError): q.validate_source(self.ctx,self.extraction,s)

    def test_valid_ids_never_automatically_admitted(self):
        r=self.intake(self.envelope())
        self.assertEqual(r['status'],'REPLAYED',r.get('error'))
        self.assertEqual(r['claims'][0]['evidence_decision'],'NEEDS_REVIEW')
        self.assertEqual(r['trusted_record']['claims'],[])

    def test_explicit_review_and_stale_review_gate(self):
        b=self.envelope();r=self.intake(b);atom=r['admission_packet']['candidates'][0]
        reason='Test-only explicit adjudication; not retained as real source certification.'
        review={**gate.review_binding(atom,r['admission_packet']), 'decision':'SUPPORTED',
            'reviewer':'unit-test','reviewed_at':'2026-10-25','method':'ai_source_review_provisional','reason':reason,
            'checks':{d:dict(verdict='PASS',basis=reason) for d in gate.DIMENSIONS}}
        admitted=self.intake(b,{atom['atomic_key']:review})
        self.assertEqual(len(admitted['trusted_record']['claims']),1)
        b['answers'][0]['claims'][0]['statement']+=' Invented qualification.'
        stale=self.intake(b,{atom['atomic_key']:review})
        self.assertEqual(stale['trusted_record']['claims'],[])
        self.assertEqual(stale['claims'][0]['evidence_decision'],'WITHHELD')

    def test_failed_semantic_dimension_never_admitted(self):
        b=self.envelope();r=self.intake(b);atom=r['admission_packet']['candidates'][0]
        rev={**gate.review_binding(atom,r['admission_packet']),'decision':'SUPPORTED','reviewer':'test','reviewed_at':'2026-10-25',
            'method':'ai_source_review_provisional','reason':'Scope unresolved',
            'checks':{d:dict(verdict='PASS',basis='test') for d in gate.DIMENSIONS}}
        rev['checks']['accounting_scope']=dict(verdict='UNRESOLVED',basis='Local scope not established')
        self.assertFalse(self.intake(b,{atom['atomic_key']:rev})['trusted_record']['claims'])

    def test_inference_classification_bound_to_review(self):
        b=self.envelope();b['answers'][0]['classifications'][0]['kind']='ANALYTICAL INFERENCE'
        r=self.intake(b)
        self.assertEqual(r['claims'][0]['research_classification'],'ANALYTICAL INFERENCE')
        self.assertEqual(r['admission_packet']['candidates'][0]['research_classification'],'ANALYTICAL INFERENCE')
        self.assertFalse(r['trusted_record']['claims'])

    def test_unknown_id_and_uncited_prose_withheld(self):
        b=self.envelope();b['answers'][0]['claims'][0]['support_region_ids']=['nonexistent']
        self.assertEqual(self.intake(b)['status'],'WITHHELD')
        b=self.envelope();b['answers'][0]['answer'][0]['claim_ids']=[]
        self.assertEqual(self.intake(b)['status'],'WITHHELD')

    def test_invalid_json_and_legacy_response_retained_withheld(self):
        for raw in [b'not-json',(LEGACY/'live/response.raw.json').read_bytes()]:
            r=q.replay(raw,('Q1',),self.ctx,self.extraction,self.sidecar,self.retrieval)
            self.assertEqual(r['status'],'WITHHELD')
            self.assertEqual(bytes.fromhex(r['raw_response_hex']),raw)

    def test_duplicate_question_and_truncation_rejected(self):
        b=self.envelope();b['answers'].append(deepcopy(b['answers'][0]))
        self.assertEqual(self.intake(b)['status'],'WITHHELD')
        b={'choices':[{'finish_reason':'length','message':{'content':json.dumps(self.envelope())}}]}
        self.assertEqual(self.intake(b)['status'],'WITHHELD')

    def test_numerical_mismatch_is_flagged_not_semantic_pass(self):
        b=self.envelope();b['answers'][0]['claims'][0]['statement']+=' Value 999999.99 crore.'
        r=self.intake(b)
        self.assertTrue(r['deterministic_observations'][0]['numerical_tokens_requiring_review'])
        self.assertFalse(r['trusted_record']['claims'])

    def test_payload_constraints_and_cost_arithmetic(self):
        payloads,cost=q.freeze_requests(self.ctx,self.retrieval)
        self.assertEqual(len(payloads),4)
        for p,row in zip(payloads.values(),cost['requests']):
            self.assertEqual(p['provider']['only'],['google-ai-studio/flex'])
            self.assertFalse(p['provider']['allow_fallbacks'])
            self.assertEqual(p['reasoning']['effort'],'low')
            self.assertEqual(row['maximum_cost_reservation_usd'],round(((row['wire_bytes']+2048)*.375+p['max_tokens']*1.875)/1e6,9))
        self.assertEqual(cost['inference_calls'],0)


if __name__=='__main__': unittest.main()
