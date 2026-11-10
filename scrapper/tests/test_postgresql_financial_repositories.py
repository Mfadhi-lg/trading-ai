"""Existing Screener, exchange and Trendlyne flows on an isolated native backend."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

from scrapper.core.http import FetchResponse
from scrapper.db import collection_scope_repo, repo, screener_discovery_repo, trendlyne_repo
from scrapper.db.exchange import filings, schedule, securities, snapshots
from scrapper.db.screener import acceptance, scope_tasks
from scrapper.sources.exchange.result_filings import parse_result_filings
from scrapper.sources.exchange.security_master import parse_security_master
from scrapper.sources.screener.company import parse_company_page
from scrapper.sources.screener.result_screen import parse_result_screen
from scrapper.sources.trendlyne.monthly_mf import parse_monthly_mf_page
from scrapper.sources.trendlyne.urls import verified_mapping
from scrapper.tests import test_e01_exchange_universe as exchange_fixture
from scrapper.tests import test_trendlyne_t01 as trendlyne_fixture
from scrapper.tests.postgresql_fixtures import DisposablePostgreSQLTestCase
from scrapper.tests.test_postgresql_schema import synthetic_sqlite
from scrapper.tests.test_screener_discovery import screen_html

NOW = '2026-11-09T00:00:00+00:00'
LATER = '2026-11-09T00:20:00+00:00'
ROOT = Path(__file__).parent


class FinancialRepositoryTests(DisposablePostgreSQLTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = synthetic_sqlite()
        self.source.row_factory = sqlite3.Row
        self.addCleanup(self.source.close)

    def records(self, conn, table):
        return sorted((dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')),
                      key=lambda row: json.dumps(row, sort_keys=True))

    def response(self, content, url='https://www.screener.in/screen/raw/?query=x'):
        return FetchResponse(url, url, 200, content, 'text/html', 'utf-8', None, None, 'public', 'ok')

    def seed_screen(self, conn):
        raw = screen_html().replace(b'/consolidated/', b'/')
        response = self.response(raw)
        run = repo.start_run(conn, 'synthetic', {})
        blob = repo.store_blob(conn, response, 'SCREEN', 'standalone', NOW, Path(self.temp.name))
        fetch = repo.record_fetch(conn, run, None, response, 'standalone', NOW, blob,
                                  resource_kind='result_screen')
        parsed = parse_result_screen(raw, 'x', response.requested_url)
        screen, created = screener_discovery_repo.persist_screen(conn, parsed,
            query_role='synthetic', fetch_id=fetch, blob_id=blob, observed_at_utc=NOW,
            parser_name='synthetic-screen', parser_version='1')
        self.assertTrue(created)
        return screener_discovery_repo.enqueue_screen_candidates(conn, screen, now_utc=NOW)

    def test_screen_discovery_scope_budget_and_claim_fencing(self):
        with self.session() as conn, patch.object(repo, 'utc_now', return_value=NOW):
            expected = self.seed_screen(self.source)
            actual = self.seed_screen(conn)
            self.assertEqual(actual, expected)
            for table in ('screener_screen_runs', 'screener_screen_source_cells', 'screener_result_candidates',
                          'screener_company_collection_state', 'screener_company_queue'):
                with self.subTest(table=table):
                    self.assertEqual(self.records(conn, table), self.records(self.source, table))
            for target in (self.source, conn):
                with target:
                    collection_scope_repo.record_scope(target, path='/company/ALPHA/', company_id=None,
                        value='1', observed_at=NOW, evidence={'synthetic': True}, floor='500')
                    # The older textual timestamp is a later instant. Preserve its source spelling.
                    collection_scope_repo.record_scope(target, path='/company/ALPHA/', company_id=None,
                        value='2', observed_at='2026-11-08T20:00:00-05:00',
                        evidence={'synthetic': True}, floor='500')
                task = scope_tasks.enqueue(target, now=NOW, budget=2)
                self.assertTrue(scope_tasks.admit(target, task['task_id']))
                self.assertTrue(scope_tasks.admit(target, task['task_id']))
                self.assertFalse(scope_tasks.admit(target, task['task_id']))
                scope_tasks.finish(target, task['task_id'], now=NOW, result={'exact': '0.0000000001'})
            self.assertEqual(self.records(conn, 'screener_collection_scope'), self.records(self.source, 'screener_collection_scope'))
            first = screener_discovery_repo.claim_due_companies(conn, now_utc=NOW, limit=1,
                                                                worker_id='first', lease_seconds=1)
            self.assertEqual(len(first), 1)
            self.assertNotEqual(first[0].company_path_normalized, '/company/ALPHA/')
            replacement = screener_discovery_repo.claim_due_companies(conn, now_utc=LATER, limit=1,
                                                                      worker_id='replacement')
            self.assertEqual(replacement[0].queue_item_id, first[0].queue_item_id)
            with self.assertRaisesRegex(RuntimeError, 'no longer owned'):
                screener_discovery_repo.mark_running(conn, first[0], worker_id='first', now_utc=LATER)
            screener_discovery_repo.mark_running(conn, replacement[0], worker_id='replacement', now_utc=LATER)
            screener_discovery_repo.fail_company(conn, replacement[0], worker_id='replacement', now_utc=LATER,
                                                 error={'message': 'controlled'}, retryable=True)
            self.assertIsNotNone(conn.execute('SELECT 1').fetchone())

    def test_concurrent_screener_claims_never_overlap(self):
        with self.session() as conn:
            self.seed_screen(conn)
            count = conn.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]
        def claim(number):
            with self.session() as conn:
                return screener_discovery_repo.claim_due_companies(conn, now_utc=NOW, limit=1,
                                                                    worker_id='worker-' + str(number))
        with ThreadPoolExecutor(max_workers=count + 1) as workers:
            results = list(workers.map(claim, range(count + 1)))
        identities = [item.queue_item_id for batch in results for item in batch]
        self.assertEqual(len(identities), count)
        self.assertEqual(len(set(identities)), count)

    def snapshot(self, conn, content, *, dataset, day):
        response = self.response(content, 'https://example.invalid/synthetic.csv')
        blob = snapshots.store_exchange_blob(conn, response, exchange='NSE', dataset=dataset,
            effective_date=day, observed_at_utc=NOW, snapshot_root=Path(self.temp.name), utc_now=lambda: NOW)
        result, created = snapshots.persist_snapshot(conn, exchange='NSE', dataset=dataset,
            effective_date=day, source_label='synthetic', requested_url=response.requested_url,
            acquisition_mode='fetched', fetch_id=None, blob_id=blob, parser_name='synthetic',
            parser_version='1', source_encoding='utf-8', observed_at_utc=NOW, headers=(),
            accepted_row_count=3, rejected=())
        return result

    def exercise_exchange(self, conn):
        outcomes = []
        for content, day in ((exchange_fixture.NSE_DAY_ONE, '2026-08-05'),
                             (exchange_fixture.NSE_DAY_TWO, '2026-08-06')):
            snapshot = self.snapshot(conn, content, dataset='nse_equity_master', day=day)
            parsed = parse_security_master(content, 'nse_equity_master')
            outcomes.append(securities.apply_security_master(conn, parsed, snapshot_id=snapshot,
                effective_date=day, now_utc=NOW, authoritative_segments=(('NSE', 'equity'),)))
        content = exchange_fixture.NSE_RESULTS
        snapshot = self.snapshot(conn, content, dataset='nse_financial_results', day='2026-08-06')
        parsed = parse_result_filings(content, 'nse_financial_results')
        outcomes.append(filings.ingest_result_filings(conn, parsed, snapshot_id=snapshot, now_utc=NOW))
        outcomes.append(filings.enqueue_result_filings(conn, snapshot_id=snapshot, now_utc=NOW,
                                                      max_new_queue_entries=1))
        outcomes.append(filings.enqueue_result_filings(conn, snapshot_id=snapshot, now_utc=NOW,
                                                      max_new_queue_entries=1))
        schedule.record_schedule_run(conn, dataset='nse_financial_results', scheduled_for='2026-08-06',
            attempt=1, outcome='published_changed', snapshot_id=snapshot, error='',
            started_at_utc=NOW, finished_at_utc=NOW)
        self.assertEqual(schedule.datasets_published_on(conn, '2026-08-06'), {'nse_financial_results'})
        return outcomes

    def test_exchange_versions_filing_budget_and_schedule_match_sqlite(self):
        expected = self.exercise_exchange(self.source)
        with self.session() as conn:
            actual = self.exercise_exchange(conn)
            self.assertEqual(actual, expected)
            for table in ('exchange_source_snapshots', 'exchange_securities', 'exchange_security_versions',
                          'exchange_security_events', 'exchange_company_registry', 'exchange_company_links',
                          'exchange_result_filings', 'exchange_result_watermarks', 'exchange_screener_enqueues',
                          'exchange_company_result_state', 'exchange_schedule_runs', 'screener_company_queue'):
                with self.subTest(table=table):
                    actual_rows = self.records(conn, table)
                    expected_rows = self.records(self.source, table)
                    if table == 'exchange_company_registry':
                        # PostgreSQL sequences legitimately advance on an UPSERT;
                        # compare source identity and every persisted value.
                        for rows in (actual_rows, expected_rows):
                            for row in rows:
                                row.pop('registry_company_id')
                    elif table == 'exchange_company_links':
                        for target, rows in ((conn, actual_rows), (self.source, expected_rows)):
                            identities = {row['registry_company_id']: row['isin']
                                          for row in target.execute('SELECT registry_company_id,isin FROM exchange_company_registry')}
                            for row in rows:
                                row['registry_company_id'] = identities[row['registry_company_id']]
                                row.pop('link_id')
                    self.assertEqual(actual_rows, expected_rows)

    def test_transferred_broadcast_offsets_choose_latest_instant_and_matching_filing(self):
        parsed = parse_result_filings(exchange_fixture.NSE_RESULTS,'nse_financial_results')
        template = parsed.filings[0]
        # Lexically larger spelling is an earlier instant: 06:00 IST=00:30UTC.
        earlier = replace(template,broadcast_at_utc='2026-11-09T06:00:00+05:30',
                          broadcast_raw='source offset earlier')
        later = replace(template,broadcast_at_utc='2026-11-09T01:00:00+00:00',
                        broadcast_raw='source offset later')
        batch = replace(parsed,filings=(earlier,later),rejected=())
        with self.session() as conn:
            snapshot = self.snapshot(conn,exchange_fixture.NSE_RESULTS,
                dataset='nse_financial_results',day='2026-11-09')
            result = filings.ingest_result_filings(conn,batch,snapshot_id=snapshot,now_utc=NOW)
            self.assertEqual(result['watermark_utc'],later.broadcast_at_utc)
            watermark = conn.execute('SELECT last_broadcast_at_utc FROM exchange_result_watermarks').fetchone()[0]
            self.assertEqual(watermark,later.broadcast_at_utc)
            # Queue decisions may process source spellings in a different order;
            # the state pointer and stored timestamp must still describe one event.
            filings.enqueue_result_filings(conn,snapshot_id=snapshot,now_utc=NOW)
            state = conn.execute('SELECT latest_broadcast_at_utc,latest_filing_id FROM exchange_company_result_state').fetchone()
            filing = conn.execute('SELECT broadcast_at_utc FROM exchange_result_filings WHERE filing_id=?',
                                  (state['latest_filing_id'],)).fetchone()[0]
            self.assertEqual(state['latest_broadcast_at_utc'],later.broadcast_at_utc)
            self.assertEqual(filing,later.broadcast_at_utc)
            # A subsequent older spelling must not move the watermark backwards.
            again = filings.ingest_result_filings(conn,replace(batch,filings=(earlier,)),
                                                  snapshot_id=snapshot,now_utc=NOW)
            self.assertEqual(again['watermark_utc'],later.broadcast_at_utc)
            self.assertEqual(again['at_or_before_watermark'],1)

    def test_trendlyne_source_sections_and_exact_observations_match_sqlite(self):
        parsed = parse_monthly_mf_page(trendlyne_fixture.FIXTURE.read_bytes())
        response = trendlyne_fixture.fixture_response()
        mapping = verified_mapping('RBA')
        def persist(conn):
            company = trendlyne_repo.ensure_trendlyne_company(conn, mapping)
            run = repo.start_run(conn, 'synthetic', {})
            blob = trendlyne_repo.store_trendlyne_blob(conn, response, mapping, NOW,
                                                      snapshot_root=Path(self.temp.name))
            fetch = trendlyne_repo.record_trendlyne_fetch(conn, run, company, response, 'latest', parsed.resolved_source_period, NOW, blob)
            parse, _ = repo.get_or_create_parse_artifact(conn, blob, 'trendlyne-synthetic', '1', '1')
            trendlyne_repo.store_parsed_trendlyne_page(conn, parse, parsed)
            return company, run, blob, fetch, parse
        with patch.object(repo, 'utc_now', return_value=NOW), patch.object(trendlyne_repo, 'utc_now', return_value=NOW):
            expected = persist(self.source)
            with self.session() as conn:
                actual = persist(conn)
                self.assertEqual(actual, expected)
                for table in ('companies', 'source_identifiers', 'security_identifiers', 'fetch_observations',
                              'source_sections', 'source_fields', 'source_table_cells', 'trendlyne_monthly_pages',
                              'trendlyne_fund_observations', 'trendlyne_mf_page_summaries'):
                    with self.subTest(table=table):
                        self.assertEqual(self.records(conn, table), self.records(self.source, table))

    def test_trendlyne_parse_failure_retains_failed_status_without_partial_sections(self):
        response = trendlyne_fixture.fixture_response()
        mapping = verified_mapping('RBA')
        with self.session() as conn:
            blob = trendlyne_repo.store_trendlyne_blob(conn, response, mapping, NOW,
                                                      snapshot_root=Path(self.temp.name))
            parse, _ = repo.get_or_create_parse_artifact(conn, blob, 'trendlyne-failure', '1', '1')
            with self.assertRaises(AttributeError):
                trendlyne_repo.store_parsed_trendlyne_page(conn, parse, object())
            row = conn.execute('SELECT parse_status,validation_status FROM parse_artifacts WHERE parse_id=?',
                               (parse,)).fetchone()
            self.assertEqual(tuple(row), ('failed','failed'))
            self.assertEqual(conn.execute('SELECT count(*) FROM source_sections WHERE parse_id=?',
                                          (parse,)).fetchone()[0], 0)
            self.assertFalse(conn.in_transaction)

    def test_acceptance_rejection_audit_commits_before_public_refusal(self):
        raw = (ROOT / 'fixtures/screener/LAURUSLABS_standalone.html').read_bytes()
        parsed = parse_company_page(raw, NOW)
        with self.session() as conn:
            company = repo.ensure_company(conn, parsed)
            blob = repo.store_blob(conn, self.response(raw), 'LAURUSLABS', 'standalone', NOW, Path(self.temp.name))
            parse, _ = repo.get_or_create_parse_artifact(conn, blob, 'synthetic', '1', '1')
            repo.store_parsed_company(conn, parse, parsed, NOW)
            with conn:
                conn.execute("UPDATE parse_artifacts SET validation_status='failed' WHERE parse_id=?", (parse,))
            with self.assertRaisesRegex(ValueError, 'automatic acceptance rules'):
                acceptance.accept_parse(conn, company, parse, 'screener', 'standalone',
                    _utc_now=lambda: NOW, sync_screener_quarter_state=lambda *args: None)
            self.assertFalse(conn.in_transaction)
        with self.session() as conn:
            self.assertEqual(conn.execute("SELECT action FROM acceptance_events").fetchone()[0], 'rejected')
            self.assertEqual(conn.execute("SELECT count(*) FROM accepted_page_versions").fetchone()[0], 0)
