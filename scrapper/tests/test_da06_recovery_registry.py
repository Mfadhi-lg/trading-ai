from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scrapper.config import MIGRATIONS_PATH
from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import register_artifact_content
from scrapper.db.document_recovery_repo import build_recovery_targets


NOW = "2026-08-16T00:00:00+00:00"


class RecoveryRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "recovery.db")
        init_db(self.conn, apply_migrations=True)

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def _blocked_artifact(self, name: str, symbol: str, url: str) -> tuple[int, int]:
        body = f"discovery:{url}".encode()
        digest = hashlib.sha256(body).hexdigest()
        path = self.root / f"{digest}.html"
        path.write_bytes(body)
        with self.conn:
            company_id = int(
                self.conn.execute(
                    "INSERT INTO companies(current_legal_name,status) VALUES (?,'active')",
                    (name,),
                ).lastrowid
            )
            self.conn.execute(
                """INSERT INTO security_identifiers
                   (company_id,identifier_type,identifier_value,is_current)
                   VALUES (?,'NSE',?,1)""",
                (company_id, symbol),
            )
            self.conn.execute(
                """INSERT INTO response_blobs
                   (content_sha256,content_type,encoding,byte_size,local_path,
                    first_observed_at_utc,created_at_utc)
                   VALUES (?,'text/html',NULL,?,?,?,?)""",
                (digest, len(body), str(path), NOW, NOW),
            )
            blob_id = int(
                self.conn.execute(
                    "SELECT blob_id FROM response_blobs WHERE content_sha256=?",
                    (digest,),
                ).fetchone()[0]
            )
            page_id = int(
                self.conn.execute(
                    """INSERT INTO artifact_discovery_pages
                       (company_id,discovered_from_source,discovery_page_url,
                        publisher_claim,blob_id,parser_name,parser_version,
                        first_observed_at_utc)
                       VALUES (?,'screener','https://www.screener.in/company/'||?,
                               NULL,?,'fixture','1',?)""",
                    (company_id, symbol, blob_id, NOW),
                ).lastrowid
            )
            artifact_id = int(
                self.conn.execute(
                    """INSERT INTO document_artifacts
                       (company_id,artifact_key,artifact_type,publisher,title_raw,
                        event_date,first_observed_at_utc)
                       VALUES (?,?,'credit_rating',?,'Credit rating','2026-08-01',?)""",
                    (company_id, f"rating:{symbol}", name, NOW),
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
                       VALUES (?,?,'screener','https://www.screener.in/',?,NULL,
                               0,'Credit rating','{}',0,?,?)""",
                    (artifact_id, page_id, url, NOW, NOW),
                ).lastrowid
            )
        return artifact_id, source_id

    def _official_content(self, artifact_id: int, source_id: int) -> None:
        body = b"%PDF-1.4 already recovered"
        digest = hashlib.sha256(body).hexdigest()
        path = self.root / f"{digest}.pdf"
        path.write_bytes(body)
        with self.conn:
            self.conn.execute(
                """INSERT OR IGNORE INTO response_blobs
                   (content_sha256,content_type,encoding,byte_size,local_path,
                    first_observed_at_utc,created_at_utc)
                   VALUES (?,'application/pdf',NULL,?,?,?,?)""",
                (digest, len(body), str(path), NOW, NOW),
            )
            blob_id = int(
                self.conn.execute(
                    "SELECT blob_id FROM response_blobs WHERE content_sha256=?",
                    (digest,),
                ).fetchone()[0]
            )
            register_artifact_content(
                self.conn,
                artifact_id=artifact_id,
                blob_id=blob_id,
                media_type="application/pdf",
                is_official=True,
                created_at_utc=NOW,
            )
            content_id = int(
                self.conn.execute(
                    "SELECT content_id FROM artifact_contents WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone()[0]
            )
            self.conn.execute(
                """INSERT INTO artifact_source_evidence_classifications
                   (artifact_source_id,content_id,content_class,
                    classification_basis_json,classifier_name,classifier_version,
                    classified_at_utc)
                   VALUES (?,?,'RATING_LETTER','{}',
                           'rating-evidence-structure','fixture',?)""",
                (source_id, content_id, NOW),
            )

    def test_crisil_and_icra_artifacts_become_targets(self) -> None:
        crisil_id, crisil_source = self._blocked_artifact(
            "CRISIL Fixture Limited", "CRFIX",
            "https://www.crisilratings.com/reports/crfix.pdf",
        )
        icra_id, icra_source = self._blocked_artifact(
            "ICRA Fixture Limited", "ICRAFIX",
            "https://www.icra.in/Rationale/ShowRationaleReport?id=1",
        )
        report = build_recovery_targets(self.conn, observed_at_utc=NOW)
        self.assertEqual(report["total_blocked_artifacts"], 2)
        self.assertEqual(report["recovery_target_count"], 2)
        self.assertEqual(report["crisil_count"], 1)
        self.assertEqual(report["icra_count"], 1)
        rows = self.conn.execute(
            """SELECT artifact_id,document_originator,status FROM artifact_recovery_targets
               ORDER BY artifact_id"""
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [(crisil_id, "CRISIL", "PENDING"), (icra_id, "ICRA", "PENDING")],
        )
        sources = self.conn.execute(
            """SELECT artifact_source_id,automation_status FROM artifact_sources
               WHERE artifact_source_id IN (?,?) ORDER BY artifact_source_id""",
            (crisil_source, icra_source),
        ).fetchall()
        self.assertEqual([row["automation_status"] for row in sources], ["BLOCKED_TERMS"] * 2)

    def test_already_satisfied_and_idempotent_accounting(self) -> None:
        artifact_id, source_id = self._blocked_artifact(
            "Satisfied Limited", "SATISFIED",
            "https://ratings.crisil.com/reports/satisfied.pdf",
        )
        self._official_content(artifact_id, source_id)
        first = build_recovery_targets(self.conn, observed_at_utc=NOW)
        second = build_recovery_targets(self.conn, observed_at_utc=NOW)
        self.assertEqual(first["already_satisfied"], 1)
        self.assertEqual(first["recovery_target_count"], 0)
        self.assertEqual(second["targets_created"], 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM artifact_recovery_targets").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM artifact_recovery_targets").fetchone()[0],
            "ALREADY_SATISFIED",
        )

    def test_dry_run_does_not_mutate(self) -> None:
        self._blocked_artifact(
            "Dry Run Limited", "DRYRUN",
            "https://www.icra.in/Rationale/dry.pdf",
        )
        report = build_recovery_targets(self.conn, dry_run=True, observed_at_utc=NOW)
        self.assertEqual(report["targets_created"], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM artifact_recovery_targets").fetchone()[0],
            0,
        )


class RecoveryRegistryMigrationTests(unittest.TestCase):
    def test_upgrade_from_pre_da06_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conn = sqlite3.connect(Path(directory) / "upgrade.db")
            conn.row_factory = sqlite3.Row
            try:
                for migration in sorted(MIGRATIONS_PATH.glob("*.sql")):
                    if migration.name >= "046_":
                        continue
                    conn.executescript(migration.read_text(encoding="utf-8"))
                conn.executescript(
                    (MIGRATIONS_PATH / "046_document_alternate_source_recovery.sql")
                    .read_text(encoding="utf-8")
                )
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(artifact_sources)")
                }
                self.assertIn("automation_status", columns)
                self.assertIn("recovery_candidate_id", columns)
                self.assertIsNotNone(
                    conn.execute(
                        """SELECT 1 FROM sqlite_master
                           WHERE type='table' AND name='artifact_recovery_targets'"""
                    ).fetchone()
                )
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
