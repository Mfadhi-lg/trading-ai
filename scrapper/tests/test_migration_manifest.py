"""Disposable-ledger checks for the offline migration lineage auditor."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.audit.check_migration_manifest import AuditError, inspect_database


class MigrationLedgerAuditTests(unittest.TestCase):
    def make_db(self, versions: list[str]) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "fixture.db"
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at_utc TEXT NOT NULL)"
            )
            conn.executemany(
                "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (?, ?)",
                [(version, "2000-01-01T00:00:00+00:00") for version in versions],
            )
        return path

    def test_distinct_archived_identity_and_pending_migrations_are_explicit(self) -> None:
        available = {"001_initial.sql", "065_operator_browser_rating_recovery.sql"}
        historical = {"065_exchange_source_access.sql"}
        db = self.make_db(["001_initial.sql", "065_exchange_source_access.sql"])

        result = inspect_database(
            db,
            available,
            historical,
            expected_applied=["001_initial.sql", "065_exchange_source_access.sql"],
            expected_pending=["065_operator_browser_rating_recovery.sql"],
        )

        self.assertEqual(result["applied_count"], 2)
        self.assertEqual(result["pending_filenames"], ["065_operator_browser_rating_recovery.sql"])
        self.assertEqual(result["unknown_applied_filenames"], [])

    def test_unknown_applied_identity_fails_closed(self) -> None:
        db = self.make_db(["001_initial.sql", "099_missing.sql"])

        with self.assertRaisesRegex(AuditError, "no source or archived disposition"):
            inspect_database(db, {"001_initial.sql"}, set())

    def test_unexpected_pending_set_fails_closed(self) -> None:
        db = self.make_db(["001_initial.sql"])

        with self.assertRaisesRegex(AuditError, "pending migration filenames differ"):
            inspect_database(
                db,
                {"001_initial.sql", "002_next.sql"},
                set(),
                expected_applied=["001_initial.sql"],
                expected_pending=[],
            )


if __name__ == "__main__":
    unittest.main()
