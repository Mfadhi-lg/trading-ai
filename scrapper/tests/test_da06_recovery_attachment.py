from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import register_artifact_content
from scrapper.db.document_download_repo import persist_download_result
from scrapper.db.document_recovery_repo import (
    attach_recovery_candidate,
    build_recovery_targets,
    claim_recovery_target,
    load_recovery_target,
    persist_recovery_candidates,
    reconcile_recovery_for_source,
    store_recovery_discovery_blob,
)
from scrapper.core.access_policy import is_blocked_terms_target
from scrapper.core.http import FetchResponse
from scrapper.documents.downloader import DownloadPolicy, DownloadResult
from scrapper.documents.recovery import RecoverySourceType
from scrapper.documents.recovery_matching import match_candidate
from scrapper.sources.documents.recovery_common import OfficialCandidateObservation
from scrapper.pipeline.alternate_source_recovery import run_document_recovery_worker
from scrapper.pipeline.document_download_recovery import requeue_document_downloads
from scrapper.pipeline.run_document_download_worker import run_document_download_worker
from scrapper.exports.document_alternate_source_recovery import (
    build_alternate_source_recovery_report,
)


NOW = "2026-08-16T00:00:00+00:00"
PDF_BODY = b"%PDF-1.4\nfixture recovered content\n%%EOF"


class _Health:
    def __init__(self, root: Path) -> None:
        self.root = root

    def to_dict(self) -> dict[str, object]:
        return {"root": str(self.root), "free_bytes": 1, "required_free_bytes": 1}


class _Response:
    def __init__(self, url: str, status_code: int) -> None:
        self.url = url
        self.status_code = status_code
        self.headers = {"Content-Type": "text/plain", "Content-Length": "0"}
        self.history: tuple[object, ...] = ()

    def iter_content(self, chunk_size: int):
        yield b""

    def close(self) -> None:
        pass


class _Session:
    max_redirects = 30

    def __init__(self, url: str, status_code: int) -> None:
        self.url = url
        self.status_code = status_code

    def get(self, url: str, **_: object) -> _Response:
        if url != self.url:
            raise AssertionError(f"Unexpected URL: {url}")
        return _Response(url, self.status_code)

    def close(self) -> None:
        pass


class RecoveryAttachmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "attachment.db")
        init_db(self.conn, apply_migrations=True)
        self.artifact_id, self.blocked_source_id = self._seed_blocked_artifact()
        build_recovery_targets(self.conn, observed_at_utc=NOW)
        target_row = self.conn.execute(
            "SELECT recovery_target_id FROM artifact_recovery_targets"
        ).fetchone()
        self.target_id = int(target_row[0])

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def _seed_blob(self, body: bytes, content_type: str, suffix: str) -> int:
        digest = hashlib.sha256(body).hexdigest()
        path = self.root / f"{digest}.{suffix}"
        path.write_bytes(body)
        with self.conn:
            self.conn.execute(
                """INSERT OR IGNORE INTO response_blobs
                   (content_sha256,content_type,encoding,byte_size,local_path,
                    first_observed_at_utc,created_at_utc)
                   VALUES (?,?,NULL,?,?,?,?)""",
                (digest, content_type, len(body), str(path), NOW, NOW),
            )
        return int(
            self.conn.execute(
                "SELECT blob_id FROM response_blobs WHERE content_sha256=?", (digest,)
            ).fetchone()[0]
        )

    def _seed_blocked_artifact(self) -> tuple[int, int]:
        blob_id = self._seed_blob(b"blocked discovery", "text/html", "html")
        with self.conn:
            company_id = int(
                self.conn.execute(
                    "INSERT INTO companies(current_legal_name,status) VALUES ('Fixture Limited','active')"
                ).lastrowid
            )
            self.conn.execute(
                """INSERT INTO security_identifiers
                   (company_id,identifier_type,identifier_value,is_current)
                   VALUES (?,'NSE','FIXTURE',1)""",
                (company_id,),
            )
            self.conn.execute(
                """INSERT INTO security_identifiers
                   (company_id,identifier_type,identifier_value,is_current)
                   VALUES (?,'ISIN','INE000A01000',1)""",
                (company_id,),
            )
            page_id = int(
                self.conn.execute(
                    """INSERT INTO artifact_discovery_pages
                       (company_id,discovered_from_source,discovery_page_url,
                        publisher_claim,blob_id,parser_name,parser_version,
                        first_observed_at_utc)
                       VALUES (?,'screener','https://www.screener.in/company/FIXTURE/',
                               NULL,?,'fixture','1',?)""",
                    (company_id, blob_id, NOW),
                ).lastrowid
            )
            artifact_id = int(
                self.conn.execute(
                    """INSERT INTO document_artifacts
                       (company_id,artifact_key,artifact_type,publisher,title_raw,
                        event_date,first_observed_at_utc)
                       VALUES (?,'rating:fixture','credit_rating','CRISIL',
                               'CRISIL AA+ Stable','2026-08-01',?)""",
                    (company_id, NOW),
                ).lastrowid
            )
            source_id = int(
                self.conn.execute(
                    """INSERT INTO artifact_sources
                       (artifact_id,discovery_page_id,discovered_from_source,
                        discovery_page_url,document_url,publisher_claim,
                        source_position,source_title_raw,
                        classification_evidence_json,is_primary_source,
                        first_observed_at_utc,last_checked_at_utc)
                       VALUES (?,?,'screener','https://www.screener.in/company/FIXTURE/',
                               'https://www.crisilratings.com/reports/fixture.pdf',
                               'CRISIL',0,'CRISIL rating','{}',0,?,?)""",
                    (artifact_id, page_id, NOW, NOW),
                ).lastrowid
            )
        return artifact_id, source_id

    def _accepted_candidate(self, *, source_type=RecoverySourceType.NSE_EXCHANGE_ATTACHMENT,
                            url="https://nsearchives.nseindia.com/corporate/fixture.pdf") -> int:
        target = load_recovery_target(self.conn, self.target_id)
        observation = OfficialCandidateObservation(
            candidate_url=url,
            retrieval_host=url.split("/")[2],
            source_type=source_type,
            discovery_page_url="https://www.nseindia.com/api/corporate-announcements",
            document_title="CRISIL Credit Rating Reaffirmation",
            publication_date="2026-08-01",
            published_at_utc="2026-08-01T08:00:00+00:00",
            available_at_utc="2026-08-01T08:00:00+00:00",
            availability_basis="exchange_filing_timestamp",
            company_identifiers={"ISIN": ("INE000A01000",), "NSE": ("FIXTURE",)},
            document_originator="CRISIL", rating=None, outlook=None,
            instrument_or_facility=None,
            source_evidence={"issuer": "Fixture Limited", "announcement_id": "NSE-1"},
        )
        candidate = match_candidate(target, observation, discovered_at_utc=NOW)
        self.assertTrue(candidate.may_auto_attach)
        response = b'[{"announcement_id":"NSE-1"}]'
        blob_id = store_recovery_discovery_blob(
            self.conn,
            content=response,
            source="nse",
            requested_url=observation.discovery_page_url,
            content_type="application/json",
            observed_at_utc=NOW,
            snapshot_root=self.root / "recovery-discovery",
        )
        persisted = persist_recovery_candidates(
            self.conn,
            recovery_target_id=self.target_id,
            candidates=(candidate,),
            discovery_blob_id=blob_id,
            persisted_at_utc=NOW,
        )
        self.assertIn(persisted["target_status"], {"CANDIDATE_FOUND", "AMBIGUOUS"})
        return int(
            self.conn.execute(
                """SELECT candidate_id FROM artifact_recovery_candidates
                    WHERE recovery_target_id=? AND candidate_url=?""",
                (self.target_id, url),
            ).fetchone()[0]
        )

    def _seed_extraction(self, content_id: int, text: str) -> None:
        with self.conn:
            extraction_id = int(
                self.conn.execute(
                    """INSERT INTO document_extractions
                       (content_id,parser_name,parser_version,ocr_engine,
                        ocr_engine_version,options_json,started_at_utc,
                        completed_at_utc,extraction_status,page_or_slide_count,
                        native_page_count,ocr_page_count,unusable_page_count,
                        error_json,created_at_utc)
                       VALUES (?,'fixture','1','none','1','{}',?,?,'ok',1,1,0,0,'{}',?)""",
                    (content_id, NOW, NOW, NOW),
                ).lastrowid
            )
            self.conn.execute(
                """INSERT INTO document_pages
                   (extraction_id,content_id,page_or_slide_number,page_kind,
                    native_text,ocr_text,selected_text,selected_text_origin,
                    native_text_usable,layout_json)
                   VALUES (?,?,1,'page',?,'',?,'native',1,'{}')""",
                (extraction_id, content_id, text, text),
            )

    def _seed_failed_extraction(self, content_id: int) -> int:
        with self.conn:
            extraction_id = int(
                self.conn.execute(
                    """INSERT INTO document_extractions
                       (content_id,parser_name,parser_version,ocr_engine,
                        ocr_engine_version,options_json,started_at_utc,
                        completed_at_utc,extraction_status,page_or_slide_count,
                        native_page_count,ocr_page_count,unusable_page_count,
                        error_json,created_at_utc)
                       VALUES (?,'fixture','1','none','1','{}',?,?,'failed',
                               0,0,0,0,?,?)""",
                    (
                        content_id,
                        NOW,
                        NOW,
                        json.dumps({"type": "FixtureExtractionError"}),
                        NOW,
                    ),
                ).lastrowid
            )
            self.conn.execute(
                "UPDATE artifact_contents SET extraction_status='failed' WHERE content_id=?",
                (content_id,),
            )
        return extraction_id

    def test_attach_uses_same_artifact_preserves_blocked_source_and_queues_d02(self) -> None:
        candidate_id = self._accepted_candidate()
        before = self.conn.execute("SELECT COUNT(*) FROM document_artifacts").fetchone()[0]
        first = attach_recovery_candidate(self.conn, candidate_id=candidate_id,
                                          attached_at_utc=NOW)
        second = attach_recovery_candidate(self.conn, candidate_id=candidate_id,
                                           attached_at_utc=NOW)
        after = self.conn.execute("SELECT COUNT(*) FROM document_artifacts").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(first["artifact_id"], self.artifact_id)
        self.assertEqual(first["artifact_source_id"], second["artifact_source_id"])
        sources = self.conn.execute(
            """SELECT document_url,automation_status,document_originator,
                      retrieval_source_type
                 FROM artifact_sources WHERE artifact_id=? ORDER BY artifact_source_id""",
            (self.artifact_id,),
        ).fetchall()
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["automation_status"], "BLOCKED_TERMS")
        self.assertIsNone(sources[0]["retrieval_source_type"])
        self.assertEqual(sources[1]["automation_status"], "AVAILABLE")
        self.assertEqual(sources[1]["document_originator"], "CRISIL")
        self.assertEqual(sources[1]["retrieval_source_type"],
                         "NSE_EXCHANGE_ATTACHMENT")
        jobs = self.conn.execute(
            """SELECT requested_url,status FROM artifact_download_queue
                WHERE artifact_id=? ORDER BY download_job_id""",
            (self.artifact_id,),
        ).fetchall()
        self.assertEqual({row["status"] for row in jobs}, {"SKIPPED_POLICY", "PENDING"})

    def test_duplicate_sha_reuses_blob_and_reconciles_jobs_without_unblocking(self) -> None:
        candidate_id = self._accepted_candidate()
        attached = attach_recovery_candidate(
            self.conn, candidate_id=candidate_id, attached_at_utc=NOW
        )
        # Existing physical bytes belong to another logical artifact first.
        company_id = int(
            self.conn.execute(
                "SELECT company_id FROM document_artifacts WHERE artifact_id=?",
                (self.artifact_id,),
            ).fetchone()[0]
        )
        with self.conn:
            other = int(
                self.conn.execute(
                    """INSERT INTO document_artifacts
                       (company_id,artifact_key,artifact_type,title_raw,first_observed_at_utc)
                       VALUES (?,'other-rating','credit_rating','Other rating',?)""",
                    (company_id, NOW),
                ).lastrowid
            )
            blob_id = self._seed_blob(PDF_BODY, "application/pdf", "pdf")
            register_artifact_content(
                self.conn, artifact_id=other, blob_id=blob_id,
                media_type="application/pdf", is_official=True, created_at_utc=NOW,
            )
        url = "https://nsearchives.nseindia.com/corporate/fixture.pdf"
        persisted = persist_download_result(
            self.conn,
            artifact_id=self.artifact_id,
            artifact_source_ids=(attached["artifact_source_id"],),
            result=DownloadResult(
                requested_url=url, final_url=url, http_status=200,
                response_headers={"content-type": "application/pdf"}, redirects=(),
                declared_media_type="application/pdf", detected_media_type="application/pdf",
                body=PDF_BODY, byte_size=len(PDF_BODY), result_status="validated", elapsed_ms=1,
            ),
            policy=DownloadPolicy(), started_at_utc=NOW, fetched_at_utc=NOW,
            snapshot_root=self.root / "documents",
        )
        self.assertIsNotNone(persisted.content_id)
        reconciled = reconcile_recovery_for_source(
            self.conn,
            artifact_source_id=attached["artifact_source_id"],
            content_id=int(persisted.content_id),
            reconciled_at_utc=NOW,
        )
        self.assertEqual(reconciled["classification_status"], "PENDING")
        self.assertIsNone(reconciled["content_class"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM response_blobs WHERE content_sha256=?",
                (hashlib.sha256(PDF_BODY).hexdigest(),),
            ).fetchone()[0],
            1,
        )
        target = self.conn.execute(
            """SELECT status,basic_rating_coverage,full_rationale_coverage
                FROM artifact_recovery_targets WHERE recovery_target_id=?""",
            (self.target_id,),
        ).fetchone()
        self.assertEqual(tuple(target), ("CANDIDATE_FOUND", 0, 0))

        self._seed_extraction(int(persisted.content_id), "Downloaded filing without rating evidence")
        classified = reconcile_recovery_for_source(
            self.conn,
            artifact_source_id=attached["artifact_source_id"],
            content_id=int(persisted.content_id),
            reconciled_at_utc=NOW,
        )
        self.assertEqual(classified["content_class"], "UNKNOWN_RATING_DOCUMENT")
        self.assertFalse(classified["basic_rating_coverage"])
        target = self.conn.execute(
            """SELECT status,basic_rating_coverage,full_rationale_coverage
                FROM artifact_recovery_targets WHERE recovery_target_id=?""",
            (self.target_id,),
        ).fetchone()
        self.assertEqual(tuple(target), ("PENDING", 0, 0))
        self.assertEqual(
            self.conn.execute(
                "SELECT automation_status FROM artifact_sources WHERE artifact_source_id=?",
                (self.blocked_source_id,),
            ).fetchone()[0],
            "BLOCKED_TERMS",
        )
        jobs = {
            row["target_host"]: row["status"]
            for row in self.conn.execute(
                """SELECT target_host,status FROM artifact_download_queue
                    WHERE artifact_id=?""",
                (self.artifact_id,),
            )
        }
        self.assertEqual(jobs["www.crisilratings.com"], "SKIPPED_POLICY")
        self.assertEqual(
            jobs["nsearchives.nseindia.com"], "PENDING"
        )
        issuer_candidate = self._accepted_candidate(
            source_type=RecoverySourceType.ISSUER_IR_ATTACHMENT,
            url="https://issuer.example/investors/rating.pdf",
        )
        attach_recovery_candidate(
            self.conn,candidate_id=issuer_candidate,attached_at_utc=NOW
        )
        issuer_job = self.conn.execute(
            """SELECT status FROM artifact_download_queue
                WHERE artifact_id=? AND target_host='issuer.example'""",
            (self.artifact_id,),
        ).fetchone()
        self.assertEqual(issuer_job["status"], "PENDING")

    def test_positive_classification_is_the_only_recovery_finalization_point(self) -> None:
        candidate_id = self._accepted_candidate()
        attached = attach_recovery_candidate(self.conn, candidate_id=candidate_id,
                                             attached_at_utc=NOW)
        persisted = persist_download_result(
            self.conn,
            artifact_id=self.artifact_id,
            artifact_source_ids=(attached["artifact_source_id"],),
            result=DownloadResult(
                requested_url="https://nsearchives.nseindia.com/corporate/fixture.pdf",
                final_url="https://nsearchives.nseindia.com/corporate/fixture.pdf",
                http_status=200,response_headers={"content-type": "application/pdf"},
                redirects=(),declared_media_type="application/pdf",
                detected_media_type="application/pdf",body=PDF_BODY,
                byte_size=len(PDF_BODY),result_status="validated",elapsed_ms=1,
            ),
            policy=DownloadPolicy(),started_at_utc=NOW,fetched_at_utc=NOW,
            snapshot_root=self.root / "classified-documents",
        )
        content_id = int(persisted.content_id or 0)
        pending = reconcile_recovery_for_source(
            self.conn,artifact_source_id=attached["artifact_source_id"],
            content_id=content_id,reconciled_at_utc=NOW,
        )
        self.assertEqual(pending["classification_status"], "PENDING")
        self._seed_extraction(
            content_id,
            "CRISIL Rating Rationale. Key Rating Drivers. Liquidity: strong. "
            "Rating Sensitivities and downgrade factors.",
        )
        classified = reconcile_recovery_for_source(
            self.conn,artifact_source_id=attached["artifact_source_id"],
            content_id=content_id,reconciled_at_utc=NOW,
        )
        self.assertEqual(classified["content_class"], "FULL_RATING_RATIONALE")
        self.assertTrue(classified["basic_rating_coverage"])
        target = self.conn.execute(
            """SELECT status,basic_rating_coverage,full_rationale_coverage
                FROM artifact_recovery_targets WHERE recovery_target_id=?""",
            (self.target_id,),
        ).fetchone()
        self.assertEqual(tuple(target), ("RECOVERED", 1, 1))

    def test_failed_extraction_releases_candidate_without_deleting_history_or_content(self) -> None:
        candidate_id = self._accepted_candidate()
        attached = attach_recovery_candidate(
            self.conn, candidate_id=candidate_id, attached_at_utc=NOW
        )
        with self.conn:
            attempt_id = int(
                self.conn.execute(
                    """INSERT INTO artifact_recovery_attempts
                       (recovery_target_id,source_type,attempt_number,status,
                        started_at_utc,completed_at_utc)
                       VALUES (?,'nse',1,'CANDIDATE_FOUND',?,?)""",
                    (self.target_id, NOW, NOW),
                ).lastrowid
            )
        url = "https://nsearchives.nseindia.com/corporate/fixture.pdf"
        persisted = persist_download_result(
            self.conn,
            artifact_id=self.artifact_id,
            artifact_source_ids=(attached["artifact_source_id"],),
            result=DownloadResult(
                requested_url=url,
                final_url=url,
                http_status=200,
                response_headers={"content-type": "application/pdf"},
                redirects=(),
                declared_media_type="application/pdf",
                detected_media_type="application/pdf",
                body=PDF_BODY,
                byte_size=len(PDF_BODY),
                result_status="validated",
                elapsed_ms=1,
            ),
            policy=DownloadPolicy(),
            started_at_utc=NOW,
            fetched_at_utc=NOW,
            snapshot_root=self.root / "failed-extraction-documents",
        )
        content_id = int(persisted.content_id or 0)
        extraction_id = self._seed_failed_extraction(content_id)

        history_before = {
            "sources": self.conn.execute(
                "SELECT COUNT(*) FROM artifact_sources WHERE artifact_id=?",
                (self.artifact_id,),
            ).fetchone()[0],
            "candidates": self.conn.execute(
                "SELECT COUNT(*) FROM artifact_recovery_candidates WHERE recovery_target_id=?",
                (self.target_id,),
            ).fetchone()[0],
            "attempts": self.conn.execute(
                "SELECT COUNT(*) FROM artifact_recovery_attempts WHERE recovery_target_id=?",
                (self.target_id,),
            ).fetchone()[0],
        }
        reconciled = reconcile_recovery_for_source(
            self.conn,
            artifact_source_id=attached["artifact_source_id"],
            content_id=content_id,
            reconciled_at_utc=NOW,
        )

        self.assertEqual(reconciled["classification_status"], "EXTRACTION_FAILED")
        self.assertEqual(reconciled["target_status"], "PENDING")
        target = self.conn.execute(
            """SELECT status,basic_rating_coverage,full_rationale_coverage,
                      resolved_candidate_id
                 FROM artifact_recovery_targets WHERE recovery_target_id=?""",
            (self.target_id,),
        ).fetchone()
        self.assertEqual(tuple(target), ("PENDING", 0, 0, None))
        self.assertIsNotNone(
            claim_recovery_target(
                self.conn, source="bse", worker="bse-after-d03", now_utc=NOW
            )
        )

        self.assertEqual(
            self.conn.execute(
                """SELECT artifact_id,content_sha256,byte_size,extraction_status
                     FROM artifact_contents WHERE content_id=?""",
                (content_id,),
            ).fetchone()["extraction_status"],
            "failed",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT extraction_status FROM document_extractions WHERE extraction_id=?",
                (extraction_id,),
            ).fetchone()[0],
            "failed",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT artifact_source_id FROM artifact_sources WHERE artifact_source_id=?",
                (attached["artifact_source_id"],),
            ).fetchone()[0],
            attached["artifact_source_id"],
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT candidate_id FROM artifact_recovery_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()[0],
            candidate_id,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT recovery_attempt_id FROM artifact_recovery_attempts WHERE recovery_attempt_id=?",
                (attempt_id,),
            ).fetchone()[0],
            attempt_id,
        )
        history_after = {
            "sources": self.conn.execute(
                "SELECT COUNT(*) FROM artifact_sources WHERE artifact_id=?",
                (self.artifact_id,),
            ).fetchone()[0],
            "candidates": self.conn.execute(
                "SELECT COUNT(*) FROM artifact_recovery_candidates WHERE recovery_target_id=?",
                (self.target_id,),
            ).fetchone()[0],
            "attempts": self.conn.execute(
                "SELECT COUNT(*) FROM artifact_recovery_attempts WHERE recovery_target_id=?",
                (self.target_id,),
            ).fetchone()[0],
        }
        # The BSE claim adds one attempt but must not remove any NSE history.
        self.assertEqual(history_after["sources"], history_before["sources"])
        self.assertEqual(history_after["candidates"], history_before["candidates"])
        self.assertEqual(history_after["attempts"], history_before["attempts"] + 1)

    def test_terminal_d02_failure_reopens_target_for_an_untried_source(self) -> None:
        candidate_id = self._accepted_candidate()
        attached = attach_recovery_candidate(self.conn,candidate_id=candidate_id,
                                             attached_at_utc=NOW)
        with self.conn:
            self.conn.execute(
                """INSERT INTO artifact_recovery_attempts
                   (recovery_target_id,source_type,attempt_number,status,
                    started_at_utc,completed_at_utc)
                   VALUES (?,'nse',1,'CANDIDATE_FOUND',?,?)""",
                (self.target_id, NOW, NOW),
            )
        url = "https://nsearchives.nseindia.com/corporate/fixture.pdf"
        result = run_document_download_worker(
            self.conn,scope_id="da06-rating-recovery",limit=1,max_attempts=1,
            throttle_seconds=0,snapshot_root=self.root / "failed-download",
            document_store=self.root,session_factory=lambda: _Session(url, 404),
            now=lambda: NOW,monotonic=lambda: 0.0,worker="failed-recovery",
            health_check=_Health,
        )
        self.assertEqual(result["queue"]["status_counts"]["TERMINAL_FAILURE"], 1)
        target = self.conn.execute(
            """SELECT status,resolved_candidate_id FROM artifact_recovery_targets
                WHERE recovery_target_id=?""",
            (self.target_id,),
        ).fetchone()
        self.assertEqual(tuple(target), ("PENDING", None))
        claim = claim_recovery_target(
            self.conn,source="bse",worker="bse-worker",now_utc=NOW
        )
        self.assertIsNotNone(claim)
        self.assertEqual(attached["recovery_target_id"], self.target_id)

    def test_stale_already_content_closure_can_be_audited_for_unresolved_alternate(self) -> None:
        # Historical unverified bytes must not prevent a permitted official
        # alternate source from entering D02 while recovery is unresolved.
        blob_id = self._seed_blob(b"historical unverified html", "text/html", "html")
        register_artifact_content(
            self.conn,
            artifact_id=self.artifact_id,
            blob_id=blob_id,
            media_type="text/html",
            is_official=False,
            created_at_utc=NOW,
        )
        candidate_id = self._accepted_candidate(
            source_type=RecoverySourceType.BSE_EXCHANGE_ATTACHMENT,
            url="https://www.bseindia.com/xml-data/corpfiling/AttachLive/rating.pdf",
        )
        attach_recovery_candidate(
            self.conn, candidate_id=candidate_id, attached_at_utc=NOW
        )
        job = self.conn.execute(
            """SELECT download_job_id FROM artifact_download_queue
                WHERE artifact_id=? AND target_host='www.bseindia.com'""",
            (self.artifact_id,),
        ).fetchone()
        with self.conn:
            self.conn.execute(
                """UPDATE artifact_download_queue
                      SET status='SKIPPED_ALREADY_HAVE_CONTENT',
                          last_result_status='CONTENT_ALREADY_PRESENT'
                    WHERE download_job_id=?""",
                (job["download_job_id"],),
            )

        result = requeue_document_downloads(
            self.conn,
            queue_kind="generic",
            job_ids=(int(job["download_job_id"]),),
            reason_code="RESOLVER_UPDATED",
            reason_detail="Stale worker lacked the unresolved DA-06 exception",
            applied_at_utc=NOW,
        )
        self.assertEqual(result["requeued_job_count"], 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM artifact_download_queue WHERE download_job_id=?",
                (job["download_job_id"],),
            ).fetchone()[0],
            "PENDING",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM document_download_recovery_actions WHERE download_job_id=?",
                (job["download_job_id"],),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM artifact_contents WHERE artifact_id=?",
                (self.artifact_id,),
            ).fetchone()[0],
            1,
        )

    def test_second_adapter_cannot_overwrite_selected_exchange_provenance(self) -> None:
        nse_candidate = self._accepted_candidate()
        attached = attach_recovery_candidate(
            self.conn, candidate_id=nse_candidate, attached_at_utc=NOW
        )
        target = load_recovery_target(self.conn, self.target_id)
        ir_observation = OfficialCandidateObservation(
            candidate_url="https://issuer.example/investors/rating.pdf",
            retrieval_host="issuer.example",
            source_type=RecoverySourceType.ISSUER_IR_ATTACHMENT,
            discovery_page_url="https://issuer.example/investor-relations",
            document_title="CRISIL Credit Rating Reaffirmation",
            publication_date="2026-08-01", published_at_utc=None,
            available_at_utc=NOW, availability_basis="first_observation",
            company_identifiers={"company_id": (str(target.company_id),)},
            document_originator="CRISIL", rating=None, outlook=None,
            instrument_or_facility=None,
            source_evidence={"issuer": "Fixture Limited"},
        )
        ir_candidate = match_candidate(target, ir_observation, discovered_at_utc=NOW)
        blob_id = store_recovery_discovery_blob(
            self.conn, content=b"issuer page", source="issuer-ir",
            requested_url=ir_observation.discovery_page_url, content_type="text/html",
            observed_at_utc=NOW, snapshot_root=self.root / "issuer-discovery",
        )
        persist_recovery_candidates(
            self.conn, recovery_target_id=self.target_id,
            candidates=(ir_candidate,), discovery_blob_id=blob_id, persisted_at_utc=NOW,
        )
        ir_id = int(
            self.conn.execute(
                "SELECT candidate_id FROM artifact_recovery_candidates WHERE candidate_url=?",
                (ir_observation.candidate_url,),
            ).fetchone()[0]
        )
        with self.assertRaisesRegex(ValueError, "already selected"):
            attach_recovery_candidate(self.conn, candidate_id=ir_id, attached_at_utc=NOW)
        source = self.conn.execute(
            "SELECT retrieval_source_type FROM artifact_sources WHERE artifact_source_id=?",
            (attached["artifact_source_id"],),
        ).fetchone()
        self.assertEqual(source[0], "NSE_EXCHANGE_ATTACHMENT")

    def test_worker_is_idempotent_and_never_requests_blocked_publishers(self) -> None:
        requested: list[str] = []
        payload = [{
            "symbol": "FIXTURE", "sm_name": "Fixture Limited",
            "desc": "CRISIL Credit Rating Reaffirmation",
            "attchmntText": "Credit Rating",
            "attchmntFile": "https://nsearchives.nseindia.com/corporate/fixture.pdf",
            "an_dt": "01-Aug-2026 13:30:00", "seq_id": "NSE-1",
            "isin": "INE000A01000",
        }]

        def fetcher(url: str, exchange: str) -> FetchResponse:
            requested.append(url)
            self.assertEqual(exchange, "NSE")
            self.assertFalse(is_blocked_terms_target(url))
            body = json.dumps(payload).encode()
            return FetchResponse(
                requested_url=url, final_url=url, status_code=200, content=body,
                content_type="application/json", encoding="utf-8", etag=None,
                last_modified=None, auth_mode="public", fetch_status="ok",
            )

        first = run_document_recovery_worker(
            self.conn, sources=("nse",), limit=1, throttle_seconds=0,
            worker="fixture-worker", fetcher=fetcher, now=lambda: NOW,
            sleep=lambda _: None,
            discovery_snapshot_root=self.root / "worker-discovery",
        )
        second = run_document_recovery_worker(
            self.conn, sources=("nse",), limit=1, throttle_seconds=0,
            worker="fixture-worker", fetcher=fetcher, now=lambda: NOW,
            sleep=lambda _: None,
            discovery_snapshot_root=self.root / "worker-discovery",
        )
        self.assertEqual(first["processed_target_source_count"], 1)
        self.assertEqual(second["processed_target_source_count"], 0)
        self.assertTrue(requested)
        self.assertTrue(all(not is_blocked_terms_target(url) for url in requested))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM artifact_recovery_candidates").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM artifact_sources WHERE recovery_candidate_id IS NOT NULL"
            ).fetchone()[0],
            1,
        )

    def test_worker_keeps_multiple_plausible_candidates_ambiguous(self) -> None:
        payload = [
            {
                "symbol": "FIXTURE", "sm_name": "Fixture Limited",
                "desc": "CRISIL Credit Rating Reaffirmation",
                "attchmntText": "Credit Rating",
                "attchmntFile": f"https://nsearchives.nseindia.com/corporate/rating-{index}.pdf",
                "an_dt": "01-Aug-2026 13:30:00", "seq_id": f"NSE-{index}",
                "isin": "INE000A01000",
            }
            for index in (1, 2)
        ]

        def fetcher(url: str, exchange: str) -> FetchResponse:
            return FetchResponse(
                requested_url=url, final_url=url, status_code=200,
                content=json.dumps(payload).encode(), content_type="application/json",
                encoding="utf-8", etag=None, last_modified=None,
                auth_mode="public", fetch_status="ok",
            )

        result = run_document_recovery_worker(
            self.conn, sources=("nse",), limit=1, throttle_seconds=0,
            worker="fixture-worker", fetcher=fetcher, now=lambda: NOW,
            sleep=lambda _: None,
            discovery_snapshot_root=self.root / "ambiguous-discovery",
        )
        self.assertEqual(result["outcome_counts"], {"nse:AMBIGUOUS": 1})
        self.assertEqual(
            {
                row[0] for row in self.conn.execute(
                    "SELECT confidence FROM artifact_recovery_candidates"
                )
            },
            {"AMBIGUOUS"},
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM artifact_sources WHERE recovery_candidate_id IS NOT NULL"
            ).fetchone()[0],
            0,
        )

    def test_residual_report_reconciles_exactly(self) -> None:
        pending = build_alternate_source_recovery_report(
            self.conn, generated_at_utc=NOW
        )["summary"]
        self.assertEqual(pending["total_blocked_logical_artifacts"], 1)
        self.assertEqual(pending["accounted_total"], 1)
        self.assertEqual(pending["in_progress"], 1)
        with self.conn:
            self.conn.execute(
                """UPDATE artifact_recovery_targets
                      SET status='NO_OFFICIAL_SOURCE_FOUND' WHERE recovery_target_id=?""",
                (self.target_id,),
            )
        residual = build_alternate_source_recovery_report(
            self.conn, generated_at_utc=NOW
        )["summary"]
        self.assertEqual(residual["licensed_feed_or_written_permission_required"], 1)
        self.assertEqual(residual["crisil_residual"], 1)
        self.assertEqual(residual["accounted_total"], 1)

    def test_report_keeps_full_rationale_source_bucket_explicit(self) -> None:
        candidate_id = self._accepted_candidate()
        attach_recovery_candidate(self.conn, candidate_id=candidate_id,
                                  attached_at_utc=NOW)
        with self.conn:
            self.conn.execute(
                """UPDATE artifact_recovery_candidates
                      SET content_class='FULL_RATING_RATIONALE' WHERE candidate_id=?""",
                (candidate_id,),
            )
            self.conn.execute(
                """UPDATE artifact_recovery_targets
                      SET status='RECOVERED',basic_rating_coverage=1,
                          full_rationale_coverage=1 WHERE recovery_target_id=?""",
                (self.target_id,),
            )
        report = build_alternate_source_recovery_report(
            self.conn, generated_at_utc=NOW
        )["summary"]
        self.assertEqual(report["full_rationale_recovered"]["NSE"], 1)
        self.assertEqual(report["recovered_total"], 1)
        self.assertEqual(report["crisil_residual"], 0)


if __name__ == "__main__":
    unittest.main()
