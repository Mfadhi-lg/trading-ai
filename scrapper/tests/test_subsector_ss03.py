import sqlite3, tempfile, unittest, csv, hashlib
from pathlib import Path
from scrapper.research.subsector_ss03 import build

class SS03Tests(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(':memory:'); src=sqlite3.connect(f'file:{build.SOURCE}?mode=ro',uri=True);src.backup(self.c);src.close();self.c.execute('pragma foreign_keys=on');build.migrate(self.c)
 def tearDown(self): self.c.close()
 def test_labels_and_aliases(self):
  c=self.c
  self.assertEqual(c.execute('select count(*) from ss_subsectors').fetchone()[0],45)
  self.assertEqual(c.execute('select count(*) from ss03_alias').fetchone()[0],45)
  self.assertEqual(c.execute('select count(*) from ss03_canonical').fetchone()[0],42)
  for left,right in [('AUTO ANCILLIARY','AUTO ANCILLARY'),('TEXTILES','TEXTILE'),('FERTILIZERS','FERTILIZER')]:
   ids=[c.execute('select a.canonical_id from ss03_alias a join ss_subsectors s using(subsector_id) where s.label_raw=?',(x,)).fetchone()[0] for x in (left,right)];self.assertEqual(*ids)
 def test_separate_categories(self):
  for a,b in [('TYRE','AUTO ANCILLARY'),('TRANSFORMERS','WIRES'),('EMS','DATA CENTER')]: self.assertNotEqual(build.cid(a),build.cid(b))
 def test_membership_dedup_and_overlay(self):
  c=self.c
  self.assertEqual(c.execute('select count(*) from ss03_historical_memberships').fetchone()[0],628)
  self.assertEqual(c.execute('select count(*) from (select quarter,company_id,canonical_id,count(*) n from ss03_historical_memberships group by 1,2,3 having n>1)').fetchone()[0],0)
  self.assertEqual(c.execute('select count(*) from ss_rows r join ss_sources s using(source_id) where s.quarter in (\'FY2026-Q4\',\'FY2027-Q1\') and r.label_raw is not null and coalesce((select company_id from ss02_identity_overlay i where i.row_id=r.row_id),r.company_id) is null').fetchone()[0],0)
 def test_original_assessments_and_unknown_dates(self):
  # The November3 append-only source adds rows101-120. Bind the expectation
  # to its retained source export instead of the superseded100-row snapshot.
  folder=build.ROOT/'artifacts/subsector-ss01/q2-2027-addendum-20261103'
  path=folder/'q2-all-assessments.csv'
  expected_hash=next(line.split()[0] for line in (folder/'SHA256SUMS').read_text().splitlines() if line.split()[-1]==path.name)
  self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),expected_hash)
  with path.open(newline='') as f: evidence=list(csv.DictReader(f))
  self.assertEqual(len(evidence),120)
  fields=[r[1] for r in self.c.execute('pragma table_info(ss_mentor_assessments)')]
  expected=[]
  for r in evidence:
   row=[]
   for k in fields:
    v=r[k]
    if k in ('company_id','original_row_number'):v=int(v) if v else None
    elif k in ('decision_at','available_at'):v=v or None
    row.append(v)
   expected.append(tuple(row))
  self.assertEqual(sorted(self.c.execute('select * from ss_mentor_assessments').fetchall()),sorted(expected))
  self.assertTrue(all(r['decision_at']==r['available_at']=='' for r in evidence))
 def test_migration_rollback_integrity(self):
  c=self.c;before=c.execute('select count(*) from ss_rows').fetchone();build.migrate(c);build.migrate(c)
  self.assertEqual(c.execute('select count(*) from ss03_alias').fetchone()[0],45)
  build.rollback(c);build.rollback(c);self.assertEqual(c.execute('select count(*) from ss_rows').fetchone(),before)
  build.migrate(c);self.assertEqual(c.execute('pragma integrity_check').fetchone()[0],'ok');self.assertEqual(c.execute('pragma foreign_key_check').fetchall(),[])
 def test_no_known_historical_classification(self):
  self.assertEqual(self.c.execute("select count(*) from ss_sources where quarter in ('FY2026-Q4','FY2027-Q1') and known_at is not null").fetchone()[0],0)
if __name__=='__main__':unittest.main()
