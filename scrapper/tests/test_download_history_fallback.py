"""Download ordering, historical handoff, and files beyond the old 10 MiB limit."""
import json
import unittest
from pathlib import Path
from scrapper.tests.test_phase4_routine import RoutineTests, NOW, EPOCH
from scrapper.sources.documents.routine import ingest_page
from scrapper.pipeline.accepted_document_downloads import enqueue_discovered_documents
from scrapper.operations.workers.automatic_documents import select_downloads
from scrapper.documents.downloader import DownloadPolicy, download_url
from scrapper.tests.test_d02_document_downloader import FakeResponse, FakeSession, PDF_BYTES


class DownloadFallbackTests(unittest.TestCase):
    setUp = RoutineTests.setUp

    def arrival(self, name, period, old=False):
        url=f'https://fixture.example/{name}.pdf'
        body=f'<a href="{url}" data-period-end="{period}">Earnings transcript</a>'.encode()
        value=ingest_page(self.c,company_id=self.f.company_id,source='company_ir',url=url,
                          body=body,now=NOW,snapshot_root=self.f.root/'pages')
        aid=value['artifact_ids'][0]
        if old:
            with self.c:self.c.execute("UPDATE document_artifacts SET first_observed_at_utc='2026-08-01T00:00:00+00:00' WHERE artifact_id=?",(aid,))
        return aid

    def enqueue(self):
        return enqueue_discovered_documents(self.c,epoch=EPOCH,now=NOW,limit=30,historical_fallback=True)

    def selected(self, limit=30):
        return select_downloads(self.c,epoch=EPOCH,now=NOW,limit=limit,historical_fallback=True)

    def test_current_precedes_history_and_history_fills_remaining_capacity(self):
        old=[self.arrival('old'+str(i),('2026-06-30','2026-03-31','2025-12-31','2025-09-30')[i],True) for i in range(4)]
        current=self.arrival('current','2026-09-30')
        self.enqueue()
        with self.c:self.c.execute("UPDATE artifact_download_queue SET created_at_utc='2026-08-01T00:00:00+00:00' WHERE artifact_id<>?",(current,))
        selected=self.selected(limit=3)
        self.assertEqual([lane for _,lane in selected],[1,3,3])
        current_job=self.c.execute('SELECT download_job_id FROM artifact_download_queue WHERE artifact_id=?',(current,)).fetchone()[0]
        self.assertEqual(selected[0][0]['download_job_id'],current_job)
        self.assertEqual([lane for _,lane in self.selected(limit=1)],[1])

    def test_current_quarter_wins_even_when_queue_entry_predates_epoch(self):
        self.arrival('current-old','2026-09-30',True);self.enqueue()
        with self.c:self.c.execute("UPDATE artifact_download_queue SET created_at_utc='2026-08-01T00:00:00+00:00'")
        self.assertEqual([lane for _,lane in self.selected()],[1])
        self.assertEqual(select_downloads(self.c,epoch=EPOCH,now=NOW,limit=30),[])

    def test_history_has_no_one_document_reservation_cap(self):
        for i in range(4):self.arrival('old'+str(i),('2026-06-30','2026-03-31','2025-12-31','2025-09-30')[i],True)
        self.assertEqual(len(self.enqueue()),4)
        with self.c:self.c.execute("UPDATE artifact_download_queue SET created_at_utc='2026-08-01T00:00:00+00:00'")
        self.assertEqual(len(self.selected()),4)

    def test_history_preserves_retry_due_cooldown_and_company_eligibility(self):
        self.arrival('old','2026-06-30',True);self.enqueue()
        with self.c:self.c.execute("UPDATE artifact_download_queue SET created_at_utc='2026-08-01T00:00:00+00:00',next_attempt_at_utc='2027-01-01T00:00:00+00:00'")
        self.assertEqual(self.selected(),[])
        with self.c:
            self.c.execute('UPDATE artifact_download_queue SET next_attempt_at_utc=NULL')
            self.c.execute("UPDATE screener_collection_scope SET eligibility='below_floor'")
        self.assertEqual(self.selected(),[])

    def test_large_pdf_is_validated_with_new_policy(self):
        url='https://fixture.example/large.pdf';body=PDF_BYTES+b'\n'*(11*1024**2)
        def session():return FakeSession({url:FakeResponse(body,url=url,content_length=len(body))})
        self.assertEqual(download_url(url,artifact_type='transcript',policy=DownloadPolicy(max_bytes=10*1024**2),session=session()).result_status,'too_large')
        result=download_url(url,artifact_type='transcript',policy=DownloadPolicy(max_bytes=256*1024**2),session=session())
        self.assertEqual(result.result_status,'validated');self.assertEqual(result.byte_size,len(body))
