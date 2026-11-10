"""Native grants enforce real production write policies even for raw SQL."""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import unittest
import uuid
from unittest import mock

from scrapper.db import postgresql_permissions as permissions


def routine_authorizer():
    activation = Path(__file__).resolve().parents[2] / 'ops/activation'
    spec = importlib.util.spec_from_file_location('native_permission_routine_fixture',
                                                 activation / 'phase4_routine_runtime.py')
    module = importlib.util.module_from_spec(spec)
    # Import source only. main() is not invoked; no production paths are opened.
    with mock.patch.object(sys, 'path', [str(activation), *sys.path]):
        spec.loader.exec_module(module)
    return module.routine_authorizer


class PolicyInputTests(unittest.TestCase):
    def test_callback_and_identifiers_fail_before_sql(self):
        conn = mock.Mock()
        for role in ('postgres;drop', 'pg_owner', '', 'x' * 64):
            with self.subTest(role=role), self.assertRaises(ValueError):
                permissions.provision_policy(conn, 'application', role, lambda *_: sqlite3.SQLITE_OK)
        with self.assertRaises(permissions.PermissionPolicyError):
            permissions.assert_policy(conn, 'application', None)
        conn.execute.assert_not_called()

    def test_actual_routine_policy_preserves_column_and_append_only_rules(self):
        policy = routine_authorizer()
        allow = permissions._allows
        self.assertTrue(allow(policy, sqlite3.SQLITE_INSERT, 'artifact_period_corrections'))
        self.assertFalse(allow(policy, sqlite3.SQLITE_UPDATE, 'artifact_period_corrections', 'period_end'))
        self.assertTrue(allow(policy, sqlite3.SQLITE_UPDATE, 'artifact_recovery_targets', 'status'))
        self.assertFalse(allow(policy, sqlite3.SQLITE_UPDATE, 'artifact_recovery_targets', 'company_id'))
        self.assertFalse(allow(policy, sqlite3.SQLITE_DELETE, 'document_extractions'))
        self.assertTrue(allow(policy, sqlite3.SQLITE_DELETE, 'acquisition_host_permits'))


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'),
                     'requires an explicitly disposable PostgreSQL 16 cluster')
class PermissionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.rows import dict_row
        cls.psycopg, cls.sql, cls.dict_row = psycopg, sql, staticmethod(dict_row)
        cls.cluster = conninfo_to_dict(os.environ['TRADING_AI_POSTGRESQL_TEST_DSN'])
        cls.database = 'permissions_test_' + uuid.uuid4().hex
        with psycopg.connect(**cls.cluster, autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.database)))
        cls.addClassCleanup(cls.drop_database)
        cls.admin = {**cls.cluster, 'dbname': cls.database}
        # This changes only our newly created UUID database, never the supplied
        # database. Explicit database setup is separate from role provisioning.
        with cls.admin_session() as conn:
            conn.execute(sql.SQL('REVOKE CREATE,TEMPORARY ON DATABASE {} FROM PUBLIC').format(
                sql.Identifier(cls.database)))
            conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        cls.policy = staticmethod(routine_authorizer())

    @classmethod
    def drop_database(cls):
        with cls.psycopg.connect(**cls.cluster, autocommit=True) as conn:
            conn.execute(cls.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(
                cls.sql.Identifier(cls.database)))

    @classmethod
    @contextmanager
    def admin_session(cls):
        with cls.psycopg.connect(**cls.admin, autocommit=True, row_factory=cls.dict_row) as conn:
            with conn.transaction():
                yield conn

    def setUp(self):
        from scrapper.db.postgresql_schema import migrate
        self.schema = 'permission_schema_' + uuid.uuid4().hex
        self.role = 'permission_role_' + uuid.uuid4().hex
        self.password = uuid.uuid4().hex  # Synthetic test-only authentication.
        self.addCleanup(self.cleanup)
        with self.admin_session() as conn:
            migrate(conn, self.schema)
            permissions.provision_policy(conn, self.schema, self.role, self.policy)
            # Provisioning intentionally sets no credential. Test authentication
            # is a separate operator-equivalent fixture action for CI SCRAM.
            conn.execute(self.sql.SQL('ALTER ROLE {} PASSWORD {}').format(
                self.sql.Identifier(self.role), self.sql.Literal(self.password)))

    def cleanup(self):
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(
                self.sql.Identifier(self.schema)))
            if conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (self.role,)).fetchone():
                conn.execute(self.sql.SQL('DROP OWNED BY {} CASCADE').format(self.sql.Identifier(self.role)))
                conn.execute(self.sql.SQL('DROP ROLE {}').format(self.sql.Identifier(self.role)))

    @contextmanager
    def session(self):
        settings = {**self.admin, 'user': self.role, 'password': self.password}
        with self.psycopg.connect(**settings, autocommit=True, row_factory=self.dict_row) as conn:
            with conn.transaction():
                conn.execute(self.sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(
                    self.sql.Identifier(self.schema)))
                yield conn

    def denied(self, conn, sql):
        with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute(sql)

    def test_actual_policy_accepts_allowed_writes_and_denies_protected_raw_sql(self):
        with self.session() as conn:
            permissions.assert_safe_login(conn, self.schema)
            permissions.assert_policy(conn, self.schema, self.policy)
            conn.execute("INSERT INTO document_extraction_claims VALUES ('synthetic','owner','token','ACTIVE','2099-01-01T00:00:00+00:00')")
            conn.execute("UPDATE document_extraction_claims SET owner='changed' WHERE resource_key='synthetic'")
            conn.execute("UPDATE artifact_recovery_targets SET status=status WHERE false")
            for statement in (
                "UPDATE artifact_recovery_targets SET company_id=company_id WHERE false",
                "INSERT INTO artifact_recovery_targets DEFAULT VALUES",
                "UPDATE artifact_period_corrections SET corrected_at_utc=corrected_at_utc WHERE false",
                "UPDATE financial_observations SET value_decimal=value_decimal WHERE false",
                "DELETE FROM document_extraction_claims",
                "UPDATE schema_migrations SET version=version WHERE false",
                "UPDATE _postgresql_migrations SET version=version WHERE false",
                "TRUNCATE document_extraction_claims",
                "CREATE TABLE forbidden_creation(id integer)",
                "CREATE TEMP TABLE forbidden_temporary(id integer)",
                "CREATE SCHEMA forbidden_schema",
                "ALTER TABLE document_extraction_claims ADD COLUMN forbidden integer",
            ):
                with self.subTest(statement=statement):
                    self.denied(conn, statement)
            self.assertEqual(conn.execute('SELECT owner FROM document_extraction_claims').fetchone()['owner'], 'changed')

    def test_callback_removal_and_set_role_cannot_remove_server_restrictions(self):
        with self.session() as conn:
            with self.assertRaises(permissions.PermissionPolicyError):
                permissions.assert_policy(conn, self.schema, None)
            with self.assertRaises(permissions.PermissionPolicyError):
                permissions.assert_policy(conn, self.schema, lambda *_: sqlite3.SQLITE_OK)
            conn.execute('RESET ROLE')
            self.denied(conn, 'DELETE FROM document_extractions')
            administrator = self.cluster['user']
            self.denied(conn, self.sql.SQL('SET ROLE {}').format(self.sql.Identifier(administrator)))
            self.denied(conn, self.sql.SQL('ALTER ROLE {} SUPERUSER').format(self.sql.Identifier(self.role)))

    def test_extra_column_or_table_grant_is_detected(self):
        for grant, revoke in (
            ('UPDATE(company_id) ON artifact_recovery_targets', 'UPDATE(company_id) ON artifact_recovery_targets'),
            ('TRUNCATE ON document_extractions', 'TRUNCATE ON document_extractions'),
            ('REFERENCES(company_id) ON companies', 'REFERENCES(company_id) ON companies'),
            ('INSERT ON document_extraction_claims WITH GRANT OPTION', 'GRANT OPTION FOR INSERT ON document_extraction_claims'),
        ):
            with self.subTest(grant=grant):
                with self.admin_session() as conn:
                    conn.execute(self.sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(self.sql.Identifier(self.schema)))
                    # WITH GRANT OPTION belongs after the grantee.
                    base = grant.removesuffix(' WITH GRANT OPTION')
                    suffix = ' WITH GRANT OPTION' if base != grant else ''
                    conn.execute(self.sql.SQL('GRANT ' + base + ' TO {}' + suffix).format(self.sql.Identifier(self.role)))
                with self.assertRaises(permissions.PermissionPolicyError):
                    with self.session() as conn:
                        permissions.assert_policy(conn, self.schema, self.policy)
                with self.admin_session() as conn:
                    conn.execute(self.sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(self.sql.Identifier(self.schema)))
                    conn.execute(self.sql.SQL('REVOKE ' + revoke + ' FROM {}').format(self.sql.Identifier(self.role)))

    def test_membership_and_set_role_sessions_are_rejected(self):
        extra = 'permission_group_' + uuid.uuid4().hex
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('CREATE ROLE {} NOLOGIN').format(self.sql.Identifier(extra)))
            conn.execute(self.sql.SQL('GRANT {} TO {}').format(self.sql.Identifier(extra), self.sql.Identifier(self.role)))
        try:
            with self.assertRaises(permissions.PermissionPolicyError):
                with self.session() as conn:
                    permissions.assert_safe_login(conn, self.schema)
        finally:
            with self.admin_session() as conn:
                conn.execute(self.sql.SQL('REVOKE {} FROM {}').format(self.sql.Identifier(extra), self.sql.Identifier(self.role)))
                conn.execute(self.sql.SQL('DROP ROLE {}').format(self.sql.Identifier(extra)))
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('SET LOCAL ROLE {}').format(self.sql.Identifier(self.role)))
            with self.assertRaises(permissions.PermissionPolicyError):
                permissions.assert_safe_login(conn, self.schema)

    def test_public_create_and_security_definer_are_rejected(self):
        function = 'permission_function_' + uuid.uuid4().hex
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('CREATE FUNCTION public.{}() RETURNS integer LANGUAGE sql SECURITY DEFINER AS \'SELECT 1\'').format(self.sql.Identifier(function)))
        try:
            with self.assertRaises(permissions.PermissionPolicyError):
                with self.session() as conn:
                    permissions.assert_safe_login(conn, self.schema)
        finally:
            with self.admin_session() as conn:
                conn.execute(self.sql.SQL('DROP FUNCTION public.{}()').format(self.sql.Identifier(function)))
        with self.admin_session() as conn:
            conn.execute('GRANT CREATE ON SCHEMA public TO PUBLIC')
        try:
            with self.assertRaises(permissions.PermissionPolicyError):
                with self.session() as conn:
                    permissions.assert_safe_login(conn, self.schema)
        finally:
            with self.admin_session() as conn:
                conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')

    def test_other_schema_write_privileges_are_rejected(self):
        extra = 'permission_external_' + uuid.uuid4().hex
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('CREATE SCHEMA {}').format(self.sql.Identifier(extra)))
            conn.execute(self.sql.SQL('CREATE TABLE {}.extra(value text)').format(self.sql.Identifier(extra)))
            conn.execute(self.sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(self.sql.Identifier(extra), self.sql.Identifier(self.role)))
            conn.execute(self.sql.SQL('GRANT UPDATE(value) ON {}.extra TO {}').format(self.sql.Identifier(extra), self.sql.Identifier(self.role)))
        try:
            with self.assertRaises(permissions.PermissionPolicyError):
                with self.session() as conn:
                    permissions.assert_safe_login(conn, self.schema)
        finally:
            with self.admin_session() as conn:
                conn.execute(self.sql.SQL('DROP SCHEMA {} CASCADE').format(self.sql.Identifier(extra)))

    def test_ownership_or_trigger_bypass_parameter_grants_are_rejected(self):
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('GRANT SET ON PARAMETER session_replication_role TO {}').format(self.sql.Identifier(self.role)))
        try:
            with self.assertRaises(permissions.PermissionPolicyError):
                with self.session() as conn:
                    permissions.assert_safe_login(conn, self.schema)
        finally:
            with self.admin_session() as conn:
                conn.execute(self.sql.SQL('REVOKE SET ON PARAMETER session_replication_role FROM {}').format(self.sql.Identifier(self.role)))
        with self.admin_session() as conn:
            conn.execute(self.sql.SQL('ALTER TABLE {}.document_extraction_claims OWNER TO {}').format(
                self.sql.Identifier(self.schema), self.sql.Identifier(self.role)))
        with self.assertRaises(permissions.PermissionPolicyError):
            with self.session() as conn:
                permissions.assert_safe_login(conn, self.schema)

    def test_provisioning_does_not_modify_existing_roles_or_database_public_grants(self):
        with self.admin_session() as conn:
            with self.assertRaises(permissions.PermissionPolicyError):
                permissions.provision_policy(conn, self.schema, self.role, self.policy)
            conn.execute(self.sql.SQL('GRANT TEMP ON DATABASE {} TO PUBLIC').format(self.sql.Identifier(self.database)))
        extra = 'permission_rejected_' + uuid.uuid4().hex
        try:
            with self.admin_session() as conn:
                with self.assertRaises(permissions.PermissionPolicyError):
                    permissions.provision_policy(conn, self.schema, extra, self.policy)
                self.assertIsNone(conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (extra,)).fetchone())
                self.assertTrue(conn.execute("SELECT has_database_privilege(%s,current_database(),'TEMP') AS allowed", (self.role,)).fetchone()['allowed'])
        finally:
            with self.admin_session() as conn:
                conn.execute(self.sql.SQL('REVOKE TEMP ON DATABASE {} FROM PUBLIC').format(self.sql.Identifier(self.database)))
