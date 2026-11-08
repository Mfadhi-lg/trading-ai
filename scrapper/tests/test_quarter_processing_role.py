"""Real durable quarter handoff, late arrivals and bounded lifecycle contracts."""
import fcntl
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from . import test_document_processing_worker as worker_fixtures
from ..operations.workers.quarter_processing import run_batch,run_role,role_health
from ..operations.workers.lifecycle import WorkerLifecycle
from ..db.document_work_claims import claim_extraction,release_extraction
from ..commands.quarter_extract import pending_contents


class QuarterRoleTests(unittest.TestCase):
    def setUp(self):
        self.f=worker_fixtures.WorkerTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.conn=self.f.conn;self.state=self.f.fixture.root/'quarter-state'

    def content(self,name='d03_text.pdf',**kw):
        return self.f.fixture._content(name,artifact_type='presentation',media_type='application/pdf',**kw)

    def selector(self,jobs,backlog=None):
        return lambda conn,**kw:(jobs[:kw['limit']],len(jobs) if backlog is None else backlog)

    def batch(self,select,**kw):
        return run_batch(db_path=self.f.fixture.db_path,period='2026-06-30',limit=10,
            state_dir=self.state,ocr_engine=self.f.engine,quarter_selector=select,
            now_utc=lambda:'2026-06-30T00:00:00+00:00',**kw)

    def role(self,select,life,**kw):
        return run_role(db_path=self.f.fixture.db_path,period='2026-06-30',limit=10,
            state_dir=self.state,ocr_engine=self.f.engine,quarter_selector=select,
            lifecycle=life,emit=lambda r:None,**kw)

    def test_real_extraction_manifest_precedes_enqueue(self):
        job=self.content()
        from ..operations.workers.processing_campaign import run_campaign
        def execute(**kwargs):
            self.assertTrue((self.state/'active.json').exists())
            return run_campaign(**kwargs)
        with patch('scrapper.operations.workers.quarter_processing.run_campaign',side_effect=execute):
            r=self.batch(self.selector([job]))
        self.assertEqual(r['outstanding'],0)
        self.assertEqual(r['records'][0]['state'],'ok')
        self.assertFalse(r['retirement_authorized'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_work').fetchone()[0],1)

    def test_crash_before_enqueue_resumes_old_batch_before_arrival(self):
        old=self.content();arrival=self.content('d03_scanned.pdf')
        with patch('scrapper.operations.workers.quarter_processing.run_campaign',side_effect=OSError('crash')):
            with self.assertRaises(OSError):self.batch(self.selector([old]))
        with patch('scrapper.pipeline.extract_document.extract_pdf',wraps=__import__('scrapper.pipeline.extract_document',fromlist=['extract_pdf']).extract_pdf):
            resumed=self.batch(lambda *a,**k:(_ for _ in ()).throw(AssertionError('arrival selected before resume')))
        self.assertEqual(resumed['records'][0]['content_id'],old[1])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_work').fetchone()[0],1)
        late=self.batch(self.selector([arrival]))
        self.assertEqual(late['records'][0]['content_id'],arrival[1])
        self.assertEqual(len(list(self.state.glob('[0-9a-f]'*64+'.json'))),2)

    def test_crash_after_extraction_export_reconciles_without_reparse(self):
        job=self.content();write=Path.write_text
        def fail(path,*args,**kw):
            if path.name.startswith('work-'):raise OSError('export crash')
            return write(path,*args,**kw)
        with patch.object(Path,'write_text',fail):
            with self.assertRaises(OSError):self.batch(self.selector([job]))
        with patch('scrapper.pipeline.extract_document.extract_pdf',side_effect=AssertionError('reparse')):
            self.assertEqual(self.batch(self.selector([]))['outstanding'],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],1)

    def test_busy_owner_stays_pending_then_recovers(self):
        job=self.content();claim=claim_extraction(self.conn,self.f.version.key(job[1]))
        r=self.batch(self.selector([job]));self.assertEqual(r['outstanding'],1)
        with self.conn:release_extraction(self.conn,claim)
        self.assertEqual(self.batch(self.selector([]))['outstanding'],0)

    def test_empty_partial_accounting_is_readonly(self):
        before=self.conn.total_changes
        r=self.batch(self.selector([],backlog=5))
        self.assertEqual(r['snapshot_accounting']['unselected_or_ineligible_artifacts'],5)
        self.assertEqual(r['status'],'waiting')
        self.assertEqual(before,self.conn.total_changes)
        self.assertFalse(self.state.exists())

    def test_failed_outcome_remains_failed(self):
        job=self.content('bad.pdf',body=b'invalid')
        r=self.batch(self.selector([job]));self.assertEqual(r['records'][0]['state'],'failed')
        self.assertEqual(self.batch(self.selector([]))['status'],'waiting')
        self.assertEqual(self.conn.execute('SELECT extraction_status FROM document_extractions').fetchone()[0],'failed')

    def test_saved_period_and_hash_checked_before_writes(self):
        job=self.content()
        with patch('scrapper.operations.workers.quarter_processing.run_campaign',side_effect=OSError):
            with self.assertRaises(OSError):self.batch(self.selector([job]))
        active=self.state/'active.json';original=active.read_text()
        for field in ('period','sha256'):
            x=json.loads(original)
            if field=='period':x['manifest']['scope']['period']='2026-03-31'
            else:x['sha256']='wrong'
            active.write_text(json.dumps(x))
            with self.assertRaises(ValueError):self.batch(self.selector([]))
            self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_work').fetchone()[0],0)

    def test_write_guard_precedes_state_creation(self):
        with patch('scrapper.operations.workers.quarter_processing.assert_configured_writable_targets',side_effect=RuntimeError('production')):
            with self.assertRaises(RuntimeError):self.batch(self.selector([self.content()]))
        self.assertFalse(self.state.exists())

    def test_prestart_and_active_job_drain(self):
        life=WorkerLifecycle();life.set()
        self.assertEqual(self.role(self.selector([]),life)['cycles'],0)
        self.assertFalse(self.state.exists())
        life=WorkerLifecycle();original=life.claimed
        def claimed(job):original(job);life.set()
        life.claimed=claimed
        jobs=[self.content(),self.content('d03_scanned.pdf')]
        r=self.role(self.selector(jobs),life,max_cycles=2)
        self.assertEqual(r['cycles'],1);self.assertIsNone(life.active_job)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],1)
        self.assertEqual(self.batch(self.selector([]))['outstanding'],0)

    def test_fixed_cadence_and_error_retry(self):
        life=WorkerLifecycle();wait=[];life.wait=lambda seconds,**kw:wait.append(seconds)
        self.assertEqual(self.role(self.selector([]),life,max_cycles=3)['cycles'],3)
        self.assertEqual(wait,[60,60])
        with patch('scrapper.operations.workers.quarter_processing.run_batch',side_effect=OSError('DB')):
            self.assertEqual(self.role(self.selector([]),life,max_cycles=2)['error_batches'],2)

    def test_same_state_has_single_role_owner(self):
        self.state.mkdir()
        with (self.state/'owner.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError,'already has an owner'):
                self.role(self.selector([]),WorkerLifecycle(),max_cycles=1)

    def test_real_selector_keeps_partial_and_other_period_out(self):
        job=self.content()
        with self.conn:
            self.conn.execute("UPDATE document_artifacts SET period_end='2026-06-30' WHERE artifact_id=?",(job[0],))
            self.conn.execute("INSERT INTO screener_collection_scope(company_path,company_id,eligibility,market_cap_crore,floor_crore,observed_at_utc,evidence_json,next_check_at_utc) VALUES ('/company/fixture/',?,'eligible',3000,2000,'2026-06-30T00:00:00+00:00','{}','2026-07-01T00:00:00+00:00')",(self.f.fixture.company_id,))
        self.assertEqual(pending_contents(self.conn,period='2026-06-30',limit=10)[0],[job])
        self.assertEqual(self.batch(pending_contents)['records'][0]['state'],'ok')
        self.assertEqual(pending_contents(self.conn,period='2026-06-30',limit=10)[0],[])
        with self.conn:self.conn.execute("UPDATE artifact_contents SET extraction_status='partial' WHERE content_id=?",(job[1],))
        self.assertEqual(self.batch(pending_contents)['status'],'waiting')
        with self.conn:
            self.conn.execute("UPDATE document_artifacts SET period_end='2026-03-31' WHERE artifact_id=?",(job[0],))
            self.conn.execute("UPDATE artifact_contents SET extraction_status='not_started' WHERE content_id=?",(job[1],))
        self.assertEqual(self.batch(pending_contents)['status'],'waiting')

    def test_invalid_scope_and_inert_help(self):
        for period,limit in [('2026-06-29',10),('2026-06-30',0),('2026-06-30',101)]:
            with self.assertRaises(ValueError):run_role(db_path=self.f.fixture.db_path,period=period,
                limit=limit,state_dir=self.state,ocr_engine=self.f.engine,
                quarter_selector=self.selector([]),lifecycle=WorkerLifecycle(),max_cycles=1)
        r=subprocess.run([sys.executable,'-m','scrapper.commands.quarter_processing_role','--help'],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)

    def test_readonly_health_checks_schema_mount_and_progress(self):
        life=WorkerLifecycle(monotonic=lambda:100.);life.beat('WAITING')
        with patch('scrapper.config.DOCUMENT_STORE',self.f.fixture.root):
            with patch.object(Path,'write_text',side_effect=AssertionError('health write')):
                self.assertTrue(role_health(self.f.fixture.db_path,life.snapshot(),100.)['healthy'])
                self.assertTrue(role_health(self.f.fixture.db_path,life.snapshot(),100.)['waiting'])
            life.claimed('work');self.assertIn('progress:stalled',role_health(self.f.fixture.db_path,life.snapshot(),2001.)['errors'])
        with patch('scrapper.config.DOCUMENT_STORE',self.f.fixture.root/'missing'):
            self.assertFalse(role_health(self.f.fixture.db_path,life.snapshot(),100.)['healthy'])


if __name__=='__main__':unittest.main()
