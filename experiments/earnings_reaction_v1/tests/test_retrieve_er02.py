"""No real HTTP: owner-scope and stop-policy tests for ER-02 transport."""
import copy,json,tempfile,unittest
from pathlib import Path
from experiments.earnings_reaction_v1 import er02,retrieve_er02 as r

class Retrieval(unittest.TestCase):
    def setUp(self):
        self.manifest=json.loads((er02.OUT/'OFFICIAL_RETRIEVAL_MANIFEST.json').read_text())
        self.requests=r.plan(self.manifest,r.APPROVED_SHA256)
    def test_exact_scope(self):self.assertEqual(len(self.requests),8)
    def test_changed_hash_rejected(self):
        with self.assertRaises(ValueError):r.plan(self.manifest,'bad')
    def test_foreign_target_rejected(self):
        self.manifest['requests'][0]['url']='https://example.com/'
        with self.assertRaises(ValueError):r.plan(self.manifest,r.APPROVED_SHA256)
    def test_retry_policy_rejected(self):
        self.manifest['requests'][0]['max_attempts']=2
        with self.assertRaises(ValueError):r.plan(self.manifest,r.APPROVED_SHA256)
    def test_access_restriction_stops_remaining_calls(self):
        for code in [401,403,429]:
            with self.subTest(code=code),tempfile.TemporaryDirectory() as folder:
                calls=[]
                def mock(req,directory,n):
                    calls.append(req['url']);return dict(request_id=req['request_id'],http_status=code,curl_exit_code=0),b'blocked'
                state=r.execute(self.requests,Path(folder),mock)
                self.assertEqual(len(calls),1);self.assertEqual(len(state['requests_not_attempted']),7)
                self.assertEqual(state['stop_reason'],'EXCHANGE_ACCESS_RESTRICTION')
    def test_html_block_stops_without_browser_bypass(self):
        self.assertEqual(r.stop_reason(dict(http_status=200,curl_exit_code=0),b'<html>Access Denied</html>'),'EXCHANGE_ACCESS_RESTRICTION')
    def test_transport_failure_no_retry(self):
        self.assertEqual(r.stop_reason(dict(http_status=0,curl_exit_code=28),b''),'TRANSPORT_FAILURE_NO_RETRY')
    def test_redirect_not_followed(self):
        self.assertEqual(r.stop_reason(dict(http_status=302,curl_exit_code=0),b''),'HTTP_FAILURE_OR_REDIRECT_NO_RETRY')
    def test_exact_eight_successes_and_rerun_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            calls=[]
            def mock(req,directory,n):
                calls.append(req['url']);return dict(request_id=req['request_id'],http_status=200,curl_exit_code=0),b'[]'
            state=r.execute(self.requests,Path(folder),mock)
            self.assertEqual(calls,[q['url'] for q in self.requests]);self.assertTrue(state['complete_all_eight'])
            with self.assertRaises(FileExistsError):r.execute(self.requests,Path(folder),mock)
            self.assertEqual(len(calls),8)
    def test_exception_consumes_attempt_and_stops(self):
        with tempfile.TemporaryDirectory() as folder:
            def mock(*args):raise TimeoutError()
            state=r.execute(self.requests,Path(folder),mock)
            self.assertEqual(state['stop_reason'],'TRANSPORT_EXCEPTION_NO_RETRY');self.assertEqual(len(state['requests_not_attempted']),7)


@unittest.skipUnless((r.RUN/'EXECUTION.json').is_file(),'Actual tranche has not been executed')
class RetainedTranche(unittest.TestCase):
    def test_real_tranche_stopped_at_bse_403(self):
        state=json.loads((r.RUN/'EXECUTION.json').read_text())
        self.assertEqual([x['http_status'] for x in state['responses']],[200,403])
        self.assertEqual(state['stop_reason'],'EXCHANGE_ACCESS_RESTRICTION')
        self.assertEqual(len(state['requests_not_attempted']),6)
    def test_actual_source_receipt_hashes_match(self):
        import hashlib
        state=json.loads((r.RUN/'EXECUTION.json').read_text())
        for receipt in state['responses']:
            raw=(er02.OUT/receipt['body_path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),receipt['response_sha256'])
            self.assertEqual(receipt['requested_url'],receipt['final_url'])
    def test_real_timestamp_verified_earliest_remains_unresolved(self):
        record=json.loads((r.RUN/'E2E_TIMESTAMP_VERIFICATION.json').read_text())
        self.assertTrue(record['start']['timestamp_verified'])
        self.assertEqual(record['start']['publication_at_ist'],'2026-04-20T15:44:44+05:30')
        self.assertFalse(record['start']['actual_results_verified'])
        self.assertFalse(record['start_verified']);self.assertFalse(record['complete'])
    def test_actual_row_is_in_pilot_timestamp_evidence(self):
        import csv
        with (er02.OUT/'TIMESTAMP_EVIDENCE.csv').open() as h:records=list(csv.DictReader(h))
        found=[x for x in records if x['native_announcement_id']=='106594219']
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['verified_publication_at_ist'],'2026-04-20T15:44:44+05:30')
        with (er02.OUT/'PILOT_VERIFICATION.csv').open() as h:events=list(csv.DictReader(h))
        event=next(x for x in events if x['company_id']=='132')
        self.assertEqual(event['start_timestamp_stage'],'VERIFIED')
        self.assertEqual(event['earliest_start_stage'],'UNRESOLVED')
        self.assertEqual(event['market_data_status'],'DATA_INCOMPLETE')

if __name__=='__main__':unittest.main()
