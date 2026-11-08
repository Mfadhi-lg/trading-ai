"""Automatic queue repair, source-policy reuse, and exact canary selection."""
import unittest
from scrapper.tests import test_document_download_queue as queue_fixtures
NOW=queue_fixtures.NOW
from scrapper.db.document_download_queue_repo import materialize_download_queue,claim_download_job
from scrapper.pipeline.accepted_document_downloads import enqueue_discovered_documents

class SelectionTests(unittest.TestCase):
    setUp = queue_fixtures.DocumentDownloadQueueDA05Tests.setUp
    tearDown = queue_fixtures.DocumentDownloadQueueDA05Tests.tearDown
    register = queue_fixtures.DocumentDownloadQueueDA05Tests.register
    def test_exact_claim_leaves_other_jobs_untouched(self):
        self.register('selected',('presentation',))
        materialize_download_queue(self.conn,scope_id='selected')
        rows=[dict(r) for r in self.conn.execute('SELECT * FROM artifact_download_queue ORDER BY download_job_id')]
        job=claim_download_job(self.conn,worker='canary',now_utc=NOW,
            allowed_job_ids=(rows[-1]['download_job_id'],))
        self.assertEqual(job['download_job_id'],rows[-1]['download_job_id'])
        after=[dict(r) for r in self.conn.execute('SELECT * FROM artifact_download_queue WHERE download_job_id<>? ORDER BY download_job_id',(job['download_job_id'],))]
        self.assertEqual(after,rows[:-1])
        self.assertIsNone(claim_download_job(self.conn,worker='empty',now_utc=NOW,allowed_job_ids=()))

    def test_repair_has_no_implicit_historical_sweep(self):
        r=enqueue_discovered_documents(self.conn,epoch='2026-10-09T00:00:00+00:00',now='2026-10-11T15:00:00+00:00')
        self.assertEqual(r,[])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM artifact_download_queue').fetchone()[0],0)
