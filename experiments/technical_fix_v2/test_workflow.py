"""Filesystem/provider-boundary workflow tests, not duplicate indicator formulas."""
from datetime import datetime,timedelta
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch

from experiments.historical_candle_archive_v1 import storage,access
from experiments.historical_candle_archive_v1.calendar import day_record
from . import update
from .integrate import chart_indicators


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name);self.output=self.path/'output'
        self.discovery=patch.object(storage,'discover',return_value=(self.path,{'VolumeUUID':'fixture'}));self.discovery.start();self.addCleanup(self.discovery.stop)
        self.guard=patch.object(storage,'verify');self.guard.start();self.addCleanup(self.guard.stop)
        for module in (access,):
            p=patch.object(module,'discover',return_value=(self.path,{'VolumeUUID':'fixture'}));p.start();self.addCleanup(p.stop)
            p=patch.object(module,'verify');p.start();self.addCleanup(p.stop)

    def manifest(self,queries):
        path=self.path/'manifest.json';storage.dump(path,dict(requests=queries))
        return dict(authorize=storage.sha(path.read_bytes()),expected_digest=storage.sha(path.read_bytes()),
            manifest_path=path,output=self.output,pace=0,supplier=Mock(return_value='fixture-token'))

    def query(self,ident='stock',**changes):
        r=dict(request_id=ident,kind='STOCK',series='FIXTURE',company_id=1,instrument_key='NSE_EQ|FIXTURE',
            timeframe='Daily',unit='days',interval=1,from_date='2026-10-30',to_date='2026-10-30',url='https://fixture.invalid')
        r.update(changes);return r

    def body(self,rows):return json.dumps(dict(status='success',data=dict(candles=rows))).encode()

    def test_hcd_unknown_delivery_and_saved_error_cannot_be_refetched(self):
        from experiments.historical_candle_archive_v1.rollout import recover
        a=storage.Archive()
        try:
            a.db.execute("INSERT INTO requests(request_id,status,attempts) VALUES ('uncertain','IN_FLIGHT',1)")
            a.db.execute("INSERT INTO requests(request_id,status,attempts,raw_path) VALUES ('error','STOPPED',1,'retained-error.json.gz')")
            a.db.commit()
            with self.assertRaisesRegex(RuntimeError,'UNCERTAIN_PROVIDER_DELIVERY'):recover(a,dict(request_id='uncertain'))
            with self.assertRaisesRegex(RuntimeError,'RETAINED_RESPONSE_REQUIRES_REVIEW'):recover(a,dict(request_id='error'))
        finally:a.db.close();a.writer_lock.close()

    def test_hcd_commit_failure_after_response_recovers_receipt_without_second_get(self):
        from experiments.historical_candle_archive_v1 import rollout,repair_queue,acquire
        q=self.query();q['request_id']=storage.sha(json.dumps({k:v for k,v in q.items() if k!='request_id'},sort_keys=True).encode())
        evidence=self.path/'evidence';evidence.mkdir()
        storage.dump(evidence/'operation-manifest.json',dict(companies=[dict(company_id=1,instrument_key=q['instrument_key'])],requests=[q]))
        original=storage.Archive
        first=True
        def factory():
            nonlocal first
            a=original()
            if first:
                first=False;db=a.db;proxy=Mock(wraps=db);commits=0
                def commit():
                    nonlocal commits
                    commits+=1
                    if commits==2:
                        db.rollback()
                        raise sqlite3.OperationalError('database is locked')
                    return db.commit()
                proxy.commit.side_effect=commit;a.db=proxy
            return a
        raw=self.body([['2026-10-30T00:00:00+05:30',10,12,9,11,100,0]])
        def fetch(query,token,**kw):
            storage.dump(evidence/'transport-receipts'/(query['request_id']+'.json'),[dict(transport_exit=0)])
            return raw,200
        with patch.object(rollout,'EVIDENCE',evidence),patch.object(rollout,'Archive',side_effect=factory),patch.object(acquire,'OUT',evidence),patch.object(acquire,'secure_supplier',return_value='fixture'),patch.object(acquire,'fetch',side_effect=fetch) as network,patch.object(repair_queue,'refresh'):
            with self.assertRaises(sqlite3.OperationalError):rollout.execute('operation')
            result=rollout.execute('operation')
            self.assertEqual(network.call_count,1)
            self.assertEqual(result['status'],'MANIFEST_PASS_FINISHED')
        s=access.HistoricalStore()
        try:
            self.assertEqual(s.db.execute('SELECT status FROM requests').fetchone()[0],'SUCCESS')
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM candle_observations').fetchone()[0],1)
        finally:s.close()

    def test_raw_interruption_recovers_real_sqlite_and_parquet_without_second_get(self):
        options=self.manifest([self.query()]);fetch=Mock(return_value=(self.body([['2026-10-30T00:00:00+05:30',10,12,9,11,100,0]]),200))
        with self.assertRaises(InterruptedError):update.execute(**options,fetch=fetch,interrupt_after_raw=True)
        state=update.execute(**options,fetch=fetch)
        self.assertEqual(fetch.call_count,1);self.assertEqual(state['status'],'COMPLETE')
        s=access.HistoricalStore()
        try:
            self.assertEqual(s.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            r=s.read(1,'Daily',start='2026-10-30',end='2026-10-30',decision_time='2026-10-31T00:00:00+05:30')
            self.assertEqual(len(r['rows']),1);self.assertEqual(r['rows'][0]['close'],11)
            strict=s.read(1,'Daily',start='2026-10-30',end='2026-10-30',decision_time='2026-10-31T00:00:00+05:30',mode='strict_delivery')
            self.assertFalse(strict['rows'])
            import pyarrow.parquet as pq
            path,digest=s.db.execute('SELECT parquet_path,parquet_sha256 FROM datasets').fetchone()
            self.assertEqual(storage.sha((s.root/path).read_bytes()),digest)
            self.assertEqual(pq.read_table(s.root/path).to_pylist()[0]['close'],11)
        finally:s.close()

    def test_developing_four_hour_prefix_never_reads_later_minute_values(self):
        start=datetime.fromisoformat('2026-10-30T13:15:00+05:30')
        rows=[[(start+timedelta(minutes=i)).isoformat(),10,12 if i<45 else 9999,9,11,1,0] for i in range(60)]
        q=self.query(unit='minutes',timeframe='1-minute')
        update.execute(**self.manifest([q]),fetch=Mock(return_value=(self.body(rows),200)))
        s=access.HistoricalStore()
        try:
            r=s.developing(1,'4-hour',decision_time='2026-10-30T14:00:00+05:30')
            self.assertFalse(r['exclusions']);self.assertEqual(len(r['rows']),1)
            bar=r['rows'][0];self.assertEqual(bar['high'],12);self.assertEqual(bar['volume'],45)
            self.assertEqual(bar['end'][11:16],'15:30');self.assertEqual(bar['status'],'PROVISIONAL')
            self.assertTrue(all(v['provider_timestamp']<'2026-10-30T14:00:00+05:30' for v in bar['original_provider_values']))
            self.assertFalse(s.developing(1,'4-hour',decision_time='2026-10-30T14:00:00+05:30',mode='strict_delivery')['rows'])
        finally:s.close()

    def test_authentication_failure_stops_before_next_instrument_and_resume_uses_error_receipt(self):
        qs=[self.query('one',kind='GLOBAL'),self.query('two',kind='GLOBAL')]
        options=self.manifest(qs);fetch=Mock(return_value=(b'{"status":"error","errors":[]}',401))
        with self.assertRaisesRegex(RuntimeError,'STOP_HTTP_401'):update.execute(**options,fetch=fetch)
        self.assertEqual(fetch.call_count,1)
        with self.assertRaisesRegex(RuntimeError,'STOP_UNRESOLVED_HTTP_RECEIPT'):update.execute(**options,fetch=fetch)
        self.assertEqual(fetch.call_count,1)

    def test_actual_receipt_gated_prefix_stops_at_last_completed_received_minute(self):
        start=datetime.fromisoformat('2026-10-30T13:15:00+05:30')
        rows=[[(start+timedelta(minutes=i)).isoformat(),10,12 if i<44 else 9999,9,11,1,0] for i in range(60)]
        with patch.object(update,'now',return_value='2026-10-30T13:59:30+05:30'):
            update.execute(**self.manifest([self.query(unit='minutes',timeframe='1-minute')]),fetch=Mock(return_value=(self.body(rows),200)))
        s=access.HistoricalStore()
        try:
            r=s.developing(1,'4-hour',decision_time='2026-10-30T14:00:00+05:30',mode='strict_delivery')
            self.assertEqual(r['rows'][0]['volume'],44);self.assertEqual(r['rows'][0]['high'],12)
            self.assertEqual(r['rows'][0]['observed_at'],'2026-10-30T13:59:30+05:30')
            self.assertEqual(r['rows'][0]['prefix_through'],'2026-10-30T13:58:00+05:30')
        finally:s.close()

    def test_live_hourly_snapshot_admits_completed_morning_without_future_afternoon_grid(self):
        rows=[[f'2026-10-30T{h:02}:15:00+05:30',10,12,9,11,10,0] for h in range(9,14)]
        with patch.object(update,'now',return_value='2026-10-30T13:20:00+05:30'):
            update.execute(**self.manifest([self.query(unit='hours',timeframe='1-hour')]),fetch=Mock(return_value=(self.body(rows),200)))
        s=access.HistoricalStore()
        try:
            r=s.read(1,'4-hour',start='2026-10-30',end='2026-10-30',mode='strict_delivery',decision_time='2026-10-30T13:20:00+05:30')
            self.assertFalse(r['exclusions']);self.assertEqual(len(r['rows']),1)
            self.assertEqual(r['rows'][0]['end'],'2026-10-30T13:15:00+05:30')
            self.assertEqual(r['rows'][0]['volume'],40)
        finally:s.close()

    def test_uncertain_transport_delivery_never_automatically_repeats_get(self):
        options=self.manifest([self.query()]);fetch=Mock(side_effect=RuntimeError('STOP_TRANSPORT_EXIT_6'))
        with self.assertRaisesRegex(RuntimeError,'STOP_TRANSPORT_EXIT_6'):update.execute(**options,fetch=fetch)
        with self.assertRaisesRegex(RuntimeError,'STOP_UNCERTAIN_PROVIDER_DELIVERY'):update.execute(**options,fetch=fetch)
        self.assertEqual(fetch.call_count,1)

    def test_invalid_catalog_key_isolated_without_substitution_or_repeat(self):
        qs=[self.query('one',kind='NSE_INDEX'),self.query('two',kind='NSE_INDEX')]
        options=self.manifest(qs)
        fetch=Mock(side_effect=[(b'{"status":"error","errors":[{"errorCode":"UDAPI1021"}]}',400),(self.body([['2026-10-30T00:00:00+05:30',10,12,9,11,0,0]]),200)])
        state=update.execute(**options,fetch=fetch);self.assertEqual(fetch.call_count,2)
        self.assertEqual(state['requests']['one']['status'],'ISOLATED_INSTRUMENT_REJECTION')
        update.execute(**options,fetch=fetch);self.assertEqual(fetch.call_count,2)

    def test_unapproved_manifest_never_opens_archive_or_reads_credentials(self):
        options=self.manifest([self.query()]);options['authorize']='wrong'
        factory=Mock()
        with self.assertRaises(PermissionError):update.execute(**options,archive_factory=factory)
        factory.assert_not_called();options['supplier'].assert_not_called()

    def test_closed_checkpoint_and_muhurat_are_not_weekday_guesses(self):
        self.assertEqual(day_record('2026-11-08')['status'],'SPECIAL_LIVE_SECURITY_GRID_UNRESOLVED')
        self.assertFalse(day_record('2026-11-08')['sessions'])
        self.assertIn('CMTR76941.pdf',day_record('2026-11-08')['source_refs'][-1])
        r=dict(start='2026-11-02T00:00:00+05:30',timeframe='Weekly',instrument_key='NSE_EQ|FIXTURE')
        # Future special Sunday prevents certifying this week's close on Friday.
        self.assertIs(access.completed_session_bucket(r),r)

    def test_scheduled_postclose_runs_real_updater_and_duplicate_tick_reuses_checkpoint(self):
        from ops.host.technical_market_updates import tick
        options=self.manifest([self.query()]);self.output.mkdir()
        storage.dump(self.output/'OWNER_AUTHORIZATION.json',dict(manifest_sha256=options['authorize']))
        fetch=Mock(return_value=(self.body([['2026-10-30T00:00:00+05:30',10,12,9,11,100,0]]),200))
        def runner(**kwargs):return update.execute(**kwargs,fetch=fetch,supplier=options['supplier'],pace=0)
        kwargs=dict(output=self.output,manifest=options['manifest_path'],expected_digest=options['expected_digest'],runner=runner)
        before=tick(datetime.fromisoformat('2026-10-30T14:00:00+05:30'),**kwargs)
        self.assertEqual(before['status'],'INTRADAY_SNAPSHOT_SCOPE_NOT_AUTHORIZED');fetch.assert_not_called()
        now=datetime.fromisoformat('2026-10-30T16:00:00+05:30')
        result=tick(now,**kwargs);self.assertEqual(result['status'],'CHECKPOINT_BATCH_COMPLETE')
        self.assertEqual(tick(now,**kwargs),result);self.assertEqual(fetch.call_count,1)
        self.assertFalse(result['historical_receipts_backdated'])
        expired=tick(datetime.fromisoformat('2026-11-02T16:00:00+05:30'),**kwargs)
        self.assertEqual(expired['status'],'BOUNDED_WINDOW_EXHAUSTED_NEW_MANIFEST_REQUIRED');self.assertEqual(fetch.call_count,1)


if __name__=='__main__':unittest.main(verbosity=2)
