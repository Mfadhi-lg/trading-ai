"""Synthetic native live store contracts; only an explicit disposable DSN."""
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
import uuid

from scrapper.db.postgresql import PostgreSQLSettings
from scrapper.market_live.engine import LiveStore
from scrapper.market_live import repository as repo, transfer
from scrapper.market_live.replay import OPEN, KEY, calendar, frame


class SelectionTests(unittest.TestCase):
    def test_selection_fails_closed_and_offline_stays_sqlite(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for value in ({'backend':'invalid'},{'backend':'postgresql'},{'backend':'postgresql','schema':'ok','target':{}}):
                with self.assertRaises(ValueError):repo.selection({'live_store':value})
            (root/repo.MARKER).write_text('{}')
            with patch('sqlite3.connect',side_effect=AssertionError('silent fallback')):
                with self.assertRaises(RuntimeError):LiveStore(root,calendar)

    def test_source_hash_path_and_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            store=LiveStore(root,calendar)
            now=OPEN+timedelta(minutes=1)
            store.ingest(frame([(OPEN,100,102.)],now),now,{KEY});store.close()
            snapshot=root/'snapshot.sqlite3'
            report=transfer.export_snapshot(root/'live.sqlite3',snapshot,root,batch_size=1)
            self.assertEqual(report['tables']['frames']['rows'],1)
            with self.assertRaises(FileExistsError):transfer.export_snapshot(root/'live.sqlite3',snapshot,root)
            for value in (0,True,100001):
                with self.assertRaises(ValueError):transfer.transfer(None,snapshot,'valid',root,expected_sha256='bad',target={},batch_size=value)
            with self.assertRaisesRegex(RuntimeError,'Unauthorized'):
                transfer.transfer(None,snapshot,'valid',root,expected_sha256='bad',target={})
            next((root/'raw').iterdir()).write_bytes(b'corrupt')
            with transfer.read_snapshot(snapshot) as source:
                with self.assertRaisesRegex(RuntimeError,'checksum'):transfer.inspect(source,root)

    def test_sqlite_reader_cannot_ingest_or_write_files(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LiveStore(folder,calendar);store.close()
            before={p.name for p in Path(folder).iterdir()}
            reader=LiveStore(folder,calendar,lane='reader')
            try:
                self.assertEqual(reader.minutes(KEY),[])
                with self.assertRaises(PermissionError):reader.ingest(b'anything',OPEN,{KEY})
                with self.assertRaises(PermissionError):reader.publish({KEY},OPEN.date(),OPEN)
                with self.assertRaises(sqlite3.OperationalError):reader.db.execute('DELETE FROM frames')
            finally:reader.close()
            self.assertEqual(before,{p.name for p in Path(folder).iterdir()})


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'),'requires disposable PostgreSQL 16')
class NativeLiveTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        self.psycopg=psycopg;self.sql=sql
        self.settings=PostgreSQLSettings(os.environ['TRADING_AI_POSTGRESQL_TEST_DSN'])
        self.schema='live_test_'+uuid.uuid4().hex
        self.role='live_role_'+uuid.uuid4().hex
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        with psycopg.connect(self.settings.dsn,autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(self.role)))
        self.addCleanup(self.drop)
        self.target=repo.provision(self.settings,self.schema,self.root,self.role)
        url=urlsplit(self.settings.dsn)
        host=url.hostname or '127.0.0.1'
        native=urlunsplit(url._replace(netloc=self.role+'@'+host+(':'+str(url.port) if url.port else '')))
        self.env=patch.dict(os.environ,{'LIVE_TEST_DATABASE_URL':native});self.env.start();self.addCleanup(self.env.stop)
        self.config={'live_store':dict(backend='postgresql',schema=self.schema,database_url_env='LIVE_TEST_DATABASE_URL',target=self.target)}
        self.old=LiveStore(self.root,calendar)
        self.addCleanup(self.old.close)
        self.now=OPEN+timedelta(minutes=2)
        self.old.ingest(frame([(OPEN,100,102.)],self.now),self.now,{KEY})
        self.snapshot=self.root/'snapshot.sqlite3'
        self.report=transfer.export_snapshot(self.root/'live.sqlite3',self.snapshot,self.root)
        self.old.close()
        self.reference=LiveStore(self.root/'reference',calendar)
        self.addCleanup(self.reference.close)
        self.reference.ingest(frame([(OPEN,100,102.)],self.now),self.now,{KEY})

    def drop(self):
        with self.psycopg.connect(self.settings.dsn,autocommit=True) as admin:
            admin.execute(self.sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(self.sql.Identifier(self.schema)))
            admin.execute(self.sql.SQL('DROP OWNED BY {}').format(self.sql.Identifier(self.role)))
            admin.execute(self.sql.SQL('DROP ROLE {}').format(self.sql.Identifier(self.role)))

    def run_transfer(self,**kwargs):
        return transfer.transfer(self.settings,self.snapshot,self.schema,self.root,expected_sha256=self.report['snapshot_sha256'],target=self.target,batch_size=1,**kwargs)

    def select(self,backend):
        return transfer.select_target(self.settings,self.snapshot,self.schema,self.root,expected_sha256=self.report['snapshot_sha256'],target=self.target,backend=backend)

    def test_transfer_interrupt_retry_raw_immutability_and_rollback(self):
        before=transfer.file_sha256(self.root/'live.sqlite3')
        def interrupt(*args):raise RuntimeError('injected')
        with self.assertRaisesRegex(RuntimeError,'injected'):self.run_transfer(progress=interrupt)
        with self.psycopg.connect(self.settings.dsn) as admin:
            self.assertEqual(admin.execute('SELECT count(*) FROM '+self.schema+'.frames').fetchone()[0],0)
        result=self.run_transfer();self.assertEqual(result,self.run_transfer())
        self.select('postgresql')
        with patch('sqlite3.connect',side_effect=AssertionError('native SQLite access')):
            store=LiveStore(self.root,calendar,config=self.config)
            self.assertEqual(store.minutes(KEY),self.reference.minutes(KEY));store.close()
        self.select('sqlite');self.assertFalse((self.root/repo.MARKER).exists())
        self.assertEqual(before,transfer.file_sha256(self.root/'live.sqlite3'))
        raw=next((self.root/'raw').iterdir());raw.write_bytes(b'bad')
        with self.assertRaisesRegex(RuntimeError,'checksum'):self.run_transfer()

    def test_native_duplicates_reorder_rest_projection_transactions_ids(self):
        self.select('postgresql')
        store=LiveStore(self.root,calendar,config=self.config);self.addCleanup(store.close)
        for start,vol,at in [(OPEN,100,self.now),(OPEN,50,OPEN+timedelta(seconds=30)),(OPEN+timedelta(minutes=1),200,self.now)]:
            raw=frame([(start,vol,102.)],at)
            store.ingest(raw,self.now,{KEY});self.reference.ingest(raw,self.now,{KEY})
        raw=b'{"status":"success","data":{"candles":[["2026-11-13T09:16:00+05:30",100.000000000000000001,105,99,102,201,0]]}}'
        for value in (store,self.reference):value.ingest_rest(raw,self.now,KEY,OPEN.date())
        self.assertEqual(store.minutes(KEY),self.reference.minutes(KEY))
        self.assertEqual(store.chart(KEY,OPEN.date(),self.now),self.reference.chart(KEY,OPEN.date(),self.now))
        self.assertEqual(store.indicators(KEY,OPEN.date(),self.now),self.reference.indicators(KEY,OPEN.date(),self.now))
        self.assertEqual(store.minutes(KEY)[1]['open'],'100.000000000000000001')
        count=store.db.execute('SELECT count(*) FROM frames').fetchone()[0]
        with self.assertRaises(ValueError):store.ingest(frame([(OPEN,1,102.),(OPEN,2,102.)],self.now),self.now,{KEY})
        self.assertEqual(count,store.db.execute('SELECT count(*) FROM frames').fetchone()[0])
        ids=[r[0] for r in store.db.execute('SELECT id FROM frames ORDER BY id')]
        self.assertEqual(len(ids),len(set(ids)));self.assertGreater(max(ids),1)
        with self.assertRaises((RuntimeError,BlockingIOError)):LiveStore(self.root,calendar,config=self.config)
        with self.assertRaises((RuntimeError,BlockingIOError)):self.select('sqlite')
        store.close()
        with self.assertRaisesRegex(RuntimeError,'content mismatch'):self.select('sqlite')

    def test_concurrent_reader_recovery_owner_and_target_drift(self):
        self.select('postgresql')
        writer=LiveStore(self.root,calendar,config=self.config);self.addCleanup(writer.close)
        results=[]
        def read():
            store=LiveStore(self.root,calendar,config=self.config,lane='reader')
            try:results.append(store.minutes(KEY))
            finally:store.close()
        thread=threading.Thread(target=read);thread.start();thread.join(10)
        self.assertEqual(results,[writer.minutes(KEY)])
        recovery=LiveStore(self.root,calendar,config=self.config,lane='recovery');recovery.close()
        with self.psycopg.connect(self.settings.dsn,autocommit=True) as admin:
            admin.execute('GRANT CREATE ON SCHEMA '+self.schema+' TO '+self.role)
        with self.assertRaisesRegex(RuntimeError,'create schema'):LiveStore(self.root,calendar,config=self.config,lane='reader')
        with self.psycopg.connect(self.settings.dsn,autocommit=True) as admin:
            admin.execute('REVOKE CREATE ON SCHEMA '+self.schema+' FROM '+self.role)
            admin.execute('DROP INDEX '+self.schema+'.observation_lookup')
        with self.assertRaisesRegex(RuntimeError,'schema mismatch'):LiveStore(self.root,calendar,config=self.config,lane='reader')

    def test_service_reconnect_and_recovery_use_native_selection(self):
        from scrapper.market_live.service import stream
        from scrapper.tests.test_market_live import grant
        self.select('postgresql')
        class Stop:
            def is_set(self):return False
            def wait(self,_):pass
        class Socket:
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def send(self,_):pass
            def recv(self,**_):raise ConnectionError('synthetic reconnect')
        config=dict(self.config,state_dir=str(self.root),calendar_path='unused',max_reconnects=2,**grant())
        with patch('scrapper.market_live.service.load_calendar',return_value=calendar),patch('scrapper.market_live.service.load',return_value=({'version':'v1'},{KEY:{}})),patch('sqlite3.connect',side_effect=AssertionError('SQLite fallback')):
            stream(config,Stop(),connector=Socket,clock=lambda:OPEN)
        status=json.loads((self.root/'stream_status.json').read_text())
        self.assertEqual(status['reconnect_attempts'],2)

    def test_full_synthetic_native_replay_projection_parity(self):
        from scrapper.market_live.replay import run
        root=self.root/'native-replay';root.mkdir()
        schema=self.schema+'_replay'
        target=repo.provision(self.settings,schema,root,self.role)
        def cleanup():
            with self.psycopg.connect(self.settings.dsn,autocommit=True) as admin:
                admin.execute('DROP SCHEMA '+schema+' CASCADE')
        self.addCleanup(cleanup)
        (root/repo.MARKER).write_text(json.dumps(target))
        config={'live_store':dict(self.config['live_store'],schema=schema,target=target)}
        with patch('sqlite3.connect',side_effect=AssertionError('native replay opened SQLite')):
            result=run(root,config=config)
        reference=self.root/'sqlite-replay';run(reference)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(json.loads((root/'chart.json').read_text()),json.loads((reference/'chart.json').read_text()))

    def test_rollback_keeps_native_fence_until_marker_removed(self):
        self.select('postgresql')
        original=Path.unlink
        attempted=[]
        def unlink(path,*args,**kwargs):
            if path.name == repo.MARKER:
                with self.assertRaisesRegex(RuntimeError,'busy'):
                    repo.NativeRepository(self.root,self.config)
                attempted.append(True)
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink):self.select('sqlite')
        self.assertEqual(attempted,[True])

    def test_native_recovery_replays_saved_rest_without_sqlite(self):
        from scrapper.market_live.recovery import recover
        from scrapper.tests.test_market_live import grant
        self.select('postgresql')
        config=dict(self.config,state_dir=str(self.root),calendar_path='unused',provider_quota_path=str(self.root/'quota.json'),recovery_days=1,**grant())
        raw=json.dumps(dict(status='success',data=dict(candles=[[(OPEN+timedelta(minutes=1)).isoformat(),100,105,99,102,200,0]]))).encode()
        with patch('scrapper.market_live.recovery.load_calendar',return_value=calendar),patch('sqlite3.connect',side_effect=AssertionError('recovery opened SQLite')):
            result=recover(config,{KEY},self.now,threading.Event(),supplier=lambda:'TOKEN',fetch=lambda *_:(raw,200),clock=lambda:self.now)
            self.assertEqual(result['requests'],1)
            result=recover(config,{KEY},self.now,threading.Event(),supplier=lambda:self.fail('repeated request'),clock=lambda:self.now)
            self.assertEqual(result['requests'],0)

    def test_sequence_permissions_and_definition_are_verified(self):
        self.select('postgresql')
        with self.psycopg.connect(self.settings.dsn,autocommit=True) as admin:
            admin.execute('GRANT UPDATE ON SEQUENCE '+self.schema+'.frames_id_seq TO '+self.role)
        with self.assertRaisesRegex(RuntimeError,'reset generated IDs'):
            LiveStore(self.root,calendar,config=self.config)
        with self.psycopg.connect(self.settings.dsn,autocommit=True) as admin:
            admin.execute('REVOKE UPDATE ON SEQUENCE '+self.schema+'.frames_id_seq FROM '+self.role)
            admin.execute('ALTER SEQUENCE '+self.schema+'.frames_id_seq INCREMENT BY 2')
        with self.assertRaisesRegex(RuntimeError,'schema mismatch'):
            LiveStore(self.root,calendar,config=self.config)

    def test_changed_raw_during_transfer_rolls_back(self):
        raw=next((self.root/'raw').iterdir());original=raw.read_bytes()
        def mutate(*_):raw.write_bytes(b'changed during import')
        try:
            with self.assertRaisesRegex(RuntimeError,'checksum'):self.run_transfer(progress=mutate)
            with self.psycopg.connect(self.settings.dsn) as admin:
                self.assertEqual(admin.execute('SELECT count(*) FROM '+self.schema+'.frames').fetchone()[0],0)
        finally:raw.write_bytes(original)
        self.run_transfer()

    def test_column_update_privileges_rejected(self):
        self.select('postgresql')
        with self.psycopg.connect(self.settings.dsn,autocommit=True) as admin:
            admin.execute('GRANT UPDATE(payload) ON '+self.schema+'.observations TO '+self.role)
        with self.assertRaisesRegex(RuntimeError,'column permissions'):
            LiveStore(self.root,calendar,config=self.config)
