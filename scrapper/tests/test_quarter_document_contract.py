"""Frozen quarter inventory/repair contracts, run against baseline and candidate."""
import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch
from scrapper.commands import quarter_documents as quarter
from scrapper.tests import test_da06_d09_repair as fixture
T0=fixture.T0
from scrapper.db.collection_scope_repo import record_scope

class QuarterContractTests(unittest.TestCase):
    def setUp(self):
        fixture.PromotedArtifactReconciliationTests.setUp(self)
        self.db=self.root/'d09-repair.db'
        with self.conn:
            record_scope(self.conn,path='/company/FIXTURE/',company_id=self.company_id,value='1000',observed_at=T0,evidence={})
            self.conn.execute("INSERT INTO artifact_download_scopes VALUES ('quarter','custom',NULL,'[]','{}',?,?)",(T0,T0))
    tearDown=fixture.PromotedArtifactReconciliationTests.tearDown
    _page=fixture.PromotedArtifactReconciliationTests._page
    _recovered_artifact=fixture.PromotedArtifactReconciliationTests._recovered_artifact

    def artifact(self,key,kind='presentation',period='2026-06-30',label='Jul 2026',source='screener',url=None,status='PENDING'):
        url=url or 'https://issuer.test/'+key+'.pdf'
        with self.conn:
            aid=self.conn.execute("""INSERT INTO document_artifacts
                (company_id,artifact_key,artifact_type,publisher,title_raw,event_date,period_end,
                 first_observed_at_utc,acquisition_status) VALUES (?,?,?,'Issuer',?,NULL,?,?,'discovered')""",
                (self.company_id,key,kind,key,period,T0)).lastrowid
            self.conn.execute("""INSERT INTO artifact_sources
                (artifact_id,discovery_page_id,discovered_from_source,discovery_page_url,document_url,
                 publisher_claim,source_position,source_title_raw,classification_evidence_json,
                 is_primary_source,first_observed_at_utc,last_checked_at_utc)
                 VALUES (?,?,?,'https://issuer.test',?,'Issuer',0,?,?,1,?,?)""",
                 (aid,self.page_id,source,url,key,json.dumps({'period_label_raw':label}),T0,T0))
            self.conn.execute("""INSERT INTO artifact_download_queue
                (artifact_id,requested_url,target_host,scope_id,status,created_at_utc,updated_at_utc)
                VALUES (?,?,'issuer.test','quarter',?,?,?)""",(aid,url,status,T0,T0))
        return aid

    def test_readonly_inventory_candidates_and_unknown_states(self):
        tagged=self.artifact('tagged')
        candidate=self.artifact('candidate','transcript',None)
        recording=self.artifact('recording','recording',None,status='SUCCEEDED')
        stale=self.artifact('stale',url='https://issuer.test/2025-26/q1/transcript.pdf')
        conflict=self.artifact('conflict',label='Jul 2025')
        before=self.conn.total_changes
        report=quarter.build_inventory(self.conn,period='2026-06-30')
        self.assertEqual(before,self.conn.total_changes)
        self.assertEqual({x['artifact_id'] for x in report['artifacts']},{tagged,candidate,recording})
        self.assertEqual(report['period_conflicts'][0]['artifact_id'],conflict)
        self.assertEqual(report['stale_url_candidates'][0]['artifact_id'],stale)
        self.assertTrue(all(not x['quarter_content_verified'] for x in report['companies']))
        self.assertEqual(report['completeness'][0]['financial_gap_action'],'resolve_exchange_identity')
        with self.conn:self.conn.execute("UPDATE screener_collection_scope SET eligibility='unknown'")
        self.assertEqual(quarter.build_inventory(self.conn,period='2026-06-30')['eligible_companies'],0)

    def test_repairs_preserve_primary_evidence_and_are_idempotent(self):
        repair=self.artifact('repair',label='Jul 2025')
        retain=self.artifact('retain',label='Jul 2025',source='nse')
        report=quarter.build_inventory(self.conn,period='2026-06-30')
        self.assertEqual(quarter.repair_period_tags(self.conn,report),1)
        self.assertEqual(quarter.repair_period_tags(self.conn,report),0)
        self.assertIsNone(self.conn.execute('SELECT period_end FROM document_artifacts WHERE artifact_id=?',(repair,)).fetchone()[0])
        self.assertEqual(self.conn.execute('SELECT period_end FROM document_artifacts WHERE artifact_id=?',(retain,)).fetchone()[0],'2026-06-30')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM artifact_period_corrections').fetchone()[0],1)

    def test_priority_does_not_retry_terminal_leased_recordings_or_blocked_urls(self):
        pending=self.artifact('pending')
        retry=self.artifact('retry','transcript',status='RETRYABLE_FAILURE')
        self.artifact('terminal',status='TERMINAL_FAILURE')
        self.artifact('leased',status='LEASED')
        self.artifact('recording','recording')
        self.artifact('blocked',url='https://www.crisilratings.com/blocked.pdf')
        report=quarter.build_inventory(self.conn,period='2026-06-30')
        self.assertEqual(quarter.prioritize(self.conn,report),2)
        self.assertEqual({x[0] for x in self.conn.execute('SELECT artifact_id FROM artifact_download_queue WHERE priority=10')},{pending,retry})
        self.assertEqual(self.conn.execute('SELECT sum(attempt_count) FROM artifact_download_queue').fetchone()[0],0)

    def test_cli_snapshot_report_files_and_apply_guard(self):
        self.artifact('cli')
        output=self.root/'reports'
        args=['quarter-documents','--db',str(self.root/'d09-repair.db'),'--period','2026-06-30','--output',str(output)]
        with patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):quarter.main()
        self.assertEqual({p.name for p in output.iterdir()},{'inventory.json','company-quarter-coverage.csv','company-quarter-completeness.csv','financial-gap-ledger.csv'})
        report=json.loads((output/'inventory.json').read_text())
        self.assertEqual(report['eligible_companies'],1)
        with patch.object(sys,'argv',args+['--prioritize']),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:quarter.main()
        self.assertEqual(error.exception.code,2)
        with patch.object(sys,'argv',args+['--prioritize','--apply']),contextlib.redirect_stdout(io.StringIO()):quarter.main()
        self.assertEqual(json.loads((output/'inventory.json').read_text())['prioritized_jobs'],1)
