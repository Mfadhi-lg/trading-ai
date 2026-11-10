# PostgreSQL application permissions

`postgresql_permissions.provision_policy(conn, schema, role, authorizer)` is an
explicit administrator setup action. It creates a new dedicated `LOGIN
NOINHERIT` role without a password, grants read access to the reviewed application
schema and sequence `USAGE`, and compiles the existing pure SQLite authorization
callback into exact table INSERT/DELETE and column UPDATE privileges. It does not
inspect SQL text or install an emulated authorizer. PostgreSQL enforces the
privileges for ordinary application calls and direct driver SQL alike.

The operator must use a dedicated application database whose `PUBLIC` privileges
have been reviewed. Database `CREATE` and `TEMPORARY`, and schema `CREATE`, must
not reach the application login. For a **new isolated database**, the explicit
administrator setup includes:

```sql
REVOKE CREATE, TEMPORARY ON DATABASE your_dedicated_database FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

Provisioning does not execute these database-wide changes. If ambient PUBLIC
privileges are unsafe, the role creation/grants roll back and provisioning fails.
Existing roles, passwords, schema comments and application rows are never changed
by provisioning. Authentication is a separate operator action: configure the new
login through the deployment secret mechanism, then connect as that login. Never
put an administrator DSN or credential in application configuration.

The application schema must already match the reviewed baseline catalogue.
Ordinary startup never creates a schema, applies a migration, creates a role or
repairs permissions. `schema_migrations` and `_postgresql_migrations` remain
read-only for application roles under every callback; views also receive no write
privileges. Migration administration uses its own connection and identity.

`assert_safe_login(conn, schema)` checks the actual login and effective rights.
The session user must equal the current user. The login must not own application
objects or the current database, inherit roles, have role memberships, or possess
superuser, CREATEROLE, CREATEDB, REPLICATION or BYPASSRLS attributes. It must not
have CREATE/TEMP rights, forbidden table/column grant options, TRUNCATE, TRIGGER,
REFERENCES, writable external-schema objects or sequence UPDATE. Callable user
SECURITY DEFINER functions and owned user functions are rejected. Ownership
dependencies also cover types and other application objects. Trigger-bypass
`session_replication_role` settings and SET grants are rejected. This check
includes privileges inherited from PUBLIC, not merely grants naming the login.

`assert_policy(conn, schema, authorizer)` additionally requires effective
INSERT/DELETE and per-column UPDATE privileges to match the selected callback
exactly across all non-system schemas. An unrestricted login cannot install a
routine-document policy by calling this function; it must use the separately
provisioned routine-document login. `SET ROLE` from an administrator is rejected.
Passing a missing or broader callback fails validation; it never changes grants.
Removing an application callback therefore cannot remove the server boundary.

Supported callbacks are pure allow/deny write whitelists taking SQLite's
`(action, table, column, database, trigger)` arguments. Compilation uses database
`main` and a top-level operation (`trigger=None`). Native invoker-security schema
triggers remain subject to the same role permissions. Callbacks that depend on
runtime state, row contents or trigger-specific exemptions require a separately
reviewed native policy; they are not SQL authorization substitutes.

## Verification

Run against an explicitly disposable PostgreSQL 16 cluster:

```sh
TRADING_AI_ENV=test TRADING_AI_POSTGRESQL_TEST_DSN="$DISPOSABLE_CLUSTER_DSN" \
  python -B -m unittest scrapper.tests.test_postgresql_permissions -v
```

The test administrator creates one UUID-named database and UUID roles/schemas,
then drops only those objects. PUBLIC privilege changes occur only in that newly
created database. Tests set synthetic role passwords separately so that CI can
exercise actual restricted logins under password authentication. No production
credentials, source records, persistent application database or runtime are used.
