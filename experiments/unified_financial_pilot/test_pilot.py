"""Synthetic admission/budget contracts plus integration with the real D03 parser."""
from dataclasses import replace
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from .provider import Client, save, http,decode_content
from .schema import Evidence,validate
from .runner import make_packet,ingest_model,run,inventory
from .evaluate import reviewed
from scrapper.research.document_intelligence.offline import extract_local,deny_network

SHA='a'*64


def evidence(**kw):
    args=dict(company_id='fixture',document_id='fixture-doc',document_type='credit_rating',
        source_filename='fixture.pdf',source_sha256=SHA,physical_page=1,category='Financial resilience',
        metric='revenue',statement='Revenue 10 crore FY25 standalone',quote='Revenue 10 crore FY25 standalone',
        reported_value='10',unit='INR',scale='crore',period='FY25',scope='standalone',statement_kind='reported_fact')
    args.update(kw);return Evidence(**args)


def review():
    return dict(reviewer='Independent fixture reviewer',reviewed_at='2026-10-20',independent=True,
        **{k:'correct' for k in ('value','unit','period','scope','column_association','attribution','qualifications','rating_action_status')})


class ContractTests(unittest.TestCase):
    def test_fenced_json_envelope_without_content_repair(self):
        self.assertEqual(decode_content('```json\n{"evidence":[]}\n```'),{'evidence':[]})
        with self.assertRaises(ValueError):decode_content('```json\n{"evidence":[')
        with self.assertRaises(ValueError):decode_content('Explanation\n{"evidence":[]}')
    def test_quote_alone_never_admits(self):
        e=validate(evidence(),{1:evidence().quote},SHA)
        self.assertTrue(e.anchor_verified);self.assertEqual(e.validation_status,'needs_review')

    def test_independent_review_must_be_complete(self):
        self.assertFalse(reviewed({}))
        r=review();self.assertFalse(reviewed(r))
        r.update(critical_unsupported=False,correct_source_reference=True)
        self.assertTrue(reviewed(r))
        r['independent']=False;self.assertFalse(reviewed(r))

    def test_wrong_value_and_dash(self):
        for v in ('0','11','—','-'):
            e=validate(evidence(reported_value=v,review=review()),{1:evidence().quote},SHA)
            self.assertEqual(e.validation_status,'needs_review')

    def test_wrong_page_hash(self):
        for pages,sha in [({2:evidence().quote},SHA),({1:evidence().quote},'b'*64)]:
            self.assertFalse(validate(evidence(review=review()),pages,sha).anchor_verified)

    def test_complete_review_admits(self):
        self.assertEqual(validate(evidence(review=review()),{1:evidence().quote},SHA).validation_status,'accepted')

    def test_incorrect_period_unit_scope_attribution_withheld(self):
        for field in ('period','unit','scope','attribution','column_association','rating_action_status','qualifications'):
            r=review();r[field]='incorrect'
            self.assertEqual(validate(evidence(review=r),{1:evidence().quote},SHA).validation_status,'needs_review')

    def test_schema_rejects_bad_types(self):
        for args in [dict(physical_page=True),dict(physical_page=0),dict(reported_value=10),dict(quote=None),dict(qualifiers='none'),dict(statement_kind='investment_recommendation')]:
            with self.assertRaises(ValueError):evidence(**args)

    def test_model_cannot_self_admit_or_inject_review(self):
        d=dict(document_id='fixture-doc',document_type='credit_rating',source_filename='fixture.pdf',sha256=SHA)
        c=dict(company_id='fixture');canonical=dict(pages=[dict(page_or_slide_number=1,selected_text=evidence().quote)])
        candidate=dict(physical_page=1,category='Financial resilience',metric='revenue',quote=evidence().quote,statement=evidence().quote,
            review=review(),validation_status='accepted')
        rows,rejections=ingest_model({'evidence':[candidate]},c,d,canonical,{'pages':[1],'task':'numerical'},'id')
        self.assertFalse(rows);self.assertEqual(len(rejections),1)

    def test_unsupplied_page_rejected(self):
        d=dict(document_id='fixture-doc',document_type='transcript',source_filename='fixture.pdf',sha256=SHA)
        candidate=dict(physical_page=4,category='Financial resilience',metric='revenue',quote='text',statement='text')
        rows,rejections=ingest_model({'evidence':[candidate]},dict(company_id='x'),d,{'pages':[]},{'pages':[1],'task':'management'},'id')
        self.assertFalse(rows);self.assertTrue(rejections)

    def test_numeric_serialization_preserved_without_reconstructing_source(self):
        d=dict(document_id='fixture-doc',document_type='credit_rating',source_filename='fixture.pdf',sha256=SHA)
        candidate=dict(physical_page=1,category='Financial resilience',metric='facility',quote='Amount 34.00 crore',statement='Amount 34.00 crore',reported_value=34.0)
        rows,notes=ingest_model({'evidence':[candidate]},dict(company_id='x'),
            d,{'pages':[dict(page_or_slide_number=1,selected_text=candidate['quote'])]}, {'pages':[1],'task':'numerical'},'id')
        self.assertEqual(rows[0]['reported_value'],'34.0');self.assertEqual(rows[0]['validation_status'],'needs_review')
        self.assertEqual(notes[0]['original_candidate']['reported_value'],34.0)

    def test_management_context_and_no_membership_input(self):
        doc=dict(document_id='x',sha256=SHA)
        canonical={'pages':[dict(page_or_slide_number=i,selected_text='Analyst question then management answer',elements=[]) for i in range(1,5)]}
        packet,messages=make_packet(doc,canonical,'management',2)
        self.assertEqual(packet['pages'],[1,2,3]);self.assertNotIn('focus',json.dumps(messages).lower())

    def test_d03_real_pdf_integration(self):
        path=Path('scrapper/tests/fixtures/documents/d03_text.pdf')
        with deny_network():canonical=extract_local(path.read_bytes(),'.pdf',ocr=False)
        cov=inventory(dict(document_id='fixture',document_type='credit_rating',physical_pages=len(canonical['pages'])),canonical)
        self.assertTrue(cov['pages']);self.assertIsNone(cov['material_recall']);self.assertIsNone(cov['pages'][0]['charts_detected'])

    def test_evaluation_locked_before_request(self):
        with patch('experiments.unified_financial_pilot.runner.Client') as client:
            with self.assertRaises(ValueError):
                run('artifacts/unified-financial-pilot-v0.1/pilot_manifest.json',
                    'artifacts/unified-financial-pilot-v0.1/test-must-not-exist',mode='smoke',company_id='RECLTD',
                    tasks=[dict(task='numerical',page=1,document_type='credit_rating')])
            client.assert_not_called()


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.v=self.root/'verification.json'
        save(self.v,dict(base_url='https://fixture.invalid/v1',model='fixture',modalities=['text'],
            verified_at=datetime.now(timezone.utc).isoformat(),pricing_per_token=dict(prompt=0.000001,completion=0.000002)))
        self.env=patch.dict(os.environ,dict(PILOT_API_ENDPOINT='https://fixture.invalid/v1',PILOT_MODEL_ID='fixture',
            PILOT_API_KEY='fixture-secret',PILOT_BUDGET_USD='1',PILOT_MAX_CALLS='1'),clear=True);self.env.start()
    def tearDown(self):self.env.stop();self.tmp.cleanup()

    def test_no_budget_no_request(self):
        os.environ.pop('PILOT_BUDGET_USD')
        with self.assertRaises(ValueError):Client(self.root,self.v)

    def test_reserve_failure_survives_restart(self):
        with patch('experiments.unified_financial_pilot.provider.http',side_effect=TimeoutError):
            with self.assertRaises(RuntimeError):Client(self.root,self.v).call({'task':'numerical'},'1',[])
        with patch('experiments.unified_financial_pilot.provider.http') as http:
            with self.assertRaises(ValueError):Client(self.root,self.v).call({'task':'numerical'},'1',[])
            http.assert_not_called()
        ledger=json.loads((self.root/'inference_usage.json').read_text())
        self.assertGreater(ledger['requests'][0]['charged_or_reserved_usd'],0)
        self.assertNotIn('fixture-secret',json.dumps(ledger))

    def test_cache_and_prompt_version_key(self):
        response=dict(choices=[dict(finish_reason='stop',message=dict(content='{"evidence":[]}'))],usage=dict(prompt_tokens=1,completion_tokens=2,cost=0.000005))
        with patch('experiments.unified_financial_pilot.provider.http',return_value=response) as http:
            c=Client(self.root,self.v);a=c.call({'task':'numerical'},'1',[]);b=c.call({'task':'numerical'},'1',[])
            self.assertEqual(a,b);self.assertEqual(http.call_count,1)
            with self.assertRaises(ValueError):c.call({'task':'numerical'},'2',[])

    def test_truncated_output_not_cached(self):
        response=dict(choices=[dict(finish_reason='length',message=dict(content='{}'))])
        with patch('experiments.unified_financial_pilot.provider.http',return_value=response):
            with self.assertRaises(RuntimeError):Client(self.root,self.v).call({'task':'numerical'},'1',[])
        self.assertFalse(list((self.root/'cache').glob('*.json')))

    def test_budget_and_tokens_preflight(self):
        for env in [dict(PILOT_BUDGET_USD='0.000001'),dict(PILOT_MAX_INPUT_TOKENS='1')]:
            with patch.dict(os.environ,env),patch('experiments.unified_financial_pilot.provider.http') as http:
                with self.assertRaises(ValueError):Client(self.root,self.v).call({'task':'numerical'},'1',[])
                http.assert_not_called()

    def test_wall_deadline_interrupts_trickling_body(self):
        class SlowBody:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):time.sleep(1);return '{}'
        with patch('urllib.request.urlopen',return_value=SlowBody()):
            start=time.monotonic()
            with self.assertRaises(TimeoutError):http('https://fixture.invalid',timeout=.03)
            self.assertLess(time.monotonic()-start,.5)


if __name__=='__main__':unittest.main()
