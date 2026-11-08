"""Exact retry admission never changes history, clocks, leases or backoff."""
import unittest
from pathlib import Path
from scrapper.tests import test_document_download_queue as fixtures
from scrapper.db.document_download_queue_repo import materialize_download_queue,claim_download_job
from phase4_documents_runtime import assert_retry_admissible

NOW='2026-10-12T05:00:00+00:00'
DUE='2026-10-12T04:00:00+00:00'

class RetryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DocumentDownloadQueueDA05Tests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.c=self.fixture.conn;self.fixture.register('retry03',('transcript',))
        materialize_download_queue(self.c,scope_id='retry03')
        row=self.c.execute('SELECT * FROM artifact_download_queue LIMIT 1').fetchone();self.job=row['download_job_id']
        source=self.c.execute('SELECT artifact_source_id FROM artifact_sources WHERE artifact_id=? AND document_url=?',(row['artifact_id'],row['requested_url'])).fetchone()[0]
        with self.c:
            self.c.execute("UPDATE artifact_download_queue SET status='RETRYABLE_FAILURE',attempt_count=1,next_attempt_at_utc=?,claim_token='original-fence',last_result_status='network_error' WHERE download_job_id=?",(DUE,self.job))
            self.c.execute("INSERT INTO artifact_download_attempts(download_attempt_id,artifact_id,requested_url,started_at_utc,fetched_at_utc,elapsed_ms,result_status,limits_json,error_json) VALUES (84702,?,?,?, ?,1,'network_error','{}','{}')",(row['artifact_id'],row['requested_url'],DUE,DUE))
            self.c.execute('INSERT INTO artifact_download_queue_attempts VALUES (?,84702)',(self.job,))
            self.c.execute('INSERT INTO artifact_download_attempt_sources VALUES (84702,?)',(source,))
        self.plan={'download_job_id':self.job,'artifact_id':row['artifact_id'],'url':row['requested_url'],'scope_id':'retry03',
            'retry_state':{'status':'RETRYABLE_FAILURE','attempt_count':1,'next_attempt_at_utc':DUE,'claim_token':'original-fence'},
            'previous_download_attempt_id':84702,'previous_artifact_source_id':source}

    def test_eligible_readonly_admission_then_existing_claim_increments_to_two(self):
        changes=self.c.total_changes
        self.assertEqual(assert_retry_admissible(self.c,self.plan,NOW)['attempt_count'],1)
        self.assertEqual(changes,self.c.total_changes)
        row=claim_download_job(self.c,worker='fixture',now_utc=NOW,allowed_job_ids=(self.job,))
        self.assertEqual(row['attempt_count'],2);self.assertNotEqual(row['claim_token'],'original-fence')
        self.assertEqual(self.c.execute('SELECT count(*) FROM artifact_download_queue_attempts').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT download_attempt_id FROM artifact_download_attempts').fetchone()[0],84702)

    def test_future_queue_backoff_rejected_without_writes(self):
        changes=self.c.total_changes
        with self.assertRaisesRegex(RuntimeError,'backoff has not expired'):
            assert_retry_admissible(self.c,self.plan,'2026-10-12T03:59:59+00:00')
        self.assertEqual(changes,self.c.total_changes)
        self.assertIsNone(claim_download_job(self.c,worker='fixture',now_utc='2026-10-12T03:59:59+00:00',allowed_job_ids=(self.job,)))
        self.assertEqual(self.c.execute('SELECT attempt_count FROM artifact_download_queue').fetchone()[0],1)

    def test_host_cooldown_blocks_otherwise_due_retry(self):
        host=self.c.execute('SELECT target_host FROM artifact_download_queue LIMIT 1').fetchone()[0]
        with self.c:self.c.execute("INSERT INTO artifact_download_host_health(host,blocked_until_utc,updated_at_utc) VALUES (?,?,?)",(host,'2026-10-12T06:00:00+00:00',NOW))
        changes=self.c.total_changes
        with self.assertRaisesRegex(RuntimeError,'cooldown has not expired'):assert_retry_admissible(self.c,self.plan,NOW)
        self.assertEqual(changes,self.c.total_changes)
        self.assertIsNone(claim_download_job(self.c,worker='fixture',now_utc=NOW,allowed_job_ids=(self.job,)))

    def test_shared_permit_circuit_blocks_retry(self):
        from scrapper.core.host_policy import policy_for
        group=policy_for(self.plan['url']).group
        from datetime import datetime
        with self.c:self.c.execute('INSERT INTO acquisition_host_state(publisher_group,policy_version,circuit_until) VALUES (?,\'fixture\',?)',(group,datetime.fromisoformat('2026-10-12T06:00:00+00:00').timestamp()))
        with self.assertRaisesRegex(RuntimeError,'Shared publisher backoff'):assert_retry_admissible(self.c,self.plan,NOW)

    def test_changed_attempt_state_lease_or_provenance_rejected(self):
        for sql in ("UPDATE artifact_download_queue SET attempt_count=2", "UPDATE artifact_download_queue SET lease_owner='another'",'DELETE FROM artifact_download_attempt_sources'):
            with self.subTest(sql=sql):
                self.c.execute('SAVEPOINT fixture');self.c.execute(sql)
                with self.assertRaises(RuntimeError):assert_retry_admissible(self.c,self.plan,NOW)
                self.c.execute('ROLLBACK TO fixture');self.c.execute('RELEASE fixture')
        with self.assertRaises(ValueError):assert_retry_admissible(self.c,self.plan,'2026-10-12T05:00:00')
