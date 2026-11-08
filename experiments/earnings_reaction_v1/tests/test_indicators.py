"""Preserved TA-03 research-proxy arithmetic, boundary and causality tests."""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D

from experiments.earnings_reaction_v1.candles import Candle, IST
from experiments.earnings_reaction_v1.indicators import (
    calculate_daily_research_proxies as calculate_daily, chronological_completed, daily_rvol, true_range, wilder_rma,
    SPECIAL_SESSION, SPECIAL_SESSION_SOURCE,
)

CUTOFF = datetime(2030, 1, 1, tzinfo=IST)


def bars(n=60, key='stock'):
    start = datetime(2026, 1, 1, tzinfo=IST)
    return tuple(Candle(key, (start+timedelta(days=i)).isoformat(),
                       start+timedelta(days=i), start+timedelta(days=i+1),
                       D(100), D(101), D(99), D(100), D(10), D(0),
                       'provider_calendar_day_start; end_exclusive_next_midnight',
                       start+timedelta(days=i+1), None) for i in range(n))


class IndicatorTests(unittest.TestCase):
    def test_true_range_no_gap_and_first(self):
        self.assertEqual(true_range(D(12), D(8)), D(4))
        self.assertEqual(true_range(D(12), D(8), D(10)), D(4))

    def test_true_range_overnight_gaps(self):
        self.assertEqual(true_range(D(12), D(8), D(5)), D(7))
        self.assertEqual(true_range(D(12), D(8), D(15)), D(7))

    def test_wilder_seed_and_smoothing_hand_calculated(self):
        # Sum 1..14 = 105; seed 7.5; next (7.5*13+14.5)/14 = 8.
        got = wilder_rma(tuple(map(D, range(1, 15)))+(D('14.5'), D(22)))
        self.assertEqual(got[:13], (None,)*13)
        self.assertEqual(got[13:], (D('7.5'), D(8), D(9)))

    def test_rma_missing_reseeds(self):
        self.assertEqual(wilder_rma([D(2), D(4), None, D(6), D(8)], 2),
                         (None, D(3), None, None, D(7)))

    def test_rvol_excludes_current_hand_calculated(self):
        self.assertEqual(daily_rvol([D(10)]*20+[D(100)])[20], D(10))
        self.assertEqual(daily_rvol([D(10)]*20+[D(100), D(29)])[21], D(2))

    def test_rvol_missing_and_zero(self):
        self.assertIsNone(daily_rvol([D(0)]*20+[D(10)])[20])
        self.assertEqual(daily_rvol([D(10)]*20+[D(0)])[20], D(0))
        self.assertIsNone(daily_rvol([D(10)]*20+[None])[20])
        self.assertIsNone(daily_rvol([None]+[D(10)]*20)[20])
        self.assertEqual(daily_rvol([None]+[D(10)]*21)[21], D(1))

    def test_invalid_period_and_volume(self):
        for period in (0, -1, True):
            with self.assertRaises(ValueError):
                daily_rvol([], period)
            with self.assertRaises(ValueError):
                wilder_rma([], period)
        with self.assertRaises(ValueError):
            daily_rvol([D(-1)])

    def test_rs55_exact_alignment_and_units(self):
        stock, bench = list(bars()), list(bars(key='benchmark'))
        stock[55] = replace(stock[55], close=D(120), high=D(121))
        bench[55] = replace(bench[55], close=D(110), high=D(111))
        got = calculate_daily(stock, bench, cutoff=CUTOFF)
        self.assertIsNone(got[54].relative_return55_pp_research_proxy)
        self.assertEqual(got[55].relative_return55_pp_research_proxy, D(10))
        self.assertEqual(got[0].normalized_price_ratio_research_proxy, D(100))
        self.assertAlmostEqual(got[55].normalized_price_ratio_research_proxy, D(1200)/11)

    def test_missing_benchmark_session_rejected(self):
        stock, bench = bars(), bars(key='benchmark')
        for broken in (bench[:20]+bench[21:], bench[1:]):
            with self.assertRaisesRegex(ValueError, 'alignment'):
                calculate_daily(stock, broken, cutoff=CUTOFF)

    def test_zero_close_rejected(self):
        for which in ('stock', 'benchmark'):
            stock, bench = list(bars()), list(bars(key='benchmark'))
            target = stock if which == 'stock' else bench
            target[0] = replace(target[0], close=D(0))
            with self.assertRaises(ValueError):
                calculate_daily(stock, bench, cutoff=CUTOFF)

    def test_zero_atr_unavailable(self):
        stock = tuple(replace(c, high=D(100), low=D(100)) for c in bars())
        got = calculate_daily(stock, bars(key='benchmark'), cutoff=CUTOFF)
        self.assertEqual(got[14].previous_atr14, D(0))
        self.assertIsNone(got[14].dtr_previous_atr14)
        self.assertIn('unavailable_input_or_zero_denominator', got[14].availability)

    def test_descending_normalization_preserves_objects(self):
        source = bars()
        got = chronological_completed(source[::-1], cutoff=CUTOFF)
        self.assertEqual(got, source)
        self.assertIs(got[0], source[0])
        with self.assertRaises(ValueError):
            chronological_completed(source+(source[0],), cutoff=CUTOFF)

    def test_warmup_and_previous_atr_hand_calculated(self):
        stock = list(bars())
        stock[14] = replace(stock[14], high=D(108), low=D(92))
        got = calculate_daily(stock, bars(key='benchmark'), cutoff=CUTOFF)
        self.assertTrue(all(r.atr14 is None for r in got[:13]))
        self.assertEqual(got[13].atr14, D(2))
        self.assertIsNone(got[13].dtr_previous_atr14)
        self.assertEqual(got[14].previous_atr14, D(2))
        self.assertEqual(got[14].dtr_previous_atr14, D(8))
        self.assertEqual(got[14].atr14, D(3))
        self.assertTrue(all(r.rvol20 is None for r in got[:20]))
        self.assertEqual(got[20].rvol20, D(1))
        self.assertTrue(all(r.relative_return55_pp_research_proxy is None for r in got[:55]))

    def test_special_sunday_is_preserved(self):
        got = calculate_daily(bars(), bars(key='benchmark'), cutoff=CUTOFF)
        selected = [r for r in got if str(r.stock.start_ist.date()) == SPECIAL_SESSION]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].stock.start_ist.weekday(), 6)
        self.assertTrue(SPECIAL_SESSION_SOURCE.endswith('FAOP72352.pdf'))

    def test_no_future_access_prefix_invariance(self):
        stock, bench = bars(), bars(key='benchmark')
        full = calculate_daily(stock, bench, cutoff=CUTOFF)
        for n in (1, 13, 14, 15, 20, 21, 55, 56, 59):
            self.assertEqual(calculate_daily(stock[:n], bench[:n], cutoff=stock[n-1].completed_at), full[:n])
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            calculate_daily(stock, bench, cutoff=stock[-1].start_ist)

    def test_no_corporate_action_adjustments(self):
        stock = list(bars())
        stock[30] = replace(stock[30], open=D(50), high=D(51), low=D(49), close=D(50), volume=D(20))
        got = calculate_daily(stock, bars(key='benchmark'), cutoff=CUTOFF)
        self.assertEqual(got[30].true_range, D(51))  # raw discontinuity retained
        self.assertIs(got[30].stock, stock[30])
        self.assertEqual(got[30].normalized_price_ratio_research_proxy, D(50))
        self.assertEqual(got[30].rvol20, D(2))

    def test_non_daily_mixed_and_empty(self):
        source = bars()
        with self.assertRaises(ValueError):
            chronological_completed([replace(source[0], end_ist=source[0].start_ist+timedelta(minutes=5))], cutoff=CUTOFF)
        with self.assertRaises(ValueError):
            chronological_completed([source[0], replace(source[1], instrument_key='other')], cutoff=CUTOFF)
        self.assertEqual(calculate_daily([], [], cutoff=CUTOFF), ())


if __name__ == '__main__':
    unittest.main()
