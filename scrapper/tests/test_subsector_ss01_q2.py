"""Independent fixtures for Q2 raw preservation, duplicate policy and reversible import."""
import csv
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scrapper.research.subsector_ss01 import importer as base, q2


class Q2Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)
        self.db=self.path/'test.sqlite3';self.csv=self.path/'q2.csv'
        self.c=sqlite3.connect(self.db);self.addCleanup(self.c.close)
        self.c.execute('PRAGMA foreign_keys=ON')
        self.c.executescript('''CREATE TABLE companies(company_id INTEGER PRIMARY KEY,current_legal_name TEXT,status TEXT);
          CREATE TABLE security_identifiers(security_identifier_id INTEGER PRIMARY KEY,company_id INTEGER,identifier_type TEXT,identifier_value TEXT,valid_from TEXT,valid_to TEXT,is_current INTEGER);
          CREATE TABLE source_identifiers(source_identifier_id INTEGER PRIMARY KEY,company_id INTEGER,source TEXT,source_company_id TEXT,source_symbol TEXT,source_slug TEXT,canonical_url TEXT,mapping_status TEXT,verified_at_utc TEXT);''')
        self.c.executemany('INSERT INTO companies VALUES(?,?,?)',[(1,'Alpha Ltd','active'),(2,'Bliss GVS Pharma Ltd','active'),(3,'Acme Resources Ltd','active'),(4,'ACME Solar Holdings Ltd','active')])
        self.c.executemany('INSERT INTO security_identifiers VALUES(?,?,?,?,NULL,NULL,1)',[(1,1,'NSE','AAA'),(2,2,'NSE','BLISSGVS')])
        self.c.commit();base.migrate(self.c);q2.migrate(self.c)
        self.data=[['1','Alpha','Excellent','Very Good','Awaiting',''],
                   ['2','Bliss GVS','Okey','Okey','','Raw typo preserved.'],
                   ['3','Bliss GVS','Blockbuster','Good','Upward','Repeated row with different assessments.'],
                   ['4','Acme','','','','']]
        self.write()
        self.plan=q2.make_plan(self.csv,self.db)

    def write(self):
        with self.csv.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow(q2.FIELDS);w.writerows(self.data)

    def test_all_fields_and_missing_preserved(self):
        q2.load(self.c,self.plan)
        actual=self.c.execute('SELECT sl_no_raw,stock_raw,q2_results_raw,rating_raw,guidance_raw,extraction_note_raw FROM ss_mentor_assessments ORDER BY original_row_number').fetchall()
        self.assertEqual(actual,[tuple(r) for r in self.data])
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_mentor_assessments WHERE decision_at IS NOT NULL OR available_at IS NOT NULL').fetchone()[0],0)

    def test_dedup_conflict_is_not_a_chosen_judgment(self):
        q2.load(self.c,self.plan)
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_q2_focus').fetchone()[0],3)
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_mentor_assessments').fetchone()[0],4)
        row=self.c.execute("SELECT assessment_state,q2_results_raw,rating_raw,guidance_raw,original_row_count FROM ss_q2_focus WHERE display_stock_raw='Bliss GVS'").fetchone()
        self.assertEqual(row,('conflicting_duplicate',None,None,None,2))
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_memberships').fetchone()[0],2)

    def test_similar_names_are_not_verified_and_ambiguity_flagged(self):
        row=self.plan['rows'][-1]
        self.assertEqual(row['resolution_state'],'ambiguous')
        self.assertIsNone(row['company_id'])
        self.assertEqual(row['resolution']['candidates'],[3,4])

    def test_idempotency_and_rollback_preserve_canonical_and_history(self):
        initial=q2.baseline(self.c)
        q2.load(self.c,self.plan);counts=q2.table_counts(self.c)
        q2.load(self.c,self.plan)
        self.assertEqual(q2.table_counts(self.c),counts)
        q2.rollback(self.c,self.plan['source']['source_id'])
        self.assertEqual(q2.baseline(self.c),initial)
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_rows').fetchone()[0],0)
        q2.load(self.c,self.plan)
        self.assertEqual(q2.table_counts(self.c),counts)

    def test_source_mutation_refused(self):
        self.csv.write_text('changed')
        with self.assertRaisesRegex(ValueError,'Source or Q4/Q1 baseline changed'):
            q2.load(self.c,self.plan)
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_sources').fetchone()[0],0)

    def test_preexisting_plan_cannot_be_silently_reinterpreted(self):
        q2.load(self.c,self.plan)
        changed=dict(self.plan,duplicate_policy='different')
        with self.assertRaisesRegex(ValueError,'different audited plan'):
            q2.load(self.c,changed)

    def test_duplicate_known_time_requires_every_source_row(self):
        self.data.append(['5','Alpha','Excellent','Very Good','Awaiting','Another note'])
        self.write();plan=q2.make_plan(self.csv,self.db);q2.load(self.c,plan)
        self.c.execute("UPDATE ss_mentor_assessments SET available_at='2026-11-01' WHERE sl_no_raw='1'")
        self.assertIsNone(self.c.execute("SELECT assessment_available_at FROM ss_q2_focus WHERE display_stock_raw='Alpha'").fetchone()[0])

    def test_exact_source_rows_have_no_import_time_decision_backfill(self):
        q2.load(self.c,self.plan)
        timestamp=self.c.execute('SELECT recorded_at_utc,decision_at FROM ss_import_batches').fetchone()
        self.assertIsNotNone(timestamp[0]);self.assertIsNone(timestamp[1])
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_q2_focus WHERE assessment_available_at IS NOT NULL OR decision_at IS NOT NULL').fetchone()[0],0)

    def test_facts_are_separate_and_agency_is_required(self):
        q2.load(self.c,self.plan)
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_extracted_facts').fetchone()[0],0)
        self.c.execute("INSERT INTO ss_documents VALUES('doc',1,'credit_rating_report','uri','hash','v1',NULL,NULL,NULL,'import-time')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("INSERT INTO ss_extracted_facts VALUES('f','doc','agency_rating','rating','Very Good',NULL,NULL,NULL,NULL,NULL,'recorded','unverified')")

    def test_future_fact_links_block_destructive_rollback(self):
        q2.load(self.c,self.plan)
        rid=self.plan['rows'][0]['row_id']
        self.c.execute("INSERT INTO ss_documents VALUES('doc',1,'financial_results','uri','hash','v1',NULL,NULL,NULL,'recorded')")
        self.c.execute("INSERT INTO ss_extracted_facts VALUES('f','doc','financial_metric','revenue','10','10','INR',NULL,NULL,NULL,'recorded','verified')")
        self.c.execute('INSERT INTO ss_assessment_fact_links VALUES(?,?,?)',(rid,'f','context'));self.c.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            q2.rollback(self.c,self.plan['source']['source_id'])
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_mentor_assessments').fetchone()[0],4)

    def test_subsector_conflicts_keep_all_original_provenance(self):
        # Deliberately conflicting prior source assignments, independently seeded.
        self.c.execute("INSERT INTO ss_sources VALUES('s4','path4','hash4','csv','FY2026-Q4','2026-03-31',NULL,'unknown',X'00')")
        self.c.execute("INSERT INTO ss_sources VALUES('s1','path1','hash1','txt','FY2027-Q1','2026-06-30',NULL,'unknown',X'00')")
        for sid,label in [('a','EMS'),('b','WIRES')]:
            self.c.execute('INSERT INTO ss_subsectors VALUES(?,?,?,"source_category",NULL)',(sid,label,label.lower()))
        self.c.execute("INSERT INTO ss_assignments VALUES('assignment4','s4',1,'a',NULL,NULL,NULL)")
        self.c.execute("INSERT INTO ss_assignments VALUES('assignment1','s1',1,'b',NULL,NULL,NULL)");self.c.commit()
        plan=q2.make_plan(self.csv,self.db)
        q2.load(self.c,plan)
        row=self.c.execute("SELECT subsector_state,subsector_raw FROM ss_q2_focus WHERE display_stock_raw='Alpha'").fetchone()
        self.assertEqual(row,('conflicting_prior_assignments',None))
        self.assertEqual(self.c.execute('SELECT count(*) FROM ss_inherited_subsector_evidence').fetchone()[0],2)

    def test_consistent_duplicates_dedup_and_keep_evidence(self):
        self.data.append(['5','Alpha','Excellent','Very Good','Awaiting','Another original note'])
        self.write();plan=q2.make_plan(self.csv,self.db);q2.load(self.c,plan)
        self.assertEqual(self.c.execute("SELECT assessment_state,q2_results_raw,original_row_count FROM ss_q2_focus WHERE display_stock_raw='Alpha'").fetchone(),('consistent_duplicate','Excellent',2))


if __name__=='__main__':
    unittest.main()
