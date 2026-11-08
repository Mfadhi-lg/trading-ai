"""Meaningful window, causal indicator and retained-data regression checks."""
import csv
import json
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from experiments.earnings_reaction_v1.er03 import (
    OUT, BASE, BENCH, IST, candle, features, performance, window_rows, digest, subsector_index,
)


class ER03Tests(unittest.TestCase):
    def test_strict_date_boundaries_and_horizons(self):
        rows=[{'date':f'2026-04-{d:02d}'} for d in range(20,30)]
        self.assertEqual([r['date'] for r in window_rows(rows,'2026-04-21','2026-04-25')],
                         ['2026-04-22','2026-04-23','2026-04-24'])
        self.assertEqual(len(window_rows(rows,'2026-04-21',None,3)),3)
        self.assertEqual(window_rows(rows,'2026-04-30',None,20),[])

    def test_actual_features_have_no_future_dependency(self):
        inventory=json.loads((OUT/'market-data-inventory.json').read_text())['datasets']
        def load(key):
            items=[r for r in inventory if r['unit']=='days' and r['instrument_key']==key]
            candles={}
            for item in items:
                p=BASE.parents[1]/item['source']['original_path']
                for row in json.loads(p.read_text())['data']['candles']:
                    c=candle(key,row);candles[c.start_ist]=c
            return tuple(candles[t] for t in sorted(candles))
        stock=load('NSE_EQ|INE716B01029');bm={str(c.start_ist.date()):c for c in load(BENCH)}
        full=features(stock,bm)
        for end in [56,280,320,len(stock)-1]:
            cutoff=stock[end-1].start_ist.date().isoformat()
            prefix=features(stock[:end],{k:v for k,v in bm.items() if k<=cutoff})
            self.assertEqual(prefix,full[:end])
        # Entire missing benchmark session never becomes an inner-join RS input.
        missing=str(stock[-20].start_ist.date());damaged=dict(bm);del damaged[missing]
        actual=features(stock,damaged)
        self.assertEqual(len(actual),len(stock))
        self.assertTrue(all(r['rs55'] is None for r in actual[-20:]))
        self.assertEqual([r['ema20'] for r in actual],[r['ema20'] for r in full])

    def test_return_extrema_and_close_drawdown(self):
        rows=[]
        for o,h,l,c in [(100,120,90,110),(110,115,80,88),(88,132,87,121)]:
            rows.append(dict(open=Decimal(o),high=Decimal(h),low=Decimal(l),close=Decimal(c),
                             ema20=Decimal(100),rvol20=Decimal(2),rs55=Decimal('.1')))
        r=performance(rows)
        self.assertEqual(r['return_pct'],21)
        self.assertEqual(r['maximum_rise_pct'],32)
        self.assertEqual(r['minimum_low_return_pct'],-20)
        self.assertEqual(r['maximum_close_drawdown_pct'],-20)

    def test_real_outputs_censoring_labels_and_preservation(self):
        def rows(name):
            with (OUT/name).open() as f:return list(csv.DictReader(f))
        pilot=rows('pilot-companies.csv');windows=rows('approximate-event-windows.csv')
        self.assertEqual(len(pilot),12)
        self.assertEqual(len({r['symbol'] for r in pilot}),12)
        self.assertTrue(all(r['reported_results_date'] for r in windows))
        self.assertEqual({q:len({r['symbol'] for r in windows if r['quarter']==q}) for q in ['FY2026-Q4','FY2027-Q1']},
                         {'FY2026-Q4':12,'FY2027-Q1':12})
        self.assertTrue(all(r['window_label']=='APPROXIMATE' for r in windows))
        results=rows('price-performance.csv');self.assertEqual(len(results),36)
        a=next(r for r in results if r['event_id']=='FY2026-Q4:AETHER')
        self.assertAlmostEqual(float(a['return_pct']),39.46,places=2)
        for r in results:
            if r['symbol']=='AETHER' and r['quarter']=='FY2027-Q1':
                self.assertEqual(r['right_censored'],'False');self.assertEqual(int(r['observations']),int(r['event_id'].split(':H')[1]))
        audit=json.loads((OUT/'label-audit.json').read_text())
        self.assertEqual(audit['explicit_rejected_labels'],0)
        preserved=json.loads((OUT/'preservation-verification.json').read_text())
        self.assertTrue(preserved['unchanged']);self.assertEqual(preserved['before'],preserved['after'])

    def test_acquisition_is_bounded_and_execution_matches_approval(self):
        plan=json.loads((OUT/'acquisition-manifest.json').read_text())
        self.assertEqual(plan['request_count'],12)
        self.assertEqual(plan['status'],'NOT_AUTHORIZED_NOT_EXECUTED')
        self.assertFalse(plan['token_accessed']);self.assertEqual(plan['network_requests_made'],0)
        self.assertTrue(all(r['unit']=='days' and r['interval']==1 for r in plan['requests']))
        self.assertEqual((OUT/'acquisition-manifest.sha256').read_text().split()[0],digest(OUT/'acquisition-manifest.json'))
        self.assertTrue(all(r['to_date']<='2026-10-30' for r in plan['requests']))

        execution=json.loads((OUT/'retrieval-execution.json').read_text())
        self.assertTrue(execution['complete']);self.assertIsNone(execution['failure'])
        self.assertEqual(execution['scope_sha256'],digest(OUT/'acquisition-manifest.json'))
        self.assertEqual(len(execution['requests']),12)
        for request,receipt in zip(plan['requests'],execution['requests']):
            self.assertEqual(request['url'],receipt['url'])
            self.assertEqual(receipt['http_status'],200)
            self.assertEqual(digest(BASE.parents[1]/receipt['retained_path']),receipt['response_sha256'])
        self.assertEqual(execution['automatic_retries'],0)

    def test_subsector_fixed_initial_equal_weights_and_gaps(self):
        def rows(values):return [dict(date=f'2026-01-{i+1:02d}',close=Decimal(v)) for i,v in enumerate(values)]
        data={'A':rows([100,200,100]),'B':rows([100,100,200]),'C':rows([100,100,100])}
        index,info=subsector_index(data)
        self.assertEqual(info['status'],'CALCULATED');self.assertEqual(index[0]['index_close'],100)
        self.assertEqual(index[1]['index_close'],Decimal(400)/3)
        self.assertEqual(index[2]['index_close'],Decimal(400)/3)
        missing={**data,'B':[data['B'][0],data['B'][2]]}
        index,info=subsector_index(missing)
        self.assertEqual(index,[]);self.assertEqual(info['status'],'MISSING_EXACT_PEER_ALIGNMENT')
        self.assertEqual(subsector_index({'A':data['A']})[1]['status'],'INSUFFICIENT_PEERS')

    def test_complete_daily_pilot_and_benchmark_warmup(self):
        with (OUT/'approximate-event-windows.csv').open() as f:windows=list(csv.DictReader(f))
        self.assertTrue(all(int(r['stock_pre_event_bars'])>=55 for r in windows))
        self.assertTrue(all(r['rs55_available_at_first_observation']=='True' for r in windows))
        self.assertTrue(all(r['right_censored']=='False' for r in windows))
        sector=json.loads((OUT/'subsector-comparisons.json').read_text())
        self.assertEqual({k:v['status'] for k,v in sector['coverage'].items()},
                         {k:'CALCULATED' for k in ['EMS','TRANSFORMERS','TYRES']})

    def test_retrieval_rejects_manifest_change_before_credentials(self):
        import tempfile
        from unittest.mock import patch
        from experiments.earnings_reaction_v1 import retrieve_er03
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);(folder/'acquisition-manifest.json').write_text('{}')
            with patch.object(retrieve_er03,'OUT',folder),patch('experiments.earnings_reaction_v1.execute_ta02d.secure_supplier') as supplier:
                with self.assertRaises(PermissionError):retrieve_er03.execute()
                supplier.assert_not_called()
                self.assertFalse((folder/'retrieval-execution.json').exists())

if __name__=='__main__':unittest.main()
