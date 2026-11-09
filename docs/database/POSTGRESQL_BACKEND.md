# Native PostgreSQL backend transition

Issue #53 introduces an explicit native connection foundation for PostgreSQL 16.
SQLite remains the application's current runtime backend. PostgreSQL is not
authoritative until repository parity, schema/data migration and operational
acceptance pass. This module does not translate SQLite queries or redirect
existing `scrapper.db.database.connect()` callers.

The compatibility audit is in
[SQLITE_POSTGRESQL_COMPATIBILITY_AUDIT.md](SQLITE_POSTGRESQL_COMPATIBILITY_AUDIT.md).
SQLite repositories use `?`, `lastrowid`, `INSERT OR IGNORE/REPLACE`, SQLite rows,
and `BEGIN IMMEDIATE`; a native connection cannot safely replace them. Queue
claim/fencing and repository ports remain explicit follow-up work. Do not change
production configuration to PostgreSQL on the strength of connection tests.

## Isolated development service

Install the optional pinned driver and pool into a development virtual environment:

```sh
python -m pip install -r scrapper/requirements.postgresql.txt
```

Use a development-only password supplied through the environment, never committed:

```sh
export TRADING_AI_POSTGRESQL_DEV_PASSWORD='<development-only password>'
docker compose -f compose.postgresql.yml up -d --wait
export TRADING_AI_DB_BACKEND=postgresql
export DATABASE_URL='postgresql://trading_ai_dev:<URL-encoded password>@127.0.0.1:55432/trading_ai_dev'
```

The standalone Compose project binds only loopback, creates its own named volume,
and checks readiness with `pg_isready`. It shares no application or production
mounts. `TRADING_AI_POSTGRESQL_DEV_PORT` changes the host port if needed. Use
`docker compose -f compose.postgresql.yml down` to stop it while preserving its
volume. Do not use this file to restart the production acquisition stack.

The version policy currently supports major version 16 only (the development
image pins 16.14). Adding another major version requires the same backend,
schema and transfer tests before extending the version gate. The optional
dependencies do not change existing SQLite installations or production locks.

## Connection contract

```python
from scrapper.db.postgresql import PostgreSQLSettings, PostgreSQLPool, connect

settings = PostgreSQLSettings.from_env()
with connect(settings, read_only=True) as conn:
    row = conn.execute("SELECT %s::text AS exact_value", ("0.00000000000000000001",)).fetchone()
    assert row["exact_value"] == "0.00000000000000000001"

with PostgreSQLPool(settings) as pool:
    with pool.connection(read_only=True) as conn:
        row = conn.execute("SELECT 1 AS value").fetchone()
```

`from_env()` requires `TRADING_AI_DB_BACKEND=postgresql` and a valid PostgreSQL
`DATABASE_URL` containing a database name. Explicit `PostgreSQLSettings(dsn=...)`
is available to controlled migration/test callers. There is no backend inference
or fallback. Importing the module neither imports optional dependencies nor
opens connections. Missing dependencies and invalid configuration fail clearly.
Configuration representations and connection-failure messages exclude the DSN;
callers must also avoid logging their environment, SQL parameters or server
errors that contain source records.

Native psycopg mapping rows use column-name access and `%s` parameters. Callers
use PostgreSQL-native SQL and `RETURNING` for generated IDs; these are not
SQLite-compatible connection objects. Direct connections and pool leases own
one transaction: success commits, exceptions roll back. Do not manually commit,
roll back, close a pooled connection, or change its autocommit mode inside a
lease. Read-only leases use a server-enforced read-only transaction. Connections
use UTC. Defaults are a 5-second connect timeout, 10-second pool acquisition,
30-second statement timeout, 5-second lock timeout and 60-second idle-transaction
timeout. Timeouts can be set explicitly through the settings object.

Pools are inert until opened, default to 0–4 sessions, cap configuration at 32,
and bound waiting callers by the configured maximum size. Closing the pool
closes idle sessions and retires outstanding sessions when returned. Each reuse
discards session settings, temporary objects and advisory locks. Prepared
statements are disabled because session reset invalidates them. Timezone and
transaction-local timeouts are restored on checkout; read-only state and failed
transactions do not leak to the next borrower.

This API makes no automatic transaction retries: callers must decide whether
an operation is idempotent before retrying deadlocks or serialization failures.
It does not run schema migrations, create application directories, change
runtime identities, or authorize production access.

## Verification and remaining gates

The offline contracts require only the Python standard library:

```sh
python -B -m unittest scrapper.tests.test_postgresql_backend -v
```

Five live tests are skipped unless `TRADING_AI_POSTGRESQL_TEST_DSN` explicitly
names an operator-provided disposable PostgreSQL 16 database. They create and
drop UUID-named test schemas; they never use application tables:

```sh
TRADING_AI_POSTGRESQL_TEST_DSN="$DATABASE_URL" python -B -m unittest scrapper.tests.test_postgresql_backend -v
```

Live coverage includes mapping rows/exact strings, commit/rollback and close,
read-only rejection, pool exhaustion, session reset, statement cancellation,
subsequent pool recovery and shutdown. It does not certify repository parity,
concurrent worker semantics, production backup/restore or cutover. Issues #54
and #55 own PostgreSQL schema lineage and verified data transfer; PostgreSQL
cannot become the primary production backend until these and the repository
ports are verified. Production cutover requires separate explicit approval.
