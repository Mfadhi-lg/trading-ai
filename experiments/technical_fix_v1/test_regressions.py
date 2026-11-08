"""TECH-FIX regression tests use original responses and adversarial boundaries."""
import csv, json, unittest
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .policy import inventory, eligible_decision_value, refresh_status
from .repair import ROOT, OUT, T, N, cash, sha, read, jsdata
from experiments.technical_integration_v2.narrowed_validation import latest_completed, validate

class TechnicalFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars=read(N/'VALIDATED_PROVIDER_CANDLES.csv')
        cls.inv=json.loads((OUT/'CURRENT_DATA_INVENTORY.json').read_text())
        cls.data=jsdata(T/'charts/global-market-data.js','window.TECH06_GLOBAL=')

    def test_live_counts_follow_new_acquisition_instead_of_frozen_status(self):
        extra=dict(next(r for r in self.bars if r['series']=='GIFT_NIFTY' and r['unit']=='days'))
        extra['provider_timestamp']='2026-11-02T00:00:00+05:30'
        inv=next(r for r in inventory(self.bars+[extra]) if r['series']=='GIFT_NIFTY')
        self.assertEqual(inv['accepted_daily'],238)
        future=next(r for r in refresh_status(self.data['status'],self.bars+[extra]) if r['series']=='GIFT_NIFTY')
        self.assertEqual(future['accepted_daily_observations'],238)
        self.assertEqual(future['last_date'],'2026-11-02')
        missing=next(r for r in refresh_status(self.data['status'],[r for r in self.bars if r['series']!='GIFT_NIFTY']) if r['series']=='GIFT_NIFTY')
        self.assertEqual(missing['status'],'UNAVAILABLE')
        self.assertEqual(missing['accepted_daily_observations'],0)
        for item in self.inv:
            status=next(r for r in self.data['status'] if r['series']==item['series'])
            self.assertEqual(status['accepted_daily_observations'],item['accepted_daily'])
            self.assertEqual(status['accepted_minute_observations'],item['accepted_minute'])
        gift=next(r for r in self.inv if r['series']=='GIFT_NIFTY')
        self.assertEqual((gift['accepted_daily'],gift['accepted_minute']),(237,13216))
        text=(T/'TECHNICAL_BLOCK_STATUS.md').read_text()
        self.assertIn('237 accepted daily candles and 13216 sampled minute',text)
        self.assertNotIn('untested',text.lower())

    def test_invalid_raw_candles_never_enter_any_accepted_channel(self):
        plotted=jsdata(T/'charts/global-minute-data.js','window.TECH06_GLOBAL_BARS=')
        for q in json.loads((N/'QUARANTINED_CANDLES.json').read_text()):
            original=json.loads((ROOT/q['source_file']).read_text())['data']['candles'][q['raw_row_index']]
            self.assertEqual(original,q['raw_row']);self.assertEqual(sha(ROOT/q['source_file']),q['source_sha256'])
            self.assertFalse(any(r['series']==q['series'] and r['unit']=='days' and r['provider_timestamp']==original[0] for r in self.bars))
            self.assertFalse(any(r['series']==q['series'] and r['unit']=='days' and r['timestamp']==original[0] for r in plotted))
            contexts=read(N/'INDIAN_CHECKPOINT_CONTEXT.csv')
            self.assertFalse(any(r['series']==q['series'] and r['channel']=='PROVIDER_DAILY' and r['source_observation_timestamp']==original[0] for r in contexts))
            req=dict(number=q['number'],series=q['series'],unit='days',interval=1,from_date='2025-12-01',to_date='2026-10-30')
            accepted,rejected,_=validate([original],req,'2026-11-05T00:00:00+00:00')
            self.assertFalse(accepted);self.assertEqual(rejected[0]['reason'],'invalid_OHLC_constraints')
        # Aggregates count only structurally admitted observations.
        self.assertEqual(sum(r['accepted_daily']+r['accepted_minute'] for r in self.inv),len(self.bars))

    def test_us_cash_mapping_is_quarantined_without_price_fitted_shift(self):
        rows=read(OUT/'US_SESSION_RECONCILIATION.csv')
        self.assertEqual(Counter(r['series'] for r in rows if r['holiday']=='True'),{'DJIA':8,'SP500':9})
        self.assertTrue(all(r['verified_cash_session_date']=='' and r['date_shift_days']=='0' for r in rows))
        self.assertTrue(all(r['provider_bucket_date']==r['original_provider_timestamp'][:10] for r in rows))
        self.assertTrue(all(r['cash_session_status']=='QUARANTINED_INTERPRETATION' for rows in self.data['context'].values() for r in rows if r['series']=='DJIA'))
        self.assertFalse(cash('2026-07-03'));self.assertTrue(cash('2026-10-12'))

    def test_dst_clock_is_independent_of_provider_session_mapping(self):
        ny,ist=ZoneInfo('America/New_York'),ZoneInfo('Asia/Kolkata')
        for day,expected in [('2026-03-06','2026-03-07T02:30:00+05:30'),('2026-03-09','2026-03-10T01:30:00+05:30'),('2026-11-02','2026-11-03T02:30:00+05:30')]:
            self.assertEqual(datetime.fromisoformat(day+'T16:00:00').replace(tzinfo=ny).astimezone(ist).isoformat(),expected)

    def test_completed_bar_boundary_and_future_perturbation(self):
        for clock in ('09:15','18:00'):
            cutoff=datetime.fromisoformat('2026-06-19T'+clock+':00+05:30')
            rows=[dict(end_ist=(cutoff+timedelta(minutes=k)).isoformat(),value=k) for k in (-1,0,1)]
            self.assertEqual(latest_completed(rows,cutoff)['value'],-1)
            rows[1]['value']=1e12;rows[2]['value']=-1e12
            self.assertEqual(latest_completed(rows,cutoff)['value'],-1)

    def test_historical_delivery_gate_fails_closed(self):
        cutoff=datetime.fromisoformat('2026-06-19T09:15:00+05:30')
        row=dict(category='VERIFIED_DECISION_TIME',identity_status='VERIFIED',series='USDINR',
            bar_end_ist='2026-06-19T09:13:00+05:30',historical_available_at='2026-06-19T09:14:00+05:30',
            delivered_at='2026-06-19T09:14:30+05:30',publication_evidence='fixture',delivery_evidence='fixture',retrospective_value=89)
        self.assertEqual(eligible_decision_value(row,cutoff),89)
        for key,val in [('delivery_evidence',''),('identity_status','PARTIAL'),('category','RETROSPECTIVE_ONLY'),('delivered_at',cutoff.isoformat()),('delivered_at','2026-11-05T00:00:00+00:00'),('bar_end_ist',cutoff.isoformat()),('historical_available_at','2026-06-19T09:14:00')]:
            self.assertIsNone(eligible_decision_value(dict(row,**{key:val}),cutoff),key)
        self.assertIsNone(eligible_decision_value(dict(row,series='DJIA'),cutoff))

    def test_all_checkpoints_keep_availability_states_separate(self):
        rows=read(OUT/'HISTORICAL_AVAILABILITY_AUDIT.csv')
        self.assertGreaterEqual(len(rows),8172)
        self.assertEqual({r['phase'] for r in rows},{'PREOPEN','POSTCLOSE'})
        self.assertTrue(all(not r['decision_feature_value'] for r in rows))
        self.assertIn('CONSERVATIVE_TIME_BOUNDED',{r['acceptance_state'] for r in rows})
        self.assertNotIn('VERIFIED_DECISION_TIME',{r['acceptance_state'] for r in rows})
        for r in rows:
            if r['bar_end_ist'] and r['retrospective_value']:
                self.assertLess(datetime.fromisoformat(r['bar_end_ist']),datetime.fromisoformat(r['cutoff_ist']))

    def test_all_catalog_indices_and_only_bounded_priority_requests(self):
        rows=read(OUT/'NSE_INDEX_COVERAGE.csv');cat=[r for r in rows if r['instrument_key']]
        self.assertEqual(len(cat),139);self.assertEqual(sum(r['daily_history_exists']=='True' for r in cat),5)
        manifest=json.loads((OUT/'PRIORITY_NSE_ACQUISITION_MANIFEST.json').read_text())
        self.assertFalse(manifest['authorized']);self.assertEqual(manifest['request_count'],7)
        self.assertEqual(manifest['executed_requests'],0)
        existing={r['instrument_key'] for r in cat if r['daily_history_exists']=='True'}
        self.assertFalse(existing & {r['instrument_key'] for r in manifest['requests']})
        self.assertTrue(all(r['unit']=='days' and r['interval']==1 for r in manifest['requests']))

    def test_identity_and_corporate_actions_never_promoted(self):
        defs=read(OUT/'GLOBAL_INSTRUMENT_DEFINITIONS.csv')
        self.assertEqual(len(defs),6);self.assertTrue(all(r['identity_verification']=='PARTIAL' for r in defs))
        self.assertIn('NOT_NASDAQ_COMPOSITE',next(r for r in defs if r['series']=='US_TECH100')['official_benchmark_identity'])
        ca=read(OUT/'CORPORATE_ACTION_GAPS.csv');self.assertEqual(len(ca),17)
        self.assertTrue(all(r['share_unit_factor']=='1_PROVISIONAL_UNCHANGED' and r['status']=='RETROSPECTIVE_ONLY' for r in ca))

    def test_only_reviewed_presentation_and_source_files_changed(self):
        baseline=json.loads((OUT/'evidence/BASELINE_HASHES.json').read_text())
        allowed={'experiments/technical_integration_v2/narrowed_charts.py','experiments/technical_integration_v2/charts.py',
            'artifacts/technical-integration-v2/charts/global-market-data.js','artifacts/technical-integration-v2/charts/global-context.js',
            'artifacts/technical-integration-v2/charts/global-market.html','artifacts/technical-integration-v2/charts/sector-rotation.html',
            'artifacts/technical-integration-v2/TECHNICAL_BLOCK_STATUS.md'}
        if (ROOT/'artifacts/technical-fix-v2/TECH_FIX_01_INTEGRATION.json').exists():
            # TECH-FIX-02 adds a chart entry and a general NSE admission option
            # to the existing rotation engine, and updates baseline-test scope.
            # Original indicator formulas, raw responses and chart payloads
            # remain frozen; the added view is independently browser-tested.
            allowed.update({'experiments/technical_integration_v2/build.py',
                'experiments/technical_integration_v2/test_narrowed.py',
                'artifacts/technical-integration-v2/charts/index.html'})
        changed={p for p,h in baseline.items() if sha(ROOT/p)!=h}
        self.assertEqual(changed,allowed)
        # Indicator sources, raw responses, acquisition manifests, and embedded chart payloads are frozen.
        for p,h in baseline.items():
            if p not in allowed:self.assertEqual(sha(ROOT/p),h,p)

if __name__=='__main__':unittest.main(verbosity=2)
