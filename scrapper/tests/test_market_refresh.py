"""Offline incremental-refresh contracts; synthetic bytes and temporary stores."""
from datetime import date, datetime, timedelta
from decimal import Decimal
import fcntl
import gzip
import json
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from experiments.historical_candle_archive_v1.storage import dump, sha
from scrapper.market_refresh.planner import coverage_report, digest, plan_refresh
from scrapper.market_refresh.runner import execute_plan
from scrapper.market_refresh.derived import VerifiedStore, refresh_outputs
from scrapper.market_refresh.derived import collect_coverage
from scrapper.market_refresh.quota import reserve

KEY = 'NSE_EQ|INE123456789'
NOW = datetime.fromisoformat('2026-11-13T17:00:00+05:30')


def calendar(day):
    day = str(day)
    closed = date.fromisoformat(day).weekday() >= 5 or day == '2026-11-10'
    return dict(verified=True, status='CLOSED' if closed else 'REGULAR_SCHEDULED', source_refs=['synthetic-calendar'],
                sessions=[] if closed else [dict(opens_at=day+'T09:15:00+05:30', closes_at=day+'T15:30:00+05:30')])


def company(key=KEY, cid=1, **kwargs):
    return dict(company_id=cid, instrument_key=key, identity_verified=True,
                identity_ref='synthetic-current-instrument-master-sha256', eligible_since='2026-11-11', **kwargs)


def source(days=('2026-11-13',), close=101):
    return json.dumps(dict(status='success', data=dict(candles=[
        [d+'T00:00:00+05:30', 100, 102, 99, close, 123, 0] for d in days]))).encode()


class RefreshPlannerTests(unittest.TestCase):
    def test_only_missing_recent_dates_and_priority_budget(self):
        a, b = company(), company('NSE_EQ|INE000000001', 2, focus=True)
        saved = {KEY: dict(validated_sessions=['2026-11-11', '2026-11-12'])}
        plan = plan_refresh([a,b], saved, calendar=calendar, now=NOW, budget=1)
        self.assertEqual(plan['requests'][0]['company_id'], 2)
        self.assertEqual(plan['deferred_requests'], 1)
        plan = plan_refresh([a], saved, calendar=calendar, now=NOW)
        self.assertEqual([(q['from_date'],q['to_date']) for q in plan['requests']], [('2026-11-13','2026-11-13')])
        self.assertNotIn('2022', plan['requests'][0]['url'])

    def test_weekend_freshness_and_holiday_not_wall_clock(self):
        saved = dict(validated_sessions=['2026-11-11','2026-11-12','2026-11-13'])
        report = coverage_report(company(), saved, calendar=calendar,
                                 now='2026-11-15T22:00:00+05:30')
        self.assertEqual(report['status'], 'FRESH')
        self.assertEqual(report['expected_latest_completed_session'], '2026-11-13')

    def test_internal_gap_differs_from_stale_tail(self):
        report = coverage_report(company(), dict(validated_sessions=['2026-11-11','2026-11-13']), calendar=calendar, now=NOW)
        self.assertEqual(report['status'],'GAP')
        self.assertEqual(report['missing_sessions'], ['2026-11-12'])
        self.assertEqual(coverage_report(company(), {}, calendar=calendar, now=NOW)['status'], 'STALE')

    def test_unknown_calendar_or_identity_cannot_plan_or_claim_fresh(self):
        uncertain = lambda day: dict(calendar(day), verified=False)
        self.assertEqual(plan_refresh([company()], {}, calendar=uncertain, now=NOW)['requests'], [])
        bad = company();bad['identity_verified']=False
        plan = plan_refresh([bad], {}, calendar=calendar, now=NOW)
        self.assertEqual(plan['coverage'][0]['status'], 'BLOCKED')

    def test_source_revision_requires_identity_and_new_request(self):
        saved = {KEY: dict(validated_sessions=['2026-11-11','2026-11-12','2026-11-13'])}
        with self.assertRaises(ValueError):
            plan_refresh([company()], saved, calendar=calendar, now=NOW, revision_dates={KEY:['2026-11-12']})
        plan = plan_refresh([company()], saved, calendar=calendar, now=NOW,
                            revision_dates={KEY:['2026-11-12']}, revision_id='revision-review-1')
        self.assertEqual(plan['requests'][0]['revision_id'], 'revision-review-1')
        self.assertEqual(plan['requests'][0]['from_date'], '2026-11-12')

    def test_older_gaps_only_when_recent_work_is_idle(self):
        complete={KEY:dict(validated_sessions=['2026-11-11','2026-11-12','2026-11-13'])}
        args=dict(calendar=calendar,now=NOW,history_idle=True,historical_gaps={KEY:['2026-11-02']})
        busy=plan_refresh([company()],{},**args)
        self.assertTrue(all(q['from_date']>='2026-11-11' for q in busy['requests']))
        idle=plan_refresh([company()],complete,**args)
        self.assertEqual(idle['requests'][0]['from_date'],'2026-11-02')
        self.assertEqual(idle['requests'][0]['priority'],4)


class FakeArchive:
    def __init__(self, root):
        self.root = root
        self.writer_lock = (root/'.writer.lock').open('a+b')
        try:
            fcntl.flock(self.writer_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.writer_lock.close()
            raise RuntimeError('STOP_ARCHIVE_WRITER_BUSY')
        self.db = sqlite3.connect(root/'inventory.sqlite3')
        self.db.execute('''CREATE TABLE IF NOT EXISTS requests(request_id TEXT PRIMARY KEY,
            company_id INTEGER,instrument_key TEXT,unit TEXT,interval INTEGER,from_date TEXT,to_date TEXT,
            status TEXT,attempts INTEGER,raw_sha256 TEXT,raw_path TEXT,received_at TEXT,http_status INTEGER,row_count INTEGER)''')
    def guard(self):
        pass
    def raw(self, raw):
        checksum=sha(raw);path=self.root/(checksum+'.gz')
        path.write_bytes(gzip.compress(raw))
        return checksum, path.name
    def parquet(self, rows, query, checksum, received):
        dump(self.root/(query['request_id']+'.normalized.json'), rows)


class RefreshExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.archive=self.root/'archive';self.archive.mkdir()
        self.state=self.root/'state'
        self.plan=plan_refresh([company()], {KEY:dict(validated_sessions=['2026-11-11','2026-11-12'])}, calendar=calendar, now=NOW)
        self.fetch=Mock(return_value=(source(),200));self.token=Mock(return_value='fixture-token')
    def run_plan(self, **kwargs):
        return execute_plan(self.plan, self.state, authorized_sha256=digest(self.plan), calendar=calendar,
                            token_supplier=self.token, fetch=self.fetch,
                            archive_factory=lambda:FakeArchive(self.archive),clock=lambda:NOW,sleep=lambda _:None,**kwargs)

    def test_incremental_request_canonical_append_and_cross_plan_dedup(self):
        db=sqlite3.connect(self.archive/'inventory.sqlite3')
        db.execute('CREATE TABLE retained_hcd02(receipt TEXT)');db.execute("INSERT INTO retained_hcd02 VALUES('unchanged')");db.commit();db.close()
        self.assertEqual(self.run_plan()['status'],'COMPLETE')
        self.plan['created_at']='2026-11-13T17:01:00+05:30'
        result=self.run_plan()
        self.assertEqual(result['status'],'COMPLETE');self.fetch.assert_called_once()
        receipt=next(iter(result['requests'].values()))
        self.assertEqual(receipt['validated_sessions'],['2026-11-13'])
        db=sqlite3.connect(self.archive/'inventory.sqlite3')
        self.assertEqual(db.execute('SELECT receipt FROM retained_hcd02').fetchone()[0], 'unchanged');db.close()

    def test_active_historical_writer_defers_before_credentials_or_fetch(self):
        owner=FakeArchive(self.archive)
        try:
            result=self.run_plan()
        finally:
            owner.db.close();owner.writer_lock.close()
        self.assertEqual(result['reason'],'HISTORICAL_ARCHIVE_WRITER_BUSY')
        self.fetch.assert_not_called();self.token.assert_not_called()

    def test_quota_exhaustion_is_durable_and_no_provider_request(self):
        dump(self.state/'quota.json',dict(calls=[NOW.timestamp()-10]*1800))
        self.assertEqual(self.run_plan()['reason'],'PROVIDER_QUOTA_EXHAUSTED')
        self.fetch.assert_not_called()

    def test_http_auth_block_does_not_save_body_or_retry(self):
        self.fetch.return_value=(b'private-error-body',401)
        self.assertEqual(self.run_plan()['reason'],'HTTP_401')
        self.assertEqual(self.run_plan()['reason'],'HTTP_401');self.fetch.assert_called_once()
        self.assertFalse(list(self.archive.glob('*.gz')))
        self.assertNotIn('private-error-body', ''.join(p.read_text() for p in self.state.rglob('*.json')))

    def test_transport_uncertainty_never_auto_retries(self):
        self.fetch.side_effect=OSError('secret failure details')
        with self.assertRaises(OSError):self.run_plan()
        result=self.run_plan()
        self.assertEqual(result['reason'],'UNCERTAIN_DELIVERY');self.fetch.assert_called_once()
        self.assertNotIn('secret failure', ''.join(p.read_text() for p in self.state.rglob('*.json')))

    def test_saved_raw_resumes_without_get_after_processing_failure(self):
        broken=Mock(side_effect=RuntimeError('processing interrupted'))
        from unittest.mock import patch
        with patch('scrapper.market_refresh.runner._persist',broken):
            with self.assertRaises(RuntimeError):self.run_plan()
        self.assertEqual(self.run_plan()['status'],'COMPLETE');self.fetch.assert_called_once()

    def test_dependent_callback_failure_replays_without_another_get(self):
        callback=Mock(side_effect=[RuntimeError('derived processing failed'),None])
        with self.assertRaises(RuntimeError):self.run_plan(on_validated=callback)
        result=self.run_plan(on_validated=callback)
        self.assertEqual(result['status'],'COMPLETE');self.fetch.assert_called_once()
        self.assertEqual(callback.call_count,2)
        self.assertEqual(next(iter(result['requests'].values()))['derived_status'],'COMPLETE')

    def test_invalid_numeric_quarantined_not_fresh(self):
        self.fetch.return_value=(source(close=200),200)
        result=self.run_plan();receipt=next(iter(result['requests'].values()))
        self.assertEqual(receipt['accepted'],0);self.assertEqual(receipt['quarantined'],1)
        self.assertEqual(receipt['status'],'EMPTY')

    def test_duplicate_worker_lock(self):
        self.state.mkdir()
        with (self.state/'.refresh.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.run_plan()['reason'],'INCREMENTAL_WORKER_BUSY')
        self.fetch.assert_not_called()

    def test_changed_url_refused_before_credentials(self):
        self.plan['requests'][0]['url']='https://outside.invalid/private'
        with self.assertRaisesRegex(ValueError,'canonical Upstox'):
            self.run_plan()
        self.token.assert_not_called();self.fetch.assert_not_called()

    def test_successful_raw_corruption_is_not_reused_as_success(self):
        self.run_plan()
        next(self.archive.glob('*.gz')).write_bytes(gzip.compress(b'changed'))
        with self.assertRaisesRegex(ValueError,'checksum mismatch'):
            self.run_plan()
        self.fetch.assert_called_once()

    def test_stop_after_token_handoff_prevents_network_without_uncertain_receipt(self):
        check=Mock(side_effect=[True,False])
        result=self.run_plan(admission_check=check)
        self.assertEqual(result['reason'],'ADMISSION_STOPPED')
        self.assertEqual(result['requests'],{});self.fetch.assert_not_called()
        self.assertFalse(list((self.state/'receipts').glob('*.json')))


class RefreshServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.archive=self.root/'archive';self.archive.mkdir()
        initial=FakeArchive(self.archive);initial.db.close();initial.writer_lock.close()
        self.calendar_path=self.root/'calendar.json'
        days=[dict(calendar(date(2026,11,3)+timedelta(days=i)),day=str(date(2026,11,3)+timedelta(days=i))) for i in range(11)]
        dump(self.calendar_path,dict(days=days))
        self.config_path=self.root/'config.json'
        dump(self.config_path,dict(owner_approved=True,activation_ref='synthetic-owner-fixture',
            expires_at='2026-11-14T00:00:00+05:30',calendar_path=str(self.calendar_path),
            calendar_sha256=sha(self.calendar_path.read_bytes()),state_dir=str(self.root/'state'),
            provider_quota_path=str(self.root/'provider-quota.json'),
            companies=[company()],max_requests=1))
    def store_factory(self):
        from experiments.historical_candle_archive_v1.access import HistoricalStore
        store=HistoricalStore.__new__(HistoricalStore)
        store.root=self.archive
        store.db=sqlite3.connect(f'file:{self.archive}/inventory.sqlite3?mode=ro',uri=True)
        store.db.row_factory=sqlite3.Row
        return store
    def test_owner_gate_before_store_or_provider(self):
        from scrapper.market_refresh.service import refresh
        factory=Mock()
        with self.assertRaises(PermissionError):
            refresh(self.config_path,NOW,authorized_sha256='wrong',store_factory=factory)
        factory.assert_not_called()
    def test_full_incremental_tick_reconciles_canonical_coverage(self):
        from scrapper.market_refresh.service import refresh
        fetch=Mock(return_value=(source(('2026-11-11','2026-11-12','2026-11-13')),200))
        kwargs=dict(authorized_sha256=sha(self.config_path.read_bytes()),store_factory=self.store_factory,
                    archive_factory=lambda:FakeArchive(self.archive),token_supplier=lambda:'fixture-token',
                    fetch=fetch,sleep=lambda _:None,clock=lambda:NOW)
        result=refresh(self.config_path,NOW,**kwargs)
        self.assertEqual(result['status'],'COMPLETE');self.assertEqual(result['counts'],{'FRESH':1})
        self.assertEqual(result['missing_sessions'],0)
        result=refresh(self.config_path,NOW,**kwargs)
        self.assertEqual(result['counts'],{'FRESH':1});fetch.assert_called_once()
        self.assertEqual(result['coverage'][0]['latest_validated_daily'],'2026-11-13')

    def test_changed_calendar_refused_before_store(self):
        from scrapper.market_refresh.service import refresh
        self.calendar_path.write_text('{"days":[]}')
        factory=Mock()
        with self.assertRaisesRegex(PermissionError,'calendar hash'):
            refresh(self.config_path,NOW,authorized_sha256=sha(self.config_path.read_bytes()),store_factory=factory)
        factory.assert_not_called()

    def test_whole_tick_collision_does_not_open_store(self):
        from scrapper.market_refresh.service import refresh
        state=self.root/'state';state.mkdir()
        factory=Mock()
        with (state/'.refresh-service.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=refresh(self.config_path,NOW,authorized_sha256=sha(self.config_path.read_bytes()),store_factory=factory)
        self.assertEqual(result['reason'],'REFRESH_SERVICE_BUSY');factory.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec('pyarrow'), 'PyArrow required for actual canonical Parquet integration')
    def test_actual_archive_parquet_and_historical_access_refresh(self):
        from unittest.mock import patch
        from scrapper.market_refresh.service import refresh
        from experiments.historical_candle_archive_v1.storage import Archive
        from experiments.historical_candle_archive_v1.access import HistoricalStore
        mount=self.root/'synthetic-mount';mount.mkdir()
        # Only mount discovery/verification is replaced. Archive schema, locking,
        # raw writes, normalization, Parquet and canonical reads run unchanged.
        with patch('experiments.historical_candle_archive_v1.storage.discover',return_value=(mount,{})), \
             patch('experiments.historical_candle_archive_v1.storage.verify'), \
             patch('experiments.historical_candle_archive_v1.access.discover',return_value=(mount,{})), \
             patch('experiments.historical_candle_archive_v1.access.verify'):
            initial=Archive();initial.db.close();initial.writer_lock.close()
            fetch=Mock(return_value=(source(('2026-11-11','2026-11-12','2026-11-13')),200))
            kwargs=dict(authorized_sha256=sha(self.config_path.read_bytes()),store_factory=HistoricalStore,
                        archive_factory=Archive,token_supplier=lambda:'fixture-token',fetch=fetch,
                        sleep=lambda _:None,clock=lambda:NOW)
            result=refresh(self.config_path,NOW,**kwargs)
            self.assertEqual(result['counts'],{'FRESH':1})
            self.assertTrue(list(mount.rglob('*.parquet')))
            again=refresh(self.config_path,NOW,**kwargs)
            self.assertEqual(again['counts'],{'FRESH':1});fetch.assert_called_once()
            revision=plan_refresh([company()],{KEY:dict(validated_sessions=['2026-11-11','2026-11-12','2026-11-13'])},
                calendar=calendar,now=NOW+timedelta(seconds=2),revision_dates={KEY:['2026-11-13']},revision_id='synthetic-owner-reviewed-correction')
            execute_plan(revision,self.root/'state',authorized_sha256=digest(revision),calendar=calendar,
                token_supplier=lambda:'fixture-token',fetch=lambda *_:(source(close=102),200),archive_factory=Archive,
                clock=lambda:NOW+timedelta(seconds=2),sleep=lambda _:None,quota_path=self.root/'provider-quota.json')
            store=HistoricalStore()
            try:
                view=VerifiedStore(store,calendar)
                old=view.read(1,'Daily',start='2026-11-13',end='2026-11-13',decision_time=NOW.isoformat(),mode='strict_delivery')
                new=view.read(1,'Daily',start='2026-11-13',end='2026-11-13',decision_time=(NOW+timedelta(seconds=2)).isoformat(),mode='strict_delivery')
                self.assertEqual(old['rows'][0]['close'],Decimal(101))
                self.assertEqual(new['rows'][0]['close'],Decimal(102))
                self.assertEqual(store.db.execute("SELECT count(*) FROM requests WHERE status='SUCCESS'").fetchone()[0],2)
            finally:
                store.close()


class DerivedRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.rows=[]
        for d in ('2026-11-11','2026-11-12','2026-11-13'):
            self.rows.append(dict(company_id=1,instrument_key=KEY,timeframe='Daily',start=d+'T00:00:00+05:30',
                observed_at=NOW.isoformat(),quality='OHLC_VALID',status='PROVISIONAL',
                source_response_sha256='synthetic',source_response_sha256s=['synthetic'],
                **{k:Decimal(v) for k,v in dict(open=100,high=102,low=99,close=101,volume=123).items()}))
        self.store=Mock();self.store.native.side_effect=lambda *args:([dict(r) for r in self.rows], [dict(sha256='synthetic')])

    def test_after_close_verified_overlay_and_warmup(self):
        view=VerifiedStore(self.store,calendar)
        result=view.read(1,'Daily',start='2026-11-11',end='2026-11-13',decision_time=NOW.isoformat())
        self.assertEqual(len(result['rows']),3)
        self.assertTrue(all(r['status']=='COMPLETED_RETROSPECTIVE' for r in result['rows']))
        result=refresh_outputs(self.store,[company()],calendar=calendar,now=NOW,output=self.root,start='2026-11-11',end='2026-11-13')
        values=result['content']['indicators'][KEY]['indicators']
        self.assertIsNone(values[-1]['sma']);self.assertIsNone(values[-1]['atr'])

    def test_old_instrument_identity_cannot_certify_current_key_coverage(self):
        current=company('NSE_EQ|INE000000002')
        result=collect_coverage(self.store,[current],calendar=calendar,now=NOW)
        self.assertEqual(result[current['instrument_key']]['validated_sessions'],[])
        self.assertEqual(coverage_report(current,result[current['instrument_key']],calendar=calendar,now=NOW)['status'],'STALE')

    def test_coverage_request_dates_use_ist_across_utc_midnight(self):
        collect_coverage(self.store,[company()],calendar=calendar,now='2026-11-13T20:00:00+00:00')
        self.assertEqual(self.store.native.call_args.args[-1],'2026-11-14')

    def test_revision_creates_new_build_and_preserves_old(self):
        args=dict(calendar=calendar,now=NOW,output=self.root,start='2026-11-11',end='2026-11-13')
        old=refresh_outputs(self.store,[company()],**args)
        self.rows[-1]['close']=Decimal(102)
        new=refresh_outputs(self.store,[company()],**args)
        self.assertNotEqual(old['build'],new['build']);self.assertTrue(old['path'].exists())
        self.assertEqual(json.loads((self.root/'latest.json').read_text())['build'],new['build'])
        again=refresh_outputs(self.store,[company()],**args)
        self.assertEqual(again['build'],new['build'])

    def test_gap_blocks_indicators_and_partial_index(self):
        self.rows.pop(1)
        result=refresh_outputs(self.store,[company()],calendar=calendar,now=NOW,output=self.root,start='2026-11-11',end='2026-11-13',groups=[dict(code='synthetic',members=[KEY,'ABSENT'])])
        self.assertEqual(result['content']['indicators'][KEY]['status'],'GAP')
        self.assertEqual(result['content']['indices']['synthetic']['status'],'GAP')


class SharedQuotaTests(unittest.TestCase):
    def test_concurrent_live_and_daily_reservations_share_capacity(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'provider-quota.json'
            with ThreadPoolExecutor(max_workers=2) as executor:
                results=list(executor.map(lambda _:reserve(path,NOW),range(2)))
            self.assertEqual(sum(results),1)
            self.assertEqual(len(json.loads(path.read_text())['calls']),1)
            self.assertFalse(reserve(path,NOW-timedelta(seconds=1)))
            self.assertTrue(reserve(path,NOW+timedelta(seconds=2)))


class ExplorerPublicationTests(unittest.TestCase):
    def test_content_addressed_export_updates_pointer_only_on_revision(self):
        from scrapper.market_refresh.explorer import publish
        calls=[]
        def builder(output,**kwargs):
            calls.append(kwargs)
            entry=Path(output)/'charts/index.html';entry.parent.mkdir(parents=True)
            entry.write_text('<html>Synthetic index</html>')
            return entry
        with tempfile.TemporaryDirectory() as root:
            config=dict(index_export_output=root,index_start='2026-11-11',calendar_sha256='reviewed')
            args=dict(calendar=calendar,now=NOW,end='2026-11-13',incremental_state=Path(root)/'receipts',builder=builder)
            first=publish(config,{'revision':1},**args)
            again=publish(config,{'revision':1},**args)
            revised=publish(config,{'revision':2},**args)
            self.assertEqual(first['status'],'PUBLISHED');self.assertEqual(again['status'],'UNCHANGED')
            self.assertNotEqual(first['build'],revised['build']);self.assertEqual(len(calls),2)
            self.assertTrue((Path(root)/'builds'/first['build']/'charts/index.html').is_file())
            self.assertEqual(json.loads((Path(root)/'latest.json').read_text())['build'],revised['build'])
