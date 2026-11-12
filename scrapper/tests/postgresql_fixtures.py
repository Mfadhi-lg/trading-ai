"""Explicitly disposable native application fixtures, never runtime factories."""
from contextlib import contextmanager
import os
import unittest
import uuid

from scrapper.db.postgresql import PostgreSQLSettings, connect
from scrapper.db.postgresql_schema import migrate
from scrapper.db.postgresql_application import PostgreSQLApplicationConnection


def application_connection(settings, schema, *, read_only=False):
    """Use the fixture admin on a UUID schema; runtime login checks have own tests."""
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    if settings.dsn != os.environ.get("TRADING_AI_POSTGRESQL_TEST_DSN"):
        raise ValueError("Fixture requires the explicitly disposable test DSN")
    raw = psycopg.connect(settings.dsn, autocommit=True, row_factory=dict_row,
                          connect_timeout=5, prepare_threshold=None)
    try:
        raw.execute(sql.SQL("SET search_path TO {}, pg_catalog, pg_temp").format(sql.Identifier(schema)))
        raw.execute("SET TIME ZONE 'UTC'")
        raw.execute("SET statement_timeout='10s'")
        raw.execute("SET lock_timeout='5s'")
        if read_only:
            raw.execute("SET default_transaction_read_only=on")
        return PostgreSQLApplicationConnection(raw, settings, schema, read_only=read_only)
    except BaseException:
        raw.close()
        raise


class DisposablePostgreSQLTestCase(unittest.TestCase):
    def setUp(self):
        dsn = os.environ.get("TRADING_AI_POSTGRESQL_TEST_DSN")
        if not dsn:
            self.skipTest("requires an explicitly disposable PostgreSQL 16 DSN")
        from psycopg import sql
        self.settings = PostgreSQLSettings(dsn)
        self.schema = "application_test_" + uuid.uuid4().hex
        self.sql = sql
        self.addCleanup(self.drop_schema)
        with connect(self.settings) as conn:
            migrate(conn, self.schema)

    def drop_schema(self):
        with connect(self.settings) as conn:
            conn.execute(self.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(self.sql.Identifier(self.schema)))

    def connection(self, *, read_only=False):
        return application_connection(self.settings, self.schema, read_only=read_only)

    @contextmanager
    def session(self, *, read_only=False):
        conn = self.connection(read_only=read_only)
        try:
            yield conn
        finally:
            conn.close()
