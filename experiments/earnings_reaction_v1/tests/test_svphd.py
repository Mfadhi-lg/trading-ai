from dataclasses import replace
from decimal import Decimal as D
import unittest

from experiments.earnings_reaction_v1.svphd import profile, coarse_edges, detailed_edges
from experiments.earnings_reaction_v1.tests.test_indicator_alternatives import fixture


class ProfileChecks(unittest.TestCase):
    def test_detailed_grid_uses_range_and_shared_price_origin(self):
        edges=detailed_edges('3457.1','3729',range_low='3457.1',range_high='3729',tick_size='.1')
        self.assertEqual(edges[1]-edges[0],D('.5'))
        self.assertEqual(edges[0],D('3457.0'))
        self.assertGreaterEqual(edges[-1],D('3729'))
        edges=detailed_edges('3601.1','3712',range_low='2490.4',range_high='3850',tick_size='.1')
        self.assertEqual(edges[1]-edges[0],D('2.5'))
        self.assertEqual(edges[0],D('3600.0'))

    def test_flat_candle_boundary_uses_lower_row(self):
        _, bars = fixture()
        bar = replace(bars[0], open=D(12), low=D(12), high=D(12), close=D(12), volume=D(80))
        result=profile([bar],cutoff=bar.completed_at,edges=[10,12,14])
        self.assertEqual(result['total'],(D(80),D(0)))

    def test_doji_uses_previous_close_then_inherits_direction(self):
        _, bars = fixture()
        first=replace(bars[0],open=D(13),high=D(13),low=D(11),close=D(11),volume=D(40))
        second=replace(bars[1],open=D(11),high=D(11),low=D(11),close=D(11),volume=D(20))
        third=replace(bars[2],open=D(12),high=D(12),low=D(12),close=D(12),volume=D(10))
        result=profile([first,second,third],cutoff=third.completed_at,edges=[10,14])
        self.assertEqual(result['up'],(D(10),))
        self.assertEqual(result['down'],(D(60),))

    def test_coarse_grid_selects_closest_count_and_resolves_tie(self):
        # Width 1.5 gives 26 rows; width 1.6 gives 24. Prefer smaller width.
        grid = coarse_edges('1692.1', '1730', tick_size='.1')
        self.assertEqual(grid[1]-grid[0], D('1.5'))
        self.assertEqual(len(grid)-1,26)
        # Width 6.2 gives 26 rows; width 6.3 gives 25.
        grid = coarse_edges('3554.9', '3710', tick_size='.1')
        self.assertEqual(grid[1]-grid[0], D('6.3'))
        self.assertEqual(len(grid)-1,25)

    def test_coarse_grid_matches_retained_original_grids(self):
        import json
        from pathlib import Path
        art=Path(__file__).resolve().parents[3]/'artifacts/earnings-reaction-v1/svphd-reconstruction'
        if not (art/'safari-graphics.json').exists():
            self.skipTest('original Safari reference not retained')
        g=json.loads((art/'safari-graphics.json').read_text())['hist']['_graphics']['_roughStudyGraphics']
        bars={r['index']:r['value'] for r in json.loads((art/'safari-chart-bars.json').read_text())['bars']['data']}
        checked=0
        for ix,rs in g['_hhistsByTimePointIndex']:
            low=min(D(str(bars[ix][3])),D(str(bars[ix+1][3])))
            high=max(D(str(bars[ix][2])),D(str(bars[ix+1][2])))
            grid=coarse_edges(low,high,tick_size='.1')
            self.assertAlmostEqual(float(grid[0]),min(r['priceLow'] for r in rs),places=7)
            self.assertAlmostEqual(float(grid[-1]),max(r['priceHigh'] for r in rs),places=7)
            self.assertAlmostEqual(float(grid[1]-grid[0]),rs[0]['priceHigh']-rs[0]['priceLow'],places=7)
            checked+=1
        self.assertEqual(checked,134)

    def test_range_allocation_and_conservation(self):
        _, bars = fixture()
        bar = replace(bars[0], open=D(10), low=D(10), high=D(14), close=D(14), volume=D(80))
        result = profile([bar], cutoff=bar.completed_at, edges=[10,11,13,14])
        self.assertEqual(result['up'], (D(20),D(40),D(20)))
        self.assertEqual(sum(result['total']), bar.volume)
        self.assertEqual((result['val'],result['vah']), (D(11),D(13)))

    def test_flat_bar_at_grid_top_and_future_rejection(self):
        _, bars = fixture()
        bar = replace(bars[0], open=D(14), low=D(14), high=D(14), close=D(14), volume=D(80))
        result = profile([bar], cutoff=bar.completed_at, edges=[10,12,14])
        self.assertEqual(result['total'], (D(0),D(80)))
        with self.assertRaises(ValueError):
            profile([bar], cutoff=bar.start_ist, edges=[10,12,14])
        with self.assertRaises(ValueError):
            profile([bar], cutoff=bar.completed_at, edges=[10,12,13])
