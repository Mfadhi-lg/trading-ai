"""Offline archive audit contracts; synthetic receipts are not historical proof."""
from datetime import date
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from experiments.historical_candle_archive_v1 import analyze
from experiments.historical_candle_archive_v1.acquire import normalize
from experiments.historical_candle_archive_v1.storage import sha


class CandleArchiveIntegrityTests(unittest.TestCase):
    def company(self, listing='2026-10-29'):
        return dict(company_id=1, symbol='FIXTURE', listing_date=listing)

    def audit(self, groups, *, end=date(2026, 10, 30), listing='2026-10-29'):
        with tempfile.TemporaryDirectory() as tmp, patch.object(analyze, 'OUT', Path(tmp)), patch.object(analyze, 'END', end):
            inventory, _, _ = analyze.coverage(groups, [self.company(listing)])
            gaps = json.loads((Path(tmp) / 'missing-session-review.json').read_text())
        return {r['timeframe']: r for r in inventory}, {r['timeframe']: r for r in gaps}

    def test_empty_timeframes_report_missing_sessions(self):
        inventory, gaps = self.audit({})
        for timeframe in ('Daily', '1-hour', '4-hour'):
            self.assertEqual(inventory[timeframe]['missing_scheduled_sessions_since2022'], 2)
            self.assertEqual(gaps[timeframe]['missing_dates'], ['2026-10-29', '2026-10-30'])
        self.assertEqual(inventory['Weekly']['session_coverage_status'], 'NOT_EVALUATED_WEEKLY')

    def test_listing_and_closed_days_bound_expected_history(self):
        inventory, gaps = self.audit({}, end=date(2026, 11, 1), listing='2026-10-30')
        self.assertEqual(gaps['Daily']['missing_dates'], ['2026-10-30'])
        self.assertEqual(inventory['Daily']['unexpected_session_dates'], 0)

    def test_unresolved_calendar_is_not_a_closed_session(self):
        row = dict(start='2026-11-08T18:00:00+05:30', status='COMPLETED_RETROSPECTIVE', quality='OHLC_VALID')
        inventory, gaps = self.audit({(1, 'Daily'): [row]}, end=date(2026, 11, 8), listing='2026-11-08')
        self.assertEqual(inventory['Daily']['unexpected_session_dates'], 0)
        self.assertEqual(inventory['Daily']['unresolved_calendar_dates'], 1)
        self.assertEqual(gaps['Daily']['unresolved_dates'], ['2026-11-08'])

    def test_intraday_grid_reports_missing_and_extra_starts(self):
        rows = [dict(start=f'2026-10-30T{hour}:00+05:30', status='COMPLETED_RETROSPECTIVE', quality='OHLC_VALID')
                for hour in ('09:15', '10:15', '11:15', '12:15', '13:15', '14:15', '15:20')]
        inventory, gaps = self.audit({(1, '1-hour'): rows}, listing='2026-10-30')
        self.assertEqual(inventory['1-hour']['native_session_grid_differences'], 1)
        self.assertEqual(gaps['1-hour']['grid_differences'], [dict(
            day='2026-10-30', missing_starts=['2026-10-30T15:15:00+05:30'],
            unexpected_starts=['2026-10-30T15:20:00+05:30'])])

    def test_overlapping_receipts_report_open_interest_revisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = sqlite3.connect(':memory:')
            self.addCleanup(db.close)
            db.execute('CREATE TABLE requests(request_id, status, received_at, raw_path, raw_sha256, elapsed_seconds)')
            requests = []
            for index, oi in enumerate((0, 0, 2)):
                q = dict(request_id=str(index), company_id=1, instrument_key='NSE_EQ|FIXTURE',
                         timeframe='Daily', unit='days', interval=1, from_date='2026-10-30',
                         to_date='2026-10-30', role='NATIVE_HISTORY')
                requests.append(q)
                raw = json.dumps(dict(status='success', data=dict(candles=[
                    ['2026-10-30T00:00:00+05:30', 10, 12, 9, 11, 100, oi]]))).encode()
                path = f'{index}.gz'
                (root / path).write_bytes(gzip.compress(raw))
                db.execute('INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?)',
                           (str(index), 'SUCCESS', f'2026-11-0{index+1}T00:00:00+00:00', path, sha(raw), 0))
            (root / 'pilot-manifest.json').write_text(json.dumps(dict(requests=requests)))
            with patch.object(analyze, 'OUT', root):
                groups, _, duplicates, _ = analyze.load(SimpleNamespace(root=root, db=db))
                self.assertEqual([r['changed'] for r in duplicates], [False, True])
                self.assertEqual(groups[(1, 'Daily')][0]['open_interest'], 2)
                # Checksum corruption must fail before parsing the replacement bytes.
                (root / '0.gz').write_bytes(gzip.compress(b'{}'))
                with self.assertRaisesRegex(ValueError, 'raw checksum failure'):
                    analyze.load(SimpleNamespace(root=root, db=db))

    def test_timestamp_normalization_preserves_source_and_adjustment_uncertainty(self):
        q = dict(company_id=1, instrument_key='NSE_EQ|FIXTURE', timeframe='1-hour',
                 unit='hours', interval=1, from_date='2026-10-30', to_date='2026-10-30')
        stamp = '2026-10-30T03:45:00+00:00'
        raw = json.dumps(dict(status='success', data=dict(candles=[[stamp, 10, 12, 9, 11, 100, 0]]))).encode()
        rows, issues = normalize(raw, q, '2026-11-01T00:00:00+00:00')
        self.assertFalse(issues)
        self.assertEqual(rows[0]['start'], '2026-10-30T09:15:00+05:30')
        self.assertEqual(rows[0]['provider_timestamp'], stamp)
        self.assertEqual(rows[0]['source_response_sha256'], sha(raw))
        self.assertEqual(rows[0]['corporate_action_status'], 'UNKNOWN_PROVIDER_ADJUSTMENT_BASIS')
        self.assertFalse(rows[0]['historical_delivery_verified'])


if __name__ == '__main__':
    unittest.main()
