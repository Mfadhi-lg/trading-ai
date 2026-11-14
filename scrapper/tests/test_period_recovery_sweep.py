"""Recurring quarter recovery revisits late evidence without re-extraction."""
import json
import hashlib
import io
from unittest.mock import patch
import unittest

from scrapper.documents.discovery.period_recovery import recover_period_batch
from scrapper.tests import test_document_processing_worker as worker_fixtures


class SweepTests(unittest.TestCase):
    def setUp(self):
        worker_fixtures.WorkerTests.setUp(self)
        self.state = self.fixture.root/'period-recovery.json'
        self.ordinal = 0

    def artifact(self, title='Unresolved', kind='presentation'):
        self.ordinal += 1
        aid, _ = self.fixture._content(title, media_type='application/pdf',
                                        artifact_type=kind, body=f'not a PDF {self.ordinal}'.encode())
        with self.conn:
            self.conn.execute('UPDATE document_artifacts SET title_raw=?,period_end=NULL WHERE artifact_id=?',
                              (title, aid))
        return aid

    def batch(self, **kwargs):
        return recover_period_batch(self.conn, state_path=self.state, **kwargs)

    def test_resume_wrap_and_later_evidence_with_audit_and_no_extraction(self):
        first = self.artifact()
        second = self.artifact('Q2FY26')
        self.artifact('Q2FY26', kind='credit_rating')
        result = self.batch(limit=1)
        self.assertEqual((result['checked'], result['recovered']), (1, 0))
        self.assertEqual(result['after_artifact_id'], first)
        self.assertEqual(self.batch(limit=1)['recovered'], 1)
        self.assertTrue(self.batch(limit=1)['sweep_complete'])
        with self.conn:
            self.conn.execute("UPDATE document_artifacts SET title_raw='Q1FY26' WHERE artifact_id=?", (first,))
        self.assertEqual(self.batch()['recovered'], 1)
        self.assertEqual(self.batch()['recovered'], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM artifact_period_corrections').fetchone()[0], 2)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT period_end FROM document_artifacts WHERE artifact_id=?',
                                           (second,)).fetchone()[0], '2025-09-30')
        self.assertIsNone(self.conn.execute("SELECT period_end FROM document_artifacts WHERE artifact_type='credit_rating'").fetchone()[0])

    def test_fixed_watermark_finishes_despite_new_arrivals(self):
        first = self.artifact()
        self.batch(limit=1)
        later = self.artifact('Q2FY26')
        result = self.batch(limit=1)
        self.assertTrue(result['sweep_complete'])
        self.assertEqual(result['checked'], 0)
        self.assertEqual(self.batch()['recovered'], 1)
        self.assertGreater(later, first)

    def test_stop_preserves_cursor_for_resume(self):
        aid = self.artifact('Q2FY26')
        self.assertEqual(self.batch(stopped=lambda: True)['checked'], 0)
        self.assertEqual(json.loads(self.state.read_text())['after_artifact_id'], 0)
        self.assertEqual(self.batch()['recovered'], 1)

    def test_does_not_overwrite_assigned_period(self):
        aid = self.artifact('Q2FY26')
        with self.conn:
            self.conn.execute("UPDATE document_artifacts SET period_end='2024-09-30' WHERE artifact_id=?", (aid,))
        self.assertEqual(self.batch()['checked'], 0)
        self.assertEqual(self.conn.execute('SELECT period_end FROM document_artifacts WHERE artifact_id=?',
                                          (aid,)).fetchone()[0], '2024-09-30')

    def test_saved_native_pdf_is_recovered_without_extraction(self):
        from reportlab.pdfgen import canvas
        output = io.BytesIO()
        pdf = canvas.Canvas(output)
        pdf.drawString(50, 700, 'Quarter ended September 30, 2025')
        pdf.save()
        aid, _ = self.fixture._content('saved.pdf', media_type='application/pdf',
                                      artifact_type='presentation', body=output.getvalue())
        with patch('scrapper.config.DOCUMENT_STORE', self.fixture.root):
            self.assertEqual(self.batch()['recovered'], 1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0], 0)
        evidence = json.loads(self.conn.execute('SELECT evidence_json FROM artifact_period_corrections WHERE artifact_id=?', (aid,)).fetchone()[0])
        self.assertIn('verified_content:', json.dumps(evidence))

    def test_native_header_reader_rejects_changed_bytes(self):
        from scrapper.documents.discovery.native_period_header import read_headers
        path = self.fixture.root/'changed.pdf'
        path.write_bytes(b'changed')
        self.assertEqual(read_headers({'local_path': str(path), 'byte_size': 7,
            'content_sha256': hashlib.sha256(b'original').hexdigest(), 'content_id': 1}), [])

    def test_interruption_replays_committed_corrections_without_duplicate_audit(self):
        from scrapper.documents.discovery.promotion import fill_stored_artifact_period
        first = self.artifact('Q2FY26')
        second = self.artifact('Q1FY26')
        def interrupt(conn, aid, **kwargs):
            if aid == second:
                raise RuntimeError('interrupted')
            return fill_stored_artifact_period(conn, aid, **kwargs)
        with patch('scrapper.documents.discovery.period_recovery.fill_stored_artifact_period', interrupt):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.batch()
        self.assertFalse(self.state.exists())
        self.assertEqual(self.batch()['recovered'], 1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM artifact_period_corrections').fetchone()[0], 2)

    def test_writable_cursor_continues_retained_readonly_legacy_state(self):
        first=self.artifact('Unresolved')
        second=self.artifact('Q2FY26')
        legacy=self.fixture.root/'legacy-readonly-cursor.json'
        original=json.dumps({'after_artifact_id':first,'through_artifact_id':second})
        legacy.write_text(original);legacy.chmod(0o444)
        new=self.fixture.root/'campaigns'/'document-period-recovery.json'
        result=recover_period_batch(self.conn,state_path=new,legacy_state_path=legacy)
        self.assertEqual(result['checked'],1)
        self.assertEqual(result['recovered'],1)
        self.assertEqual(legacy.read_text(),original)
        self.assertTrue(new.is_file())
