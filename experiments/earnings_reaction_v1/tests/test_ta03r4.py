"""TA-03R4 regression tests: independent boundaries and source-context integration."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from fractions import Fraction
import unittest

from experiments.earnings_reaction_v1.tests.test_indicator_alternatives import fixture
from experiments.earnings_reaction_v1.indicator_alternatives import volume_features,aggregate
from experiments.earnings_reaction_v1.indicators import (
    calculate_chart_rs,calculate_daily,merge_daily_dtr,dtr_source_timeframe)


class PocketBoundaryTests(unittest.TestCase):
    def bars(self):
        sessions,bars=fixture(1,60)
        closes=[11,10,11,11,11,11,11,11,11,11,12,11]
        volumes=[1000,30,1,1,1,1,1,1,1,1,31,9999]
        return sessions,tuple(replace(c,close=D(p),volume=D(v)) for c,p,v in zip(bars,closes,volumes))

    def test_exact_ten_prior_bars_first_evaluable(self):
        _,bars=self.bars()
        rows=volume_features(bars[:11],cutoff=bars[10].end_ist)
        self.assertEqual(len(rows),11)
        self.assertTrue(all(r['pocket_pivot'] is None for r in rows[:10]))
        self.assertIs(rows[10]['pocket_pivot'],True) # 31 > down-bar volume 30

    def test_equal_is_false_strictly_greater_is_true(self):
        _,bars=self.bars()
        for v,expected in [(29,False),(30,False),(31,True)]:
            sample=bars[:10]+(replace(bars[10],volume=D(v)),)
            self.assertIs(volume_features(sample,cutoff=sample[-1].end_ist)[-1]['pocket_pivot'],expected)

    def test_no_qualifying_down_bar(self):
        _,bars=self.bars()
        sample=tuple(replace(c,close=D(11)) for c in bars[:10])+(bars[10],)
        self.assertIs(volume_features(sample,cutoff=sample[-1].end_ist)[-1]['pocket_pivot'],False)

    def test_historical_cutoff_and_prefix(self):
        _,bars=self.bars()
        prefix=volume_features(bars[:11],cutoff=bars[10].end_ist)
        full=volume_features(bars,cutoff=bars[-1].end_ist)
        self.assertEqual(prefix,full[:11])
        with self.assertRaises(ValueError):
            volume_features(bars,cutoff=bars[10].end_ist)


class SourceIntegrationTests(unittest.TestCase):
    def test_rs55_chart_formula_and_strict_benchmark(self):
        sessions,minute=fixture(28,375)
        bars=aggregate(minute,sessions,'4-hour',cutoff=sessions[-1].closes_at)
        stock=tuple(replace(c,open=D(100+i),low=D(100+i),close=D(100+i),high=D(100+i)) for i,c in enumerate(bars))
        benchmark=tuple(replace(c,instrument_key='NSE_INDEX|NIFTY MIDSML 400',
            open=D(200+i),low=D(200+i),close=D(200+i),high=D(200+i)) for i,c in enumerate(bars))
        rows=calculate_chart_rs(stock,benchmark,cutoff=sessions[-1].closes_at,timeframe='4-hour')
        self.assertTrue(all(r['rs'] is None for r in rows[:55]))
        self.assertLess(abs(Fraction(rows[55]['rs'])-(Fraction(155,100)/Fraction(255,200)-1)),Fraction(1,10**24))
        with self.assertRaises(ValueError):
            calculate_chart_rs(stock,tuple(replace(c,instrument_key='NSE_INDEX|Nifty 50') for c in benchmark),
                               cutoff=sessions[-1].closes_at,timeframe='4-hour')
        with self.assertRaises(ValueError):
            calculate_chart_rs(stock,benchmark[:-1],cutoff=sessions[-1].closes_at,timeframe='4-hour')
        self.assertEqual(rows[:-1],calculate_chart_rs(stock[:-1],benchmark[:-1],cutoff=stock[-2].end_ist,timeframe='4-hour'))

    def test_dtr_source_selector(self):
        for tf in ('Daily','4-hour','75-minute','1-hour'):
            self.assertEqual(dtr_source_timeframe(tf),'D')
        self.assertEqual(dtr_source_timeframe('Weekly'),'W')
        self.assertEqual(dtr_source_timeframe('Monthly'),'M')

    def test_historical_atr_lookahead_separate_from_causal(self):
        sessions,minute=fixture(22,375)
        daily=[]
        for s in sessions:
            mid=s.opens_at.replace(hour=0,minute=0)
            daily.append(replace(minute[0],start_ist=mid,end_ist=mid+timedelta(days=1),
                completed_at=s.closes_at,volume=D(100)))
        daily[-1]=replace(daily[-1],high=D(20))
        display=aggregate(minute,sessions,'4-hour',cutoff=sessions[-1].closes_at)
        rows=merge_daily_dtr(display,daily,cutoff=sessions[-1].closes_at,timeframe='4-hour')
        early=rows[-2]
        self.assertTrue(early['historical_uses_future'])
        self.assertEqual(early['causal_last_completed_daily']['atr14'],D(2))
        self.assertEqual(early['historical_display']['atr14'],D(18)/7) # (13*2+10)/14
        self.assertLessEqual(early['causal_dependency_at'],early['observation_at'])
        self.assertFalse(rows[-1]['historical_uses_future'])
        changed=daily[:-1]+[replace(daily[-1],high=D(30))]
        altered=merge_daily_dtr(display,changed,cutoff=sessions[-1].closes_at,timeframe='4-hour')
        self.assertEqual(early['causal_last_completed_daily'],altered[-2]['causal_last_completed_daily'])
        self.assertNotEqual(early['historical_display'],altered[-2]['historical_display'])

    def test_extracted_source_arithmetic_matches_retained_daily_values(self):
        from experiments.earnings_reaction_v1.compare_ta03r3 import load_retained,ROOT
        import csv
        batches=load_retained()
        result=calculate_daily(batches[0].candles,batches[2].candles,cutoff=min(b.retrieved_at_utc for b in batches))
        with (ROOT/'artifacts/earnings-reaction-v1/ta03r/daily-indicators.csv').open() as stream:
            prior=list(csv.DictReader(stream))
        for actual,old in zip(result,prior):
            for name in ('atr','dtr','dtr_pct','rvol_ratio','rvol_pct','rs','rs_mean'):
                self.assertEqual(getattr(actual,name),D(old[name]) if old[name] else None)
        self.assertEqual(len(result),len(prior))


class RetrievalScopeTests(unittest.TestCase):
    def test_exact_scope_and_provider_limits(self):
        from experiments.earnings_reaction_v1.retrieve_ta03r4 import REQUESTS,ResearchRequest,manifest
        from experiments.earnings_reaction_v1.candles import CandleRequest
        self.assertEqual(len(REQUESTS),9)
        self.assertTrue(all(q.to_date.isoformat() <= '2026-10-30' for q in REQUESTS))
        self.assertEqual(manifest()['automatic_retries'],0)
        for q in REQUESTS:
            self.assertIsInstance(q,CandleRequest)
            if q.unit=='minutes':
                self.assertLessEqual((q.to_date-q.from_date).days,27)
        q=REQUESTS[-1]
        with self.assertRaises(ValueError):
            ResearchRequest(q.instrument_key,q.unit,5,q.from_date,q.to_date)

    def test_old_request_contract_not_weakened(self):
        from experiments.earnings_reaction_v1.candles import CandleRequest
        from experiments.earnings_reaction_v1.retrieve_ta03r4 import REQUESTS
        q=REQUESTS[-1]
        with self.assertRaises(ValueError):
            CandleRequest(q.instrument_key,q.unit,1,q.from_date,q.to_date)

    def test_invalid_approval_cannot_read_credential_or_call_transport(self):
        from experiments.earnings_reaction_v1.retrieve_ta03r4 import execute
        from unittest.mock import Mock
        supplier,transport=Mock(),Mock()
        with self.assertRaises(PermissionError):
            execute(approved_sha256='wrong',approval_reference='mock',token_supplier=supplier,transport=transport)
        supplier.assert_not_called()
        transport.assert_not_called()

    def test_mock_http_failure_stops_no_retry_or_error_body_retention(self):
        import tempfile,json
        from pathlib import Path
        from unittest.mock import Mock,patch
        from experiments.earnings_reaction_v1 import retrieve_ta03r4 as module
        with tempfile.TemporaryDirectory() as tmp,patch.object(module,'OUT',Path(tmp)):
            digest=module.write_plan()
            supplier=Mock(return_value='test_fake_token_with_adequate_length')
            transport=Mock(return_value=(b'private provider error body',429))
            self.assertFalse(module.execute(approved_sha256=digest,approval_reference='SYNTHETIC_UNIT_TEST_ONLY',
                token_supplier=supplier,transport=transport))
            self.assertEqual(transport.call_count,1)
            state=json.loads((Path(tmp)/'retrieval-execution.json').read_text())
            self.assertEqual(len(state['requests']),1)
            self.assertFalse(list((Path(tmp)/'provider').glob('*')))
            self.assertNotIn('private provider error body',(Path(tmp)/'retrieval-execution.json').read_text())
            self.assertNotIn('test_fake_token',(Path(tmp)/'retrieval-execution.json').read_text())
            with self.assertRaises(FileExistsError):
                module.execute(approved_sha256=digest,approval_reference='SYNTHETIC_UNIT_TEST_ONLY',
                    token_supplier=supplier,transport=transport)
            self.assertEqual(supplier.call_count,1)


class ComparisonContinuationTests(unittest.TestCase):
    def test_preservation_does_not_ignore_changed_research_evidence(self):
        import tempfile,json,hashlib
        from pathlib import Path
        from unittest.mock import patch
        from experiments.earnings_reaction_v1 import compare_ta03r4 as module
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out';out.mkdir()
            (root/'prior.csv').write_text('changed')
            (out/'preservation-before.json').write_text(json.dumps(dict(prior_artifacts={'prior.csv':hashlib.sha256(b'original').hexdigest()})))
            with patch.object(module,'ROOT',root),patch.object(module,'OUT',out):
                with self.assertRaises(ValueError):
                    module.package()

    def test_os_metadata_exception_records_both_hashes(self):
        import tempfile,json,hashlib
        from pathlib import Path
        from unittest.mock import patch
        from experiments.earnings_reaction_v1 import compare_ta03r4 as module
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out';out.mkdir()
            (root/'.DS_Store').write_text('changed')
            (out/'preservation-before.json').write_text(json.dumps(dict(prior_artifacts={'.DS_Store':hashlib.sha256(b'original').hexdigest()})))
            with patch.object(module,'ROOT',root),patch.object(module,'OUT',out):
                module.package()
            result=json.loads((out/'preservation-verification.json').read_text())
            self.assertTrue(result['research_evidence_unchanged'])
            self.assertFalse(result['all_original_bytes_unchanged'])
            self.assertEqual(len(result['os_metadata_changes']),1)
            self.assertEqual(result['os_metadata_changes'][0]['after_sha256'],hashlib.sha256(b'changed').hexdigest())

    def test_missing_provider_ledger_fails_before_chart_output(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from experiments.earnings_reaction_v1 import compare_ta03r4 as module
        with tempfile.TemporaryDirectory() as tmp,patch.object(module,'OUT',Path(tmp)):
            with self.assertRaises(PermissionError):
                module.build()
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_scorecard_rounding_and_unverified_units_are_separate(self):
        import tempfile,json,csv
        from pathlib import Path
        from unittest.mock import patch
        from experiments.earnings_reaction_v1 import compare_ta03r4 as module
        day='2026-08-18'
        readings=[dict(symbol='NSE:TIPSMUSIC',timeframe='Daily',bar_date=day,metric=k,
                       tradingview_display=v,reference_sha256='SYNTHETIC_TEST')
                  for k,v in [('rs55','1.00'),('each_bar_rvol20_pct','18.00'),('dss_rvol_display','20.00')]]
        values=dict(bar_date=day,observation_at=day+'T15:30:00+05:30',rs55=D('1.003'),
                    each_bar_rvol20_pct=D(19),candidate_a_pct=D(20),candidate_b_pct=D(21))
        with tempfile.TemporaryDirectory() as tmp,patch.object(module,'OUT',Path(tmp)):
            (Path(tmp)/'reference-inventory.json').write_text(json.dumps(dict(readings=readings)))
            module.scorecard({'Daily':[values]},'SYNTHETIC_TEST')
            with (Path(tmp)/'comparison-scorecard.csv').open() as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual([r['comparison_status'] for r in rows],['MATCHED','MISMATCHED','NOT_COMPARABLE','NOT_COMPARABLE'])
            self.assertEqual(rows[0]['absolute_difference'],'0.003')

    def test_shared_renderer_accepts_real_context_without_old_labels(self):
        import xml.etree.ElementTree as ET
        from experiments.earnings_reaction_v1.compare_ta03r3 import render_chart
        sessions,bars=fixture(1,60)
        features=volume_features(bars,cutoff=sessions[-1].closes_at)
        args=dict(symbol='TIPSMUSIC',reference_status='SYNTHETIC_TEST_ONLY',
            rs_values={c.completed_at:D(i)/100 for i,c in enumerate(bars)},base_minutes=1,
            candidate_b_note='synthetic unit-test fixture')
        a=render_chart('1-hour',bars,features,{},[],'test',**args)
        self.assertEqual(a,render_chart('1-hour',bars,features,{},[],'test',**args))
        self.assertNotIn('AETHER',a)
        self.assertNotIn('benchmark not retained',a)
        ET.fromstring(a[a.index('<svg'):])


if __name__=='__main__':
    unittest.main()
