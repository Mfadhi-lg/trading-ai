"""Explicit-grant result poll preparation, collisions and consumed-delivery tests."""
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from scrapper.db.database import connect, init_db
from scrapper.documents.availability import parse_utc

spec = importlib.util.spec_from_file_location('result_freshness', Path(__file__).parents[2]/'ops/activation/result_freshness.py')
polling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(polling)
NOW = parse_utc('2026-11-12T04:30:00+00:00')


class ResultPollingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root/'fixture.sqlite3'
        conn = connect(self.db)
        init_db(conn, apply_migrations=True)
        conn.close()
        self.lock = self.root/'acquisition-owner.lock'
        self.lock.write_text('')
        self.config = {'schema_version':1, 'enabled':True, 'cadence_minutes':10,
            'monitored_start_ist':'09:00', 'monitored_end_ist':'23:00', 'max_new_queue_entries':100,
            'sources':{'nse_financial_results':{'requests_max_per_day':4, 'source_policy_evidence':'Synthetic offline grant'}},
            'db_path':str(self.db), 'snapshot_root':str(self.root/'snapshots'),
            'state_dir':str(self.root/'receipts'), 'acquisition_owner_lock':str(self.lock)}
        self.config_path = self.root/'config.json'
        self.config_path.write_text(json.dumps(self.config))
        self.grant_path = self.root/'owner-grant.json'
        self.grant_path.write_text(json.dumps({'authorized':True,
            'configuration_sha256':hashlib.sha256(self.config_path.read_bytes()).hexdigest(),
            'owner_authorization_evidence':'Synthetic test only; no live source permitted',
            'starts_at_utc':'2026-11-12T00:00:00+00:00', 'expires_at_utc':'2026-11-13T00:00:00+00:00'}))
        self.calls = []

    def workflow(self, **kwargs):
        self.calls.append(kwargs)
        return {'status':'OBSERVED', 'filings':{'stored':1}, 'enqueue':{'queued':1}}

    def run_tick(self, **kwargs):
        return polling.tick(self.config_path,self.grant_path,now=NOW,workflow=self.workflow,
                            clock=lambda:NOW,fetcher=lambda *_: self.fail('workflow should own mocked response'),**kwargs)

    def test_preparation_is_inert_until_exact_explicit_grant(self):
        self.assertEqual(self.run_tick()['status'],'AWAITING_EXPLICIT_EXECUTION')
        self.assertFalse((self.root/'receipts').exists())
        self.assertEqual(self.calls,[])
        self.config_path.write_text(json.dumps(dict(self.config,max_new_queue_entries=99)))
        with self.assertRaisesRegex(RuntimeError,'exact independently approved'):
            self.run_tick(execute=True)

    def test_same_slot_is_idempotent_and_only_current_and_prior_periods_are_requested(self):
        first=self.run_tick(execute=True)
        self.assertEqual(len(self.calls),2)
        self.assertEqual([row['result_period'] for row in self.calls],['2026-09','2026-06'])
        second=self.run_tick(execute=True)
        self.assertEqual(len(self.calls),2)
        self.assertEqual({row['status'] for row in second['results']},{'SLOT_CONSUMED'})
        self.assertFalse(first['live_detection_target_verified'])

    def test_mocked_official_responses_reach_canonical_filings_watermark_and_receipts(self):
        from urllib.parse import parse_qs,urlsplit
        from scrapper.core.http import FetchResponse
        def fetch(url,exchange):
            self.calls.append(url)
            period=parse_qs(urlsplit(url).query)['period_ended'][0]
            body=('SYMBOL,COMPANY NAME,PERIOD ENDED,CONSOLIDATED,BROADCAST DATE/TIME\n'
                  'SYNTH,Synthetic Issuer,'+period+',Standalone,12-Nov-2026 09:59:00\n').encode()
            return FetchResponse(url,url,200,body,'text/csv','utf-8',None,None,'public','ok')
        result=polling.tick(self.config_path,self.grant_path,execute=True,now=NOW,
                            clock=lambda:NOW,fetcher=fetch)
        self.assertEqual(result['status'],'OBSERVED')
        self.assertEqual(len(self.calls),2)
        conn=connect(self.db)
        try:
            self.assertEqual(conn.execute('SELECT count(*) FROM exchange_result_filings').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM exchange_result_watermarks').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT count(*) FROM exchange_source_snapshots').fetchone()[0],2)
        finally:
            conn.close()
        self.assertEqual(len(list((self.root/'receipts').glob('*.reserved.result.json'))),2)

    def test_quota_and_owner_lock_are_shared_admission_gates(self):
        with self.lock.open('r+') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.run_tick(execute=True)['status'],'ACQUISITION_BUSY')
        self.assertEqual(len(self.calls),0)
        self.run_tick(execute=True)
        polling.tick(self.config_path,self.grant_path,execute=True,
                     now=parse_utc('2026-11-12T04:40:00+00:00'),clock=lambda:NOW,workflow=self.workflow,fetcher=lambda *_:None)
        third=polling.tick(self.config_path,self.grant_path,execute=True,
                     now=parse_utc('2026-11-12T04:50:00+00:00'),clock=lambda:NOW,workflow=self.workflow,fetcher=lambda *_:None)
        self.assertEqual(len(self.calls),4)
        self.assertEqual({row['status'] for row in third['results']},{'QUOTA_EXHAUSTED'})

    def test_unknown_delivery_and_processing_failure_never_repeat_provider_access(self):
        state=self.root/'receipts';state.mkdir()
        (state/'old.reserved.json').write_text(json.dumps({'dataset':'nse_financial_results'}))
        result=self.run_tick(execute=True)
        self.assertEqual({row['status'] for row in result['results']},{'BLOCKED_UNCERTAIN_DELIVERY'})
        self.assertEqual(self.calls,[])

    def test_failed_poll_stops_source_before_second_period_and_later_slots(self):
        def fail(**kwargs):
            self.calls.append(kwargs)
            raise RuntimeError('Synthetic uncertain source failure')
        result=polling.tick(self.config_path,self.grant_path,execute=True,now=NOW,clock=lambda:NOW,workflow=fail,fetcher=lambda *_:None)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(result['results'][1]['status'],'BLOCKED_SOURCE_ACCESS')
        later=polling.tick(self.config_path,self.grant_path,execute=True,
            now=parse_utc('2026-11-12T04:40:00+00:00'),clock=lambda:NOW,workflow=self.workflow,fetcher=lambda *_:None)
        self.assertEqual({row['status'] for row in later['results']},{'BLOCKED_SOURCE_ACCESS'})
        self.assertEqual(len(self.calls),1)

    def test_grant_expiry_between_requests_stops_new_source_access(self):
        stamps=iter((NOW,parse_utc('2026-11-13T00:00:00+00:00')))
        result=polling.tick(self.config_path,self.grant_path,execute=True,now=NOW,
            clock=lambda:next(stamps),workflow=self.workflow,fetcher=lambda *_:None)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['results'][1]['status'],'BLOCKED')

    def test_resume_polls_current_source_state_in_next_slot_without_replaying_missed_slots(self):
        self.run_tick(execute=True)
        later=polling.tick(self.config_path,self.grant_path,execute=True,
            now=parse_utc('2026-11-12T06:10:00+00:00'),clock=lambda:NOW,workflow=self.workflow,fetcher=lambda *_:None)
        self.assertEqual(len(later['results']),2)
        self.assertEqual(len(self.calls),4)
        self.assertTrue(all(row['effective_date']=='2026-11-12' for row in self.calls))


if __name__=='__main__':
    unittest.main()
