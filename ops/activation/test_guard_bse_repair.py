"""Actual production authorizer on non-idle extraction, plus retained BSE bytes."""
import json,sqlite3,unittest,tempfile,hashlib
from pathlib import Path
from unittest.mock import patch
import phase4_routine_runtime as runtime
from scrapper.db.database import connect
from scrapper.operations.workers.document_processing import run_automatic_processing
from scrapper.tests import test_credit_rating_acquisition as credit_fixtures
from scrapper.tests.test_d03_document_extraction import FakeOcr,FIXTURES
from scrapper.pipeline.promote_document_links import promote_document_links
from scrapper.documents.recovery.failed_download import import_manual_document
from scrapper.sources.exchange.result_filings import parse_result_filings
from scrapper.research.bse_browser_results import assess_download
from scrapper.core.http import FetchResponse

NOW='2026-10-13T04:00:00+00:00';EPOCH='2026-10-12T06:28:31+00:00'
CLASSIFICATIONS='artifact_source_evidence_classifications'

class DownloadRecoveryGuardTests(unittest.TestCase):
    def test_replacement_url_retains_append_only_audit_under_production_guard(self):
        from scrapper.tests.test_document_download_recovery import DocumentDownloadRecoveryTests
        fixture = DocumentDownloadRecoveryTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.conn.set_authorizer(runtime.routine_authorizer)
        fixture.test_replacement_url_preserves_old_source_and_queues_new_job()
        for sql in ('DELETE FROM document_download_recovery_actions',
                    'UPDATE document_download_recovery_actions SET reason_code=reason_code',
                    "UPDATE accepted_page_versions SET accepted_by='untrusted'",
                    'CREATE TABLE unauthorized(x)'):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                fixture.conn.execute(sql)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.f=credit_fixtures.CreditRatingAcquisitionTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        c=self.f.conn;promote_document_links(c)
        a=c.execute('SELECT * FROM document_artifacts').fetchone();s=c.execute('SELECT * FROM artifact_sources').fetchone()
        self.aid=a['artifact_id'];self.engine=FakeOcr()
        saved=import_manual_document(c,artifact_id=self.aid,artifact_source_id=s['artifact_source_id'],path=FIXTURES/'d04_credit_rating.pdf',official=True,reason_detail='Explicitly synthetic offline fixture',snapshot_root=self.f.root/'contents',imported_at_utc=NOW)
        self.cid=c.execute('SELECT content_id FROM artifact_contents WHERE artifact_id=?',(self.aid,)).fetchone()[0]
        with c:c.execute("INSERT INTO screener_collection_scope(company_path,company_id,eligibility,floor_crore,observed_at_utc,evidence_json,next_check_at_utc) VALUES('/company/FIXTURE/',?,'eligible','580',?,'{}',?)",(a['company_id'],NOW,NOW))
        self.db=self.f.root/'scraper.db'
        # Fixture uses its own filename; select the connection's actual target.
        self.db=Path(c.execute('PRAGMA database_list').fetchone()[2])
    def guarded(self,path):
        c=connect(path);c.set_authorizer(runtime.routine_authorizer);return c
    def auto(self):
        return run_automatic_processing(db_path=self.db,epoch=EPOCH,limit=1,historical_limit=0,ocr_engine=self.engine,output_dir=self.f.root/'processed',now_utc=lambda:NOW,connection_factory=self.guarded)
    def test_actual_guard_allows_real_rating_extraction_and_classification(self):
        result=self.auto();self.assertEqual(result['outcomes'][0]['extraction_status'],'ok')
        self.assertEqual(self.f.conn.execute('SELECT count(*) FROM '+CLASSIFICATIONS).fetchone()[0],1)
        self.assertEqual(self.auto()['status'],'idle')
        self.assertEqual(self.f.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],1)
    def test_period_correction_audit_is_append_only_under_production_guard(self):
        from scrapper.documents.discovery.promotion import fill_stored_artifact_period
        c=self.guarded(self.db)
        try:
            with c:
                c.execute("UPDATE document_artifacts SET artifact_type='transcript',title_raw='Q1FY27' WHERE artifact_id=?",(self.aid,))
                c.execute("UPDATE artifact_sources SET classification_evidence_json='{}' WHERE artifact_id=?",(self.aid,))
            self.assertTrue(fill_stored_artifact_period(c,self.aid))
            self.assertFalse(fill_stored_artifact_period(c,self.aid))
            self.assertEqual(c.execute('SELECT period_end FROM document_artifacts WHERE artifact_id=?',(self.aid,)).fetchone()[0],'2026-06-30')
            for sql in ('DELETE FROM artifact_period_corrections','UPDATE artifact_period_corrections SET reason=reason'):
                with self.subTest(sql=sql),self.assertRaises(sqlite3.DatabaseError):c.execute(sql)
        finally:c.close()
    def test_old_guard_failure_recovers_cached_parser_without_reparse(self):
        with patch.object(runtime,'ROUTINE_WRITE_TABLES',runtime.ROUTINE_WRITE_TABLES-{CLASSIFICATIONS}):
            with self.assertRaisesRegex(sqlite3.DatabaseError,'not authorized'):self.auto()
        self.assertEqual(self.f.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],1)
        self.assertEqual(self.f.conn.execute('SELECT count(*) FROM '+CLASSIFICATIONS).fetchone()[0],0)
        with patch('scrapper.pipeline.extract_document.extract_pdf',side_effect=AssertionError('cached extraction reparsed')):
            result=self.auto()
        self.assertEqual(result['outcomes'][0]['extraction_status'],'ok')
        self.assertEqual(self.f.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],1)
        self.assertEqual(self.f.conn.execute('SELECT count(*) FROM '+CLASSIFICATIONS).fetchone()[0],1)
        self.assertIsNotNone(self.f.conn.execute('SELECT extraction_id FROM document_processing_work').fetchone()[0])
    def test_guard_keeps_protected_writes_schema_and_classification_deletes_denied(self):
        self.auto();c=self.guarded(self.db)
        try:
            for sql in ('DELETE FROM '+CLASSIFICATIONS,'UPDATE '+CLASSIFICATIONS+' SET content_id=content_id',"UPDATE accepted_page_versions SET accepted_by='untrusted'",'CREATE TABLE unauthorized(x)','ATTACH DATABASE \':memory:\' AS other'):
                with self.subTest(sql=sql),self.assertRaises(sqlite3.DatabaseError):c.execute(sql)
            c.execute('UPDATE '+CLASSIFICATIONS+' SET classification_basis_json=classification_basis_json');c.commit()
        finally:c.close()

class BSETests(unittest.TestCase):
    def test_registry_quarantine_does_not_accept_unknowns_or_relax_conflict_and_NSE_stops(self):
        from nse_results_runtime import review_dispositions
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.executescript('CREATE TABLE exchange_result_filings(filing_id INTEGER,snapshot_id INTEGER);CREATE TABLE exchange_screener_enqueues(filing_id INTEGER,decision TEXT,decision_reason TEXT);')
        cases=[(1,'security_not_in_registry'),(2,'identity_conflict'),(3,'possible_same_period_revision:prior_filing=99'),(4,'unusable_company_path')]
        for fid,reason in cases:
            c.execute('INSERT INTO exchange_result_filings VALUES (?,1)',(fid,));c.execute("INSERT INTO exchange_screener_enqueues VALUES (?,'manual_review',?)",(fid,reason))
        before=c.total_changes
        pending,fatal=review_dispositions(c,1,retained_bse=True)
        self.assertEqual([r['filing_id'] for r in pending],[1]);self.assertEqual([r['filing_id'] for r in fatal],[2,4])
        pending,fatal=review_dispositions(c,1)
        self.assertFalse(pending);self.assertEqual([r['filing_id'] for r in fatal],[1,2,4]);self.assertEqual(before,c.total_changes)
        c.close()
    def test_relative_receipt_reassessment_preserves_original_and_uses_no_transport(self):
        import phase3_bse_reassess as reassess
        source=Path('artifacts/bse-browser-results/2026-10-13-morning.json')
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as folder,patch.object(reassess,'OUT',Path(folder)),patch.object(reassess,'verify_config'),patch.object(reassess.nse,'check_configuration'):
            reassess.prepare(source)
            receipt=json.loads((Path(folder)/'receipt.json').read_text())
            self.assertEqual(receipt['accepted_row_count'],49)
            self.assertEqual(receipt['reassessment']['source_requests'],0)
            self.assertEqual(receipt['reassessment']['original_receipt_sha256'],before)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
    def test_retained_successful_http_response_no_longer_rejected_for_half_year_rows(self):
        root=Path(__file__).resolve().parents[2];receipt=json.loads((root/'artifacts/bse-browser-results/2026-10-13-morning.json').read_text());raw=(root/'artifacts/bse-browser-results/2026-10-13-morning.raw').read_bytes()
        checked,parsed,reason=assess_download(FetchResponse(content=raw,**receipt['response']),result_period=receipt['result_period'])
        self.assertEqual(reason,'PARSED_PARTIAL_SURFACE');self.assertEqual(parsed.accepted_row_count,49);self.assertFalse(parsed.rejected)
        self.assertEqual(checked.content,raw);self.assertTrue(all(f.period_end_date.endswith(('03-31','06-30','09-30','12-31')) for f in parsed.filings))
    def test_half_year_and_cumulative_never_invent_quarters_but_malformed_codes_reject(self):
        base={'Scrip_cd':532540,'company_name':'Fixture','Fld_CreateDate':'2026-10-12T12:00:00','XMLName':'fixture.xml','Consol_XMLName':None}
        for code in ('SH2026-2027','MH2026-2027','SC2026-2027'):
            p=parse_result_filings(json.dumps({'Table':[base|{'quarter_code':code}]}).encode(),'bse_financial_results')
            self.assertFalse(p.filings);self.assertFalse(p.rejected);self.assertEqual(p.excluded_row_count,1)
        for code in ('SH2026-2028','SQ2026-2028','ZH2026-2027','bad-code'):
            p=parse_result_filings(json.dumps({'Table':[base|{'quarter_code':code}]}).encode(),'bse_financial_results')
            self.assertFalse(p.filings);self.assertTrue(p.rejected)
