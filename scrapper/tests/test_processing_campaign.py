"""Finite campaign selection/accounting and crash recovery against real fixtures."""
from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from . import test_document_processing_worker as worker_fixtures
from ..operations.workers.processing_campaign import (
    freeze_campaign, digest, run_campaign, validate_campaign, campaign_status)
from ..operations.workers.lifecycle import WorkerLifecycle


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.f=worker_fixtures.WorkerTests();self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(self.f.tearDown) if hasattr(self.f,'tearDown') else None
        self.conn=self.f.conn

    def quarter(self, jobs, backlog=None):
        return freeze_campaign(self.conn,campaign_id='june-2026-1',version=self.f.version,
            period='2026-06-30',quarter_selector=lambda *a,**k:(jobs,len(jobs) if backlog is None else backlog))

    def run_campaign(self,campaign,**kw):
        return run_campaign(db_path=self.f.fixture.db_path,campaign=campaign,
            expected_sha256=digest(campaign),ocr_engine=self.f.engine,
            output_dir=self.f.fixture.root/'campaign',now_utc=lambda:'2026-06-30T00:00:00+00:00',**kw)

    def content(self,name='d03_text.pdf',**kw):
        kw.setdefault('media_type','application/pdf');kw.setdefault('artifact_type','presentation')
        return self.f.fixture._content(name,**kw)

    def test_finite_budget_restart_and_arrivals_excluded(self):
        a=self.content();b=self.content('d03_scanned.pdf')
        campaign=self.quarter([a,b],backlog=4)
        arrival=self.content('later.pdf',body=b'later')
        first=self.run_campaign(campaign,limit=1)
        self.assertEqual(first['outstanding'],1)
        second=self.run_campaign(campaign,limit=1)
        self.assertEqual(second['outstanding'],0)
        self.assertFalse(second['retirement_authorized'])
        self.assertEqual(second['snapshot_accounting']['unselected_or_ineligible_artifacts'],2)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_work').fetchone()[0],2)
        self.assertIsNone(self.conn.execute('SELECT * FROM document_processing_work WHERE content_id=?',(arrival[1],)).fetchone())
        with patch('scrapper.pipeline.extract_document.extract_pdf',side_effect=AssertionError('repeat parse')):
            self.assertEqual(self.run_campaign(campaign)['outcomes'],[])

    def test_freeze_readonly_and_empty_noop(self):
        before=self.conn.total_changes
        campaign=self.quarter([],backlog=3)
        self.assertEqual(self.conn.total_changes,before)
        self.assertEqual(self.run_campaign(campaign)['records'],[])
        self.assertFalse(campaign['retirement_authorized'])

    def test_mismatch_entire_campaign_before_writes(self):
        a=self.content();b=self.content('d03_scanned.pdf');c=self.quarter([a,b])
        changed=deepcopy(c);changed['contents'][-1]['content_sha256']='wrong'
        for campaign in (changed,{**c,'version':{**c['version'],'parser_version':'future'}}):
            with self.assertRaises(ValueError):self.run_campaign(campaign)
        with self.assertRaises(ValueError):
            run_campaign(db_path=self.f.fixture.db_path,campaign=c,expected_sha256='bad',ocr_engine=self.f.engine,output_dir=self.f.fixture.root/'out')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_work').fetchone()[0],0)

    def test_failed_is_recorded_not_success_and_not_retried(self):
        c=self.quarter([self.content('bad.pdf',body=b'bad')])
        first=self.run_campaign(c)
        self.assertEqual(first['records'][0]['state'],'failed')
        self.assertTrue(first['extraction_accounted'])
        self.assertEqual(self.run_campaign(c)['outcomes'],[])

    def test_export_crash_reconciles_exact_cache(self):
        c=self.quarter([self.content()]);original=Path.write_text
        def fail(path,*args,**kw):
            if path.name.startswith('work-'):raise OSError('crash after persist')
            return original(path,*args,**kw)
        with patch.object(Path,'write_text',fail):
            with self.assertRaises(OSError):self.run_campaign(c)
        self.assertEqual(campaign_status(self.conn,c)['outstanding'],1)
        with patch('scrapper.pipeline.extract_document.extract_pdf',side_effect=AssertionError('reparse')):
            self.assertEqual(self.run_campaign(c)['outstanding'],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],1)

    def test_busy_claim_pending_then_restart(self):
        a=self.content();c=self.quarter([a])
        from ..db.document_work_claims import claim_extraction,release_extraction
        claim=claim_extraction(self.conn,self.f.version.key(a[1]))
        first=self.run_campaign(c)
        self.assertEqual(first['outstanding'],1);self.assertEqual(first['outcomes'][0]['state'],'busy')
        with self.conn:release_extraction(self.conn,claim)
        self.assertEqual(self.run_campaign(c)['outstanding'],0)

    def test_drain_before_start_no_enqueue(self):
        c=self.quarter([self.content()]);life=WorkerLifecycle();life.set()
        self.assertEqual(self.run_campaign(c,lifecycle=life)['outstanding'],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_work').fetchone()[0],0)

    def test_drain_during_first_job_keeps_remaining_pending(self):
        c=self.quarter([self.content('a.pdf',body=b'a'),self.content('b.pdf',body=b'b')]);life=WorkerLifecycle()
        def parser(*a,**kw):life.set();raise RuntimeError('test parse failure')
        with patch('scrapper.pipeline.extract_document.extract_pdf',parser):r=self.run_campaign(c,lifecycle=life)
        self.assertEqual(r['outstanding'],1);self.assertEqual(len(r['outcomes']),1)
        self.assertEqual(life.completed,1)

    def browser_job(self,aid,cid,evidence=None):
        # Exercise actual browser ledger schema, leaving imported state explicit.
        # Minimal fixture rows; defaults preserve existing ledger constraints.
        with self.conn:
            blob=self.conn.execute('SELECT blob_id FROM artifact_contents WHERE content_id=?',(cid,)).fetchone()[0]
            page=self.conn.execute("""INSERT INTO artifact_discovery_pages
                (company_id,discovered_from_source,discovery_page_url,blob_id,parser_name,parser_version,first_observed_at_utc)
                VALUES (?,'rating_agency','https://example.test',?,'fixture','1','2026-06-30')""",(self.f.fixture.company_id,blob)).lastrowid
            source=self.conn.execute("""INSERT INTO artifact_sources
                (artifact_id,discovery_page_id,discovered_from_source,discovery_page_url,document_url,
                 source_position,source_title_raw,classification_evidence_json,is_primary_source,
                 first_observed_at_utc,last_checked_at_utc)
                VALUES (?,?,'rating_agency','https://example.test','https://example.test/report',0,'report','{}',1,'2026-06-30','2026-06-30')""",(aid,page)).lastrowid
        columns={r['name'] for r in self.conn.execute('PRAGMA table_info(browser_rating_recovery_jobs)')}
        values={'artifact_source_id':source,'target_host':'example.test','company_name':'Fixture Limited','run_id':'run-1','artifact_id':aid,'content_id':cid,'status':'SUCCEEDED',
            'evidence_json':json.dumps(evidence or {}),'requested_url':'https://example.test/report',
            'created_at_utc':'2026-06-30T00:00:00+00:00','updated_at_utc':'2026-06-30T00:00:00+00:00',
            'agency':'icra','publisher':'ICRA','company_id':1,'priority':1}
        values={k:v for k,v in values.items() if k in columns}
        with self.conn:
            cur=self.conn.execute('INSERT INTO browser_rating_recovery_jobs ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()))
        return cur.lastrowid

    def test_browser_explicit_run_original_and_residual(self):
        a=self.content('rating.html',body=b'<html>rating</html>',media_type='text/html',artifact_type='credit_rating')
        job=self.browser_job(*a)
        c=freeze_campaign(self.conn,campaign_id='browser-1',version=self.f.version,run_id='run-1',browser_job_ids=[job])
        self.assertEqual(c['contents'][0]['content_id'],a[1]);self.assertTrue(c['residual_stages'])
        r=self.run_campaign(c)
        self.assertEqual(r['outstanding'],0);self.assertFalse(r['retirement_authorized'])
        self.assertEqual(json.loads(self.conn.execute('SELECT evidence_json FROM browser_rating_recovery_jobs').fetchone()[0]),{})
        with self.assertRaises(ValueError):freeze_campaign(self.conn,campaign_id='bad',version=self.f.version,run_id='other',browser_job_ids=[job])

    def test_browser_foreign_derivative_rejected(self):
        a=self.content();b=self.content('other.pdf',body=b'other')
        job=self.browser_job(*a,evidence={'rendered_content_id':b[1]})
        with self.assertRaises(ValueError):freeze_campaign(self.conn,campaign_id='bad',version=self.f.version,run_id='run-1',browser_job_ids=[job])

    def test_invalid_bounds_and_double_scope(self):
        for kw in ({'limit':0},{'limit':101},{'limit':True},{'run_id':'run-1','browser_job_ids':[True]}):
            with self.assertRaises(ValueError):freeze_campaign(self.conn,campaign_id='test',version=self.f.version,period='2026-06-30',**kw)

    def test_readonly_status_and_inert_import_help(self):
        code="""from unittest.mock import patch
with patch('sqlite3.connect',side_effect=AssertionError('DB')),patch('pathlib.Path.mkdir',side_effect=AssertionError('FS')),patch('threading.Thread.start',side_effect=AssertionError('thread')),patch('socket.socket',side_effect=AssertionError('network')):
 import scrapper.operations.workers.processing_campaign
 from scrapper.commands.processing_campaign import main
 try:main(['--help'])
 except SystemExit as e:assert e.code==0
"""
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        c=self.quarter([self.content()]);before=self.conn.total_changes
        validate_campaign(self.conn,c,expected_sha256=digest(c),version=self.f.version)
        self.assertEqual(campaign_status(self.conn,c)['records'][0]['state'],'not_enqueued')
        self.assertEqual(before,self.conn.total_changes)

    def test_browser_derivative_and_unsupported_accounting(self):
        a=self.content('rating.html',body=b'<html>rating</html>',media_type='text/html',artifact_type='credit_rating')
        b=self.content('derived.pdf',body=b'bad',artifact_type='credit_rating')
        with self.conn:
            self.conn.execute("UPDATE artifact_contents SET artifact_id=?,content_origin='generated_derivative' WHERE content_id=?",(a[0],b[1]))
        job=self.browser_job(*a,evidence={'rendered_content_id':b[1]})
        c=freeze_campaign(self.conn,campaign_id='browser-derivative',version=self.f.version,run_id='run-1',browser_job_ids=[job])
        self.assertEqual([x['content_id'] for x in c['contents']],[a[1],b[1]])
        self.assertEqual(c['contents'][1]['content_origin'],'generated_derivative')
        r=self.run_campaign(c);self.assertEqual(len(r['records']),2)
        self.assertEqual(r['records'][1]['state'],'failed')
        with self.conn:self.conn.execute("UPDATE browser_rating_recovery_jobs SET evidence_json='{}' WHERE browser_job_id=?",(job,))
        with self.assertRaises(ValueError):self.run_campaign(c)
        audio=self.content('audio.mp3',body=b'audio',media_type='audio/mpeg',artifact_type='recording')
        job2=self.browser_job(*audio)
        c2=freeze_campaign(self.conn,campaign_id='unsupported',version=self.f.version,run_id='run-1',browser_job_ids=[job2])
        self.assertEqual(self.run_campaign(c2)['records'][0]['state'],'unsupported')

    def test_existing_quarter_selector_readonly_equivalence(self):
        from ..commands.quarter_extract import pending_contents
        from ..db.collection_scope_repo import record_scope
        aid,cid=self.content()
        with self.conn:
            record_scope(self.conn,path='/company/FIXTURE/',company_id=self.f.fixture.company_id,value='1000',observed_at='2026-06-30T00:00:00+00:00',evidence={})
            self.conn.execute("UPDATE document_artifacts SET period_end='2026-06-30' WHERE artifact_id=?",(aid,))
        before=self.conn.total_changes
        selected,backlog=pending_contents(self.conn,period='2026-06-30',limit=10)
        c=freeze_campaign(self.conn,campaign_id='actual-quarter',version=self.f.version,
            period='2026-06-30',limit=10,quarter_selector=pending_contents)
        self.assertEqual(selected,[(aid,cid)])
        self.assertEqual([(j['artifact_id'],j['content_ids'][0]) for j in c['jobs']],selected)
        self.assertEqual(c['snapshot_accounting']['candidate_artifacts'],backlog)
        self.assertEqual(self.conn.total_changes,before)

    def test_writer_guard_before_enqueue_and_architecture(self):
        c=self.quarter([self.content()])
        with patch('scrapper.operations.workers.processing_campaign.assert_configured_writable_targets',side_effect=RuntimeError('production target')):
            with self.assertRaises(RuntimeError):self.run_campaign(c)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_work').fetchone()[0],0)
        import ast
        from ..operations.workers import processing_campaign
        tree=ast.parse(Path(processing_campaign.__file__).read_text())
        imports=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        self.assertFalse(any('commands' in x or 'cli' in x for x in imports))
