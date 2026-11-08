"""Exercise landing-page recovery through the actual production SQL guard."""
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scrapper.tests import test_document_download_recovery as fixtures
from scrapper.workflows.documents.download_reconciliation import reconcile_download_outcome


class RoutineRecoveryAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DocumentDownloadRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.conn = self.fixture.conn
        self.job = dict(self.fixture._terminal_job())
        with patch.object(sys, 'path', list(sys.path)):
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ops/activation'))
            from phase4_routine_runtime import routine_authorizer
        self.conn.set_authorizer(routine_authorizer)
        self.addCleanup(self.conn.set_authorizer, None)

    def test_landing_pdf_replacement_preserves_source_and_appends_audit(self):
        page = 'https://issuer.example/investors'
        replacement = 'https://issuer.example/investor-presentation.pdf'
        self.job['artifact_type'] = 'presentation'
        source_id = self.job['artifact_source_id']
        old_url = self.conn.execute('SELECT document_url FROM artifact_sources WHERE artifact_source_id=?', (source_id,)).fetchone()[0]
        before = self.conn.execute('SELECT count(*) FROM document_artifacts').fetchone()[0]
        reconcile_download_outcome(self.conn, job=self.job,
            persisted=SimpleNamespace(artifact_source_ids=(source_id,), download_attempt_id=19),
            result=SimpleNamespace(http_status=200, detected_media_type='text/html',
                body=b'<main><a href="/investor-presentation.pdf">Investor presentation</a></main>', final_url=page),
            status='RETRYABLE_FAILURE', now=lambda: fixtures.NOW)
        rows = self.conn.execute('SELECT * FROM document_download_recovery_actions').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['old_url'], rows[0]['new_url']), (old_url, replacement))
        self.assertEqual(json.loads(rows[0]['evidence_json']), {'landing_url': page, 'download_attempt_id': 19})
        self.assertEqual(self.conn.execute('SELECT document_url FROM artifact_sources WHERE artifact_source_id=?', (source_id,)).fetchone()[0], old_url)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_artifacts').fetchone()[0], before)
        self.assertEqual(self.conn.execute('SELECT status FROM artifact_download_queue WHERE requested_url=?', (replacement,)).fetchone()[0], 'PENDING')
        for sql in ('UPDATE document_download_recovery_actions SET reason_detail=reason_detail',
                    'DELETE FROM document_download_recovery_actions',
                    'UPDATE accepted_page_versions SET accepted_by=accepted_by',
                    'CREATE TABLE unauthorized(value)'):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                self.conn.execute(sql)
