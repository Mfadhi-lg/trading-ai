"""Existing Screener repository parity on synthetic, isolated application schemas."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from scrapper.core.http import FetchResponse
from scrapper.db import repo
from scrapper.db.postgresql import PostgreSQLSettings, connect
from scrapper.db.postgresql_schema import migrate
from scrapper.sources.screener.company import parse_company_page
from scrapper.sources.screener.peers import parse_peer_table
from scrapper.tests.test_postgresql_schema import synthetic_sqlite

FIRST = "2026-11-09T00:00:00+00:00"
FIXTURES = Path(__file__).parent / "fixtures" / "screener"


@unittest.skipUnless(os.environ.get("TRADING_AI_POSTGRESQL_TEST_DSN"),
                     "requires explicitly disposable PostgreSQL 16 DSN")
class ObservationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = (FIXTURES / "LAURUSLABS_standalone.html").read_bytes()
        cls.parsed = parse_company_page(cls.content, FIRST)
        # These source/display strings must survive without numeric coercion.
        samples = (("", None, "blank"), ("-", None, "dash"), ("0", "0", None),
                   ("huge", "000123456789012345678901234567890.0000000000000000000100", None))
        cls.parsed = replace(cls.parsed, financials=[
            replace(obs, display_value=display, value_decimal=value, missing_kind=missing)
            for obs, (display, value, missing) in zip(cls.parsed.financials, samples)
        ] + cls.parsed.financials[len(samples):])
        cls.peers = parse_peer_table((FIXTURES / "LAURUSLABS_standalone_peers.html").read_bytes(), "1273603")

    def setUp(self):
        from psycopg import sql
        self.sql = sql
        self.settings = PostgreSQLSettings(os.environ["TRADING_AI_POSTGRESQL_TEST_DSN"])
        self.schema = "observation_test_" + uuid.uuid4().hex
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.drop_schema)
        with connect(self.settings) as conn:
            migrate(conn, self.schema)
        self.source = synthetic_sqlite()
        self.source.row_factory = sqlite3.Row
        self.addCleanup(self.source.close)

    def drop_schema(self):
        with connect(self.settings) as conn:
            conn.execute(self.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(self.sql.Identifier(self.schema)))

    def session(self, conn):
        conn.execute(self.sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(self.sql.Identifier(self.schema)))

    def response(self):
        return FetchResponse("https://www.screener.in/company/LAURUSLABS/",
                             "https://www.screener.in/company/LAURUSLABS/",
                             200, self.content, "text/html", "utf-8", None, None, "public", "ok")

    def seed(self, conn):
        run = repo.start_run(conn, "synthetic", {"fixture": True})
        company = repo.ensure_company(conn, self.parsed)
        repo.ensure_source_identifier(conn, company, self.parsed, self.response().final_url)
        blob = repo.store_blob(conn, self.response(), "LAURUSLABS", "standalone", FIRST, Path(self.temp.name))
        fetch = repo.record_fetch(conn, run, company, self.response(), "standalone", FIRST, blob)
        artifact = repo.get_or_create_parse_artifact(conn, blob, "synthetic-parser", "1", "1")
        return run, company, blob, fetch, artifact

    def records(self, conn, table):
        # Table names are fixed test constants below, never source-controlled values.
        return sorted((dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')),
                      key=lambda row: json.dumps(row, sort_keys=True))

    def test_existing_company_and_peer_repositories_match_sqlite_exactly(self):
        with patch.object(repo, "utc_now", return_value=FIRST):
            source_ids = self.seed(self.source)
            repo.store_parsed_company(self.source, source_ids[-1][0], self.parsed, FIRST)
            peer_id, _ = repo.get_or_create_parse_artifact(self.source, source_ids[2], "peers", "1", "1")
            repo.store_parsed_peer_table(self.source, peer_id, self.peers, "standalone", FIRST)
            repo.link_supplemental_parse(self.source, source_ids[-1][0], "peers", peer_id)
            repo.finish_run(self.source, source_ids[0], "ok")
            with connect(self.settings) as conn:
                self.session(conn)
                destination_ids = self.seed(conn)
                self.assertEqual(destination_ids, source_ids)
                repo.store_parsed_company(conn, destination_ids[-1][0], self.parsed, FIRST)
                pg_peer_id, _ = repo.get_or_create_parse_artifact(conn, destination_ids[2], "peers", "1", "1")
                repo.store_parsed_peer_table(conn, pg_peer_id, self.peers, "standalone", FIRST)
                repo.link_supplemental_parse(conn, destination_ids[-1][0], "peers", pg_peer_id)
                repo.finish_run(conn, destination_ids[0], "ok")
                for table in ("scrape_runs", "companies", "security_identifiers", "source_identifiers",
                              "response_blobs", "fetch_observations", "parse_artifacts", "source_sections",
                              "source_fields", "source_table_cells", "quote_observations",
                              "company_metric_observations", "financial_observations", "growth_observations",
                              "shareholding_observations", "company_profile_links", "company_text_observations",
                              "document_links", "peer_observations", "supplemental_parse_links"):
                    with self.subTest(table=table):
                        self.assertEqual(self.records(conn, table), self.records(self.source, table))
                path = conn.execute("SELECT local_path FROM response_blobs").fetchone()["local_path"]
                self.assertEqual(Path(path).read_bytes(), self.content)

    def test_caller_rollback_undoes_entire_native_ingestion(self):
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with connect(self.settings) as conn, patch.object(repo, "utc_now", return_value=FIRST):
                self.session(conn)
                ids = self.seed(conn)
                repo.store_parsed_company(conn, ids[-1][0], self.parsed, FIRST)
                repo.finish_run(conn, ids[0], "ok")
                raise RuntimeError("rollback")
        with connect(self.settings) as conn:
            self.session(conn)
            for table in ("companies", "scrape_runs", "response_blobs", "parse_artifacts", "financial_observations"):
                self.assertEqual(conn.execute(f'SELECT count(*) AS n FROM "{table}"').fetchone()["n"], 0)
        # Blob storage deliberately retains original source bytes on DB rollback.
        self.assertEqual(next(Path(self.temp.name).rglob("*.html")).read_bytes(), self.content)

    def test_parse_failure_rolls_back_sections_without_closing_managed_connection(self):
        with connect(self.settings) as conn, patch.object(repo, "utc_now", return_value=FIRST):
            self.session(conn)
            ids = self.seed(conn)
            with self.assertRaises(AttributeError):
                repo.store_parsed_company(conn, ids[-1][0], object(), FIRST)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM source_sections").fetchone()["n"], 0)
            self.assertEqual(conn.execute("SELECT parse_status FROM parse_artifacts").fetchone()["parse_status"], "failed")
            self.assertFalse(conn.closed)
        with connect(self.settings) as conn:
            self.session(conn)
            self.assertEqual(conn.execute("SELECT parse_status FROM parse_artifacts").fetchone()["parse_status"], "failed")

    def test_concurrent_parse_cache_and_persistence_have_one_winner(self):
        with connect(self.settings) as conn, patch.object(repo, "utc_now", return_value=FIRST):
            self.session(conn)
            ids = self.seed(conn)
        def create(_):
            with connect(self.settings) as conn:
                self.session(conn)
                return repo.get_or_create_parse_artifact(conn, ids[2], "concurrent", "1", "1")
        with ThreadPoolExecutor(max_workers=4) as workers:
            cached = list(workers.map(create, range(4)))
        self.assertEqual(len(set(cached)), 1)
        parse_id = cached[0][0]
        def persist(_):
            with connect(self.settings) as conn:
                self.session(conn)
                try:
                    repo.store_parsed_company(conn, parse_id, self.parsed, FIRST)
                except ValueError as exc:
                    self.assertIn("Only a pending", str(exc))
                    return "already-stored"
                return "stored"
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(persist, range(2)))
        self.assertCountEqual(outcomes, ["stored", "already-stored"])
        with connect(self.settings) as conn:
            self.session(conn)
            count = conn.execute("SELECT count(*) AS n FROM source_sections").fetchone()["n"]
            self.assertEqual(count, 13)

    def test_concurrent_company_identity_and_source_mapping_converge(self):
        def identify(_):
            with connect(self.settings) as conn:
                self.session(conn)
                company = repo.ensure_company(conn, self.parsed)
                repo.ensure_source_identifier(conn, company, self.parsed, self.response().final_url)
                return company
        with ThreadPoolExecutor(max_workers=4) as workers:
            companies = list(workers.map(identify, range(4)))
        self.assertEqual(len(set(companies)), 1)
        with connect(self.settings) as conn:
            self.session(conn)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM companies").fetchone()["n"], 1)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM source_identifiers").fetchone()["n"], 1)

    def test_native_repositories_reject_idle_connections_before_side_effects(self):
        import psycopg
        from psycopg.pq import TransactionStatus
        from scrapper.db.repository_backend import execute, executemany
        with psycopg.connect(self.settings.dsn, autocommit=True) as idle:
            calls = (
                lambda: execute(idle, "SELECT ?", (1,)),
                lambda: executemany(idle, "SELECT ?", [(1,)]),
                lambda: repo.start_run(idle, "synthetic", {}),
                lambda: repo.ensure_company(idle, self.parsed),
                lambda: repo.get_or_create_parse_artifact(idle, 1, "parser", "1", "1"),
                lambda: repo.store_blob(idle, self.response(), "LAURUSLABS", "standalone", FIRST, Path(self.temp.name)),
            )
            for call in calls:
                with self.assertRaisesRegex(ValueError, "managed PostgreSQL transaction"):
                    call()
                self.assertEqual(idle.info.transaction_status, TransactionStatus.IDLE)
        self.assertFalse(list(Path(self.temp.name).rglob("*.html")))

    def test_repository_session_checks_lineage_and_read_only(self):
        import psycopg
        from scrapper.db.postgresql import repository_session
        with repository_session(self.settings, schema=self.schema) as conn:
            run = repo.start_run(conn, "session", {})
            repo.finish_run(conn, run, "ok")
        with repository_session(self.settings, schema=self.schema, read_only=True) as conn:
            self.assertEqual(conn.execute("SELECT status FROM scrape_runs").fetchone()["status"], "ok")
        with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
            with repository_session(self.settings, schema=self.schema, read_only=True) as conn:
                repo.start_run(conn, "read-only", {})
        with self.assertRaises(ValueError):
            with repository_session(self.settings, schema="public"):
                self.fail("shared schema accepted")
        with self.assertRaises(psycopg.errors.UndefinedTable):
            with repository_session(self.settings, schema=self.schema + "_missing"):
                self.fail("missing lineage accepted")
        with connect(self.settings) as conn:
            self.session(conn)
            conn.execute("ALTER TABLE scrape_runs ADD COLUMN drift text")
        with self.assertRaisesRegex(RuntimeError, "drift"):
            with repository_session(self.settings, schema=self.schema):
                self.fail("schema drift accepted")

    def test_binding_preserves_sql_literals_and_parameter_values(self):
        from scrapper.db.repository_backend import execute
        value = "quote'; DROP TABLE companies; -- ? 50%"
        with connect(self.settings) as conn:
            row = execute(conn, "SELECT ? AS value, 'literal? 50%' AS literal, 7 % 2 AS remainder -- ?\n",
                          (value,)).fetchone()
            self.assertEqual(row, {'value': value, 'literal': 'literal? 50%', 'remainder': 1})

    def test_concurrent_blob_writers_use_independent_temporary_files(self):
        original_replace = os.replace
        ready = threading.Barrier(2)
        temporary_paths = []
        def replace_file(source, target):
            temporary_paths.append(source)
            ready.wait(timeout=10)
            return original_replace(source, target)
        def store(_):
            with connect(self.settings) as conn:
                self.session(conn)
                return repo.store_blob(conn, self.response(), "LAURUSLABS", "standalone", FIRST, Path(self.temp.name))
        with patch("scrapper.db.screener.observations.os.replace", side_effect=replace_file):
            with ThreadPoolExecutor(max_workers=2) as workers:
                blob_ids = list(workers.map(store, range(2)))
        self.assertEqual(len(set(blob_ids)), 1)
        self.assertEqual(len(set(temporary_paths)), 2)
        self.assertFalse(list(Path(self.temp.name).rglob("*.tmp")))
        with connect(self.settings) as conn:
            self.session(conn)
            rows = conn.execute("SELECT local_path FROM response_blobs").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(Path(rows[0]["local_path"]).read_bytes(), self.content)
