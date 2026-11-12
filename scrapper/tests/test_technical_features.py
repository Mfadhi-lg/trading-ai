"""Synthetic reference vectors; no archive, database or provider access."""
from copy import deepcopy
from decimal import Decimal as D
from unittest import TestCase
from unittest.mock import Mock

from scrapper.research.features.technical import daily_indicators


class TechnicalTests(TestCase):
    def setUp(self):
        self.rows = [dict(start=f'2026-10-{day:02}T00:00:00+05:30',
                          end=f'2026-10-{day:02}T15:30:00+05:30',
                          timeframe='Daily', instrument_key='NSE_EQ|TEST',
                          quality='OHLC_VALID', open=D(c), close=D(c),
                          high=D(c+1), low=D(c-1), volume=D(v))
                     for day, c, v in [(26,10,10),(27,12,20),(28,11,30),(29,15,40)]]
        self.source = dict(rows=self.rows, exclusions=[], source_revisions=['receipt'],
                           requested_window=dict(start='2026-10-26', end='2026-10-29'))
        self.store = Mock()
        self.store.read.return_value = self.source

    def calculate(self, **kwargs):
        return daily_indicators(self.store, 1, start='2026-10-26', end='2026-10-29',
                                decision_time='2026-10-30T00:00:00+05:30',
                                price_period=3, atr_period=2, volume_period=2, **kwargs)

    def test_hand_calculated_reference(self):
        result = self.calculate()
        values = result['indicators']
        self.assertEqual([r['sma'] for r in values], [None,None,D(11),D(38)/3])
        self.assertEqual([r['ema'] for r in values], [D(10),D(11),D(11),D(13)])
        self.assertEqual([r['true_range'] for r in values], [None,D(3),D(2),D(5)])
        self.assertEqual([r['atr'] for r in values], [None,None,D('2.5'),D('3.75')])
        self.assertEqual([r['rvol'] for r in values], [None,None,D(2),D('1.6')])
        self.assertIs(result['source'], self.source)
        self.store.read.assert_called_once_with(1,'Daily',start='2026-10-26',end='2026-10-29',
            mode='strict_delivery',decision_time='2026-10-30T00:00:00+05:30',
            revision_asof=None,definition='owner')

    def test_zero_volume_and_no_mutation(self):
        for row in self.rows: row['volume'] = D(0)
        original = deepcopy(self.source)
        self.assertTrue(all(r['rvol'] is None for r in self.calculate()['indicators']))
        self.assertEqual(original, self.source)

    def test_exclusions_fail_closed(self):
        self.source['exclusions'] = [{'reason':'MISSING_EXPECTED_SESSION'}]
        with self.assertRaises(ValueError): self.calculate()

    def test_invalid_rows_fail_closed(self):
        for field, value in [('close', None), ('close', D('NaN')), ('volume', D(-1)),
                             ('high', D(1)), ('quality','QUARANTINED'),
                             ('end','2027-01-01T00:00:00+05:30')]:
            with self.subTest(field=field,value=value):
                original = deepcopy(self.rows[0])
                self.rows[0][field] = value
                with self.assertRaises(ValueError): self.calculate()
                self.rows[0] = original
        self.rows.reverse()
        with self.assertRaises(ValueError): self.calculate()

    def test_prefix_does_not_use_future_prices(self):
        baseline = self.calculate()['indicators'][:3]
        self.rows[-1].update(open=D(100),close=D(100),high=D(101),low=D(99))
        self.assertEqual(baseline, self.calculate()['indicators'][:3])

    def test_empty_window(self):
        self.source['rows'] = []
        self.assertEqual([], self.calculate()['indicators'])

    def test_real_archive_access_rejects_gap_and_late_receipt(self):
        from experiments.historical_candle_archive_v1.access import HistoricalStore
        store = HistoricalStore.__new__(HistoricalStore)
        for row in self.rows:
            row.update(status='COMPLETED_RETROSPECTIVE', definition='UPSTOX_NATIVE',
                       observed_at='2026-10-29T16:00:00+05:30')
        store.native = Mock(return_value=(self.rows, ['receipt']))
        self.store = store
        self.assertEqual(4, len(self.calculate()['indicators']))
        self.rows.pop(1)
        with self.assertRaisesRegex(ValueError, 'missing owner sessions'):
            self.calculate()
        self.rows[:] = [self.rows[0]]
        self.rows[0]['observed_at'] = '2026-11-01T00:00:00+05:30'
        with self.assertRaisesRegex(ValueError, 'missing owner sessions'):
            self.calculate()
