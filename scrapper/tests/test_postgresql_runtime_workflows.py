"""Public application workflows on disposable PostgreSQL, with isolated I/O."""
from contextlib import contextmanager
from datetime import datetime, timezone
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

from scrapper.db import document_artifact_repo as artifacts
from scrapper.db import document_download_queue_repo as downloads
from scrapper.db import document_work_claims as claims
from scrapper.db.documents import processing
from scrapper.operations.workers.document_processing import run_processing
from scrapper.operations.workers.renewal import LeaseRenewal
from scrapper.operations.workers import automatic_documents
from scrapper.operations.coordinator import exchange as exchange_coordinator
from scrapper.db.exchange import tasks as exchange_tasks
from scrapper.pipeline import extract_document as extraction
from scrapper.tests import test_d03_document_extraction as document_fixtures
from scrapper.tests import test_screener_vertical as screener_fixtures
from scrapper.tests.postgresql_fixtures import DisposablePostgreSQLTestCase
from scrapper.workflows.screener import ingest


class RuntimeWorkflowTests(DisposablePostgreSQLTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime.now(timezone.utc).isoformat()
        self.connections = []

    def factory(self, *_args, **_kwargs):
        conn = self.connection()
        self.connections.append(conn)
        # Runtime login/reopen checks are covered independently. This fixture
        # supplies only a new thread-owned connection to the same UUID schema.
        conn.reopen = lambda **_options: self.factory()
        return conn

    def test_public_financial_ingest_validates_accepts_and_exports_exact_source(self):
        result = ingest.ingest_screener_response(
            'LAURUSLABS', 'standalone', screener_fixtures.fixture_standalone_response(),
            screener_fixtures.fixture_standalone_peer_response(),
            db_path=self.root/'logical.db', snapshot_root=self.root/'snapshots',
            export_root=self.root/'exports', fetched_at_utc=screener_fixtures.FIRST_FETCH,
            connect=self.factory,
        )
        payload = json.loads(result.export_path.read_text())
        self.assertIsInstance(payload, dict)
        with self.session() as conn:
            accepted = conn.execute('SELECT parse_id FROM accepted_page_versions WHERE company_id=?',
                                    (result.company_id,)).fetchone()
            self.assertEqual(accepted['parse_id'], result.parse_id)
            self.assertGreater(conn.execute('SELECT count(*) FROM financial_observations WHERE parse_id=?',
                                            (result.parse_id,)).fetchone()[0], 0)
            rows = conn.execute('SELECT local_path,content_sha256 FROM response_blobs').fetchall()
            self.assertGreaterEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(hashlib.sha256(Path(row['local_path']).read_bytes()).hexdigest(),
                                 row['content_sha256'])
            self.assertEqual(conn.execute('SELECT status FROM scrape_runs WHERE run_id=?',
                                          (result.run_id,)).fetchone()[0], 'ok')
        self.assertTrue(all(conn.closed for conn in self.connections))

    def content(self):
        body = (document_fixtures.FIXTURES/'d03_text.pdf').read_bytes()
        destination = self.root/'source.pdf'
        destination.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        with self.session() as conn, conn:
            company = conn.execute("INSERT INTO companies(current_legal_name) VALUES ('Synthetic') RETURNING company_id").fetchone()[0]
            artifact = conn.execute("""INSERT INTO document_artifacts(company_id,artifact_key,
                artifact_type,title_raw,first_observed_at_utc,acquisition_status)
                VALUES (?,'synthetic','presentation','Synthetic',?,'downloaded') RETURNING artifact_id""",
                (company,self.now)).fetchone()[0]
            blob = conn.execute("""INSERT INTO response_blobs(content_sha256,content_type,
                byte_size,local_path,first_observed_at_utc,created_at_utc)
                VALUES (?,'application/pdf',?,?,?,?) RETURNING blob_id""",
                (digest,len(body),str(destination),self.now,self.now)).fetchone()[0]
            content = artifacts.register_artifact_content(conn,artifact_id=artifact,
                blob_id=blob,media_type='application/pdf',is_official=True,created_at_utc=self.now)
        return artifact, content

    def test_document_worker_commits_claim_before_parser_and_records_cache(self):
        _, content = self.content()
        engine = document_fixtures.FakeOcr()
        version = extraction.processing_version(engine)
        with self.session() as conn:
            work = processing.reconcile_processing(conn,content_ids=[content],version=version,now=self.now)[0]
        original = extraction.extract_pdf
        checks = []
        def parser(*args, **kwargs):
            # All worker-owned connections are outside transactions while the
            # original deterministic parser/OCR work executes.
            checks.append(True)
            self.assertTrue(all(not conn.in_transaction for conn in self.connections if not conn.closed))
            with self.session() as observer:
                self.assertEqual(observer.execute('SELECT status FROM document_extraction_claims').fetchone()[0], 'ACTIVE')
            return original(*args, **kwargs)
        with patch.object(extraction, 'extract_pdf', side_effect=parser):
            first = run_processing(db_path=self.root/'logical.db',work_ids=[work['work_id']],
                ocr_engine=engine,output_dir=self.root/'output',now_utc=lambda:self.now,
                connection_factory=self.factory)
            second = run_processing(db_path=self.root/'logical.db',work_ids=[work['work_id']],
                ocr_engine=engine,output_dir=self.root/'output',now_utc=lambda:self.now,
                connection_factory=self.factory)
        self.assertEqual(len(checks),1)
        self.assertEqual(first,second)
        self.assertEqual(first[0]['extraction_status'],'ok')
        with self.session() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT status FROM document_extraction_claims').fetchone()[0],'RELEASED')
            self.assertEqual(conn.execute('SELECT count(*) FROM document_pages').fetchone()[0],2)
        self.assertTrue(all(conn.closed for conn in self.connections))

    def test_renewal_uses_independent_connection_and_commits_before_next_tick(self):
        conn = self.factory()
        self.addCleanup(conn.close)
        claim = claims.claim_extraction(conn,'synthetic-renewal',seconds=60,now=self.now)
        main_thread = threading.get_ident()
        observed = threading.Event()
        threads = []
        def renew(other):
            self.assertIsNot(other,conn)
            self.assertNotEqual(threading.get_ident(),main_thread)
            claims.renew_extraction(other,claim,seconds=120,now=self.now)
            self.assertFalse(other.in_transaction)
            threads.append(threading.get_ident())
            observed.set()
        with LeaseRenewal(conn,renew,seconds=0.03) as renewal:
            self.assertTrue(observed.wait(5),'renewal thread did not perform one bounded renewal')
            renewal.check()
        self.assertTrue(threads)
        self.assertFalse(conn.in_transaction)
        self.assertTrue(all(other.closed for other in self.connections if other is not conn))

    def test_download_preview_is_read_only_and_does_not_take_write_transaction(self):
        with self.session() as writer, writer:
            writer.execute("""INSERT INTO artifact_download_scopes
                (scope_id,scope_kind,artifact_types_json,policy_json,created_at_utc,updated_at_utc)
                VALUES ('synthetic-preview','custom','["presentation"]','{}',?,?)""",
                (self.now,self.now))
        with self.session(read_only=True) as conn:
            report = downloads.preview_download_queue(conn,scope_id='synthetic-preview')
            self.assertIsInstance(report,dict)
            self.assertFalse(conn.in_transaction)

    def test_browser_ownership_checkpoint_compares_offset_instants_without_connection(self):
        from scrapper.db.browser_host_defer import assert_unexpired
        job = {'lease_expires_at_utc':'2026-11-09T06:00:00+05:30'}
        assert_unexpired(job,'2026-11-09T00:20:00+00:00')
        with self.assertRaises(claims.OwnershipLost):
            assert_unexpired(job,'2026-11-09T00:40:00+00:00')

    def test_exchange_dispatch_commits_claim_before_transport_and_holds_executor_ownership(self):
        with self.session() as conn:
            task = exchange_tasks.enqueue(conn,kind='attempt',scheduled_for='2026-11-09',
                attempt=1,priority=0,now=self.now)
        checks = []
        def perform(**kwargs):
            checks.append(kwargs['attempt'])
            self.assertTrue(all(not conn.in_transaction for conn in self.connections if not conn.closed))
            with self.session() as observer:
                claimed = observer.execute('SELECT state,generation FROM exchange_coordinator_tasks WHERE task_id=?',
                                           (task['task_id'],)).fetchone()
                self.assertEqual(tuple(claimed),('RUNNING',1))
            # Session advisory ownership, rather than just an expiring task
            # lease, excludes an overlapping executor across source work.
            with exchange_coordinator.ownership(self.root/'logical.db','executor') as acquired:
                self.assertFalse(acquired)
            return {'status':'ok','exact':'000123.000000000000000001'}
        workflow = SimpleNamespace(run_attempt=perform,alert=lambda *_: self.fail('unexpected dispatch alert'))
        with patch.object(exchange_coordinator,'connect',side_effect=self.factory), \
             patch.object(exchange_coordinator,'selected_backend',return_value='postgresql'):
            result = exchange_coordinator.run_pending(db_path=self.root/'logical.db',
                snapshot_root=self.root/'snapshots',now_utc=lambda:self.now,
                workflow=workflow,limit=1,fetcher=lambda *_: self.fail('unexpected network'))
        self.assertEqual(result,{'processed':1,'pending':0,'status':'ok'})
        self.assertEqual(checks,[1])
        with self.session() as conn:
            completed = conn.execute('SELECT state,result_json FROM exchange_coordinator_tasks WHERE task_id=?',
                                     (task['task_id'],)).fetchone()
            self.assertEqual(completed['state'],'DONE')
            self.assertEqual(json.loads(completed['result_json'])['exact'],'000123.000000000000000001')

    def test_automatic_discovery_selects_due_company_event_peers_and_boolean_long_tail(self):
        from scrapper.db.documents import due
        from scrapper.documents.priority import current_period
        now = datetime.fromisoformat(self.now)
        old = (now-timedelta(days=100)).isoformat()
        epoch = (now-timedelta(days=1)).isoformat()
        future = (now+timedelta(days=1)).isoformat()
        policy = json.dumps({'offsets_hours':[2,6], 'unknown_retry_limit':3,
                             'claim_lease_seconds':900,'long_tail_v1':True})
        expected = []
        with self.session() as conn, conn:
            company = conn.execute("INSERT INTO companies(current_legal_name) VALUES ('Synthetic selection') RETURNING company_id").fetchone()[0]
            conn.execute("""INSERT INTO screener_collection_scope
                (company_path,company_id,eligibility,market_cap_crore,floor_crore,
                 observed_at_utc,evidence_json,next_check_at_utc)
                VALUES ('/company/SYNTHETIC/',?,'eligible','1000','500',?,'{}',?)""",
                (company,self.now,future))
            for group in range(3):
                event = conn.execute("""INSERT INTO artifact_events(company_id,event_key,event_type,
                    period_end,evidenced_at_utc,evidence_source) VALUES (?,?,'results',?,?,'synthetic') RETURNING event_id""",
                    (company,'synthetic-'+str(group),current_period(self.now),self.now)).fetchone()[0]
                for kind in ('presentation','transcript'):
                    deadline = future if group==1 and kind=='transcript' else self.now
                    lease = future if group==2 else None
                    token = 'fixture-'+str(group)+'-'+kind if lease else None
                    item = conn.execute("""INSERT INTO artifact_expectations(company_id,artifact_type,
                        event_id,expectation_basis,expected_after_utc,next_check_at_utc,schedule_json,
                        claim_token,claimed_at_utc,claim_expires_at_utc,created_at_utc,updated_at_utc)
                        VALUES (?,?,?,'synthetic',?,?,?,?,?,?,?,?) RETURNING expectation_id""",
                        (company,kind,event,old,deadline,policy,token,self.now if lease else None,lease,old,self.now)).fetchone()[0]
                    if not lease and deadline!=future:
                        expected.append(item)
        observed = []
        def reconcile(**options):
            with self.session() as conn:
                preview = options['preview_due_expectations'](conn,now_utc=self.now,limit=options['limit'])
                claimed = options['claim_due_expectations'](conn,now_utc=self.now,limit=options['limit'],worker_id='synthetic')
                observed.extend(row.expectation_id for row in preview)
                self.assertCountEqual([row.expectation_id for row in claimed],expected)
                self.assertFalse(conn.in_transaction)
                due.release_claims(conn,claimed,released_at_utc=self.now)
            return {'processed':len(preview)}
        def official(conn, **options):
            self.assertFalse(conn.in_transaction)
            self.assertEqual(options['company_ids'],[company])
            self.assertEqual(options['request_limit'],0)
            return {'requests':0}
        with patch.object(automatic_documents,'ownership',side_effect=lambda path,role: self.discovery_ownership()), \
             patch('scrapper.workflows.documents.reconciliation.reconcile_artifacts',side_effect=reconcile), \
             patch('scrapper.sources.documents.routine.refresh_official_pages',side_effect=official):
            result = automatic_documents.run_automatic_discovery(db_path=self.root/'logical.db',epoch=epoch,
                limit=2,historical_limit=0,screener_request_limit=2,official_request_limit=0,
                now_utc=lambda:self.now,connection_factory=self.factory,snapshot_root=self.root/'snapshots')
        self.assertCountEqual([row['expectation_id'] for row in result['selected']],expected)
        self.assertCountEqual(observed,expected)
        self.assertEqual(result['capacity']['selected_company_events'],2)
        self.assertEqual(result['capacity']['selected_expectations'],3)

    @contextmanager
    def discovery_ownership(self):
        # The separate exchange test exercises the actual native advisory lock.
        yield True

    def test_fresh_native_shared_transport_preflight_and_idle_permit_io(self):
        from scrapper.db.host_request_defer import require_shared_schema
        from scrapper.operations.workers.host_requests import HostSession
        from scrapper.documents.downloader import DownloadPolicy
        from scrapper.tests.test_d02_document_downloader import FakeResponse, FakeSession
        url = 'https://issuer.example/synthetic-native.pdf'
        body = b'%PDF-synthetic-native-transport-only'
        with self.session() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 0)
            with patch('sys.platform', 'linux'):
                require_shared_schema(conn)
            self.assertFalse(conn.in_transaction)
            checked = []
            class Offline(FakeSession):
                def get(fake, requested, **kwargs):
                    self.assertFalse(conn.in_transaction)
                    checked.append(requested)
                    with self.session() as other:
                        self.assertEqual(other.execute("SELECT count(*) FROM acquisition_host_permits WHERE status='ACTIVE'").fetchone()[0], 1)
                    return super().get(requested, **kwargs)
            fake = Offline({url: FakeResponse(body, url=url)})
            with HostSession(conn, mode='generic', owner='native-offline',
                    policy=DownloadPolicy(), check=lambda: None,
                    offline_session=fake, wait_seconds=0) as session:
                response = session.get(url, allow_redirects=False)
                self.assertEqual(b''.join(response.iter_content(1024)), body)
            self.assertEqual(checked, [url])
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT count(*) FROM acquisition_host_permits WHERE status='ACTIVE'").fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT last_outcome FROM acquisition_host_state').fetchone()[0], 'success')
