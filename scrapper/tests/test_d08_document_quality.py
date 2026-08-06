from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import (
    persist_discovery_plan,
    register_artifact_content,
)
from scrapper.db.document_download_repo import persist_download_result
from scrapper.cli import main
from scrapper.documents.downloader import DownloadPolicy, DownloadResult
from scrapper.exports.document_quality_report import (
    build_document_quality_report,
    write_document_quality_report,
)
from scrapper.pipeline.extract_document import extract_document
from scrapper.pipeline.normalize_credit_rating import normalize_credit_rating_document
from scrapper.pipeline.normalize_recording import normalize_recording
from scrapper.pipeline.transcribe_recording import transcribe_recording
from scrapper.sources.documents.discovery import build_discovery_plan
from scrapper.tests.test_d03_document_extraction import FakeOcr
from scrapper.tests.test_d04_credit_rating_normalization import NoOcr
from scrapper.tests.test_d07_machine_transcription import (
    FixtureMediaTool,
    FixtureTranscriber,
)


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "documents"
DISCOVERY = FIXTURES / "d01_discovery_manifest.json"
GOLDEN = ROOT / "golden" / "d08_document_quality_expected.json"
OBSERVED = "2026-08-05T18:00:00+00:00"
GENERATED = "2026-08-05T18:10:00+00:00"


class DocumentQualityD08Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.db"
        self.snapshot_root = self.root / "snapshots"
        conn = connect(self.db_path)
        try:
            init_db(conn)
            persisted = persist_discovery_plan(
                conn,
                build_discovery_plan(DISCOVERY, requested_symbol="LAURUSLABS"),
                observed_at_utc=OBSERVED,
                snapshot_root=self.snapshot_root,
            )
            self.company_id = persisted.company_id
            self.artifacts = {
                str(row["artifact_type"]): int(row["artifact_id"])
                for row in conn.execute(
                    "SELECT artifact_id, artifact_type FROM document_artifacts"
                )
            }
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _content(
        self,
        artifact_id: int,
        fixture_name: str,
        media_type: str,
        *,
        is_official: bool = True,
    ) -> int:
        body = (FIXTURES / fixture_name).read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        destination = self.root / "content" / digest / fixture_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
        conn = connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    """INSERT OR IGNORE INTO response_blobs
                       (content_sha256, content_type, encoding, byte_size,
                        local_path, first_observed_at_utc, created_at_utc)
                       VALUES (?, ?, NULL, ?, ?, ?, ?)""",
                    (
                        digest,
                        media_type,
                        len(body),
                        str(destination),
                        OBSERVED,
                        OBSERVED,
                    ),
                )
                blob_id = int(
                    conn.execute(
                        "SELECT blob_id FROM response_blobs WHERE content_sha256=?",
                        (digest,),
                    ).fetchone()[0]
                )
                return register_artifact_content(
                    conn,
                    artifact_id=artifact_id,
                    blob_id=blob_id,
                    media_type=media_type,
                    is_official=is_official,
                    created_at_utc=OBSERVED,
                )
        finally:
            conn.close()

    def _accept_presentation_with_ocr(self) -> None:
        content_id = self._content(
            self.artifacts["presentation"],
            "d03_presentation.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        times = iter(("2026-08-05T18:01:00+00:00", "2026-08-05T18:01:01+00:00"))
        report = extract_document(
            artifact_id=self.artifacts["presentation"],
            content_id=content_id,
            db_path=self.db_path,
            output=self.root / "presentation.json",
            ocr_engine=FakeOcr((("SCANNED CUSTOMER GROWTH 42 PERCENT", "0.97"),)),
            now_utc=lambda: next(times),
        )
        self.assertEqual(report["extraction"]["extraction_status"], "ok")

    def _accept_credit_rating(self) -> None:
        content_id = self._content(
            self.artifacts["credit_rating"],
            "d04_credit_rating.pdf",
            "application/pdf",
        )
        times = iter(("2026-08-05T18:02:00+00:00", "2026-08-05T18:02:01+00:00"))
        extracted = extract_document(
            artifact_id=self.artifacts["credit_rating"],
            content_id=content_id,
            db_path=self.db_path,
            output=self.root / "rating-extraction.json",
            ocr_engine=NoOcr(),
            now_utc=lambda: next(times),
        )
        normalized_times = iter(
            ("2026-08-05T18:02:02+00:00", "2026-08-05T18:02:03+00:00")
        )
        rating = normalize_credit_rating_document(
            artifact_id=self.artifacts["credit_rating"],
            extraction_id=int(extracted["extraction"]["extraction_id"]),
            db_path=self.db_path,
            output=self.root / "rating.json",
            now_utc=lambda: next(normalized_times),
        )
        self.assertEqual(rating["normalization"]["normalization_status"], "accepted")

    def _accept_recording_fallback(self) -> None:
        recording_id = self.artifacts["recording"]
        conn = connect(self.db_path)
        try:
            source_id = int(
                conn.execute(
                    """SELECT artifact_source_id FROM artifact_sources
                       WHERE artifact_id=? AND is_primary_source=1
                       ORDER BY artifact_source_id LIMIT 1""",
                    (recording_id,),
                ).fetchone()[0]
            )
            url = str(
                conn.execute(
                    "SELECT document_url FROM artifact_sources WHERE artifact_source_id=?",
                    (source_id,),
                ).fetchone()[0]
            )
            body = (FIXTURES / "d06_recording.wav").read_bytes()
            persisted = persist_download_result(
                conn,
                artifact_id=recording_id,
                artifact_source_ids=(source_id,),
                result=DownloadResult(
                    requested_url=url,
                    final_url=url,
                    http_status=200,
                    response_headers={"content-type": "audio/wav"},
                    redirects=(),
                    declared_media_type="audio/wav",
                    detected_media_type="audio/wav",
                    body=body,
                    byte_size=len(body),
                    result_status="validated",
                    elapsed_ms=5,
                ),
                policy=DownloadPolicy(),
                started_at_utc="2026-08-05T18:03:00+00:00",
                fetched_at_utc="2026-08-05T18:03:01+00:00",
                snapshot_root=self.root / "recording-content",
            )
            self.assertIsNotNone(persisted.content_id)
        finally:
            conn.close()
        normalization = normalize_recording(
            artifact_id=recording_id,
            db_path=self.db_path,
            output=self.root / "recording.json",
            media_tool=FixtureMediaTool(),
            now_utc=iter(
                ("2026-08-05T18:03:02+00:00", "2026-08-05T18:03:03+00:00")
            ).__next__,
            snapshot_root=self.root / "recording-content",
        )
        self.assertEqual(normalization["normalization"]["normalization_status"], "accepted")
        transcription = transcribe_recording(
            artifact_id=self.artifacts["transcript"],
            recording_artifact_id=recording_id,
            transcriber=FixtureTranscriber(),
            db_path=self.db_path,
            output=self.root / "machine.json",
            now_utc=iter(
                ("2026-08-05T18:03:04+00:00", "2026-08-05T18:03:05+00:00")
            ).__next__,
            snapshot_root=self.root / "recording-content",
        )
        self.assertEqual(transcription["transcription"]["run_status"], "accepted")

    def _add_explicit_absence_and_blocked_extraction(self) -> None:
        conn = connect(self.db_path)
        try:
            with conn:
                absent_id = int(
                    conn.execute(
                        """INSERT INTO document_artifacts
                           (company_id, artifact_key, artifact_type, publisher,
                            title_raw, event_date, period_end, fiscal_year,
                            fiscal_quarter, first_observed_at_utc,
                            acquisition_status, transcript_preference_status)
                           VALUES (?, 'fy2025-presentation-not-offered', 'presentation',
                            'Laurus Labs Limited', 'FY2025 presentation',
                            '2025-05-01', '2025-03-31', 2025, 4, ?, 'discovered',
                            'not_applicable')""",
                        (self.company_id, OBSERVED),
                    ).lastrowid
                )
                page_id = int(
                    conn.execute(
                        """SELECT discovery_page_id FROM artifact_discovery_pages
                           WHERE company_id=? AND discovered_from_source='company_ir'
                           LIMIT 1""",
                        (self.company_id,),
                    ).fetchone()[0]
                )
                conn.execute(
                    """INSERT INTO artifact_sources
                       (artifact_id, discovery_page_id, discovered_from_source,
                        discovery_page_url, document_url, publisher_claim,
                        source_position, source_title_raw,
                        classification_evidence_json, is_primary_source,
                        first_observed_at_utc, last_checked_at_utc,
                        availability_status)
                       VALUES (?, ?, 'company_ir',
                        'https://www.lauruslabs.com/investors/financial-information/',
                        'https://www.lauruslabs.com/investors/fy2025/no-presentation',
                        'Laurus Labs Limited', 99, 'FY2025 presentation', '{}', 1,
                        ?, ?, 'not_offered')""",
                    (absent_id, page_id, OBSERVED, OBSERVED),
                )
                blocked_id = int(
                    conn.execute(
                        """INSERT INTO document_artifacts
                           (company_id, artifact_key, artifact_type, publisher,
                            title_raw, event_date, period_end, fiscal_year,
                            fiscal_quarter, first_observed_at_utc,
                            acquisition_status, transcript_preference_status)
                           VALUES (?, 'q4-fy2024-broken-presentation', 'presentation',
                            'Laurus Labs Limited', 'Q4 FY2024 presentation',
                            '2024-05-01', '2024-03-31', 2024, 4, ?, 'downloaded',
                            'not_applicable')""",
                        (self.company_id, OBSERVED),
                    ).lastrowid
                )
                # Demonstrate a persisted provenance disagreement without
                # rewriting either discovery URL or publisher claim away.
                conn.execute(
                    """UPDATE artifact_sources
                       SET publisher_claim='Conflicting Publisher Limited'
                       WHERE artifact_source_id=(
                         SELECT artifact_source_id FROM artifact_sources
                         WHERE artifact_id=? AND discovered_from_source='company_ir'
                         LIMIT 1
                       )""",
                    (self.artifacts["presentation"],),
                )
        finally:
            conn.close()
        malformed_content = self._content(
            blocked_id, "d03_malformed.pdf", "application/pdf"
        )
        times = iter(("2026-08-05T18:04:00+00:00", "2026-08-05T18:04:01+00:00"))
        report = extract_document(
            artifact_id=blocked_id,
            content_id=malformed_content,
            db_path=self.db_path,
            output=self.root / "failed-extraction.json",
            ocr_engine=NoOcr(),
            now_utc=lambda: next(times),
        )
        self.assertEqual(report["extraction"]["extraction_status"], "failed")

    def _complete_pilot(self) -> None:
        self._accept_presentation_with_ocr()
        self._accept_credit_rating()
        self._accept_recording_fallback()
        self._add_explicit_absence_and_blocked_extraction()

    def test_pilot_report_accounts_for_every_artifact_and_matches_golden(self) -> None:
        self.maxDiff = None
        self._complete_pilot()
        conn = connect(self.db_path)
        try:
            payload = build_document_quality_report(
                conn, symbols=("LAURUSLABS",), generated_at_utc=GENERATED
            )
            output = write_document_quality_report(
                conn,
                self.root / "document-quality.json",
                symbols=("LAURUSLABS",),
                generated_at_utc=GENERATED,
            )
        finally:
            conn.close()
        self.assertEqual(payload, json.loads(output.read_text(encoding="utf-8")))
        report_hash = payload.pop("report_sha256")
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.assertEqual(report_hash, hashlib.sha256(canonical).hexdigest())

        projection = {
            "coverage_status": payload["coverage_status"],
            "summary": payload["summary"],
            "pilot": payload["pilot"],
            "companies": payload["companies"],
            "period_coverage": payload["period_coverage"],
            "anomalies": payload["anomalies"],
            "artifacts": [
                {
                    "artifact_type": row["artifact_type"],
                    "title_raw": row["title_raw"],
                    "period_key": row["period_key"],
                    "outcome": row["outcome"],
                    "outcome_reason": row["outcome_reason"],
                    "official_transcript_status": row[
                        "official_transcript_status"
                    ],
                    "recording_fallback_used": row["recording_fallback_used"],
                }
                for row in payload["artifacts"]
            ],
        }
        self.assertEqual(
            projection, json.loads(GOLDEN.read_text(encoding="utf-8"))
        )
        self.assertEqual(
            {row["outcome"] for row in payload["artifacts"]},
            {"accepted", "explicitly_missing", "blocked"},
        )

    def test_machine_preference_without_d07_run_is_blocked_not_a_fallback(self) -> None:
        self._accept_presentation_with_ocr()
        self._accept_credit_rating()
        self._accept_recording_fallback()
        conn = connect(self.db_path)
        try:
            with conn:
                conn.execute("DELETE FROM machine_transcription_runs")
            payload = build_document_quality_report(
                conn, symbols=("LAURUSLABS",), generated_at_utc=GENERATED
            )
        finally:
            conn.close()
        transcript = next(
            row for row in payload["artifacts"] if row["artifact_type"] == "transcript"
        )
        self.assertEqual(transcript["outcome"], "blocked")
        self.assertEqual(
            transcript["outcome_reason"], "machine_transcription_provenance_missing"
        )
        self.assertFalse(transcript["recording_fallback_used"])

    def test_symbol_selection_fails_closed_for_unknown_or_uncovered_company(self) -> None:
        conn = connect(self.db_path)
        try:
            with self.assertRaisesRegex(ValueError, "Unknown document-report"):
                build_document_quality_report(conn, symbols=("UNKNOWN",))
            with conn:
                company_id = int(
                    conn.execute(
                        """INSERT INTO companies(current_legal_name, status)
                           VALUES ('Empty Limited', 'active')"""
                    ).lastrowid
                )
                conn.execute(
                    """INSERT INTO security_identifiers
                       (company_id, identifier_type, identifier_value, is_current)
                       VALUES (?, 'NSE', 'EMPTY', 1)""",
                    (company_id,),
                )
            with self.assertRaisesRegex(ValueError, "No document artifacts"):
                build_document_quality_report(conn, symbols=("EMPTY",))
        finally:
            conn.close()

    def test_cli_writes_complete_selected_pilot_report(self) -> None:
        self._complete_pilot()
        output = self.root / "cli-document-quality.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "document-quality-report",
                    "--symbol",
                    "LAURUSLABS",
                    "--db",
                    str(self.db_path),
                    "--output",
                    str(output),
                ]
            )
        event = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(event["coverage_status"], "complete")
        self.assertEqual(event["pilot_status"], "complete")
        self.assertEqual(event["summary"]["expected_artifact_count"], 6)
        self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
