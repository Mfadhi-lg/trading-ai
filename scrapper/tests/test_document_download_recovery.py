from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scrapper.db.database import connect, init_db
from scrapper.config import MIGRATIONS_PATH
from scrapper.db.document_artifact_repo import (
    persist_discovery_plan,
    register_artifact_content,
)
from scrapper.db.document_download_queue_repo import materialize_download_queue
from scrapper.documents.download_scope import register_download_scope
from scrapper.exports.document_storage_projection import (
    build_document_storage_projection,
)
from scrapper.pipeline.document_download_recovery import (
    build_document_recovery_report,
    import_manual_document,
    register_replacement_document_url,
    requeue_document_downloads,
)
from scrapper.sources.documents.discovery import build_discovery_plan


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "fixtures" / "documents" / "d01_discovery_manifest.json"
PDF = ROOT / "fixtures" / "documents" / "d02_sample.pdf"
NOW = "2026-08-15T10:00:00+00:00"


class DocumentDownloadRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "scraper.db")
        init_db(self.conn, apply_migrations=True)
        persist_discovery_plan(
            self.conn,
            build_discovery_plan(MANIFEST, requested_symbol="LAURUSLABS"),
            observed_at_utc=NOW,
            snapshot_root=self.root / "discovery",
        )
        artifact_ids = tuple(
            int(row["artifact_id"])
            for row in self.conn.execute(
                """SELECT artifact_id FROM document_artifacts
                    WHERE artifact_type IN ('transcript', 'presentation')
                    ORDER BY artifact_id"""
            )
        )
        register_download_scope(
            self.conn,
            scope_id="recovery-scope",
            companies=("LAURUSLABS",),
            artifact_types=("transcript", "presentation"),
            artifact_ids=artifact_ids,
            enforce_pilot_composition=False,
            require_explicit_artifacts=False,
            created_at_utc=NOW,
        )
        materialize_download_queue(
            self.conn, scope_id="recovery-scope", created_at_utc=NOW
        )

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def _terminal_job(self):
        row = self.conn.execute(
            """SELECT queue.*, source.artifact_source_id
                 FROM artifact_download_queue AS queue
                 JOIN artifact_sources AS source
                   ON source.artifact_id=queue.artifact_id
                  AND source.document_url=queue.requested_url
                WHERE queue.scope_id='recovery-scope'
                ORDER BY queue.download_job_id LIMIT 1"""
        ).fetchone()
        with self.conn:
            self.conn.execute(
                """UPDATE artifact_download_queue
                      SET status='TERMINAL_FAILURE', attempt_count=1,
                          last_result_status='rejected_http_status',
                          last_error='http_404'
                    WHERE download_job_id=?""",
                (int(row["download_job_id"]),),
            )
        return row

    def _add_content(self, artifact_id: int, body: bytes) -> int:
        digest = hashlib.sha256(body).hexdigest()
        path = self.root / f"{digest}.pdf"
        path.write_bytes(body)
        with self.conn:
            self.conn.execute(
                """INSERT OR IGNORE INTO response_blobs
                   (content_sha256, content_type, encoding, byte_size, local_path,
                    first_observed_at_utc, created_at_utc)
                   VALUES (?, 'application/pdf', NULL, ?, ?, ?, ?)""",
                (digest, len(body), str(path), NOW, NOW),
            )
            blob_id = int(
                self.conn.execute(
                    "SELECT blob_id FROM response_blobs WHERE content_sha256=?",
                    (digest,),
                ).fetchone()[0]
            )
            return register_artifact_content(
                self.conn,
                artifact_id=artifact_id,
                blob_id=blob_id,
                media_type="application/pdf",
                is_official=True,
                created_at_utc=NOW,
            )

    def test_report_and_explicit_requeue_are_audited(self) -> None:
        job = self._terminal_job()
        report = build_document_recovery_report(
            self.conn, scope_id="recovery-scope"
        )
        item = next(
            row for row in report["jobs"]
            if row["download_job_id"] == int(job["download_job_id"])
        )
        self.assertEqual(item["suggested_action"], "REDISCOVER_REPLACEMENT_URL")

        preview = requeue_document_downloads(
            self.conn,
            queue_kind="generic",
            job_ids=(int(job["download_job_id"]),),
            reason_code="OPERATOR_VERIFIED",
            dry_run=True,
        )
        self.assertTrue(preview["dry_run"])
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM artifact_download_queue WHERE download_job_id=?",
                (int(job["download_job_id"]),),
            ).fetchone()[0],
            "TERMINAL_FAILURE",
        )

        applied = requeue_document_downloads(
            self.conn,
            queue_kind="generic",
            job_ids=(int(job["download_job_id"]),),
            reason_code="OPERATOR_VERIFIED",
            reason_detail="Publisher restored the original URL",
            applied_at_utc=NOW,
        )
        self.assertEqual(applied["requeued_job_count"], 1)
        queue = self.conn.execute(
            """SELECT status, attempt_count, last_result_status
                 FROM artifact_download_queue WHERE download_job_id=?""",
            (int(job["download_job_id"]),),
        ).fetchone()
        self.assertEqual(tuple(queue), ("PENDING", 0, "RECOVERY_REQUEUED"))
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM document_download_recovery_actions"
            ).fetchone()[0],
            1,
        )

    def test_replacement_url_preserves_old_source_and_queues_new_job(self) -> None:
        job = self._terminal_job()
        replacement = "https://investors.example.com/archive/replacement.pdf#download"
        result = register_replacement_document_url(
            self.conn,
            artifact_id=int(job["artifact_id"]),
            replaces_artifact_source_id=int(job["artifact_source_id"]),
            replacement_url=replacement,
            reason_code="SOURCE_REPUBLISHED",
            reason_detail="Moved into the publisher archive",
            evidence={"archive_page": "https://investors.example.com/archive"},
            applied_at_utc=NOW,
        )
        self.assertEqual(
            result["new_url"],
            "https://investors.example.com/archive/replacement.pdf",
        )
        self.assertEqual(len(result["created_jobs"]), 1)
        old = self.conn.execute(
            "SELECT availability_status FROM artifact_sources WHERE artifact_source_id=?",
            (int(job["artifact_source_id"]),),
        ).fetchone()[0]
        self.assertEqual(old, "inaccessible")
        queued = self.conn.execute(
            """SELECT status FROM artifact_download_queue
                WHERE artifact_id=? AND requested_url=?""",
            (int(job["artifact_id"]), result["new_url"]),
        ).fetchone()
        self.assertEqual(queued["status"], "PENDING")
        self.assertEqual(
            self.conn.execute(
                """SELECT action_type FROM document_download_recovery_actions
                    ORDER BY recovery_action_id DESC LIMIT 1"""
            ).fetchone()[0],
            "REGISTER_REPLACEMENT_URL",
        )

    def test_identical_blob_can_cover_two_logical_artifacts_without_copying_bytes(self) -> None:
        artifact_ids = [
            int(row[0])
            for row in self.conn.execute(
                """SELECT artifact_id FROM document_artifacts
                    WHERE artifact_type IN ('transcript', 'presentation')
                    ORDER BY artifact_id LIMIT 2"""
            )
        ]
        first = self._add_content(artifact_ids[0], b"shared publisher PDF")
        second = self._add_content(artifact_ids[1], b"shared publisher PDF")
        self.assertNotEqual(first, second)
        digest = hashlib.sha256(b"shared publisher PDF").hexdigest()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM response_blobs WHERE content_sha256=?",
                (digest,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM artifact_contents WHERE content_sha256=?",
                (digest,),
            ).fetchone()[0],
            2,
        )

    def test_da08_storage_projection_uses_observed_sizes_and_headroom(self) -> None:
        artifacts = list(
            self.conn.execute(
                """SELECT artifact_id, artifact_type FROM document_artifacts
                    WHERE artifact_type IN ('transcript', 'presentation')
                    ORDER BY artifact_id LIMIT 2"""
            )
        )
        self._add_content(int(artifacts[0]["artifact_id"]), b"x" * 100)
        self._add_content(int(artifacts[1]["artifact_id"]), b"y" * 300)
        report = build_document_storage_projection(
            self.conn,
            scope_id="recovery-scope",
            planned_target_counts={
                str(artifacts[0]["artifact_type"]): 10,
                str(artifacts[1]["artifact_type"]): 10,
            },
            headroom_percent=25,
            document_store=self.root,
        )
        self.assertEqual(report["actual"]["unique_content_bytes"], 400)
        self.assertTrue(report["document_store"]["exists"])
        self.assertGreater(report["projection"]["bytes_with_headroom"], 0)
        self.assertEqual(
            sum(
                item["observed_sizes"]["sample_count"]
                for item in report["by_artifact_type"].values()
            ),
            2,
        )

    def test_manual_import_is_signature_validated_audited_and_closes_queue(self) -> None:
        job = self._terminal_job()
        result = import_manual_document(
            self.conn,
            artifact_id=int(job["artifact_id"]),
            artifact_source_id=int(job["artifact_source_id"]),
            path=PDF,
            official=True,
            reason_detail="Publisher terms require operator acquisition",
            evidence={"ticket": "DOC-17"},
            snapshot_root=self.root / "manual-content",
            imported_at_utc=NOW,
        )
        self.assertEqual(result["detected_media_type"], "application/pdf")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM manual_document_imports").fetchone()[0],
            1,
        )
        queue = self.conn.execute(
            "SELECT status, last_result_status FROM artifact_download_queue WHERE download_job_id=?",
            (int(job["download_job_id"]),),
        ).fetchone()
        self.assertEqual(
            tuple(queue),
            ("SKIPPED_ALREADY_HAVE_CONTENT", "CONTENT_IMPORTED_MANUALLY"),
        )


class DocumentRecoveryMigrationTests(unittest.TestCase):
    def test_upgrade_keeps_every_foreign_key_on_artifact_contents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conn = sqlite3.connect(Path(directory) / "upgrade.db")
            conn.row_factory = sqlite3.Row
            try:
                for migration in sorted(MIGRATIONS_PATH.glob("*.sql")):
                    if migration.name >= "045_":
                        continue
                    conn.executescript(migration.read_text(encoding="utf-8"))
                conn.executescript(
                    (MIGRATIONS_PATH / "045_document_download_recovery.sql").read_text(
                        encoding="utf-8"
                    )
                )
                stale = conn.execute(
                    """SELECT name, sql FROM sqlite_master
                        WHERE sql LIKE '%artifact_contents_before_recovery%'"""
                ).fetchall()
                self.assertEqual(stale, [])
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                columns = {
                    row["name"]: bool(row["notnull"])
                    for row in conn.execute("PRAGMA table_info(artifact_contents)")
                }
                self.assertTrue(columns["content_sha256"])
                indexes = list(conn.execute("PRAGMA index_list(artifact_contents)"))
                self.assertFalse(
                    any(
                        row["unique"]
                        and [
                            item["name"]
                            for item in conn.execute(
                                f"PRAGMA index_info({row['name']})"
                            )
                        ]
                        == ["content_sha256"]
                        for row in indexes
                    )
                )
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
