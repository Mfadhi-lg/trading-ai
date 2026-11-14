"""Fenced canonical discovery selection and finite delayed-publication scope."""
import unittest
from unittest.mock import patch
from scrapper.tests import test_d03_document_extraction as fixtures
from scrapper.db.database import connect
from scrapper.db.documents.expectations import ensure_result_artifact_expectations
from scrapper.db.documents.due import claim_due_expectations,release_claims
from scrapper.operations.workers.automatic_documents import run_automatic_discovery

NOW='2026-10-11T15:00:00+00:00';EPOCH='2026-10-09T00:00:00+00:00'

class DiscoverySelectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DocumentExtractionD03Tests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.c=connect(self.fixture.db_path);self.addCleanup(self.c.close)
        with self.c:
            self.c.execute("INSERT INTO screener_collection_scope(company_path,company_id,eligibility,floor_crore,observed_at_utc,evidence_json,next_check_at_utc) VALUES (?,?,'eligible','580',?,'{}',?)",('/company/FIXTURE/',self.fixture.company_id,NOW,NOW))

    def expectations(self,filing,period,created):
        return ensure_result_artifact_expectations(self.c,company_id=self.fixture.company_id,filing_id=filing,
            exchange='NSE',exchange_key='fixture',result_period=period[:7],period_end=period,
            broadcast_at_utc='2026-10-01T00:00:00+00:00',created_at_utc=created)

    def test_current_and_late_not_oldest_backlog(self):
        self.expectations(1,'2026-03-31','2026-08-01T00:00:00+00:00')
        current=self.expectations(2,'2026-09-30',EPOCH)
        late=self.expectations(3,'2026-06-30',EPOCH)
        captured=[]
        def canonical(**kwargs):
            c=connect(self.fixture.db_path)
            try:
                rows=kwargs['claim_due_expectations'](c,now_utc=NOW,limit=kwargs['limit'],worker_id='fixture')
                captured.extend(rows)
                release_claims(c,rows,released_at_utc=NOW)
                return {'claimed':len(rows)}
            finally:c.close()
        with patch('scrapper.workflows.documents.reconciliation.reconcile_artifacts',side_effect=canonical):
            r=run_automatic_discovery(db_path=self.fixture.db_path,epoch=EPOCH,now_utc=lambda:NOW,official_request_limit=0)
        self.assertEqual([x['priority'] for x in r['selected']],[1,1,2,2])
        self.assertEqual(set(x.expectation_id for x in captured),{current[0],current[1],late[0],late[1]})
        self.assertEqual(self.c.execute('SELECT sum(attempt_count) FROM artifact_expectations').fetchone()[0],0)

    def test_only_due_unleased_peers_share_the_request_budget(self):
        current=self.expectations(2,'2026-09-30',EPOCH)
        late=self.expectations(3,'2026-06-30',EPOCH)
        with self.c:
            self.c.execute('UPDATE artifact_expectations SET next_check_at_utc=? WHERE expectation_id=?',
                ('2026-12-01T00:00:00+00:00',current[1]))
            self.c.execute('UPDATE artifact_expectations SET claim_token=?,claimed_at_utc=?,claim_expires_at_utc=? WHERE expectation_id=?',
                ('other-worker',NOW,'2026-12-01T00:00:00+00:00',late[1]))
        with patch('scrapper.workflows.documents.reconciliation.reconcile_artifacts',return_value={}) as reconcile:
            result=run_automatic_discovery(db_path=self.fixture.db_path,epoch=EPOCH,
                now_utc=lambda:NOW,official_request_limit=0,screener_request_limit=1)
        self.assertEqual([r['expectation_id'] for r in result['selected']],[current[0]])
        self.assertEqual(result['capacity']['remaining_due_expectations'],1)
        self.assertEqual(reconcile.call_args.kwargs['limit'],1)

    def test_verified_url_replaces_stale_collection_path(self):
        from scrapper.db.documents.due import preview_due_expectations,_source_refresh_targets
        self.expectations(2,'2026-09-30',EPOCH)
        with self.c:
            self.c.execute('''INSERT INTO screener_company_collection_state
                (company_path_normalized,reporting_scope,company_id,latest_discovered_result_period,latest_accepted_result_period,updated_at_utc)
                VALUES ('/company/OLD/','standalone',?,'2026-09','2026-09',?)''',(self.fixture.company_id,NOW))
            self.c.execute('''INSERT INTO source_identifiers
                (company_id,source,source_company_id,canonical_url,mapping_status,verified_at_utc)
                VALUES (?,'screener','123','https://www.screener.in/company/NEW/consolidated/','verified',?)''',
                (self.fixture.company_id,NOW))
        def targets():
            return _source_refresh_targets(self.c,preview_due_expectations(self.c,now_utc=NOW,limit=100))
        self.assertEqual(targets()[0].company_path,'/company/NEW/')
        with self.c:
            self.c.execute("UPDATE source_identifiers SET canonical_url='https://evil.example/company/WRONG/'")
        self.assertEqual(targets()[0].company_path,'/company/OLD/')

    def test_request_ceiling_blocks_transport_before_extra_dispatch(self):
        from scrapper.operations.workers.automatic_documents import BoundedDocumentScreenerClient
        from scrapper.operations.workers.screener_requests import SharedScreenerClient
        client=BoundedDocumentScreenerClient.__new__(BoundedDocumentScreenerClient)
        client.request_limit=2;client.document_requests=0
        with patch.object(SharedScreenerClient,'_get',return_value='response') as get:
            self.assertEqual(client._get('https://www.screener.in/company/A/'),'response')
            self.assertEqual(client._get('https://www.screener.in/company/B/'),'response')
            with self.assertRaisesRegex(RuntimeError,'budget exhausted'):
                client._get('https://www.screener.in/company/C/')
        self.assertEqual(get.call_count,2)

    def test_official_link_is_queued_even_when_independent_screener_refresh_fails(self):
        from scrapper.sources.documents.routine import ingest_page
        self.expectations(2,'2026-09-30',EPOCH)
        def official(conn, **kwargs):
            body = b'<a href="https://fixture.example/fresh.pdf" data-period-end="2026-09-30" data-published-at-utc="2026-10-11T14:55:00+00:00">Earnings transcript</a>'
            return ingest_page(conn, company_id=self.fixture.company_id, source='company_ir',
                url='https://fixture.example/investors', body=body, now=NOW,
                snapshot_root=self.fixture.root/'pages')
        with patch('scrapper.sources.documents.routine.refresh_official_pages',side_effect=official), \
             patch('scrapper.workflows.documents.reconciliation.reconcile_artifacts',side_effect=RuntimeError('synthetic parse failure')):
            with self.assertRaisesRegex(RuntimeError,'synthetic parse failure'):
                run_automatic_discovery(db_path=self.fixture.db_path,epoch=EPOCH,
                    now_utc=lambda:NOW,official_request_limit=1,snapshot_root=self.fixture.root/'pages')
        job=self.c.execute('SELECT * FROM artifact_download_queue').fetchone()
        self.assertIsNotNone(job)
        self.assertEqual(job['requested_url'],'https://fixture.example/fresh.pdf')
        self.assertEqual(job['status'],'PENDING')
