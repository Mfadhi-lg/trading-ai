"""Regression observations of UNMODIFIED upstream modules. Synthetic data only.

Run only via run_offline.py, under network-deny.sb. Transport dependencies are
inert stubs, not installed packages. Expected defects are asserted explicitly.
"""
import copy
import csv
import importlib.util
import io
import json
import logging
from pathlib import Path
import socket
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parents[1] / 'artifacts/tradingview-scraper-poc/tv01'
sys.path.insert(0, str(ROOT))
from offline import import_csv, normalize_studies, timestamp

# Fail closed even if someone bypasses the trusted runner. No DNS, no TV address.
try:
    socket.socket().connect(('192.0.2.1', 443))
except PermissionError:
    pass
else:
    raise SystemExit('Required OS network deny is missing')

AUDIT = []


def audit(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto', 'subprocess.Popen', 'os.system'):
        AUDIT.append(event)
        raise RuntimeError('Offline execution attempted forbidden I/O: ' + event)


sys.addaudithook(audit)
VENDOR = next((ROOT / 'vendor').glob('tradingview-scraper-*'))


def forbidden(*args, **kwargs):
    raise AssertionError('Unexpected transport/export call')


def module(name, **members):
    obj = types.ModuleType(name)
    obj.__dict__.update(members)
    sys.modules[name] = obj
    return obj


class Closed(Exception):
    pass


# Do not run package __init__, dotenv, setup.py, or dependency installation hooks.
for name in ('tradingview_scraper', 'tradingview_scraper.symbols', 'tradingview_scraper.symbols.stream'):
    module(name, __path__=[])
module('requests', get=forbidden, post=forbidden, RequestException=type('RequestException', (Exception,), {}))
module('websocket', create_connection=forbidden, WebSocketConnectionClosedException=Closed)
module('pkg_resources', resource_filename=lambda package, name: str(VENDOR / package / name))
module('tradingview_scraper.symbols.utils', generate_user_agent=lambda: 'OFFLINE-TEST',
       save_json_file=forbidden, save_csv_file=forbidden)


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, VENDOR / relative)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


exceptions = load('tradingview_scraper.symbols.exceptions', 'tradingview_scraper/symbols/exceptions.py')
utils = load('tradingview_scraper.symbols.stream.utils', 'tradingview_scraper/symbols/stream/utils.py')
handler = load('tradingview_scraper.symbols.stream.stream_handler', 'tradingview_scraper/symbols/stream/stream_handler.py')
sys.modules['tradingview_scraper.symbols.stream'].StreamHandler = handler.StreamHandler
streamer = load('tradingview_scraper.symbols.stream.streamer', 'tradingview_scraper/symbols/stream/streamer.py')
technicals = load('tradingview_scraper.symbols.technicals', 'tradingview_scraper/symbols/technicals.py')
logging.getLogger().setLevel(logging.CRITICAL)


def make_streamer():
    obj = object.__new__(streamer.Streamer)  # Never run network-opening __init__.
    obj.study_id_to_name_map = {'st9': 'SYNTHETIC_A', 'st10': 'SYNTHETIC_B'}
    obj.stream_obj = types.SimpleNamespace(quote_session='qs_fixture', chart_session='cs_fixture',
                                          send_message=Mock(), ws=Mock())
    obj.export_result = False
    obj.export_type = 'json'
    return obj


def frame(pkt):
    data = json.dumps(pkt, separators=(',', ':'))
    return f'~m~{len(data)}~m~{data}'


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.s = make_streamer()
        self.p = json.loads((ROOT / 'fixtures/packets.synthetic.json').read_text())

    def test_ohlcv_fields_and_epoch_preserved(self):
        self.assertEqual(self.s._extract_ohlc_from_stream(self.p['ohlcv'])[0],
                         dict(index=0, timestamp=1700000000, open=100, high=103, low=99, close=102, volume=1234))

    def test_ohlc_missing_volume_is_absent(self):
        self.p['ohlcv']['p'][1]['sds_1']['s'][0]['v'].pop()
        self.assertNotIn('volume', self.s._serialize_ohlc(self.p['ohlcv'])[0])

    def test_ohlc_extra_numeric_field_is_dropped(self):
        self.p['ohlcv']['p'][1]['sds_1']['s'][0]['v'].append(987654)
        self.assertNotIn(987654, self.s._serialize_ohlc(self.p['ohlcv'])[0].values())

    def test_timestamp_upstream_does_not_validate(self):
        self.p['ohlcv']['p'][1]['sds_1']['s'][0]['v'][0] = 'not-a-time'
        self.assertEqual(self.s._serialize_ohlc(self.p['ohlcv'])[0]['timestamp'], 'not-a-time')

    def test_all_numeric_outputs_preserved_by_new_decoder(self):
        out = self.s._extract_indicator_from_stream(self.p['multi'])
        self.assertEqual(out['SYNTHETIC_A'][0], {'index': 0, 'timestamp': 1700000000, '0': 1.25, '1': 700, '2': 650})
        self.assertEqual(out['SYNTHETIC_B'][0]['0'], 999)
        self.assertEqual(len(out['SYNTHETIC_A']), 11)

    def test_legacy_decoder_drops_extra_plot(self):
        out = self.s._serialize_indicator(self.p['multi'])
        self.assertEqual(out[0], dict(index=0, timestamp=1700000000, smoothing=1.25, close=700))
        self.assertNotIn(650, out[0].values())

    def test_legacy_decoder_crashes_on_one_plot(self):
        self.p['multi']['p'][1]['st9']['st'][0]['v'] = [1700000000, 1.25]
        with self.assertRaises(ValueError):
            self.s._serialize_indicator(self.p['multi'])

    def test_ten_or_fewer_study_rows_are_silently_dropped(self):
        for count in (0, 1, 10):
            packet = copy.deepcopy(self.p['multi'])
            for study in packet['p'][1].values():
                study['st'] = study['st'][:count]
            self.assertEqual(self.s._extract_indicator_from_stream(packet), {})

    def test_null_plot_is_retained(self):
        self.p['multi']['p'][1]['st9']['st'][0]['v'][1] = None
        self.assertIsNone(self.s._extract_indicator_from_stream(self.p['multi'])['SYNTHETIC_A'][0]['0'])

    def test_unknown_study_ignored(self):
        self.s.study_id_to_name_map = {}
        self.assertEqual(self.s._extract_indicator_from_stream(self.p['multi']), {})

    def test_duplicate_indicator_id_overwrites_first_instance(self):
        self.s.study_id_to_name_map = {'st9': 'SAME_ID', 'st10': 'SAME_ID'}
        self.assertEqual(self.s._extract_indicator_from_stream(self.p['multi'])['SAME_ID'][0]['0'], 999)

    def test_missing_study_values_are_ignored(self):
        self.p['multi']['p'][1]['st9'] = {'other': []}
        self.assertNotIn('SYNTHETIC_A', self.s._extract_indicator_from_stream(self.p['multi']))

    def test_unexpected_packet_structure_raises(self):
        with self.assertRaises(IndexError):
            self.s._serialize_ohlc({'p': []})
        with self.assertRaises(TypeError):
            self.s._extract_indicator_from_stream({'m': 'du', 'p': ['cs', {'st9': None}]})

    def test_custom_inputs_are_not_accepted_and_defaults_win(self):
        meta = self.p['metadata']
        self.assertEqual(utils.prepare_indicator_metadata('SYNTHETIC_ID', meta, 'cs')['p'][5]['in_0']['v'], 14)
        with self.assertRaises(TypeError):
            utils.prepare_indicator_metadata('SYNTHETIC_ID', meta, 'cs', inputs={'in_0': 55})

    def test_empty_metadata_inputs_raise(self):
        with self.assertRaises(IndexError):
            utils.prepare_indicator_metadata('SYNTHETIC_ID', {'inputs': []}, 'cs')

    def test_missing_metadata_returns_empty_mock_http_only(self):
        with patch.object(utils.requests, 'get', return_value=Mock(json=lambda: {})) as get:
            self.assertEqual(utils.fetch_indicator_metadata('SYNTHETIC', '1', 'cs'), {})
            self.assertEqual(get.call_count, 1)

    def test_discovery_optional_version_mock_http_only(self):
        item = dict(scriptName='Synthetic', imageUrl='', author={'username': 'fixture'},
                    agreeCount=0, isRecommended=False, scriptIdPart='SYNTHETIC')
        with patch.object(utils.requests, 'get', return_value=Mock(json=lambda: {'results': [item]})):
            self.assertIsNone(utils.fetch_tradingview_indicators('synthetic')[0]['version'])

    def test_missing_metadata_does_not_shift_remaining_study_mapping(self):
        payload = utils.prepare_indicator_metadata('SYNTHETIC_B', self.p['metadata'], 'cs')
        self.s.study_id_to_name_map = {}
        with patch.object(streamer, 'fetch_indicator_metadata', side_effect=[{}, payload]):
            self.s._add_indicators([('SYNTHETIC_A', '1'), ('SYNTHETIC_B', '1')])
        self.assertEqual(self.s.study_id_to_name_map, {'st10': 'SYNTHETIC_B'})

    def test_supported_timeframes_emit_expected_resolution(self):
        for tf, resolution in [('1d', '1D'), ('4h', '240'), ('1h', '60')]:
            self.s._add_symbol_to_sessions('qs', 'cs', 'NSE:FIXTURE', tf)
            self.assertEqual(self.s.stream_obj.send_message.call_args_list[-2].args[1][4], resolution)

    def test_75_minute_and_unknown_silently_become_one_minute(self):
        for tf in ('75m', '75', 'unsupported'):
            self.s._add_symbol_to_sessions('qs', 'cs', 'NSE:FIXTURE', tf)
            self.assertEqual(self.s.stream_obj.send_message.call_args_list[-2].args[1][4], '1')

    def test_scanner_validation_rejects_75m(self):
        obj = technicals.Indicators()
        self.assertIn('EMA10', obj.indicators)
        self.assertIn('EMA20', obj.indicators)
        for tf in ('1d', '4h', '1h'):
            obj._validate_timeframe(tf)
        with self.assertRaises(ValueError):
            obj._validate_timeframe('75m')

    def test_scanner_response_mock_only(self):
        obj = technicals.Indicators()
        response = Mock(status_code=200, json=lambda: {'EMA10|60': 100.5, 'EMA20|60': None})
        with patch.object(technicals.requests, 'get', return_value=response) as get:
            out = obj.scrape(exchange='NSE', symbol='FIXTURE', timeframe='1h', indicators=['EMA10', 'EMA20'])
        self.assertEqual(out['data'], {'EMA10': 100.5, 'EMA20': None})
        self.assertEqual(get.call_count, 1)

    def test_framed_synthetic_packets_and_closed_session(self):
        ws = self.s.stream_obj.ws
        ws.recv.side_effect = [frame(self.p['ohlcv']) + frame(self.p['multi']), Closed()]
        with patch.object(streamer, 'sleep', return_value=None):
            self.assertEqual(list(self.s.get_data()), [self.p['ohlcv'], self.p['multi']])
        ws.close.assert_called_once()

    def test_malformed_frame_closes_session(self):
        self.s.stream_obj.ws.recv.return_value = '~m~3~m~bad'
        with patch.object(streamer, 'sleep', return_value=None):
            self.assertEqual(list(self.s.get_data()), [])
        self.s.stream_obj.ws.close.assert_called_once()

    def test_heartbeat_consumes_and_loses_next_packet(self):
        ws = self.s.stream_obj.ws
        ws.recv.side_effect = ['~m~6~m~~h~123', frame(self.p['ohlcv']), Closed()]
        with patch.object(streamer, 'sleep', return_value=None):
            self.assertEqual(list(self.s.get_data()), [])
        ws.send.assert_called_once_with('~m~6~m~~h~123')

    def test_normalized_csv_has_three_positional_plots(self):
        decoded = self.s._extract_indicator_from_stream(self.p['multi'])
        rows = normalize_studies(decoded, symbol='NSE:SYNTHETIC', timeframe='75-minute')
        self.assertEqual(len(rows), 44)
        self.assertEqual({r['plot_id'] for r in rows}, {'0', '1', '2'})
        self.assertEqual({r['source'] for r in rows}, {'SYNTHETIC_MOCK_ONLY'})
        with (OUT / 'normalized.synthetic.csv').open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['symbol', 'timeframe', 'timestamp', 'indicator_id', 'plot_id', 'value', 'source'])
            writer.writeheader()
            writer.writerows(rows)


class CSVTests(unittest.TestCase):
    def test_import_preserves_headers_duplicates_missing_and_precision(self):
        data = import_csv(ROOT / 'fixtures/chart.synthetic.csv', symbol='NSE:SYNTHETIC', timeframe='75-minute',
                          expected=['DSS table', 'POC', 'VAH', 'VAL'])
        self.assertEqual(data['original_columns'].count('EMA'), 2)
        values = [r for r in data['records'] if r['original_column_name'] == 'EMA']
        self.assertEqual(values[0]['value'], '100.1234567890123456789')
        self.assertNotEqual(values[0]['column_index'], values[1]['column_index'])
        self.assertTrue(any(r['missing'] for r in data['records']))
        self.assertEqual(data['unavailable_columns'], ['DSS table', 'POC', 'VAH', 'VAL'])
        self.assertEqual(data['original_rows'][1][-1], 'na')
        (OUT / 'imported.synthetic.json').write_text(json.dumps(data, indent=2) + '\n')

    def test_timestamp_epoch_iso_timezone_and_missing(self):
        expected = '2023-11-14T22:13:20+00:00'
        self.assertEqual(timestamp('1700000000'), expected)
        self.assertEqual(timestamp('1700000000000', epoch_unit='milliseconds'), expected)
        self.assertEqual(timestamp('2023-11-15T03:43:20+05:30'), expected)
        self.assertEqual(timestamp('2023-11-15T03:43:20', 'Asia/Kolkata'), expected)
        self.assertIsNone(timestamp(''))
        with self.assertRaises(ValueError):
            timestamp('2023-11-15 03:43:20')

    def test_original_source_hash(self):
        import hashlib
        path = ROOT / 'fixtures/chart.synthetic.csv'
        data = import_csv(path, symbol='NSE:SYNTHETIC', timeframe='1d')
        self.assertEqual(data['file_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_symbol_timeframe_not_guessed(self):
        with self.assertRaises(ValueError):
            import_csv(ROOT / 'fixtures/chart.synthetic.csv', symbol='', timeframe='')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    summary = dict(tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                   forbidden_io_events=AUDIT, transport='inert dependency stubs; in-memory mocks only',
                   upstream_source_modified=False, live_tradingview_requests=0,
                   scope='Offline behavior only. Passing defect assertions do not certify library reliability.')
    (OUT / 'test-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    sys.exit(0 if result.wasSuccessful() and not AUDIT else 1)
