"""Candle shutdown drains committed work; wake cannot invent storage or scope."""
import fcntl
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch

from . import control,rollout,storage,acquire,repair_queue
from ops.host import historical_candle_lifecycle as host


class LifecycleTests(unittest.TestCase):
    def test_pause_during_fetch_finishes_receipt_and_resumes_without_duplicate_get(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);root=path/'archive';root.mkdir();pause=path/'pause.json'
            a=Mock();a.root=root;a.mount=path;a.info={'VolumeUUID':'fixture'}
            database=root/'inventory.sqlite3';a.db=sqlite3.connect(database)
            a.db.execute('''CREATE TABLE requests(request_id TEXT PRIMARY KEY,company_id INTEGER,instrument_key TEXT,
                unit TEXT,interval INTEGER,from_date TEXT,to_date TEXT,status TEXT,attempts INTEGER DEFAULT 0,
                raw_sha256 TEXT,raw_path TEXT,received_at TEXT,http_status INTEGER,row_count INTEGER,error_kind TEXT,elapsed_seconds REAL)''')
            a.db.execute('CREATE TABLE datasets(request_id TEXT,definition TEXT,parquet_path TEXT,parquet_sha256 TEXT)')
            a.raw=lambda raw:storage.Archive.raw(a,raw);qs=[]
            for cid in (1,2):
                q=dict(company_id=cid,instrument_key=f'NSE_EQ|FIXTURE{cid}',unit='days',interval=1,timeframe='Daily',
                    from_date='2026-10-30',to_date='2026-10-30',role='TEST')
                q['request_id']=storage.sha(json.dumps(q,sort_keys=True).encode());qs.append(q)
            storage.dump(path/'operation-manifest.json',dict(companies=[dict(company_id=q['company_id'],instrument_key=q['instrument_key']) for q in qs],requests=qs))
            raw=b'{"status":"success","data":{"candles":[["2026-10-30T00:00:00+05:30",10,12,9,11,7,0]]}}'
            def fetch(q,token,retain_error_body=False):
                storage.dump(path/'transport-receipts'/(q['request_id']+'.json'),[dict(transport_exit=0)])
                if q['company_id']==1:storage.dump(pause,dict(reason='TEST_SHUTDOWN'))
                return raw,200
            with patch.object(rollout,'EVIDENCE',path),patch.object(rollout,'Archive',return_value=a),\
                 patch.object(acquire,'OUT',path),patch.object(acquire,'secure_supplier',return_value='synthetic-token'),\
                 patch.object(acquire,'fetch',side_effect=fetch) as provider,patch.object(repair_queue,'refresh'),\
                 patch.object(control,'PAUSE',pause),patch.object(control,'SHARED_PAUSE',path/'shared-pause.json'):
                with self.assertRaises(control.PauseRequested):rollout.execute('operation')
                self.assertEqual(provider.call_count,1)
                db=sqlite3.connect(database)
                self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')
                self.assertEqual(db.execute('SELECT status,row_count,attempts FROM requests').fetchone(),('SUCCESS',1,1));db.close()
                receipt=json.loads((root/'receipts'/(qs[0]['request_id']+'.json')).read_text())
                self.assertEqual(gzip.decompress((root/receipt['raw_path']).read_bytes()),raw)
                self.assertEqual(json.loads((path/'operation-execution.json').read_text())['status'],'PAUSED')
                pause.unlink();a.db=sqlite3.connect(database)
                result=rollout.execute('operation')
                self.assertEqual(result['status'],'MANIFEST_PASS_FINISHED')
                self.assertEqual(provider.call_count,2)
                self.assertEqual(result['runs'][-1]['reused'],1)

    def test_pause_marker_prevents_any_provider_or_archive_open(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);pause=path/'pause';pause.touch()
            q=dict(company_id=1,instrument_key='NSE_EQ|FIXTURE',unit='days',interval=1,timeframe='Daily',from_date='2026-10-30',to_date='2026-10-30',role='TEST')
            q['request_id']=storage.sha(json.dumps(q,sort_keys=True).encode())
            storage.dump(path/'operation-manifest.json',dict(companies=[dict(company_id=1,instrument_key=q['instrument_key'])],requests=[q]))
            with patch.object(rollout,'EVIDENCE',path),patch.object(control,'PAUSE',pause),\
                 patch.object(rollout,'Archive') as archive,patch.object(acquire,'fetch') as provider:
                with self.assertRaises(control.PauseRequested):rollout.execute('operation')
                archive.assert_not_called();provider.assert_not_called()

    def test_host_shutdown_waits_for_reader_or_writer_lock_and_never_signals(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);pause=path/'pause.json';storage.dump(path/'owner-authorization.json',{})
            entered=threading.Event();finished=threading.Event()
            def writer():
                with (path/'.acquisition-owner.lock').open('a+b') as owner:
                    fcntl.flock(owner,fcntl.LOCK_EX);entered.set()
                    while not pause.exists():time.sleep(.005)
                    time.sleep(.03);finished.set()
            thread=threading.Thread(target=writer);thread.start();self.assertTrue(entered.wait(2))
            with patch.object(host,'STATE',path),patch.object(control,'PAUSE',pause),patch.object(host,'legacy',return_value=[]),\
                 patch.object(host,'registered',return_value=None),patch.object(host.os,'kill',side_effect=AssertionError('ordinary shutdown must never signal')):
                result=host.pause(wait=2)
                self.assertTrue(finished.is_set());self.assertTrue(result['workers_drained'])
            thread.join(2)

    def test_wrong_passport_keeps_pause_and_never_spawns(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);state=path/'hcd02';state.mkdir();pause=state/'pause.json';pause.touch()
            for name in ('full-universe-manifest.json','hcd02/special-full-manifest.json'):(path/name).write_text('{}')
            digest=storage.sha(b'{}')
            storage.dump(state/'owner-authorization.json',dict(native_manifest_sha256=digest,session_repair_manifest_sha256=digest))
            with patch.object(host,'STATE',state),patch.object(host,'OUT',path),patch.object(control,'PAUSE',pause),\
                 patch.object(control,'SHARED_PAUSE',state/'shared-pause'),patch.object(host,'legacy',return_value=[]),\
                 patch.object(host,'discover',side_effect=RuntimeError('Passport identity mismatch')),patch.object(host.subprocess,'Popen') as spawn:
                with self.assertRaisesRegex(RuntimeError,'identity mismatch'):host.wake()
                self.assertTrue(pause.exists());spawn.assert_not_called()

    def test_stale_pid_does_not_identify_an_unrelated_process(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);storage.dump(path/'acquisition-worker.json',dict(state='RUNNING',pid=42,process_started='old'))
            with patch.object(host,'STATE',path),patch.object(host,'process',return_value=dict(start='different',command='python -m '+host.MODULE+' authorized-full')):
                self.assertIsNone(host.registered('acquisition'))


if __name__=='__main__':unittest.main()
