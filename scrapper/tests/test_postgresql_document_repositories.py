"""Policy-preserving document repositories on disposable native application sessions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from threading import Barrier
from unittest.mock import patch

from scrapper.db import document_artifact_repo as artifacts
from scrapper.db import document_download_queue_repo as queue
from scrapper.db import host_permits as permits
from scrapper.db import document_work_claims as claims
from scrapper.db.documents import processing
from scrapper.db.documents.recovery import browser_rating, attempts, targets
from scrapper.db.documents import expectations, due, quarter
from scrapper.documents.availability import RetryPolicy
from scrapper.pipeline import credit_rating_downloads as rating
from scrapper.sources.documents.discovery import build_discovery_plan
from scrapper.tests.postgresql_fixtures import DisposablePostgreSQLTestCase

NOW = '2026-11-09T10:00:00+00:00'
LATER = '2026-11-09T10:20:00+00:00'
MANIFEST = Path(__file__).parent/'fixtures/documents/d01_discovery_manifest.json'


class DocumentRepositoryIntegrationTests(DisposablePostgreSQLTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conn = self.connection()
        self.addCleanup(self.conn.close)
        self.plan = build_discovery_plan(MANIFEST,requested_symbol='LAURUSLABS')
        self.discovered = artifacts.persist_discovery_plan(self.conn,self.plan,
            observed_at_utc=NOW,snapshot_root=self.root/'snapshots')
        with self.conn:
            self.conn.execute('''INSERT INTO artifact_download_scopes
                (scope_id,scope_kind,artifact_types_json,policy_json,created_at_utc,updated_at_utc)
                VALUES ('synthetic','custom',?, '{}',?,?)''',
                (json.dumps(['presentation','transcript','credit_rating']),NOW,NOW))
            self.conn.execute('''INSERT INTO artifact_download_scope_companies
                (scope_id,company_id,symbol) VALUES ('synthetic',?,'LAURUSLABS')''',
                (self.discovered.company_id,))
            for aid in dict.fromkeys(self.discovered.artifact_ids):
                self.conn.execute("INSERT INTO artifact_download_scope_artifacts VALUES ('synthetic',?)",(aid,))

    def materialize(self):
        return queue.materialize_download_queue(self.conn,scope_id='synthetic',created_at_utc=NOW)

    def test_discovery_materialization_idempotence_and_native_rows(self):
        again = artifacts.persist_discovery_plan(self.conn,self.plan,
            observed_at_utc=NOW,snapshot_root=self.root/'snapshots')
        self.assertEqual(again.created_artifact_count,0)
        self.assertEqual(again.created_source_count,0)
        self.assertGreater(again.reused_source_count,0)
        first = self.materialize()
        second = self.materialize()
        self.assertGreater(first['created_job_count'],0)
        self.assertEqual(second['created_job_count'],0)
        self.assertFalse(self.conn.in_transaction)
        row = self.conn.execute('SELECT company_id,current_legal_name FROM companies').fetchone()
        self.assertEqual(row[0],row['company_id'])
        self.assertEqual(tuple(row),tuple(dict(row).values()))

    def test_concurrent_generic_claims_never_duplicate_or_overlap_same_host(self):
        self.materialize()
        gate = Barrier(3)
        def claim(number):
            with self.session() as conn:
                gate.wait(timeout=10)
                return queue.claim_download_job(conn,worker='worker-'+str(number),now_utc=NOW,scope_id='synthetic')
        with ThreadPoolExecutor(max_workers=3) as workers:
            jobs = list(workers.map(claim,range(3)))
        claimed = [job for job in jobs if job is not None]
        self.assertGreater(len(claimed),0)
        self.assertEqual(len({job['download_job_id'] for job in claimed}),len(claimed))
        self.assertEqual(len({job['target_host'] for job in claimed}),len(claimed))
        first = claimed[0]
        with self.conn:
            self.conn.execute("UPDATE artifact_download_queue SET lease_expires_at_utc=? WHERE download_job_id=?",(NOW,first['download_job_id']))
        replacement = queue.claim_download_job(self.conn,worker='new',now_utc=LATER,
            allowed_job_ids=(first['download_job_id'],),scope_id='synthetic')
        self.assertIsNotNone(replacement)
        with self.assertRaises(claims.OwnershipLost):
            claims.renew_queue(self.conn,'generic',first,seconds=60,now=LATER)
        self.assertNotEqual(first['claim_token'],replacement['claim_token'])

    def test_generic_scope_host_policy_cooldown_and_below_floor_are_preserved(self):
        self.materialize()
        row = self.conn.execute("SELECT * FROM artifact_download_queue WHERE status='PENDING' ORDER BY download_job_id LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        with self.conn:
            self.conn.execute('''INSERT INTO artifact_download_host_health
                (host,blocked_until_utc,last_http_status,consecutive_blocks,last_result_status,updated_at_utc)
                VALUES (?, ?,429,1,'synthetic',?) ON CONFLICT(host) DO UPDATE SET blocked_until_utc=excluded.blocked_until_utc''',
                (row['target_host'],LATER,NOW))
        self.assertIsNone(queue.claim_download_job(self.conn,worker='blocked',now_utc=NOW,
            allowed_job_ids=(row['download_job_id'],),scope_id='synthetic'))
        with self.conn:
            self.conn.execute('DELETE FROM artifact_download_host_health')
            self.conn.execute('''INSERT INTO screener_collection_scope
                (company_path,company_id,eligibility,floor_crore,observed_at_utc,evidence_json,next_check_at_utc)
                VALUES ('/company/LAURUSLABS/',?,'below_floor','580',?,'{}',?)''',
                (self.discovered.company_id,NOW,NOW))
        self.assertIsNone(queue.claim_download_job(self.conn,worker='below',now_utc=NOW,
            allowed_job_ids=(row['download_job_id'],),scope_id='synthetic'))
        with self.conn:
            self.conn.execute('DELETE FROM screener_collection_scope')
            self.conn.execute('''INSERT INTO credit_rating_host_policy
                (host,automation_status,max_concurrency,min_delay_seconds,policy_basis,reviewed_at_utc)
                VALUES (?,'BLOCKED_TERMS',0,0,'synthetic',?) ON CONFLICT(host) DO UPDATE SET
                automation_status='BLOCKED_TERMS',max_concurrency=0''',(row['target_host'],NOW))
        self.assertIsNone(queue.claim_download_job(self.conn,worker='policy',now_utc=NOW,
            allowed_job_ids=(row['download_job_id'],),scope_id='synthetic'))
        self.assertEqual(self.conn.execute('SELECT status FROM artifact_download_queue WHERE download_job_id=?',
            (row['download_job_id'],)).fetchone()[0],'PENDING')

    def test_rating_queue_full_policy_and_generation_fence(self):
        queued = rating.queue_credit_rating_downloads(self.conn,created_at_utc=NOW)
        self.assertGreater(queued['created_job_count'],0)
        self.assertEqual(rating.queue_credit_rating_downloads(self.conn,created_at_utc=NOW)['created_job_count'],0)
        row = self.conn.execute("SELECT * FROM credit_rating_download_queue WHERE status='PENDING' ORDER BY download_job_id LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        with patch.object(rating,'_iso_after',return_value=LATER):
            job = rating._claim_job(self.conn,worker='rating',now=NOW,allowed_job_ids=(row['download_job_id'],))
        self.assertIsNotNone(job)
        self.assertIsNone(rating._claim_job(self.conn,worker='other',now=NOW,allowed_job_ids=(row['download_job_id'],)))
        with self.assertRaises(RuntimeError):
            rating._finish_job(self.conn,job_id=job['download_job_id'],worker='other',
                claim_attempt_count=job['attempt_count'],claim_token=job['claim_token'],
                status='SUCCEEDED',result_status='synthetic',error=None)
        rating._finish_job(self.conn,job_id=job['download_job_id'],worker='rating',
            claim_attempt_count=job['attempt_count'],claim_token=job['claim_token'],
            status='SUCCEEDED',result_status='synthetic',error=None)
        self.assertFalse(self.conn.in_transaction)

    def test_shared_host_permit_race_across_modes_expiry_and_cooldown(self):
        with self.conn:
            self.conn.execute('DELETE FROM credit_rating_host_policy')
        gate = Barrier(3)
        def acquire(mode):
            with self.session() as conn:
                gate.wait(timeout=10)
                return permits.acquire(conn,'https://careratings.com/a',mode=mode,owner=mode,now=100,lease_seconds=5)
        with ThreadPoolExecutor(max_workers=3) as workers:
            decisions = list(workers.map(acquire,('generic','rating','screener')))
        granted = [result.permit for result in decisions if result.permit is not None]
        self.assertEqual(len(granted),1)
        self.assertFalse(self.conn.in_transaction)
        newer = permits.acquire(self.conn,'www.careratings.com',mode='rating',owner='new',now=106).permit
        self.assertIsNotNone(newer)
        with self.assertRaises(permits.PermitLost):
            permits.finish(self.conn,granted[0],now=107)
        permits.finish(self.conn,newer,now=107,outcome='blocked',retry_after='120')
        self.assertEqual(permits.acquire(self.conn,'careratings.com',mode='generic',owner='later',now=110).retry_at,227)
        self.assertFalse(self.conn.in_transaction)

    def test_due_expectation_claim_and_processing_selector_native_sql(self):
        event = expectations.register_artifact_event(self.conn,company_id=self.discovered.company_id,
            event_key='synthetic-due',event_type='results',period_end='2026-09-30',
            evidenced_at_utc=NOW,evidence_source='synthetic',evidence={})
        eid = expectations.ensure_artifact_expectation(self.conn,event_id=event,artifact_type='presentation',
            expectation_basis='synthetic',expected_after_utc=NOW,created_at_utc=NOW,policy=RetryPolicy())
        all_due = due.claim_due_expectations(self.conn,now_utc='2026-11-09T13:00:00+00:00',limit=100,worker_id='first')
        self.assertIn(eid,{item.expectation_id for item in all_due})
        self.assertEqual(due.claim_due_expectations(self.conn,now_utc='2026-11-09T13:00:00+00:00',limit=100,worker_id='second'),())
        due.release_claims(self.conn,expectations=all_due,released_at_utc=LATER)
        version = processing.ProcessingVersion.create(parser_name='synthetic',parser_version='1',
            ocr_engine='none',ocr_engine_version='1',options={})
        self.assertEqual(processing.automatic_processing_contents(self.conn,version=version,
            now=NOW,epoch=NOW,limit=2),[])
        list(quarter._artifact_sources(self.conn))
        list(quarter._artifact_contents(self.conn))
        list(quarter._latest_accepted_periods(self.conn))
        list(quarter._result_filing_coverage(self.conn,'2026-09-30'))

    def test_browser_seed_run_scope_cooldown_and_stale_generation(self):
        rating.queue_credit_rating_downloads(self.conn,created_at_utc=NOW)
        with self.conn:
            self.conn.execute("UPDATE credit_rating_download_queue SET status='SKIPPED_POLICY'")
        hosts = tuple(row[0] for row in self.conn.execute('SELECT DISTINCT target_host FROM credit_rating_download_queue'))
        seeded = browser_rating.seed(self.conn,'synthetic-browser',agency_hosts=hosts,
            settled=('SKIPPED_POLICY',),utc_now=lambda: NOW)
        self.assertGreater(seeded,0)
        self.assertEqual(browser_rating.seed(self.conn,'synthetic-browser',agency_hosts=hosts,
            settled=('SKIPPED_POLICY',),utc_now=lambda: NOW),0)
        self.assertIsNone(browser_rating.claim(self.conn,'different-run',hosts,'worker',utc_now=lambda: NOW))
        first = browser_rating.claim(self.conn,'synthetic-browser',hosts,'worker',utc_now=lambda: NOW)
        with self.conn:
            self.conn.execute('UPDATE browser_rating_recovery_jobs SET lease_expires_at_utc=? WHERE browser_job_id=?',
                (NOW,first['browser_job_id']))
        replacement = browser_rating.claim(self.conn,'synthetic-browser',hosts,'worker',
            (first['browser_job_id'],),utc_now=lambda: LATER)
        self.assertNotEqual(first['claim_token'],replacement['claim_token'])
        with self.assertRaises(RuntimeError):
            browser_rating.finish(self.conn,first,'FAILED',error='stale',utc_now=lambda: LATER)
        browser_rating.finish(self.conn,replacement,'FAILED',error='synthetic',utc_now=lambda: LATER)
        self.assertFalse(self.conn.in_transaction)

    def test_download_bytes_contents_and_failed_media_normalization_are_durable(self):
        from scrapper.db.documents.download import persist_download_result
        from scrapper.documents.downloader import DownloadResult, DownloadPolicy
        from scrapper.db import recording_repo, machine_transcript_repo
        source = self.conn.execute("""SELECT source.* FROM artifact_sources source
            JOIN document_artifacts artifact USING(artifact_id)
            WHERE artifact.artifact_type='recording' ORDER BY source.artifact_source_id LIMIT 1""").fetchone()
        body = b'ID3-synthetic-original-audio'
        downloaded = persist_download_result(self.conn,artifact_id=source['artifact_id'],
            artifact_source_ids=(source['artifact_source_id'],),
            result=DownloadResult(source['document_url'],source['document_url'],200,{},(),
                'audio/mpeg','audio/mpeg',body,len(body),'validated',1),
            policy=DownloadPolicy(),started_at_utc=NOW,fetched_at_utc=NOW,
            snapshot_root=self.root/'audio')
        content = self.conn.execute('SELECT * FROM artifact_contents WHERE content_id=?',
            (downloaded.content_id,)).fetchone()
        blob = self.conn.execute('SELECT * FROM response_blobs WHERE blob_id=?',(content['blob_id'],)).fetchone()
        self.assertEqual(Path(blob['local_path']).read_bytes(),body)
        failed = recording_repo.persist_recording_normalization(self.conn,artifact_id=source['artifact_id'],
            source_content_id=downloaded.content_id,source_probe=None,normalized_probe=None,
            normalized_body=None,validations=(),probe_engine='synthetic',probe_engine_version='1',
            normalizer_engine='synthetic',normalizer_engine_version='1',options={},
            started_at_utc=NOW,completed_at_utc=NOW,error={'reason':'synthetic unsupported'},
            snapshot_root=self.root/'normalized')
        self.assertEqual(failed.normalization_status,'failed')
        from scrapper.documents.media import MediaProbe, MediaStream, MediaValidation
        probe = MediaProbe(('wav',),1000,(MediaStream(0,'audio','pcm_s16le',1000,16000,1,'mono'),))
        accepted = recording_repo.persist_recording_normalization(self.conn,artifact_id=source['artifact_id'],
            source_content_id=downloaded.content_id,source_probe=probe,normalized_probe=probe,
            normalized_body=b'RIFF-synthetic-normalized-audio',
            validations=(MediaValidation('synthetic-probe',True,'fixture','fixture',{}),),
            probe_engine='synthetic',probe_engine_version='1',normalizer_engine='synthetic',
            normalizer_engine_version='1',options={'fixture':'accepted'},started_at_utc=NOW,
            completed_at_utc=NOW,error={},snapshot_root=self.root/'normalized')
        media = self.conn.execute('SELECT * FROM artifact_contents WHERE content_id=?',
            (accepted.normalized_content_id,)).fetchone()
        with self.conn:
            transcript = self.conn.execute("""INSERT INTO document_artifacts
                (company_id,artifact_key,artifact_type,title_raw,event_date,period_end,
                 fiscal_year,fiscal_quarter,first_observed_at_utc)
                SELECT company_id,'synthetic-machine','transcript','Synthetic',event_date,period_end,
                  fiscal_year,fiscal_quarter,first_observed_at_utc FROM document_artifacts
                WHERE artifact_id=? RETURNING artifact_id""",(source['artifact_id'],)).fetchone()[0]
        run = machine_transcript_repo.persist_machine_transcription_failure(self.conn,
            transcript_artifact_id=transcript,recording_artifact_id=source['artifact_id'],
            normalization_id=accepted.normalization_id,source_media_content_id=accepted.normalized_content_id,
            source_media_sha256=media['content_sha256'],source_media_duration_ms=1000,
            source_recording_content_id=downloaded.content_id,source_recording_sha256=content['content_sha256'],
            engine='synthetic',engine_version='1',model_version='1',options={},comparison_requested=False,
            started_at_utc=NOW,completed_at_utc=NOW,error={'reason':'synthetic unsupported'})
        self.assertGreater(run,0)
        self.assertFalse(self.conn.in_transaction)

    def test_recovery_target_claim_and_completion_preserve_source_history(self):
        from scrapper.documents.recovery import RECOVERY_CONTRACT_VERSION
        with self.conn:
            source = self.conn.execute("""SELECT source.*,artifact.company_id FROM artifact_sources source
                JOIN document_artifacts artifact USING(artifact_id)
                WHERE artifact.artifact_type='credit_rating' ORDER BY source.artifact_source_id LIMIT 1""").fetchone()
            self.conn.execute("""INSERT INTO artifact_recovery_targets
                (artifact_id,company_id,artifact_type,document_originator,blocked_source_id,
                 blocked_source_url,blocked_source_host,company_canonical_name,contract_version,status,
                 created_at_utc,updated_at_utc)
                VALUES (?,?,'credit_rating','ICRA',?,?,'www.icra.in','Synthetic',?,'PENDING',?,?)""",
                (source['artifact_id'],source['company_id'],source['artifact_source_id'],source['document_url'],RECOVERY_CONTRACT_VERSION,NOW,NOW))
        claimed = attempts.claim_recovery_target(self.conn,source='nse',worker='recovery',now_utc=NOW)
        self.assertIsNotNone(claimed)
        target, attempt_id = claimed
        self.assertIsNone(attempts.claim_recovery_target(self.conn,source='nse',worker='other',now_utc=NOW))
        attempts.finish_recovery_attempt(self.conn,recovery_attempt_id=attempt_id,worker='recovery',
            status='NO_OFFICIAL_SOURCE_FOUND',completed_at_utc=NOW,result_evidence={'synthetic':True})
        self.assertIsNone(attempts.claim_recovery_target(self.conn,source='nse',worker='again',now_utc=LATER))
        self.assertIsNotNone(attempts.claim_recovery_target(self.conn,source='bse',worker='next',now_utc=LATER))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM artifact_recovery_attempts').fetchone()[0],2)
        self.assertFalse(self.conn.in_transaction)

    def test_transferred_offset_queue_lease_renews_without_shortening(self):
        self.materialize()
        job = queue.claim_download_job(self.conn, worker='offset-owner', now_utc=NOW,
                                       scope_id='synthetic')
        self.assertIsNotNone(job)
        original_deadline = '2026-11-09T05:30:00-05:00'  # 10:30 UTC
        with self.conn:
            self.conn.execute('UPDATE artifact_download_queue SET lease_expires_at_utc=? WHERE download_job_id=?',
                              (original_deadline, job['download_job_id']))
        claims.renew_queue(self.conn, 'generic', job, seconds=60,
                           now='2026-11-09T10:01:00+00:00')
        persisted = self.conn.execute('SELECT lease_expires_at_utc FROM artifact_download_queue WHERE download_job_id=?',
                                      (job['download_job_id'],)).fetchone()[0]
        self.assertEqual(persisted, original_deadline)
        claims.renew_queue(self.conn, 'generic', job, seconds=3600,
                           now='2026-11-09T10:02:00+00:00')
        self.assertEqual(self.conn.execute('SELECT lease_expires_at_utc FROM artifact_download_queue WHERE download_job_id=?',
                                          (job['download_job_id'],)).fetchone()[0],
                         '2026-11-09T11:02:00+00:00')
        self.assertFalse(self.conn.in_transaction)
