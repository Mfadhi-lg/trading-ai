"""C19.2: stale claims fail closed even when process owner names are reused."""
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone
from scrapper.db.document_download_queue_repo import claim_download_job, finish_download_job, materialize_download_queue
from scrapper.db.documents.recovery import browser_rating as browser, browser_rating_content
from scrapper.pipeline import credit_rating_downloads as rating
from scrapper.tests import test_document_download_queue as fixtures

NOW = fixtures.NOW
LATER = '2026-08-14T10:00:02+00:00'

class ClaimFencingTests(unittest.TestCase):
    def generic(self):
        fixture = fixtures.DocumentDownloadQueueDA05Tests(); fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.register('fence', ('presentation',))
        materialize_download_queue(fixture.conn, scope_id='fence', created_at_utc=NOW)
        with fixture.conn:
            fixture.conn.execute("UPDATE artifact_download_queue SET status='SKIPPED_POLICY' WHERE download_job_id != (SELECT min(download_job_id) FROM artifact_download_queue)")
        return fixture

    def finish_generic(self, conn, job, **override):
        args = dict(job_id=job['download_job_id'],worker=job['lease_owner'],claim_attempt_count=job['attempt_count'],claim_token=job['claim_token'],status='SUCCEEDED',result_status='OK',error=None,now_utc=LATER)
        args.update(override); finish_download_job(conn, **args)

    def test_generic_same_owner_expiry_reclaim_rejects_old_generation(self):
        f=self.generic(); other=sqlite3.connect(f.db_path);other.row_factory=sqlite3.Row;self.addCleanup(other.close)
        first=claim_download_job(f.conn,worker='same',now_utc=NOW,scope_id='fence',lease_seconds=1)
        second=claim_download_job(other,worker='same',now_utc=LATER,scope_id='fence')
        self.assertEqual(second['attempt_count'],first['attempt_count']+1)
        self.assertFalse(f.conn.in_transaction);self.assertFalse(other.in_transaction)
        before=[tuple(r) for r in other.execute('SELECT * FROM artifact_download_queue')]
        with self.assertRaises(RuntimeError):self.finish_generic(f.conn,first)
        self.assertEqual(before,[tuple(r) for r in other.execute('SELECT * FROM artifact_download_queue')])
        self.finish_generic(other,second)
        self.assertEqual(other.execute('SELECT status FROM artifact_download_queue WHERE download_job_id=?',(first['download_job_id'],)).fetchone()[0],'SUCCEEDED')
        self.assertEqual(other.execute('SELECT count(*) FROM artifact_download_attempts').fetchone()[0],0)

    def test_generic_reset_attempt_count_cannot_reuse_old_claim(self):
        f=self.generic();first=claim_download_job(f.conn,worker='same',now_utc=NOW,scope_id='fence')
        with f.conn:
            f.conn.execute("UPDATE artifact_download_queue SET status='PENDING',attempt_count=0,lease_owner=NULL,lease_expires_at_utc=NULL WHERE download_job_id=?",(first['download_job_id'],))
        newer=claim_download_job(f.conn,worker='same',now_utc=NOW,scope_id='fence')
        self.assertEqual(first['attempt_count'],newer['attempt_count'])
        self.assertEqual(first['lease_expires_at_utc'],newer['lease_expires_at_utc'])
        self.assertNotEqual(first['claim_token'],newer['claim_token'])
        with self.assertRaises(RuntimeError):self.finish_generic(f.conn,first)
        self.finish_generic(f.conn,newer)

    def test_generic_wrong_owner_duplicate_completion_and_missing_generation(self):
        f=self.generic();job=claim_download_job(f.conn,worker='owner',now_utc=NOW,scope_id='fence')
        with self.assertRaises(RuntimeError):self.finish_generic(f.conn,job,worker='other')
        args=dict(job_id=job['download_job_id'],worker='owner',status='SUCCEEDED',result_status='OK',error=None,now_utc=NOW)
        with self.assertRaises(TypeError):finish_download_job(f.conn,**args)
        self.finish_generic(f.conn,job)
        with self.assertRaises(RuntimeError):self.finish_generic(f.conn,job)

    def direct(self):
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row;self.addCleanup(conn.close)
        conn.executescript('''CREATE TABLE credit_rating_download_queue(download_job_id INTEGER PRIMARY KEY,artifact_id INTEGER,target_host TEXT,status TEXT,attempt_count INTEGER,lease_owner TEXT,lease_expires_at_utc TEXT,next_attempt_at_utc TEXT,updated_at_utc TEXT,last_result_status TEXT,last_error TEXT,priority INTEGER,claim_token TEXT);
        CREATE TABLE screener_collection_scope(company_id INTEGER,eligibility TEXT);
        CREATE TABLE document_artifacts(artifact_id INTEGER,company_id INTEGER);
        CREATE TABLE credit_rating_host_policy(host TEXT,automation_status TEXT,max_concurrency INTEGER);
        CREATE TABLE credit_rating_host_health(host TEXT,blocked_until_utc TEXT);
        INSERT INTO credit_rating_download_queue VALUES(1,1,'example.test','PENDING',0,NULL,NULL,NULL,NULL,NULL,NULL,1,NULL);''')
        return conn

    def finish_direct(self, conn, job, **override):
        args=dict(job_id=1,worker=job['lease_owner'],claim_attempt_count=job['attempt_count'],claim_token=job['claim_token'],status='SUCCEEDED',result_status='OK',error=None)
        args.update(override);rating._finish_job(conn,**args)

    def test_direct_same_owner_reclaim_wrong_owner_and_duplicate_finish(self):
        conn=self.direct()
        with patch.object(rating,'_iso_after',return_value='2026-08-14T10:00:01+00:00'):
            first=rating._claim_job(conn,worker='same',now=NOW)
        second=rating._claim_job(conn,worker='same',now=LATER)
        self.assertEqual(first['attempt_count'],1);self.assertEqual(second['attempt_count'],2)
        before=tuple(conn.execute('SELECT * FROM credit_rating_download_queue').fetchone())
        for job,override in [(first,{}),(second,{'worker':'other'})]:
            with self.assertRaises(RuntimeError):self.finish_direct(conn,job,**override)
            self.assertEqual(before,tuple(conn.execute('SELECT * FROM credit_rating_download_queue').fetchone()))
        self.assertFalse(conn.in_transaction);self.finish_direct(conn,second)
        with self.assertRaises(RuntimeError):self.finish_direct(conn,second)

    def browser(self):
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row;self.addCleanup(conn.close)
        root=Path(browser.__file__).parents[2]/'migrations'
        for n in ['065_operator_browser_rating_recovery.sql','066_browser_rating_recovery_backoff.sql']:
            conn.executescript((root/n).read_text())
        conn.execute('ALTER TABLE browser_rating_recovery_jobs ADD COLUMN claim_token TEXT')
        with conn:
            conn.execute("INSERT INTO browser_rating_recovery_jobs(run_id,artifact_id,artifact_source_id,requested_url,target_host,company_name,created_at_utc,updated_at_utc) VALUES('run',1,1,'https://example.test/a','example.test','Example',?,?)",(NOW,NOW))
        return conn

    def browser_pair(self):
        conn=self.browser();first=browser.claim(conn,'run',[],'same',utc_now=lambda:NOW)
        with conn:conn.execute("UPDATE browser_rating_recovery_jobs SET lease_expires_at_utc=?",(NOW,))
        second=browser.claim(conn,'run',[],'same',utc_now=lambda:LATER)
        return conn,first,second

    def test_browser_preincrement_retry_contract_and_stale_finish(self):
        conn,first,second=self.browser_pair()
        self.assertEqual(first['attempt_count'],0);self.assertEqual(second['attempt_count'],1)
        self.assertEqual(first['lease_owner'],'same')
        before=tuple(conn.execute('SELECT * FROM browser_rating_recovery_jobs').fetchone())
        with self.assertRaises(RuntimeError):browser.finish(conn,first,'FAILED',error='stale')
        self.assertEqual(before,tuple(conn.execute('SELECT * FROM browser_rating_recovery_jobs').fetchone()))
        browser.finish(conn,second,'SUCCEEDED')
        with self.assertRaises(RuntimeError):browser.finish(conn,second,'FAILED')

    def test_browser_session_release_does_not_release_new_incarnation(self):
        conn=self.browser();job=browser.claim(conn,'run',[],'new-session',utc_now=lambda:NOW)
        self.assertEqual(browser.release_owned_jobs(conn,'run','old-session'),0)
        self.assertEqual(browser.release_owned_jobs(conn,'other','new-session'),0)
        self.assertEqual(browser.release_owned_jobs(conn,'run','new-session'),1)
        with self.assertRaises(RuntimeError):browser.finish(conn,job,'FAILED')

    def test_browser_stale_import_cannot_reconcile_any_queue(self):
        conn,first,second=self.browser_pair()
        # A stale snapshot is rejected before any artifact/other-queue SQL;
        # those tables deliberately do not exist in this boundary fixture.
        with self.assertRaisesRegex(RuntimeError,'Browser import lease'):
            browser_rating_content.reconcile_download(conn,1,LATER,job=first)
        self.assertFalse(conn.in_transaction)
        self.assertEqual(conn.execute('SELECT lease_owner,attempt_count,status FROM browser_rating_recovery_jobs').fetchone()[:],('same',2,'RUNNING'))

    def test_bridge_reclaim_rejects_old_token_and_ambiguous_legacy_finish(self):
        import json
        from types import SimpleNamespace
        from unittest.mock import Mock
        from scrapper.documents.recovery.browser_rating import bridge
        conn=self.browser()
        with conn:
            conn.execute("UPDATE browser_rating_recovery_jobs SET target_host='www.icra.in'")
        conn.executescript((Path(browser.__file__).parents[2]/'migrations/067_browser_rating_workers.sql').read_text())
        conn.execute('CREATE TABLE artifact_contents(content_id INTEGER,artifact_id INTEGER)')
        requests=[{'action':'claim'}, {'action':'finish','browser_job_id':1,'status':'PENDING','claim_token':'first'},
                  {'action':'claim'}, {'action':'finish','browser_job_id':1,'status':'FAILED','claim_token':'first'},
                  {'action':'finish','browser_job_id':1,'status':'FAILED'},
                  {'action':'finish','browser_job_id':1,'status':'FAILED','claim_token':'second'}]
        replies=[]
        with patch.object(bridge.secrets,'token_hex',side_effect=['session','first','second']):
            bridge.serve(conn,SimpleNamespace(run_id='run'),lines=[json.dumps(r) for r in requests],emit=replies.append,
                worker_state=browser.worker_state,release_owned_jobs=browser.release_owned_jobs,
                claim=lambda c,r,h,o,j:browser.claim(c,r,h,o,j,utc_now=lambda:NOW),
                cool_down=Mock(),finish=browser.finish,validate_report=Mock(),company_names=Mock(),import_report=Mock(),
                DownloadPolicy=Mock(),DownloadResult=Mock(),utc_now=lambda:NOW)
        self.assertIn('error',replies[3]);self.assertIn('error',replies[4]);self.assertEqual(replies[5]['status'],'FAILED')
        self.assertEqual(conn.execute('SELECT status,attempt_count FROM browser_rating_recovery_jobs').fetchone()[:],('FAILED',2))

    def test_browser_retry_remains_four_claims_until_accounting_chunk(self):
        conn=self.browser();seen=[]
        for i in range(4):
            job=browser.claim(conn,'run',[],'session',utc_now=lambda:NOW)
            seen.append(job['attempt_count'])
            browser.finish(conn,job,'PENDING' if job['attempt_count']<3 else 'FAILED')
        self.assertEqual(seen,[0,1,2,3]);self.assertIsNone(browser.claim(conn,'run',[],'session',utc_now=lambda:NOW))

if __name__=='__main__':unittest.main()
