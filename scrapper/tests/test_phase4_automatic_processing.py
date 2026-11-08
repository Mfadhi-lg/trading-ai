"""Real extraction through the inbox selector, fairness, and crash recovery."""
import unittest
from pathlib import Path
from scrapper.tests import test_document_processing_worker as worker_fixtures
from scrapper.db.documents.processing import automatic_processing_contents
from scrapper.operations.workers.document_processing import run_automatic_processing
from scrapper.db.document_work_claims import claim_extraction
from scrapper.documents.priority import current_period

NOW='2026-10-11T15:00:00+00:00'
EPOCH='2026-10-09T00:00:00+00:00'

class AutomaticTests(unittest.TestCase):
    def setUp(self):
        worker_fixtures.WorkerTests.setUp(self)
        with self.conn:
            self.conn.execute("INSERT INTO screener_collection_scope(company_path,company_id,eligibility,floor_crore,observed_at_utc,evidence_json,next_check_at_utc) VALUES (?,?,'eligible','580',?,'{}',?)",('/company/FIXTURE/',self.fixture.company_id,NOW,NOW))
    # Reuse fixture setup only; inherited worker tests also exercise compatibility.
    def arrival(self,name,period='2026-09-30',fresh=True,media='application/pdf',kind='presentation',body=None):
        aid,cid=self.fixture._content(name,media_type=media,artifact_type=kind,body=body)
        with self.conn:
            self.conn.execute('UPDATE document_artifacts SET first_observed_at_utc=?,period_end=? WHERE artifact_id=?',
                ('2026-10-10T00:00:00+00:00' if fresh else '2026-08-01T00:00:00+00:00',period,aid))
        with self.conn:
            self.conn.execute('UPDATE artifact_contents SET created_at_utc=? WHERE content_id=?',
                ('2026-10-10T00:00:00+00:00' if fresh else '2026-08-01T00:00:00+00:00',cid))
        return cid

    def select(self,**kwargs):
        return automatic_processing_contents(self.conn,version=self.version,now=NOW,epoch=EPOCH,**kwargs)

    def auto(self,**kwargs):
        return run_automatic_processing(db_path=self.fixture.db_path,epoch=EPOCH,
            ocr_engine=self.engine,output_dir=self.fixture.root/'automatic',now_utc=lambda:NOW,**kwargs)

    def test_automatic_real_parser_no_manual_ids(self):
        cid=self.arrival('d03_text.pdf')
        result=self.auto()
        self.assertEqual(result['selected'],[{'content_id':cid,'priority':1}])
        self.assertEqual(result['outcomes'][0]['extraction_status'],'ok')
        self.assertEqual(self.auto()['status'],'idle')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],1)

    def test_current_late_and_reserved_history(self):
        old=self.arrival('old.pdf',fresh=False,body=b'old')
        current=self.arrival('current.pdf',body=b'current')
        late=self.arrival('late.pdf',period='2026-06-30',body=b'late')
        self.assertEqual([(r['content_id'],lane) for r,lane in self.select(limit=2)],[(current,1),(late,2)])
        self.assertEqual([(r['content_id'],lane) for r,lane in self.select(limit=3,historical_limit=1)],[(current,1),(old,3),(late,2)])

    def test_unsupported_not_selected_and_unknown_not_fabricated(self):
        self.arrival('audio',media='audio/mpeg',kind='recording',body=b'audio')
        cid=self.arrival('unknown.pdf',period=None,body=b'unknown')
        self.assertEqual([(r['content_id'],lane) for r,lane in self.select()],[(cid,2)])

    def test_failed_cache_is_durable_and_not_retried(self):
        self.arrival('broken.pdf',body=b'broken')
        result=self.auto()
        self.assertEqual(result['outcomes'][0]['extraction_status'],'failed')
        self.assertEqual(self.auto()['status'],'idle')

    def test_missing_legacy_inbox_requires_historical_budget(self):
        cid=self.arrival('d03_text.pdf',fresh=False)
        with self.conn: self.conn.execute('DELETE FROM document_processing_inbox')
        self.assertEqual(self.select(),[])
        self.assertEqual(self.auto(historical_limit=1)['outcomes'][0]['extraction_status'],'ok')
        self.assertEqual(self.conn.execute('SELECT content_id FROM document_processing_inbox').fetchone()[0],cid)

    def test_idle_processing_recovers_historical_quarter_without_extracting(self):
        cid=self.arrival('old.pdf',fresh=False,period=None,body=b'old')
        with self.conn:
            self.conn.execute("UPDATE document_artifacts SET title_raw='Q2FY26' WHERE artifact_id=(SELECT artifact_id FROM artifact_contents WHERE content_id=?)", (cid,))
        result=self.auto()
        self.assertEqual(result['status'],'idle')
        self.assertEqual(result['period_recovery']['recovered'],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],0)
        self.assertEqual(self.auto()['period_recovery']['recovered'],0)

    def test_active_fence_skipped_then_expiry_recovers(self):
        cid=self.arrival('d03_text.pdf')
        from scrapper.db.documents.processing import reconcile_processing
        reconcile_processing(self.conn,content_ids=[cid],version=self.version,now=NOW)
        claim_extraction(self.conn,self.version.key(cid),now=NOW,seconds=600)
        self.assertEqual(self.select(),[])
        with self.conn: self.conn.execute("UPDATE document_extraction_claims SET lease_expires_at_utc='2026-10-10T00:00:00+00:00'")
        self.assertEqual(self.auto()['outcomes'][0]['extraction_status'],'ok')

    def test_rollover_and_invalid_budget(self):
        self.assertEqual(current_period('2027-01-01T00:00:00+00:00'),'2026-12-31')
        self.assertEqual(current_period('2026-10-01T00:00:00+00:00'),'2026-09-30')
        for kwargs in ({'limit':0},{'historical_limit':2},{'limit':True}):
            with self.assertRaises(ValueError):self.select(**kwargs)
