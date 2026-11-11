"""Synthetic lineage checks and explicit disposable PostgreSQL schema parity."""
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from unittest import mock

from scrapper.db import postgresql_schema as schema
from scrapper.db.postgresql_source_text import SOURCE_TEXT_COLUMNS

ROOT = Path(__file__).resolve().parents[2]


def synthetic_sqlite():
    conn = sqlite3.connect(":memory:")
    for path in sorted((ROOT / "scrapper/db/migrations").glob("*.sql")):
        conn.executescript(path.read_text())
    return conn


class BaselineTests(unittest.TestCase):
    def test_generated_baseline_matches_committed_bytes_and_sources(self):
        spec = importlib.util.spec_from_file_location("pg_generator", ROOT / "scripts/audit/generate_postgresql_baseline.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        sql, manifest = generator.generate()
        self.assertEqual(sql, (schema.MIGRATIONS / "001_baseline.sql").read_text())
        committed = schema.manifest()
        # The generated catalog certifies frozen PG001, not later migrations.
        self.assertEqual(manifest['migrations']['001_baseline.sql'], committed['migrations']['001_baseline.sql'])
        self.assertEqual({k: v for k, v in manifest.items() if k != 'migrations'},
                         {k: v for k, v in committed.items() if k != 'migrations'})
        self.assertEqual(len(list(schema.migration_files())), len(committed['migrations']))

    def test_generator_cannot_overwrite_later_lineage(self):
        before = {path.name: path.read_bytes() for path in schema.MIGRATIONS.iterdir() if path.is_file()}
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/audit/generate_postgresql_baseline.py')],
                                capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PG001 is frozen', result.stderr)
        self.assertEqual(before, {path.name: path.read_bytes() for path in schema.MIGRATIONS.iterdir() if path.is_file()})

    def test_schema_name_rejects_shared_or_unsafe_schemas(self):
        for name in ("public", "pg_catalog", "information_schema", "a;drop", "A", "", "x" * 64):
            with self.subTest(name=name), self.assertRaises(ValueError):
                schema.validate_schema_name(name)
        schema.validate_schema_name("application_test_55")

    def test_checksum_drift_is_rejected_before_sql(self):
        original = schema.manifest()
        original["migrations"]["001_baseline.sql"] = "incorrect"
        with mock.patch.object(schema, "manifest", return_value=original):
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                list(schema.migration_files())


@unittest.skipUnless(os.environ.get("TRADING_AI_POSTGRESQL_TEST_DSN"), "requires explicitly disposable PostgreSQL 16 DSN")
class SchemaIntegrationTests(unittest.TestCase):
    def setUp(self):
        from scrapper.db.postgresql import PostgreSQLSettings, connect
        from psycopg import sql
        self.connect = lambda: connect(PostgreSQLSettings(os.environ["TRADING_AI_POSTGRESQL_TEST_DSN"]))
        self.sql = sql
        self.name = "schema_test_" + uuid.uuid4().hex
        self.source = synthetic_sqlite()
        self.addCleanup(self.source.close)
        self.addCleanup(self.drop_schema)
        with self.connect() as conn:
            schema.migrate(conn, self.name)

    def drop_schema(self):
        with self.connect() as conn:
            conn.execute(self.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(self.sql.Identifier(self.name)))

    def session(self, conn):
        conn.execute(self.sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(self.sql.Identifier(self.name)))

    def test_full_object_and_column_constraint_parity(self):
        objects = schema.application_objects()
        with self.connect() as conn:
            self.session(conn)
            for kind, relkind in (("table", "r"), ("view", "v")):
                names = {row["relname"] for row in conn.execute("SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind=%s", (self.name, relkind))}
                self.assertEqual(names - {"_postgresql_migrations"}, set(objects[kind]))
            triggers = {row["tgname"] for row in conn.execute("SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND NOT t.tgisinternal", (self.name,))}
            self.assertEqual(triggers, set(objects["trigger"]) - {"trg_queue_natural_insert_guard"})
            indexes = {row["indexname"] for row in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname=%s", (self.name,))}
            self.assertTrue(set(objects["index"]) <= indexes)
            for table in objects["table"]:
                with self.subTest(table=table):
                    source = list(self.source.execute(f'PRAGMA table_info("{table}")'))
                    dest = list(conn.execute("SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position", (self.name, table)))
                    self.assertEqual([col[1] for col in source], [col["column_name"] for col in dest])
                    for col, target in zip(source, dest):
                        self.assertEqual(target["data_type"], "bytea" if col[1] in SOURCE_TEXT_COLUMNS.get(table, ()) else {"INTEGER": "bigint", "REAL": "double precision", "TEXT": "text"}[col[2]])
                        self.assertEqual(target["is_nullable"], "NO" if col[3] or col[5] else "YES")
                    pks = conn.execute("SELECT a.attname FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,ord) JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum WHERE n.nspname=%s AND c.relname=%s AND i.indisprimary ORDER BY k.ord", (self.name, table)).fetchall()
                    self.assertEqual([col[1] for col in sorted(source, key=lambda c: c[5]) if col[5]], [row["attname"] for row in pks])
                    source_fk = list(self.source.execute(f'PRAGMA foreign_key_list("{table}")'))
                    dest_fk = conn.execute("SELECT cardinality(conkey) AS columns, condeferrable, convalidated FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s AND t.relname=%s AND contype='f'", (self.name, table)).fetchall()
                    self.assertEqual(len(source_fk), sum(row["columns"] for row in dest_fk))
                    self.assertTrue(all(row["convalidated"] and row["condeferrable"] for row in dest_fk))
                    source_sql = self.source.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
                    checks = conn.execute("SELECT count(*) AS n FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s AND t.relname=%s AND contype='c'", (self.name, table)).fetchone()["n"]
                    self.assertEqual(checks, len(re.findall(r"\bCHECK\s*\(", source_sql, re.I)))
            # Every view must parse and resolve all its references/types.
            for view in objects["view"]:
                conn.execute(self.sql.SQL("SELECT * FROM {} LIMIT 0").format(self.sql.Identifier(view)))

    def test_legacy_004_upgrade_preserves_signatures_and_refuses_drift(self):
        targets=[self.name+'_old',self.name+'_bad']
        try:
            with tempfile.TemporaryDirectory() as directory:
                migrations=Path(directory);info=schema.manifest()
                info['migrations']={name:checksum for name,checksum in info['migrations'].items()
                                    if name<'005_portable_schema_fingerprint.sql'}
                for name in info['migrations']:shutil.copy(schema.MIGRATIONS/name,migrations/name)
                (migrations/'manifest.json').write_text(json.dumps(info))
                with mock.patch.object(schema,'MIGRATIONS',migrations):
                    for target in targets:
                        with self.connect() as conn:schema.migrate(conn,target)
                with self.connect() as conn:
                    before=conn.execute(self.sql.SQL('SELECT version,sha256,schema_sha256 FROM {}._postgresql_migrations ORDER BY version').format(self.sql.Identifier(targets[0]))).fetchall()
                    conn.execute(self.sql.SQL('ALTER TABLE {}.companies DISABLE TRIGGER ALL').format(self.sql.Identifier(targets[1])))
            with self.connect() as conn:
                schema.migrate(conn,targets[0]);schema.check_compatibility(conn,targets[0])
                after=conn.execute(self.sql.SQL('SELECT version,sha256,schema_sha256 FROM {}._postgresql_migrations ORDER BY version').format(self.sql.Identifier(targets[0]))).fetchall()
                self.assertEqual(after[:4],before)
                self.assertEqual(after[4]['version'],'005_portable_schema_fingerprint.sql')
            with self.assertRaisesRegex(RuntimeError,'structure drifted'):
                with self.connect() as conn:schema.migrate(conn,targets[1])
            with self.connect() as conn:
                self.assertEqual(conn.execute(self.sql.SQL('SELECT count(*) AS n FROM {}._postgresql_migrations').format(self.sql.Identifier(targets[1]))).fetchone()['n'],4)
        finally:
            with self.connect() as conn:
                for target in targets:conn.execute(self.sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(self.sql.Identifier(target)))

    def test_portable_fingerprint_keeps_user_trigger_names_exact(self):
        with self.connect() as conn:
            trigger=conn.execute('''SELECT relation.relname,trigger.tgname FROM pg_trigger trigger
                JOIN pg_class relation ON relation.oid=trigger.tgrelid
                JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
                WHERE namespace.nspname=%s AND NOT trigger.tgisinternal LIMIT 1''',(self.name,)).fetchone()
            conn.execute(self.sql.SQL('ALTER TRIGGER {} ON {}.{} RENAME TO changed_user_trigger').format(
                self.sql.Identifier(trigger['tgname']),self.sql.Identifier(self.name),self.sql.Identifier(trigger['relname'])))
        with self.assertRaisesRegex(RuntimeError,'structure drifted'):
            with self.connect() as conn:schema.check_compatibility(conn,self.name)

    def test_portable_fingerprint_retains_internal_foreign_key_enforcement(self):
        with self.connect() as conn:
            self.session(conn)
            conn.execute("ALTER TABLE companies DISABLE TRIGGER ALL")
        with self.assertRaisesRegex(RuntimeError,"structure drifted"):
            with self.connect() as conn:
                schema.check_compatibility(conn,self.name)

    def test_idempotency_checksum_and_read_only_checks(self):
        with self.connect() as conn:
            schema.migrate(conn, self.name)
            schema.check_compatibility(conn, self.name)
            self.session(conn)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM _postgresql_migrations").fetchone()["n"], len(schema.manifest()["migrations"]))
            conn.execute("UPDATE _postgresql_migrations SET sha256='drift'")
        with self.assertRaisesRegex(RuntimeError, "lineage"):
            with self.connect() as conn:
                schema.migrate(conn, self.name)

    def test_rollback_removes_fresh_schema(self):
        other = self.name + "_r"
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.connect() as conn:
                schema.migrate(conn, other)
                raise RuntimeError("rollback")
        with self.connect() as conn:
            self.assertIsNone(conn.execute("SELECT to_regnamespace(%s) AS n", (other,)).fetchone()["n"])

    def test_sequential_upgrade_and_transactional_failure(self):
        with tempfile.TemporaryDirectory() as root:
            migrations = Path(root)
            for path in schema.MIGRATIONS.glob("*.sql"):
                shutil.copy(path, migrations)
            info = schema.manifest()
            initial_count = len(info["migrations"])
            content = "CREATE TABLE upgrade_probe(id bigint PRIMARY KEY);"
            (migrations / "900_probe.sql").write_text(content)
            info["migrations"]["900_probe.sql"] = hashlib.sha256(content.encode()).hexdigest()
            (migrations / "manifest.json").write_text(json.dumps(info))
            with mock.patch.object(schema, "MIGRATIONS", migrations):
                with self.connect() as conn:
                    schema.migrate(conn, self.name)
                    schema.check_compatibility(conn, self.name)
                    self.session(conn)
                    self.assertEqual(conn.execute("SELECT count(*) AS n FROM _postgresql_migrations").fetchone()["n"], initial_count + 1)
                bad = "CREATE TABLE rollback_probe(id bigint); SELECT nonexistent_column;"
                (migrations / "901_failure.sql").write_text(bad)
                info["migrations"]["901_failure.sql"] = hashlib.sha256(bad.encode()).hexdigest()
                (migrations / "manifest.json").write_text(json.dumps(info))
                import psycopg
                with self.assertRaises(psycopg.errors.UndefinedColumn):
                    with self.connect() as conn:
                        schema.migrate(conn, self.name)
                with self.connect() as conn:
                    self.session(conn)
                    self.assertIsNone(conn.execute("SELECT to_regclass('rollback_probe') AS n").fetchone()["n"])
                    self.assertEqual(conn.execute("SELECT count(*) AS n FROM _postgresql_migrations").fetchone()["n"], initial_count + 1)

    def test_upgrade_from_frozen_baseline_adds_only_history_index(self):
        from scrapper.db.postgresql import PostgreSQLSettings, connect
        target = self.name + "_v1"
        settings = PostgreSQLSettings(os.environ["TRADING_AI_POSTGRESQL_TEST_DSN"])
        try:
            with tempfile.TemporaryDirectory() as root:
                migrations = Path(root)
                shutil.copy(schema.MIGRATIONS / "001_baseline.sql", migrations)
                info = schema.manifest()
                info["migrations"] = {"001_baseline.sql": info["migrations"]["001_baseline.sql"]}
                (migrations / "manifest.json").write_text(json.dumps(info))
                with mock.patch.object(schema, "MIGRATIONS", migrations):
                    with connect(settings) as conn:
                        schema.migrate(conn, target)
                        conn.execute(self.sql.SQL("INSERT INTO {}.companies(current_legal_name) VALUES ('0001.2300')").format(self.sql.Identifier(target)))
            with connect(settings) as conn:
                schema.migrate(conn, target)
                schema.check_compatibility(conn, target)
                self.assertEqual(conn.execute(self.sql.SQL("SELECT current_legal_name FROM {}.companies").format(self.sql.Identifier(target))).fetchone()["current_legal_name"], "0001.2300")
                definition = conn.execute("SELECT indexdef FROM pg_indexes WHERE schemaname=%s AND indexname='idx_acceptance_events_company_scope'", (target,)).fetchone()["indexdef"]
                self.assertIn("(company_id, reporting_scope)", definition)
        finally:
            with connect(settings) as conn:
                conn.execute(self.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(self.sql.Identifier(target)))

    def test_temporary_tables_cannot_shadow_trigger_guards(self):
        import psycopg
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.connect() as conn:
                self.session(conn)
                conn.execute("CREATE TEMP TABLE document_artifacts(artifact_id bigint,artifact_type text)")
                conn.execute("INSERT INTO pg_temp.document_artifacts VALUES (991,'transcript')")
                # A fake temporary table must not make the first identity guard
                # pass, exposing later NOT NULL/FK failures instead.
                conn.execute(self.sql.SQL("INSERT INTO {}.transcripts(artifact_id) VALUES (991)").format(self.sql.Identifier(self.name)))

    def test_generated_ids_exact_strings_and_trigger_backstops(self):
        import psycopg
        raw = "000000000000000.0000000000000000000100"
        with self.connect() as conn:
            self.session(conn)
            row = conn.execute("INSERT INTO companies(current_legal_name) VALUES (%s) RETURNING company_id", (raw,)).fetchone()
            self.assertGreater(row["company_id"], 0)
            self.assertEqual(conn.execute("SELECT current_legal_name FROM companies").fetchone()["current_legal_name"], raw)
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.connect() as conn:
                self.session(conn)
                # Trigger must fail before unrelated NOT NULL/FK checks.
                conn.execute("INSERT INTO financial_observations(reporting_scope) VALUES ('consolidated')")
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.connect() as conn:
                self.session(conn)
                conn.execute("INSERT INTO artifact_expectations(company_id,event_id) VALUES(1,999)")


if __name__ == "__main__":
    unittest.main()
