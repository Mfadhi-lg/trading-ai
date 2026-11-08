"""Independent expected outcomes using small synthetic source and identity fixtures."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scrapper.research.subsector_ss01 import importer as imp


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.q4 = self.path/'q4.csv'
        self.q4.write_bytes(b'\xef\xbb\xbfSymbol,Company Name,Category\r\nAAA,Alpha Limited,Odd LABEL\r\nBBB,Beta Limited,\r\n')
        self.q1 = self.path/'q1.txt'
        self.q1.write_text('###New Label,NSE:AAA,NSE:AAA,NSE:UNKNOWN,NSE:NAM_INDIA')
        self.c = sqlite3.connect(':memory:')
        self.addCleanup(self.c.close)
        self.c.execute('PRAGMA foreign_keys=ON')
        self.c.execute('CREATE TABLE companies(company_id INTEGER PRIMARY KEY,current_legal_name TEXT,status TEXT)')
        self.c.executemany('INSERT INTO companies VALUES(?,?,?)',[(10,'Alpha Ltd','active'),(20,'Beta Ltd','active'),(30,'Nippon Ltd','active')])
        imp.migrate(self.c)
        self.snapshot = dict(tables={
            'companies':dict(rows=[(10,'Alpha Ltd','active'),(20,'Beta Ltd','active'),(30,'Nippon Ltd','active')]),
            'security_identifiers':dict(rows=[(1,10,'NSE','AAA',None,None,1),(2,20,'NSE','BBB',None,None,1),(3,30,'NSE','NAM-INDIA',None,None,1)]),
            'source_identifiers':dict(rows=[])},bse_aliases=[])
        self.plan = self.plan_for()

    def plan_for(self):
        sources,rows=[],[]
        for p,fmt,q,end in [(self.q4,'csv','FY2026-Q4','2026-03-31'),(self.q1,'tradingview_txt','FY2027-Q1','2026-06-30')]:
            s,rr=imp.parse_source(p,fmt,q,end)
            sources.append(s)
            for r in rr:
                r.update(imp.resolve(r,self.snapshot))
                rows.append(r)
        return dict(sources=sources,rows=rows)

    def test_membership_and_repeat_are_separate_from_evidence(self):
        imp.import_rows(self.c,self.plan)
        imp.import_rows(self.c,self.plan)
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_rows').fetchone()[0],6)
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_memberships').fetchone()[0],4)
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_membership_evidence').fetchone()[0],5)
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_assignments').fetchone()[0],3)
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_assignment_evidence').fetchone()[0],4)
        self.assertEqual(self.c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_missing_labels_and_unknown_id_preserved(self):
        imp.import_rows(self.c,self.plan)
        self.assertEqual(self.c.execute("SELECT company_id,label_raw,resolution_state FROM ss_rows WHERE symbol_raw='UNKNOWN'").fetchone(),(None,'New Label','unresolved'))
        self.assertEqual(self.c.execute("SELECT count(*) FROM ss_assignment_evidence JOIN ss_rows USING(row_id) WHERE symbol_raw='BBB'").fetchone()[0],0)

    def test_no_date_backfill_or_mentor_rejection(self):
        imp.import_rows(self.c,self.plan)
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_assignments WHERE known_at IS NOT NULL OR valid_from IS NOT NULL').fetchone()[0],0)
        self.assertEqual(self.c.execute("SELECT count(*) FROM ss_classification_evidence WHERE known_at<='2026-06-30'").fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT DISTINCT decision FROM ss_mentor_evidence ORDER BY decision').fetchall(),[('focus_reference',),('unknown',)])
        self.assertEqual(self.c.execute('SELECT label_raw FROM ss_classification_evidence WHERE company_id=10 ORDER BY quarter,row_ref').fetchall(),[('Odd LABEL',),('New Label',),('New Label',)])

    def test_competing_exact_identity_is_quarantined(self):
        self.snapshot['tables']['security_identifiers']['rows'].append((4,20,'NSE','AAA',None,None,0))
        resolution=imp.resolve(self.plan['rows'][0],self.snapshot)
        self.assertEqual(resolution['resolution_state'],'ambiguous')
        self.assertIsNone(resolution['company_id'])

    def test_source_identifier_cannot_override_conflict(self):
        self.snapshot['tables']['source_identifiers']['rows'].append((1,20,'screener','id','AAA','slug','url','verified','2026-01-01'))
        self.assertEqual(imp.resolve(self.plan['rows'][0],self.snapshot)['resolution_state'],'ambiguous')

    def test_similar_or_suffix_normalized_names_are_review_only(self):
        row=dict(self.plan['rows'][0],symbol_raw='MISSING')
        result=imp.resolve(row,self.snapshot)
        self.assertIsNone(result['company_id'])
        self.assertEqual(result['resolution']['review_name_suggestions'],[10])

    def test_explicit_symbol_alias_has_proof(self):
        result=imp.resolve(self.plan['rows'][-1],self.snapshot)
        self.assertEqual(result['company_id'],30)
        self.assertEqual(result['resolution']['proof'][0]['basis'],'underscore_to_hyphen_alias')

    def test_source_tamper_rolls_back_whole_import(self):
        self.q1.write_text('changed')
        with self.assertRaisesRegex(ValueError,'Source changed'):
            imp.import_rows(self.c,self.plan)
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_sources').fetchone()[0],0)

    def test_new_source_version_preserves_old_classification(self):
        imp.import_rows(self.c,self.plan)
        self.q1.write_text('###Changed,NSE:AAA')
        imp.import_rows(self.c,self.plan_for())
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_sources').fetchone()[0],3)
        self.assertEqual(self.c.execute("SELECT DISTINCT label_raw FROM ss_classification_evidence WHERE quarter='FY2027-Q1' AND company_id=10 ORDER BY label_raw").fetchall(),[('Changed',),('New Label',)])

    def test_raw_multiline_csv_is_recoverable(self):
        self.q4.write_text('Symbol,Company Name,Category\nAAA,"Alpha\nLimited", CAT \n')
        source,rows=imp.parse_source(self.q4,'csv','FY2026-Q4','2026-03-31')
        self.assertEqual(rows[0]['row_ref'],'lines:2-3')
        self.assertEqual(rows[0]['raw_text'],'AAA,"Alpha\nLimited", CAT \n')
        self.assertEqual(rows[0]['label_raw'],' CAT ')

    def test_foreign_key_blocks_orphan_assignment(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('INSERT INTO ss_assignments VALUES("orphan","missing",999,"none",NULL,NULL,NULL)')

    def test_live_target_is_rejected_before_opening(self):
        with self.assertRaisesRegex(ValueError,'dedicated SS-01'):
            imp.apply({}, {},self.path/'live.db')
        self.assertFalse((self.path/'live.db').exists())


if __name__=='__main__':
    unittest.main()
