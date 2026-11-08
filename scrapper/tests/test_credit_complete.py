"""Full-rationale contract and visual-review tests: invented sources only."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scrapper.tests import test_credit_fresh as legacy
from scrapper.research.document_intelligence import credit_candidate_provider as p
from scrapper.research.document_intelligence import credit_fresh as cli
from scrapper.research.document_intelligence import credit_v1 as v
from scrapper.research.document_intelligence.credit_atomic import derive_claims
from scrapper.research.document_intelligence.credit_coverage import coverage_checklist
from scrapper.research.document_intelligence.credit_visual import bind_visual, validate_visual


def synthetic_pdf(path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    texts = [
        ['Synthetic Limited','October 30, 2025','Long-term rating AA; short-term rating A1.','Revenue was 20 crore in FY24 and 10 crore in FY23.'],
        ['Standalone revenue 20 crore in FY24.','Consolidated revenue 25 crore in FY24.','Liquidity adequate; debt below unity only if no debt-funded capex.'],
        ['Agency expects capex 30 crore in FY26, including interest.','Upgrade requires lower working capital and stronger profits.'],
        ['Rating reaffirmed.','Financial indicators: FY24 FY23; units crore.']]
    for lines in texts:
        page=writer.add_blank_page(width=612,height=792)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject(); stream.set_data(('BT /F1 11 Tf 40 740 Td '+ ' 0 -20 Td '.join('('+s+') Tj' for s in lines)+' ET').encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
    writer.write(path)


class CompleteTests(unittest.TestCase):
    setUp = legacy.FreshTests.setUp
    save = legacy.FreshTests.save
    load = legacy.FreshTests.load

    def full_fixture(self):
        self.ext['pages'].extend([
            {'page_or_slide_number':2,'selected_text':'Long-term AA; short-term A1.\nStandalone FY24 revenue 20 crore.\nConsolidated FY24 revenue 25 crore.\n','selected_text_origin':'native','elements':[]},
            {'page_or_slide_number':3,'selected_text':'Liquidity adequate only with undrawn lines.\n','selected_text_origin':'native','elements':[]}])
        self.catalog=p.build_region_catalog(self.ctx,self.ext,version=p.FULL_VERSION)
        self.request=p.build_request(self.ctx,self.catalog)

    def test_full_request_all_pages_tables_fields_and_limits(self):
        self.full_fixture()
        self.assertEqual([x['page'] for x in self.catalog['pages']],[1,2,3])
        self.assertEqual(self.request['field_coverage_checklist'],v.FIELD_NAMES)
        self.assertEqual(self.catalog['pages'][0]['tables'][0]['rows'][0],['Metric','FY24','FY23'])
        payload=cli.payload_for(self.request); estimate=cli.budget(payload)
        self.assertEqual(payload['response_format']['type'],'json_schema')
        self.assertEqual(payload['max_tokens'],65536)
        self.assertEqual(estimate['reservation_usd'],'0.35')
        self.assertFalse(payload['provider']['allow_fallbacks'])
        self.assertLess(estimate['conservative_max_cost_usd'],0.35)
        with self.assertRaises(ValueError): cli.budget({**payload,'messages':['x'*1048576]})

    def test_multipage_ratings_scope_periods_and_table_headers(self):
        self.full_fixture()
        def ids(page,needle):
            return [r['id'] for r in self.catalog['regions'] if r['context_page']==page and needle in r['evidence']['quote']]
        atoms=[]
        for key,value,scope in [('standalone','20','Standalone'),('consolidated','25','Consolidated')]:
            a=deepcopy(self.atom); a.update(id=key,value=value,accounting_scope=scope,support_region_ids=ids(2,scope))
            atoms.append(a)
        for key,value in [('long-term','AA'),('short-term','A1')]:
            a=deepcopy(self.atom);a.update(id=key,field='rating.current_rating',disclosure_category='rating',statement=key+' '+value,value=value,unit=None,support_region_ids=ids(2,'Long-term'))
            atoms.append(a)
        a=deepcopy(self.atom);a.update(id='financial-table',support_region_ids=[r['id'] for r in self.catalog['regions'] if r['evidence']['kind']=='table_cell'])
        atoms.append(a)
        a=deepcopy(self.atom);a.update(id='future',statement='Agency expects debt below unity in FY26 if no significant debt-funded capex.',period='FY26',period_kind='expectation_horizon',statement_kind='expectation',conditions=['No significant debt-funded capex'],support_region_ids=ids(1,'expects debt')+ids(3,'Liquidity'))
        atoms.append(a)
        self.save(atoms);claims,packet=self.load()
        self.assertEqual(len(packet['candidates']),6)
        children=[c for c in claims if 'atomic_key' in c]
        self.assertEqual([c['accounting_scope'] for c in children[:2]],['Standalone','Consolidated'])
        self.assertEqual(children[2]['value'],'AA');self.assertEqual(children[3]['value'],'A1')
        self.assertEqual(children[4]['verification_methods'],['table_cells'])
        self.assertEqual({e['page'] for e in children[5]['evidence']},{1,3})
        self.assertEqual(children[5]['period_kind'],'expectation_horizon')
        self.assertEqual(children[5]['conditions'],['No significant debt-funded capex'])
        self.assertEqual(claims,self.load()[0])

    def test_wrong_valid_region_and_unsupported_claim_require_review(self):
        self.full_fixture();a=deepcopy(self.atom)
        a.update(statement='Invented guaranteed liquidity',support_region_ids=[self.catalog['regions'][-1]['id']])
        bundle=self.save([a]);claims,packet=self.load()
        self.assertEqual(claims[-1]['evidence_decision'],'NEEDS_REVIEW')
        atom=packet['candidates'][0]
        reviews={'source_sha256':self.ctx.content_sha256,'canonical_sha256':p.canonical_hash(self.ext),'bundle_sha256':p.canonical_hash(bundle),'reviews':{atom['atomic_key']:{'candidate_sha256':p.canonical_hash(atom),'decision':'WITHHELD','reason':'Wrong source association; source contains no guarantee'}}}
        self.assertEqual(self.load(reviews=reviews)[0][-1]['evidence_decision'],'WITHHELD')

    def test_coverage_omission_parser_failure_evidence_failure_and_absence(self):
        self.full_fixture()
        empty=coverage_checklist(self.catalog)
        self.assertEqual({x['status'] for x in empty['fields'].values()},{'MISSING_CANDIDATE'})
        self.ext['pages'][2]['selected_text']='Liquidity \ufffd';self.catalog=p.build_region_catalog(self.ctx,self.ext,version=p.FULL_VERSION)
        self.request=p.build_request(self.ctx,self.catalog)
        a=deepcopy(self.atom);a['support_region_ids']=['wrong'];self.save([a]);claims,_=self.load()
        ledger=coverage_checklist(self.catalog,claims)
        self.assertEqual(ledger['fields'][a['field']]['status'],'EVIDENCE_FAILURE')
        self.assertEqual(ledger['fields'][a['field']]['parser_unresolved_pages'],[3])
        field='rating.withdrawal'
        review={field:{'catalog_sha256':p.canonical_hash(self.catalog),'reviewed_pages':[1,2,3],'decision':'NOT_DISCLOSED','reviewer':'synthetic-human','reason':'Test-only exhaustive original-page review','exact_source_review':True}}
        self.assertEqual(coverage_checklist(self.catalog,absence_reviews=review)['fields'][field]['status'],'NOT_DISCLOSED')
        review[field]['reviewed_pages']=[1]
        with self.assertRaises(ValueError): coverage_checklist(self.catalog,absence_reviews=review)

    def visual_fixture(self):
        pdf=Path(self.tmp.name)/'source.pdf';synthetic_pdf(pdf)
        from dataclasses import replace
        self.ctx=replace(self.ctx,content_sha256=v.digest(pdf))
        self.ext['pages'][0]['selected_text']='Rating reaf\ufffdrmed.\n'
        self.catalog=p.build_region_catalog(self.ctx,self.ext,version=p.FULL_VERSION)
        self.request=p.build_request(self.ctx,self.catalog)
        a=deepcopy(self.atom);a.update(id='damaged',statement='Rating reaffirmed.',field='rating.action',disclosure_category='rating',value='reaffirmed',unit=None,support_region_ids=[self.catalog['regions'][0]['id']])
        self.save([a]);parents,packet=self.load()
        self.assertEqual(parents[0]['evidence_decision'],'WITHHELD')
        self.assertFalse(packet['candidates'])
        # Use page 4 of the synthetic original, while its native extraction is damaged.
        self.ext['pages'][0]['page_or_slide_number']=4
        native=v.attach_evidence(self.ctx,self.ext,[p.text_evidence(self.ext['pages'][0],'Rating reaf\ufffdrmed.')])
        binding=bind_visual(pdf,self.ctx,self.ext,native,4,[30,35,400,100],Path(self.tmp.name)/'page.png')
        atom={k:deepcopy(a[k]) for k in ('statement','value','unit','period','period_kind','accounting_scope','attribution','statement_kind','conditions','qualifications','disclosure_category')}
        atom.update(atomic_key='visual-correction-v1',parent_claim_id=parents[0]['claim_id'],parent_candidate_sha256=p.canonical_hash(a),fields=[a['field']],derivation_basis='source_review_addition',support=native,visual_sources=[binding])
        packet={'source_sha256':self.ctx.content_sha256,'canonical_sha256':p.canonical_hash(self.ext),'candidates':[atom],'reviews':{}}
        return parents,packet,atom,binding

    def visual_review(self,packet,atom):
        packet['reviews'][atom['atomic_key']]={'candidate_sha256':p.canonical_hash(atom),'decision':'SUPPORTED','reason':'Synthetic exact original page/crop review, not native correction','verification_method':'visual_pdf_inspection','reviewer':'synthetic-human','reviewed_at':'2026-10-24','exact_source_review':True}

    def test_visual_requires_explicit_whole_atom_review_and_preserves_native(self):
        parents,packet,atom,binding=self.visual_fixture();before=deepcopy(self.ext)
        def derive(): return derive_claims(packet,parents,self.ctx,self.ext)[0]
        self.assertEqual(derive()['evidence_decision'],'NEEDS_REVIEW')
        self.visual_review(packet,atom);claim=derive()
        self.assertEqual(claim['evidence_decision'],'SUPPORTED')
        self.assertEqual(claim['verification_methods'],['visual_pdf_inspection'])
        self.assertIn('\ufffd',claim['evidence'][0]['quote'])
        self.assertEqual(self.ext,before);self.assertEqual(parents[0]['evidence_decision'],'WITHHELD')
        atom['statement']='Changed assertion'
        self.assertEqual(derive()['evidence_decision'],'WITHHELD')

    def test_visual_review_can_resolve_partial_native_extraction(self):
        parents,packet,atom,_=self.visual_fixture()
        self.ext['extraction_status']='partial'
        native=v.attach_evidence(self.ctx,self.ext,atom['support'])
        old=atom['visual_sources'][0]
        atom['support']=native
        atom['visual_sources']=[bind_visual(old['pdf_path'],self.ctx,self.ext,native,4,old['bbox'],old['image_path'])]
        packet['canonical_sha256']=p.canonical_hash(self.ext)
        self.visual_review(packet,atom)
        self.assertEqual(derive_claims(packet,parents,self.ctx,self.ext)[0]['evidence_decision'],'SUPPORTED')

    def test_visual_tampering_and_missing_assets_fail_closed(self):
        parents,packet,atom,binding=self.visual_fixture();self.visual_review(packet,atom)
        for key,value in [('page',1),('image_sha256','0'*64),('bbox',[0,0,20,20]),('source_document_sha256','b'*64),('native_support',[])]:
            b=deepcopy(binding);b[key]=value;b['visual_id']=p.canonical_hash({k:v for k,v in b.items() if k!='visual_id'})
            with self.subTest(key=key), self.assertRaises(ValueError):validate_visual(b,self.ctx,self.ext,atom['support'])
        Path(binding['image_path']).write_bytes(b'changed')
        self.assertEqual(derive_claims(packet,parents,self.ctx,self.ext)[0]['evidence_decision'],'WITHHELD')
        Path(binding['image_path']).unlink()
        self.assertEqual(derive_claims(packet,parents,self.ctx,self.ext)[0]['evidence_decision'],'WITHHELD')
        self.assertFalse(Path(binding['image_path']).exists())

    def test_damaged_native_cannot_be_admitted_without_visual_binding(self):
        parents,packet,atom,_=self.visual_fixture();del atom['visual_sources'];self.visual_review(packet,atom)
        self.assertEqual(derive_claims(packet,parents,self.ctx,self.ext)[0]['evidence_decision'],'WITHHELD')

    def test_visual_ai_correction_not_automatically_reviewed(self):
        parents,packet,atom,_=self.visual_fixture();atom['derivation_basis']='generated_ai';self.visual_review(packet,atom)
        self.assertEqual(derive_claims(packet,parents,self.ctx,self.ext)[0]['evidence_decision'],'WITHHELD')

    def test_visual_correction_exports_through_v1_and_replays(self):
        root=Path(self.tmp.name);pdf=root/'source.pdf';synthetic_pdf(pdf)
        cli.dump(root/'selection.json',{'pdf':str(pdf),'content_sha256':v.digest(pdf),'company_name':'Synthetic Limited','symbol':'SYNTHETIC','document_id':'synthetic-visual-full','report_date':'2025-10-30','report_date_basis':'Synthetic cover','source_url':'synthetic://visual-full'})
        prepared=root/'prepared';cli.prepare(root/'selection.json',prepared,full=True)
        manifest=v.read_json(prepared/'manifest.json');ctx=cli.DocumentContext(**manifest['context'])
        ext=v.read_json(prepared/'canonical.json')
        # Simulate parser damage, retaining the original canonical as a separate version.
        page=ext['pages'][3];page['selected_text']=page['selected_text'].replace('reaffirmed','reaf\ufffdrmed')
        cli.dump(root/'damaged-canonical.json',ext);manifest['canonical']=cli.descriptor(root/'damaged-canonical.json')
        catalog=p.build_region_catalog(ctx,ext,version=p.FULL_VERSION);request=p.build_request(ctx,catalog)
        native=v.attach_evidence(ctx,ext,[p.text_evidence(page,'Rating reaf\ufffdrmed.')])
        a=deepcopy(self.atom);a.update(id='action',field='rating.action',disclosure_category='rating',statement='Rating reaffirmed.',value='reaffirmed',unit=None,support_region_ids=[native[0]['region_id']])
        bundle=p.generate_candidates(lambda _: (json.dumps({'identity':p.identity(ctx),'disclosures':[a]}).encode(),{'inference_calls':0,'model':'synthetic'}),request)
        cli.dump(root/'bundle.json',bundle);manifest['candidate_bundle']=cli.descriptor(root/'bundle.json')
        parents,_=p.load_candidate_bundle(root/'bundle.json',ctx,ext)
        binding=bind_visual(pdf,ctx,ext,native,4,[30,35,400,100],root/'page-4.png')
        atom={k:deepcopy(a[k]) for k in ('statement','value','unit','period','period_kind','accounting_scope','attribution','statement_kind','conditions','qualifications','disclosure_category')}
        atom.update(atomic_key='reviewer-action-v1',parent_claim_id=parents[0]['claim_id'],parent_candidate_sha256=p.canonical_hash(a),fields=[a['field']],support=native,visual_sources=[binding],derivation_basis='source_review_addition')
        packet={'source_sha256':ctx.content_sha256,'canonical_sha256':p.canonical_hash(ext),'candidates':[atom],'reviews':{}}
        self.visual_review(packet,atom);cli.dump(root/'visual-review-v1.json',packet)
        manifest['atomic_candidates']=cli.descriptor(root/'visual-review-v1.json');cli.dump(root/'manifest.json',manifest)
        for n in (1,2):v.export_record(v.build_record(root/'manifest.json'),root/f'replay{n}','synthetic')
        record=v.read_json(root/'replay1/synthetic-credit-intelligence.json')
        visual=[c for c in record['claims'] if c.get('visual_sources')]
        self.assertEqual(len(visual),1)
        self.assertTrue(any(c['evidence_decision']=='WITHHELD' for c in record['evidence_review_queue']))
        for f in (root/'replay1').iterdir():self.assertEqual(f.read_bytes(),(root/'replay2'/f.name).read_bytes())

    def test_complete_pdf_workflow_and_replay(self):
        root=Path(self.tmp.name);pdf=root/'source.pdf';synthetic_pdf(pdf)
        cli.dump(root/'selection.json',{'pdf':str(pdf),'content_sha256':v.digest(pdf),'company_name':'Synthetic Limited','symbol':'SYNTHETIC','document_id':'synthetic-full','report_date':'2025-10-30','report_date_basis':'Synthetic cover','source_url':'synthetic://full'})
        prepared=root/'prepared';cli.prepare(root/'selection.json',prepared,full=True)
        request=v.read_json(prepared/'request.json');catalog=request['source_catalog']
        atoms=[]
        for page,needle in [(1,'Revenue was'),(2,'Liquidity'),(3,'expects capex')]:
            a=deepcopy(self.atom);a.update(id=str(page),support_region_ids=[r['id'] for r in catalog['regions'] if r['context_page']==page and needle in r['evidence']['quote']][:1])
            atoms.append(a)
        raw=json.dumps({'identity':request['identity'],'disclosures':atoms}).encode()
        bundle=p.generate_candidates(lambda _: (raw,{'inference_calls':0,'model':'synthetic'}),request)
        cli.dump(root/'bundle.json',bundle)
        record=cli.admit(prepared,root/'bundle.json',root/'unreviewed')
        packet=v.read_json(root/'unreviewed/atomic-review-proposals.json')
        reviews=v.read_json(root/'unreviewed/review-template.json')
        for atom in packet['candidates']:
            reviews['reviews'][atom['atomic_key']]={'candidate_sha256':p.canonical_hash(atom),'decision':'SUPPORTED' if atom['atomic_key'].startswith('fresh:0:') else 'WITHHELD','reason':'Synthetic source review; other atoms have deliberately wrong values/statements'}
        cli.dump(root/'reviews.json',reviews)
        reviewed=cli.admit(prepared,root/'bundle.json',root/'reviewed',root/'reviews.json')
        for n in (1,2):v.export_record(v.build_record(root/'reviewed/manifest.json'),root/f'replay{n}','synthetic')
        for f in (root/'replay1').iterdir():self.assertEqual(f.read_bytes(),(root/'replay2'/f.name).read_bytes())
        self.assertTrue(any(c.get('atomic_key') for c in reviewed['claims']))
        self.assertTrue((root/'reviewed/coverage-checklist.json').exists())
        self.assertFalse((prepared/'generation-reservation.json').exists())

if __name__=='__main__':unittest.main()
