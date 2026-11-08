"""B1 cutoff and evidence regressions, using an isolated in-memory fixture."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

from .b1_feature_fixture import fixture, insert
from scrapper.pipeline.ml_contracts import company_quarter_features, MLContractError


class CompanyQuarterContractTests(unittest.TestCase):
    def setUp(self):
        self.conn, self.samples, self.fields = fixture()
        self.conn.execute('PRAGMA query_only=OFF')
        self.sample = self.samples[0]
        self.cid, self.pid = int(self.sample['company_id']), int(self.sample['parse_id'])

    def tearDown(self):
        self.conn.close()

    def result(self, **kwargs):
        defaults = dict(period='2026-06', sector='industrial')
        defaults.update(kwargs)
        return company_quarter_features(self.conn,self.cid,'2026-09-29T08:37:44Z', **defaults)

    def features(self, **kwargs):
        return {r.feature_code:r for r in self.result(**kwargs).features}

    def test_read_only_and_raw_lineage(self):
        self.conn.execute('PRAGMA query_only=ON')
        before=self.conn.total_changes
        result=self.result()
        self.assertEqual(result.status,'PIT_SAFE_FROM_CAPTURE')
        self.assertEqual(self.conn.total_changes,before)
        f=self.features()['quarter_revenue']
        self.assertEqual(f.value,'13896')
        self.assertEqual(f.scale_multiplier,'10000000')
        self.assertEqual(f.inputs[0]['financial_observation_id'],4562064)
        self.assertEqual(f.inputs[0]['parse_id'],self.pid)
        self.assertEqual(f.inputs[0]['blob_sha256'],self.sample['blob_sha256'])
        self.assertEqual(f.available_at_utc,self.sample['accepted_at_utc'])

    def test_old_cutoff_never_uses_future_capture(self):
        r=company_quarter_features(self.conn,self.cid,'2026-08-01T00:00:00Z',period='2026-06',sector='industrial')
        self.assertEqual(r.status,'EXPLORATORY_BACKFILL')
        self.assertEqual(r.completeness,'INSUFFICIENT_EVIDENCE')
        self.assertFalse(r.features)

    def test_revision_replay_uses_old_accepted_version(self):
        newpid=999999
        old=dict(self.conn.execute('SELECT * FROM parse_artifacts WHERE parse_id=?',(self.pid,)).fetchone())
        old.update(parse_id=newpid,parser_version='revision',parsed_at_utc='2026-09-25T00:00:00Z')
        insert(self.conn,'parse_artifacts',old)
        for row in self.conn.execute('SELECT * FROM financial_observations WHERE parse_id=?',(self.pid,)).fetchall():
            data=dict(row);data.update(parse_id=newpid,financial_observation_id=data['financial_observation_id']+100000000)
            if data['line_item_code']=='sales' and data['period_end']=='2026-06-30':data['value_decimal']='999'
            insert(self.conn,'financial_observations',data)
        a=dict(self.conn.execute('SELECT * FROM acceptance_events WHERE company_id=?',(self.cid,)).fetchone())
        a.pop('acceptance_id');a.update(parse_id=newpid,accepted_at_utc='2026-09-25T00:00:00Z');insert(self.conn,'acceptance_events',a)
        self.conn.execute('UPDATE accepted_page_versions SET parse_id=?,accepted_at_utc=? WHERE company_id=?',(newpid,a['accepted_at_utc'],self.cid))
        before=company_quarter_features(self.conn,self.cid,'2026-09-20T00:00:00Z',period='2026-06',sector='industrial')
        self.assertEqual(before.provenance['parse_id'],self.pid)
        self.assertEqual(next(f.value for f in before.features if f.feature_code=='quarter_revenue'),'13896')
        self.assertEqual(self.features()['quarter_revenue'].value,'999')

    def test_unknown_history_is_exploratory(self):
        self.conn.execute('DELETE FROM acceptance_events WHERE company_id=?',(self.cid,))
        self.assertEqual(self.result().status,'EXPLORATORY_BACKFILL')
        self.assertEqual(self.result(evidence_kind='retained_export').status,'EXPLORATORY_BACKFILL')

    def test_migrated_pointer_does_not_prove_history(self):
        self.conn.execute('UPDATE acceptance_events SET reason_json=? WHERE company_id=?',(json.dumps({'basis':'migration_003_current_pointer'}),self.cid))
        self.assertEqual(self.result().status,'EXPLORATORY_BACKFILL')

    def test_missing_not_zero_and_signed_losses(self):
        r=company_quarter_features(self.conn,719,'2026-09-29T08:37:44Z',period='2026-06',sector='industrial')
        self.assertEqual(next(f.value for f in r.features if f.feature_code=='quarter_pat'),'-15')
        self.conn.execute("UPDATE financial_observations SET value_decimal=NULL,missing_kind='not_disclosed' WHERE parse_id=? AND line_item_code='sales' AND period_end='2026-06-30'",(self.pid,))
        f=self.features()['quarter_revenue']
        self.assertIsNone(f.value);self.assertEqual(f.status,'MISSING')
        self.assertEqual(self.result().completeness,'INSUFFICIENT_EVIDENCE')

    def test_zero_comparator_and_unit_mismatch(self):
        self.conn.execute("UPDATE financial_observations SET value_decimal='0' WHERE parse_id=? AND line_item_code='sales' AND period_end='2026-03-31'",(self.pid,))
        self.assertEqual(self.features()['quarter_revenue_qoq'].status,'INVALID_COMPARATOR')
        self.conn.execute("UPDATE financial_observations SET scale_multiplier='1' WHERE parse_id=? AND line_item_code='sales'",(self.pid,))
        with self.assertRaises(MLContractError):self.result()

    def test_ytd_never_used_as_quarter(self):
        self.conn.execute("UPDATE financial_observations SET period_type='half_year' WHERE parse_id=? AND line_item_code='sales' AND period_end='2026-06-30'",(self.pid,))
        self.assertIsNone(self.features()['quarter_revenue'].value)
        self.assertEqual(self.result().completeness,'INSUFFICIENT_EVIDENCE')

    def test_scope_guard(self):
        with self.assertRaises(ValueError):self.result(scope='consolidated')
        self.conn.execute("UPDATE financial_observations SET reporting_scope='consolidated' WHERE parse_id=?",(self.pid,))
        with self.assertRaises(MLContractError):self.result()

    def test_bad_missing_naive_and_future_availability(self):
        for value in ('', '2026-09-16T05:15:48', '2099-01-01T00:00:00Z'):
            self.conn.execute('UPDATE financial_observations SET available_at_utc=? WHERE parse_id=?',(value,self.pid))
            with self.assertRaises(MLContractError):self.result()

    def test_observation_after_cutoff_is_excluded(self):
        self.conn.execute("UPDATE financial_observations SET available_at_utc='2026-09-30T00:00:00Z' WHERE parse_id=? AND line_item_code='sales'",(self.pid,))
        self.assertIsNone(self.features()['quarter_revenue'].value)
        self.assertIn('OBSERVATIONS_AFTER_CUTOFF_EXCLUDED',self.result().reasons)

    def test_banks_insurers_unknown_have_no_industrial_proxy(self):
        for sector in ('bank','nbfc','insurer','unknown'):
            self.assertIsNone(self.features(sector=sector)['quarter_operating_margin'].value)
        r=company_quarter_features(self.conn,138,'2026-09-29T08:37:44Z',period='2026-06',sector='bank')
        values={f.feature_code:f for f in r.features}
        self.assertEqual(values['quarter_revenue'].value,'5631')
        self.assertEqual(values['annual_deposits'].status,'PRESENT')
        self.assertEqual(self.result(sector='insurer').completeness,'INSUFFICIENT_EVIDENCE')

    def test_annual_calendar_and_dated_trend(self):
        r=company_quarter_features(self.conn,135,'2026-09-29T08:37:44Z',period='2026-06',sector='industrial')
        f=next(f for f in r.features if f.feature_code=='annual_cfo')
        self.assertEqual(f.period_end,'2025-12-31')
        self.assertEqual(f.period_type,'fiscal_year')
        self.assertTrue(all(t.period_type=='quarter' and t.inputs for t in r.trends))

    def test_source_independent_and_ambiguous_sources_fail(self):
        self.conn.execute("UPDATE acceptance_events SET source='owner_import' WHERE company_id=?",(self.cid,))
        self.conn.execute("UPDATE accepted_page_versions SET source='owner_import' WHERE company_id=?",(self.cid,))
        self.assertEqual(self.result().provenance['source'],'owner_import')
        a=dict(self.conn.execute('SELECT * FROM acceptance_events WHERE company_id=?',(self.cid,)).fetchone())
        a.pop('acceptance_id');a['source']='other';insert(self.conn,'acceptance_events',a)
        with self.assertRaises(MLContractError):self.result()
        self.assertEqual(self.result(source='owner_import').provenance['source'],'owner_import')

    def test_bad_blob_and_acceptance_before_parse(self):
        self.conn.execute("UPDATE response_blobs SET content_sha256='' WHERE blob_id=?",(int(self.sample['blob_id']),))
        with self.assertRaises(MLContractError):self.result()
        self.conn.execute('UPDATE response_blobs SET content_sha256=? WHERE blob_id=?',(self.sample['blob_sha256'],int(self.sample['blob_id'])))
        self.conn.execute("UPDATE parse_artifacts SET parsed_at_utc='2026-09-28T00:00:00Z' WHERE parse_id=?",(self.pid,))
        with self.assertRaises(MLContractError):self.result()

    def test_version_timezone_and_unknown_availability_basis(self):
        with self.assertRaises(MLContractError):self.result(feature_version='v2')
        with self.assertRaises(MLContractError):company_quarter_features(self.conn,self.cid,'2026-09-29')
        self.conn.execute("UPDATE financial_observations SET availability_basis='unknown' WHERE parse_id=?",(self.pid,))
        self.assertEqual(self.result().status,'EXPLORATORY_BACKFILL')

    def test_rejected_version_is_not_exported(self):
        a=dict(self.conn.execute('SELECT * FROM acceptance_events WHERE company_id=?',(self.cid,)).fetchone())
        a.pop('acceptance_id');a.update(action='rejected',accepted_at_utc='2026-09-28T00:00:00Z')
        insert(self.conn,'acceptance_events',a)
        self.assertEqual(self.result().completeness,'INSUFFICIENT_EVIDENCE')
        self.assertFalse(self.result().features)

    def test_snapshot_metrics_retained_without_historical_ratio_promotion(self):
        insert(self.conn,'company_metric_observations',dict(parse_id=self.pid,reporting_scope='standalone',metric_code='debt_to_equity',label_raw='Debt to equity',
            position=0,display_value='2',value_decimal='2',unit_code='RATIO',scale_multiplier='1',
            first_observed_at_utc=self.sample['first_observed_at_utc'],available_at_utc=self.sample['first_observed_at_utc'],availability_basis='first_observed'))
        r=self.result()
        self.assertEqual(r.supplemental_observations[0]['metric_code'],'debt_to_equity')
        self.assertFalse(any(f.feature_code=='debt_to_equity' for f in r.features))
        self.conn.execute("UPDATE company_metric_observations SET reporting_scope='consolidated'")
        with self.assertRaises(MLContractError):self.result()

    def test_duplicate_observations_fail_closed(self):
        row=dict(self.conn.execute("SELECT * FROM financial_observations WHERE parse_id=? AND line_item_code='sales' AND period_end='2026-06-30'",(self.pid,)).fetchone())
        row.update(financial_observation_id=99999999,row_position=999)
        insert(self.conn,'financial_observations',row)
        with self.assertRaises(MLContractError):self.result()

    def test_snapshot_growth_never_replaces_quarter_growth(self):
        insert(self.conn,'growth_observations',dict(parse_id=self.pid,reporting_scope='standalone',metric_code='profit_growth',metric_label_raw='Profit Growth',
           block_position=0,row_position=0,period_code='3years',period_label_raw='3 Years',display_value='999%',value_decimal='999',unit_code='PERCENT_POINT',
           first_observed_at_utc=self.sample['first_observed_at_utc'],available_at_utc=self.sample['first_observed_at_utc'],availability_basis='first_observed'))
        r=self.result()
        self.assertEqual(r.supplemental_observations[0]['value_decimal'],'999')
        self.assertNotEqual(next(f.value for f in r.features if f.feature_code=='quarter_pat_yoy'),'999')

if __name__=='__main__':unittest.main()
