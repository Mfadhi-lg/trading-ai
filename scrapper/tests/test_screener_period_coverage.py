from __future__ import annotations

from scrapper.tests.database_fixtures import prepare_database

import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from scrapper.db.database import connect
from scrapper.db.screener_discovery_repo import ClaimedCompany, skip_if_already_accepted, claim_due_companies, mark_running, complete_company
from scrapper.pipeline.screener_vertical import ingest_screener_response
from scrapper.commands.screener_period_coverage import period_coverage, run_period_coverage
from scrapper.tests.test_screener_vertical import (
    fixture_standalone_response, fixture_standalone_peer_response,
    fixture_quick_ratios_response, FIRST_FETCH,
)


class PeriodCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / 'scraper.db'

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, expected=None):
        prepare_database(self.db)
        return ingest_screener_response('LAURUSLABS', 'standalone',
            fixture_standalone_response(), fixture_standalone_peer_response(),
            fixture_quick_ratios_response(), db_path=self.db,
            snapshot_root=self.root/'snapshots', export_root=self.root/'exports',
            fetched_at_utc=FIRST_FETCH, expected_result_period=expected)

    def test_bootstrap_and_old_queue_period_resolve_from_table(self):
        for expected in (None, '1970-01', '2026-03'):
            result = self.ingest(expected)
            with closing(connect(self.db)) as conn:
                row = conn.execute('SELECT resolved_source_period FROM accepted_page_versions WHERE parse_id=?', (result.parse_id,)).fetchone()
                self.assertEqual(row[0], '2026-06')

    def test_repair_is_offline_and_idempotent(self):
        result = self.ingest()
        with closing(connect(self.db)) as conn:
            with conn:
                conn.execute("UPDATE accepted_page_versions SET resolved_source_period='1970-01'")
            before = period_coverage(conn, '2026-06', ['LAURUSLABS'])
            self.assertTrue(before[0]['has_target_quarter'])
            self.assertTrue(before[0]['period_tag_mismatch'])
            fetch_count = conn.execute('SELECT COUNT(*) FROM fetch_observations').fetchone()[0]
        opts = dict(period='2026-06', symbols=['LAURUSLABS'], repair=True,
                    db_path=self.db, export_root=self.root/'exports')
        repaired = run_period_coverage(**opts)
        self.assertEqual(repaired['period_tag_mismatch_count'], 0)
        self.assertEqual(len(repaired['repaired']), 1)
        self.assertEqual(run_period_coverage(**opts)['repaired'], [])
        with closing(connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM fetch_observations').fetchone()[0], fetch_count)

    def test_valid_historical_period_is_preserved(self):
        from scrapper.validation.screener import accept_parse
        result=self.ingest()
        with closing(connect(self.db)) as conn:
            accept_parse(conn,result.company_id,result.parse_id,'screener','standalone','2026-03')
        run_period_coverage(period='2026-06',symbols=['LAURUSLABS'],repair=True,
                            db_path=self.db,export_root=self.root/'exports')
        with closing(connect(self.db)) as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM accepted_page_versions WHERE resolved_source_period='2026-03'").fetchone())

    def test_future_period_missing_despite_accepted_company(self):
        self.ingest()
        with closing(connect(self.db)) as conn:
            rows = period_coverage(conn, '2026-09', ['LAURUSLABS'])
            self.assertFalse(rows[0]['has_target_quarter'])

    def test_watermark_without_requested_rows_does_not_skip(self):
        result = self.ingest()
        with closing(connect(self.db)) as conn:
            with conn:
                conn.execute("""INSERT INTO screener_company_collection_state
                    (company_path_normalized,reporting_scope,company_id,latest_discovered_result_period,latest_accepted_result_period,
                     latest_accepted_parse_id,updated_at_utc)
                    VALUES ('/company/LAURUSLABS/','standalone',?,'2026-09','2026-09',?,?)""",
                    (result.company_id,result.parse_id,FIRST_FETCH))
            item = ClaimedCompany(999,None,'/company/LAURUSLABS/','standalone','2026-09','result_period_advanced',0,'test')
            self.assertFalse(skip_if_already_accepted(conn,item,worker_id='test',now_utc=FIRST_FETCH))

    def test_queue_completion_uses_actual_latest_period(self):
        from scrapper.pipeline.screener_discovery import discover_screener_results
        from scrapper.tests.test_screener_discovery import screen_html
        result = self.ingest('2026-03')
        fixture = self.root / 'screen.html'
        fixture.write_bytes(screen_html().replace(b'ALPHA/consolidated/', b'LAURUSLABS/').replace(b'202606.00',b'202603.00'))
        prepare_database(self.db)
        discover_screener_results(query_role='period_test', query='Last result date > 202602',
            fixture=fixture, db_path=self.db, snapshot_root=self.root/'snapshots', now_utc=lambda: FIRST_FETCH)
        with closing(connect(self.db)) as conn:
            item = next(x for x in claim_due_companies(conn,now_utc=FIRST_FETCH,limit=3,worker_id='test')
                        if x.company_path_normalized == '/company/LAURUSLABS/')
            mark_running(conn,item,worker_id='test',now_utc=FIRST_FETCH)
            complete_company(conn,item,parse_id=result.parse_id,company_id=result.company_id,
                export_path=str(result.export_path),worker_id='test',now_utc=FIRST_FETCH)
            row=conn.execute("SELECT latest_accepted_result_period FROM screener_company_collection_state WHERE company_path_normalized='/company/LAURUSLABS/'").fetchone()
            self.assertEqual(row[0],'2026-06')

    def test_missing_quarter_stops_before_supplemental_requests(self):
        from scrapper.pipeline.screener_vertical import scrape_screener_company_path
        class Client:
            def fetch_company(self, url):
                return fixture_standalone_response()
        with self.assertRaisesRegex(RuntimeError,'Screen result period is absent'):
            prepare_database(self.db)
            scrape_screener_company_path('/company/LAURUSLABS/','standalone',
                expected_result_period='2026-09',host_client=Client(),
                db_path=self.db,snapshot_root=self.root/'snapshots',export_root=self.root/'exports')
        with closing(connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM fetch_observations').fetchone()[0],1)

    def test_recent_absent_source_is_reused_without_network_or_cookie(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        prepare_database(self.db)
        ingest_screener_response('LAURUSLABS','standalone',fixture_standalone_response(),
            fixture_standalone_peer_response(),fixture_quick_ratios_response(),
            db_path=self.db,snapshot_root=self.root/'snapshots',export_root=self.root/'exports',
            fetched_at_utc=datetime.now(timezone.utc).isoformat())
        with patch('scrapper.core.http.ScreenerHostClient',side_effect=AssertionError('network client created')):
            report=run_period_coverage(period='2026-09',symbols=['LAURUSLABS'],refresh=True,
                db_path=self.db,snapshot_root=self.root/'snapshots',export_root=self.root/'exports')
        self.assertEqual(len(report['source_absences']),1)
        self.assertTrue(report['source_absences'][0]['reused_saved_response'])
        self.assertEqual(report['refreshed'],[])

    def test_manual_acceptance_synchronizes_existing_queue_state(self):
        from scrapper.validation.screener import accept_parse
        result=self.ingest()
        with closing(connect(self.db)) as conn:
            with conn:
                conn.execute("""INSERT INTO screener_company_collection_state
                    (company_path_normalized,reporting_scope,company_id,latest_discovered_result_period,
                     latest_accepted_result_period,latest_accepted_parse_id,updated_at_utc)
                    VALUES ('/company/LAURUSLABS/','standalone',?,'1970-01','1970-01',?,?)""",
                    (result.company_id,result.parse_id,FIRST_FETCH))
            accept_parse(conn,result.company_id,result.parse_id,'screener','standalone','2026-06')
            self.assertEqual(conn.execute('SELECT latest_accepted_result_period FROM screener_company_collection_state').fetchone()[0],'2026-06')

    def test_parser_failure_preserves_source_response(self):
        from dataclasses import replace
        from bs4 import BeautifulSoup
        from scrapper.pipeline.screener_vertical import scrape_screener_company_path
        response=fixture_standalone_response()
        soup=BeautifulSoup(response.content,'html.parser')
        soup.select_one('#quarters thead').decompose()
        response=replace(response,content=str(soup).encode())
        class Client:
            def fetch_company(self,url): return response
        with self.assertRaisesRegex(ValueError,'Quarterly results headers are missing'):
            prepare_database(self.db)
            scrape_screener_company_path('/company/LAURUSLABS/','standalone',host_client=Client(),
                db_path=self.db,snapshot_root=self.root/'snapshots',export_root=self.root/'exports')
        with closing(connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM fetch_observations').fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scrape_runs WHERE status='error'").fetchone()[0],1)

    def test_requested_period_absent_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Screen result period is absent'):
            self.ingest('2026-09')
