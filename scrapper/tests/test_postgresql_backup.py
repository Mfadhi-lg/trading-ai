"""Repeatable synthetic pg_dump/pg_restore drill; never accepts a runtime URL.

Run with the explicitly disposable PostgreSQL service from postgresql.yml.
Artifacts and the restore database exist only for the duration of the test.
"""
from contextlib import closing
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from urllib.parse import urlsplit, urlunsplit
import uuid

from scrapper.db.postgresql import PostgreSQLSettings, connect
from scrapper.db.postgresql_schema import check_compatibility, migrate
from scrapper.db.postgresql_transfer import export_snapshot, transfer
from scrapper.tests.test_postgresql_transfer import make_source


def disposable_settings(environ):
    """Fail before any connection unless the dedicated local test target is set."""
    dsn = environ.get('TRADING_AI_POSTGRESQL_TEST_DSN', '')
    url = urlsplit(dsn)
    if (environ.get('TRADING_AI_ENV') != 'test'
            or url.scheme != 'postgresql' or url.hostname not in ('127.0.0.1', 'localhost')
            or url.path != '/trading_ai_test' or url.query or url.fragment):
        raise ValueError('Backup drill requires the isolated local trading_ai_test service')
    return PostgreSQLSettings(dsn)


def portable_ddl(content):
    # Patched PostgreSQL clients randomize these psql injection-protection keys.
    return re.sub(rb'^\\(?:un)?restrict \S+\r?\n', b'', content, flags=re.M)


def client(tool, settings, *args):
    from psycopg.conninfo import conninfo_to_dict
    params = conninfo_to_dict(settings.dsn)
    # Do not inherit PGOPTIONS, service files, runtime URLs or production auth.
    env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    env.update(PGHOST=params['host'], PGPORT=params.get('port', '5432'),
               PGDATABASE=params['dbname'], PGUSER=params.get('user', ''),
               PGPASSWORD=params.get('password', ''), PGPASSFILE=os.devnull,
               PGCONNECT_TIMEOUT='5', PGSSLMODE='disable')
    result = subprocess.run([tool, *args], env=env, capture_output=True, timeout=120)
    if result.returncode:
        # Native diagnostics can contain SQL or connection details.
        raise RuntimeError(f'{tool} failed (exit {result.returncode}); diagnostics withheld')
    return result.stdout


def inventory(settings, schema):
    """Counts and exact row digests include financial strings, provenance and ledger."""
    from psycopg import sql
    with connect(settings, read_only=True) as conn:
        tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (schema,)).fetchall()
        result = {}
        for row in tables:
            name = row['tablename']
            rows = conn.execute(sql.SQL('SELECT * FROM {}.{}').format(
                sql.Identifier(schema), sql.Identifier(name))).fetchall()
            encoded = sorted(json.dumps(row, sort_keys=True, ensure_ascii=True, default=str) for row in rows)
            result[name] = (len(rows), hashlib.sha256(json.dumps(encoded).encode()).hexdigest())
        for row in conn.execute('SELECT sequencename FROM pg_sequences WHERE schemaname=%s ORDER BY sequencename', (schema,)):
            name = row['sequencename']
            result['sequence:' + name] = conn.execute(sql.SQL('SELECT last_value,is_called FROM {}.{}').format(
                sql.Identifier(schema), sql.Identifier(name))).fetchone()
        return result


class BackupSafetyTests(unittest.TestCase):
    def test_rejects_runtime_or_remote_targets_before_connecting(self):
        good = {'TRADING_AI_ENV': 'test', 'TRADING_AI_POSTGRESQL_TEST_DSN':
                'postgresql://postgres:postgres@127.0.0.1:5432/trading_ai_test'}
        self.assertIsInstance(disposable_settings(good), PostgreSQLSettings)
        for update in ({'TRADING_AI_ENV': 'production'}, {'TRADING_AI_ENV': ''},
                       {'TRADING_AI_POSTGRESQL_TEST_DSN': 'postgresql://host/production'},
                       {'TRADING_AI_POSTGRESQL_TEST_DSN': good['TRADING_AI_POSTGRESQL_TEST_DSN'] + '?service=production'},
                       {'TRADING_AI_POSTGRESQL_TEST_DSN': ''}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                disposable_settings(good | update)

    def test_portable_ddl_removes_only_random_client_keys(self):
        self.assertEqual(portable_ddl(b'\\restrict abc\nCREATE TABLE x();\n\\unrestrict abc\n'), b'CREATE TABLE x();\n')
        self.assertNotEqual(portable_ddl(b'CREATE TABLE x();'), portable_ddl(b'CREATE TABLE y();'))


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'),
                     'requires explicitly disposable PostgreSQL 16 service')
class BackupRestoreTests(unittest.TestCase):
    def test_custom_archive_restores_schema_rows_financial_evidence_and_sequences(self):
        import psycopg
        from psycopg import sql
        settings = disposable_settings(os.environ)
        schema = 'backup_test_' + uuid.uuid4().hex
        database = 'restore_test_' + uuid.uuid4().hex
        url = urlsplit(settings.dsn)
        restored = replace(settings, dsn=urlunsplit(url._replace(path='/' + database)))
        # Only this newly allocated database is ever removed; the service DB is retained.
        with psycopg.connect(settings.dsn, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(database)))
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                root.chmod(0o700)
                source = root / 'synthetic.sqlite3'
                with closing(make_source(source)):
                    pass
                snapshot = root / 'snapshot.sqlite3'
                report = export_snapshot(source, snapshot)
                with connect(settings) as conn:
                    migrate(conn, schema)
                transfer(settings, snapshot, schema, expected_sha256=report['snapshot_sha256'])
                with connect(settings, read_only=True) as conn:
                    check_compatibility(conn, schema)
                archive = root / 'backup.dump'
                client('pg_dump', settings, '--format=custom', '--no-owner', '--no-privileges',
                       '--schema=' + schema, '--file=' + str(archive))
                archive.chmod(0o600)
                self.assertGreater(archive.stat().st_size, 0)
                client('pg_restore', restored, '--dbname=' + database, '--exit-on-error',
                       '--no-owner', '--no-privileges', str(archive))
                ddl_args = ('--schema-only', '--no-owner', '--no-privileges', '--schema=' + schema)
                self.assertEqual(portable_ddl(client('pg_dump', settings, *ddl_args)),
                                 portable_ddl(client('pg_dump', restored, *ddl_args)))
                expected = inventory(settings, schema)
                self.assertEqual(expected, inventory(restored, schema))
                self.assertEqual(expected['company_metric_observations'][0], 4)
                self.assertEqual(expected['artifact_contents'][0], 1)
                # Prove same-count corruption cannot pass the verification.
                with connect(restored) as conn:
                    conn.execute(sql.SQL("UPDATE {}.company_metric_observations SET value_decimal='0' WHERE position=3").format(sql.Identifier(schema)))
                self.assertNotEqual(expected, inventory(restored, schema))
                with connect(restored) as conn:
                    conn.execute(sql.SQL('DROP VIEW {}.artifact_retry_coverage').format(sql.Identifier(schema)))
                self.assertNotEqual(portable_ddl(client('pg_dump', settings, *ddl_args)),
                                    portable_ddl(client('pg_dump', restored, *ddl_args)))
        finally:
            try:
                with connect(settings) as conn:
                    conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
            finally:
                with psycopg.connect(settings.dsn, autocommit=True) as admin:
                    admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
