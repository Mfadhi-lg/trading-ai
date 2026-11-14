import json
from datetime import timedelta
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from scrapper.market_live.engine import LiveStore
from scrapper.market_live.protocol import decode
from scrapper.market_live.replay import OPEN,DAY,KEY,calendar,frame,field,run
from scrapper.market_live.service import subscriptions,stream
from scrapper.market_live.watchlist import resolve_definition


def grant():
    return dict(owner_approved=True,activation_ref='SYNTHETIC_TEST',active_from=OPEN.isoformat(),
                active_until=(OPEN+timedelta(days=1)).isoformat())


class MarketLiveTests(unittest.TestCase):
    def test_actual_v3_wire_and_unknown_field(self):
        decoded=decode(frame([(OPEN,100,102.)],OPEN+timedelta(minutes=1))+field(99,b'future'))
        self.assertEqual(decoded['feeds'][KEY]['ohlc'][0]['vol'],100)
        for raw in (b'\x80',b'\x00',b'\x12\x20x'):
            with self.assertRaises(ValueError):decode(raw)

    def test_negative_wire_volume_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store=LiveStore(root,calendar)
            try:
                with self.assertRaises(ValueError):store.ingest(frame([(OPEN,-1,102.)],OPEN+timedelta(minutes=1)),OPEN+timedelta(minutes=1),{KEY})
                self.assertEqual(store.minutes(KEY),[])
            finally:store.close()

    def test_partial_repeat_reorder_and_completed_separation(self):
        with tempfile.TemporaryDirectory() as root:
            store=LiveStore(root,calendar)
            try:
                a=OPEN+timedelta(seconds=30);b=OPEN+timedelta(minutes=1,seconds=1)
                store.ingest(frame([(OPEN,50,101.)],a),a,{KEY})
                self.assertFalse(store.minutes(KEY)[0]['completed'])
                store.ingest(frame([(OPEN,100,102.)],b),b,{KEY})
                store.ingest(frame([(OPEN,100,102.)],b),b,{KEY})
                store.ingest(frame([(OPEN,50,101.)],a),b+timedelta(seconds=10),{KEY})
                self.assertEqual(store.minutes(KEY)[0]['vol'],'100')
                result=store.chart(KEY,OPEN.date(),b)
                self.assertEqual(result['completed']['1-hour'],[])
                self.assertEqual(result['developing']['1-hour'][0]['volume'],'100')
                before=store.chart(KEY,OPEN.date(),a)
                self.assertEqual(before['developing']['1-hour'][0]['volume'],'50')
                self.assertIsNone(before['latest_completed_minute'])
                after=store.chart(KEY,OPEN.date(),OPEN+timedelta(hours=1))
                self.assertEqual(after['developing']['1-hour'][0]['status'],'GAP')
            finally:store.close()

    def test_full_feed_to_chart_replay(self):
        with tempfile.TemporaryDirectory() as root:
            result=run(Path(root)/'replay')
            self.assertEqual(result['status'],'PASS')
            data=json.loads((Path(root)/'replay/chart.json').read_text())['instruments'][0]
            self.assertEqual(data['completed']['4-hour'][0]['start'],OPEN.isoformat())
            self.assertEqual(data['completed']['4-hour'][1]['end'],(OPEN+timedelta(minutes=375)).isoformat())
            self.assertEqual(data['developing']['Daily'],[])
            self.assertFalse(data['historical_delivery_verified'])

    def test_rest_repair_retains_original_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            store=LiveStore(root,calendar)
            try:
                now=OPEN+timedelta(minutes=2)
                raw=json.dumps(dict(status='success',data=dict(candles=[[OPEN.isoformat(),100,105,99,102,100,0]]))).encode()
                store.ingest_rest(raw,now,KEY,OPEN.date());store.ingest_rest(raw,now,KEY,OPEN.date())
                self.assertEqual(len(store.minutes(KEY)),1)
                self.assertIsNone(store.minutes(KEY)[0]['provider_timestamp'])
                with self.assertRaises(ValueError):store.ingest_rest(raw,now,KEY,OPEN.date()+timedelta(days=1))
                self.assertEqual(store.minutes(KEY)[0]['vol'],'100')
                self.assertEqual(list((Path(root)/'raw').glob('*.json'))[0].read_bytes(),raw)
            finally:store.close()

    def test_membership_no_duplicate_subscription(self):
        class Socket:
            def __init__(self):self.sent=[]
            def send(self,value):self.sent.append(json.loads(value))
        socket=Socket()
        current=subscriptions(socket,set(),{'a','b'})
        current=subscriptions(socket,current,{'b','c'})
        subscriptions(socket,current,{'b','c'})
        self.assertEqual([s['method'] for s in socket.sent],['sub','unsub','sub'])
        self.assertEqual(socket.sent[-1]['data']['mode'],'full')

    def test_disconnect_reconnect_and_expired_token(self):
        class Stop:
            done=False
            waits=[]
            def is_set(self):return self.done
            def wait(self,value):self.waits.append(value)
        class Socket:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def send(self,value):pass
            def recv(self,timeout):raise ConnectionError('PRIVATE_URL')
        with tempfile.TemporaryDirectory() as root:
            config=dict(state_dir=root,calendar_path='unused',max_reconnects=3,**grant())
            with patch('scrapper.market_live.service.load_calendar',return_value=calendar),patch('scrapper.market_live.service.load',return_value=({'version':'v1'},{KEY:{}})):
                stop=Stop();stream(config,stop,connector=Socket,clock=lambda:OPEN)
                self.assertEqual(stop.waits,[2,4])
                status=json.loads((Path(root)/'stream_status.json').read_text())
                self.assertEqual(status['reconnect_attempts'],3)
                self.assertNotIn('PRIVATE_URL',json.dumps(status))
                from scrapper.market_live.transport import AuthenticationBlocked
                def expired():raise AuthenticationBlocked()
                stream(config,Stop(),connector=expired,clock=lambda:OPEN)
                status=json.loads((Path(root)/'stream_status.json').read_text())
                self.assertEqual(status['reason'],'CREDENTIAL_EXPIRED_OR_DENIED')

    def test_identity_scope_and_age_gates(self):
        identity=dict(observed_at=OPEN.isoformat(),source_ref='synthetic',securities=[],identifiers=[],provider_records=[])
        with self.assertRaises(ValueError):resolve_definition({'schema_version':1,'version':'v1','company_ids':[1]},identity,OPEN)
        with self.assertRaises(ValueError):resolve_definition({'schema_version':1,'version':'v1','company_ids':list(range(1,21))},identity,OPEN+timedelta(days=2))

    def test_multi_instrument_recovery_uses_advancing_quota_clock(self):
        from scrapper.market_live.recovery import recover
        second='NSE_EQ|INE000A01001'
        with tempfile.TemporaryDirectory() as root:
            now=OPEN+timedelta(minutes=2)
            ticks=iter([now+timedelta(seconds=i) for i in (0,1,2,3,4,5,6,7,8,9)])
            calls=[]
            raw=json.dumps(dict(status='success',data=dict(candles=[[OPEN.isoformat(),100,105,99,102,100,0]]))).encode()
            def fetch(query,token):calls.append(query);return raw,200
            config=dict(state_dir=root,calendar_path='unused',provider_quota_path=str(Path(root)/'quota.json'),recovery_days=1,**grant())
            class Stop:
                def is_set(self):return False
                def wait(self,_):pass
            with patch('scrapper.market_live.recovery.load_calendar',return_value=calendar):
                result=recover(config,{KEY,second},now,Stop(),supplier=lambda:'TOKEN',fetch=fetch,clock=lambda:next(ticks))
            self.assertEqual(len(calls),2)
            self.assertEqual(result['status'],'COMPLETE')
            data=json.loads((Path(root)/'chart.json').read_text())
            self.assertTrue(all(r['latest_completed_minute'] is not None for r in data['instruments']))

    def test_verified_twenty_company_watchlist_and_versioned_membership(self):
        from scrapper.market_live.replay import KEYS
        records=[];identifiers=[]
        for i,key in enumerate(KEYS):
            records.append(dict(segment='NSE_EQ',exchange='NSE',isin=key.split('|')[1],
                                trading_symbol='SYN'+str(i),instrument_key=key,instrument_type='EQ'))
            identifiers.append(dict(company_id=i+1,identifier_type='NSE',identifier_value='SYN'+str(i),is_current=1))
        identity=dict(observed_at=OPEN.isoformat(),source_ref='SYNTHETIC_PROVIDER_SNAPSHOT',
                      securities=[],identifiers=identifiers,provider_records=records)
        definition=dict(schema_version=1,version='test-v1',company_ids=list(range(1,21)))
        self.assertEqual(set(resolve_definition(definition,identity,OPEN)),set(KEYS))
        changed=dict(definition,version='test-v2',company_ids=list(range(2,21))+[21])
        with self.assertRaises(ValueError):resolve_definition(changed,identity,OPEN)

    def test_account_lock_prevents_second_stream_in_another_directory(self):
        import fcntl
        with tempfile.TemporaryDirectory() as root:
            quota=str(Path(root)/'shared-quota.json')
            with open(quota+'.stream-owner.lock','a+b') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                config=dict(state_dir=str(Path(root)/'different-state'),calendar_path='unused',provider_quota_path=quota,**grant())
                with patch('scrapper.market_live.service.load_calendar',return_value=calendar):
                    with self.assertRaises(BlockingIOError):stream(config,threading.Event(),connector=lambda:self.fail('duplicate socket opened'),clock=lambda:OPEN)

    def test_intraday_indicators_keep_warmup_and_break_on_gap(self):
        from scrapper.market_live.indicators import calculate
        bars=[dict(start=(OPEN+timedelta(hours=i)).isoformat(),status='COMPLETED',open='100',high='105',low='99',close=str(100+i),volume='100') for i in range(5)]
        result=calculate(bars,price_period=2,atr_period=2,volume_period=2)
        self.assertEqual(result['status'],'FRESH')
        self.assertEqual(result['values'][-1]['sma'],__import__('decimal').Decimal('103.5'))
        bars[-2]['status']='GAP'
        result=calculate(bars,price_period=2,atr_period=2,volume_period=2)
        self.assertEqual(result['warmup_bars'],1)
        self.assertIsNone(result['values'][-1]['atr'])

    def test_monitor_is_explicit_receipt_only_and_unconfigured_is_unknown(self):
        from scrapper.market_live.monitor import snapshot
        with patch.dict('os.environ',{},clear=True):
            self.assertEqual(snapshot()['status'],'UNKNOWN')
        with tempfile.TemporaryDirectory() as root:
            config=Path(root)/'config.json'
            config.write_text(json.dumps(dict(state_dir=root)))
            before=set(Path(root).iterdir())
            result=snapshot(config,OPEN)
            self.assertEqual(result['status'],'UNKNOWN')
            self.assertEqual(set(Path(root).iterdir()),before)

    def test_daily_identity_overlay_retains_bytes_and_deduplicates(self):
        import gzip
        from scrapper.market_live.identity_refresh import current
        with tempfile.TemporaryDirectory() as root:
            config=dict(state_dir=root,provider_quota_path=str(Path(root)/'quota.json'),identity_refresh_enabled=True,**grant())
            raw=gzip.compress(json.dumps([dict(instrument_key=KEY)]).encode())
            calls=[]
            def fetcher():calls.append(1);return raw,200
            baseline=dict(identifiers=[{'company_id':1}],provider_records=[])
            first=current(config,baseline,OPEN,threading.Event(),fetcher=fetcher,clock=lambda:OPEN)
            again=current(config,baseline,OPEN,threading.Event(),fetcher=fetcher,clock=lambda:OPEN)
            self.assertEqual(len(calls),1);self.assertEqual(first,again)
            self.assertEqual(first['identifiers'],baseline['identifiers'])
            self.assertEqual(list((Path(root)/'identities').glob('*.gz'))[0].read_bytes(),raw)

    def test_entire_missing_middle_hour_breaks_indicator_chain(self):
        with tempfile.TemporaryDirectory() as root:
            store=LiveStore(root,calendar)
            try:
                for offset in list(range(60))+list(range(120,180)):
                    start=OPEN+timedelta(minutes=offset);end=start+timedelta(minutes=1)
                    store.ingest(frame([(start,100,102.)],end),end,{KEY})
                result=store.indicators(KEY,OPEN.date(),OPEN+timedelta(hours=3))
                self.assertEqual(result['1-hour']['warmup_bars'],1)
            finally:store.close()

    def test_real_websocket_binary_and_disconnect_on_isolated_loopback(self):
        try:
            from websockets.sync.client import connect
            from websockets.sync.server import serve
        except ImportError:
            self.skipTest('opt-in hash-locked websockets dependency is not installed')
        from scrapper.market_live.transport import Socket
        messages=[]
        def handler(socket):
            messages.append(socket.recv())
            socket.send(frame([(OPEN,100,102.)],OPEN+timedelta(minutes=1)))
        with serve(handler,'127.0.0.1',0) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                port=server.socket.getsockname()[1]
                with Socket(connect('ws://127.0.0.1:'+str(port),proxy=None)) as client:
                    subscriptions(client,set(),{KEY})
                    self.assertEqual(decode(client.recv(timeout=2))['feeds'][KEY]['ohlc'][0]['vol'],100)
                    with self.assertRaises(ConnectionError):client.recv(timeout=2)
                self.assertIsInstance(messages[0],bytes)
            finally:server.shutdown();thread.join(timeout=3)

    def test_slow_credential_handoff_stop_prevents_recovery_request(self):
        from scrapper.market_live.recovery import recover
        with tempfile.TemporaryDirectory() as root:
            stop=threading.Event()
            config=dict(state_dir=root,calendar_path='unused',provider_quota_path=str(Path(root)/'quota.json'),recovery_days=1,**grant())
            def supplier():stop.set();return 'TOKEN'
            with patch('scrapper.market_live.recovery.load_calendar',return_value=calendar):
                with self.assertRaises(PermissionError):
                    recover(config,{KEY},OPEN+timedelta(minutes=2),stop,supplier=supplier,
                            fetch=lambda *_:self.fail('request after stop'),clock=lambda:OPEN+timedelta(minutes=2))
            self.assertEqual(list((Path(root)/'recovery').glob('*.json')),[])
