import unittest
from decimal import Decimal as D
from scrapper.indices.timeframes import calculate, expected_grid, aggregate_weekly


def bar(start, end, close='100', volume='100'):
    return dict(start=start,end=end,open=close,high=close,low=close,close=close,volume=volume)


class TimeframeTests(unittest.TestCase):
    def test_four_hour_anchors_and_completed_only(self):
        grid=expected_grid('2026-11-02','2026-11-02','4-hour',decision_time='2026-11-02T14:00:00+05:30')
        self.assertEqual(len(grid),1)
        self.assertIn('09:15',grid[0]['start'])
        full=expected_grid('2026-11-02','2026-11-02','4-hour',decision_time='2026-11-02T16:00:00+05:30')
        self.assertEqual([g['start'][11:16] for g in full],['09:15','13:15'])
        self.assertEqual(full[-1]['end'][11:16],'15:30')

    def test_independent_intraday_arithmetic(self):
        grid=expected_grid('2026-11-02','2026-11-02','1-hour',decision_time='2026-11-02T12:15:00+05:30')
        prices={s:[bar(g['start'],g['end'],c) for g,c in zip(grid,closes)] for s,closes in [('A',['100','110','121']),('B',['100','90','90'])]}
        rows=calculate(prices,grid,[],members=['A','B'],timeframe='1-hour',decision_time='2026-11-02T12:15:00+05:30')
        self.assertEqual([r['index_close'] for r in rows],[D(1000),D(1000),D(1050)])
        self.assertEqual(rows[-1]['available_at'],grid[-1]['end'])
        self.assertEqual(rows[-1]['rising_count'],1)

    def test_future_final_ohlc_does_not_leak(self):
        grid=expected_grid('2026-11-02','2026-11-02','1-hour',decision_time='2026-11-02T16:00:00+05:30')
        prices={s:[bar(g['start'],g['end'],'100' if i==0 else '1000') for i,g in enumerate(grid)] for s in ('A','B')}
        rows=calculate(prices,grid,[],members=['A','B'],timeframe='1-hour',decision_time='2026-11-02T10:30:00+05:30')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['index_close'],D(1000))

    def test_missing_fixed_member_breaks_chain(self):
        grid=expected_grid('2026-11-02','2026-11-02','1-hour',decision_time='2026-11-02T12:15:00+05:30')
        prices={s:[bar(g['start'],g['end']) for g in grid] for s in ('A','B')}
        prices['B'].pop(1)
        rows=calculate(prices,grid,[],members=['A','B'],timeframe='1-hour',decision_time='2026-11-02T12:15:00+05:30')
        self.assertIsNone(rows[-1]['index_close'])
        self.assertEqual(rows[1]['observed_constituents'],1)

    def test_misaligned_boundary_rejected(self):
        grid=expected_grid('2026-11-02','2026-11-02','1-hour',decision_time='2026-11-02T12:15:00+05:30')
        prices={s:[bar(g['start'],g['end']) for g in grid] for s in ('A','B')}
        prices['A'][0]['end']='2026-11-02T10:16:00+05:30'
        with self.assertRaisesRegex(ValueError,'synchronized'):
            calculate(prices,grid,[],members=['A','B'],timeframe='1-hour',decision_time='2026-11-02T12:15:00+05:30')

    def test_weekly_requires_all_daily_sessions_and_sums_daily_value(self):
        dg=expected_grid('2026-11-02','2026-11-08','Daily',decision_time='2026-11-08T23:00:00+05:30')
        wg=expected_grid('2026-11-02','2026-11-08','Weekly',decision_time='2026-11-08T23:00:00+05:30')
        rows=[bar(g['start'],g['end'],str(100+i)) for i,g in enumerate(dg)]
        weekly=aggregate_weekly(rows,dg,wg)
        self.assertEqual(len(weekly),1)
        self.assertEqual(weekly[0]['close'],D('104'))
        self.assertEqual(weekly[0]['volume'],D('500'))
        self.assertEqual(weekly[0]['traded_value_proxy_crore'],D('0.0051'))
        self.assertEqual(aggregate_weekly(rows[:-1],dg,wg),[])

    def test_invalid_ohlc(self):
        grid=expected_grid('2026-11-02','2026-11-02','1-hour',decision_time='2026-11-02T10:15:00+05:30')
        prices={s:[bar(g['start'],g['end']) for g in grid] for s in ('A','B')};prices['A'][0]['high']='99'
        with self.assertRaisesRegex(ValueError,'OHLCV'):
            calculate(prices,grid,[],members=['A','B'],timeframe='1-hour',decision_time='2026-11-02T10:15:00+05:30')
