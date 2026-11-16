"""Execute the Docker health commands against isolated application stores."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scrapper.tests import test_postgresql_configuration as fixtures

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = ('Dockerfile', 'ops/docker/postgresql/Dockerfile')


def health_command(filename):
    source = (ROOT / filename).read_text()
    command = json.loads(source.split('HEALTHCHECK ', 1)[1].split('CMD ', 1)[1].splitlines()[0])
    if command[:2] != ['python', '-c']:
        raise AssertionError('Expected executable Python health command')
    return command[2]


class ContainerHealthcheckTests(unittest.TestCase):
    def test_missing_sqlite_database_is_not_created(self):
        from scrapper.db import database
        for filename in DOCKERFILES:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                missing = Path(tmp) / 'missing.db'
                with patch.dict(os.environ, {'TRADING_AI_DB_BACKEND': 'sqlite'}), \
                        patch.object(database, 'DB_PATH', missing), \
                        patch('scrapper.config.DB_PATH', missing):
                    # connect's default path is bound at import time.
                    original = database.connect
                    with patch.object(database, 'connect', side_effect=lambda **kw: original(missing, **kw)):
                        with self.assertRaises(FileNotFoundError):
                            exec(health_command(filename), {})
                self.assertFalse(missing.exists())

    def test_empty_sqlite_database_is_not_healthy(self):
        from scrapper.db import database
        for filename in DOCKERFILES:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'empty.db'
                sqlite3.connect(path).close()
                before = path.read_bytes()
                original = database.connect
                with patch.dict(os.environ, {'TRADING_AI_DB_BACKEND': 'sqlite'}), \
                        patch('scrapper.config.DB_PATH', path), \
                        patch.object(database, 'connect', side_effect=lambda **kw: original(path, **kw)):
                    with self.assertRaisesRegex(RuntimeError, 'schema is not initialized'):
                        exec(health_command(filename), {})
                self.assertEqual(path.read_bytes(), before)

    def test_initialized_sqlite_database_passes_without_changes(self):
        from scrapper.db import database
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'initialized.db'
            conn = sqlite3.connect(path)
            try:
                database.apply_migrations(conn)
            finally:
                conn.close()
            before = path.read_bytes()
            original = database.connect
            with patch.dict(os.environ, {'TRADING_AI_DB_BACKEND': 'sqlite'}), \
                    patch.object(database, 'connect', side_effect=lambda **kw: original(path, **kw)):
                for filename in DOCKERFILES:
                    with self.subTest(filename=filename):
                        exec(health_command(filename), {})
            self.assertEqual(path.read_bytes(), before)

    def test_readonly_connection_closes_after_schema_failure(self):
        from scrapper.db import database
        for filename in DOCKERFILES:
            with self.subTest(filename=filename), patch.object(database, 'connect') as connect, \
                    patch.object(database, 'init_db', side_effect=RuntimeError('schema drift')):
                with self.assertRaisesRegex(RuntimeError, 'schema drift'):
                    exec(health_command(filename), {})
                connect.assert_called_once_with(read_only=True)
                connect.return_value.close.assert_called_once_with()


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'), 'requires disposable PostgreSQL')
class NativeHealthcheckTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ConfigurationIntegrationTests.setUpClass.__func__)
    drop_database = classmethod(fixtures.ConfigurationIntegrationTests.drop_database.__func__)
    admin_session = classmethod(fixtures.ConfigurationIntegrationTests.admin_session.__func__)
    setUp = fixtures.ConfigurationIntegrationTests.setUp
    cleanup = fixtures.ConfigurationIntegrationTests.cleanup
    dsn = fixtures.ConfigurationIntegrationTests.dsn
    child = fixtures.ConfigurationIntegrationTests.child

    def test_public_health_commands_verify_native_target_without_sqlite(self):
        for filename in DOCKERFILES:
            with self.subTest(filename=filename):
                body = "sqlite3.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('SQLite fallback'))\n"
                body += health_command(filename)
                self.child(body)
                self.child(body, changes={'TRADING_AI_POSTGRESQL_TARGET_ID':
                    '00000000-0000-4000-8000-000000000001'}, expected_error='RuntimeError')
                self.assertFalse((self.root / 'store').exists())
