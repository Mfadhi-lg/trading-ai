from scrapper.tests.retained_audits import retained_source_audit
import csv,json,sqlite3,unittest
from scrapper.research.subsector_ss03 import pilot,build

@retained_source_audit
class PilotChecks(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  pilot.run();cls.c=sqlite3.connect(f'file:{pilot.DB}?mode=ro',uri=True)
  cls.members=list(csv.DictReader(open(pilot.OUT/'CANDIDATE_BASKETS.csv',encoding='utf8')))
  cls.reviews=list(csv.DictReader(open(pilot.OUT/'PEER_REVIEW.csv',encoding='utf8')))
 @classmethod
 def tearDownClass(cls): cls.c.close()
 def test_every_tutor_company_reviewed(self):
  for label in pilot.CANDIDATES:
   ca=build.cid(label)
   missing=self.c.execute('select distinct m.company_id from ss03_historical_memberships m left join ss03_pilot_review r on r.company_id=m.company_id and r.canonical_id=m.canonical_id where m.canonical_id=? and r.company_id is null',(ca,)).fetchall()
   self.assertEqual(missing,[])
 def test_major_scope_and_nonfocus_peers(self):
  for r in self.reviews:
   if r['decision']=='CANDIDATE':self.assertTrue(json.loads(r['evidence_json'])['major_scope'])
  self.assertTrue(any(r['decision']=='CANDIDATE' and not json.loads(r['evidence_json'])['tutor_quarters'] for r in self.reviews))
 def test_conflicts_not_silently_included(self):
  for company in ('132','836','551','743','2111'):
   self.assertTrue(any(r['company_id']==company and r['decision']=='EXCLUDE' for r in self.reviews))
   self.assertFalse(any(r['company_id']==company for r in self.members))
 def test_baskets_and_no_future_claims(self):
  self.assertEqual(len(self.members),32)
  for row in self.members:
   self.assertEqual(row['eligibility'],'PROVISIONAL')
   self.assertEqual(row['full_price_coverage'],'NOT_ESTABLISHED')
   self.assertEqual(row['listing_continuity'],'NOT_ESTABLISHED')
   self.assertTrue(not row['source_date'] or row['source_date']<=row['cutoff'])
   self.assertTrue(row['historical_weight'].startswith('UNAVAILABLE'))
  for label in pilot.CANDIDATES:
   for quarter in build.CUT:
    rows=[r for r in self.members if r['label']==label and r['quarter']==quarter]
    self.assertGreaterEqual(len(rows),3);self.assertEqual(len(rows),len({r['company_id'] for r in rows}))
 def test_source_tables_unchanged(self):
  source=sqlite3.connect(f'file:{build.SOURCE}?mode=ro',uri=True)
  for table in ['ss_rows','ss_subsectors','ss_assignments','ss_memberships','ss_mentor_assessments','ss02_baskets','ss02_basket_members']:
   self.assertEqual(source.execute('select * from '+table+' order by rowid').fetchall(),self.c.execute('select * from '+table+' order by rowid').fetchall())
  source.close();self.assertEqual(self.c.execute('pragma integrity_check').fetchone()[0],'ok');self.assertEqual(self.c.execute('pragma foreign_key_check').fetchall(),[])
 def test_both_quarters_and_idempotent_rebuild(self):
  before=(pilot.OUT/'CANDIDATE_BASKETS.csv').read_bytes();pilot.run();self.assertEqual(before,(pilot.OUT/'CANDIDATE_BASKETS.csv').read_bytes())
  self.assertEqual(self.c.execute("select count(*) from ss03_pilot_basket where status='PROVISIONAL' and chart_ready=0").fetchone()[0],10)
if __name__=='__main__':unittest.main()
