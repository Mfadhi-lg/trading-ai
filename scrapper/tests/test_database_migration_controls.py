"""C04.2 contract: compatibility checks are inert; migrations are explicit."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scrapper.db import database


class DatabaseMigrationControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.migrations = self.root / "migrations"
        self.migrations.mkdir()
        self.db_path = self.root / "service.sqlite"
        self.patch_migrations = mock.patch.object(database, "MIGRATIONS_PATH", self.migrations)
        self.patch_dirs = mock.patch.object(database, "ensure_dirs")
        self.patch_migrations.start()
        self.patch_dirs.start()
        self.addCleanup(self.patch_migrations.stop)
        self.addCleanup(self.patch_dirs.stop)

    def migration(self, filename: str, sql: str) -> None:
        (self.migrations / filename).write_text(sql, encoding="utf-8")

    def connect(self) -> sqlite3.Connection:
        return database.connect(self.db_path)

    def test_default_init_checks_without_creating_ledger_or_applying_sql(self) -> None:
        self.migration("001_initial.sql", "CREATE TABLE initial(id INTEGER);\n")
        conn = self.connect()
        self.addCleanup(conn.close)

        with self.assertRaisesRegex(RuntimeError, "not initialized"):
            database.init_db(conn)

        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, set())

    def test_only_explicit_apply_runs_newly_visible_migration(self) -> None:
        self.migration("001_initial.sql", "CREATE TABLE initial(id INTEGER);\n")
        conn = self.connect()
        self.addCleanup(conn.close)
        database.init_db(conn, apply_migrations=True)
        self.migration("002_later.sql", "CREATE TABLE later(id INTEGER);\n")

        with self.assertRaisesRegex(RuntimeError, "pending migrations: 002_later.sql"):
            database.init_db(conn)
        self.assertIsNone(
            conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='later'").fetchone()
        )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 1)

        database.init_db(conn, apply_migrations=True)
        self.assertIsNotNone(
            conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='later'").fetchone()
        )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 2)

    def test_historical_a2_identity_is_accepted_by_full_filename(self) -> None:
        self.migration("065_operator_browser_rating_recovery.sql", "CREATE TABLE browser_065(id INTEGER);\n")
        conn = self.connect()
        self.addCleanup(conn.close)
        database.init_db(conn, apply_migrations=True)
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (?, ?)",
            ("065_exchange_source_access.sql", "2026-09-29T00:00:00+00:00"),
        )

        database.check_schema_compatibility(conn)
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        self.assertEqual(
            applied,
            {"065_operator_browser_rating_recovery.sql", "065_exchange_source_access.sql"},
        )

    def test_unknown_applied_identity_blocks_compatibility_and_migration(self) -> None:
        self.migration("001_initial.sql", "CREATE TABLE initial(id INTEGER);\n")
        conn = self.connect()
        self.addCleanup(conn.close)
        conn.execute(
            "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at_utc TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at_utc) VALUES ('099_unknown.sql', '2000')"
        )

        with self.assertRaisesRegex(RuntimeError, "unknown applied identities"):
            database.check_schema_compatibility(conn)
        with self.assertRaisesRegex(RuntimeError, "unknown applied identities"):
            database.apply_migrations(conn)

    def test_read_only_connection_cannot_write_or_create_missing_database(self) -> None:
        with sqlite3.connect(self.db_path) as writer:
            writer.execute("CREATE TABLE sample(value TEXT)")
        conn = database.connect(self.db_path, read_only=True)
        self.addCleanup(conn.close)

        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("INSERT INTO sample(value) VALUES ('no')")
        with self.assertRaisesRegex(RuntimeError, "not initialized"):
            database.init_db(conn)
        with self.assertRaisesRegex(RuntimeError, "read-only connection"):
            database.init_db(conn, apply_migrations=True)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0], 0)

        missing = self.root / "missing.sqlite"
        with self.assertRaises(FileNotFoundError):
            database.connect(missing, read_only=True)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
