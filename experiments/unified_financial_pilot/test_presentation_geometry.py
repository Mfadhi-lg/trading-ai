"""Offline structural regressions, never financial correctness measurements."""
from copy import deepcopy
import json
from pathlib import Path
import re
import unittest

from .presentation_geometry import page_context, presentation_context, model_context
from .runner import make_packet, ingest_model, record_id
from .evaluate import reviewed
from .provider import digest, stable
from .schema import PRESENTATION_CHECKS, validate
from .test_pilot import evidence, review, SHA
from scrapper.research.document_intelligence.offline import deny_network

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'artifacts/unified-financial-pilot-v0.1'


def fixture_words(texts, boxes):
    raw = ' '.join(texts)
    offset = 0
    words = []
    for text, box in zip(texts, boxes):
        words.append(dict(text=text, start=offset, end=offset+len(text), bbox=box))
        offset += len(text)+1
    return dict(element_position=0, element_kind='text_block', text_raw=raw,
                bbox=[0,0,200,100], reference=dict(native_words=words))


def synthetic(boundaries=True):
    # D03 has merged same-baseline headings and values across two columns.
    element = fixture_words(['Division A','Division B','10.00%','2,000'],
                            [[5,5,40,17],[105,5,140,17],[5,30,40,40],[105,30,140,40]])
    elements = [element]
    if boundaries:
        elements += [dict(element_position=i+1, element_kind='image', bbox=box)
                     for i, box in enumerate([[0,0,90,60],[100,0,190,60]])]
    return dict(page_or_slide_number=1, selected_text=element['text_raw'], elements=elements)


def normalized(text):
    return re.sub(r'\s+', ' ', text.replace('•', ' ')).strip()


class GeometryTests(unittest.TestCase):
    def test_multicolumn_split_original_offsets_and_numeric_format(self):
        p = page_context(synthetic())
        self.assertEqual([(r['heading'],normalized(r['text'])) for r in p['regions']],
                         [('Division A','Division A 10.00%'),('Division B','Division B 2,000')])
        self.assertFalse(p['ambiguities'])
        blocks={b['id']:b for b in p['blocks']}
        for region in p['regions']:
            for fragment in region['fragments']:
                a,b=fragment['offsets']
                self.assertEqual(fragment['text'],blocks[fragment['block']]['text'][a:b])

    def test_borderless_multicolumn_is_unresolved_no_nearest_heading(self):
        p=page_context(synthetic(False))
        self.assertFalse(p['regions'])
        self.assertEqual(len(p['ambiguities']),4)
        self.assertEqual(len(p['spans']),4)

    def test_overlapping_boundaries_are_not_nearest_assigned(self):
        page=synthetic()
        page['elements'][1]['bbox']=[0,0,150,60]
        page['elements'][2]['bbox']=[0,0,160,50]
        # Exclude second heading so both competing boxes have one heading.
        page['elements'][0]=fixture_words(['Title','10.00'],[[5,5,30,17],[5,30,30,40]])
        p=page_context(page)
        self.assertTrue(all(s['region'] is None for s in p['spans']))
        self.assertTrue(all(a['reason']=='Competing boundaries' for a in p['ambiguities']))

    def test_coextensive_layers_and_containing_background(self):
        page=synthetic()
        page['elements'] += [dict(element_position=3, element_kind='image',bbox=[.2,.2,89.8,59.8]),
                             dict(element_position=4, element_kind='image',bbox=[0,0,200,100])]
        p=page_context(page)
        self.assertEqual(len(p['regions']),2)
        self.assertEqual(len(p['regions'][0]['boundary_sources']),2)
        self.assertFalse(p['ambiguities'])

    def test_stacked_panels_reading_order(self):
        page=synthetic()
        words=page['elements'][0]['reference']['native_words']
        words[1]['bbox']=[5,105,40,117];words[3]['bbox']=[5,130,40,140]
        page['elements'][2]['bbox']=[0,100,90,160]
        p=page_context(page)
        self.assertEqual([r['heading'] for r in p['regions']],['Division A','Division B'])
        self.assertEqual([r['reading_order'] for r in p['regions']],[0,1])

    def test_no_word_geometry_keeps_original_block_unresolved(self):
        page=synthetic();page['elements'][0]['reference']={}
        p=page_context(page)
        self.assertFalse(p['regions']);self.assertEqual(len(p['blocks']),1)
        self.assertIn('No word geometry',p['ambiguities'][0]['reason'])

    def test_equal_size_heading_and_body_not_guessed(self):
        page=synthetic()
        for w in page['elements'][0]['reference']['native_words']:
            w['bbox'][3]=w['bbox'][1]+10
        self.assertFalse(page_context(page)['regions'])


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.context=model_context(dict(kind='test',note='',pages=[page_context(synthetic())]))['pages'][0]
        self.region=self.context['regions'][0]
        self.fragment=self.region['fragments'][1]
        self.annotation=review()|{k:'correct' for k in PRESENTATION_CHECKS}
        self.e=evidence(document_type='presentation',reported_value='10.00',quote='10.00%',
                        source_region_id=self.region['id'],source_span_ids=[self.fragment['id']])

    def test_literal_match_does_not_admit(self):
        checked=validate(self.e,{1:'10.00%'},SHA,region_context=self.context)
        self.assertTrue(checked.anchor_verified);self.assertEqual(checked.validation_status,'needs_review')

    def test_each_required_review_dimension_must_be_explicit(self):
        for key in ('business_association','period_association','source_region','unit','scope','qualifications'):
            for state in (None,'incorrect'):
                e=deepcopy(self.e);e.review=self.annotation|{key:state}
                self.assertEqual(validate(e,{1:'10.00%'},SHA,region_context=self.context).validation_status,'needs_review')

    def test_wrong_region_and_missing_geometry_cannot_be_admitted(self):
        self.e.review=self.annotation
        self.assertEqual(validate(self.e,{1:'10.00%'},SHA).validation_status,'needs_review')
        self.e.source_region_id=self.context['regions'][1]['id']
        self.assertEqual(validate(self.e,{1:'10.00%'},SHA,region_context=self.context).validation_status,'needs_review')

    def test_source_span_ids_contract_is_fragment_ids_not_native_word_ids(self):
        self.e.review=self.annotation
        self.e.source_span_ids=['p1-e0-w2']
        checked=validate(self.e,{1:'10.00%'},SHA,region_context=self.context)
        self.assertTrue(checked.anchor_verified)
        self.assertEqual(checked.validation_status,'needs_review')

    def test_geometry_alone_is_not_semantic_validator(self):
        self.e.review=self.annotation
        self.assertEqual(validate(self.e,{1:'10.00%'},SHA,region_context=self.context).validation_status,'accepted')
        # Admission depends on a real external human annotation; this is synthetic.
        self.e.review['business_association']='incorrect'
        self.assertEqual(validate(self.e,{1:'10.00%'},SHA,region_context=self.context).validation_status,'needs_review')

    def test_presentation_review_intake_requires_business_and_region_checks(self):
        annotation=review()|dict(critical_unsupported=False,correct_source_reference=True)
        self.assertTrue(reviewed(annotation,'credit_rating'))
        self.assertTrue(reviewed(annotation,'transcript'))
        self.assertFalse(reviewed(annotation,'presentation'))
        annotation.update({k:'correct' for k in PRESENTATION_CHECKS})
        self.assertTrue(reviewed(annotation,'presentation'))
        annotation['source_region']='not_applicable'
        self.assertFalse(reviewed(annotation,'presentation'))

    def test_region_identity_distinguishes_identical_quotes(self):
        first=self.e.to_dict();first['representation_id']='test'
        second=first|dict(source_region_id='different')
        self.assertNotEqual(record_id(first),record_id(second))


class VimtaRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        m=json.loads((BASE/'pilot_manifest.json').read_text())
        cls.doc=next(d for c in m['companies'] if c['company_id']=='VIMTALABS'
                     for d in c['documents'] if d['document_type']=='presentation')
        cls.canonical=json.loads((BASE/'replayed-final/VIMTALABS/presentation.canonical.json').read_text())
        with deny_network():
            cls.geometry=presentation_context(cls.doc,cls.canonical,[25])
            cls.packet,cls.messages=make_packet(cls.doc,cls.canonical,'qualitative',25)

    def test_six_disclosures_have_distinct_correct_panel_memberships(self):
        # Audit expectations are test data, never extraction rules.
        cases=[('Customers – 90% of India top 20','Pharma Analytical'),
               ('40% revenues from overseas','Pharma Analytical'),
               ('75% of revenues from overseas','Clinical Research'),
               ('Amongst Top 3 in India','Preclinical'),
               ('Top 2 in India','Food & Environment Testing'),
               ('The only private EU commission approved lab in India','Food & Environment Testing')]
        for needle,heading in cases:
            for p in (self.geometry['pages'][0],self.packet['representation']['pages'][0]):
                matches=[r for r in p['regions'] if needle in normalized(r.get('text','\n'.join(f['text'] for f in r['fragments'])))]
                self.assertEqual(len(matches),1,needle)
                self.assertEqual(matches[0]['heading'],heading)
                self.assertEqual(matches[0]['status'],'proposed')

    def test_southern_india_literal_survives_no_semantic_claim(self):
        r=next(r for r in self.packet['representation']['pages'][0]['regions'] if r['heading']=='Electronics & Electricals')
        self.assertIn('One of the leading labs in Southern India',normalized(' '.join(f['text'] for f in r['fragments'])))

    def test_every_word_preserved_and_all_ids_resolve(self):
        p=self.geometry['pages'][0];used=[]
        for r in p['regions']:
            used.extend(r['spans'])
        used.extend(s['id'] for s in p['spans'] if not s['region'])
        self.assertEqual(sorted(used),sorted(s['id'] for s in p['spans']))
        self.assertEqual(len(used),len(set(used)))
        self.assertEqual(len(p['regions']),5)

    def test_exact_qwen_context_identity_and_offline_budget(self):
        self.assertEqual(json.loads(self.messages[1]['content']),self.packet['representation'])
        self.assertEqual(self.packet['representation_sha256'],digest(stable(self.packet['representation']).encode()))
        self.assertLess(len(stable(self.messages).encode())+1024,16000)
        self.assertNotIn('at most four priority',self.messages[0]['content'])
        self.assertIn('source_region_id',self.messages[0]['content'])

    def test_presentation_json_example_explicitly_includes_citation_contract(self):
        example=json.JSONDecoder().raw_decode(self.messages[0]['content'].split('Return JSON only: ',1)[1])[0]
        self.assertEqual(example['evidence'][0]['source_region_id'],'pN-rR')
        self.assertEqual(example['evidence'][0]['source_span_ids'],['pN-rR-fF'])

    def test_historical_text_packets_for_other_document_types_unchanged(self):
        ledger=json.loads((BASE/'inference/inference_usage.json').read_text())
        m=json.loads((BASE/'pilot_manifest.json').read_text())
        docs={d['document_id']:d for c in m['companies'] if c['company_id']=='VIMTALABS' for d in c['documents']}
        for attempt in ledger['requests']:
            doc=docs[attempt['packet']['document_id']]
            if doc['document_type']=='presentation':continue
            canonical=json.loads((BASE/'replayed-final/VIMTALABS'/f'{doc["document_type"]}.canonical.json').read_text())
            packet,_=make_packet(doc,canonical,attempt['packet']['task'],attempt['packet']['pages'][len(attempt['packet']['pages'])//2])
            self.assertEqual(packet['representation'],attempt['packet']['representation'])


if __name__=='__main__':
    unittest.main()
