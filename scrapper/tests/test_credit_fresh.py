"""Software-contract tests using synthetic provider output, not AI accuracy evidence."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scrapper.research.document_intelligence import credit_candidate_provider as p
from scrapper.research.document_intelligence import credit_fresh as cli
from scrapper.research.document_intelligence import credit_v1 as v
from scrapper.research.document_intelligence.schema import DocumentContext


class FreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'bundle.json'
        self.ctx = DocumentContext(document_id='synthetic', company_name='Synthetic Limited', content_sha256='a'*64, document_type='credit_rating')
        text = ('Synthetic Limited\nRevenue was 20 crore in FY24 and 10 crore in FY23.\n'
                'Agency expects debt below unity in FY26 if no significant debt-funded capex.\n'
                'Estimated capex is 30 crore, including interest; not completed spending.\n')
        self.ext = {'extraction_status': 'ok', 'parser_name': 'synthetic', 'parser_version': '1', 'pages': [
            {'page_or_slide_number': 1, 'selected_text': text, 'selected_text_origin': 'native', 'elements': [
                {'element_position': 0, 'text_origin': 'native', 'rows': [['Metric', 'FY24', 'FY23'], ['Revenue', '20', '10']], 'bbox': [0,0,10,10]}]}]}
        self.catalog = p.build_region_catalog(self.ctx, self.ext)
        self.request = p.build_request(self.ctx, self.catalog)
        self.atom = {'id': 'revenue24', 'disclosure_category': 'assessment', 'field': 'assessment.revenue_and_profitability',
            'statement': 'Revenue was 20 crore in FY24.', 'value': '20', 'unit': 'crore', 'period': 'FY24',
            'period_kind': 'historical_period', 'accounting_scope': None, 'attribution': 'Agency',
            'statement_kind': 'historical_fact', 'conditions': [], 'qualifications': [],
            'support_region_ids': [self.catalog['regions'][1]['id']]}

    def save(self, atoms=None, envelope=None):
        body = envelope or {'identity': p.identity(self.ctx), 'disclosures': atoms if atoms is not None else [self.atom]}
        raw = json.dumps(body).encode()
        bundle = p.generate_candidates(lambda req: (raw, {'model': 'synthetic', 'inference_calls': 0}), self.request)
        self.path.write_text(json.dumps(bundle))
        return bundle

    def load(self, **kwargs):
        return p.load_candidate_bundle(self.path, self.ctx, self.ext, **kwargs)

    def test_catalog_offsets_tables_context_and_hashes(self):
        self.assertEqual(len(self.catalog['regions']), 10)
        for r in self.catalog['regions']:
            self.assertTrue(v.attach_evidence(self.ctx, self.ext, [r['evidence']]))
        self.assertEqual(self.catalog['pages'][0]['tables'][0]['rows'][0], ['Metric','FY24','FY23'])
        self.assertEqual(self.catalog['canonical_sha256'], p.canonical_hash(self.ext))
        self.assertIn('if no significant', self.catalog['pages'][0]['text'])

    def test_valid_unreviewed_original_and_deterministic_replay(self):
        bundle = self.save()
        a, packet = self.load(); b, _ = self.load()
        self.assertEqual(a, b)
        self.assertEqual(a[0]['original_candidate'], self.atom)
        self.assertTrue(all(c['evidence_decision'] == 'NEEDS_REVIEW' for c in a))
        self.assertEqual(a[1]['field_completeness']['accounting_scope'], 'UNRESOLVED')
        self.assertEqual(bytes.fromhex(bundle['raw_response_hex']), json.dumps({'identity': p.identity(self.ctx), 'disclosures': [self.atom]}).encode())
        self.assertEqual(packet['reviews'], {})

    def test_unknown_regions_extra_page_table_quote_and_invalid_contract(self):
        for field, value in [('support_region_ids',['missing']), ('physical_page',99), ('table',99), ('quote','made up'),
                              ('field','wrong'), ('accounting_scope',23), ('statement_kind','SUPPORTED')]:
            with self.subTest(field=field):
                atom = deepcopy(self.atom); atom[field] = value
                self.save([atom]); claims, packet = self.load()
                self.assertEqual(claims[0]['evidence_decision'], 'WITHHELD')
                self.assertFalse(packet['candidates'])
        a = deepcopy(self.atom); del a['accounting_scope']
        self.save([a]); self.assertEqual(self.load()[0][0]['evidence_decision'], 'WITHHELD')

    def test_identity_raw_catalog_and_canonical_changes_fail_closed(self):
        for change in ('source', 'canonical', 'catalog', 'raw', 'company'):
            with self.subTest(change=change):
                bundle = self.save()
                if change == 'source': bundle['request']['identity']['content_sha256'] = 'b'*64
                elif change == 'catalog': bundle['request']['source_catalog']['regions'][0]['evidence']['page'] = 2
                elif change == 'raw': bundle['raw_response_hex'] += '20'
                elif change == 'company':
                    self.save(envelope={'identity': {**p.identity(self.ctx), 'company_name':'Other'}, 'disclosures':[self.atom]})
                if change not in ('company',): self.path.write_text(json.dumps(bundle))
                ext = deepcopy(self.ext)
                if change == 'canonical': ext['parser_version'] = '2'
                with self.assertRaises(ValueError): p.load_candidate_bundle(self.path, self.ctx, ext)

    def test_review_pins_entire_atom_and_conditions(self):
        a = deepcopy(self.atom)
        a.update(id='debt', field='assessment.capex_and_execution_risks', statement='Agency expects debt below unity if no significant debt-funded capex.',
                 value=None, unit=None, period='FY26', period_kind='expectation_horizon', statement_kind='expectation',
                 conditions=['No significant debt-funded capex'], qualifications=['Agency expectation, not company commitment'],
                 support_region_ids=[self.catalog['regions'][2]['id']])
        bundle = self.save([a]); claims, packet = self.load(); atom = packet['candidates'][0]
        review = {'source_sha256':self.ctx.content_sha256, 'canonical_sha256':p.canonical_hash(self.ext), 'bundle_sha256':p.canonical_hash(bundle),
                  'reviews':{atom['atomic_key']:{'candidate_sha256':p.canonical_hash(atom), 'decision':'SUPPORTED', 'reason':'Synthetic test source review'}}}
        claims, _ = self.load(reviews=review)
        self.assertEqual(claims[1]['evidence_decision'],'SUPPORTED')
        self.assertEqual(claims[0]['evidence_decision'],'NEEDS_REVIEW')
        review['reviews'][atom['atomic_key']]['candidate_sha256'] = 'c'*64
        self.assertEqual(self.load(reviews=review)[0][1]['evidence_decision'],'WITHHELD')
        # Removing the condition does not get semantic acceptance from an exact quote.
        a['conditions'] = []; a['qualifications'] = []
        self.save([a]); self.assertEqual(self.load()[0][1]['evidence_decision'],'NEEDS_REVIEW')
        with self.assertRaises(ValueError): self.load(reviews=review)

    def test_multiple_atoms_periods_scope_and_no_parent_inheritance(self):
        prior = deepcopy(self.atom); prior.update(id='revenue23', value='10', period='FY23', statement='Revenue was 10 crore in FY23.')
        capex = deepcopy(self.atom); capex.update(id='capex', field='assessment.capex_and_execution_risks', statement='Capex estimated at 30 crore.', value='30',
            period=None, period_kind='unknown', statement_kind='assessment', qualifications=['Includes interest; not completed spending'],
            support_region_ids=[self.catalog['regions'][3]['id']])
        self.save([self.atom, prior, capex]); claims, packet = self.load()
        atoms = [c for c in claims if 'atomic_key' in c]
        self.assertEqual([a['period'] for a in atoms],['FY24','FY23',None])
        self.assertTrue(all(a['accounting_scope'] is None for a in atoms))
        self.assertEqual(atoms[2]['qualifications'], capex['qualifications'])

    def test_unsupported_and_mixed_period_statement_never_auto_accept(self):
        a = deepcopy(self.atom); a.update(statement='Invented company commitment and mixed FY24/FY23 growth.', period='FY24/FY23')
        self.save([a]); self.assertEqual(self.load()[0][1]['evidence_decision'],'NEEDS_REVIEW')

    def test_duplicates_and_coverage(self):
        self.save([self.atom, self.atom]); claims, packet = self.load()
        self.assertFalse(packet['candidates']); self.assertTrue(all(c['evidence_decision']=='WITHHELD' for c in claims))
        a = deepcopy(self.atom); a['id']='copy'
        self.save([self.atom,a]); claims, _ = self.load()
        children = claims[2:]
        self.assertEqual(len(v.coverage_claim_ids(children,self.atom['field'])),1)

    def test_unresolved_text_and_partial_sources(self):
        self.ext['extraction_status']='partial'
        self.catalog=p.build_region_catalog(self.ctx,self.ext);self.request=p.build_request(self.ctx,self.catalog)
        self.save(); self.assertEqual(self.load()[0][0]['evidence_decision'],'WITHHELD')

    def test_malformed_response_retained_and_truncation_rejected(self):
        b=p.generate_candidates(lambda _: (b'not json', {}),self.request)
        self.assertEqual(bytes.fromhex(b['raw_response_hex']),b'not json')
        self.path.write_text(json.dumps(b))
        with self.assertRaises(ValueError): self.load()
        self.save(envelope={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})
        with self.assertRaises(ValueError): self.load()

    def test_prepare_admit_export_and_offline_replay(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        root = Path(self.tmp.name)
        pdf = root/'synthetic.pdf'
        writer = PdfWriter(); page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 50 740 Td (Synthetic Limited) Tj 0 -25 Td (October 30, 2025) Tj 0 -25 Td (Revenue was 20 crore in FY24 and 10 crore in FY23.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        writer.write(pdf)
        selection = {'pdf':str(pdf), 'content_sha256':v.digest(pdf), 'company_name':'Synthetic Limited', 'symbol':'SYNTHETIC', 'document_id':'synthetic-pdf',
            'report_date':'2025-10-30', 'report_date_basis':'Synthetic fixture cover', 'source_url':'synthetic://fixture', 'scope_limits':['Synthetic software test only']}
        cli.dump(root/'selection.json', selection)
        prepared = root/'prepared'
        cli.prepare(root/'selection.json', prepared)
        request = v.read_json(prepared/'request.json')
        region = next(r['id'] for r in request['source_catalog']['regions'] if 'Revenue was' in r['evidence']['quote'])
        atom = deepcopy(self.atom); atom['support_region_ids']=[region]
        bad = deepcopy(atom); bad.update(id='bad',support_region_ids=['unknown'])
        bundle = p.generate_candidates(lambda req: (json.dumps({'identity':request['identity'],'disclosures':[atom,bad]}).encode(), {'model':'synthetic','inference_calls':0}),request)
        cli.dump(root/'bundle.json',bundle)
        with v.offline_guard():
            record = cli.admit(prepared,root/'bundle.json',root/'admitted')
            first = v.build_record(root/'admitted/manifest.json'); second = v.build_record(root/'admitted/manifest.json')
            self.assertEqual(first,second); self.assertEqual(first,record)
            v.export_record(first,root/'replay1','synthetic');v.export_record(second,root/'replay2','synthetic')
        for file in (root/'replay1').iterdir():
            self.assertEqual(file.read_bytes(),(root/'replay2'/file.name).read_bytes())
        self.assertTrue(any(c['evidence_decision']=='WITHHELD' for c in record['evidence_review_queue']))
        packet = v.read_json(root/'admitted/atomic-review-proposals.json')
        review = v.read_json(root/'admitted/review-template.json')
        a = packet['candidates'][0]
        review['reviews'][a['atomic_key']]={'candidate_sha256':p.canonical_hash(a),'decision':'SUPPORTED','reason':'Synthetic test source review'}
        cli.dump(root/'source-review.json',review)
        admitted = cli.admit(prepared,root/'bundle.json',root/'reviewed',root/'source-review.json')
        self.assertEqual(len(admitted['claims']),1)
        self.assertEqual(admitted['coverage_ledger'][atom['field']]['completeness'],'PARTIAL')
        self.assertEqual(admitted['claims'][0]['field_completeness']['accounting_scope'],'UNRESOLVED')
        manifest = v.read_json(root/'admitted/manifest.json')
        manifest['context']['symbol'] = 'OTHER'
        cli.dump(root/'changed-company-manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'acquisition identity'):
            v.build_record(root/'changed-company-manifest.json')

    def test_generate_requires_authorization_and_reserves_once(self):
        directory = Path(self.tmp.name)
        payload = cli.payload_for(self.request)
        digest = p.sha_bytes(p.wire_bytes(payload))
        raw = json.dumps({'identity':p.identity(self.ctx), 'disclosures':[self.atom]}).encode()
        with patch.object(cli, 'verify_prepared', return_value=({'payload_sha256':digest}, self.request, payload)), \
             patch('scrapper.research.general_ai_experiment.runner.api_key', return_value='synthetic-key'), \
             patch('scrapper.research.general_ai_experiment.runner.request', return_value=raw) as transport:
            with self.assertRaises(ValueError): cli.generate(directory, 'wrong', '0.05')
            transport.assert_not_called()
            cli.generate(directory, digest, '0.05')
            self.assertEqual(transport.call_count, 1)
            self.assertTrue(transport.call_args.kwargs['return_raw'])
            self.assertEqual((directory/'response.raw.json').read_bytes(), raw)
            with self.assertRaises(FileExistsError): cli.generate(directory, digest, '0.05')
            self.assertEqual(transport.call_count, 1)

    def test_generate_failure_keeps_reservation_and_no_retry(self):
        directory = Path(self.tmp.name)
        payload = cli.payload_for(self.request)
        digest = p.sha_bytes(p.wire_bytes(payload))
        with patch.object(cli, 'verify_prepared', return_value=({'payload_sha256':digest}, self.request, payload)), \
             patch('scrapper.research.general_ai_experiment.runner.api_key', return_value='synthetic-key'), \
             patch('scrapper.research.general_ai_experiment.runner.request', side_effect=TimeoutError) as transport:
            with self.assertRaises(TimeoutError): cli.generate(directory, digest, '0.05')
            self.assertTrue((directory/'generation-failure.json').exists())
            with self.assertRaises(FileExistsError): cli.generate(directory, digest, '0.05')
            self.assertEqual(transport.call_count, 1)

    def test_budget_has_fixed_ceiling_no_fallback_and_exact_wire_hash(self):
        payload=cli.payload_for(self.request)
        self.assertFalse(payload['provider']['allow_fallbacks'])
        self.assertEqual(payload['provider']['only'],[cli.PROVIDER])
        self.assertLess(cli.budget(payload)['conservative_max_cost_usd'],0.05)
        with self.assertRaises(ValueError): cli.budget({'messages':['x'*1048576]})

    def test_structured_catalog_preserves_damage_and_adds_exact_intact_spans(self):
        ext = deepcopy(self.ext)
        ext['pages'][0]['selected_text'] = 'Facility 123 crore reaf\ufffdrmed.\n'
        legacy = p.build_region_catalog(self.ctx, ext)
        catalog = p.build_region_catalog(self.ctx, ext, version=p.STRUCTURED_VERSION)
        self.assertEqual(legacy['regions'][0]['state'], 'UNRESOLVED')
        clean = next(r for r in catalog['regions'] if r['evidence']['quote'] == 'Facility 123 crore ')
        self.assertEqual(clean['state'], 'AVAILABLE')
        v.attach_evidence(self.ctx, ext, [clean['evidence']])
        self.assertFalse(any(r['evidence']['quote'] in ('reaf', 'rmed.') for r in catalog['regions']))
        request = p.build_request(self.ctx, catalog)
        payload = cli.payload_for(request)
        self.assertEqual(payload['response_format']['type'], 'json_schema')
        self.assertTrue(payload['response_format']['json_schema']['strict'])
        schema = payload['response_format']['json_schema']['schema']
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['properties']), {'identity', 'disclosures'})
        malformed = json.dumps({'schema_version': p.VERSION, 'identity': p.identity(self.ctx), 'disclosures': []}).encode()
        self.path.write_text(json.dumps(p.generate_candidates(lambda _: (malformed, {}), request)))
        with self.assertRaisesRegex(ValueError, 'identity/contract mismatch'):
            p.load_candidate_bundle(self.path, self.ctx, ext)
        atom = deepcopy(self.atom)
        atom['support_region_ids'] = [clean['id']]
        raw = json.dumps({'identity': p.identity(self.ctx), 'disclosures': [atom]}).encode()
        self.path.write_text(json.dumps(p.generate_candidates(lambda _: (raw, {}), request)))
        claims, _ = p.load_candidate_bundle(self.path, self.ctx, ext)
        self.assertTrue(all(c['evidence_decision'] == 'NEEDS_REVIEW' for c in claims))
        atom['support_region_ids'] = [catalog['regions'][0]['id']]
        raw = json.dumps({'identity': p.identity(self.ctx), 'disclosures': [atom]}).encode()
        self.path.write_text(json.dumps(p.generate_candidates(lambda _: (raw, {}), request)))
        claims, packet = p.load_candidate_bundle(self.path, self.ctx, ext)
        self.assertEqual(claims[0]['evidence_decision'], 'WITHHELD')
        self.assertEqual(packet['candidates'], [])


if __name__ == '__main__': unittest.main()
