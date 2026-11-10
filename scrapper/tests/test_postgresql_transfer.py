"""Full synthetic schema transfer, exact evidence, rerun and rollback tests."""
from decimal import Decimal
from contextlib import closing
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from scrapper.db.postgresql_transfer import export_snapshot, file_sha256, read_snapshot, source_tables, transfer
from scrapper.tests.test_postgresql_schema import synthetic_sqlite


def make_source(path, *, wal=False, formatted=False):
    source = formatted_sqlite() if formatted else synthetic_sqlite()
    with closing(sqlite3.connect(path)) as disk:
        source.backup(disk)
    source.close()
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO companies(company_id,current_legal_name) VALUES (91,'Synthetic ₹ source')")
    conn.execute("INSERT INTO response_blobs VALUES (31,'fixture-sha','application/pdf',NULL,12,'synthetic-only','2026-11-08T00:00:00+05:30','2026-11-08T00:00:00+05:30')")
    conn.execute("INSERT INTO parse_artifacts VALUES (41,31,'synthetic','1','1','2026-11-08T00:00:00Z','ok','passed')")
    for idx, (display, value, missing) in enumerate((('', None, 'blank'), ('-', None, 'dash'), ('0','0',None), ('huge','000123456789012345678901234567890.0000000000000000000100',None))):
        conn.execute("""INSERT INTO company_metric_observations
            (parse_id,reporting_scope,label_raw,position,display_value,value_decimal,missing_kind,first_observed_at_utc,available_at_utc,availability_basis)
            VALUES (41,'standalone','Synthetic',?,?,?,?, '2026-11-08T00:00:00+05:30','2026-11-08T00:00:00+05:30','synthetic')""", (idx, display, value, missing))
    conn.execute("""INSERT INTO document_artifacts(artifact_id,company_id,artifact_key,artifact_type,title_raw,first_observed_at_utc)
        VALUES(51,91,'synthetic-artifact','credit_rating','Synthetic','2026-11-08T00:00:00Z')""")
    conn.execute("""INSERT INTO artifact_contents(content_id,artifact_id,blob_id,media_type,content_sha256,byte_size,is_official,content_origin,created_at_utc)
        VALUES(61,51,31,'application/pdf','synthetic-content',12,1,'publisher_original','2026-11-08T00:00:00Z')""")
    # Exercise the artifact/content FK cycle and preferred-content trigger.
    conn.execute("UPDATE document_artifacts SET preferred_content_id=61 WHERE artifact_id=51")
    conn.commit()
    return conn


def formatted_sqlite():
    """Normal ledger bootstrap plus the three observed DDL formatting variants."""
    with closing(synthetic_sqlite()) as baseline:
        statements = list(baseline.iterdump())
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at_utc TEXT NOT NULL)")
    formatted = []
    for statement in statements:
        if statement.startswith("CREATE TABLE schema_migrations"):
            continue
        if statement.startswith('CREATE TABLE "exchange_source_snapshots"'):
            statement = statement.replace('"exchange_source_snapshots"', 'exchange_source_snapshots', 1)
            statement = statement.replace("\n    ", "\n        ")
        if statement.startswith("CREATE INDEX idx_exchange_source_excluded_class"):
            statement = statement.replace("\n", " ")
        formatted.append(statement)
    conn.executescript("\n".join(formatted))
    return conn


class SourceSchemaTests(unittest.TestCase):
    def test_runtime_bootstrap_and_observed_formatting_are_accepted(self):
        with closing(formatted_sqlite()) as conn:
            before = list(conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name"))
            self.assertEqual(len(source_tables(conn)), 157)
            self.assertEqual(before, list(conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name")))

    def test_real_drift_is_rejected_even_with_formatting_variants(self):
        replacements = (
            ("excluded_row_count >= 0", "excluded_row_count >= -1"),
            ("header_json TEXT NOT NULL DEFAULT '[]'", "header_json TEXT NOT NULL DEFAULT '[ ]'"),
            ("source_label TEXT NOT NULL", "source_label TEXT"),
            ("source_encoding TEXT NOT NULL", "source_encoding BLOB NOT NULL"),
            ("REFERENCES response_blobs(blob_id)", "REFERENCES response_blobs(blob_id) ON DELETE CASCADE"),
            ("UNIQUE(dataset, effective_date, blob_id, parser_name, parser_version)",
             "UNIQUE(dataset, effective_date, blob_id, parser_name)"),
        )
        with closing(formatted_sqlite()) as baseline:
            dump = "\n".join(baseline.iterdump())
        for old, new in replacements:
            with self.subTest(change=new), closing(sqlite3.connect(":memory:")) as conn:
                self.assertIn(old, dump)
                conn.executescript(dump.replace(old, new, 1))
                with self.assertRaisesRegex(RuntimeError, "schema differs"):
                    source_tables(conn)

    def test_object_inventory_and_definitions_are_enforced(self):
        changes = (
            "DROP INDEX idx_exchange_source_excluded_class",
            "DROP INDEX idx_exchange_source_excluded_class; CREATE INDEX idx_exchange_source_excluded_class ON exchange_source_excluded_rows(snapshot_id)",
            "CREATE TABLE unexpected (id INTEGER PRIMARY KEY)",
        )
        with closing(formatted_sqlite()) as sample:
            for kind in ("trigger", "view"):
                name = sample.execute("SELECT name FROM sqlite_master WHERE type=? LIMIT 1", (kind,)).fetchone()[0]
                changes += (f'DROP {kind} "{name}"',)
        for ddl in changes:
            with self.subTest(ddl=ddl), closing(formatted_sqlite()) as conn:
                conn.executescript(ddl)
                with self.assertRaisesRegex(RuntimeError, "schema differs"):
                    source_tables(conn)

    def test_token_boundaries_literals_comments_and_expression_quotes_are_preserved(self):
        from scrapper.db.postgresql_transfer import _catalog_tokens
        normalize = lambda ddl: _catalog_tokens([("view", "example", ddl)])
        pairs = (
            ("SELECT 'a b'", "SELECT 'ab'"),
            ("SELECT 'a'' b'", "SELECT 'a''b'"),
            ('SELECT "a b"', 'SELECT "ab"'),
            ('SELECT "label"', "SELECT label"),
            ("SELECT 1 <= 2", "SELECT 1 < = 2"),
            ("SELECT a || b", "SELECT a | | b"),
            ("SELECT 1e2", "SELECT 1 e2"),
            ("SELECT ab", "SELECT a b"),
            ("SELECT 1 -- comment\n+ 2", "SELECT 1 -- comment + 2"),
        )
        for before, after in pairs:
            with self.subTest(before=before):
                self.assertNotEqual(normalize(before), normalize(after))
        self.assertEqual(normalize("SELECT\n 1 + 2"), normalize("SELECT 1+2"))

    def test_fallback_requires_pinned_migration_bytes_and_original_catalog_hash(self):
        from unittest import mock
        from scrapper.db.postgresql_schema import manifest
        for corrupt in ("migration", "catalog"):
            expected = manifest()
            if corrupt == "migration":
                expected["sqlite_sources"][next(iter(expected["sqlite_sources"]))] = "0" * 64
            else:
                expected["sqlite_schema_sha256"] = "0" * 64
            with self.subTest(corrupt=corrupt), closing(formatted_sqlite()) as conn:
                with mock.patch("scrapper.db.postgresql_transfer.manifest", return_value=expected):
                    with self.assertRaises(RuntimeError):
                        source_tables(conn)

    def test_formatted_snapshot_exports_and_restores_without_schema_rewrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, snapshot, restored = (root / name for name in ("source.db", "snapshot.db", "restored.db"))
            with closing(formatted_sqlite()) as source, closing(sqlite3.connect(original)) as disk:
                source.backup(disk)
            before = file_sha256(original)
            first = export_snapshot(original, snapshot)
            second = export_snapshot(snapshot, restored)
            self.assertEqual(first["status"], "verified_snapshot")
            self.assertEqual(first["tables"], second["tables"])
            self.assertEqual(len(first["tables"]), 157)
            self.assertEqual(file_sha256(original), before)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_verified_backup_includes_wal_and_leaves_original_unchanged(self):
        original = self.root / "source.db"
        writer = make_source(original, wal=True)
        self.addCleanup(writer.close)
        before = file_sha256(original)
        target = self.root / "snapshot.db"
        events = []
        report = export_snapshot(original, target, batch_size=2, progress=events.append)
        self.assertEqual(file_sha256(original), before)
        self.assertEqual(report["snapshot_sha256"], file_sha256(target))
        self.assertEqual(report["tables"]["companies"]["rows"], 1)
        self.assertEqual(report["tables"]["company_metric_observations"]["rows"], 4)
        self.assertEqual(len(report["tables"]), 157)
        with read_snapshot(target) as copied:
            self.assertEqual(copied.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(copied.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(writer.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertTrue(events)
        with self.assertRaises(FileExistsError):
            export_snapshot(original, target)

    def test_bad_schema_null_primary_key_and_batch_bounds_fail_closed(self):
        original = self.root / "source.db"
        with closing(make_source(original)) as writer, writer:
            writer.execute("INSERT INTO schema_migrations VALUES (NULL,'synthetic')")
        with self.assertRaisesRegex(RuntimeError, "NULL primary key"):
            export_snapshot(original, self.root / "null.db")
        for value in (0, 100001):
            with self.assertRaises(ValueError):
                export_snapshot(original, self.root / "bad.db", batch_size=value)

    def test_failed_cli_report_has_no_raw_exception_or_values(self):
        from scrapper.db.postgresql_migration import main
        from unittest import mock
        report = self.root / "failure.json"
        with mock.patch("scrapper.db.postgresql_transfer.export_snapshot", side_effect=RuntimeError("password=secret raw-source-record")):
            self.assertEqual(main(["export", "--source", "unused", "--snapshot", "unused", "--report", str(report)]), 1)
        data = json.loads(report.read_text())
        self.assertEqual(data["status"], "failed")
        self.assertNotIn("secret", report.read_text())
        self.assertNotIn("raw-source", report.read_text())

    def test_restore_to_new_disposable_copy_matches_verified_backup(self):
        original = self.root / "source.db"
        make_source(original).close()
        snapshot = self.root / "verified.db"
        before = export_snapshot(original, snapshot)
        with closing(sqlite3.connect(original)) as changed, changed:
            changed.execute("UPDATE companies SET current_legal_name='changed disposable working copy'")
        restored = self.root / "restored.db"
        result = export_snapshot(snapshot, restored)
        self.assertEqual(result["tables"], before["tables"])
        with read_snapshot(restored) as conn:
            self.assertEqual(conn.execute("SELECT current_legal_name FROM companies").fetchone()[0], "Synthetic ₹ source")


@unittest.skipUnless(os.environ.get("TRADING_AI_POSTGRESQL_TEST_DSN"), "requires explicitly disposable PostgreSQL 16 DSN")
class TransferIntegrationTests(unittest.TestCase):
    def setUp(self):
        from scrapper.db.postgresql import PostgreSQLSettings, connect
        from scrapper.db.postgresql_schema import migrate
        from psycopg import sql
        self.settings = PostgreSQLSettings(os.environ["TRADING_AI_POSTGRESQL_TEST_DSN"])
        self.connect = lambda: connect(self.settings)
        self.sql = sql
        self.schema = "transfer_test_" + uuid.uuid4().hex
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.db"
        writer = make_source(self.source, formatted=True)
        writer.close()
        self.snapshot = self.root / "snapshot.db"
        self.backup = export_snapshot(self.source, self.snapshot, batch_size=2)
        self.addCleanup(self.drop_schema)
        with self.connect() as conn:
            migrate(conn, self.schema)

    def drop_schema(self):
        with self.connect() as conn:
            conn.execute(self.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(self.sql.Identifier(self.schema)))

    def run_transfer(self, **kwargs):
        return transfer(self.settings, self.snapshot, self.schema, expected_sha256=self.backup["snapshot_sha256"], batch_size=2, **kwargs)

    def session(self, conn):
        conn.execute(self.sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(self.sql.Identifier(self.schema)))

    def test_all_tables_exact_values_rerun_trigger_cycle_and_generated_ids(self):
        before = file_sha256(self.source)
        events = []
        report = self.run_transfer(progress=events.append)
        self.assertEqual(report["status"], "committed")
        self.assertEqual(len(report["tables"]), 157)
        self.assertTrue(all(table["verified"] for table in report["tables"].values()))
        self.assertEqual(self.run_transfer()["status"], "verified_existing")
        self.assertEqual(file_sha256(self.source), before)
        with self.connect() as conn:
            self.session(conn)
            values = conn.execute("SELECT display_value,value_decimal,missing_kind FROM company_metric_observations ORDER BY position").fetchall()
            self.assertEqual(values[0], {"display_value": "", "value_decimal": None, "missing_kind": "blank"})
            self.assertEqual(values[1], {"display_value": "-", "value_decimal": None, "missing_kind": "dash"})
            self.assertEqual(values[2]["value_decimal"], "0")
            exact = "000123456789012345678901234567890.0000000000000000000100"
            self.assertEqual(values[3]["value_decimal"], exact)
            self.assertEqual(Decimal(values[3]["value_decimal"]), Decimal(exact))
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM document_processing_inbox").fetchone()["n"], 1)
            self.assertEqual(conn.execute("SELECT preferred_content_id FROM document_artifacts WHERE artifact_id=51").fetchone()["preferred_content_id"], 61)
            self.assertEqual(conn.execute("INSERT INTO companies(current_legal_name) VALUES ('next') RETURNING company_id").fetchone()["company_id"], 92)
        self.assertTrue(any(event["phase"] == "import" for event in events))
        self.assertTrue(any(event["phase"] == "verify" for event in events))

    def test_failure_mid_import_rolls_back_and_restart_succeeds(self):
        def fail(event):
            if event["phase"] == "import" and event["table"] == "companies":
                raise RuntimeError("injected interruption")
        with self.assertRaisesRegex(RuntimeError, "injected"):
            self.run_transfer(progress=fail)
        with self.connect() as conn:
            self.session(conn)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM companies").fetchone()["n"], 0)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND NOT t.tgisinternal AND t.tgenabled<>'O'", (self.schema,)).fetchone()["n"], 0)
        self.assertEqual(self.run_transfer()["status"], "committed")

    def test_foreign_destination_rows_not_overwritten_or_merged(self):
        with self.connect() as conn:
            self.session(conn)
            conn.execute("INSERT INTO companies VALUES(91,'unexpected','active')")
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            self.run_transfer()
        with self.connect() as conn:
            self.session(conn)
            self.assertEqual(conn.execute("SELECT current_legal_name FROM companies").fetchone()["current_legal_name"], "unexpected")

    def test_schema_constraint_or_trigger_drift_is_rejected(self):
        for statement in ("ALTER TABLE companies DROP CONSTRAINT companies_pkey CASCADE", "DROP TRIGGER document_processing_saved ON artifact_contents"):
            with self.subTest(statement=statement):
                with self.connect() as conn:
                    self.session(conn)
                    with conn.transaction(force_rollback=True):
                        conn.execute(statement)
                        from scrapper.db.postgresql_schema import check_compatibility
                        with self.assertRaisesRegex(RuntimeError, "structure drifted"):
                            check_compatibility(conn, self.schema)

    def test_unauthorized_checksum_and_wal_input_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            transfer(self.settings, self.snapshot, self.schema, expected_sha256="wrong")
        writer = sqlite3.connect(self.snapshot)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        with self.assertRaisesRegex(RuntimeError, "standalone verified snapshot"):
            transfer(self.settings, self.snapshot, self.schema, expected_sha256=file_sha256(self.snapshot))

    def test_rerun_rejects_sequence_position_that_would_duplicate_keys(self):
        self.run_transfer()
        with self.connect() as conn:
            self.session(conn)
            conn.execute("ALTER SEQUENCE companies_company_id_seq RESTART WITH 1")
        with self.assertRaisesRegex(RuntimeError, "sequence is behind"):
            self.run_transfer()

    def test_operator_cli_reports_success_and_never_overwrites_evidence(self):
        from scrapper.db.postgresql_migration import main
        from unittest import mock
        snapshot = self.root / "cli-snapshot.db"
        export_report = self.root / "cli-export.json"
        schema_report = self.root / "cli-schema.json"
        import_report = self.root / "cli-import.json"
        with redirect_stderr(io.StringIO()), mock.patch.dict(os.environ, {
            "TRADING_AI_DB_BACKEND": "postgresql", "DATABASE_URL": self.settings.dsn,
        }):
            self.assertEqual(main(["export", "--source", str(self.source), "--snapshot", str(snapshot), "--report", str(export_report)]), 0)
            digest = json.loads(export_report.read_text())["snapshot_sha256"]
            self.assertEqual(main(["schema", "--schema", self.schema, "--allow-destination-write", "--report", str(schema_report)]), 0)
            self.assertEqual(main(["import", "--schema", self.schema, "--allow-destination-write", "--snapshot", str(snapshot), "--snapshot-sha256", digest, "--report", str(import_report)]), 0)
            summary = json.loads(import_report.read_text())
            self.assertEqual(summary["status"], "committed")
            self.assertEqual(len(summary["tables"]), 157)
            self.assertEqual(import_report.stat().st_mode & 0o777, 0o600)
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                main(["export", "--source", str(self.source), "--snapshot", str(snapshot), "--report", str(export_report)])


if __name__ == "__main__":
    unittest.main()
