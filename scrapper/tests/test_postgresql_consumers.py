"""Native public validators, source promotion, exports and read-only views."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

from scrapper.core.http import FetchResponse
from scrapper.db import repo
from scrapper.documents.discovery.promotion import promote_document_links, stored_artifact_period_evidence
from scrapper.documents.download_scope import register_download_scope
from scrapper.documents.rating_identity import company_names
from scrapper.exports.json_export import build_export
from scrapper.exports.quality_report import build_quality_report
from scrapper.exports.document_quality_report import build_document_quality_report
from scrapper.exports.exchange_universe_coverage import build_exchange_universe_coverage
from scrapper.exports.mf_workbook_json_export import build_mf_workbook_export
from scrapper.exports.mf_coverage_report import build_mf_coverage_report
from scrapper.exports.document_storage_projection import build_document_storage_projection
from scrapper.exports.artifact_coverage import build_artifact_coverage_report
from scrapper.exports.document_alternate_source_recovery import build_alternate_source_recovery_report
from scrapper.exports.document_recovery import build_document_recovery_report
from scrapper.exports.exchange_reconciliation_report import build_exchange_reconciliation_report
from scrapper.exports.screener_discovery_coverage import build_screener_discovery_coverage
from scrapper.commands.financial_coverage_audit import build_financial_coverage_report
from scrapper.commands.screener_period_coverage import period_coverage
from scrapper.documents.revisions import enqueue_revision
from scrapper.documents.late_publications import extend_exhausted_waits
from scrapper.sources.screener.company import parse_company_page
from scrapper.validation.screener import validate_parse
from scrapper.view.browser_rating_monitor import fetch_rating_data
from scrapper.view.download_monitor import fetch_download_monitor_data
from scrapper.view.monitor import fetch_monitor_data
from scrapper.view.render import render_tables, render_browse
from scrapper.tests.postgresql_fixtures import DisposablePostgreSQLTestCase
from scrapper.tests.test_postgresql_schema import synthetic_sqlite
from scrapper.tests import test_postgresql_mf_repositories as mf_fixtures
from scrapper.sources.documents.routine import company_pages, ingest_page

NOW = '2026-11-09T10:00:00+00:00'
FIXTURE = Path(__file__).parent / 'fixtures/screener/LAURUSLABS_standalone.html'


class PostgreSQLConsumerTests(DisposablePostgreSQLTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = synthetic_sqlite()
        self.source.row_factory = sqlite3.Row
        self.addCleanup(self.source.close)
        self.conn = self.connection()
        self.addCleanup(self.conn.close)
        self.content = FIXTURE.read_bytes()
        self.parsed = parse_company_page(self.content, NOW)

    def seed(self, conn):
        url = 'https://www.screener.in/company/LAURUSLABS/'
        response = FetchResponse(url, url, 200, self.content, 'text/html', 'utf-8', None, None, 'public', 'ok')
        with patch.object(repo, 'utc_now', return_value=NOW):
            run = repo.start_run(conn, 'synthetic', {})
            company = repo.ensure_company(conn, self.parsed)
            repo.ensure_source_identifier(conn, company, self.parsed, url)
            blob = repo.store_blob(conn, response, 'LAURUSLABS', 'standalone', NOW, Path(self.temp.name))
            repo.record_fetch(conn, run, company, response, 'standalone', NOW, blob)
            parse_id, _ = repo.get_or_create_parse_artifact(conn, blob, 'fixture-parser', '1', '1')
            repo.store_parsed_company(conn, parse_id, self.parsed, NOW)
        return company, parse_id

    def test_existing_validator_and_source_export_match_sqlite(self):
        company, parse_id = self.seed(self.source)
        native_company, native_parse = self.seed(self.conn)
        self.assertEqual((native_company, native_parse), (company, parse_id))
        with patch('scrapper.validation.screener._utc_now', return_value=NOW):
            expected = validate_parse(self.source, parse_id, 'LAURUSLABS', 'standalone')
            actual = validate_parse(self.conn, parse_id, 'LAURUSLABS', 'standalone')
        self.assertEqual(actual, expected)
        self.assertEqual(build_export(self.conn, company, parse_id), build_export(self.source, company, parse_id))
        self.assertFalse(self.conn.in_transaction)

    def test_existing_promotion_and_explicit_scope_are_idempotent(self):
        company, parse_id = self.seed(self.conn)
        first = promote_document_links(self.conn, document_type='concall_ppt',
            parse_id=parse_id, verified_company_id=company, promoted_at_utc=NOW)
        again = promote_document_links(self.conn, document_type='concall_ppt',
            parse_id=parse_id, verified_company_id=company, promoted_at_utc=NOW)
        self.assertEqual(first['artifact_ids'], again['artifact_ids'])
        artifacts = tuple(row[0] for row in self.conn.execute('SELECT artifact_id FROM document_artifacts'))
        self.assertTrue(artifacts)
        options = dict(scope_id='consumer-scope', companies=[{'company_id': company}],
                       scope_kind='custom', artifact_types=('presentation',), artifact_ids=artifacts,
                       enforce_pilot_composition=False, created_at_utc=NOW)
        self.assertTrue(register_download_scope(self.conn, **options)['created'])
        self.assertFalse(register_download_scope(self.conn, **options)['created'])
        with self.session(read_only=True) as reader:
            self.assertFalse(register_download_scope(reader, **options)['created'])
            preview = promote_document_links(reader, document_type='concall_ppt',
                parse_id=parse_id, verified_company_id=company, dry_run=True, promoted_at_utc=NOW)
            self.assertTrue(preview['dry_run'])
            self.assertFalse(reader.in_transaction)
        for artifact in artifacts:
            self.assertTrue(company_names(self.conn, artifact))
            stored_artifact_period_evidence(self.conn, artifact)
        self.assertFalse(self.conn.in_transaction)

    def test_monitor_introspection_and_reports_execute_with_native_catalogues(self):
        company, parse_id = self.seed(self.conn)
        self.assertIn('companies', render_tables(self.conn))
        self.assertIn('LAURUS', render_browse(self.conn, 'companies', where='company_id = 1', limit=5).upper())
        self.assertEqual(render_browse(self.conn, 'missing', where=None, limit=1), 'Unknown table or view: missing')
        with self.assertRaises(ValueError):
            render_browse(self.conn, 'companies', where='missing=1', limit=1)
        fetch_rating_data(self.conn)
        fetch_download_monitor_data(self.conn)
        fetch_monitor_data(self.conn)
        build_quality_report(self.conn, generated_at_utc=NOW)
        build_document_quality_report(self.conn, generated_at_utc=NOW)
        build_exchange_universe_coverage(self.conn)
        self.assertFalse(self.conn.in_transaction)

    def test_all_read_only_reports_keep_connection_idle(self):
        company, parse_id = self.seed(self.conn)
        with self.conn:
            self.conn.execute("""INSERT INTO accepted_page_versions(company_id,source,reporting_scope,
                resolved_source_period,parse_id,accepted_at_utc,accepted_by,acceptance_mode)
                VALUES (?,'screener','standalone','2026-06',?,?,'test','automatic')""",(company,parse_id,NOW))
            self.conn.execute("""INSERT INTO acceptance_events(company_id,source,reporting_scope,
                resolved_source_period,parse_id,action,accepted_at_utc,accepted_by,acceptance_mode,reason_json)
                VALUES (?,'screener','standalone','2026-06',?,'accepted',?,'test','automatic','{}')""",(company,parse_id,NOW))
        with self.session(read_only=True) as reader:
            for function in (fetch_rating_data, fetch_download_monitor_data, fetch_monitor_data,
                             build_quality_report, build_document_quality_report,
                             build_exchange_universe_coverage, build_artifact_coverage_report,
                             build_alternate_source_recovery_report, build_document_recovery_report,
                             build_exchange_reconciliation_report, build_screener_discovery_coverage,
                             build_financial_coverage_report):
                with self.subTest(function=function.__name__):
                    function(reader)
                    self.assertFalse(reader.in_transaction)
            self.assertTrue(period_coverage(reader, '2026-06'))
            self.assertTrue(company_pages(reader,company,permitted_only=False,now=NOW))
            self.assertFalse(reader.in_transaction)

    def test_official_page_persistence_uses_original_source_and_no_open_network_transaction(self):
        company, _ = self.seed(self.conn)
        body = b'<html><a href="https://issuer.example/presentation.pdf">Q2 FY2027 Investor Presentation</a></html>'
        result = ingest_page(self.conn,company_id=company,source='company_ir',url='https://issuer.example/investors',
                             body=body,now=NOW,snapshot_root=Path(self.temp.name))
        self.assertEqual(result['status'],'discovered')
        self.assertFalse(self.conn.in_transaction)
        paths = [Path(row[0]) for row in self.conn.execute('SELECT local_path FROM response_blobs')]
        self.assertIn(body,[path.read_bytes() for path in paths])

    def test_official_workbook_export_and_coverage_match_sqlite(self):
        expected, _, _ = mf_fixtures.MutualFundRepositoryTests.import_workbook(self, self.source)
        actual, _, _ = mf_fixtures.MutualFundRepositoryTests.import_workbook(self, self.conn)
        self.assertEqual(build_mf_workbook_export(self.conn, actual.import_file_id),
                         build_mf_workbook_export(self.source, expected.import_file_id))
        self.assertEqual(build_mf_coverage_report(self.conn, publisher='ppfas', through_month='2026-06', month_count=2),
                         build_mf_coverage_report(self.source, publisher='ppfas', through_month='2026-06', month_count=2))
        self.assertFalse(self.conn.in_transaction)

    def test_revision_queue_is_concurrent_idempotent_and_preserves_source(self):
        company, parse_id = self.seed(self.conn)
        promote_document_links(self.conn, document_type='concall_ppt',
            parse_id=parse_id, verified_company_id=company, promoted_at_utc=NOW)
        with self.conn:
            artifact = self.conn.execute('SELECT artifact_id FROM document_artifacts ORDER BY artifact_id LIMIT 1').fetchone()[0]
            url = self.conn.execute('SELECT document_url FROM artifact_sources WHERE artifact_id=? ORDER BY artifact_source_id LIMIT 1', (artifact,)).fetchone()[0]
            blob = self.conn.execute('SELECT blob_id FROM response_blobs LIMIT 1').fetchone()[0]
            self.conn.execute("""INSERT INTO artifact_contents(artifact_id,blob_id,media_type,
                content_sha256,byte_size,is_official,content_origin,created_at_utc)
                VALUES (?,?,'application/pdf','synthetic-revision-source',12,1,'publisher_original',?)""", (artifact,blob,NOW))
        def enqueue(_):
            with self.session() as conn:
                return enqueue_revision(conn, artifact_id=artifact, url=url,
                    indicator={'etag':'new-indicator'}, now=NOW)
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(enqueue,range(3)))
        self.assertEqual(len({row['download_job_id'] for row in results}),1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM artifact_contents').fetchone()[0],1)
        self.assertEqual(extend_exhausted_waits(self.conn,now=NOW,limit=1),[])
        scope = self.conn.execute("SELECT scope_id FROM artifact_download_scopes WHERE scope_id LIKE 'document-revision-%'").fetchone()[0]
        build_document_storage_projection(self.conn, scope_id=scope, document_store=Path(self.temp.name))
        self.assertFalse(self.conn.in_transaction)
