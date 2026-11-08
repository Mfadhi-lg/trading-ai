import csv,json,sqlite3,tempfile,unittest,hashlib
from scrapper.research.subsector_ss04 import build as b
class SS04(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  # This suite reviews the frozen SS04 snapshot. Do not rebuild it from the
  # subsequently corrected live research source during a regression check.
  cls.c=sqlite3.connect(f'file:{b.DB}?mode=ro',uri=True);cls.ds={r[0]:json.loads(r[1]) for r in cls.c.execute('select company_id,decision_json from ss04_decisions')}
 @classmethod
 def tearDownClass(cls):cls.c.close()
 def test_complete_inventory(self):
  self.assertEqual(len(self.ds),4428);self.assertEqual(sum(d['major_scope'] for d in self.ds.values()),1887)
  ids={int(r['company_id']) for r in csv.DictReader((b.R/'artifacts/subsector-ss02/existing-collection-scope-inventory.csv').open()) if r['eligibility']=='eligible' and r['company_id']}
  self.assertEqual(ids,{i for i,d in self.ds.items() if d['major_scope']})
 def test_identity_and_integrity(self):
  self.assertEqual(self.c.execute('pragma integrity_check').fetchone()[0],'ok');self.assertEqual(self.c.execute('pragma foreign_key_check').fetchall(),[])
 def test_source_preservation(self):
  # Compare every original table with the source fingerprints saved when
  # SS04 was built, not the later120-row Q2 and corrected-identity source.
  evidence=b.O/'validation.json'
  expected_hash=next(line.split()[0] for line in (b.O/'SHA256SUMS').read_text().splitlines() if line.split()[-1]==evidence.name)
  self.assertEqual(hashlib.sha256(evidence.read_bytes()).hexdigest(),expected_hash)
  expected=json.loads(evidence.read_text())['original_table_fingerprints']
  self.assertEqual(b.fingerprint(self.c),expected)
 def test_labels_aliases(self):
  self.assertEqual(self.c.execute('select count(*) from ss03_canonical').fetchone()[0],42)
  for old,new in b.ss03.ALIASES.items():
   self.assertEqual(self.c.execute('select a.canonical_id from ss03_alias a join ss_subsectors s using(subsector_id) where label_raw=?',(old,)).fetchone()[0],b.ss03.cid(new))
 def test_no_broad_completion(self):
  for d in self.ds.values():self.assertNotIn(d['primary_code'],{'PROPERTY_DEVELOPMENT','ELECTRICITY_GENERATION','ENGINEERED_WOOD_PANELS','HOME_APPLIANCES','AUTO','PHARMA','CHEMICALS','POWER'})
 def test_evidence_and_roles(self):
  for d in self.ds.values():
   self.assertTrue(d['evidence']);self.assertTrue(d['reason']);self.assertIsNone(d['valid_from'])
   primary=[e for e in d['exposures'] if e['role']=='primary']
   self.assertEqual([e['code'] for e in primary],[d['primary_code']] if d['primary_code'] else [])
   self.assertEqual(len(d['exposures']),len({e['code'] for e in d['exposures']}))
   if d['status']=='VERIFIED_TUTOR':self.assertTrue(d['tutor_evidence'])
 def test_known_false_positive(self):
  self.assertEqual(self.ds[405]['status'],'MULTI_ACTIVITY_REVIEW');self.assertNotIn('WIND_TURBINES',{e['code'] for e in self.ds[405]['exposures']})
 def test_q2_assessment_preservation(self):
  self.assertEqual(self.c.execute('select count(*) from ss04_q2').fetchone()[0],98)
  self.assertEqual(self.c.execute('select count(*) from ss_mentor_assessments').fetchone()[0],100)
  rows=list(csv.DictReader((b.O/'Q2_RECONCILIATION.csv').open()));prior=list(csv.DictReader((b.R/'artifacts/subsector-ss03/Q2_SUBSECTOR_RECONCILIATION.csv').open()))
  self.assertEqual({r['entry_id']:r['original_assessments_json'] for r in rows},{r['entry_id']:r['original_assessments_json'] for r in prior})
 def test_idempotency_and_rollback(self):
  p=json.loads((b.R/'artifacts/subsector-ss02/full-universe-plan.json').read_text());major={i for i,d in self.ds.items() if d['major_scope']}
  with tempfile.TemporaryDirectory() as tmp:
   c=sqlite3.connect(tmp+'/test.sqlite3');self.c.backup(c);before=b.fingerprint(c);old=c.execute('select * from ss04_decisions order by company_id').fetchall()
   b.apply(c,p,major);self.assertEqual(old,c.execute('select * from ss04_decisions order by company_id').fetchall());b.rollback(c);self.assertEqual(before,b.fingerprint(c));b.apply(c,p,major);self.assertEqual(old,c.execute('select * from ss04_decisions order by company_id').fetchall());c.close()
if __name__=='__main__':unittest.main()
