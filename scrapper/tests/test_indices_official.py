"""Offline synthetic retained-receipt contracts; no provider or archive access."""
import csv
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from scrapper.indices.official import load_official

KEY = 'NSE_INDEX|Exact Name'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class OfficialIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        base = self.root/'artifacts/technical-integration-v2'
        base.mkdir(parents=True)
        with (base/'UPSTOX_ALL_INDICES.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['instrument_key','official_name','classification'])
            writer.writeheader()
            writer.writerows([dict(instrument_key=KEY,official_name='Exact Name',classification='BROAD'),
                              dict(instrument_key='NSE_INDEX|Missing',official_name='Missing',classification='THEMATIC')])
        self.archive=self.root/'archive';self.archive.mkdir()
        self.store=SimpleNamespace(root=self.archive)
        self.base=self.root/'artifacts/technical-fix-v2';self.base.mkdir(parents=True)
        self.query=dict(request_id='one',kind='NSE_INDEX',series='Exact Name',instrument_key=KEY,unit='days',interval=1,
                        from_date='2026-11-02',to_date='2026-11-04',url='retained://exact')

    def save(self, candles):
        raw=json.dumps(dict(data=dict(candles=candles))).encode()
        (self.archive/'one.gz').write_bytes(gzip.compress(raw))
        plan=json.dumps(dict(requests=[self.query])).encode()
        (self.base/'BOUNDED_ACQUISITION_MANIFEST.json').write_bytes(plan)
        self.receipt=dict(kind='NSE_INDEX',series='Exact Name',url='retained://exact',status='SUCCESS',raw_path='one.gz',
                          received_at='2026-11-05T00:00:00+05:30',sha256=digest(raw),request_id='one')
        self.write_ledger(digest(plan))

    def write_ledger(self, plan_hash=None):
        if plan_hash is None:
            plan_hash=digest((self.base/'BOUNDED_ACQUISITION_MANIFEST.json').read_bytes())
        (self.base/'UPDATE_LEDGER.json').write_text(json.dumps(dict(manifest_sha256=plan_hash,requests={'one':self.receipt})))

    def load(self, **kwargs):
        return load_official(self.store,root=self.root,start='2026-11-02',end='2026-11-04',
                             asof=kwargs.pop('asof','2026-11-06T00:00:00+05:30'),**kwargs)

    def test_exact_keys_decimal_coverage_and_no_substitution(self):
        self.save([['2026-11-02T00:00:00+05:30',100.01,102,99,101.01,0,0]])
        result=self.load()
        self.assertEqual(str(result['bars'][KEY][0]['close']),'101.01')
        self.assertEqual(result['bars']['NSE_INDEX|Missing'],[])
        row=result['inventory'][0]
        self.assertEqual(row['unit'],'index points')
        self.assertEqual(row['missing_dates'],['2026-11-03','2026-11-04'])
        self.assertEqual(row['age_calendar_days'],4)
        self.assertFalse(result['bars'][KEY][0]['historical_delivery_verified'])

    def test_invalid_ohlc_and_duplicates_not_selected(self):
        row=['2026-11-02T00:00:00+05:30',100,102,99,101,0,0]
        self.save([row,row,['2026-11-03T00:00:00+05:30',100,90,99,101,0,0]])
        result=self.load()
        self.assertEqual(result['bars'][KEY],[])
        self.assertIn('DUPLICATED_DAILY_BUCKET',[r['reason'] for r in result['exclusions']])
        self.assertIn('INVALID_OHLC',[r['reason'] for r in result['exclusions']])

    def test_unfinished_close_not_exposed(self):
        self.save([['2026-11-02T00:00:00+05:30',100,102,99,101,0,0]])
        self.assertEqual(self.load(asof='2026-11-02T14:00:00+05:30')['bars'][KEY],[])

    def test_checksum_and_manifest_corruption_fail_closed(self):
        self.save([])
        self.receipt['sha256']='bad';self.write_ledger()
        with self.assertRaisesRegex(ValueError,'source checksum'):
            self.load()
        self.write_ledger('bad')
        with self.assertRaisesRegex(ValueError,'manifest checksum'):
            self.load()

    def test_missing_history_receipt_remains_unavailable(self):
        self.save([])
        self.receipt.update(status='REJECTED',http_status=400)
        self.write_ledger()
        result=self.load()
        self.assertEqual(result['inventory'][0]['history_status'],'UNAVAILABLE_NO_ACCEPTED_HISTORY')
        self.assertEqual(result['inventory'][0]['acquisition_failures'][0]['http_status'],400)

    def test_archive_path_traversal_refused(self):
        self.save([])
        self.receipt['raw_path']='../escape';self.write_ledger()
        with self.assertRaisesRegex(ValueError,'escapes'):
            self.load()

    def test_incremental_receipts_update_official_history(self):
        from scrapper.market_refresh.planner import digest as plan_digest
        self.save([['2026-11-02T00:00:00+05:30',100,102,99,101,0,0]])
        raw=json.dumps(dict(data=dict(candles=[['2026-11-03T00:00:00+05:30',101,103,100,102,0,0]]))).encode()
        (self.archive/'incremental.gz').write_bytes(gzip.compress(raw))
        query=dict(self.query,request_id='LIVE_FRESH_fixture',from_date='2026-11-03',to_date='2026-11-03')
        plan=dict(requests=[query])
        receipt=dict(self.receipt,request_id=query['request_id'],raw_path='incremental.gz',sha256=digest(raw),
                     query_sha256=plan_digest(query))
        state=self.root/'freshness';run=state/'runs/fixture';run.mkdir(parents=True)
        (run/'manifest.json').write_text(json.dumps(plan))
        (run/'UPDATE_LEDGER.json').write_text(json.dumps(dict(manifest_sha256=plan_digest(plan),requests={query['request_id']:receipt})))
        result=self.load(incremental_state=state)
        self.assertEqual([r['date'] for r in result['bars'][KEY]],['2026-11-02','2026-11-03'])
        self.assertEqual(result['inventory'][0]['missing_dates'],['2026-11-04'])
        receipt['query_sha256']='changed'
        (run/'UPDATE_LEDGER.json').write_text(json.dumps(dict(manifest_sha256=plan_digest(plan),requests={query['request_id']:receipt})))
        with self.assertRaisesRegex(ValueError,'identity mismatch'):
            self.load(incremental_state=state)

    def test_original_unknown_receipt_is_explicit_retrospective(self):
        base=self.root/'artifacts/technical-integration-v2'
        raw=json.dumps(dict(data=dict(candles=[['2026-11-02T00:00:00+05:30',100,102,99,101,0,0]]))).encode()
        source=self.root/'artifacts/original.json';source.write_bytes(raw)
        with (base/'MARKET_DATA_PROVENANCE.csv').open('w',newline='') as stream:
            fields=['instrument_key','value','source_file','source_sha256','retrieved_at']
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
            writer.writerow(dict(instrument_key=KEY,value='101',source_file='artifacts/original.json',
                                 source_sha256=digest(raw),retrieved_at='RETAINED_UPSTOX_RECEIPT'))
        result=self.load()
        row=result['bars'][KEY][0]
        self.assertIsNone(row['observed_at'])
        self.assertFalse(row['receipt_timestamp_verified'])
        self.assertFalse(row['completion_at_receipt_verified'])
        self.assertEqual(row['original_receipt_label'],'RETAINED_UPSTOX_RECEIPT')
        self.assertEqual(result['inventory'][0]['missing_data_reason'],'')
        self.assertTrue(result['inventory'][0]['key_resolves_in_retained_response'])
        self.assertEqual(self.load(asof='2026-11-02T14:00:00+05:30')['bars'][KEY],[])

    def test_acquired_unknown_receipt_timestamp_fails(self):
        self.save([])
        self.receipt['received_at']='RETAINED_UPSTOX_RECEIPT';self.write_ledger()
        with self.assertRaisesRegex(ValueError,'Invalid acquired receipt timestamp'):
            self.load()
