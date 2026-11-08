"""Phase 2 finite selection, integrity, restart and zero-network regressions."""
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from scrapper.tests.test_document_processing_worker import WorkerTests
from scrapper.db.database import connect
from scrapper.operations.workers.local_document_campaign import run_local_campaign, digest


class LocalCampaignTests(unittest.TestCase):
    def setUp(self):
        WorkerTests.setUp(self)
        with self.conn:
            self.conn.execute("""INSERT INTO screener_collection_scope(company_path,company_id,
                eligibility,floor_crore,observed_at_utc,evidence_json,next_check_at_utc)
                VALUES (?,?,'eligible','580',?,'{}',?)""",
                ('/company/FIXTURE/',self.fixture.company_id,'2026-10-26T00:00:00+00:00','2026-10-26T00:00:00+00:00'))

    def manifest(self, name='d03_text.pdf',body=None,media='application/pdf',kind='presentation'):
        aid,cid=self.fixture._content(name,body=body,media_type=media,artifact_type=kind)
        row=dict(self.conn.execute('''SELECT c.*,b.local_path,a.company_id,a.artifact_type
            FROM artifact_contents c JOIN response_blobs b USING(blob_id)
            JOIN document_artifacts a USING(artifact_id) WHERE c.content_id=?''',(cid,)).fetchone())
        return {'campaign_id':'fixture','version':asdict(self.version),'network_mode':'none',
            'external_requests':0,'maximum_documents':100,'daily_maximum':24,
            'contents':[{**row,'verification_state':'verified'}]}

    def run_manifest(self,manifest,**kw):
        return run_local_campaign(db_path=self.fixture.db_path,manifest=manifest,
            expected_sha256=digest(manifest),ocr_engine=self.engine,
            output_dir=self.fixture.root/'campaign',connection_factory=connect,limit=1,**kw)

    def test_real_pdf_zero_network_and_resume(self):
        m=self.manifest()
        with patch('socket.create_connection',side_effect=AssertionError('network')),patch('socket.socket.connect',side_effect=AssertionError('network')):
            first=self.run_manifest(m);second=self.run_manifest(m)
        self.assertEqual(first['outcomes'][0]['extraction_status'],'ok')
        self.assertEqual(second['outcomes'],[])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],1)
        self.assertIn('pdf_extraction_including_ocr',first['outcomes'][0]['timings'])

    def test_dry_run_never_claims_or_reconciles(self):
        m=self.manifest();before=self.conn.total_changes
        result=self.run_manifest(m,dry_run=True)
        self.assertEqual(result['outcomes'][0]['state'],'would_process')
        for table in ('document_processing_work','document_extractions','document_extraction_claims'):
            self.assertEqual(self.conn.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
        self.assertFalse((self.fixture.root/'campaign').exists())

    def test_missing_corrupt_and_disconnected_storage_fail_before_work(self):
        m=self.manifest();path=Path(m['contents'][0]['local_path']);original=path.read_bytes()
        for mode in ('missing','corrupt','disconnected'):
            if mode=='missing':path.unlink()
            elif mode=='corrupt':path.write_bytes(b'bad')
            else:path.write_bytes(original)
            context=patch('scrapper.operations.workers.local_document_campaign._verified_bytes',side_effect=OSError('disconnected')) if mode=='disconnected' else patch('builtins.print')
            with context,self.assertRaises((RuntimeError,OSError)):self.run_manifest(m)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_processing_work').fetchone()[0],0)

    def test_wrong_identity_version_unsupported_and_duplicate_ids(self):
        for key,value in [('content_sha256','bad'),('artifact_id',999),('company_id',999),('verification_state','missing')]:
            m=self.manifest(name=key+'.pdf',body=key.encode());m['contents'][0][key]=value
            with self.assertRaises(ValueError):self.run_manifest(m)
        m=self.manifest(name='audio',body=b'audio',media='audio/mpeg',kind='recording')
        with self.assertRaises(ValueError):self.run_manifest(m)
        m=self.manifest(name='duplicate.pdf',body=b'duplicate');m['contents']*=2
        with self.assertRaises(ValueError):self.run_manifest(m)
        m=self.manifest(name='version.pdf',body=b'version');m['version']['parser_version']='old'
        with self.assertRaises(ValueError):self.run_manifest(m)

    def test_post_commit_receipt_crash_reuses_exact_extraction(self):
        m=self.manifest()
        from scrapper.operations.workers.local_document_campaign import durable_receipt
        def crash(path,value):
            if path.name.startswith('content-'):raise OSError('crash')
            return durable_receipt(path,value)
        with patch('scrapper.operations.workers.local_document_campaign.durable_receipt',side_effect=crash):
            with self.assertRaises(OSError):self.run_manifest(m)
        result=self.run_manifest(m)
        self.assertTrue(result['outcomes'][0]['cache_hit'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],1)

    def test_before_commit_interruption_and_database_lock(self):
        m=self.manifest()
        with patch('scrapper.operations.workers.local_document_campaign.run_processing',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.run_manifest(m)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],0)
        # Short busy timeout on the canonical connection for a bounded fixture.
        other=connect(self.fixture.db_path);other.execute('BEGIN IMMEDIATE')
        def quick(path):
            c=connect(path);c.execute('PRAGMA busy_timeout=1');return c
        try:
            with self.assertRaises(sqlite3.OperationalError):
                run_local_campaign(db_path=self.fixture.db_path,manifest=m,expected_sha256=digest(m),
                    ocr_engine=self.engine,output_dir=self.fixture.root/'campaign',connection_factory=quick,limit=1)
        finally:other.rollback();other.close()
        self.assertEqual(self.run_manifest(m)['outcomes'][0]['extraction_status'],'ok')

    def test_identical_hash_different_content_ids_keep_lineage(self):
        first=self.manifest()
        second=self.manifest(name='second.pdf',body=b'different')
        a=first['contents'][0];b=second['contents'][0]
        with self.conn:
            self.conn.execute('''UPDATE artifact_contents SET blob_id=?,content_sha256=?,byte_size=?
                WHERE content_id=?''',(a['blob_id'],a['content_sha256'],a['byte_size'],b['content_id']))
        b.update(blob_id=a['blob_id'],content_sha256=a['content_sha256'],byte_size=a['byte_size'],local_path=a['local_path'])
        first['contents']+=second['contents']
        self.run_manifest(first);self.run_manifest(first)
        rows=self.conn.execute('SELECT content_id,count(*) n FROM document_extractions GROUP BY content_id').fetchall()
        self.assertEqual({r['content_id']:r['n'] for r in rows},{a['content_id']:1,b['content_id']:1})

    def test_old_parser_is_preserved_and_exact_current_version_reused(self):
        m=self.manifest();self.run_manifest(m)
        cid=m['contents'][0]['content_id']
        # Fixture-only historical version, never a production transition.
        with self.conn:self.conn.execute("UPDATE document_extractions SET parser_version='old' WHERE content_id=?",(cid,))
        receipt=self.fixture.root/'campaign'/('content-%s.json'%cid);receipt.unlink()
        result=self.run_manifest(m)
        self.assertFalse(result['outcomes'][0]['cache_hit'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],2)
        receipt.unlink()
        self.assertTrue(self.run_manifest(m)['outcomes'][0]['cache_hit'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0],2)

    def test_retained_partial_temporary_receipt_does_not_block_restart(self):
        import os
        from scrapper.operations.workers.local_document_campaign import durable_receipt
        root=self.fixture.root/'receipts';root.mkdir()
        path=root/'content-1.json'
        (root/('content-1.json.%s.tmp'%os.getpid())).write_text('interrupted')
        durable_receipt(path,{'extraction_id':1})
        self.assertEqual(json.loads(path.read_text()),{'extraction_id':1})

