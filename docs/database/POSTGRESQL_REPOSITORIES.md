# PostgreSQL repository and worker transition

This change ports existing repository functions, rather than redirecting all
application connections. PostgreSQL 16 is supported for the explicit paths below;
SQLite remains the default application backend. Issue #53 is not fully complete:
the supported paths do not yet cover every production repository or worker.

## Supported paths

| Existing API | PostgreSQL behavior |
|---|---|
| `db.repo` company identity, scrape runs and all its observation facade methods | Explicit generated IDs, source identity locking, immutable blob bytes, fetch/parse identity, raw cells, financial/quote/growth/shareholding/peer observations and source provenance |
| `db.exchange.tasks` enqueue, claim, finish and count | Idempotent admissions, priority ordering, locked-row skipping, expired lease replacement, generation fencing and rollback |
| `db.document_work_claims` extraction claim/assert/renew/release | Atomic same-resource ownership, timezone-aware expiry, nonshortening renewal and stale-token rejection |
| `db.document_extraction_repo` persistence and payload | Exact parser/OCR/options cache, pages/elements/validation evidence, atomic savepoint rollback, preferred content coordination and fenced release |
| `db.documents.processing` bounded reconciliation and explicit work claim | Same media gates, exact version identity and shared extraction fence; failed caches remain failed |

`db.postgresql_workers` supplies the native document ownership and bounded handoff
operations. It does not admit downloads. Unsupported media still enter the
processing inbox as evidence, and do not become extraction jobs.

The binding helper in `repository_backend.py` handles positional parameters in
reviewed portable statements. It lexes quotes/comments, preserves literal
question marks and percent signs, and rejects unsupported quoting/bind forms.
Dialect differences remain explicit SQL branches. It does not emulate SQLite's
rows, pragmas, authorizer, `REPLACE`, or transaction model.

## Connection and transaction contract

Install the optional dependencies and explicitly migrate an isolated development
schema using [the existing procedures](POSTGRESQL_SCHEMA.md). Configure
`TRADING_AI_DB_BACKEND=postgresql`, `DATABASE_URL`, and a dedicated
`TRADING_AI_POSTGRESQL_SCHEMA`. Then call only supported APIs:

```python
from scrapper.db.postgresql import repository_session
from scrapper.db.exchange import tasks

with repository_session() as conn:
    task = tasks.claim(conn, owner="development-worker", now=utc_now)
# Commit the claim before external work; no lease/session spans HTTP or OCR.
if task is not None:
    result = perform_bounded_work(task)
    with repository_session() as conn:
        tasks.finish(conn, task, now=completed_at, result=result)
```

The example's clock/result/work values are supplied by the caller. The session
checks migration checksums and schema structure without applying migrations,
sets the dedicated search path and closes on every exit. `read_only=True`
enforces server-side read-only access. Invalid/shared schemas and structural
drift fail before yielding a repository connection.

Native mutating operations require a managed transaction. They never commit or
close that connection; successful session exit commits and an exception rolls
back. Nested persistence uses savepoints so a failed parse/extraction can be
handled without aborting unrelated work. SQLite retains its existing internal
commit behavior. Do not hold native sessions during external work.

For extraction, claim in one short session, perform extraction outside it, then
call `persist_extraction(..., claim=claim)` in another session. Ownership assertion
locks the resource until persistence and release commit. Reclaimers must wait;
an expired/replaced token cannot persist. Cache identity locks prevent duplicate
unclaimed persistence. No automatic deadlock/serialization retry is introduced.
The caller must decide whether retrying a complete transaction is safe.

Company identity decisions serialize conservatively within each database.
Observation persistence locks the pending parse row, so concurrent writers
cannot duplicate its sections. Concurrent blob writers use independent temporary
files; a database rollback retains immutable source bytes, as on SQLite.

## Remaining gates before primary application use

- Port and verify full download eligibility/admission, publisher/host policies,
  queue renewal/completion and recovery operations. A low-level claim primitive
  must not bypass those policies.
- Port shared host permits, collection/screener/periodic queues, mutual-fund and
  exchange ingestion repositories beyond coordinator tasks, and remaining
  document repositories.
- Port automatic processing selection and actual worker connection lifecycles.
  Existing workers still open SQLite connections; changing an environment flag
  does not activate these native repository paths.
- Replace SQLite-only operational SQL authorizers with reviewed native
  enforcement. `set_authorizer()` must never become a no-op. Direct SQLite
  connections in operational commands also need explicit ports.
- Complete measured workload benchmarks (#56), PostgreSQL backup/restore and
  operational readiness (#57), then rehearse the documented transfer/cutover and
  rollback procedures. Synthetic parity does not certify retained production
  data or runtime health.

No production configuration, database, migrations, acquisition state or running
worker was changed. Rollback of this code restores the preceding source revision;
no additional PostgreSQL schema migration or data rewrite is required.

## Verification

The PostgreSQL workflow installs the locked application/test dependencies plus
the pinned optional driver. It runs the new native contracts and relevant SQLite
regressions under the existing isolated CI storage contract.

For local execution, use a disposable PostgreSQL 16 DSN in
`TRADING_AI_POSTGRESQL_TEST_DSN`, set `TRADING_AI_ENV=test`, and direct
`SCRAPER_STORE_PATH` and `SCRAPER_DB_PATH` to a fresh temporary root before running
the exact unittest command from `.github/workflows/postgresql.yml`. Each live
test creates/drops its own UUID schema. Unset DSNs produce explicit skips, not
passing live coverage.

Tests compare the existing SQLite and native Screener repository output across
20 tables, including blank/dash/zero/exact long decimal strings and original
blob bytes. Concurrent-session checks cover identity/cache races, duplicate
claims, expired generation/token fencing, claim rollback and persistence locks.
Document tests use synthetic extraction data; they do not certify OCR accuracy
or invoke an external provider.

On 9 November 2026, the following command passed **148 tests with zero skips**
on a new disposable PostgreSQL 16.14 cluster, Python 3.12.13, psycopg 3.3.8 and
isolated temporary application storage (the DSN was supplied through the test
environment):

```sh
python -B -m unittest \
  scrapper.tests.test_repository_backend \
  scrapper.tests.test_postgresql_backend \
  scrapper.tests.test_postgresql_schema \
  scrapper.tests.test_postgresql_transfer \
  scrapper.tests.test_postgresql_observations \
  scrapper.tests.test_postgresql_exchange_tasks \
  scrapper.tests.test_postgresql_workers \
  scrapper.tests.test_postgresql_extraction \
  scrapper.tests.test_screener_observation_repo \
  scrapper.tests.test_exchange_coordinator \
  scrapper.tests.test_periodic_coordinator \
  scrapper.tests.test_claim_fencing \
  scrapper.tests.test_document_processing_work \
  scrapper.tests.test_ci_database_isolation -v
```

`python3 -B scripts/audit/check_migration_manifest.py` and `git diff --check`
also passed; SQLite migration bytes and the PostgreSQL baseline are unchanged.
Independent review found and corrected missing native transaction preconditions
and the shared blob temporary-file race. A separate reviewer subsequently
verified the extraction locking/savepoint paths and independently ran its six
live integration tests. Workload benchmarking, PostgreSQL operational restore
and production end-to-end worker execution were **not run** in this change.
