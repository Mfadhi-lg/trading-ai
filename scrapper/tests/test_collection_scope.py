
from scrapper.tests.database_fixtures import prepare_database
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from scrapper.core.collection_scope import numeric_cap, scope_snapshot
from scrapper.commands.collection_scope import apply_observation
from scrapper.commands.quarter_documents import call_months
from scrapper.db.database import connect, init_db
from scrapper.db.collection_scope_repo import record_scope, bootstrap_scope
from scrapper.db.screener_discovery_repo import claim_due_companies, preview_due_companies
from scrapper.pipeline.screener_discovery import discover_screener_results, _failure_policy
from scrapper.tests.test_screener_discovery import screen_html

NOW='2026-09-28T12:00:00+00:00'


class CollectionScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.db=self.root/'test.db'
        fixture=self.root/'screen.html'; fixture.write_bytes(screen_html().replace(b'/consolidated/',b'/'))
        prepare_database(self.db)
        discover_screener_results(query_role='scope_test',query='x',fixture=fixture,
                                  db_path=self.db,snapshot_root=self.root/'snapshots')
        self.conn=connect(self.db); self.addCleanup(self.conn.close)

    def test_exclusion_blocks_claim_and_rising_cap_requeues(self):
        c=self.conn
        with c:
            c.execute("UPDATE screener_company_queue SET status='failed_terminal',last_error_json=? WHERE company_path_normalized='/company/ALPHA/'",
                      (json.dumps({'error':{'message':'Company is below Screener market-cap scope floor: 500 INR crore'}}),))
            record_scope(c,path='/company/ALPHA/',company_id=None,value='499',observed_at=NOW,evidence={'fixture':True})
            c.execute("UPDATE screener_company_queue SET collection_disposition='below_market_cap' WHERE company_path_normalized='/company/ALPHA/'")
        self.assertNotIn('/company/ALPHA/',[r.company_path_normalized for r in preview_due_companies(c,now_utc='2099-01-01T00:00:00+00:00',limit=10)])
        row=c.execute("SELECT * FROM screener_collection_scope").fetchone()
        with c:
            state=apply_observation(c,row,snapshot={'market_cap_crore':Decimal('580'),'identifiers':{'ALPHA'},'source_company_id':None},now='2026-10-05T12:00:00+00:00',evidence={'fixture':True})
        self.assertEqual(state,'eligible')
        jobs=claim_due_companies(c,now_utc='2099-01-01T00:00:00+00:00',limit=10,worker_id='test')
        self.assertIn('/company/ALPHA/',[r.company_path_normalized for r in jobs])

    def test_unknown_cap_does_not_become_zero_or_clear_exclusion(self):
        self.assertIsNone(numeric_cap('NaN'));self.assertIsNone(numeric_cap(None));self.assertIsNone(numeric_cap('-1'))
        with self.conn:
            record_scope(self.conn,path='/company/ALPHA/',company_id=None,value='100',observed_at=NOW,evidence={})
        row=self.conn.execute('SELECT * FROM screener_collection_scope').fetchone()
        with self.assertRaises(ValueError):
            apply_observation(self.conn,row,snapshot={'market_cap_crore':None},now=NOW,evidence={})
        self.assertEqual(self.conn.execute('SELECT eligibility FROM screener_collection_scope').fetchone()[0],'below_floor')

    def test_identity_mismatch_does_not_reactivate(self):
        with self.conn:
            record_scope(self.conn,path='/company/ALPHA/',company_id=None,value='100',observed_at=NOW,evidence={})
        row=self.conn.execute('SELECT * FROM screener_collection_scope').fetchone()
        with self.assertRaisesRegex(ValueError,'identity mismatch'):
            apply_observation(self.conn,row,snapshot={'market_cap_crore':Decimal('1000'),'identifiers':{'OTHER'},'source_company_id':None},now=NOW,evidence={})

    def test_bootstrap_idempotent_and_scope_failures_not_retried(self):
        with self.conn:
            self.conn.execute("UPDATE screener_company_queue SET status='failed_terminal',last_error_json=?",(json.dumps({'error':{'message':'Company is below Screener market-cap scope floor: 500 INR crore'}}),))
        bootstrap_scope(self.conn,now=NOW); bootstrap_scope(self.conn,now=NOW)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM screener_collection_scope').fetchone()[0],3)
        self.assertEqual(preview_due_companies(self.conn,now_utc='2099-01-01T00:00:00+00:00',limit=10),())
        self.assertEqual(_failure_policy(RuntimeError('Company is below Screener market-cap scope floor: 500 INR crore')),(False,False))

    def test_lightweight_cap_check_without_financial_tables(self):
        html=b'<div id="company-info" data-company-id="1"></div><div id="top"><a>NSE: ALPHA</a></div><ul id="top-ratios"><li><span class="name">Market Cap</span><span class="value">Rs. 579.5 Cr.</span></li></ul>'
        self.assertEqual(scope_snapshot(html)['market_cap_crore'],Decimal('579.5'))
        self.assertEqual(call_months('2026-06-30'),('Jul 2026','Aug 2026','Sep 2026'))
        self.assertEqual(call_months('2026-12-31'),('Jan 2027','Feb 2027','Mar 2027'))
        sme=html.replace(b'NSE: ALPHA',b'BSE - SME: 543621')
        self.assertIn('543621',scope_snapshot(sme)['identifiers'])

    def test_old_call_is_not_tagged_as_latest_quarter(self):
        from scrapper.pipeline.promote_document_links import call_matches_result_window, url_fiscal_year_conflicts
        self.assertFalse(call_matches_result_window('Jul 2025','2026-06-30'))
        self.assertTrue(call_matches_result_window('Jul 2026','2026-06-30'))
        self.assertFalse(call_matches_result_window('Jun 2026','2026-06-30'))
        self.assertTrue(url_fiscal_year_conflicts('https://issuer.test/2025-26/q1/transcript.pdf','2026-06-30'))
        self.assertFalse(url_fiscal_year_conflicts('https://issuer.test/2026-27/q1/transcript.pdf','2026-06-30'))

    def test_landing_page_requires_one_same_issuer_pdf(self):
        from scrapper.documents.landing_page import issuer_pdf_link
        html=b'<article><a href="/Concall-Q1FY27.pdf">Download transcript</a></article>'
        self.assertEqual(issuer_pdf_link(html,page_url='https://issuer.test/call',artifact_type='transcript'),'https://issuer.test/Concall-Q1FY27.pdf')
        self.assertIsNone(issuer_pdf_link(html.replace(b'/Concall',b'https://other.test/Concall'),page_url='https://issuer.test/call',artifact_type='transcript'))
        self.assertIsNone(issuer_pdf_link(html.replace(b'</article>',b'<a href="/Concall-Q2FY27.pdf">Transcript</a></article>'),page_url='https://issuer.test/call',artifact_type='transcript'))
        self.assertIsNone(issuer_pdf_link(html+b'<input type="password">',page_url='https://issuer.test/call',artifact_type='transcript'))


if __name__=='__main__': unittest.main()
