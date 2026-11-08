from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import (
    persist_discovery_plan,
    register_artifact_content,
    set_preferred_artifact_content,
)
from scrapper.pipeline.discover_documents import discover_documents
from scrapper.sources.documents.discovery import build_discovery_plan


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "documents" / "d01_discovery_manifest.json"
GOLDEN = ROOT / "golden" / "d01_discovery_expected.json"
OBSERVED = "2026-08-05T10:00:00+00:00"


class DocumentDiscoveryD01Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.db"
        self.snapshots = self.root / "snapshots"
        self.plan = build_discovery_plan(FIXTURE, requested_symbol="LAURUSLABS")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_saved_pages_produce_source_faithful_golden_candidate_plan(self) -> None:
        payload = self.plan.to_dict(dry_run=True)
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        actual = {
            **payload["summary"],
            "first_document_url": payload["candidates"][0]["document_url"],
            "last_document_url": payload["candidates"][-1]["document_url"],
        }
        self.assertEqual(actual, golden)
        self.assertEqual(
            [page["ignored_link_count"] for page in payload["pages"]],
            [1, 0, 1, 1],
        )
        self.assertEqual(
            [candidate["source_position"] for candidate in payload["candidates"]],
            [0, 1, 0, 1, 2, 0, 0, 1],
        )
        screener = [
            row
            for row in payload["candidates"]
            if row["discovered_from_source"] == "screener"
        ]
        self.assertTrue(screener)
        self.assertTrue(all(row["publisher_claim"] is None for row in screener))
        self.assertTrue(all(not row["is_primary_source"] for row in screener))
        self.assertEqual(
            len(
                {
                    row["artifact_key"]
                    for row in payload["candidates"]
                    if row["artifact_type"] == "presentation"
                }
            ),
            1,
        )

    def test_dry_run_is_database_snapshot_and_fixture_write_free(self) -> None:
        fixture_hash_before = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        payload = discover_documents(
            symbol="LAURUSLABS",
            manifest_path=FIXTURE,
            dry_run=True,
            db_path=self.db_path,
            snapshot_root=self.snapshots,
        )
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["persistence"]["status"], "not_written")
        self.assertFalse(self.db_path.exists())
        self.assertFalse(self.snapshots.exists())
        self.assertEqual(
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), fixture_hash_before
        )

    def test_primary_paths_and_screener_crosscheck_persist_idempotently(self) -> None:
        conn = connect(self.db_path)
        try:
            init_db(conn, apply_migrations=True)
            first = persist_discovery_plan(
                conn,
                self.plan,
                observed_at_utc=OBSERVED,
                snapshot_root=self.snapshots,
            )
            first_rows = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "companies",
                    "response_blobs",
                    "artifact_discovery_pages",
                    "document_artifacts",
                    "artifact_sources",
                    "artifact_contents",
                )
            }
            second = persist_discovery_plan(
                conn,
                self.plan,
                observed_at_utc="2026-08-05T11:00:00+00:00",
                snapshot_root=self.snapshots,
            )
            second_rows = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in first_rows
            }
            self.assertEqual(
                first_rows,
                {
                    "companies": 1,
                    "response_blobs": 4,
                    "artifact_discovery_pages": 4,
                    "document_artifacts": 4,
                    "artifact_sources": 8,
                    "artifact_contents": 0,
                },
            )
            self.assertEqual(second_rows, first_rows)
            self.assertEqual(first.created_artifact_count, 4)
            self.assertEqual(first.created_source_count, 8)
            self.assertEqual(second.created_artifact_count, 0)
            self.assertEqual(second.created_source_count, 0)
            self.assertEqual(second.reused_source_count, 8)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM artifact_sources WHERE is_primary_source=1"
                ).fetchone()[0],
                6,
            )
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM artifact_sources
                       WHERE discovered_from_source='screener'
                         AND publisher_claim IS NULL"""
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(DISTINCT artifact_id) FROM artifact_sources
                       WHERE is_primary_source=1"""
                ).fetchone()[0],
                4,
            )
            presentation_sources = conn.execute(
                """SELECT source.discovered_from_source, source.document_url
                   FROM artifact_sources AS source
                   JOIN document_artifacts AS artifact USING (artifact_id)
                   WHERE artifact.artifact_type='presentation'
                   ORDER BY source.artifact_source_id"""
            ).fetchall()
            self.assertEqual(len(presentation_sources), 3)
            self.assertEqual(
                {row["discovered_from_source"] for row in presentation_sources},
                {"bse", "company_ir", "screener"},
            )
            self.assertEqual(
                conn.execute(
                    "SELECT MIN(first_observed_at_utc) FROM artifact_sources"
                ).fetchone()[0],
                OBSERVED,
            )
        finally:
            conn.close()

    def test_content_rows_are_immutable_and_only_preferred_pointer_moves(self) -> None:
        conn = connect(self.db_path)
        try:
            init_db(conn, apply_migrations=True)
            persisted = persist_discovery_plan(
                conn,
                self.plan,
                observed_at_utc=OBSERVED,
                snapshot_root=self.snapshots,
            )
            transcript_id = conn.execute(
                "SELECT artifact_id FROM document_artifacts WHERE artifact_type='transcript'"
            ).fetchone()[0]
            presentation_id = conn.execute(
                "SELECT artifact_id FROM document_artifacts WHERE artifact_type='presentation'"
            ).fetchone()[0]

            def add_blob(content: bytes, position: int) -> int:
                digest = hashlib.sha256(content).hexdigest()
                cursor = conn.execute(
                    """INSERT INTO response_blobs
                       (content_sha256, content_type, encoding, byte_size,
                        local_path, first_observed_at_utc, created_at_utc)
                       VALUES (?, 'application/pdf', NULL, ?, ?, ?, ?)""",
                    (
                        digest,
                        len(content),
                        str(self.root / f"document-{position}.pdf"),
                        OBSERVED,
                        OBSERVED,
                    ),
                )
                return int(cursor.lastrowid)

            blob_one = add_blob(b"first official transcript", 1)
            blob_two = add_blob(b"corrected official transcript", 2)
            content_one = register_artifact_content(
                conn,
                artifact_id=transcript_id,
                blob_id=blob_one,
                media_type="application/pdf",
                is_official=True,
                created_at_utc=OBSERVED,
            )
            self.assertEqual(
                register_artifact_content(
                    conn,
                    artifact_id=transcript_id,
                    blob_id=blob_one,
                    media_type="application/pdf",
                    is_official=True,
                ),
                content_one,
            )
            content_two = register_artifact_content(
                conn,
                artifact_id=transcript_id,
                blob_id=blob_two,
                media_type="application/pdf",
                is_official=True,
                created_at_utc="2026-08-05T12:00:00+00:00",
            )
            set_preferred_artifact_content(
                conn, artifact_id=transcript_id, content_id=content_one
            )
            set_preferred_artifact_content(
                conn, artifact_id=transcript_id, content_id=content_two
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM artifact_contents WHERE artifact_id=?",
                    (transcript_id,),
                ).fetchone()[0],
                2,
            )
            preferred = conn.execute(
                """SELECT preferred_content_id, transcript_preference_status
                   FROM document_artifacts WHERE artifact_id=?""",
                (transcript_id,),
            ).fetchone()
            self.assertEqual(preferred["preferred_content_id"], content_two)
            self.assertEqual(preferred["transcript_preference_status"], "official_preferred")
            shared_content = register_artifact_content(
                conn,
                artifact_id=presentation_id,
                blob_id=blob_one,
                media_type="application/pdf",
                is_official=True,
            )
            self.assertNotEqual(shared_content, content_one)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM artifact_contents WHERE blob_id=?",
                    (blob_one,),
                ).fetchone()[0],
                2,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE document_artifacts SET preferred_content_id=? WHERE artifact_id=?",
                    (content_two, presentation_id),
                )
            self.assertEqual(len(set(persisted.artifact_ids)), 4)
        finally:
            conn.close()

    def test_manifest_identity_and_declared_type_drift_fail_closed(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["symbol"] = "WRONG"
        wrong_symbol = self.root / "wrong-symbol.json"
        wrong_symbol.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_discovery_plan(wrong_symbol, requested_symbol="LAURUSLABS")

        original_page = ROOT / "fixtures" / "documents" / "d01_bse_filings.html"
        changed_page = self.root / "changed.html"
        changed_page.write_text(
            original_page.read_text(encoding="utf-8").replace(
                'data-artifact-type="presentation"',
                'data-artifact-type="spreadsheet"',
                1,
            ),
            encoding="utf-8",
        )
        drift = {
            "schema_version": "1.0.0",
            "symbol": "LAURUSLABS",
            "company_name": "Laurus Labs Limited",
            "pages": [
                {
                    "source": "bse",
                    "page_url": "https://www.bseindia.com/filings",
                    "content_path": "changed.html",
                }
            ],
        }
        drift_path = self.root / "drift.json"
        drift_path.write_text(json.dumps(drift), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unknown data-artifact-type"):
            build_discovery_plan(drift_path, requested_symbol="LAURUSLABS")


if __name__ == "__main__":
    unittest.main()
