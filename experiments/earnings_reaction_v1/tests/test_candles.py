"""All candle prices and session calendars here are SYNTHETIC, never market evidence."""
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from experiments.earnings_reaction_v1.candles import CandleRequest
from experiments.earnings_reaction_v1.events import Session, TradingCalendar
from experiments.earnings_reaction_v1.upstox_candles import (
    AETHER, NIFTY, SCOPE, REQUESTS, ProviderError, parse_response,
    request_plan, retrieve_bounded, url_for, verify_instrument_records,
)


def dt(clock, day='2026-07-31'):
    return datetime.fromisoformat(f'{day}T{clock}+05:30')


def calendar():
    sessions = (Session(date(2026, 7, 30), dt('09:15', '2026-07-30'), dt('15:30', '2026-07-30')),
                Session(date(2026, 7, 31), dt('09:15'), dt('15:30')))
    return TradingCalendar('NSE', 'SYNTHETIC-v1', 'synthetic:calendar', 'Asia/Kolkata',
                           date(2026, 7, 30), date(2026, 8, 2), sessions)


def row(clock='00:00', day='2026-07-31'):
    return [dt(clock, day).isoformat(), 100.25, 104.5, 98.5, 102.5, 1000, 0]


def metadata():
    return [dict(instrument_key=AETHER, isin='INE0BWX01014', trading_symbol='AETHER',
                 segment='NSE_EQ', instrument_type='EQ', exchange='NSE'),
            dict(instrument_key=NIFTY, segment='NSE_INDEX', instrument_type='INDEX', exchange='NSE')]


class CandleTests(unittest.TestCase):
    def setUp(self):
        self.request = CandleRequest(AETHER, 'days', 1, date(2026, 7, 30), date(2026, 7, 31))
        self.rows = [row(day='2026-07-30'), row()]

    def parse(self, rows=None, **kwargs):
        opts = dict(retrieved_at=dt('18:00'), calendar=calendar(), calendar_verified=True,
                    synthetic=True, raw_response_ref='synthetic:raw',
                    instrument_metadata_ref='synthetic:metadata', instrument_metadata_verified=True)
        opts.update(kwargs)
        return parse_response(json.dumps({'status':'success', 'data':{'candles':self.rows if rows is None else rows}}).encode(),
                              self.request, **opts)

    def test_success_decimal_and_raw_values(self):
        b = self.parse()
        self.assertFalse(b.issues)
        self.assertEqual(b.candles[0].open, Decimal('100.25'))
        self.assertEqual(b.candles[0].volume, 1000)
        self.assertEqual(b.adjustment_status, 'UNKNOWN_PROVIDER_ADJUSTMENT_BASIS')

    def test_daily_bucket_not_session_open(self):
        c = self.parse().candles[1]
        self.assertEqual(c.start_utc.isoformat(), '2026-07-30T18:30:00+00:00')
        self.assertEqual(c.end_ist, dt('00:00', '2026-08-01'))
        self.assertEqual(c.completed_at, dt('15:30'))
        self.assertEqual(c.session_id, 'NSE:2026-07-31')

    def test_five_minute_end_and_utc_offset_conversion(self):
        req = replace(self.request, unit='minutes', interval=5, from_date=date(2026,7,31))
        r = row('09:15'); r[0] = '2026-07-31T03:45:00+00:00'
        c = parse_response(
            json.dumps({'status':'success','data':{'candles':[r]}}).encode(), req,
            retrieved_at=dt('18:00'), calendar=calendar(), synthetic=True).candles[0]
        self.assertEqual(c.start_ist, dt('09:15'))
        self.assertEqual(c.end_ist, dt('09:20'))

    def test_missing_session_no_fill(self):
        b = self.parse([row()])
        self.assertEqual(len(b.candles), 1)
        self.assertEqual(b.missing_starts, ('2026-07-30T00:00:00+05:30',))
        self.assertIn('missing_candles_or_sessions', b.issues)

    def test_intraday_missing_and_partial_session(self):
        req = replace(self.request, unit='minutes', interval=5, from_date=date(2026,7,31))
        b = parse_response(json.dumps({'status':'success','data':{'candles':[row('09:15')]}}).encode(),
                           req, retrieved_at=dt('09:18'), calendar=calendar(), synthetic=True)
        self.assertIn('incomplete_candle', b.issues)
        self.assertIn('incomplete_session_at_retrieval', b.issues)
        self.assertEqual(len(b.missing_starts), 74)
        self.assertFalse(b.candles[0].completed_by(dt('09:18')))
        self.assertTrue(b.candles[0].completed_by(dt('09:20')))

    def test_duplicate_and_order_preserved(self):
        b = self.parse([self.rows[0], self.rows[0], self.rows[1]])
        self.assertIn('duplicate_candles', b.issues)
        self.assertEqual(len(b.candles), 3)
        b = self.parse(self.rows[::-1])
        self.assertIn('provider_descending_order:normalize_explicitly_before_analysis', b.issues)
        b = self.parse([self.rows[0], self.rows[1], self.rows[0]])
        self.assertIn('unordered_candles', b.issues)

    def test_invalid_ohlcv(self):
        for pos, value in [(1, 0),(2, 99),(3, 105),(4, 200),(5, -1),(5, 1.1),
                           (6, -1),(1, True),(2, '104.5'),(2, float('nan')),(2,float('inf'))]:
            r = row(); r[pos] = value
            with self.subTest(pos=pos, value=value), self.assertRaises(ProviderError):
                self.parse([r])

    def test_invalid_timestamps_and_row_shapes(self):
        for r in [row()[:-1], ['not-date', *row()[1:]], ['2026-07-31T00:00', *row()[1:]], {}]:
            with self.subTest(row=r), self.assertRaises(ProviderError):
                self.parse([r])

    def test_daily_incomplete_no_future_use(self):
        b = self.parse(retrieved_at=dt('12:00'))
        self.assertIn('incomplete_candle', b.issues)
        self.assertFalse(b.candles[1].completed_by(dt('14:11:07')))
        with self.assertRaises(ValueError):
            b.candles[1].completed_by(datetime(2026,7,31))

    def test_future_bar_and_requested_date_bounds(self):
        b = self.parse([row(day='2026-08-01')])
        for flag in ('outside_requested_dates','non_session_candle','future_candle','incomplete_candle'):
            self.assertIn(flag, b.issues)

    def test_session_straddle_misalignment(self):
        req = replace(self.request, unit='minutes', interval=5, from_date=date(2026,7,31))
        b = parse_response(json.dumps({'status':'success','data':{'candles':[row('15:28')]}}).encode(),
                           req, retrieved_at=dt('18:00'), calendar=calendar(), synthetic=True)
        self.assertIn('outside_or_straddling_session', b.issues)
        self.assertIn('misaligned_interval', b.issues)

    def test_no_weekday_calendar_inference(self):
        b = self.parse(calendar=None)
        self.assertIn('calendar_unresolved:gaps_and_session_completeness_unknown', b.issues)
        self.assertFalse(b.missing_starts)
        self.assertFalse(b.candle_verified)
        self.assertIn('calendar_provisional', self.parse(calendar_verified=False).issues)
        self.assertIn('calendar_coverage_gap', self.parse(calendar=replace(calendar(), covered_from=date(2026,7,31),
                         sessions=calendar().sessions[1:])).issues)

    def test_raw_byte_identity_and_http_metadata(self):
        raw = b'{ "status":"success", "data":{"candles":[]} }\n'
        b = parse_response(raw, self.request, retrieved_at=dt('18:00'), synthetic=True)
        self.assertEqual(b.response_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(b.http_status, 200)
        self.assertNotEqual(b.response_sha256, hashlib.sha256(raw.strip()).hexdigest())
        self.assertEqual(b.retrieved_at_utc.utcoffset().total_seconds(), 0)

    def test_empty_payload(self):
        b = self.parse([])
        self.assertIn('empty_response', b.issues)
        self.assertFalse(b.candle_verified)

    def test_invalid_provider_payloads(self):
        for raw in (b'', b'html', b'{}', b'[]', b'{"status":"error"}',
                    b'{"status":"success","data":{"candles":null}}',
                    b'{"status":"error","status":"success","data":{"candles":[]}}'):
            with self.subTest(raw=raw), self.assertRaises(ProviderError):
                parse_response(raw, self.request, retrieved_at=dt('18:00'))

    def test_unauthorized_forbidden_rate_limited(self):
        for status, kind in ((401,'unauthorized'),(403,'forbidden_or_entitlement_denied'),(429,'rate_limited_stop_no_retry'),(500,'provider_http_error')):
            with self.subTest(status=status), self.assertRaises(ProviderError) as cm:
                parse_response(b'private-provider-error', self.request, http_status=status, retrieved_at=dt('18:00'))
            self.assertEqual(cm.exception.kind, kind)
            self.assertNotIn('private-provider-error', str(cm.exception))

    def test_provisional_event_never_promoted(self):
        b = self.parse(synthetic=False)
        self.assertTrue(b.candle_verified)  # Synthetic assertion of the independent gate.
        self.assertFalse(b.backtest_admissible)
        self.assertEqual(b.event_status, 'EVENT_DATE_PROVISIONAL')
        self.assertFalse(self.parse().candle_verified)
        self.assertFalse(self.parse(instrument_metadata_verified=False).candle_verified)
        self.assertFalse(self.parse(raw_response_ref='').candle_verified)

    def test_request_bounds_and_url_encoding(self):
        self.assertIn('NSE_EQ%7CINE0BWX01014/days/1/2026-07-31/2026-07-30', url_for(self.request))
        self.assertIn('Nifty%2050', url_for(REQUESTS[2]))
        for change in (dict(instrument_key='AETHER'), dict(unit='hours'), dict(interval=3), dict(interval=True),
                       dict(from_date=date(2026,8,1))):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.request, **change)

    def test_current_provider_metadata_does_not_prove_historical(self):
        self.assertTrue(verify_instrument_records(metadata()))
        for records in ([], metadata()*2, [dict(metadata()[0], trading_symbol='OTHER'), metadata()[1]]):
            with self.assertRaises(ValueError):
                verify_instrument_records(records)
        self.assertEqual(request_plan()['status'], 'NOT_AUTHORIZED_NOT_EXECUTED')
        self.assertFalse(request_plan()['backtest_admissible'])
        self.assertEqual(len(request_plan()['requests']), 3)

    def test_live_preflight_denies_before_token_or_network(self):
        supplier, opener = Mock(), Mock()
        with self.assertRaises(PermissionError):
            retrieve_bounded(authorization={}, token_supplier=supplier, instrument_records=metadata(),
                             metadata_ref='synthetic', output_dir='artifacts/earnings-reaction-v1/ta02b', opener=opener)
        supplier.assert_not_called(); opener.assert_not_called()
        with self.assertRaises(ValueError):
            retrieve_bounded(authorization=dict(scope=SCOPE, owner_approval_ref='synthetic',
                             allow_raw_retention=True, allow_entitlement_probe=True),
                             token_supplier=supplier, instrument_records=[], metadata_ref='synthetic',
                             output_dir='artifacts/earnings-reaction-v1/ta02b', opener=opener)
        supplier.assert_not_called(); opener.assert_not_called()


class BoundedTransportTests(unittest.TestCase):
    def auth(self):
        return dict(scope=SCOPE, owner_approval_ref='SYNTHETIC_APPROVAL_FOR_MOCK_ONLY',
                    allow_raw_retention=True, allow_entitlement_probe=True)

    def test_mock_three_requests_exact_raw_retention_and_no_token_artifacts(self):
        root = Path('artifacts/earnings-reaction-v1/ta02b')
        with tempfile.TemporaryDirectory(dir=root) as temp, patch('time.sleep'):
            raw = json.dumps({'status':'success', 'data':{'candles':[row()]}}).encode()+b'\n'
            response = Mock(status=200)
            response.read.return_value = raw
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            opener = Mock(return_value=response)
            result = retrieve_bounded(authorization=self.auth(), token_supplier=lambda:'SYNTHETIC_SECRET',
                        instrument_records=metadata(), metadata_ref='synthetic:metadata', output_dir=temp, opener=opener)
            self.assertEqual(opener.call_count, 3)
            self.assertEqual([c.args[0].full_url for c in opener.call_args_list], [url_for(r) for r in REQUESTS])
            self.assertEqual(len(result), 3)
            for b in result:
                self.assertFalse(b.candle_verified)  # No certified calendar supplied.
                self.assertFalse(b.backtest_admissible)
            paths = list(Path(temp).glob('*'))
            self.assertEqual(len(paths), 6)
            for path in paths:
                self.assertNotIn(b'SYNTHETIC_SECRET', path.read_bytes())
                self.assertNotIn(b'Authorization', path.read_bytes())
            self.assertTrue(all(p.read_bytes()==raw for p in paths if not p.name.endswith('.validation.json')))

    def test_mock_authorization_failure_stops_no_retry_no_error_body_retention(self):
        import io
        for status in (401,403,429):
            with self.subTest(status=status), tempfile.TemporaryDirectory(dir='artifacts/earnings-reaction-v1/ta02b') as temp:
                opener = Mock(side_effect=HTTPError('https://api.upstox.com',status,'error',{},io.BytesIO(b'private body')))
                with self.assertRaises(ProviderError) as cm:
                    retrieve_bounded(authorization=self.auth(), token_supplier=lambda:'SYNTHETIC_SECRET',
                        instrument_records=metadata(), metadata_ref='synthetic:metadata',output_dir=temp,opener=opener)
                self.assertEqual(opener.call_count,1)
                self.assertFalse(list(Path(temp).iterdir()))
                self.assertNotIn('private body',str(cm.exception))


if __name__ == '__main__':
    unittest.main()
