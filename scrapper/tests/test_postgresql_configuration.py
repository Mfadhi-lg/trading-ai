"""Configured application startup with actual restricted PostgreSQL logins.

Each test child imports application configuration fresh under isolated filesystem
roots. The administrator touches only the UUID database/schema/roles created by
the existing explicit permission fixture, never a persistent application store.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
import io
from unittest import mock
from urllib.parse import quote

from scrapper.tests import test_postgresql_permissions as permission_fixtures


class SetupCommandInputTests(unittest.TestCase):
    def arguments(self):
        return ['--schema', 'application', '--role', 'application_reader',
                '--policy', 'reader', '--environment', 'test']

    def test_destination_write_flag_is_required_before_connection(self):
        from scrapper.db import postgresql_setup
        with mock.patch.object(postgresql_setup, 'connect') as connect:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                postgresql_setup.main(self.arguments())
        self.assertEqual(caught.exception.code, 2)
        connect.assert_not_called()

    def test_invalid_admin_configuration_and_driver_errors_are_sanitized(self):
        from scrapper.db import postgresql_setup
        arguments = self.arguments() + ['--allow-destination-write']
        for dsn in ('postgresql://synthetic-private:private-password@localhost:invalid/application',
                    'postgresql://synthetic-private:private-password@localhost/application'):
            output = io.StringIO()
            with mock.patch.dict(os.environ, {'TRADING_AI_POSTGRESQL_ADMIN_URL': dsn}):
                with mock.patch.object(postgresql_setup, 'connect', side_effect=RuntimeError('private-password private-source')):
                    with redirect_stdout(output):
                        self.assertEqual(postgresql_setup.main(arguments), 1)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload['status'], 'failed')
            self.assertEqual(set(payload), {'status', 'error_type'})
            self.assertNotIn('private', output.getvalue())


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'),
                     'requires an explicitly disposable PostgreSQL 16 cluster')
class ConfigurationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        permission_fixtures.PermissionIntegrationTests.setUpClass.__func__(cls)
        # A generic application role still has no ledger/view writes, DDL or
        # administrative privilege. Bounded runtime roles use narrower callbacks.
        cls.policy = staticmethod(lambda *_: sqlite3.SQLITE_OK)

    @classmethod
    def drop_database(cls):
        return permission_fixtures.PermissionIntegrationTests.drop_database.__func__(cls)

    @classmethod
    def admin_session(cls):
        return permission_fixtures.PermissionIntegrationTests.admin_session.__func__(cls)

    def setUp(self):
        permission_fixtures.PermissionIntegrationTests.setUp(self)
        from scrapper.db.postgresql_application import mark_schema_identity
        with self.admin_session() as conn:
            self.target_id = mark_schema_identity(conn, self.schema, environment='test')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cleanup(self):
        return permission_fixtures.PermissionIntegrationTests.cleanup(self)

    def dsn(self, *, admin=False):
        fields = self.admin
        user = fields.get('user', '') if admin else self.role
        password = fields.get('password', '') if admin else self.password
        authentication = quote(user, safe='') + (':' + quote(password, safe='') if password else '')
        host = fields.get('host', '127.0.0.1')
        if ':' in host and not host.startswith('['):
            host = '[' + host + ']'
        return 'postgresql://' + authentication + '@' + host + ':' + str(fields.get('port', 5432)) + '/' + quote(self.database, safe='')

    def child(self, body, *, changes=None, expected_error=None):
        environment = os.environ.copy()
        for key in ('TRADING_AI_RUNTIME_IDENTITY_FILE', 'TRADING_AI_CLONE_IDENTITY_FILE',
                    'SCRAPER_DOCUMENT_STORE_PATH'):
            environment.pop(key, None)
        environment.update({
            'TRADING_AI_ENV': 'test', 'TRADING_AI_DB_BACKEND': 'postgresql',
            'DATABASE_URL': self.dsn(), 'TRADING_AI_POSTGRESQL_SCHEMA': self.schema,
            'TRADING_AI_POSTGRESQL_TARGET_ID': self.target_id,
            'SCRAPER_DB_PATH': str(self.root / 'state' / 'app.db'),
            'SCRAPER_STORE_PATH': str(self.root / 'store'),
            'PYTHONDONTWRITEBYTECODE': '1',
        })
        for key, leaf in (
            ('SCRAPER_FILES_PATH', 'files'), ('SCRAPER_SNAPSHOTS_PATH', 'snapshots'),
            ('SCRAPER_EXPORTS_PATH', 'exports'), ('SCRAPER_CAMPAIGNS_PATH', 'campaigns'),
            ('SCRAPER_LOGS_PATH', 'logs'), ('SCRAPER_BROWSER_STATE_PATH', 'browser-state'),
            ('SCRAPER_BACKUPS_PATH', 'backups'),
        ):
            environment[key] = str(self.root / 'store' / leaf)
        environment.update(changes or {})
        script = '''import json, os, sqlite3
from pathlib import Path
from scrapper.db import database
try:
'''
        script += '\n'.join('    ' + line for line in body.splitlines())
        script += '''
except Exception as error:
    print(json.dumps({'error': type(error).__name__, 'message': str(error)}))
else:
    print(json.dumps({'error': None}))
'''
        result = subprocess.run([sys.executable, '-B', '-c', script],
                                cwd=Path(__file__).resolve().parents[2], env=environment,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(payload['error'], expected_error, payload)
        # Selection must never create an ignored SQLite database as fallback.
        self.assertFalse((self.root / 'state' / 'app.db').exists())
        return payload

    def test_central_factory_connects_restricted_login_and_preserves_schema(self):
        self.child('''conn = database.connect(Path(os.environ['SCRAPER_DB_PATH']))
database.init_db(conn)
assert not conn.in_transaction
with conn:
    row = conn.execute("INSERT INTO companies(current_legal_name) VALUES (?) RETURNING company_id", ('synthetic configured source',)).fetchone()
    assert row['company_id'] > 0
assert not conn.in_transaction
assert conn.execute('SELECT current_user').fetchone()[0] == os.environ['DATABASE_URL'].split('://')[1].split(':')[0]
conn.close()''')

    def test_read_only_factory_rejects_writes_and_creates_no_store(self):
        self.child('''conn = database.connect(Path(os.environ['SCRAPER_DB_PATH']), read_only=True)
database.init_db(conn)
assert conn.execute('SELECT count(*) FROM companies').fetchone()[0] == 0
assert not conn.in_transaction
try:
    conn.execute("INSERT INTO companies(current_legal_name) VALUES ('denied')")
except sqlite3.OperationalError:
    pass
else:
    raise AssertionError('read-only mutation succeeded')
conn.close()''')
        self.assertFalse((self.root / 'store').exists())

    def test_invalid_target_and_environment_never_fall_back_to_sqlite(self):
        body = "conn = database.connect(Path(os.environ['SCRAPER_DB_PATH']))"
        for changes in (
            {'TRADING_AI_POSTGRESQL_TARGET_ID': 'invalid-uuid'},
            {'TRADING_AI_POSTGRESQL_TARGET_ID': '00000000-0000-4000-8000-000000000001'},
            {'TRADING_AI_ENV': 'development'},
            {'TRADING_AI_ENV': ''},
            {'DATABASE_URL': 'sqlite:///invalid-backend'},
        ):
            with self.subTest(keys=tuple(changes)):
                self.child(body, changes=changes,
                           expected_error='ValueError' if 'DATABASE_URL' in changes else 'RuntimeError')

    def test_owner_or_shared_schema_is_rejected(self):
        body = "conn = database.connect(Path(os.environ['SCRAPER_DB_PATH']), read_only=True)"
        self.child(body, changes={'DATABASE_URL': self.dsn(admin=True)},
                   expected_error='PermissionPolicyError')
        self.child(body, changes={'TRADING_AI_POSTGRESQL_SCHEMA': 'public'}, expected_error='ValueError')

    def test_schema_drift_is_rejected_before_application_work(self):
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('ALTER TABLE {}.companies ADD COLUMN unreviewed text').format(self.sql.Identifier(self.schema)))
        self.child("conn = database.connect(Path(os.environ['SCRAPER_DB_PATH']), read_only=True)",
                   expected_error='RuntimeError')

    def test_ledger_privilege_drift_is_rejected_without_installing_callback(self):
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('GRANT UPDATE(version) ON {}._postgresql_migrations TO {}').format(
                self.sql.Identifier(self.schema), self.sql.Identifier(self.role)))
        self.child("conn = database.connect(Path(os.environ['SCRAPER_DB_PATH']), read_only=True)",
                   expected_error='PermissionPolicyError')

    def test_generic_role_cannot_install_narrower_runtime_policy(self):
        self.child('''from scrapper.tests.test_postgresql_permissions import routine_authorizer
conn = database.connect(Path(os.environ['SCRAPER_DB_PATH']))
try:
    conn.set_authorizer(routine_authorizer())
finally:
    conn.close()''', expected_error='PermissionPolicyError')

    def setup_role(self):
        role = 'setup_role_' + uuid.uuid4().hex
        def cleanup_role():
            with self.admin_session() as conn:
                if conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                    conn.execute(self.sql.SQL('DROP OWNED BY {} CASCADE').format(self.sql.Identifier(role)))
                    conn.execute(self.sql.SQL('DROP ROLE {}').format(self.sql.Identifier(role)))
        self.addCleanup(cleanup_role)
        return role

    def setup_command(self, role, *, target_id=None, policy='application', expected_success=True):
        environment = os.environ.copy()
        environment['TRADING_AI_POSTGRESQL_ADMIN_URL'] = self.dsn(admin=True)
        arguments = [sys.executable, '-B', '-m', 'scrapper.db.postgresql_setup',
                     '--schema', self.schema, '--role', role, '--policy', policy,
                     '--environment', 'test', '--allow-destination-write']
        if target_id is not None:
            arguments.extend(['--target-id', target_id])
        result = subprocess.run(arguments, cwd=Path(__file__).resolve().parents[2],
                                env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0 if expected_success else 1, result.stderr)
        payload = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(payload['status'], 'committed' if expected_success else 'failed')
        return payload

    def identity(self, conn):
        return conn.execute("SELECT obj_description(oid,'pg_namespace') AS identity FROM pg_namespace WHERE nspname=%s",
                            (self.schema,)).fetchone()['identity']

    def test_setup_command_marks_new_target_and_creates_passwordless_reader(self):
        role = self.setup_role()
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('COMMENT ON SCHEMA {} IS NULL').format(self.sql.Identifier(self.schema)))
        report = self.setup_command(role, policy='reader')
        self.assertEqual(report['schema'], self.schema)
        self.assertNotEqual(report['target_id'], self.target_id)
        with self.admin_session() as conn:
            marker = json.loads(self.identity(conn))
            self.assertEqual(marker, {'application': 'trading-ai', 'environment': 'test',
                                      'schema': self.schema, 'target_id': report['target_id']})
            actor = conn.execute('SELECT rolcanlogin,rolinherit,rolsuper,rolpassword FROM pg_authid WHERE rolname=%s', (role,)).fetchone()
            self.assertTrue(actor['rolcanlogin'])
            self.assertFalse(actor['rolinherit'])
            self.assertFalse(actor['rolsuper'])
            self.assertIsNone(actor['rolpassword'])
            self.assertFalse(conn.execute("SELECT has_table_privilege(%s,%s,'INSERT') AS allowed",
                                          (role, self.schema + '.companies')).fetchone()['allowed'])

    def test_setup_command_preserves_existing_identity_only_with_explicit_uuid(self):
        role = self.setup_role()
        with self.admin_session() as conn:
            original = self.identity(conn)
        self.setup_command(role, expected_success=False)
        with self.admin_session() as conn:
            self.assertEqual(self.identity(conn), original)
            self.assertIsNone(conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone())
        report = self.setup_command(role, target_id=self.target_id)
        self.assertEqual(report['target_id'], self.target_id)
        with self.admin_session() as conn:
            self.assertEqual(self.identity(conn), original)

    def test_setup_command_refuses_unknown_identity_and_rolls_back_role(self):
        for marker in (
            {'environment': 'test'},
            {'application': 'other', 'environment': 'test', 'schema': self.schema,
             'target_id': self.target_id},
            {'application': 'trading-ai', 'environment': 'test', 'schema': 'different_schema',
             'target_id': self.target_id},
        ):
            role = self.setup_role()
            original = json.dumps(marker, sort_keys=True)
            with self.admin_session() as conn:
                conn.execute(self.sql.SQL('COMMENT ON SCHEMA {} IS {}').format(
                    self.sql.Identifier(self.schema), self.sql.Literal(original)))
            target_id = self.target_id if 'target_id' in marker else None
            with self.subTest(marker_keys=tuple(marker)):
                self.setup_command(role, target_id=target_id, expected_success=False)
                with self.admin_session() as conn:
                    self.assertEqual(self.identity(conn), original)
                    self.assertIsNone(conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone())

    def test_setup_command_checks_full_schema_before_creating_role(self):
        role = self.setup_role()
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('ALTER TABLE {}.companies ADD COLUMN unreviewed text').format(self.sql.Identifier(self.schema)))
        self.setup_command(role, target_id=self.target_id, expected_success=False)
        with self.admin_session() as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone())
