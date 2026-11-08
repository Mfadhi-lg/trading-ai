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
from scrapper.db.document_download_repo import persist_download_result
from scrapper.documents.downloader import DownloadPolicy, DownloadResult
from scrapper.documents.media import MediaProbe, MediaStream
from scrapper.documents.transcription import EngineSegment, EngineTranscript
from scrapper.pipeline.normalize_recording import normalize_recording
from scrapper.pipeline.transcribe_recording import transcribe_recording


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "documents"
GOLDEN = ROOT / "golden" / "d07_machine_transcription_expected.json"
OBSERVED = "2026-08-05T17:00:00+00:00"
RECORDING_URL = "https://www.fixture.example/investors/q1-fy26-recording.wav"


class FixtureMediaTool:
    name = "fixture-normalizer"
    version = "1.0.0"
    probe_name = "fixture-probe"
    probe_version = "1.0.0"

    def __init__(self) -> None:
        self.calls = 0

    def options(self) -> dict[str, Any]:
        return {"fixture": True, "sample_rate_hz": 16000}

    def probe(self, path: Path) -> MediaProbe:
        self.calls += 1
        return MediaProbe(
            ("wav",),
            1250,
            (
                MediaStream(
                    0,
                    "audio",
                    "pcm_s16le",
                    1250,
                    44100 if self.calls == 1 else 16000,
                    2 if self.calls == 1 else 1,
                ),
            ),
        )

    def normalize_audio(self, source: Path, destination: Path) -> None:
        destination.write_bytes(b"RIFF\x08\x00\x00\x00WAVEdata")


class FixtureTranscriber:
    engine = "fixture-transcriber"
    engine_version = "1.0.0"
    model_version = "fixture-en@sha256:abc123"

    def __init__(self, result: EngineTranscript | None = None) -> None:
        self.calls = 0
        self.result = result or EngineTranscript(
            language="en",
            language_confidence="0.98",
            segments=(
                EngineSegment(
                    0,
                    600,
                    " Welcome to the quarterly earnings call.",
                    "0.91",
                    {
                        "average_log_probability": "-0.09",
                        "no_speech_probability": "0.01",
                    },
                ),
                EngineSegment(
                    650,
                    1200,
                    " Revenue grew during the quarter.",
                    "0.87",
                    {
                        "average_log_probability": "-0.14",
                        "no_speech_probability": "0.02",
                    },
                ),
            ),
        )

    def options(self) -> dict[str, Any]:
        return {"beam_size": 5, "language": None, "fixture": True}

    def transcribe(self, path: Path) -> EngineTranscript:
        self.calls += 1
        return self.result


class ExplodingTranscriber(FixtureTranscriber):
    def transcribe(self, path: Path) -> EngineTranscript:
        self.calls += 1
        raise AssertionError("transcriber must not be invoked")


class FailingTranscriber(FixtureTranscriber):
    def transcribe(self, path: Path) -> EngineTranscript:
        self.calls += 1
        raise RuntimeError("fixture engine failed")


class MachineTranscriptionD07Tests(unittest.TestCase):
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
            conn.commit()
        finally:
            conn.close()
        self.transcript_artifact_id, self.recording_artifact_id = self._artifacts()
        self.normalization = self._normalize_recording()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _blob(
        self, conn: sqlite3.Connection, *, body: bytes, media_type: str, name: str
    ) -> int:
        digest = hashlib.sha256(body).hexdigest()
        path = self.root / "seed" / digest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        conn.execute(
            """INSERT OR IGNORE INTO response_blobs
               (content_sha256, content_type, encoding, byte_size, local_path,
                first_observed_at_utc, created_at_utc)
               VALUES (?, ?, NULL, ?, ?, ?, ?)""",
            (digest, media_type, len(body), str(path), OBSERVED, OBSERVED),
        )
        return int(
            conn.execute(
                "SELECT blob_id FROM response_blobs WHERE content_sha256=?", (digest,)
            ).fetchone()[0]
        )

    def _artifacts(self) -> tuple[int, int]:
        conn = connect(self.db_path)
        try:
            with conn:
                transcript_id = int(
                    conn.execute(
                        """INSERT INTO document_artifacts
                           (company_id, artifact_key, artifact_type, publisher,
                            title_raw, event_date, period_end, fiscal_year,
                            fiscal_quarter, first_observed_at_utc,
                            acquisition_status, transcript_preference_status)
                           VALUES (?, 'fixture-q1-transcript', 'transcript',
                            'Fixture Limited', 'Q1 FY2026 Transcript', '2026-07-20',
                            '2026-06-30', 2026, 1, ?, 'discovered',
                            'official_candidate')""",
                        (self.company_id, OBSERVED),
                    ).lastrowid
                )
                recording_id = int(
                    conn.execute(
                        """INSERT INTO document_artifacts
                           (company_id, artifact_key, artifact_type, publisher,
                            title_raw, event_date, period_end, fiscal_year,
                            fiscal_quarter, first_observed_at_utc,
                            acquisition_status, transcript_preference_status)
                           VALUES (?, 'fixture-q1-recording', 'recording',
                            'Fixture Limited', 'Q1 FY2026 Recording', '2026-07-20',
                            '2026-06-30', 2026, 1, ?, 'discovered',
                            'recording_fallback_candidate')""",
                        (self.company_id, OBSERVED),
                    ).lastrowid
                )
                page_blob = self._blob(
                    conn, body=b"<html>recording</html>", media_type="text/html", name="ir.html"
                )
                page_id = int(
                    conn.execute(
                        """INSERT INTO artifact_discovery_pages
                           (company_id, discovered_from_source, discovery_page_url,
                            publisher_claim, blob_id, parser_name, parser_version,
                            first_observed_at_utc)
                           VALUES (?, 'company_ir', 'https://www.fixture.example/ir',
                            'Fixture Limited', ?, 'fixture', '1.0.0', ?)""",
                        (self.company_id, page_blob, OBSERVED),
                    ).lastrowid
                )
                self.source_id = int(
                    conn.execute(
                        """INSERT INTO artifact_sources
                           (artifact_id, discovery_page_id, discovered_from_source,
                            discovery_page_url, document_url, publisher_claim,
                            source_position, source_title_raw,
                            classification_evidence_json, is_primary_source,
                            first_observed_at_utc, last_checked_at_utc,
                            availability_status)
                           VALUES (?, ?, 'company_ir',
                            'https://www.fixture.example/ir', ?, 'Fixture Limited',
                            0, 'Q1 FY2026 Recording', '{}', 1, ?, ?, 'available')""",
                        (recording_id, page_id, RECORDING_URL, OBSERVED, OBSERVED),
                    ).lastrowid
                )
            return transcript_id, recording_id
        finally:
            conn.close()

    def _normalize_recording(self) -> dict[str, Any]:
        body = (FIXTURES / "d06_recording.wav").read_bytes()
        conn = connect(self.db_path)
        try:
            persisted = persist_download_result(
                conn,
                artifact_id=self.recording_artifact_id,
                artifact_source_ids=(self.source_id,),
                result=DownloadResult(
                    requested_url=RECORDING_URL,
                    final_url=RECORDING_URL,
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
                started_at_utc=OBSERVED,
                fetched_at_utc=OBSERVED,
                snapshot_root=self.root / "content",
            )
            self.source_content_id = int(persisted.content_id)
        finally:
            conn.close()
        return normalize_recording(
            artifact_id=self.recording_artifact_id,
            db_path=self.db_path,
            output=self.root / "normalization.json",
            media_tool=FixtureMediaTool(),
            now_utc=lambda: "2026-08-05T17:01:00+00:00",
            snapshot_root=self.root / "content",
        )

    def _transcribe(
        self,
        transcriber: FixtureTranscriber,
        *,
        comparison_requested: bool = False,
        name: str = "machine.json",
    ) -> dict[str, Any]:
        times = iter(("2026-08-05T17:02:00+00:00", "2026-08-05T17:02:01+00:00"))
        return transcribe_recording(
            artifact_id=self.transcript_artifact_id,
            recording_artifact_id=self.recording_artifact_id,
            comparison_requested=comparison_requested,
            transcriber=transcriber,
            db_path=self.db_path,
            output=self.root / name,
            now_utc=lambda: next(times),
            snapshot_root=self.root / "content",
        )

    def _add_official(self) -> int:
        conn = connect(self.db_path)
        try:
            with conn:
                blob_id = self._blob(
                    conn,
                    body=b"%PDF fixture official transcript",
                    media_type="application/pdf",
                    name="official.pdf",
                )
                content_id = register_artifact_content(
                    conn,
                    artifact_id=self.transcript_artifact_id,
                    blob_id=blob_id,
                    media_type="application/pdf",
                    is_official=True,
                    created_at_utc=OBSERVED,
                )
                extraction_id = int(
                    conn.execute(
                        """INSERT INTO document_extractions
                           (content_id, parser_name, parser_version, ocr_engine,
                            ocr_engine_version, options_json, started_at_utc,
                            completed_at_utc, extraction_status,
                            page_or_slide_count, native_page_count, ocr_page_count,
                            unusable_page_count, error_json, created_at_utc)
                           VALUES (?, 'fixture', '1.0.0', 'none', '1.0.0', '{}', ?,
                            ?, 'ok', 1, 1, 0, 0, '{}', ?)""",
                        (content_id, OBSERVED, OBSERVED, OBSERVED),
                    ).lastrowid
                )
                official_id = int(
                    conn.execute(
                        """INSERT INTO transcripts
                           (artifact_id, content_id, extraction_id, origin,
                            source_media_content_id, language, engine, model_version,
                            options_json, created_at_utc, validation_status,
                            segment_count, page_start, page_end,
                            prepared_segment_count, qa_segment_count,
                            unknown_segment_count, error_json)
                           VALUES (?, ?, ?, 'official', NULL, 'en', 'fixture',
                            '1.0.0', '{}', ?, 'accepted', 0, 1, 1, 0, 0, 0, '{}')""",
                        (
                            self.transcript_artifact_id,
                            content_id,
                            extraction_id,
                            OBSERVED,
                        ),
                    ).lastrowid
                )
                conn.execute(
                    """INSERT INTO preferred_transcripts
                       (artifact_id, transcript_id, updated_at_utc) VALUES (?, ?, ?)
                       ON CONFLICT(artifact_id) DO UPDATE SET
                         transcript_id=excluded.transcript_id,
                         updated_at_utc=excluded.updated_at_utc""",
                    (self.transcript_artifact_id, official_id, OBSERVED),
                )
                conn.execute(
                    """UPDATE document_artifacts SET preferred_content_id=?,
                       acquisition_status='validated',
                       transcript_preference_status='official_preferred'
                       WHERE artifact_id=?""",
                    (content_id, self.transcript_artifact_id),
                )
            return official_id
        finally:
            conn.close()

    def test_recording_only_event_yields_auditable_preferred_machine_transcript(self) -> None:
        engine = FixtureTranscriber()
        report = self._transcribe(engine)
        run = report["transcription"]
        transcript = run["transcript"]
        self.assertEqual(engine.calls, 1)
        self.assertEqual(run["run_status"], "accepted")
        self.assertEqual(transcript["origin"], "machine_generated")
        self.assertTrue(transcript["is_preferred"])
        self.assertEqual(report["artifact"]["transcript_preference_status"], "machine_preferred")
        self.assertEqual(
            [row["speaker_raw"] for row in transcript["segments"]],
            ["unknown", "unknown"],
        )
        self.assertEqual(
            [(row["start_ms"], row["end_ms"]) for row in transcript["segments"]],
            [(0, 600), (650, 1200)],
        )
        self.assertEqual(
            run["source_media"]["content_sha256"],
            self.normalization["normalization"]["normalized_audio"][
                "content_sha256"
            ],
        )
        self.assertTrue(all(row["status"] == "passed" for row in transcript["validations"]))

        projection = {
            "origin": transcript["origin"],
            "language": transcript["language"],
            "engine": transcript["engine"],
            "model_version": transcript["model_version"],
            "duration_coverage": run["duration_coverage"],
            "segments": [
                {
                    "position": row["segment_position"],
                    "range": [row["start_ms"], row["end_ms"]],
                    "speaker": row["speaker_raw"],
                    "text": row["text_raw"],
                    "confidence": row["confidence"],
                    "uncertainty": row["uncertainty"],
                }
                for row in transcript["segments"]
            ],
            "validation_codes": [row["rule_code"] for row in transcript["validations"]],
        }
        self.assertEqual(projection, json.loads(GOLDEN.read_text(encoding="utf-8")))

    def test_same_media_model_and_options_reuse_without_engine_call(self) -> None:
        first = self._transcribe(FixtureTranscriber(), name="first.json")
        second_engine = ExplodingTranscriber()
        second = self._transcribe(second_engine, name="second.json")
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(second_engine.calls, 0)
        self.assertEqual(
            first["transcription"]["transcription_run_id"],
            second["transcription"]["transcription_run_id"],
        )

    def test_usable_official_skips_engine_unless_comparison_requested(self) -> None:
        official_id = self._add_official()
        skipped_engine = ExplodingTranscriber()
        skipped = self._transcribe(skipped_engine, name="skipped.json")
        self.assertEqual(skipped_engine.calls, 0)
        self.assertIsNone(skipped["transcription"])
        self.assertEqual(skipped["gate"]["status"], "skipped_official_preferred")
        self.assertEqual(skipped["gate"]["official_transcript_id"], official_id)

        comparison = self._transcribe(
            FixtureTranscriber(), comparison_requested=True, name="comparison.json"
        )
        self.assertEqual(comparison["transcription"]["run_status"], "accepted")
        self.assertFalse(comparison["transcription"]["transcript"]["is_preferred"])
        self.assertEqual(
            comparison["artifact"]["transcript_preference_status"],
            "official_preferred",
        )

    def test_later_official_moves_preference_without_deleting_machine_evidence(self) -> None:
        machine = self._transcribe(FixtureTranscriber(), name="machine-first.json")
        machine_id = machine["transcription"]["transcript"]["transcript_id"]
        official_id = self._add_official()
        conn = connect(self.db_path)
        try:
            preferred = conn.execute(
                "SELECT transcript_id FROM preferred_transcripts WHERE artifact_id=?",
                (self.transcript_artifact_id,),
            ).fetchone()[0]
            self.assertEqual(preferred, official_id)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM transcripts WHERE transcript_id=?",
                    (machine_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM transcript_segments WHERE transcript_id=?",
                    (machine_id,),
                ).fetchone()[0],
                2,
            )
        finally:
            conn.close()

    def test_invalid_timeline_is_retained_but_not_preferred(self) -> None:
        result = EngineTranscript(
            "en",
            "0.9",
            (
                EngineSegment(0, 900, " First.", "0.8", {}),
                EngineSegment(800, 1301, " Overlap.", "0.7", {}),
            ),
        )
        report = self._transcribe(FixtureTranscriber(result), name="invalid.json")
        run = report["transcription"]
        self.assertEqual(run["run_status"], "failed")
        self.assertFalse(run["transcript"]["is_preferred"])
        failed = {
            row["rule_code"]
            for row in run["transcript"]["validations"]
            if row["status"] == "failed"
        }
        self.assertIn("machine_transcript_timestamps_monotonic", failed)
        self.assertIn("machine_transcript_within_media_duration", failed)

    def test_engine_failure_is_auditable_and_idempotent(self) -> None:
        failing = FailingTranscriber()
        first = self._transcribe(failing, name="failure.json")
        self.assertEqual(failing.calls, 1)
        self.assertEqual(first["transcription"]["run_status"], "failed")
        self.assertIsNone(first["transcription"]["transcript"])
        self.assertEqual(first["transcription"]["error"]["type"], "RuntimeError")
        reused = self._transcribe(ExplodingTranscriber(), name="failure-reused.json")
        self.assertTrue(reused["reused"])

    def test_reuse_reverifies_immutable_normalized_audio(self) -> None:
        first = self._transcribe(FixtureTranscriber(), name="before-tamper.json")
        path = Path(first["transcription"]["source_media"]["local_path"])
        original = path.read_bytes()
        path.write_bytes(b"X" + original[1:])
        with self.assertRaisesRegex(RuntimeError, "normalized audio content hash changed"):
            self._transcribe(ExplodingTranscriber(), name="tampered.json")


if __name__ == "__main__":
    unittest.main()
