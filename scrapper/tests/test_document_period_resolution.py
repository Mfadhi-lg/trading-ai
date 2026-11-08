"""Explicit document quarter recovery, ambiguity and durable idempotence."""
import json
import sqlite3
import unittest
from scrapper.documents.discovery.promotion import explicit_reporting_period, fill_stored_artifact_period


class PeriodResolutionTests(unittest.TestCase):
    def test_saved_call_siblings_share_only_their_exact_event_group(self):
        from scrapper.tests.test_credit_rating_acquisition import CreditRatingAcquisitionTests, OBSERVED
        from scrapper.documents.discovery.promotion import promote_document_links
        fixture=CreditRatingAcquisitionTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        c=fixture.conn
        with c:
            for position, kind, label, url in (
                (2,'concall_ppt','May 2026','https://issuer.test/Q4FY26.pdf'),
                (3,'concall_recording','May 2026','https://issuer.test/audio.mp3'),
                (4,'concall_transcript','Feb 2026','https://issuer.test/other.pdf')):
                c.execute('''INSERT INTO document_links(parse_id,reporting_scope,document_type,period_label_raw,
                    title_raw,position,source_url,availability_status,first_observed_at_utc,available_at_utc,availability_basis)
                    VALUES (?,'consolidated',?,?,'Call',?,?,'available',?,?,'source')''',
                    (fixture.parse_id,kind,label,position,url,OBSERVED,OBSERVED))
        for kind in ('concall_ppt','concall_recording','concall_transcript'):
            promote_document_links(c,document_type=kind)
        recording=c.execute("SELECT artifact_id FROM document_artifacts WHERE artifact_type='recording'").fetchone()[0]
        other=c.execute("SELECT artifact_id FROM document_artifacts WHERE artifact_type='transcript'").fetchone()[0]
        self.assertTrue(fill_stored_artifact_period(c,recording))
        self.assertFalse(fill_stored_artifact_period(c,other))
        self.assertEqual(c.execute('SELECT period_end FROM document_artifacts WHERE artifact_id=?',(recording,)).fetchone()[0],'2026-03-31')

    def test_fiscal_quarter_urls_and_headers(self):
        cases = {'Q2FY26': '2025-09-30', 'Q4 FY2025': '2025-03-31',
                 'Q1FY26_27': '2026-06-30', '/FY2026-2027/Q2/report.pdf': '2026-09-30',
                 'Q2 & H1 FY 2025-26': '2025-09-30',
                 'quarter ended 30th September, 2025': '2025-09-30',
                 'Quarter ended June 30, 2026': '2026-06-30'}
        cases.update({'quarter and year ended March 31, 2026': '2026-03-31',
                      'quarter and nine-month period ended December 31, 2025': '2025-12-31',
                      'Q4 & FY23': '2023-03-31',
                      'second quarter of fiscal year 2024': '2023-09-30'})
        cases.update({"Q3 & 9M FY’26": '2025-12-31', "Q2 H1 FY23": '2022-09-30',
                      "Q4 FY '26": '2026-03-31', "1QFY26": '2025-06-30',
                      'Q3 for the Financial Year 2025-26': '2025-12-31',
                      'Quarter & Year Ended 31st March 2022': '2022-03-31',
                      'quarter and six-month period ended September 30, 2024': '2024-09-30',
                      'quarter ended 30.06.2024': '2024-06-30'})
        for text, period in cases.items():
            with self.subTest(text=text):
                actual, evidence = explicit_reporting_period([('test', text)])
                self.assertEqual(actual, period)
                self.assertTrue(evidence)

    def test_dates_and_incomplete_fiscal_labels_are_not_reporting_quarters(self):
        for text in ('Nov 2025', '2025-11-02', 'Q2', 'FY2026', 'Q2 2026', 'Q2FY2024-26',
                     'quarter ended June 15, 2026', 'quarter ended 15.06.2026'):
            with self.subTest(text=text):
                self.assertIsNone(explicit_reporting_period([('test', text)])[0])

    def test_call_label_disambiguates_comparatives_but_cannot_supply_evidence(self):
        text = 'Q2FY26 compared with Q2FY25'
        self.assertIsNone(explicit_reporting_period([('header', text)])[0])
        self.assertEqual(explicit_reporting_period([('header', text)], call_labels=['Nov 2025'])[0], '2025-09-30')
        self.assertIsNone(explicit_reporting_period([('header', 'Q2FY25')], call_labels=['Nov 2025'])[0])
        self.assertIsNone(explicit_reporting_period([('header', 'PPT')], call_labels=['Nov 2025'])[0])

    def test_repair_is_audited_preserves_assigned_periods_and_excludes_ratings(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.executescript('''CREATE TABLE document_artifacts(artifact_id INTEGER PRIMARY KEY,artifact_type TEXT,title_raw TEXT,period_end TEXT);
          CREATE TABLE artifact_sources(artifact_source_id INTEGER PRIMARY KEY,artifact_id INTEGER,document_url TEXT,source_title_raw TEXT,classification_evidence_json TEXT);
          CREATE TABLE artifact_contents(content_id INTEGER PRIMARY KEY,artifact_id INTEGER);
          CREATE TABLE artifact_period_corrections(artifact_id INTEGER,old_period_end TEXT,new_period_end TEXT,reason TEXT,evidence_json TEXT,corrected_at_utc TEXT);
          INSERT INTO document_artifacts VALUES(1,'transcript','Transcript',NULL),(2,'credit_rating','Q2FY26',NULL),(3,'presentation','Q2FY26','2024-09-30');''')
        c.execute('INSERT INTO artifact_sources VALUES(1,1,?,?,?)', ('https://issuer.test/Q2FY26.pdf', 'Transcript',json.dumps({'period_label_raw':'Nov 2025'})))
        self.assertTrue(fill_stored_artifact_period(c,1))
        self.assertFalse(fill_stored_artifact_period(c,1))
        self.assertFalse(fill_stored_artifact_period(c,2))
        self.assertFalse(fill_stored_artifact_period(c,3))
        self.assertEqual(c.execute('SELECT period_end FROM document_artifacts WHERE artifact_id=1').fetchone()[0], '2025-09-30')
        self.assertEqual(c.execute('SELECT count(*) FROM artifact_period_corrections').fetchone()[0],1)
        self.assertIsNone(json.loads(c.execute('SELECT evidence_json FROM artifact_period_corrections').fetchone()[0])['old_period_end'])
        c.close()
