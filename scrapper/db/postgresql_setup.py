"""Explicit administrator setup for a new PostgreSQL application login.

Never called by normal application startup. Authentication/credentials and
database-wide PUBLIC privilege changes remain explicit operator actions.
"""
import argparse
import json
import os
import sqlite3
import uuid

from .postgresql import PostgreSQLSettings, connect
from .postgresql_application import mark_schema_identity
from .postgresql_permissions import provision_policy
from .postgresql_schema import check_compatibility


def application_policy(action, table, column, database, trigger):
    """Full application DML; native provisioning always protects both ledgers."""
    return sqlite3.SQLITE_OK


def reader_policy(action, table, column, database, trigger):
    return sqlite3.SQLITE_DENY if action in (
        sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) else sqlite3.SQLITE_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', required=True)
    parser.add_argument('--environment', choices=('test', 'development', 'production'), required=True)
    parser.add_argument('--role', required=True)
    parser.add_argument('--policy', choices=('application', 'reader'), required=True)
    parser.add_argument('--target-id', help='Keep an existing reviewed target identity UUID')
    parser.add_argument('--allow-destination-write', action='store_true', required=True)
    args = parser.parse_args(argv)
    try:
        settings = PostgreSQLSettings(os.environ.get('TRADING_AI_POSTGRESQL_ADMIN_URL', ''))
        policy = application_policy if args.policy == 'application' else reader_policy
        with connect(settings) as conn:
            check_compatibility(conn, args.schema)
            existing = conn.execute("SELECT obj_description(oid,'pg_namespace') AS identity FROM pg_namespace WHERE nspname=%s", (args.schema,)).fetchone()
            if existing and existing['identity']:
                identity = json.loads(existing['identity'])
                target_id = str(uuid.UUID(args.target_id)) if args.target_id else None
                expected = {'application': 'trading-ai', 'environment': args.environment,
                            'schema': args.schema, 'target_id': target_id}
                if target_id is None or identity != expected:
                    raise ValueError('Existing target identity must be preserved explicitly')
            provision_policy(conn, args.schema, args.role, policy)
            target_id = mark_schema_identity(conn, args.schema, environment=args.environment,
                                             target_id=args.target_id)
        print(json.dumps({'status': 'committed', 'schema': args.schema, 'role': args.role,
                          'environment': args.environment, 'target_id': target_id,
                          'authentication': 'configure separately; no password was assigned'}))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
