"""ER03C integration and output-contract tests; no duplicated indicator engine."""
import json
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from experiments.earnings_reaction_v1.er03_chart_upgrade import (
    ROOT, ER, OUT, ASSETS, assemble, load_inputs, json_text, pilots, preserved)
from experiments.earnings_reaction_v1.er03 import BENCH, read_csv


class ChartAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data,_=load_inputs()
        cls.bench={str(c.start_ist.date()):c for c in cls.data[BENCH]}

    def test_no_fabricated_records_or_profiles_for_other_pilots(self):
        p=read_csv(ER/'pilot-companies.csv')[0]
        out=assemble(p['symbol'],p['instrument_key'],self.data[p['instrument_key']],self.bench)
        self.assertEqual(out['statuses']['profile'],'UNAVAILABLE')
        self.assertTrue(all(r['profile'] is None for r in out['rows']))
        self.assertTrue(any(r['simple']['original_hve'] is None for r in out['rows']))

    def test_preserved_ohlcv_and_indicator_session_coordinates(self):
        for p in read_csv(ER/'pilot-companies.csv'):
            bars=self.data[p['instrument_key']]
            payload=assemble(p['symbol'],p['instrument_key'],bars,self.bench)
            rows=payload['rows']
            self.assertEqual(len(rows),len(bars))
            self.assertEqual(len({r['date'] for r in rows}),len(rows))
            for row,c in zip(rows,bars):
                self.assertEqual(row['date'],str(c.start_ist.date()))
                for k in ('open','high','low','close','volume'):self.assertEqual(row[k],getattr(c,k))
                self.assertEqual(str(row['simple']['bar_start_ist'].date()),row['date'])
                self.assertEqual(row['dss_pct'],row['rvol20']*100 if row['rvol20'] is not None else None)

    def test_json_preserves_null_zero_and_false_distinctions(self):
        self.assertEqual(json.loads(json_text([None,Decimal(0),False])),[None,0,False])
        with self.assertRaises(ValueError):json_text(float('nan'))

    def test_rs_missing_benchmark_date_resets_existing_warmup(self):
        p=read_csv(ER/'pilot-companies.csv')[1];bars=self.data[p['instrument_key']]
        missing='2026-07-01';bm=dict(self.bench);bm.pop(missing)
        rows=assemble(p['symbol'],p['instrument_key'],bars,bm)['rows']
        start=next(i for i,r in enumerate(rows) if r['date']==missing)
        self.assertTrue(all(r['rs55'] is None for r in rows[start:start+56]))
        self.assertIsNotNone(rows[start+56]['rs55'])
        self.assertEqual(len(rows),392)

    def test_future_result_window_is_not_backfilled_into_earlier_data(self):
        p=read_csv(ER/'pilot-companies.csv')[1];bars=self.data[p['instrument_key']]
        payload=assemble(p['symbol'],p['instrument_key'],bars,self.bench)
        dates={r['date'] for r in payload['rows']}
        for e in payload['events']:
            if e['plotted_session']:
                self.assertIn(e['plotted_session'],dates)
                self.assertGreaterEqual(e['plotted_session'],e['date'])

    def test_pilot_generation_rejects_changed_reference(self):
        if not (OUT/'reference-browser-validation.json').exists():self.skipTest('Browser gate not yet produced')
        with patch('experiments.earnings_reaction_v1.er03_chart_upgrade.digest',return_value='tampered'):
            with self.assertRaises(ValueError):pilots()


@unittest.skipUnless((OUT/'TIPSMUSIC-data.json').is_file(),'Reference not built')
class DeliveredReferenceTests(unittest.TestCase):
    def test_reference_output_and_numerical_gate(self):
        d=json.loads((OUT/'TIPSMUSIC-data.json').read_text())
        self.assertEqual(len(d['rows']),392)
        self.assertEqual(sum(r['profile'] is not None for r in d['rows']),71)
        self.assertEqual(sum(r['rs55'] is not None for r in d['rows']),172)
        self.assertEqual(json.loads((OUT/'numerical-validation.json').read_text())['failures'],0)

    def test_original_research_files_unchanged(self):
        before=json.loads((OUT/'preservation-before.json').read_text());after=preserved()
        self.assertFalse([p for p,h in before.items() if after.get(p)!=h])

    def test_offline_assets_and_original_indicator_defaults(self):
        content=(OUT/'TIPSMUSIC.html').read_text()
        self.assertIn('assets/lightweight-charts-5.0.9.js',content)
        self.assertIn("connect-src 'none'",content)
        self.assertNotIn('<script src="http',content)
        self.assertTrue((OUT/'assets/lightweight-charts-5.0.9.js').is_file())
        d=json.loads((OUT/'TIPSMUSIC-data.json').read_text())
        self.assertEqual(d['statuses']['simple'],'BEHAVIORALLY_RECONSTRUCTED')
        self.assertTrue(all(r['simple']['version']=='simple-volume-reference-v4' for r in d['rows']))


if __name__=='__main__':unittest.main()
