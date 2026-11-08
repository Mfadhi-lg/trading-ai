import unittest
from dataclasses import asdict
from scrapper.research import features
from scrapper.pipeline import ml_contracts
from .b1_feature_fixture import fixture
class FinancialFeatureOwnershipTests(unittest.TestCase):
 def test_legacy_identity_and_read_only_cutoff(self):
  for name in ['MLContractError','MLFeature','QuarterFeature','CompanyQuarterFeatures','company_quarter_features','build_screener_ml_features']:
   self.assertIs(getattr(features,name),getattr(ml_contracts,name))
  conn,samples,_=fixture()
  try:
   before=conn.total_changes
   cid=int(samples[0]['company_id'])
   kw=dict(period='2026-06',sector='industrial',evidence_kind='retained_export')
   a=features.company_quarter_features(conn,cid,'2026-09-29T08:37:44Z',**kw)
   self.assertEqual(asdict(a),asdict(ml_contracts.company_quarter_features(conn,cid,'2026-09-29T08:37:44Z',**kw)))
   self.assertEqual(a.status,'EXPLORATORY_BACKFILL')
   self.assertEqual(conn.total_changes,before)
  finally: conn.close()
