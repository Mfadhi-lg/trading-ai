"""HCD-02 regression tests: evidence-only repairs and resumable immutable storage."""
from datetime import datetime, timedelta
from decimal import Decimal
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from .access import resolve_daily, owner_day, indicator_segments, HistoricalStore, completed_session_bucket
from .calendar import day_record
from .rollout import validate_manifest, recover
from .storage import sha
from . import rollout,storage,acquire
from . import repair_queue


def row(start, **changes):
    r = dict(start=start, provider_timestamp=start, open=Decimal(10),high=Decimal(12),low=Decimal(9),
             close=Decimal(11),volume=1,open_interest=Decimal(0), quality='OHLC_VALID',
             timeframe='1-hour',instrument_key='NSE_EQ|FIXTURE',definition='UPSTOX_NATIVE',
             observed_at='2026-11-05T00:00:00+00:00',source_response_sha256='fixture',
             source_response_sha256s=['fixture'], original_provider_values=[{'provider_timestamp':start}],
             status='COMPLETED_RETROSPECTIVE',end_upper_bound=(datetime.fromisoformat(start)+timedelta(hours=1)).isoformat())
    r.update(changes)
    return r


class HCD02Tests(unittest.TestCase):
    def test_equivalent_daily_aliases_preserve_both_originals(self):
        a=row('2015-12-31T00:00:00+05:30', quality='AMBIGUOUS_DAILY_SESSION_BUCKET')
        b=row('2015-12-31T09:47:00+05:30', quality='AMBIGUOUS_DAILY_SESSION_BUCKET')
        result=resolve_daily([b,a])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['quality'],'OHLC_VALID')
        self.assertEqual(len(result[0]['original_provider_values']),2)
        self.assertEqual(a['quality'],'AMBIGUOUS_DAILY_SESSION_BUCKET')

    def test_different_daily_aliases_are_not_repaired(self):
        a=row('2015-12-31T00:00:00+05:30')
        b=row('2015-12-31T09:15:00+05:30', close=Decimal('11.01'))
        self.assertEqual(resolve_daily([a,b])[0]['quality'],'AMBIGUOUS_DAILY_SESSION_BUCKET')

    def test_same_bad_values_do_not_become_valid(self):
        a=row('2015-12-31T00:00:00+05:30',quality='QUARANTINED_OHLC')
        b=row('2015-12-31T09:15:00+05:30',quality='QUARANTINED_OHLC')
        self.assertNotEqual(resolve_daily([a,b])[0]['quality'],'OHLC_VALID')

    def test_regular_owner_final_short_bar_and_four_hour(self):
        day='2026-10-30'
        rows=[row(f'{day}T{h:02}:15:00+05:30') for h in range(9,16)]
        hourly=owner_day(rows,[],day_record(day),60)
        four=owner_day(rows,[],day_record(day),240)
        self.assertEqual([r['volume'] for r in four],[4,3])
        self.assertEqual(four[0]['end'][11:16],'13:15')
        self.assertEqual(four[1]['end'][11:16],'15:30')
        self.assertEqual(hourly[-1]['end'][11:16],'15:30')

    def test_muhurat_uses_real_open_and_close(self):
        day='2023-11-12'
        result=owner_day([row(f'{day}T18:15:00+05:30')],[],day_record(day),240)
        self.assertEqual(result[0]['end'][11:16],'19:15')

    def test_split_session_does_not_bridge_break(self):
        day='2024-03-02'; minutes=[]
        for session in day_record(day)['sessions']:
            start=datetime.fromisoformat(session['opens_at']);end=datetime.fromisoformat(session['closes_at'])
            while start<end:
                minutes.append(row(start.isoformat())); start+=timedelta(minutes=1)
        result=owner_day([],minutes,day_record(day),240)
        self.assertEqual([(r['start'][11:16],r['end'][11:16]) for r in result],[('09:15','10:00'),('11:30','12:30')])
        self.assertEqual([r['volume'] for r in result],[45,60])

    def test_invalid_minute_never_repairs_bad_hour(self):
        day='2026-10-30'
        rows=[row(f'{day}T{h:02}:15:00+05:30') for h in range(9,16)]
        rows[0]['quality']='QUARANTINED_OHLC'
        with self.assertRaises(ValueError):
            owner_day(rows,[row(f'{day}T09:15:00+05:30',quality='QUARANTINED_OHLC')],day_record(day),60)

    def test_sparse_session_not_filled(self):
        with self.assertRaises(ValueError):
            owner_day([], [row('2024-03-02T09:15:00+05:30')], day_record('2024-03-02'),60)

    def test_indicator_request_rejects_known_exclusion(self):
        with self.assertRaises(ValueError):
            indicator_segments(dict(rows=[row('2026-10-30T09:15:00+05:30')],exclusions=[{'day':'2026-10-29'}]))

    def test_manifest_identity_and_digest_checked(self):
        q=dict(company_id=1,instrument_key='NSE_EQ|FIXTURE',unit='days',interval=1,from_date='2026-10-30',to_date='2026-10-30',role='NATIVE_HISTORY',timeframe='Daily')
        q['request_id']=sha(json.dumps(q,sort_keys=True).encode())
        m=dict(companies=[dict(company_id=1,instrument_key=q['instrument_key'])],requests=[q])
        validate_manifest(m)
        q['to_date']='2026-11-01'
        with self.assertRaises(RuntimeError):validate_manifest(m)

    def test_raw_saved_recovers_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Mock();a.root=Path(directory);a.db=sqlite3.connect(':memory:')
            self.addCleanup(a.db.close)
            a.db.execute('CREATE TABLE requests(request_id TEXT,status TEXT,raw_sha256 TEXT,raw_path TEXT,received_at TEXT,attempts INTEGER,row_count INTEGER)')
            raw=b'fixture';(a.root/'raw.gz').write_bytes(gzip.compress(raw))
            a.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)',('r','RAW_SAVED',sha(raw),'raw.gz','2026-11-05T00:00:00+00:00',1,None))
            with patch('experiments.historical_candle_archive_v1.rollout.normalize',return_value=([],[])) as normal:
                self.assertTrue(recover(a,dict(request_id='r'))[0])
                self.assertEqual(a.db.execute('select status from requests').fetchone()[0],'SUCCESS')
                normal.assert_called_once()
            a.parquet.assert_called_once()

    def test_corrupt_recovery_fails_before_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Mock();a.root=Path(directory);a.db=sqlite3.connect(':memory:')
            self.addCleanup(a.db.close)
            a.db.execute('CREATE TABLE requests(request_id TEXT,status TEXT,raw_sha256 TEXT,raw_path TEXT,received_at TEXT,attempts INTEGER)')
            (a.root/'raw.gz').write_bytes(gzip.compress(b'corrupt'))
            a.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?)',('r','RAW_SAVED',sha(b'original'),'raw.gz','2026-11-05T00:00:00+00:00',1))
            with self.assertRaises(RuntimeError):recover(a,dict(request_id='r'))
            a.parquet.assert_not_called()

    def test_retrospective_and_strict_delivery_differ(self):
        store=HistoricalStore.__new__(HistoricalStore)
        r=row('2026-10-30T00:00:00+05:30',timeframe='Daily')
        store.native=Mock(return_value=([r],[]))
        retro=store.read(1,'Daily',decision_time='2026-10-31T00:00:00+05:30')
        strict=store.read(1,'Daily',mode='strict_delivery',decision_time='2026-10-31T00:00:00+05:30')
        self.assertEqual(len(retro['rows']),1)
        self.assertEqual(strict['rows'],[])
        self.assertEqual(retro['available_adjusted_versions'],[])

    def test_native_week_rejects_known_bad_daily_constituent(self):
        store=HistoricalStore.__new__(HistoricalStore)
        store.native=Mock(side_effect=[([row('2026-10-26T00:00:00+05:30',timeframe='Weekly')],[]),
                                      ([row('2026-10-30T00:00:00+05:30',quality='QUARANTINED_OHLC')],[])])
        result=store.read(1,'Weekly')
        self.assertFalse(result['rows'])
        self.assertEqual(result['exclusions'][0]['reason'],'QUARANTINED_DAILY_CONSTITUENT')

    def test_missing_whole_session_prevents_indicator_bridging(self):
        store=HistoricalStore.__new__(HistoricalStore)
        store.native=Mock(return_value=([row('2026-10-26T00:00:00+05:30',timeframe='Daily'),
                                      row('2026-10-28T00:00:00+05:30',timeframe='Daily')],[]))
        result=store.read(1,'Daily',start='2026-10-26',end='2026-10-28')
        self.assertEqual(result['exclusions'],[dict(day='2026-10-27',reason='MISSING_EXPECTED_SESSION')])
        with self.assertRaises(ValueError):indicator_segments(result)

    def test_completed_week_uses_last_real_session_without_backdating_receipt(self):
        r=row('2026-10-26T00:00:00+05:30',timeframe='Weekly')
        revised=completed_session_bucket(r)
        self.assertEqual(revised['end'],'2026-10-30T15:30:00+05:30')
        self.assertEqual(revised['observed_at'],r['observed_at'])

    def test_week_with_sunday_muhurat_finishes_on_sunday(self):
        revised=completed_session_bucket(row('2023-11-06T00:00:00+05:30',timeframe='Weekly'))
        self.assertEqual(revised['end'],'2023-11-12T19:15:00+05:30')

    def test_week_receipt_before_final_session_remains_provisional(self):
        r=row('2026-10-26T00:00:00+05:30',timeframe='Weekly',observed_at='2026-10-29T16:00:00+05:30')
        self.assertEqual(completed_session_bucket(r)['status'],'PROVISIONAL')

    def test_calendar_horizon_does_not_certify_future_week(self):
        r=row('2026-11-02T00:00:00+05:30',timeframe='Weekly',status='PROVISIONAL')
        self.assertEqual(completed_session_bucket(r),r)

    def test_rate_limit_stops_campaign_and_retains_original_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);root=path/'archive';root.mkdir()
            a=Mock();a.root=root;a.mount=path;a.info={'VolumeUUID':'fixture'}
            a.db=sqlite3.connect(root/'inventory.sqlite3')
            a.db.execute('''CREATE TABLE requests(request_id TEXT PRIMARY KEY,company_id INTEGER,instrument_key TEXT,
                unit TEXT,interval INTEGER,from_date TEXT,to_date TEXT,status TEXT,attempts INTEGER DEFAULT 0,
                raw_sha256 TEXT,raw_path TEXT,received_at TEXT,http_status INTEGER,row_count INTEGER,error_kind TEXT,elapsed_seconds REAL)''')
            a.raw=lambda raw:storage.Archive.raw(a,raw)
            qs=[]
            for cid in (1,2):
                q=dict(company_id=cid,instrument_key=f'NSE_EQ|FIXTURE{cid}',unit='days',interval=1,
                       timeframe='Daily',from_date='2026-10-30',to_date='2026-10-30',role='TEST')
                q['request_id']=sha(json.dumps(q,sort_keys=True).encode());qs.append(q)
            storage.dump(path/'operation-manifest.json',dict(companies=[dict(company_id=q['company_id'],instrument_key=q['instrument_key']) for q in qs],requests=qs))
            body=b'{"status":"error","reason":"synthetic rate limit"}'
            def fetch(q,token,retain_error_body=False):
                storage.dump(path/'transport-receipts'/(q['request_id']+'.json'),[dict(transport_exit=0)])
                return body,429
            with patch.object(rollout,'EVIDENCE',path),patch.object(rollout,'Archive',return_value=a),\
                 patch.object(acquire,'secure_supplier',return_value='synthetic-token'),\
                 patch.object(acquire,'OUT',path),patch.object(acquire,'fetch',side_effect=fetch) as transport,\
                 patch.object(repair_queue,'refresh'):
                with self.assertRaisesRegex(RuntimeError,'STOP_PROVIDER_HTTP_429'):rollout.execute('operation')
                self.assertEqual(transport.call_count,1)
            db=sqlite3.connect(root/'inventory.sqlite3')
            result=db.execute('SELECT status,raw_path FROM requests').fetchone();db.close()
            self.assertEqual(result[0],'STOPPED')
            self.assertEqual(gzip.decompress((root/result[1]).read_bytes()),body)
            state=json.loads((path/'operation-execution.json').read_text())
            self.assertEqual(state['status'],'STOPPED')

    def test_negative_volume_is_exact_quarantine_only_when_opted_in(self):
        q=dict(company_id=1,instrument_key='BSE_EQ|FIXTURE',timeframe='1-hour',unit='hours',interval=1,
               from_date='2025-02-01',to_date='2025-02-01')
        raw=json.dumps(dict(status='success',data=dict(candles=[['2025-02-01T09:15:00+05:30',10,12,9,11,-7,0]]))).encode()
        with self.assertRaises(ValueError):acquire.normalize(raw,q,'2026-11-05T00:00:00+00:00')
        rows,issues=rollout.normalize(raw,q,'2026-11-05T00:00:00+00:00')
        self.assertEqual(rows[0]['volume'],-7)
        self.assertEqual(rows[0]['quality'],'QUARANTINED_NUMERIC')
        self.assertEqual(issues[0]['kind'],'NEGATIVE_SOURCE_NUMERIC')
        self.assertEqual(rows[0]['source_response_sha256'],sha(raw))

    def test_fractional_volume_remains_a_fatal_schema_error(self):
        q=dict(company_id=1,instrument_key='BSE_EQ|FIXTURE',timeframe='1-hour',unit='hours',interval=1,
               from_date='2025-02-01',to_date='2025-02-01')
        raw=b'{"status":"success","data":{"candles":[["2025-02-01T09:15:00+05:30",10,12,9,11,1.5,0]]}}'
        with self.assertRaises(ValueError):rollout.normalize(raw,q,'2026-11-05T00:00:00+00:00')

    def test_missing_entire_week_blocks_indicator_window(self):
        store=HistoricalStore.__new__(HistoricalStore)
        store.native=Mock(side_effect=[([row('2024-01-01T00:00:00+05:30',timeframe='Weekly'),
                                        row('2024-01-15T00:00:00+05:30',timeframe='Weekly')],[]),([],[])])
        result=store.read(1,'Weekly',start='2024-01-01',end='2024-01-15')
        self.assertEqual(result['exclusions'],[dict(day='2024-01-08',reason='MISSING_EXPECTED_WEEKLY_BUCKET')])
        with self.assertRaises(ValueError):indicator_segments(result)

    def test_full_authorization_cannot_reuse_acceptance_for_different_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);raw=b'{"companies":[],"requests":[]}'
            (path/'full-universe-manifest.json').write_bytes(raw)
            storage.dump(path/'scaled-validation.json',dict(safe_for_full_manifest=True,full_manifest_sha256='older-manifest'))
            with patch.object(rollout,'OUT',path),patch.object(rollout,'EVIDENCE',path),patch.object(rollout,'Archive') as archive:
                with self.assertRaises(PermissionError):rollout.execute('full',authorize=sha(raw))
                archive.assert_not_called()

    def test_full_session_supplement_requires_completed_authorized_native_backfill(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);raw=b'{"companies":[],"requests":[],"source_manifest_sha256":"native"}'
            (path/'special-full-manifest.json').write_bytes(raw)
            storage.dump(path/'full-execution.json',dict(full_manifest_authorized=True,status='STOPPED',owner_authorized_manifest_sha256='native'))
            with patch.object(rollout,'EVIDENCE',path),patch.object(rollout,'Archive') as archive:
                with self.assertRaises(PermissionError):rollout.execute('special-full',authorize=sha(raw))
                archive.assert_not_called()

    def test_zero_placeholder_remains_quarantined_and_is_not_positive_price_corruption(self):
        q=dict(company_id=1,instrument_key='NSE_EQ|FIXTURE',timeframe='1-hour',unit='hours',interval=1,
               from_date='2023-04-19',to_date='2023-04-19')
        raw=b'{"status":"success","data":{"candles":[["2023-04-19T09:15:00+05:30",0,0,0,0,0,0]]}}'
        rows,_=rollout.normalize(raw,q,'2026-11-05T00:00:00+00:00')
        self.assertTrue(rollout.zero_placeholder(rows[0]))
        self.assertEqual(rows[0]['quality'],'QUARANTINED_OHLC')
        self.assertFalse(rollout.zero_placeholder(dict(rows[0],volume=1)))

    def test_owner_repair_list_excludes_proven_equivalent_daily_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);a=Mock();a.root=path;a.db=sqlite3.connect(':memory:');self.addCleanup(a.db.close)
            a.db.execute('''CREATE TABLE requests(request_id TEXT,status TEXT,company_id INTEGER,instrument_key TEXT,
                unit TEXT,interval INTEGER,from_date TEXT,to_date TEXT,raw_sha256 TEXT,raw_path TEXT,received_at TEXT,error_kind TEXT)''')
            a.db.execute('CREATE TABLE datasets(request_id TEXT,definition TEXT,parquet_path TEXT,completeness TEXT,company_id INTEGER,timeframe TEXT)')
            bars=[['2015-12-31T00:00:00+05:30',10,12,9,11,7,0],['2015-12-31T09:15:00+05:30',10,12,9,11,7,0]]
            raw=json.dumps(dict(status='success',data=dict(candles=bars))).encode();(path/'raw.gz').write_bytes(gzip.compress(raw))
            a.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('r','SUCCESS',1,'NSE_EQ|FIXTURE','days',1,'2015-12-31','2015-12-31',sha(raw),'raw.gz','2026-11-05T00:00:00+00:00',None))
            a.db.execute('INSERT INTO datasets VALUES(?,?,?,?,?,?)',('r','UPSTOX_NATIVE','quarantine/fixture.parquet','CALENDAR_NOT_YET_CERTIFIED',1,'Daily'))
            manifest=dict(companies=[dict(company_id=1,symbol='FIXTURE',instrument_key='NSE_EQ|FIXTURE')],requests=[dict(request_id='r',company_id=1,timeframe='Daily')])
            with patch.object(repair_queue,'OUT',path),patch.object(repair_queue,'EVIDENCE',path):
                self.assertEqual(repair_queue.refresh(a,manifest,'TEST'),0)
                with patch.object(repair_queue,'normalize',side_effect=AssertionError('cache should be reused')):
                    self.assertEqual(repair_queue.refresh(a,manifest,'TEST'),0)
            self.assertEqual(gzip.decompress((path/'raw.gz').read_bytes()),raw)

    def test_malformed_stock_response_does_not_stop_next_stock(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);root=path/'archive';root.mkdir();a=Mock();a.root=root;a.mount=path;a.info={'VolumeUUID':'fixture'}
            a.db=sqlite3.connect(root/'inventory.sqlite3')
            a.db.execute('''CREATE TABLE requests(request_id TEXT PRIMARY KEY,company_id INTEGER,instrument_key TEXT,unit TEXT,interval INTEGER,
                from_date TEXT,to_date TEXT,status TEXT,attempts INTEGER DEFAULT0,raw_sha256 TEXT,raw_path TEXT,received_at TEXT,http_status INTEGER,
                row_count INTEGER,error_kind TEXT,elapsed_seconds REAL)'''.replace('DEFAULT0','DEFAULT 0'))
            a.raw=lambda raw:storage.Archive.raw(a,raw);qs=[]
            for cid in (1,2):
                q=dict(company_id=cid,instrument_key=f'NSE_EQ|FIXTURE{cid}',unit='days',interval=1,timeframe='Daily',
                       from_date='2026-10-30',to_date='2026-10-30',role='TEST');q['request_id']=sha(json.dumps(q,sort_keys=True).encode());qs.append(q)
            storage.dump(path/'operation-manifest.json',dict(companies=[dict(company_id=q['company_id'],instrument_key=q['instrument_key']) for q in qs],requests=qs))
            def fetch(q,token,retain_error_body=False):
                storage.dump(path/'transport-receipts'/(q['request_id']+'.json'),[dict(transport_exit=0)])
                return (b'{"broken":true}' if q['company_id']==1 else b'{"status":"success","data":{"candles":[]}}'),200
            with patch.object(rollout,'EVIDENCE',path),patch.object(rollout,'Archive',return_value=a),\
                 patch.object(acquire,'secure_supplier',return_value='synthetic-token'),patch.object(acquire,'OUT',path),\
                 patch.object(acquire,'fetch',side_effect=fetch) as transport,patch.object(repair_queue,'refresh'),patch.object(rollout.time,'sleep'):
                result=rollout.execute('operation');self.assertEqual(transport.call_count,2)
            self.assertEqual(result['status'],'FINISHED_WITH_ISOLATED_FAILURES')
            db=sqlite3.connect(root/'inventory.sqlite3');statuses=db.execute('SELECT company_id,status FROM requests ORDER BY company_id').fetchall();db.close()
            self.assertEqual(statuses,[(1,'QUARANTINED_RESPONSE'),(2,'SUCCESS')])


if __name__=='__main__':unittest.main()
