from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import register_artifact_content
from scrapper.pipeline.extract_document import extract_document
from scrapper.pipeline.ingest_official_transcript import ingest_official_transcript
from scrapper.documents.transcript import parse_official_transcript


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "documents"
GOLDEN = ROOT / "golden" / "d05_official_transcript_expected.json"
OBSERVED = "2026-08-05T16:00:00+00:00"


class NoOcr:
    name = "fixture-ocr"
    version = "1.0.0"
    language = "eng"

    def extract_text(self, image: Any) -> tuple[str, str | None]:
        raise AssertionError("OCR must not run for the native transcript fixture")

    def options(self) -> dict[str, str]:
        return {"language": "eng", "config": "fixture"}


class OfficialTranscriptD05Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.db"
        conn = connect(self.db_path)
        try:
            init_db(conn, apply_migrations=True)
            self.company_id = int(
                conn.execute(
                    "INSERT INTO companies(current_legal_name, status) "
                    "VALUES ('Fixture Limited', 'active')"
                ).lastrowid
            )
            conn.execute(
                """INSERT INTO security_identifiers
                   (company_id, identifier_type, identifier_value, is_current)
                   VALUES (?, 'NSE', 'FIXTURE', 1)""",
                (self.company_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _artifact(self, key: str) -> int:
        conn = connect(self.db_path)
        try:
            with conn:
                return int(
                    conn.execute(
                        """INSERT INTO document_artifacts
                           (company_id, artifact_key, artifact_type, publisher,
                            title_raw, event_date, period_end, fiscal_year,
                            fiscal_quarter, first_observed_at_utc,
                            acquisition_status, transcript_preference_status)
                           VALUES (?, ?, 'transcript', 'Fixture Limited',
                            'Q1 FY2026 Earnings Call Transcript', '2026-07-20',
                            '2026-06-30', 2026, 1, ?, 'downloaded',
                            'official_candidate')""",
                        (self.company_id, key, OBSERVED),
                    ).lastrowid
                )
        finally:
            conn.close()

    def _blob(
        self, conn: sqlite3.Connection, *, body: bytes, media_type: str, name: str
    ) -> int:
        digest = hashlib.sha256(body).hexdigest()
        snapshot = self.root / digest / name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(body)
        conn.execute(
            """INSERT OR IGNORE INTO response_blobs
               (content_sha256, content_type, encoding, byte_size, local_path,
                first_observed_at_utc, created_at_utc)
               VALUES (?, ?, NULL, ?, ?, ?, ?)""",
            (digest, media_type, len(body), str(snapshot), OBSERVED, OBSERVED),
        )
        return int(
            conn.execute(
                "SELECT blob_id FROM response_blobs WHERE content_sha256=?", (digest,)
            ).fetchone()[0]
        )

    def _attach_and_extract(
        self, artifact_id: int, key: str, *, is_official: bool = True
    ) -> tuple[int, int, Path]:
        body = (FIXTURES / "d05_official_transcript.pdf").read_bytes()
        conn = connect(self.db_path)
        try:
            with conn:
                blob_id = self._blob(
                    conn,
                    body=body,
                    media_type="application/pdf",
                    name=f"{key}.pdf",
                )
                content_id = register_artifact_content(
                    conn,
                    artifact_id=artifact_id,
                    blob_id=blob_id,
                    media_type="application/pdf",
                    is_official=is_official,
                    created_at_utc=OBSERVED,
                )
                snapshot = Path(
                    conn.execute(
                        "SELECT local_path FROM response_blobs WHERE blob_id=?",
                        (blob_id,),
                    ).fetchone()[0]
                )
        finally:
            conn.close()
        times = iter(("2026-08-05T16:01:00+00:00", "2026-08-05T16:01:01+00:00"))
        extracted = extract_document(
            artifact_id=artifact_id,
            content_id=content_id,
            db_path=self.db_path,
            output=self.root / f"{key}-extraction.json",
            ocr_engine=NoOcr(),
            now_utc=lambda: next(times),
        )
        return content_id, int(extracted["extraction"]["extraction_id"]), snapshot

    def _extracted_artifact(
        self, key: str, *, is_official: bool = True
    ) -> tuple[int, int, int, Path]:
        artifact_id = self._artifact(key)
        content_id, extraction_id, snapshot = self._attach_and_extract(
            artifact_id, key, is_official=is_official
        )
        return artifact_id, content_id, extraction_id, snapshot

    def _ingest(self, artifact_id: int, extraction_id: int, key: str) -> dict[str, Any]:
        return ingest_official_transcript(
            artifact_id=artifact_id,
            extraction_id=extraction_id,
            db_path=self.db_path,
            output=self.root / f"{key}-transcript.json",
            now_utc=lambda: "2026-08-05T16:02:00+00:00",
        )

    def test_official_pdf_preserves_order_speakers_boundaries_and_page_ranges(self) -> None:
        artifact_id, content_id, extraction_id, _ = self._extracted_artifact("accepted")
        report = self._ingest(artifact_id, extraction_id, "accepted")
        transcript = report["transcript"]
        self.assertEqual(
            report["content"]["content_sha256"],
            "530bc24b991da866317c7c0859ca2d70e134479e3f18994f96e0406ffca3b83c",
        )
        self.assertEqual(transcript["origin"], "official")
        self.assertEqual(transcript["validation_status"], "accepted")
        self.assertTrue(transcript["is_preferred"])
        self.assertEqual(transcript["document_marker_page"], 1)
        self.assertEqual(transcript["segment_count"], 5)
        self.assertEqual(transcript["prepared_segment_count"], 2)
        self.assertEqual(transcript["qa_segment_count"], 3)
        self.assertEqual(transcript["unknown_segment_count"], 0)
        self.assertEqual(
            [(row["section_kind"], row["page_number"]) for row in transcript["boundaries"]],
            [("prepared_remarks", 2), ("qa", 3)],
        )
        self.assertEqual(
            [row["speaker_raw"] for row in transcript["segments"]],
            ["Moderator", "Asha Rao", "Moderator", "Ravi Shah - Analyst", "Asha Rao"],
        )
        spanning = transcript["segments"][1]
        self.assertEqual((spanning["page_start"], spanning["page_end"]), (2, 3))
        self.assertEqual(len(spanning["source_fragments"]), 2)
        self.assertIn("Revenue grew eighteen percent", spanning["text_raw"])
        self.assertEqual(spanning["speaker_raw"], spanning["speaker_normalized"])
        self.assertTrue(all(row["start_ms"] is None for row in transcript["segments"]))
        self.assertTrue(all(row["status"] == "passed" for row in transcript["validations"]))
        self.assertFalse(report["fallback"]["recording_transcription_needed"])
        self.assertEqual(report["fallback"]["preferred_origin"], "official")
        self.assertEqual(report["artifact"]["preferred_content_id"], content_id)
        self.assertEqual(
            report["artifact"]["transcript_preference_status"], "official_preferred"
        )
        conn = connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM current_transcript_segments WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone()[0],
                5,
            )
        finally:
            conn.close()

    def test_ingestion_is_idempotent_and_reverifies_immutable_bytes(self) -> None:
        artifact_id, _, extraction_id, snapshot = self._extracted_artifact("idempotent")
        first = self._ingest(artifact_id, extraction_id, "first")
        second = ingest_official_transcript(
            artifact_id=artifact_id,
            extraction_id=extraction_id,
            db_path=self.db_path,
            output=self.root / "second.json",
        )
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(
            first["transcript"]["transcript_id"], second["transcript"]["transcript_id"]
        )
        conn = connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0], 5)
        finally:
            conn.close()
        snapshot.write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "byte size changed"):
            ingest_official_transcript(
                artifact_id=artifact_id,
                extraction_id=extraction_id,
                db_path=self.db_path,
                output=self.root / "tampered.json",
            )

    def test_missing_prepared_boundary_requires_review_and_does_not_block_fallback(self) -> None:
        artifact_id, content_id, extraction_id, _ = self._extracted_artifact("review")
        conn = connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    """UPDATE document_page_elements SET text_raw='Discussion'
                       WHERE extraction_id=? AND page_or_slide_number=2
                         AND text_raw='Presentation'""",
                    (extraction_id,),
                )
        finally:
            conn.close()
        report = self._ingest(artifact_id, extraction_id, "review")
        transcript = report["transcript"]
        self.assertEqual(transcript["validation_status"], "manual_review")
        self.assertFalse(transcript["is_preferred"])
        self.assertGreater(transcript["unknown_segment_count"], 0)
        self.assertTrue(report["fallback"]["recording_transcription_needed"])
        self.assertIsNone(report["fallback"]["preferred_transcript_id"])
        self.assertIsNone(report["artifact"]["preferred_content_id"])
        self.assertEqual(
            report["artifact"]["transcript_preference_status"], "official_candidate"
        )
        self.assertNotEqual(content_id, report["artifact"]["preferred_content_id"])

    def test_missing_speaker_structure_is_failed_without_partial_preference(self) -> None:
        artifact_id, _, extraction_id, _ = self._extracted_artifact("failed")
        conn = connect(self.db_path)
        try:
            with conn:
                rows = conn.execute(
                    """SELECT page_or_slide_number, element_position, text_raw
                       FROM document_page_elements
                       WHERE extraction_id=? AND text_raw LIKE '%:%'""",
                    (extraction_id,),
                ).fetchall()
                for row in rows:
                    conn.execute(
                        """UPDATE document_page_elements SET text_raw=?
                           WHERE extraction_id=? AND page_or_slide_number=?
                             AND element_position=?""",
                        (
                            row["text_raw"].replace(":", " -", 1),
                            extraction_id,
                            row["page_or_slide_number"],
                            row["element_position"],
                        ),
                    )
        finally:
            conn.close()
        report = self._ingest(artifact_id, extraction_id, "failed")
        self.assertEqual(report["transcript"]["validation_status"], "failed")
        self.assertEqual(report["transcript"]["segment_count"], 0)
        self.assertFalse(report["transcript"]["is_preferred"])
        self.assertTrue(report["fallback"]["recording_transcription_needed"])

    def test_nonofficial_content_cannot_enter_official_ingestion(self) -> None:
        artifact_id, _, extraction_id, _ = self._extracted_artifact(
            "nonofficial", is_official=False
        )
        with self.assertRaisesRegex(ValueError, "no matching usable official"):
            self._ingest(artifact_id, extraction_id, "nonofficial")

    def test_sentence_colon_is_continuation_not_an_inferred_speaker(self) -> None:
        _, _, extraction_id, _ = self._extracted_artifact("colon-continuation")
        conn = connect(self.db_path)
        try:
            from scrapper.db.document_extraction_repo import extraction_payload

            extraction = extraction_payload(conn, extraction_id)
        finally:
            conn.close()
        continuation = extraction["pages"][2]["elements"][0]
        continuation["text_raw"] = "Our priorities are: revenue growth and margin expansion."
        parsed = parse_official_transcript(extraction)
        self.assertEqual(parsed.status, "accepted")
        self.assertEqual(len(parsed.segments), 5)
        self.assertEqual(parsed.segments[1].speaker_raw, "Asha Rao")
        self.assertIn("Our priorities are:", parsed.segments[1].text_raw)

    def test_official_transcript_supersedes_machine_without_deleting_evidence(self) -> None:
        artifact_id = self._artifact("preference")
        conn = connect(self.db_path)
        try:
            with conn:
                recording_id = int(
                    conn.execute(
                        """INSERT INTO document_artifacts
                           (company_id, artifact_key, artifact_type, publisher,
                            title_raw, event_date, period_end, first_observed_at_utc,
                            acquisition_status, transcript_preference_status)
                           VALUES (?, 'preference-recording', 'recording',
                            'Fixture Limited', 'Q1 FY2026 recording', '2026-07-20',
                            '2026-06-30', ?, 'validated',
                            'recording_fallback_candidate')""",
                        (self.company_id, OBSERVED),
                    ).lastrowid
                )
                media_blob = self._blob(
                    conn, body=b"fixture source media", media_type="audio/mpeg", name="media.mp3"
                )
                source_media_id = register_artifact_content(
                    conn,
                    artifact_id=recording_id,
                    blob_id=media_blob,
                    media_type="audio/mpeg",
                    is_official=True,
                    created_at_utc=OBSERVED,
                )
                generated_blob = self._blob(
                    conn,
                    body=b"machine generated transcript",
                    media_type="text/plain",
                    name="machine.txt",
                )
                machine_content_id = register_artifact_content(
                    conn,
                    artifact_id=artifact_id,
                    blob_id=generated_blob,
                    media_type="text/plain",
                    is_official=False,
                    content_origin="generated_derivative",
                    created_at_utc=OBSERVED,
                )
                machine_id = int(
                    conn.execute(
                        """INSERT INTO transcripts
                           (artifact_id, content_id, extraction_id, origin,
                            source_media_content_id, language, engine, model_version,
                            options_json, document_marker_raw, document_marker_page,
                            created_at_utc, validation_status, segment_count,
                            page_start, page_end, prepared_segment_count,
                            qa_segment_count, unknown_segment_count, error_json)
                           VALUES (?, ?, NULL, 'machine_generated', ?, 'en',
                            'fixture-machine', '1.0.0', '{}', NULL, NULL, ?,
                            'accepted', 1, NULL, NULL, 0, 0, 1, '{}')""",
                        (artifact_id, machine_content_id, source_media_id, OBSERVED),
                    ).lastrowid
                )
                conn.execute(
                    """INSERT INTO transcript_segments
                       (transcript_id, segment_position, page_start, page_end,
                        element_start, element_end, start_ms, end_ms, speaker_raw,
                        speaker_normalized, text_raw, confidence, section_kind,
                        text_origin, source_fragments_json)
                       VALUES (?, 0, NULL, NULL, NULL, NULL, 0, 1000, 'unknown',
                        'unknown', 'Machine text', '0.8', 'unknown',
                        'machine_generated', '[]')""",
                    (machine_id,),
                )
                conn.execute(
                    """INSERT INTO preferred_transcripts
                       (artifact_id, transcript_id, updated_at_utc) VALUES (?, ?, ?)""",
                    (artifact_id, machine_id, OBSERVED),
                )
                conn.execute(
                    """UPDATE document_artifacts SET preferred_content_id=?,
                       transcript_preference_status='machine_preferred'
                       WHERE artifact_id=?""",
                    (machine_content_id, artifact_id),
                )
        finally:
            conn.close()
        official_content_id, extraction_id, _ = self._attach_and_extract(
            artifact_id, "preference-official"
        )
        report = self._ingest(artifact_id, extraction_id, "preference-official")
        official_id = report["transcript"]["transcript_id"]
        self.assertEqual(report["fallback"]["preferred_origin"], "official")
        self.assertFalse(report["fallback"]["recording_transcription_needed"])
        self.assertEqual(report["artifact"]["preferred_content_id"], official_content_id)
        conn = connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0], 2)
            self.assertEqual(
                conn.execute(
                    "SELECT transcript_id FROM preferred_transcripts WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone()[0],
                official_id,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM transcript_segments WHERE transcript_id=?",
                    (machine_id,),
                ).fetchone()[0],
                1,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE preferred_transcripts SET transcript_id=? WHERE artifact_id=?",
                    (machine_id, artifact_id),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE document_artifacts SET preferred_content_id=? WHERE artifact_id=?",
                    (machine_content_id, artifact_id),
                )
        finally:
            conn.close()

    def test_accepted_projection_matches_golden(self) -> None:
        artifact_id, _, extraction_id, _ = self._extracted_artifact("golden")
        report = self._ingest(artifact_id, extraction_id, "golden")
        transcript = report["transcript"]
        projection = {
            "content_sha256": report["content"]["content_sha256"],
            "document_marker_raw": transcript["document_marker_raw"],
            "origin": transcript["origin"],
            "page_range": [transcript["page_start"], transcript["page_end"]],
            "boundaries": [
                [row["section_kind"], row["page_number"], row["label_raw"]]
                for row in transcript["boundaries"]
            ],
            "segments": [
                {
                    "position": row["segment_position"],
                    "section": row["section_kind"],
                    "speaker_raw": row["speaker_raw"],
                    "page_range": [row["page_start"], row["page_end"]],
                    "text_raw": row["text_raw"],
                    "fragment_pages": [
                        fragment["page_number"] for fragment in row["source_fragments"]
                    ],
                }
                for row in transcript["segments"]
            ],
            "validation_codes": [row["rule_code"] for row in transcript["validations"]],
            "recording_transcription_needed": report["fallback"][
                "recording_transcription_needed"
            ],
        }
        self.assertEqual(projection, json.loads(GOLDEN.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
