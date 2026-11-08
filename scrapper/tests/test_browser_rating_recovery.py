import sqlite3
import io
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from scrapper.commands.browser_rating_recovery import PublisherFileUnavailable, PublisherRateLimit, claim, cool_down, fetch_report, name_key, release_owned_jobs, retry_delay, seed, transient_error, validate_report


class BrowserRatingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
          CREATE TABLE companies(company_id INTEGER PRIMARY KEY,current_legal_name TEXT);
          CREATE TABLE document_artifacts(artifact_id INTEGER PRIMARY KEY,company_id INTEGER,artifact_type TEXT,event_date TEXT);
          CREATE TABLE artifact_sources(artifact_source_id INTEGER PRIMARY KEY,artifact_id INTEGER,document_url TEXT);
          CREATE TABLE artifact_contents(content_id INTEGER PRIMARY KEY,artifact_id INTEGER);
          CREATE TABLE credit_rating_download_queue(artifact_id INTEGER,requested_url TEXT,target_host TEXT,status TEXT);
          CREATE TABLE artifact_download_queue(artifact_id INTEGER,requested_url TEXT,target_host TEXT,status TEXT);
          INSERT INTO companies VALUES(1,'Example Limited');
        ''')
        migration = Path(__file__).parents[1] / 'db/migrations/065_operator_browser_rating_recovery.sql'
        self.conn.executescript(migration.read_text())
        self.conn.executescript((migration.parent / '066_browser_rating_recovery_backoff.sql').read_text())
        self.conn.execute('ALTER TABLE browser_rating_recovery_jobs ADD COLUMN claim_token TEXT')

    def tearDown(self):
        self.conn.close()

    def add(self, artifact_id, status='SKIPPED_POLICY', kind='credit_rating', covered=False):
        url = f'https://www.icra.in/report/{artifact_id}'
        self.conn.execute('INSERT INTO document_artifacts VALUES(?,1,?,?)', (artifact_id, kind, '2026-06-01'))
        self.conn.execute('INSERT INTO artifact_sources VALUES(?,?,?)', (artifact_id, artifact_id, url))
        self.conn.execute('INSERT INTO credit_rating_download_queue VALUES(?,?,?,?)', (artifact_id, url, 'www.icra.in', status))
        self.conn.execute('INSERT INTO artifact_download_queue VALUES(?,?,?,?)', (artifact_id, url, 'www.icra.in', status))
        if covered:
            self.conn.execute('INSERT INTO artifact_contents VALUES(?,?)', (artifact_id, artifact_id))
        self.conn.commit()

    def test_seed_deduplicates_queues_and_is_resumable(self):
        self.add(1)
        self.assertEqual(seed(self.conn, 'test'), 1)
        self.assertEqual(seed(self.conn, 'test'), 0)

    def test_seed_only_missing_settled_rating_reports(self):
        self.add(1)
        self.add(2, covered=True)
        self.add(3, status='PENDING')
        self.add(4, kind='recording')
        self.add(5, status='IN_PROGRESS')
        self.assertEqual(seed(self.conn, 'test'), 1)
        self.assertEqual(self.conn.execute('SELECT artifact_id FROM browser_rating_recovery_jobs').fetchone()[0], 1)

    def test_claim_is_exclusive_and_scoped_to_run(self):
        self.add(1)
        seed(self.conn, 'test')
        self.assertIsNone(claim(self.conn, 'other', [], 'worker'))
        self.assertIsNone(claim(self.conn, 'test', ['www.crisil.com'], 'worker'))
        self.assertEqual(claim(self.conn, 'test', ['www.icra.in'], 'worker')['artifact_id'], 1)
        self.assertIsNone(claim(self.conn, 'test', [], 'worker2'))

    def test_company_normalization(self):
        self.assertEqual(name_key('Example & Company Ltd'), name_key('Example and Company Limited'))
        self.assertEqual(name_key('Andrew Yule & Co. Limited'), name_key('Andrew Yule and Company Ltd'))

    def test_claim_can_target_only_selected_exceptions(self):
        self.add(1)
        self.add(2)
        seed(self.conn, 'test')
        job_id = self.conn.execute('SELECT browser_job_id FROM browser_rating_recovery_jobs WHERE artifact_id=2').fetchone()[0]
        self.assertEqual(claim(self.conn, 'test', [], 'retry', [job_id])['artifact_id'], 2)
        self.assertIsNone(claim(self.conn, 'test', [], 'retry2', [job_id]))

    def test_shutdown_releases_only_this_workers_inflight_claim(self):
        self.add(1)
        self.add(2)
        seed(self.conn, 'test')
        claim(self.conn, 'test', [], 'host-browser')
        claim(self.conn, 'test', [], 'other-worker')
        self.assertEqual(release_owned_jobs(self.conn, 'test', 'host-browser'), 1)
        states = [row[0] for row in self.conn.execute('SELECT status FROM browser_rating_recovery_jobs ORDER BY browser_job_id')]
        self.assertEqual(states, ['PENDING', 'RUNNING'])

    def test_rate_limited_host_cannot_be_claimed(self):
        self.add(1)
        seed(self.conn, 'test')
        cool_down(self.conn, 'test', 'www.icra.in', '3600')
        self.assertIsNone(claim(self.conn, 'test', ['www.icra.in'], 'worker'))
        self.assertEqual(self.conn.execute('SELECT status FROM browser_rating_recovery_jobs').fetchone()[0], 'PENDING')

    def test_retry_after_is_respected_and_fallback_increases(self):
        self.assertEqual(retry_delay('7200'), 7200)
        self.assertEqual(retry_delay(None, 1), 3600)
        self.assertEqual(retry_delay(None, 2), 7200)

    def html(self, name='Example Limited'):
        return ('<html><body><h1>Rating Rationale</h1><h2>' + name + '</h2><p>' +
                ('Credit rating of bank loan facilities reaffirmed. Stable outlook. Liquidity and key rating drivers. ' * 30) + '</p></body></html>').encode()

    def test_genuine_matching_html_is_accepted(self):
        result = validate_report(self.html(), 'text/html', 'https://www.crisil.com/report.html', 'Example Ltd')
        self.assertTrue(result['company_name_match'])

    def test_wrong_company_is_not_attached(self):
        with self.assertRaisesRegex(ValueError, 'REVIEW_REQUIRED'):
            validate_report(self.html('Different Company Limited'), 'text/html', 'https://www.crisil.com/report.html', 'Example Ltd')

    def test_confirmed_former_name_is_accepted_with_evidence(self):
        alias = {'name': 'Allsec Technologies Limited', 'basis': 'official_exchange_name_change', 'snapshot_id': 42}
        result = validate_report(self.html(alias['name']), 'text/html', 'https://www.crisil.com/report.html', 'Alldigi Tech Ltd', [alias])
        self.assertEqual(result['company_identity_match'], alias)

    def test_unverified_former_name_remains_for_review(self):
        with self.assertRaisesRegex(ValueError, 'REVIEW_REQUIRED'):
            validate_report(self.html('Allsec Technologies Limited'), 'text/html', 'https://www.crisil.com/report.html', 'Alldigi Tech Ltd')

    def test_short_alias_does_not_match_inside_unrelated_text(self):
        alias = {'name': 'HIL Limited', 'basis': 'official_exchange_name_change'}
        with self.assertRaisesRegex(ValueError, 'REVIEW_REQUIRED'):
            validate_report(self.html('Different Company while stable'), 'text/html', 'https://www.crisil.com/report.html', 'BirlaNu Ltd', [alias])

    def test_error_page_cannot_be_a_report(self):
        with self.assertRaises(ValueError):
            validate_report(b'<html>Access denied</html>', 'text/html', 'https://www.crisil.com/report.html', 'Example Ltd')

    def test_html_disguised_as_pdf_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Not PDF'):
            validate_report(self.html(), 'application/pdf', 'https://www.icra.in/report.pdf', 'Example Ltd')

    def test_missing_file_pdf_is_not_imported_or_sent_to_ocr(self):
        from reportlab.pdfgen import canvas
        output = io.BytesIO()
        pdf = canvas.Canvas(output)
        pdf.drawString(100, 700, 'NO FILE TO VIEW')
        pdf.save()
        with self.assertRaises(PublisherFileUnavailable):
            validate_report(output.getvalue(), 'application/pdf', 'https://www.icra.in/report.pdf', 'Example Ltd')

    def test_transient_errors_exclude_missing_or_wrong_company_reports(self):
        self.assertTrue(transient_error(ValueError('ERR_NAME_NOT_RESOLVED')))
        self.assertTrue(transient_error(ValueError('HTTP 503')))
        self.assertFalse(transient_error(PublisherFileUnavailable('NO FILE TO VIEW')))
        self.assertFalse(transient_error(ValueError('REVIEW_REQUIRED: wrong company')))

    def test_soft_404_is_a_missing_source_even_when_http_status_was_200(self):
        body = b'<html><h1>File not found</h1>A custom errorhandler for 404 responses</html>'
        with self.assertRaises(PublisherFileUnavailable):
            validate_report(body, 'text/html', 'https://www.crisil.com/report.html', 'Example Ltd')

    def test_direct_pdf_does_not_navigate_invalidating_pdf_viewer(self):
        browser = MagicMock()
        context = browser.new_context.return_value
        response = context.request.get.return_value
        response.ok, response.status = True, 200
        response.url = 'https://www.crisil.com/report.pdf'
        response.headers = {'content-type': 'application/pdf'}
        response.body.return_value = b'%PDF-example'
        with patch('scrapper.commands.browser_rating_recovery.validate_report', return_value={'company_name_match': True}):
            result, evidence = fetch_report(browser, response.url, 'Example Ltd')
        self.assertEqual(result.body, b'%PDF-example')
        self.assertTrue(evidence['direct_pdf_request'])
        context.new_page.return_value.goto.assert_not_called()
        context.close.assert_called_once()

    def test_direct_pdf_rate_limit_remains_a_cooldown(self):
        browser = MagicMock()
        response = browser.new_context.return_value.request.get.return_value
        response.ok, response.status = False, 429
        response.headers = {'retry-after': '7200'}
        with self.assertRaises(PublisherRateLimit) as caught:
            fetch_report(browser, 'https://www.crisil.com/report.pdf', 'Example Ltd')
        self.assertEqual(caught.exception.retry_after, '7200')


if __name__ == '__main__':
    unittest.main()
