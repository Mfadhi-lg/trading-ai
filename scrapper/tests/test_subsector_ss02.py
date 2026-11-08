"""Independent SS-02 tests for granular classification, history and eligibility."""
import copy
import json
import sqlite3
import unittest

from scrapper.research.subsector_ss02 import taxonomy
from scrapper.research.subsector_ss02.full_universe import classify,eligibility,select,apply_to,all_counts
from scrapper.research.subsector_ss02.build import before_hash,OUT


class BusinessRulesTest(unittest.TestCase):
    def test_broad_pharma_is_incomplete(self):
        result=classify({'display_text':'The company is in the pharmaceutical industry.'})
        self.assertIsNone(result['primary_code'])
        self.assertEqual(result['status'],'insufficient_granular_business_evidence')

    def test_api_and_formulations_are_distinct(self):
        self.assertEqual(classify({'display_text':'The company manufactures active pharmaceutical ingredients.'})['primary_code'],'API')
        self.assertEqual(classify({'display_text':'The company manufactures pharmaceutical formulations.'})['primary_code'],'FORMULATIONS')

    def test_generic_auto_and_capital_goods_are_incomplete(self):
        for text in ['The company operates in Auto.','A chemicals and capital goods company.','A leading electronics company.']:
            self.assertIsNone(classify({'display_text':text})['primary_code'])

    def test_diversified_no_arbitrary_primary(self):
        result=classify({'display_text':'It manufactures winding wires and transformer oils.'})
        self.assertIsNone(result['primary_code'])
        self.assertEqual({e['code'] for e in result['exposures']},{'WINDING_WIRES','SPECIALTY_OILS'})

    def test_explicit_primary_and_separate_secondary(self):
        result=classify({'display_text':'It primarily manufactures winding wires. It also produces transformer oils.'})
        self.assertEqual(result['primary_code'],'WINDING_WIRES')
        self.assertEqual({e['role'] for e in result['exposures']},{'primary','secondary'})

    def test_future_and_former_activity_not_assigned(self):
        for text in ['It plans to manufacture power transformers.','It formerly manufactured power transformers.']:
            self.assertIsNone(classify({'display_text':text})['primary_code'])

    def test_current_broad_official_industry_not_granular(self):
        self.assertIsNone(taxonomy.HISTORICAL_INDUSTRY.get('Pharmaceuticals'))
        self.assertIsNone(taxonomy.HISTORICAL_INDUSTRY.get('Specialty Chemicals'))
        self.assertIsNone(taxonomy.HISTORICAL_INDUSTRY.get('Auto Components & Equipments'))

    def test_broad_official_nbfc_does_not_invent_loan_type(self):
        result=classify({'display_text':'A company providing financial services.'},official=[{'basic_industry':'Non Banking Financial Company (NBFC)'}])
        self.assertIsNone(result['primary_code'])

    def test_it_enabled_does_not_imply_bpm_or_exclude_cloud(self):
        result=classify({'display_text':'The company provides technology-enabled services.'},official=[{'basic_industry':'IT Enabled Services'}])
        self.assertIsNone(result['primary_code'])

    def test_fine_official_business_is_separate_from_broad_sector(self):
        result=classify({'display_text':'The bank offers deposits and loans.'},official=[{'basic_industry':'Private Sector Bank'}])
        self.assertEqual(result['primary_code'],'PRIVATE_BANKS')
        self.assertEqual(result['status'],'verified_official_granular_business')

    def test_official_parent_does_not_erase_finer_documented_activity(self):
        result=classify({'display_text':'It is a small finance bank.'},official=[{'basic_industry':'Private Sector Bank'}])
        self.assertEqual(result['primary_code'],'SMALL_FINANCE_BANKS')

    def test_taxonomy_has_unique_ids_and_labels(self):
        rows=taxonomy.definitions()
        self.assertEqual(len(rows),len({r['code'] for r in rows}))
        self.assertEqual(len(rows),len({r['label'] for r in rows}))


class HistoricalTest(unittest.TestCase):
    def record(self):
        return dict(company_id=1,symbol='BANK',basic_industry='Private Sector Bank',
            available_at='2026-03-06T17:15:00+00:00',observation_date='2026-02-27',
            historical_index_mcap_crore=100,historical_impact_cost_percent=0.02,
            quote_evidence={'status':'historical_quote_observed'})

    def test_future_modified_version_rejected(self):
        r=self.record();r['available_at']='2026-07-20T11:30:00+00:00'
        self.assertIn('publisher_version_after_cutoff',eligibility(r,'PRIVATE_BANKS','2026-06-30T23:59:59+05:30'))

    def test_missing_prices_and_liquidity_are_missing(self):
        r=self.record();r['quote_evidence']=None;r['historical_impact_cost_percent']=None
        reasons=eligibility(r,'PRIVATE_BANKS','2026-03-31T23:59:59+05:30')
        self.assertIn('historical_price_quote_not_observed',reasons)
        self.assertIn('missing_or_insufficient_historical_liquidity',reasons)

    def test_deterministic_selection_ignores_returns(self):
        a=dict(self.record(),company_id=2,monthly_return=9999)
        b=dict(self.record(),company_id=1,monthly_return=-9999)
        self.assertEqual([r['company_id'] for r in select([a,b])],[1,2])
        a['monthly_return']=-99999;b['monthly_return']=99999
        self.assertEqual([r['company_id'] for r in select([b,a])],[1,2])

    def test_observation_after_cutoff_rejected(self):
        r=self.record();r['observation_date']='2026-04-01'
        self.assertIn('observation_after_cutoff',eligibility(r,'PRIVATE_BANKS','2026-03-31T23:59:59+05:30'))

    def test_sql_asof_compares_instants_across_timezones(self):
        c=sqlite3.connect(':memory:')
        result=c.execute('SELECT julianday(?)<=julianday(?)',('2026-11-02T05:00:00+00:00','2026-11-02T10:00:00+05:30')).fetchone()[0]
        self.assertEqual(result,0)
        c.close()


class FullUniverseIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan=json.loads((OUT/'full-universe-plan.json').read_text())

    def test_entire_existing_master_accounted(self):
        self.assertEqual(len(self.plan['mappings']),4428)
        self.assertEqual(len({m['company_id'] for m in self.plan['mappings']}),4428)
        self.assertEqual(len(self.plan['security_mapping']),5918)

    def test_existing_verified_identities_never_changed(self):
        old=sqlite3.connect(f"file:{OUT/'ss01-v2-before.sqlite3'}?mode=ro",uri=True)
        expected=dict(old.execute('SELECT row_id,company_id FROM ss_rows WHERE company_id IS NOT NULL'))
        old.close()
        actual={o['row_id']:o['company_id'] for o in self.plan['identity_overlays']}
        self.assertTrue(all(actual[rid]==cid for rid,cid in expected.items()))

    def test_owner_symbol_alignment_insertion_not_assessment(self):
        by_name={o['raw_symbol']:o for o in self.plan['identity_overlays'] if o['source_quarter']=='FY2027-Q2'}
        self.assertEqual(by_name['Ather Industries']['evidence']['txt_token'],'NSE:AETHER')
        self.assertEqual(by_name['Mankind Pharma']['evidence']['txt_token'],'NSE:MANKIND')
        self.assertIn('NSE:ATHERENERG',self.plan['unmatched_owner_symbol_entries'])

    def test_reit_not_mapped_to_embassy_developments(self):
        r=next(o for o in self.plan['identity_overlays'] if o['raw_symbol']=='Embassy')
        self.assertIsNone(r['company_id'])
        self.assertEqual(r['evidence']['external']['isin'],'INE041025011')

    def test_all_frozen_members_pass_dated_eligibility(self):
        for b in self.plan['baskets']:
            for m in b['members']:
                self.assertEqual(eligibility(m['selection_evidence'],b['code'],b['cutoff_at']),[])
            if b['members']:
                self.assertAlmostEqual(sum(m['equal_weight'] for m in b['members']),1)
                self.assertAlmostEqual(sum(m['historical_cap_weight'] for m in b['members']),1)
            self.assertEqual(b['chart_ready'],0)

    def test_complete_ss01_preservation_and_idempotency_on_clone(self):
        old=sqlite3.connect(f"file:{OUT/'ss01-v2-before.sqlite3'}?mode=ro",uri=True)
        clone=sqlite3.connect(':memory:');old.backup(clone);old.close()
        clone.execute('PRAGMA foreign_keys=ON')
        before=before_hash(clone)
        apply_to(clone,self.plan);first=all_counts(clone);apply_to(clone,self.plan)
        self.assertEqual(first,all_counts(clone))
        self.assertEqual(before_hash(clone),before)
        self.assertEqual(clone.execute('PRAGMA foreign_key_check').fetchall(),[])
        clone.close()


if __name__=='__main__':unittest.main()
