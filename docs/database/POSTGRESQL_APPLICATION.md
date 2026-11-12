# Configured PostgreSQL application backend

Issue #53's application path selects PostgreSQL explicitly through the existing
database factory. SQLite remains the default for existing installations and
offline fixtures. Financial values and original source bytes retain their
existing contracts; the frozen SQLite and PostgreSQL baseline migrations are
unchanged by these application ports.

## Isolated development setup

Start the standalone PostgreSQL 16 service using
[POSTGRESQL_BACKEND.md](POSTGRESQL_BACKEND.md). It is separate from the production
Compose application. Install both the locked application dependencies and the
optional driver in a development virtual environment. Set passwords using a
private libpq password file or the interactive `psql` password command; do not
commit credentials. A safe [environment example](postgresql.example.env) is
provided.

For a newly created development database, use an administrator connection only
for the following explicit setup. Review an existing database's grants before
changing them. Never run these commands against production as development setup.

```sh
export TRADING_AI_ENV=development
export TRADING_AI_DB_BACKEND=postgresql
export TRADING_AI_POSTGRESQL_SCHEMA=trading_ai
export TRADING_AI_POSTGRESQL_ADMIN_URL='postgresql://trading_ai_dev@127.0.0.1:55432/trading_ai_dev'
task_root=$(mktemp -d /tmp/trading-ai-postgresql-development.XXXXXX)
export SCRAPER_STORE_PATH="$task_root/store"
export SCRAPER_DB_PATH="$task_root/state/legacy-path-placeholder.db"
DATABASE_URL="$TRADING_AI_POSTGRESQL_ADMIN_URL" python -B -m scrapper.db.postgresql_migration schema \
  --schema trading_ai --allow-destination-write --report "$task_root/schema.json"
psql "$TRADING_AI_POSTGRESQL_ADMIN_URL" -v ON_ERROR_STOP=1 \
  -c 'REVOKE CREATE,TEMPORARY ON DATABASE trading_ai_dev FROM PUBLIC' \
  -c 'REVOKE CREATE ON SCHEMA public FROM PUBLIC'
python -B -m scrapper.db.postgresql_setup --schema trading_ai \
  --environment development --role trading_ai_app --policy application \
  --allow-destination-write
```

Setup creates a **new** passwordless `LOGIN NOINHERIT`, applies reviewed DML
grants and returns a `target_id` UUID. It does not modify an existing login,
assign authentication, revoke database-wide PUBLIC rights, initialize tables or
start workers. Configure the login's authentication separately, for example
with interactive `psql` `\password trading_ai_app`. Copy the returned UUID into
`TRADING_AI_POSTGRESQL_TARGET_ID`; set `DATABASE_URL` to the runtime login, not
the administrator. A second setup for a reader login uses `--policy reader` and
the same explicit `--target-id`. An existing unrecognized or mismatched schema
identity is never overwritten.

The SQLite path setting remains a filesystem-identity input for application
storage validation. PostgreSQL does not create or open that SQLite database.
Normal startup checks the target UUID/environment, native schema lineage and
effective login privileges. It never repairs grants or applies migrations.
Production startup additionally requires an authorized runtime-identity file
whose `postgresql` object exactly names `database`, `schema`, `role` and
`target_id`, alongside the existing filesystem/release identity requirements.

```python
from contextlib import closing
from scrapper.db.database import connect, init_db

with closing(connect(read_only=True)) as conn:
    init_db(conn)  # Check only; no migration or privilege change.
    print(conn.execute("SELECT count(*) AS companies FROM companies").fetchone()["companies"])
```

## Application transaction and worker behavior

Application connections remain open across repository calls. Pure reads run
without an open transaction, and writes use short units committed through the
existing repository boundaries or `with conn:`. Exiting that context commits
or rolls back without closing the connection. Nested units use savepoints.
An explicit commit inside a managed unit cannot split that unit. Deferred
constraint/commit errors are sanitized, rolled back and exposed through the
existing SQLite-compatible exception families.

SQL dialect choices remain explicit in source. Binding lexes positional qmarks
without replacing quoted source values. Native casts, JSON scalar types,
grouping, generated IDs and conflict behavior have dedicated ports. Normal
application cursors are deliberately unsupported; repositories use `execute`
and `executemany`. Native migration/infrastructure APIs remain separate.

The first native implementation conservatively serializes short multi-step
application write decisions with one schema-scoped transaction advisory lock.
This preserves SQLite's writer-reservation guarantees across queue eligibility,
publisher capacity, accepted-version pointers, intake budgets and cache races.
It does not hold that lock during HTTP, browser, parser or OCR work. Reads remain
concurrent. Queue claims retain token/generation fencing; native lease comparisons
use timestamp instants while preserving source spelling where a deadline is
retained. Separate thread-owned renewal connections commit each renewal.
Coordinator ownership uses a session advisory lock, rather than a shared local
SQLite pathname, and survives transaction commits during external work.

This serialization is a correctness choice, not a throughput improvement claim.
More granular concurrency and workload indexes require measured benchmarking
under #56. No automatic retry of complete transactions is introduced.

## Operational policies and boundaries

Use a dedicated native login for each bounded runtime policy. The
[permission compiler and preflight](POSTGRESQL_PERMISSIONS.md) preserve the
existing table/column/append-only rules using server privileges. Installing an
authorizer validates those privileges; it never makes the callback a no-op or
uses administrator credentials with `SET ROLE`. Both migration ledgers, views,
DDL, TRUNCATE, role escalation and trigger disabling remain protected.

Canonical source workflows, repositories, validation/export consumers and worker
handoffs support the configured application connection. The retained activation
manifests, mounted SQLite-volume ownership checks and pinned production images
remain evidence of the existing deployment. They are not relabeled as PostgreSQL
release proof or automatically restarted. A native production release must use
the reviewed native login/target identity and complete the existing authorized
cutover process. Explicit frozen research SQLite stores and the SQLite transfer
source remain SQLite.

## Verification and release gates

The dedicated PostgreSQL workflow runs every `test_postgresql*.py` suite on a
disposable PostgreSQL 16 service, plus relevant SQLite regression tests. Tests
create/drop only UUID schemas and dedicated UUID test databases/roles. They do
not use retained application data or live providers. Public workflow tests mock
source transport and check durable claims and idle connections at external work.

```sh
python -B -m unittest discover -s scrapper/tests -t . -p 'test_postgresql*.py' -v
python -B -m unittest scrapper.tests.test_repository_backend scrapper.tests.test_ci_database_isolation -v
```

Set `TRADING_AI_POSTGRESQL_TEST_DSN` only to an explicitly disposable test cluster,
`TRADING_AI_ENV=test` and isolated application storage roots. Without that DSN,
live tests are skipped, not certified. The regular offline suite continues to
exercise SQLite. Linux shared-transport tests require their existing parent-death
fencing support; macOS cannot certify those tests.

Production dataset compatibility, workload benchmarking (#56), PostgreSQL
operational backup/restore (#57), deployment and cutover remain separate operator
gates. This application change does not activate acquisition, access production
data, change production credentials or certify production runtime health.

The complete native command above passed 136 tests with zero skips on the
disposable PostgreSQL 16.14 cluster (Python 3.12.13, psycopg 3.3.8). The migration
manifest and changed Python syntax/diff checks also passed. Independent reviews
covered application identity/permissions/commit handling and repository/workflow
semantics; identified failures received executable regressions. The local full
offline run on macOS encountered Linux parent-death/network-test prerequisites
and path-normalization expectations; the Linux CI result is the release gate
for that suite, not the macOS run.
