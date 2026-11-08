import copy
from decimal import Decimal
import json
import sqlite3
import unittest

from experiments.finance_only_v1.engine import Statements, analyse, growth, metric, shifted
from experiments.finance_only_v1.pilot import AREA, SNAPSHOT, open_snapshot, source_validation


class FinanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle=json.loads((AREA/'reports/evidence.json').read_text())
        cls.payloads={p['symbol']:p for p in cls.bundle['companies']}

    def test_growth_positive_and_signed_loss(self):
        self.assertEqual(growth(metric(120),metric(100))['value'],'20.0000')
        self.assertEqual(growth(metric(-20),metric(100))['value'],'-120.0000')
        self.assertEqual(growth(metric(0),metric(100))['value'],'-100.0000')

    def test_invalid_bases_and_cagr_endpoints(self):
        for base in (0,-10):
            self.assertEqual(growth(metric(20),metric(base))['status'],'INVALID_COMPARATOR')
        for end in (0,-10):
            self.assertEqual(growth(metric(end),metric(100),3)['status'],'INVALID_COMPARATOR')

    def test_cagr_exact_roots(self):
        self.assertEqual(growth(metric('133.1'),metric(100),3)['value'],'10.0000')
        self.assertEqual(growth(metric('161.051'),metric(100),5)['value'],'10.0000')

    def test_missing_is_not_zero(self):
        self.assertIsNone(growth(metric(),metric(100))['value'])
        self.assertEqual(growth(metric(),metric(100))['status'],'MISSING')

    def test_calendar_alignment(self):
        self.assertEqual(shifted('2024-02-29',-12),'2023-02-28')
        self.assertEqual(shifted('2026-03-31',-3),'2025-12-31')

    def test_nonfinancial_gate(self):
        for sector in ('bank','nbfc','insurer','unknown'):
            p=copy.deepcopy(self.payloads['TVSMOTOR']);p['sector']=sector
            with self.assertRaises(ValueError): analyse(p)

    def test_mixed_scope_rejected(self):
        p=copy.deepcopy(self.payloads['TVSMOTOR'])
        p['observations'][0]['reporting_scope']='consolidated'
        with self.assertRaises(ValueError): analyse(p)

    def test_mixed_versions_rejected(self):
        p=copy.deepcopy(self.payloads['TVSMOTOR'])
        p['observations'][0]['parse_id']=999999
        with self.assertRaises(ValueError): analyse(p)

    def test_ambiguous_observation_rejected(self):
        p=copy.deepcopy(self.payloads['TVSMOTOR'])
        p['observations'].append(copy.deepcopy(p['observations'][0]))
        with self.assertRaises(ValueError): analyse(p)

    def test_future_observation_rejected(self):
        p=copy.deepcopy(self.payloads['TVSMOTOR'])
        p['observations'][0]['available_at_utc']='2026-10-23T00:00:00+00:00'
        with self.assertRaises(ValueError): analyse(p)

    def test_offset_timestamps_are_compared_as_instants(self):
        p=copy.deepcopy(self.payloads['TVSMOTOR'])
        p['observations'][0]['available_at_utc']='2026-10-22T05:30:00+05:30'
        analyse(p)  # Exactly at the cutoff, not lexicographically after it.

    def test_period_sentinel_rejected(self):
        p=copy.deepcopy(self.payloads['TVSMOTOR'])
        p['observations'][0]['period_end']='1970-01-31'
        with self.assertRaises(ValueError): analyse(p)

    def test_midmonth_period_requires_review(self):
        p=copy.deepcopy(self.payloads['TVSMOTOR'])
        p['observations'][0]['period_end']='2015-03-15'
        with self.assertRaises(ValueError): analyse(p)

    def mutate(self,code,value,**extra):
        p=copy.deepcopy(self.payloads['BAJAJ-AUTO'])
        row=next(r for r in p['observations'] if r['section_code']=='pnl' and
                 r['period_end']=='2026-03-31' and r['line_item_code']==code)
        row['value_decimal']=value;row.update(extra)
        return analyse(p)['annual'][-1]['metrics']

    def test_zero_interest_is_undefined_not_infinite(self):
        self.assertEqual(self.mutate('interest','0')['interest_coverage']['status'],'INVALID_COMPARATOR')

    def test_negative_pat_cfo_ratio_undefined(self):
        self.assertEqual(self.mutate('net_profit','-100')['cfo_to_pat']['status'],'INVALID_COMPARATOR')

    def test_null_without_missing_kind(self):
        self.assertEqual(self.mutate('net_profit',None,missing_kind=None)['pat']['status'],'MISSING')

    def test_missing_kind_with_number_rejected(self):
        with self.assertRaises(ValueError): self.mutate('net_profit','0',missing_kind='blank')

    def test_nonfinite_numbers_suppressed(self):
        for value in ('NaN','Infinity','not numeric'):
            self.assertEqual(self.mutate('net_profit',value)['pat']['status'],'INVALID_NUMBER')

    def test_wrong_unit_scale_suppressed(self):
        m=self.mutate('sales','58732',scale_multiplier='1')
        self.assertEqual(m['revenue']['status'],'INCOMPATIBLE_UNIT')
        self.assertIsNone(m['net_margin']['value'])

    def test_gap_suppresses_cagr_and_consistency(self):
        p=copy.deepcopy(self.payloads['BAJAJ-AUTO'])
        p['observations']=[r for r in p['observations'] if not
            (r['section_code']=='pnl' and r['period_end']=='2024-03-31')]
        a=analyse(p)
        self.assertEqual(a['annual'][-1]['metrics']['revenue_cagr_3y']['status'],'NONCOMPARABLE')
        self.assertIsNone(a['consistency']['pat_positive_years_of_5'])

    def test_no_mixed_year_cfo_or_opening_equity(self):
        p=copy.deepcopy(self.payloads['BAJAJ-AUTO'])
        p['observations']=[r for r in p['observations'] if not
            (r['section_code']=='cash_flow' and r['period_end']=='2026-03-31') and not
            (r['section_code']=='balance_sheet' and r['period_end']=='2025-03-31')]
        m=analyse(p)['annual'][-1]['metrics']
        self.assertIsNone(m['cfo_to_pat']['value'])
        self.assertIsNone(m['roe']['value'])

    def test_unsupported_features_not_proxied(self):
        m=analyse(self.payloads['BAJAJ-AUTO'])['annual'][-1]['metrics']
        self.assertEqual(m['capex']['status'],'UNSUPPORTED')
        self.assertEqual(m['roce']['status'],'UNSUPPORTED')
        self.assertEqual(m['implied_capex']['value'],'491.0000')

    def test_hand_checked_bajaj_financials(self):
        # Fixed independently worked FY2026 example from original retained cells:
        # Sales 58,732; PAT 9,825; OP 12,024; CFO 8,961; FCF 8,470.
        # Equity: opening 32,147 and closing 34,975; average 33,561.
        a=analyse(self.payloads['BAJAJ-AUTO']);m=a['annual'][-1]['metrics']
        self.assertEqual(m['net_margin']['value'],'16.7285')
        self.assertEqual(m['operating_margin']['value'],'20.4727')
        self.assertEqual(m['cfo_to_pat']['value'],'0.9121')
        self.assertEqual(m['roe']['value'],'29.2751')
        self.assertEqual(m['interest_coverage']['value'],'364.1111')
        self.assertEqual(a['quarters'][-1]['metrics']['revenue_yoy']['value'],'37.0312')

    def test_india_cements_loss_base_and_negative_cash(self):
        m=analyse(self.payloads['INDIACEM'])['annual'][-1]['metrics']
        self.assertEqual(m['pat_yoy']['status'],'INVALID_COMPARATOR')
        self.assertEqual(m['pat_cagr_3y']['status'],'INVALID_COMPARATOR')
        self.assertEqual(m['cfo_to_pat']['value'],'-0.5077')

    def test_provider_blank_working_capital_is_not_zero(self):
        m=analyse(self.payloads['OFSS'])['annual'][-1]['metrics']
        self.assertIsNone(m['inventory_days']['value'])
        self.assertIsNone(m['recomputed_ccc']['value'])

    def test_rounded_source_ccc_difference_visible(self):
        a=analyse(self.payloads['TVSMOTOR'])
        self.assertTrue(any('2026-03-31: source CCC differs' in w for w in a['warnings']))

    def test_all_2304_source_cells_reconcile(self):
        count=0
        for p in self.payloads.values():
            v=source_validation(p['observations'],p['source_cells'])
            self.assertEqual(v['checked'],v['matched'])
            count+=v['checked']
        self.assertEqual(count,2304)

    def test_changed_source_cell_is_detected(self):
        p=copy.deepcopy(self.payloads['TVSMOTOR'])
        p['source_cells'][0]['normalized_display_text']='999999'
        self.assertTrue(source_validation(p['observations'],p['source_cells'])['failed'])

    def test_changed_period_alignment_is_detected(self):
        p=copy.deepcopy(self.payloads['TVSMOTOR'])
        p['observations'][0]['period_end']='2016-03-31'
        self.assertTrue(source_validation(p['observations'],p['source_cells'])['failed'])

    def test_facade_metrics_agree_with_engine_and_retain_lineage(self):
        for p in self.payloads.values():
            r=analyse(p);q=r['quarters'][-1]['metrics']
            known={x['feature_code']:x for x in p['facade']['features']}
            for name in ('revenue','pat','eps'):
                self.assertEqual(Decimal(q[name]['value']),Decimal(known['quarter_'+name]['value']))
            for name in ('revenue','pat'):
                # Canonical facade rounds derived growth to 2dp.
                if q[name+'_yoy']['value'] is None:
                    self.assertEqual(q[name+'_yoy']['status'],known['quarter_'+name+'_yoy']['status'])
                    self.assertIsNone(known['quarter_'+name+'_yoy']['value'])
                else:
                    self.assertLess(abs(Decimal(q[name+'_yoy']['value'])-Decimal(known['quarter_'+name+'_yoy']['value'])),Decimal('.0051'))
            ids={o['financial_observation_id'] for o in p['observations']}
            for period in r['annual']+r['quarters']:
                for m in period['metrics'].values():
                    self.assertTrue(set(m['inputs'])<=ids)
                    if m['value'] is not None:self.assertTrue(m['inputs'])

    def test_no_focus_truth_or_action(self):
        for p in self.payloads.values():
            r=analyse(p)
            self.assertEqual(r['focus_member'],p['focus_member'])
            self.assertNotIn('decision',r)
            self.assertNotIn('add_to_focus',r)
        self.assertEqual(analyse(self.payloads['BAJAJ-AUTO'])['assessment']['passed'],5)

    def test_snapshot_preservation_receipt(self):
        s=self.bundle['snapshot']
        self.assertEqual(s['before'],s['after'])
        self.assertEqual(s['sha256'],s['sha256_after'])

    def test_reader_refuses_other_paths(self):
        with self.assertRaises(ValueError):open_snapshot(AREA/'unapproved.db')

    def test_live_reader_denies_mutation(self):
        if not SNAPSHOT.exists():self.skipTest('historical snapshot not available; replay remains offline')
        c,_=open_snapshot(SNAPSHOT)
        try:
            for sql in ('CREATE TABLE finance_test(x)', 'DELETE FROM companies',
                        'CREATE TEMP TABLE finance_test(x)', 'PRAGMA query_only=OFF'):
                with self.assertRaises(sqlite3.DatabaseError):c.execute(sql)
            self.assertEqual(c.execute('SELECT count(*) FROM companies').fetchone()[0],4428)
        finally:c.close()

    def test_replay_identical_to_delivered_reports(self):
        expected=json.loads((AREA/'reports/report.json').read_text())
        actual=[analyse(p) for p in self.bundle['companies']]
        self.assertEqual(actual,expected)


if __name__=='__main__':unittest.main()
