"""TA-03R source-formula arithmetic; TradingView export parity remains untested."""
import unittest
from dataclasses import replace
from decimal import Decimal as D
from experiments.earnings_reaction_v1.indicators import (
    calculate_daily, calculate_daily_research_proxies, pine_average, pine_round, pine_rvol, _extreme,
)
from experiments.earnings_reaction_v1.tests.test_indicators import bars, CUTOFF


class SourceIndicatorTests(unittest.TestCase):
    def test_current_atr_and_first_tr_na(self):
        stock = list(bars())
        stock[14] = replace(stock[14], high=D(108), low=D(92))
        got = calculate_daily(stock, bars(key='benchmark'), cutoff=CUTOFF)
        self.assertIsNone(got[0].true_range)
        self.assertTrue(all(r.atr is None for r in got[:14]))
        self.assertEqual(got[14].atr, D(3))  # (13*2+16)/14
        self.assertEqual(got[14].dtr_atr_ratio, D(16)/3)
        self.assertEqual(got[14].dtr_pct, D(533))
        old = calculate_daily_research_proxies(stock, bars(key='benchmark'), cutoff=CUTOFF)
        self.assertEqual(old[14].dtr_previous_atr14, D(8))

    def test_smoothing_hand_arithmetic_and_na(self):
        x = [None, D(2), D(4), D(8)]
        self.assertEqual(pine_average(x, 2, 'RMA'), (None, None, D(3), D('5.5')))
        self.assertEqual(pine_average(x, 3, 'EMA'), (None, D(2), D(3), D('5.5')))
        self.assertEqual(pine_average(x, 2, 'SMA'), (None, None, D(3), D(6)))
        self.assertEqual(pine_average(x, 2, 'WMA')[-1], D(20)/3)
        self.assertEqual(pine_average([D(2), None, D(4), None, D(8)], 2, 'RMA'),
                         (None, None, D(3), D(3), D('5.5')))

    def test_rounding_and_rvol_units(self):
        self.assertEqual(pine_round(D('70.5')), D(71))
        self.assertEqual(pine_round(D('-1.5')), D(-1))
        ratios, percents = pine_rvol([D(10)]*20+[D(100), D(29)])
        self.assertEqual(ratios[-2:], (D(10), D(2)))
        self.assertEqual(percents[-2:], (D(1000), D(200)))
        self.assertIsNone(pine_rvol([D(0)]*20+[D(10)])[0][-1])
        self.assertEqual(pine_rvol([D(10)]*20+[D(0)])[1][-1], D(0))
        self.assertEqual(pine_rvol([D(10), None, D(20), D(30)], 2)[0][-1], D(2))

    def test_ratio_of_returns_default_123_and_mean(self):
        stock, bench = list(bars(180)), list(bars(180, key='benchmark'))
        stock[123] = replace(stock[123], close=D(120), high=D(121))
        bench[123] = replace(bench[123], close=D(110), high=D(111))
        got = calculate_daily(stock, bench, cutoff=CUTOFF)
        self.assertIsNone(got[122].rs)
        self.assertAlmostEqual(got[123].rs, D(1)/11)
        self.assertEqual(got[123].reference_date, '2026-01-01')
        self.assertIsNone(got[171].rs_mean)
        self.assertAlmostEqual(got[172].rs_mean, D(1)/550)
        self.assertEqual(got[123].rs_color, 'green')
        self.assertEqual(got[124].rs_color, 'red')  # zero is red in source

    def test_configured_source_lengths_trend_and_extremes(self):
        stock = [replace(c, close=D(100+i), high=D(101+i)) for i, c in enumerate(bars(10))]
        got = calculate_daily(stock, bars(10, key='benchmark'), cutoff=CUTOFF,
                              rs_length=1, rs_mean_length=2, price_mean_length=2, trend_base=1)
        self.assertLess(got[2].rs_trend_radians, 0)
        self.assertTrue(got[2].positive_confirmation)
        # New high vs previous three, despite nonmonotonic preceding values.
        self.assertTrue(_extreme([D(3), D(1), D(2), D(4)], 3, True))
        self.assertFalse(_extreme([D(3), D(1), D(2), D(3)], 3, True))
        opens = calculate_daily(stock, bars(10, key='benchmark'), cutoff=CUTOFF, rs_length=1, source='open')
        self.assertEqual(opens[1].rs, D(0))

    def test_alignment_zero_and_causality(self):
        stock, bench = bars(180), bars(180, key='benchmark')
        full = calculate_daily(stock, bench, cutoff=CUTOFF)
        for n in (1, 14, 15, 21, 123, 124, 173, 180):
            self.assertEqual(calculate_daily(stock[:n], bench[:n], cutoff=stock[n-1].completed_at), full[:n])
        self.assertEqual(calculate_daily([], [], cutoff=CUTOFF), ())
        with self.assertRaisesRegex(ValueError, 'alignment'):
            calculate_daily(stock, bench[:-1], cutoff=CUTOFF)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            calculate_daily(stock, bench, cutoff=stock[-1].start_ist)
        flat = [replace(c, high=D(100), low=D(100)) for c in stock]
        zero = calculate_daily(flat, bench, cutoff=CUTOFF)
        self.assertEqual(zero[14].atr, D(0))
        self.assertIsNone(zero[14].dtr_pct)

    def test_invalid_parameters(self):
        for arg in ('atr_length', 'volume_length', 'rs_length', 'rs_mean_length', 'price_mean_length', 'trend_base'):
            for value in (0, -1, True):
                with self.assertRaises(ValueError):
                    calculate_daily([], [], cutoff=CUTOFF, **{arg: value})
        with self.assertRaises(ValueError):
            pine_average([], 14, 'BAD')
        with self.assertRaises(ValueError):
            calculate_daily([], [], cutoff=CUTOFF, source='unknown')
