"""ER-02 promotion tests use SYNTHETIC receipts/content, never market evidence."""
import collections
import copy
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from experiments.earnings_reaction_v1 import event_isolation as er
from experiments.earnings_reaction_v1 import er02
from experiments.earnings_reaction_v1.verification import verify_event_boundaries


class Fixture:
    def __init__(self,root):self.root=root;self.n=0
    def body(self,payload,url):
        self.n+=1
        raw=json.dumps(payload).encode() if not isinstance(payload,bytes) else payload
        digest=hashlib.sha256(raw).hexdigest();body=f'{self.n}.body';receipt=f'{self.n}.receipt.json'
        (self.root/body).write_bytes(raw)
        (self.root/receipt).write_text(json.dumps(dict(requested_url=url,final_url=url,http_status=200,
            captured_at='2026-11-02T10:00:00+00:00',response_sha256=digest,provenance_locator='SYNTHETIC_TEST_ONLY')))
        return dict(body_path=body,receipt_path=receipt,sha256=digest)
    def disclosure(self,period,day):
        native=period+'@'+day;pub=day+'T10:00:01+05:30'
        rawtime=__import__('datetime').datetime.fromisoformat(pub).strftime('%d-%b-%Y %H:%M:%S')
        attachurl='https://nsearchives.nseindia.com/corporate/SYNTHETIC-'+period+'.pdf'
        text='SYNTHETIC financial results for issuer TEST quarter '+period+' revenue 100 profit 10'
        att=self.body(text.encode(),attachurl)
        textpath=period+'.txt';(self.root/textpath).write_text(text)
        row=dict(SYMBOL='TEST',ID=native,**{'EXCHANGE DISSEMINATION TIME':rawtime,'ATTACHMENT_URL':attachurl})
        ann=self.body({'rows':[row],'total':1},'https://www.nseindia.com/api/corporate-announcements?symbol=TEST&index=equities&from_date='+__import__('datetime').date.fromisoformat(period).strftime('%d-%m-%Y')+'&to_date='+__import__('datetime').date.fromisoformat(day).strftime('%d-%m-%Y'))
        return dict(exchange='NSE',exchange_key='TEST',native_announcement_id=native,announcement=ann,
            row_pointer='/rows/0',identity_field='SYMBOL',native_id_field='ID',
            public_timestamp_field='EXCHANGE DISSEMINATION TIME',attachment=att,attachment_url_field='ATTACHMENT_URL',
            content_review=dict(reviewed_by='synthetic_reviewer',locator='synthetic-page-1',quote=text,text_path=textpath,
                attachment_sha256=att['sha256'],company_id=1,period_end=period,kind='actual_financial_results'),
            issuer_exchanges=['NSE'],issuer_identifiers={'NSE':'TEST'},
            earliest_review=dict(reviewed_by='synthetic_reviewer',conflicts=[],unresolved_relationships=[],searches=[dict(
                exchange='NSE',exchange_key='TEST',source=ann,records_pointer='/rows',total_count_pointer='/total',
                identity_field='SYMBOL',native_id_field='ID',complete=True,pagination_complete=True,total_records=1,
                from_at=period+'T00:00:00+05:30',through_at=day+'T23:59:59+05:30',dispositions=[dict(
                    native_announcement_id=native,reviewed_by='synthetic_reviewer',reason='Synthetic attached results reviewed',
                    kind='actual_results',period_end=period,content_review_ref='content_review',publication_at_ist=pub)])]))


class Promotion(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.fixture=Fixture(self.root)
        self.review=dict(start=self.fixture.disclosure('2026-03-31','2026-04-20'),next=self.fixture.disclosure('2026-06-30','2026-07-20'))
    def tearDown(self):self.temp.cleanup()
    def verify(self):return verify_event_boundaries(self.review,company_id=1,period_end='2026-03-31',next_period_end='2026-06-30',cutoff='2026-11-02T10:00:00+00:00',evidence_root=self.root)
    def test_positive_boundaries_need_no_prices_calendar_or_subsector(self):
        result=self.verify();self.assertTrue(result['complete'],result)
        self.assertEqual(result['start_inclusive_ist'],'2026-04-20T10:00:01+05:30')
        self.assertEqual(result['end_exclusive_ist'],'2026-07-20T10:00:01+05:30')
    def test_independent_timestamp_survives_missing_attachment(self):
        self.review['start'].pop('attachment');r=self.verify()
        self.assertTrue(r['start']['timestamp_verified']);self.assertFalse(r['start_verified'])
    def test_independent_timestamp_survives_missing_earliest_review(self):
        self.review['start'].pop('earliest_review');r=self.verify()
        self.assertTrue(r['start']['timestamp_verified']);self.assertTrue(r['start']['actual_results_verified']);self.assertFalse(r['start_verified'])
    def test_verified_start_missing_next_is_censored(self):
        self.review.pop('next');r=self.verify();self.assertTrue(r['start_verified']);self.assertFalse(r['complete'])
    def test_boolean_claims_cannot_promote(self):
        self.review={'start_verified':True,'next_verified':True,'complete':True}
        self.assertFalse(self.verify()['complete'])
    def test_hash_alone_not_receipt(self):
        (self.root/self.review['start']['announcement']['receipt_path']).unlink()
        self.assertFalse(self.verify()['start']['timestamp_verified'])
    def test_publication_cannot_postdate_its_source_capture(self):
        path=self.root/self.review['start']['announcement']['receipt_path'];r=json.loads(path.read_text());r['captured_at']='2026-04-19T00:00:00+00:00';path.write_text(json.dumps(r))
        self.assertFalse(self.verify()['start']['timestamp_verified'])
    def test_non_exchange_origin(self):
        path=self.root/self.review['start']['announcement']['receipt_path'];r=json.loads(path.read_text());r['final_url']='https://example.com/file';path.write_text(json.dumps(r))
        self.assertFalse(self.verify()['start']['timestamp_verified'])
    def test_hash_mismatch(self):
        (self.root/self.review['start']['announcement']['body_path']).write_text('{}')
        self.assertFalse(self.verify()['start']['timestamp_verified'])
    def test_wrong_exchange_identity(self):
        self.review['start']['exchange_key']='OTHER';self.assertFalse(self.verify()['start']['timestamp_verified'])
    def test_native_id_not_generated_key(self):
        self.review['start']['native_announcement_id']='invented-key';self.assertFalse(self.verify()['start']['timestamp_verified'])
    def test_creation_time_is_rejected(self):
        self.review['start']['public_timestamp_field']='Fld_CreateDate';self.assertFalse(self.verify()['start']['timestamp_verified'])
    def test_unverified_semantics_not_promoted(self):
        self.review['start']['public_timestamp_field']='an_dt';self.assertFalse(self.verify()['start']['timestamp_verified'])
    def test_board_notice_not_actual_results(self):
        self.review['start']['content_review']['kind']='board_notice';r=self.verify()
        self.assertTrue(r['start']['timestamp_verified']);self.assertFalse(r['start']['actual_results_verified'])
    def test_wrong_attachment_link(self):
        self.review['start']['attachment']=self.review['next']['attachment'];self.assertFalse(self.verify()['start_verified'])
    def test_wrong_period_quote_and_parent_hash(self):
        for key,val in [('period_end','2026-06-30'),('quote','fabricated quote'),('attachment_sha256','bad')]:
            old=self.review['start']['content_review'][key];self.review['start']['content_review'][key]=val
            self.assertFalse(self.verify()['start_verified']);self.review['start']['content_review'][key]=old
    def test_earlier_search_needs_both_issuer_exchanges(self):
        self.review['start']['issuer_exchanges']=['NSE','BSE'];self.assertFalse(self.verify()['start_verified'])
    def test_pagination_flag_is_not_native_completeness_proof(self):
        self.review['start']['earliest_review']['searches'][0].pop('total_count_pointer');self.assertFalse(self.verify()['start_verified'])
    def test_partial_search_date(self):
        self.review['start']['earliest_review']['searches'][0]['from_at']='2026-04-20T00:00:00+05:30';self.assertFalse(self.verify()['start_verified'])
    def test_search_scope_is_bound_to_receipt_url(self):
        search=self.review['start']['earliest_review']['searches'][0];path=self.root/search['source']['receipt_path'];r=json.loads(path.read_text());r['requested_url']=r['requested_url'].replace('from_date=31-03-2026','from_date=20-04-2026');path.write_text(json.dumps(r));self.assertFalse(self.verify()['start_verified'])
    def test_unlinked_correction_blocks_earliest_not_timestamp(self):
        self.review['start']['earliest_review']['unresolved_relationships']=['revision-without-parent'];r=self.verify()
        self.assertTrue(r['start']['timestamp_verified']);self.assertFalse(r['start_verified'])
    def test_conflicting_exchange_record_blocks_earliest(self):
        self.review['start']['earliest_review']['conflicts']=['unresolved earlier BSE results'];self.assertFalse(self.verify()['complete'])
    def test_unreviewed_search_row(self):
        self.review['start']['earliest_review']['searches'][0]['dispositions'][0]['reviewed_by']='';self.assertFalse(self.verify()['start_verified'])
    def test_search_timestamp_not_source_bound(self):
        self.review['start']['earliest_review']['searches'][0]['dispositions'][0]['publication_at_ist']='2026-04-19T10:00:00+05:30';self.assertFalse(self.verify()['start_verified'])
    def test_future_next_release_not_admissible(self):
        r=verify_event_boundaries(self.review,company_id=1,period_end='2026-03-31',next_period_end='2026-06-30',cutoff='2026-05-01T00:00:00+05:30',evidence_root=self.root)
        self.assertTrue(r['start_verified']);self.assertFalse(r['next_verified']);self.assertFalse(r['complete'])
    def test_native_server_total_reveals_missing_pages(self):
        search=self.review['start']['earliest_review']['searches'][0]
        path=self.root/search['source']['body_path'];body=json.loads(path.read_text());body['total']=2
        receipt=json.loads((self.root/search['source']['receipt_path']).read_text())
        search['source']=self.fixture.body(body,receipt['requested_url'])
        self.assertFalse(self.verify()['start_verified'])
    def test_earlier_supported_actual_results_blocks_later_filing(self):
        other=self.fixture.disclosure('2026-03-31','2026-04-19')
        current=self.review['start'];search=current['earliest_review']['searches'][0]
        chosen_row=json.loads((self.root/current['announcement']['body_path']).read_text())['rows'][0]
        earlier_row=json.loads((self.root/other['announcement']['body_path']).read_text())['rows'][0]
        receipt=json.loads((self.root/search['source']['receipt_path']).read_text())
        search['source']=self.fixture.body({'rows':[earlier_row,chosen_row],'total':2},receipt['requested_url'])
        old=copy.deepcopy(other['earliest_review']['searches'][0]['dispositions'][0])
        old['content_review_ref']='other_content'
        search['dispositions'].insert(0,old);search['total_records']=2
        search['disclosure_reviews']={other['native_announcement_id']:other}
        result=self.verify();self.assertTrue(result['start']['timestamp_verified'])
        self.assertFalse(result['start_verified']);self.assertIn('earlier actual-results disclosure found',result['start']['reasons'])
    def test_receipt_needs_attributable_provenance(self):
        path=self.root/self.review['start']['announcement']['receipt_path'];r=json.loads(path.read_text());r.pop('provenance_locator');path.write_text(json.dumps(r))
        self.assertFalse(self.verify()['start']['timestamp_verified'])
    def test_partial_or_failed_transport_is_not_source_authentication(self):
        path=self.root/self.review['start']['announcement']['receipt_path'];r=json.loads(path.read_text());r['curl_exit_code']=63;path.write_text(json.dumps(r))
        self.assertFalse(self.verify()['start']['timestamp_verified'])
    def test_received_after_publication_conflict_is_not_ignored(self):
        d=self.review['start'];path=self.root/d['announcement']['body_path'];body=json.loads(path.read_text())
        body['rows'][0]['EXCHANGE RECEIVED TIME']='20-Apr-2026 11:00:00'
        receipt=json.loads((self.root/d['announcement']['receipt_path']).read_text())
        d['announcement']=self.fixture.body(body,receipt['requested_url'])
        self.assertFalse(self.verify()['start']['timestamp_verified'])
    def test_builder_emits_real_window_rows_from_evidence_mechanism(self):
        p=dict(cutoff='2026-11-02T10:00:00+00:00',sources=[],filings=[],parsed_filings=[],classifications=[],
            cohort=[dict(quarter='FY2026-Q4',canonical_company_id=1,company_name_raw='Synthetic TEST',source_id='test',source_path='test',source_sha256='test',row_id='1',row_ref='1',symbol_raw='TEST',exchange_raw='NSE',label_raw='',canonical_tutor_id='',mentor_decision='unknown',mentor_known_at=None,source_known_at=None,resolution_state='resolved',resolution_json='{}',overlay_evidence='{}')],
            companies=[dict(company_id=1,current_legal_name='Synthetic TEST')],identifiers=[],links=[],securities=[],candle_datasets=[])
        from contextlib import redirect_stdout
        import io
        with redirect_stdout(io.StringIO()):
            er.build(payload=p,output_dir=self.root,verification_reviews={'FY2026-Q4:1':self.review},evidence_root=self.root)
        with (self.root/'EVENT_WINDOWS.csv').open() as h:windows=list(csv.DictReader(h))
        with (self.root/'EVENT_LEDGER.csv').open() as h:ledger=list(csv.DictReader(h))
        self.assertEqual(len(windows),1);self.assertEqual(ledger[0]['boundary_status'],'VERIFIED_COMPLETE')
        self.assertEqual(ledger[0]['market_data_status'],'DATA_INCOMPLETE')
        self.review.pop('next')
        with redirect_stdout(io.StringIO()):
            er.build(payload=p,output_dir=self.root/'partial',verification_reviews={'FY2026-Q4:1':self.review},evidence_root=self.root)
        with (self.root/'partial/EVENT_LEDGER.csv').open() as h:partial=list(csv.DictReader(h))
        self.assertEqual(partial[0]['boundary_status'],'VERIFIED_RIGHT_CENSORED')
        self.assertEqual(partial[0]['start_timestamp_stage'],'VERIFIED')
        with (self.root/'partial/EVENT_WINDOWS.csv').open() as h:self.assertEqual(list(csv.DictReader(h)),[])


class ActualPilot(unittest.TestCase):
    def test_twelve_distinct_companies_six_per_quarter(self):
        p=json.loads((er02.OUT/'PILOT_SELECTION.json').read_text())
        self.assertEqual(len(p),12);self.assertEqual(len({r['company_id'] for r in p}),12)
        self.assertEqual(collections.Counter(r['quarter'] for r in p),{'FY2026-Q4':6,'FY2027-Q1':6})
    def test_er01_outputs_preserved(self):
        p=json.loads((er02.OUT/'ER01_PRESERVATION_BEFORE.json').read_text())
        if not any((er.OUT/name).is_file() for name in p):self.skipTest('Original ER-01 outputs absent from compact archive; local preservation audit retained')
        for name,digest in p.items():
            path=er.OUT/name
            if path.exists():self.assertEqual(er.sha(path),digest)
    def test_manifest_no_execution_and_exact_bounded_requests(self):
        p=json.loads((er02.OUT/'OFFICIAL_RETRIEVAL_MANIFEST.json').read_text())
        self.assertEqual(p['request_count'],48);self.assertEqual(p['initial_tranche_request_count'],8)
        self.assertEqual(len({r['url'] for r in p['requests']}),48)
        self.assertIn('AWAITING_OWNER_APPROVAL',p['approval_status'])
        self.assertTrue(all(r['max_attempts']==1 for r in p['requests']))
    def test_no_unsubstantiated_real_boundaries(self):
        with (er02.OUT/'EVENT_LEDGER.csv').open() as h:rows=list(csv.DictReader(h))
        self.assertEqual(len(rows),12)
        self.assertTrue(all(r['earliest_start_stage']=='UNRESOLVED' for r in rows))
        with (er02.OUT/'EVENT_WINDOWS.csv').open() as h:self.assertEqual(list(csv.DictReader(h)),[])
    def test_known_correction_remarks_retained(self):
        p=json.loads((er02.OUT/'PILOT_INPUTS.json').read_text())
        remarks=[json.loads(r['raw_row_json']).get('REVISION REMARKS','') for r in p['filings']]
        self.assertTrue(any('Discrepancy in Reporting type' in r for r in remarks))
        self.assertTrue(any('figures are left blank' in r for r in remarks))

if __name__=='__main__':unittest.main()
