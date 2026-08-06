from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import persist_discovery_plan
from scrapper.documents.downloader import (
    PDF,
    PPT,
    PPTX,
    DownloadPolicy,
    detect_media_type,
    download_url,
)
from scrapper.pipeline.fetch_documents import fetch_documents
from scrapper.sources.documents.discovery import build_discovery_plan


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "documents"
DISCOVERY_MANIFEST = FIXTURES / "d01_discovery_manifest.json"
GOLDEN = ROOT / "golden" / "d02_download_expected.json"
PDF_BYTES = (FIXTURES / "d02_sample.pdf").read_bytes()
HTML_BYTES = (FIXTURES / "d02_html_error.pdf").read_bytes()
MP3_BYTES = (FIXTURES / "d02_sample.mp3").read_bytes()
OBSERVED = "2026-08-05T10:00:00+00:00"


class FakeRedirect:
    def __init__(self, status_code: int, url: str, location: str) -> None:
        self.status_code = status_code
        self.url = url
        self.headers = {"Location": location}


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str,
        status_code: int = 200,
        content_type: str = "application/pdf",
        content_length: int | None = None,
        history: tuple[FakeRedirect, ...] = (),
        chunks: tuple[bytes, ...] | None = None,
    ) -> None:
        self.body = body
        self.url = url
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.history = history
        self.chunks = chunks
        self.iterated = False
        self.closed = False

    def iter_content(self, chunk_size: int) -> Any:
        self.iterated = True
        for chunk in self.chunks if self.chunks is not None else (self.body,):
            yield chunk

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: dict[str, list[FakeResponse] | FakeResponse]) -> None:
        self.responses = {
            key: value if isinstance(value, list) else [value]
            for key, value in responses.items()
        }
        self.calls: list[str] = []
        self.closed = False
        self.max_redirects = 30

    def get(self, url: str, **_: Any) -> FakeResponse:
        self.calls.append(url)
        queue = self.responses[url]
        if not queue:
            raise AssertionError(f"Unexpected repeated request: {url}")
        return queue.pop(0)

    def close(self) -> None:
        self.closed = True


def _pptx_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
    return output.getvalue()


class DocumentDownloaderD02Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.db"
        self.snapshots = self.root / "snapshots"
        self.output = self.root / "download-report.json"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        plan = build_discovery_plan(
            DISCOVERY_MANIFEST, requested_symbol="LAURUSLABS"
        )
        persist_discovery_plan(
            self.conn,
            plan,
            observed_at_utc=OBSERVED,
            snapshot_root=self.root / "discovery",
        )
        self.conn.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_byte_signatures_cover_each_d02_container_family(self) -> None:
        legacy_ppt = (
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
            + b"\x00" * 32
            + "PowerPoint Document".encode("utf-16le")
            + b"\x00" * 16
            + "Current User".encode("utf-16le")
        )
        wav = b"RIFF" + (16).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 8
        mp4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 12
        self.assertEqual(detect_media_type(PDF_BYTES), PDF)
        self.assertEqual(detect_media_type(legacy_ppt), PPT)
        self.assertEqual(detect_media_type(_pptx_bytes()), PPTX)
        self.assertEqual(detect_media_type(MP3_BYTES), "audio/mpeg")
        self.assertEqual(detect_media_type(wav), "audio/wav")
        self.assertEqual(detect_media_type(mp4), "video/mp4")
        self.assertEqual(detect_media_type(b"OggSfixture"), "audio/ogg")
        self.assertEqual(detect_media_type(b"fLaCfixture"), "audio/flac")
        self.assertEqual(detect_media_type(HTML_BYTES), "text/html")
        self.assertIsNone(detect_media_type(b"not a known container"))

    def test_html_error_with_pdf_filename_is_diagnostic_not_content(self) -> None:
        url = "https://www.careratings.com/uploads/newsfiles/202607/laurus-labs-rating-rationale.pdf"
        session = FakeSession(
            {url: FakeResponse(HTML_BYTES, url=url, content_type="text/html")}
        )
        times = iter(
            [
                "2026-08-05T11:00:00+00:00",
                "2026-08-05T11:00:01+00:00",
            ]
        )
        report = fetch_documents(
            symbol="LAURUSLABS",
            artifact_types=("credit_rating",),
            db_path=self.db_path,
            output=self.output,
            snapshot_root=self.snapshots,
            session_factory=lambda: session,
            now_utc=lambda: next(times),
            throttle_seconds=0,
            throttle_jitter=0,
        )
        self.assertEqual(report["summary"]["status_counts"], {"rejected_unexpected_html": 1})
        conn = connect(self.db_path)
        try:
            attempt = conn.execute(
                "SELECT * FROM artifact_download_attempts"
            ).fetchone()
            self.assertEqual(attempt["result_status"], "rejected_unexpected_html")
            self.assertEqual(attempt["detected_media_type"], "text/html")
            self.assertIsNotNone(attempt["blob_id"])
            self.assertIsNone(attempt["content_id"])
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM artifact_contents AS content
                       JOIN document_artifacts AS artifact USING (artifact_id)
                       WHERE artifact.artifact_type='credit_rating'"""
                ).fetchone()[0],
                0,
            )
            path = Path(
                conn.execute(
                    "SELECT local_path FROM response_blobs WHERE blob_id=?",
                    (attempt["blob_id"],),
                ).fetchone()[0]
            )
            self.assertEqual(path.suffix, ".html")
            self.assertEqual(path.read_bytes(), HTML_BYTES)
            self.assertEqual(
                conn.execute(
                    """SELECT acquisition_status FROM document_artifacts
                       WHERE artifact_type='credit_rating'"""
                ).fetchone()[0],
                "inaccessible",
            )
        finally:
            conn.close()

    def test_declared_type_and_logical_artifact_type_both_fail_closed(self) -> None:
        wrong_header = download_url(
            "https://example.test/report.pdf",
            artifact_type="transcript",
            session=FakeSession(
                {
                    "https://example.test/report.pdf": FakeResponse(
                        PDF_BYTES,
                        url="https://example.test/report.pdf",
                        content_type="text/html",
                    )
                }
            ),
        )
        self.assertEqual(wrong_header.result_status, "rejected_declared_media_type")

        wrong_artifact = download_url(
            "https://example.test/call.pdf",
            artifact_type="recording",
            session=FakeSession(
                {
                    "https://example.test/call.pdf": FakeResponse(
                        PDF_BYTES,
                        url="https://example.test/call.pdf",
                        content_type="application/pdf",
                    )
                }
            ),
        )
        self.assertEqual(wrong_artifact.result_status, "rejected_artifact_type")

    def test_content_length_stream_size_and_total_time_are_bounded(self) -> None:
        url = "https://example.test/large.pdf"
        declared = FakeResponse(
            PDF_BYTES,
            url=url,
            content_length=10_000,
        )
        result = download_url(
            url,
            artifact_type="transcript",
            policy=DownloadPolicy(max_bytes=32),
            session=FakeSession({url: declared}),
        )
        self.assertEqual(result.result_status, "too_large")
        self.assertFalse(declared.iterated)
        self.assertIsNone(result.body)

        streamed = download_url(
            url,
            artifact_type="transcript",
            policy=DownloadPolicy(max_bytes=12, chunk_bytes=8),
            session=FakeSession(
                {url: FakeResponse(b"", url=url, chunks=(b"%PDF-1.", b"4overflow"))}
            ),
        )
        self.assertEqual(streamed.result_status, "too_large")
        self.assertEqual(len(streamed.body or b""), 12)

        ticks = iter((0.0, 0.6, 0.7))
        timed = download_url(
            url,
            artifact_type="transcript",
            policy=DownloadPolicy(total_timeout_seconds=0.5),
            session=FakeSession({url: FakeResponse(PDF_BYTES, url=url)}),
            monotonic=lambda: next(ticks),
        )
        self.assertEqual(timed.result_status, "timed_out")
        self.assertEqual(timed.body, PDF_BYTES)

    def test_multiple_urls_share_bytes_and_keep_all_source_provenance(self) -> None:
        bse_url = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/q1-fy26-presentation.pdf"
        ir_url = "https://www.lauruslabs.com/investors/q1-fy26/earnings-presentation.pdf"
        cdn_url = "https://cdn.bseindia.com/q1-fy26-presentation.pdf"
        session = FakeSession(
            {
                bse_url: FakeResponse(
                    PDF_BYTES,
                    url=cdn_url,
                    history=(FakeRedirect(302, bse_url, cdn_url),),
                    content_length=len(PDF_BYTES),
                ),
                ir_url: FakeResponse(
                    PDF_BYTES,
                    url=ir_url,
                    content_length=len(PDF_BYTES),
                ),
            }
        )
        times = iter(
            [
                "2026-08-05T12:00:00+00:00",
                "2026-08-05T12:00:01+00:00",
                "2026-08-05T12:00:02+00:00",
                "2026-08-05T12:00:03+00:00",
            ]
        )
        report = fetch_documents(
            symbol="LAURUSLABS",
            artifact_types=("presentation",),
            db_path=self.db_path,
            output=self.output,
            snapshot_root=self.snapshots,
            session_factory=lambda: session,
            now_utc=lambda: next(times),
            throttle_seconds=0,
            throttle_jitter=0,
        )
        self.assertEqual(session.calls, [bse_url, ir_url])
        conn = connect(self.db_path)
        try:
            actual = {
                **report["summary"],
                "download_attempt_count": conn.execute(
                    "SELECT COUNT(*) FROM artifact_download_attempts"
                ).fetchone()[0],
                "download_source_path_count": conn.execute(
                    "SELECT COUNT(*) FROM artifact_download_attempt_sources"
                ).fetchone()[0],
                "artifact_content_count": conn.execute(
                    "SELECT COUNT(*) FROM artifact_contents"
                ).fetchone()[0],
                "distinct_download_blob_count": conn.execute(
                    """SELECT COUNT(DISTINCT blob_id)
                       FROM artifact_download_attempts WHERE result_status='validated'"""
                ).fetchone()[0],
                "redirect_count": sum(row["redirect_count"] for row in report["attempts"]),
                "detected_media_type": report["attempts"][0]["detected_media_type"],
                "content_sha256": report["attempts"][0]["content_sha256"],
            }
            golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
            self.assertEqual(actual, golden)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(DISTINCT document_url) FROM artifact_download_provenance"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(DISTINCT discovery_page_url) FROM artifact_download_provenance"
                ).fetchone()[0],
                3,
            )
            first_attempt = conn.execute(
                """SELECT started_at_utc, fetched_at_utc, redirect_chain_json,
                          response_headers_json, limits_json
                   FROM artifact_download_attempts ORDER BY download_attempt_id"""
            ).fetchone()
            self.assertEqual(first_attempt["started_at_utc"], "2026-08-05T12:00:00+00:00")
            self.assertEqual(first_attempt["fetched_at_utc"], "2026-08-05T12:00:01+00:00")
            self.assertEqual(
                json.loads(first_attempt["redirect_chain_json"]),
                [{"location": cdn_url, "status_code": 302, "url": bse_url}],
            )
            self.assertEqual(
                json.loads(first_attempt["response_headers_json"])["content-length"],
                str(len(PDF_BYTES)),
            )
            self.assertEqual(json.loads(first_attempt["limits_json"])["max_redirects"], 5)
            self.assertEqual(
                [row["discovered_from_source"] for row in report["attempts"][0]["source_provenance"]],
                ["bse", "screener"],
            )
            content = conn.execute("SELECT * FROM artifact_contents").fetchone()
            self.assertEqual(content["is_official"], 1)
            self.assertEqual(content["media_type"], PDF)
            self.assertEqual(Path(self.output).exists(), True)
            exported = json.loads(self.output.read_text(encoding="utf-8"))
            self.assertEqual(exported["schema_version"], "1.0.0")
            self.assertNotIn("output", exported)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
