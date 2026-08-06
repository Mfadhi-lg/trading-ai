from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import register_artifact_content
from scrapper.db.document_download_repo import persist_download_result
from scrapper.documents.downloader import DownloadPolicy, DownloadResult
from scrapper.documents.media import (
    FfmpegMediaTool,
    MediaProbe,
    MediaStream,
)
from scrapper.pipeline.normalize_recording import normalize_recording


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "documents"
GOLDEN = ROOT / "golden" / "d06_recording_normalization_expected.json"
OBSERVED = "2026-08-05T16:30:00+00:00"
RECORDING_URL = "https://www.fixture.example/investors/q1-fy26-call-recording.wav"


class FixtureMediaTool:
    name = "fixture-normalizer"
    version = "1.0.0"
    probe_name = "fixture-probe"
    probe_version = "1.0.0"

    def __init__(self, source_probe: MediaProbe, normalized_probe: MediaProbe) -> None:
        self.source_probe = source_probe
        self.normalized_probe = normalized_probe
        self.probe_calls = 0
        self.normalize_calls = 0

    def options(self) -> dict[str, Any]:
        return {"fixture": True, "codec": "pcm_s16le", "sample_rate_hz": 16000}

    def probe(self, path: Path) -> MediaProbe:
        self.probe_calls += 1
        return self.source_probe if self.probe_calls == 1 else self.normalized_probe

    def normalize_audio(self, source: Path, destination: Path) -> None:
        self.normalize_calls += 1
        destination.write_bytes(b"RIFF\x08\x00\x00\x00WAVEdata")


class ExplodingReuseTool:
    def __init__(self, report: dict[str, Any]) -> None:
        normalization = report["normalization"]
        self.name = normalization["normalizer_engine"]
        self.version = normalization["normalizer_engine_version"]
        self.probe_name = normalization["probe_engine"]
        self.probe_version = normalization["probe_engine_version"]
        self._options = normalization["options"]

    def options(self) -> dict[str, Any]:
        return self._options

    def probe(self, path: Path) -> MediaProbe:
        raise AssertionError("idempotent reuse must not probe media")

    def normalize_audio(self, source: Path, destination: Path) -> None:
        raise AssertionError("idempotent reuse must not invoke normalization")


class RecordingNormalizationD06Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.db"
        conn = connect(self.db_path)
        try:
            init_db(conn)
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

    def _response_blob(
        self,
        conn: sqlite3.Connection,
        *,
        body: bytes,
        media_type: str,
        name: str,
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

    def _artifact_and_primary_source(self, key: str) -> tuple[int, int]:
        conn = connect(self.db_path)
        try:
            with conn:
                page_body = f"<html><body>{key}</body></html>".encode()
                page_blob_id = self._response_blob(
                    conn,
                    body=page_body,
                    media_type="text/html",
                    name=f"{key}.html",
                )
                artifact_id = int(
                    conn.execute(
                        """INSERT INTO document_artifacts
                           (company_id, artifact_key, artifact_type, publisher,
                            title_raw, event_date, period_end, fiscal_year,
                            fiscal_quarter, first_observed_at_utc,
                            acquisition_status, transcript_preference_status)
                           VALUES (?, ?, 'recording', 'Fixture Limited',
                            'Q1 FY2026 Earnings Call Recording', '2026-07-20',
                            '2026-06-30', 2026, 1, ?, 'discovered',
                            'recording_fallback_candidate')""",
                        (self.company_id, key, OBSERVED),
                    ).lastrowid
                )
                discovery_page_id = int(
                    conn.execute(
                        """INSERT INTO artifact_discovery_pages
                           (company_id, discovered_from_source, discovery_page_url,
                            publisher_claim, blob_id, parser_name, parser_version,
                            first_observed_at_utc)
                           VALUES (?, 'company_ir', ?, 'Fixture Limited', ?,
                            'fixture-discovery', '1.0.0', ?)""",
                        (
                            self.company_id,
                            f"https://www.fixture.example/investors/{key}",
                            page_blob_id,
                            OBSERVED,
                        ),
                    ).lastrowid
                )
                source_id = int(
                    conn.execute(
                        """INSERT INTO artifact_sources
                           (artifact_id, discovery_page_id, discovered_from_source,
                            discovery_page_url, document_url, publisher_claim,
                            source_position, source_title_raw,
                            classification_evidence_json, is_primary_source,
                            first_observed_at_utc, last_checked_at_utc,
                            availability_status)
                           VALUES (?, ?, 'company_ir', ?, ?, 'Fixture Limited', 0,
                            'Q1 FY2026 Earnings Call Recording', '{}', 1, ?, ?,
                            'available')""",
                        (
                            artifact_id,
                            discovery_page_id,
                            f"https://www.fixture.example/investors/{key}",
                            RECORDING_URL,
                            OBSERVED,
                            OBSERVED,
                        ),
                    ).lastrowid
                )
            return artifact_id, source_id
        finally:
            conn.close()

    def _download(self, key: str = "accepted") -> tuple[int, int]:
        artifact_id, source_id = self._artifact_and_primary_source(key)
        body = (FIXTURES / "d06_recording.wav").read_bytes()
        result = DownloadResult(
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
            elapsed_ms=25,
        )
        conn = connect(self.db_path)
        try:
            persisted = persist_download_result(
                conn,
                artifact_id=artifact_id,
                artifact_source_ids=(source_id,),
                result=result,
                policy=DownloadPolicy(),
                started_at_utc=OBSERVED,
                fetched_at_utc="2026-08-05T16:30:01+00:00",
                snapshot_root=self.root / "content",
            )
            return artifact_id, int(persisted.content_id)
        finally:
            conn.close()

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "D06 integration fixture requires local FFmpeg tools",
    )
    def test_recording_only_fixture_derives_deterministic_linked_audio(self) -> None:
        artifact_id, source_content_id = self._download()
        times = iter(("2026-08-05T16:31:00+00:00", "2026-08-05T16:31:01+00:00"))
        report = normalize_recording(
            artifact_id=artifact_id,
            db_path=self.db_path,
            output=self.root / "accepted.json",
            now_utc=lambda: next(times),
            snapshot_root=self.root / "content",
        )
        normalization = report["normalization"]
        self.assertEqual(normalization["normalization_status"], "accepted")
        self.assertEqual(normalization["source"]["content_id"], source_content_id)
        self.assertTrue(normalization["source"]["is_official"])
        self.assertEqual(normalization["source"]["content_origin"], "publisher_original")
        self.assertEqual(normalization["source"]["duration_ms"], 1250)
        self.assertEqual(normalization["source"]["streams"][0]["sample_rate_hz"], 44100)
        self.assertEqual(normalization["source"]["streams"][0]["channel_count"], 2)
        derived = normalization["normalized_audio"]
        self.assertEqual(derived["media_type"], "audio/wav")
        self.assertFalse(derived["is_official"])
        self.assertEqual(derived["content_origin"], "generated_derivative")
        self.assertEqual(derived["duration_ms"], 1250)
        self.assertEqual(derived["streams"][0]["codec_name"], "pcm_s16le")
        self.assertEqual(derived["streams"][0]["sample_rate_hz"], 16000)
        self.assertEqual(derived["streams"][0]["channel_count"], 1)
        self.assertEqual(normalization["relationship"]["relationship_type"], "normalized_audio")
        self.assertTrue(all(row["status"] == "passed" for row in normalization["validations"]))
        self.assertEqual(report["artifact"]["preferred_content_id"], source_content_id)
        self.assertNotEqual(source_content_id, derived["content_id"])

        actual = {
            "schema_version": report["schema_version"],
            "normalization_status": normalization["normalization_status"],
            "resolution_basis": normalization["resolution_basis"],
            "source": {
                "media_type": normalization["source"]["media_type"],
                "content_sha256": normalization["source"]["content_sha256"],
                "duration_ms": normalization["source"]["duration_ms"],
                "container_formats": normalization["source"]["container_formats"],
                "codec_name": normalization["source"]["streams"][0]["codec_name"],
                "sample_rate_hz": normalization["source"]["streams"][0]["sample_rate_hz"],
                "channel_count": normalization["source"]["streams"][0]["channel_count"],
                "resolution_path_count": len(normalization["source"]["resolution_paths"]),
            },
            "normalized_audio": {
                "media_type": derived["media_type"],
                "content_sha256": derived["content_sha256"],
                "byte_size": derived["byte_size"],
                "duration_ms": derived["duration_ms"],
                "container_formats": derived["container_formats"],
                "codec_name": derived["streams"][0]["codec_name"],
                "sample_rate_hz": derived["streams"][0]["sample_rate_hz"],
                "channel_count": derived["streams"][0]["channel_count"],
            },
            "relationship_type": normalization["relationship"]["relationship_type"],
            "validation_rule_count": len(normalization["validations"]),
            "preferred_content_is_source": report["artifact"]["preferred_content_id"]
            == source_content_id,
        }
        self.assertEqual(actual, json.loads(GOLDEN.read_text(encoding="utf-8")))

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "D06 integration fixture requires local FFmpeg tools",
    )
    def test_same_media_tool_and_options_reuse_without_reprocessing_and_reverify_bytes(self) -> None:
        artifact_id, _ = self._download("idempotent")
        report = normalize_recording(
            artifact_id=artifact_id,
            db_path=self.db_path,
            output=self.root / "first.json",
            snapshot_root=self.root / "content",
        )
        reused = normalize_recording(
            artifact_id=artifact_id,
            db_path=self.db_path,
            output=self.root / "second.json",
            media_tool=ExplodingReuseTool(report),
            snapshot_root=self.root / "content",
        )
        self.assertTrue(reused["reused"])
        self.assertEqual(
            report["normalization"]["normalization_id"],
            reused["normalization"]["normalization_id"],
        )
        conn = connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM recording_normalizations").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM artifact_content_relationships").fetchone()[0], 1)
            derived_path = Path(reused["normalization"]["normalized_audio"]["local_path"])
            original = derived_path.read_bytes()
            derived_path.write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "Normalized audio byte size changed"):
                normalize_recording(
                    artifact_id=artifact_id,
                    db_path=self.db_path,
                    output=self.root / "tampered.json",
                    media_tool=ExplodingReuseTool(report),
                    snapshot_root=self.root / "content",
                )
            derived_path.write_bytes(original)
            source_path = Path(reused["normalization"]["source"]["local_path"])
            source_original = source_path.read_bytes()
            source_path.write_bytes(b"X" + source_original[1:])
            with self.assertRaisesRegex(RuntimeError, "recording content hash changed"):
                normalize_recording(
                    artifact_id=artifact_id,
                    db_path=self.db_path,
                    output=self.root / "source-tampered.json",
                    media_tool=ExplodingReuseTool(report),
                    snapshot_root=self.root / "content",
                )
            source_path.write_bytes(source_original)
        finally:
            conn.close()

    def test_video_only_source_fails_validation_and_cannot_create_derivative(self) -> None:
        artifact_id, _ = self._download("no-audio")
        source_probe = MediaProbe(
            container_formats=("mp4",),
            duration_ms=1000,
            streams=(MediaStream(0, "video", "h264", 1000, width=320, height=180),),
        )
        normalized_probe = MediaProbe(
            container_formats=("wav",),
            duration_ms=1000,
            streams=(MediaStream(0, "audio", "pcm_s16le", 1000, 16000, 1),),
        )
        tool = FixtureMediaTool(source_probe, normalized_probe)
        times = iter(("2026-08-05T16:32:00+00:00", "2026-08-05T16:32:01+00:00"))
        report = normalize_recording(
            artifact_id=artifact_id,
            db_path=self.db_path,
            output=self.root / "failed.json",
            media_tool=tool,
            now_utc=lambda: next(times),
            snapshot_root=self.root / "content",
        )
        normalization = report["normalization"]
        self.assertEqual(normalization["normalization_status"], "failed")
        self.assertIsNone(normalization["normalized_audio"])
        failed = {row["rule_code"] for row in normalization["validations"] if row["status"] == "failed"}
        self.assertIn("recording_source_audio_stream_present", failed)
        self.assertEqual(tool.normalize_calls, 1)
        conn = connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM artifact_content_relationships").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM artifact_contents").fetchone()[0], 1)
        finally:
            conn.close()

    def test_content_without_validated_primary_download_is_not_resolvable(self) -> None:
        conn = connect(self.db_path)
        try:
            with conn:
                artifact_id = int(
                    conn.execute(
                        """INSERT INTO document_artifacts
                           (company_id, artifact_key, artifact_type, publisher,
                            title_raw, first_observed_at_utc, acquisition_status,
                            transcript_preference_status)
                           VALUES (?, 'unproven', 'recording', 'Fixture Limited',
                            'Unproven Recording', ?, 'downloaded',
                            'recording_fallback_candidate')""",
                        (self.company_id, OBSERVED),
                    ).lastrowid
                )
                body = (FIXTURES / "d06_recording.wav").read_bytes()
                blob_id = self._response_blob(
                    conn, body=body, media_type="audio/wav", name="unproven.wav"
                )
                register_artifact_content(
                    conn,
                    artifact_id=artifact_id,
                    blob_id=blob_id,
                    media_type="audio/wav",
                    is_official=True,
                    created_at_utc=OBSERVED,
                )
        finally:
            conn.close()
        with self.assertRaisesRegex(ValueError, "no validated official primary-source recording"):
            normalize_recording(
                artifact_id=artifact_id,
                db_path=self.db_path,
                output=self.root / "unproven.json",
                media_tool=FixtureMediaTool(
                    MediaProbe(("wav",), 1000, (MediaStream(0, "audio", "pcm_s16le", 1000, 44100, 2),)),
                    MediaProbe(("wav",), 1000, (MediaStream(0, "audio", "pcm_s16le", 1000, 16000, 1),)),
                ),
            )


if __name__ == "__main__":
    unittest.main()
