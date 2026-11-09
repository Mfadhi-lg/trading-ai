"""Native backend contracts; live cases require an explicitly disposable DSN."""
import os
from unittest import TestCase, mock
import unittest
import uuid

from scrapper.db.postgresql import PostgreSQLPool, PostgreSQLSettings, connect


class SettingsTests(TestCase):
    def test_explicit_backend_and_url_required(self):
        for env in ({}, {"DATABASE_URL": "postgresql://localhost/test"},
                    {"TRADING_AI_DB_BACKEND": "sqlite"},
                    {"TRADING_AI_DB_BACKEND": "postgresql"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                PostgreSQLSettings.from_env(env)
        settings = PostgreSQLSettings.from_env({
            "TRADING_AI_DB_BACKEND": "postgresql",
            "DATABASE_URL": "postgresql://user:secret@localhost/test",
        })
        self.assertNotIn("secret", repr(settings))
        self.assertNotIn("localhost", repr(settings))

    def test_invalid_url_errors_hide_credentials(self):
        for dsn in ("sqlite:///test", "postgresql://user:secret@localhost:wrong/test",
                    "postgresql://user:secret@localhost", "", "not a URL", None):
            with self.subTest(dsn=dsn), self.assertRaises(ValueError) as caught:
                PostgreSQLSettings(dsn)
            self.assertNotIn("secret", str(caught.exception))

    def test_bounded_pool_and_timeouts(self):
        for kwargs in ({"max_size": 0}, {"max_size": 33}, {"min_size": 5},
                       {"min_size": -1}, {"connect_timeout": 0},
                       {"statement_timeout_ms": -1}, {"lock_timeout_ms": 0},
                       {"acquire_timeout": 0}, {"idle_transaction_timeout_ms": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PostgreSQLSettings("postgresql://localhost/test", **kwargs)

    def test_constructor_does_not_open_pool(self):
        fake_pool = mock.Mock()
        fake_driver = mock.Mock()
        # Connection is subclassed for sanitized worker diagnostics.
        fake_driver.Connection = type("Connection", (), {})
        with mock.patch("scrapper.db.postgresql._dependencies", return_value=(
            fake_driver, object(), fake_pool,
        )):
            PostgreSQLPool(PostgreSQLSettings("postgresql://localhost/test"))
        self.assertFalse(fake_pool.call_args.kwargs["open"])
        self.assertEqual(fake_pool.call_args.kwargs["max_waiting"], 4)
        fake_pool.return_value.open.assert_not_called()

    def test_direct_connection_errors_are_sanitized(self):
        class DriverError(Exception):
            pass
        driver = mock.Mock()
        driver.Error = DriverError
        driver.connect.side_effect = DriverError("password=secret")
        with mock.patch("scrapper.db.postgresql._dependencies", return_value=(driver, None, None)):
            with self.assertRaises(RuntimeError) as caught:
                with connect(PostgreSQLSettings("postgresql://user:secret@localhost/test")):
                    self.fail("connection should fail")
        self.assertNotIn("secret", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_wrong_server_major_closes_connection(self):
        driver = mock.Mock()
        driver.Error = RuntimeError
        driver.connect.return_value.info.server_version = 170000
        with mock.patch("scrapper.db.postgresql._dependencies", return_value=(driver, None, None)):
            with self.assertRaisesRegex(RuntimeError, "requires PostgreSQL 16"):
                with connect(PostgreSQLSettings("postgresql://localhost/test")):
                    self.fail("unsupported server should fail")
        driver.connect.return_value.close.assert_called_once()


@unittest.skipUnless(os.environ.get("TRADING_AI_POSTGRESQL_TEST_DSN"),
                     "set TRADING_AI_POSTGRESQL_TEST_DSN to an explicitly disposable PostgreSQL 16 database")
class PostgreSQLIntegrationTests(TestCase):
    def setUp(self):
        self.settings = PostgreSQLSettings(os.environ["TRADING_AI_POSTGRESQL_TEST_DSN"], max_size=1)
        self.schema = "backend_test_" + uuid.uuid4().hex
        from psycopg import sql
        self.sql = sql
        with connect(self.settings) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            conn.execute(sql.SQL("CREATE TABLE {}.entries (id bigint PRIMARY KEY, raw text)").format(
                sql.Identifier(self.schema)))

    def tearDown(self):
        with connect(self.settings) as conn:
            conn.execute(self.sql.SQL("DROP SCHEMA {} CASCADE").format(self.sql.Identifier(self.schema)))

    def insert(self, conn, value):
        conn.execute(self.sql.SQL("INSERT INTO {}.entries VALUES (%s, %s)").format(
            self.sql.Identifier(self.schema)), (value, "0.00000000000000000001"))

    def test_commit_rollback_mapping_rows_and_close(self):
        with connect(self.settings) as conn:
            self.insert(conn, 1)
            self.assertEqual(conn.execute("SELECT current_setting('TimeZone') AS tz").fetchone()["tz"], "UTC")
        self.assertTrue(conn.closed)
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with connect(self.settings) as failed:
                self.insert(failed, 2)
                raise RuntimeError("rollback")
        self.assertTrue(failed.closed)
        with connect(self.settings, read_only=True) as reader:
            rows = reader.execute(self.sql.SQL("SELECT * FROM {}.entries").format(
                self.sql.Identifier(self.schema))).fetchall()
        self.assertEqual(rows, [{"id": 1, "raw": "0.00000000000000000001"}])

    def test_read_only_blocks_writes(self):
        import psycopg
        with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
            with connect(self.settings, read_only=True) as conn:
                self.insert(conn, 1)

    def test_pool_rollback_reset_timeout_and_shutdown(self):
        import psycopg
        from psycopg_pool import PoolClosed
        with PostgreSQLPool(self.settings) as pool:
            with pool.connection() as conn:
                conn.execute("SET application_name='temporary backend test'")
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with pool.connection() as conn:
                    self.insert(conn, 2)
                    raise RuntimeError("rollback")
            with pool.connection(read_only=True) as conn:
                self.assertNotEqual(conn.execute("SHOW application_name").fetchone()["application_name"],
                                    "temporary backend test")
                count = conn.execute(self.sql.SQL("SELECT count(*) AS n FROM {}.entries").format(
                    self.sql.Identifier(self.schema))).fetchone()["n"]
                self.assertEqual(count, 0)
                self.assertEqual(conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"], "30s")
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                with pool.connection(read_only=True) as conn:
                    self.insert(conn, 3)
            with pool.connection() as conn:
                self.insert(conn, 4)
        with self.assertRaises(PoolClosed):
            with pool.connection():
                self.fail("closed pool acquired connection")

    def test_pool_exhaustion(self):
        from psycopg_pool import PoolTimeout, TooManyRequests
        with PostgreSQLPool(self.settings) as pool:
            with pool.connection():
                with self.assertRaises(PoolTimeout):
                    # Hold the only lease: acquisition must fail within its bound.
                    with pool._pool.connection(timeout=0.02):
                        self.fail("pool exceeded maximum")
                # Pending request slots may include timed-out borrowers until a
                # connection returns; they still cannot exceed max_waiting.
                with self.assertRaises(TooManyRequests):
                    with pool._pool.connection(timeout=0.02):
                        self.fail("pool exceeded waiting bound")

    def test_statement_timeout_rolls_back_and_pool_recovers(self):
        import psycopg
        settings = PostgreSQLSettings(self.settings.dsn, max_size=1, statement_timeout_ms=25)
        with PostgreSQLPool(settings) as pool:
            with self.assertRaises(psycopg.errors.QueryCanceled):
                with pool.connection() as conn:
                    self.insert(conn, 1)
                    conn.execute("SELECT pg_sleep(0.2)")
            with pool.connection(read_only=True) as conn:
                self.assertEqual(conn.execute(self.sql.SQL("SELECT count(*) AS n FROM {}.entries").format(
                    self.sql.Identifier(self.schema))).fetchone()["n"], 0)


if __name__ == "__main__":
    unittest.main()
