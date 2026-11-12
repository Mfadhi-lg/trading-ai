"""Compile pure SQLite write policies into native PostgreSQL grants.

Provisioning is an explicit administrator action. Runtime checks only inspect
catalogues: they never SET ROLE, repair grants, or emulate a SQL authorizer.
"""
import re
import sqlite3

from .postgresql_schema import manifest, validate_schema_name, check_compatibility, application_objects


class PermissionPolicyError(RuntimeError):
    """The native login cannot enforce the selected application write boundary."""


_LEDGERS = frozenset({'schema_migrations', '_postgresql_migrations'})
_RELATIONS = """SELECT c.oid,n.nspname AS schema,c.relname AS name,c.relkind,
    c.relowner=%s AS owned,
    has_table_privilege(%s,c.oid,'DELETE') AS "delete",
    has_table_privilege(%s,c.oid,'TRUNCATE') AS "truncate",
    has_table_privilege(%s,c.oid,'TRIGGER') AS "trigger",
    has_table_privilege(%s,c.oid,'REFERENCES') AS "references",
    has_table_privilege(%s,c.oid,'INSERT WITH GRANT OPTION') AS grant_insert,
    has_table_privilege(%s,c.oid,'UPDATE WITH GRANT OPTION') AS grant_update,
    has_table_privilege(%s,c.oid,'DELETE WITH GRANT OPTION') AS grant_delete
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema'
      AND c.relkind IN ('r','p','v','m','f') ORDER BY n.nspname,c.relname"""
_COLUMNS = """SELECT c.oid,a.attname AS name,
    has_column_privilege(%s,c.oid,a.attnum,'INSERT') AS "insert",
    has_column_privilege(%s,c.oid,a.attnum,'UPDATE') AS "update",
    has_column_privilege(%s,c.oid,a.attnum,'REFERENCES') AS "references",
    has_column_privilege(%s,c.oid,a.attnum,'INSERT WITH GRANT OPTION') AS grant_insert,
    has_column_privilege(%s,c.oid,a.attnum,'UPDATE WITH GRANT OPTION') AS grant_update
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
    WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema'
      AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped
    ORDER BY c.oid,a.attnum"""


def _callback(authorizer):
    if not callable(authorizer):
        raise PermissionPolicyError('A callable native permission policy is required')


def _allows(authorizer, action, table, column=None):
    # These are SQLite callback input symbols, not SQL strings. Native grants
    # enforce the compiled result even when callers issue raw driver SQL.
    return authorizer(action, table, column, 'main', None) == sqlite3.SQLITE_OK


def _role_name(role):
    if not isinstance(role, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', role) or role.startswith('pg_'):
        raise ValueError('Use a dedicated lowercase PostgreSQL login identifier')


def _actor(conn, role=None):
    identity = conn.execute('SELECT session_user AS session_user,current_user AS current_user').fetchone()
    if role is None:
        if identity['session_user'] != identity['current_user']:
            raise PermissionPolicyError('Use the restricted login directly; SET ROLE sessions are not accepted')
        role = identity['current_user']
    row = conn.execute('''SELECT oid,rolname,rolsuper,rolinherit,rolcreaterole,
        rolcreatedb,rolcanlogin,rolreplication,rolbypassrls
        FROM pg_catalog.pg_roles WHERE rolname=%s''', (role,)).fetchone()
    if row is None or not row['rolcanlogin'] or row['rolinherit'] or any(row[key] for key in (
            'rolsuper','rolcreaterole','rolcreatedb','rolreplication','rolbypassrls')):
        raise PermissionPolicyError('A dedicated LOGIN NOINHERIT without administrative attributes is required')
    if conn.execute('SELECT 1 FROM pg_catalog.pg_auth_members WHERE member=%s LIMIT 1', (row['oid'],)).fetchone():
        raise PermissionPolicyError('Runtime login must have no role memberships or SET ROLE escape')
    if conn.execute('''SELECT 1 FROM pg_catalog.pg_shdepend
        WHERE refclassid='pg_catalog.pg_authid'::regclass AND refobjid=%s
          AND deptype='o' AND (dbid=0 OR dbid=(
            SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database()))
        LIMIT 1''', (row['oid'],)).fetchone():
        raise PermissionPolicyError('Runtime login must not own database or application objects')
    parameters = conn.execute('''SELECT current_setting('session_replication_role') AS replication,
        has_parameter_privilege(%s,'session_replication_role','SET') AS can_disable_triggers''',
        (row['rolname'],)).fetchone()
    if parameters['replication'] != 'origin' or parameters['can_disable_triggers']:
        raise PermissionPolicyError('Runtime login must not bypass schema triggers')
    return row


def _safe_role(conn, schema, role=None):
    validate_schema_name(schema)
    actor = _actor(conn, role)
    oid = actor['oid']
    database = conn.execute('''SELECT datdba=%s AS owned,
        has_database_privilege(%s,oid,'CREATE') AS "create",
        has_database_privilege(%s,oid,'TEMP') AS "temporary"
        FROM pg_catalog.pg_database WHERE datname=current_database()''', (oid, oid, oid)).fetchone()
    if database['owned'] or database['create'] or database['temporary']:
        raise PermissionPolicyError('Runtime login must not own the database or have database CREATE/TEMP; review dedicated database PUBLIC grants')
    schemas = conn.execute('''SELECT nspname,nspowner=%s AS owned,
        has_schema_privilege(%s,oid,'CREATE') AS "create",
        has_schema_privilege(%s,oid,'USAGE') AS "usage"
        FROM pg_catalog.pg_namespace
        WHERE left(nspname,3)<>'pg_' AND nspname<>'information_schema' ''', (oid, oid, oid)).fetchall()
    if any(row['owned'] or row['create'] for row in schemas):
        raise PermissionPolicyError('Runtime login must not own or create objects in any application schema')
    if not any(row['nspname'] == schema and row['usage'] for row in schemas):
        raise PermissionPolicyError('Runtime login requires USAGE on the selected application schema')
    if conn.execute('''SELECT 1 FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema'
          AND (p.proowner=%s OR (p.prosecdef
            AND has_schema_privilege(%s,n.oid,'USAGE')
            AND has_function_privilege(%s,p.oid,'EXECUTE'))) LIMIT 1''', (oid, oid, oid)).fetchone():
        raise PermissionPolicyError('Runtime login must not own user functions or execute user SECURITY DEFINER functions')
    relations = conn.execute(_RELATIONS, (oid,) * 8).fetchall()
    columns = conn.execute(_COLUMNS, (oid,) * 5).fetchall()
    for relation in relations:
        if relation['owned'] or any(relation[key] for key in (
                'truncate','trigger','references','grant_insert','grant_update','grant_delete')):
            raise PermissionPolicyError('Runtime login has ownership, forbidden table privileges or grant options')
    protected = {row['oid'] for row in relations
                 if row['name'] in _LEDGERS or row['relkind'] not in ('r', 'p')}
    if any(row['delete'] for row in relations if row['oid'] in protected) or any(
            row['insert'] or row['update'] for row in columns if row['oid'] in protected):
        raise PermissionPolicyError('Runtime login must not write migration ledgers or views')
    if any(row['references'] or row['grant_insert'] or row['grant_update'] for row in columns):
        raise PermissionPolicyError('Runtime login has forbidden column privileges or grant options')
    foreign = {row['oid'] for row in relations if row['schema'] != schema}
    if any(row['delete'] for row in relations if row['oid'] in foreign) or any(
            row['insert'] or row['update'] for row in columns if row['oid'] in foreign):
        raise PermissionPolicyError('Runtime login has writes outside the selected application schema')
    sequences = conn.execute('''SELECT c.relowner=%s AS owned,n.nspname AS schema,
        has_sequence_privilege(%s,c.oid,'UPDATE') AS "update",
        has_sequence_privilege(%s,c.oid,'USAGE') AS "usage"
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind='S' AND left(n.nspname,3)<>'pg_' ''', (oid, oid, oid)).fetchall()
    if any(row['owned'] or row['update'] or (row['schema'] != schema and row['usage']) for row in sequences):
        raise PermissionPolicyError('Runtime login has sequence ownership, UPDATE or external sequence usage')
    return actor, relations, columns


def assert_safe_login(conn, schema):
    """Verify native login identity and deny privilege escalation, without writes."""
    _safe_role(conn, schema)


def _expected(authorizer, relation, columns, schema):
    writable = (relation['schema'] == schema and relation['relkind'] in ('r','p')
                and relation['name'] not in _LEDGERS)
    insert = writable and _allows(authorizer, sqlite3.SQLITE_INSERT, relation['name'])
    if relation['name'] == 'screener_queue_identities':
        # Invoker-rights queue registration; immutable ledger triggers enforce
        # queue existence and forbid runtime creation of historical identities.
        insert = writable and _allows(authorizer, sqlite3.SQLITE_INSERT, 'screener_company_queue')
        return insert, False, set()
    delete = writable and _allows(authorizer, sqlite3.SQLITE_DELETE, relation['name'])
    updates = {row['name'] for row in columns if writable and _allows(
        authorizer, sqlite3.SQLITE_UPDATE, relation['name'], row['name'])}
    return insert, delete, updates


def _check_policy(schema, authorizer, relations, columns):
    by_relation = {}
    for row in columns:
        by_relation.setdefault(row['oid'], []).append(row)
    for relation in relations:
        members = by_relation.get(relation['oid'], [])
        insert, delete, updates = _expected(authorizer, relation, members, schema)
        if relation['delete'] != delete or any(
                row['insert'] != insert or row['update'] != (row['name'] in updates) for row in members):
            raise PermissionPolicyError('Effective native writes do not exactly match the selected permission policy')


def assert_policy(conn, schema, authorizer):
    """Check server-enforced effective privileges; never SET ROLE or alter grants.

    A different/removed callback cannot remove server restrictions. Schema
    migration ledgers and view writes remain denied under every policy.
    """
    _callback(authorizer)
    _, relations, columns = _safe_role(conn, schema)
    _check_policy(schema, authorizer, relations, columns)


def provision_policy(conn, schema, role, authorizer):
    """Create a new passwordless least-privilege login in an explicit admin txn.

    Never changes an existing role, database PUBLIC grants, credentials, schema
    comments or source tables. The operator configures authentication separately.
    Unsafe ambient PUBLIC privileges make provisioning roll back and fail closed.
    """
    validate_schema_name(schema)
    _role_name(role)
    _callback(authorizer)
    from psycopg import sql
    from psycopg.pq import TransactionStatus
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise PermissionPolicyError('Role provisioning requires a managed administrator transaction')
    check_compatibility(conn, schema)
    expected = set(application_objects()['table']) | {'_postgresql_migrations'}
    tables = conn.execute('''SELECT c.relname FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relkind IN ('r','p','f')''', (schema,)).fetchall()
    if {row['relname'] for row in tables} != expected:
        raise PermissionPolicyError('Provision only the complete reviewed application schema')
    with conn.transaction():
        if conn.execute('SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s', (role,)).fetchone():
            raise PermissionPolicyError('Provisioning requires a new dedicated login; existing roles are never rewritten')
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(sql.Identifier(role)))
        database = conn.execute('SELECT current_database() AS name').fetchone()['name']
        conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database), sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
        # USAGE supports nextval/currval, but cannot rewrite sequence positions.
        conn.execute(sql.SQL('GRANT USAGE ON ALL SEQUENCES IN SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
        actor = conn.execute('SELECT oid FROM pg_catalog.pg_roles WHERE rolname=%s', (role,)).fetchone()
        relations = conn.execute(_RELATIONS, (actor['oid'],) * 8).fetchall()
        columns = conn.execute(_COLUMNS, (actor['oid'],) * 5).fetchall()
        by_relation = {}
        for row in columns:
            by_relation.setdefault(row['oid'], []).append(row)
        for relation in relations:
            insert, delete, updates = _expected(authorizer, relation, by_relation.get(relation['oid'], []), schema)
            target = sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(relation['name']))
            if insert:
                conn.execute(sql.SQL('GRANT INSERT ON {} TO {}').format(target, sql.Identifier(role)))
            if delete:
                conn.execute(sql.SQL('GRANT DELETE ON {} TO {}').format(target, sql.Identifier(role)))
            if updates:
                conn.execute(sql.SQL('GRANT UPDATE ({}) ON {} TO {}').format(
                    sql.SQL(',').join(map(sql.Identifier, sorted(updates))), target, sql.Identifier(role)))
        _, relations, columns = _safe_role(conn, schema, role)
        _check_policy(schema, authorizer, relations, columns)
