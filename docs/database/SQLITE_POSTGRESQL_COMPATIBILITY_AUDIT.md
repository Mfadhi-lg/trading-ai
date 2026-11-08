# SQLite → PostgreSQL Compatibility Audit

**Status:** Audit baseline for issues #52–#57  
**Audited ref:** `main` at `d2502db890fa5f361c0e32d2c636e795f4496632`  
**Audit date:** 2026-11-08  
**Scope:** Static repository inspection only. No application database was opened, copied, migrated, modified, or benchmarked.

## Executive decision

PostgreSQL is a sensible target for the next growth stage because the application runs multiple ingestion, reconciliation, document, and scheduled-worker processes against one shared database. SQLite remains useful for lightweight local development and isolated unit tests. However, the current database implementation is not a drop-in portable SQL layer: repository functions are written directly against `sqlite3.Connection`, and the migration runner, locking, row behavior, SQL syntax, and transaction assumptions are SQLite-specific.

**Recommendation:** do not change the production database setting yet. Complete the CI isolation work (#5/#6), then introduce a tested database connection/repository contract and a separate PostgreSQL migration lineage. Keep the existing SQLite migrations immutable. Only after repository parity, data-copy verification, operational backup/restore, and workload benchmarks pass should PostgreSQL be documented as the authoritative production backend.

## 1. Repository inventory

Confirmed from the audited `main` tree:

| Area | Observed inventory |
|---|---:|
| Python modules under `scrapper/db/` | 70 |
| Canonical SQL migration files | 76 (001–076) |
| Central database connection implementation | `scrapper/db/database.py` |
| Central path/runtime configuration | `scrapper/config.py` |
| Main migration directory | `scrapper/db/migrations/` |
| Canonical schema pointer | `scrapper/db/schema.sql` (comment-only pointer; migrations are canonical) |
| Database-writing production Compose services | Multiple services share `/data/scraper.db` through the `scraper-data` volume |
| Production PostgreSQL driver/service | Not present in the inspected `scrapper/requirements.txt` or `compose.yml` |

The 70 modules are not 70 independent databases. They are repository/service modules operating on the same SQLite connection contract. Major functional areas include:

- Core company/security identity, source observations, financial observations and validation.
- Screener collection and result queues.
- NSE/BSE exchange snapshots, securities, filings, identity reconciliation and schedules.
- Mutual-fund workbook ingestion, normalized holdings, month changes and feature lineage.
- Document artifacts, downloads, extraction, OCR evidence, recovery, credit ratings, transcripts and recordings.
- Worker coordination, leases, fencing, periodic tasks and host permits.

The dated `docs/architecture/2026-10-01/migration-inventory.json` is a historical inventory covering migrations 001–067. A current static declaration inventory for all 76 migrations is now included at `docs/database/migration-inventory-2026-11-08.json`. It records 380 declaration entries: 168 CREATE TABLE, 30 CREATE VIEW, 98 CREATE INDEX (including UNIQUE indexes), 22 CREATE TRIGGER, 46 ALTER TABLE and 16 DROP statements. These are declaration occurrences, not counts of unique live schema objects: migrations intentionally rebuild/rename objects and may re-declare indexes/views. The inventory includes source file SHA-256 values and declaration line numbers, checked against canonical source bytes. Regenerate it with `python -B scripts/audit/generate_database_migration_inventory.py`; `scrapper/tests/test_database_compatibility_inventory.py` checks coverage, hashes and declarations. This line-based scanner inventories the current declaration forms, not general SQL semantics.

## 2. Current connection and runtime contract

### Confirmed implementation

In `scrapper/db/database.py`:

- The public connection function returns `sqlite3.Connection`.
- Read-only mode uses a SQLite URI with `mode=ro` and `PRAGMA query_only=ON`.
- Writable mode calls `assert_configured_writable_targets`, ensures directories, opens `sqlite3.connect(db_path)`, and enables `PRAGMA journal_mode=WAL`.
- Every connection enables `PRAGMA foreign_keys=ON` and `PRAGMA busy_timeout=5000`.
- Rows are exposed as `sqlite3.Row`.
- Schema compatibility queries `sqlite_master` and the `schema_migrations` table.
- Migration application uses a filesystem `.migrate.lock` plus `fcntl.flock`, an in-process lock, and `conn.executescript()`.
- Migration execution is explicit; ordinary initialization checks compatibility and does not silently migrate.
- The historical migration identity `065_exchange_source_access.sql` is retained as an explicit full-filename identity, distinct from the current canonical `065_operator_browser_rating_recovery.sql`.

In `scrapper/config.py`:

- `SCRAPER_DB_PATH` selects the SQLite file; default is `scrapper/store/scraper.db`.
- `SCRAPER_STORE_PATH` and related environment variables define application-owned file roots.
- `MIGRATIONS_PATH` points to the single SQLite SQL migration directory.
- `ensure_dirs()` and `assert_configured_writable_targets()` enforce runtime write-target identity before writes.

In `compose.yml`:

- Multiple production services set `SCRAPER_DB_PATH=/data/scraper.db`.
- Those services share the named `scraper-data` volume.
- The application therefore has multiple processes using one SQLite file, not one isolated SQLite process per worker.

In the repository layer:

- Modules import `sqlite3`, annotate functions with `sqlite3.Connection`, and use SQLite row access such as `row["column"]`.
- SQL uses positional `?` parameters.
- Observed statements include SQLite `INSERT OR IGNORE` and `INSERT OR REPLACE` patterns, plus `ON CONFLICT ... DO UPDATE` upserts.
- Worker processing uses explicit transaction boundaries and `BEGIN IMMEDIATE` in at least the document processing/reconciliation path.
- Repository functions depend on `rowcount`, `lastrowid`/integer identity behavior and connection context management in multiple workflows.

## 3. SQLite-specific compatibility gaps

| Gap | Evidence / affected contract | PostgreSQL migration requirement | Severity |
|---|---|---|---|
| Driver and connection API | `sqlite3.Connection`, `sqlite3.Row`, stdlib SQLite in central DB and repositories | Introduce an explicit connection/repository protocol and PostgreSQL adapter; do not simply replace the driver | Critical |
| Parameter binding | SQL statements use `?` placeholders | Convert SQL execution to a safe dialect-aware binding strategy; do not globally regex-replace placeholders inside SQL strings | High |
| Row/result behavior | Code reads rows by string key and uses SQLite cursor semantics | Standardize mapping rows, `rowcount`, generated IDs and fetch behavior; test every repository contract | High |
| Conflict/upsert behavior | `INSERT OR IGNORE`, `INSERT OR REPLACE`, and `ON CONFLICT` are used | Translate each operation intentionally. SQLite REPLACE may delete/reinsert and is not equivalent to PostgreSQL `ON CONFLICT DO UPDATE` | Critical |
| Transaction/locking | `BEGIN IMMEDIATE`, connection context managers, file lock and process lock | Replace SQLite writer reservation and file locks with PostgreSQL transactions, row/advisory locks and atomic lease claims; preserve claim fencing | Critical |
| Migration execution | `sqlite_master`, `PRAGMA`, `executescript`, filename ledger and `fcntl.flock` | Add PostgreSQL-native introspection and migration runner/lineage; keep SQLite migration checksums untouched | Critical |
| DDL rebuilds | Migration 003 rebuilds tables with create-copy-drop-rename inside `BEGIN IMMEDIATE` | Rewrite as reviewed PostgreSQL `ALTER TABLE`/copy operations with equivalent constraints and rollback behavior | High |
| SQLite-only DDL | `WITHOUT ROWID`, SQLite partial-index and view syntax appear in migration history | Review each DDL statement and recreate supported semantics in PostgreSQL | High |
| Runtime pragmas | `foreign_keys`, `journal_mode=WAL`, `busy_timeout`, `query_only`, `database_list` | Move equivalent behavior to server configuration, role grants, transaction/read-only settings and connection-pool configuration | High |
| ID generation | Many tables use `INTEGER PRIMARY KEY` | Map IDs to `BIGINT GENERATED BY DEFAULT AS IDENTITY` or equivalent while preserving explicit source IDs during copy | High |
| Data typing | Financial decimals are stored in text columns such as `value_decimal`, `price_decimal`, `amount_decimal`; timestamps are stored as text; JSON is stored in text columns | Preserve exact decimal semantics and raw source values; define explicit precision/scale, timezone and JSON validation policies before any type conversion | Critical |
| Boolean/enums | Integer 0/1 flags and `CHECK`-constrained text states are used | Preserve constraints and valid historical values; choose boolean/enum/text deliberately | Medium |
| Foreign keys | Connection enables FK checks per connection; migrations define FK and cascade behavior | Ensure constraints are enabled and validated in PostgreSQL; audit historical orphans before enforcing | High |
| Concurrency | Several worker/queue domains share one SQLite DB and use leases, claims and fencing | Verify atomic claim/lease transitions under concurrent PostgreSQL sessions; test stale-worker fencing and deadlock/retry behavior | Critical |
| Platform locking | `fcntl.flock` is Unix-specific and protects SQLite migration files | Remove filesystem lock dependency from database migration coordination; use PostgreSQL advisory locks or a migration singleton | Medium |

### Financial data integrity rule

The current schema deliberately retains source/display strings alongside normalized decimal strings. For example, `financial_observations` includes `display_value`, `value_decimal`, `unit_code`, `scale_multiplier` and `missing_kind`. The migration must preserve distinctions among:

- raw display text and normalized exact numeric value;
- currency, unit and scale multiplier;
- missing, blank, dash, zero and null;
- source period, reporting scope and availability timestamp;
- first-observed time versus source-published time;
- source identifiers and provenance.

Do not cast these fields through Python `float`. Any conversion to PostgreSQL `NUMERIC(p,s)` must be justified by observed precision/scale bounds and round-trip tests. Keep raw values for audit and reproducibility.

## 4. Migration lineage and schema risks

1. The current canonical SQLite migration set is 001–076. Do not rewrite released SQL files or renumber them.
2. The existing filename ledger is a SQLite contract. Its special historical identity for `065_exchange_source_access.sql` demonstrates why filename-only migration assumptions must be preserved and tested.
3. Migration 003 includes `BEGIN IMMEDIATE`, table rebuilds, data copy, drop/rename and view recreation. It is a high-risk compatibility case.
4. Later migrations introduce extensive document acquisition and worker coordination tables. The PostgreSQL schema must preserve unique constraints and indexes used for leases, idempotency, queue ordering and fencing.
5. The 2026-10-01 migration inventory is stale after migration 067; migrations 068–076 must be included in the new full schema extraction.
6. `schema.sql` is not the authoritative full schema. The migration files are the source of truth.

## 5. Recommended PostgreSQL architecture

### Keep responsibilities explicit

- **Configuration:** select backend explicitly with a documented setting such as `TRADING_AI_DB_BACKEND=sqlite|postgresql`; do not infer backend from a path or silently fall back.
- **Connection lifecycle:** centralize connect, close, transaction, read-only mode, timeouts and pool ownership.
- **Repository contract:** repositories depend on a small typed connection/transaction protocol rather than importing `sqlite3.Connection` throughout the application.
- **Dialect-specific SQL:** keep backend-specific SQL explicit where semantics differ (upsert, queue claims, JSON, date operations, returning IDs).
- **Migrations:** maintain separate immutable SQLite and PostgreSQL migration lineages with checksums and explicit backend selection.
- **Local development:** keep SQLite supported while it remains useful; provide PostgreSQL Compose profile/service for parity and integration tests.
- **Production:** do not switch the production Compose environment until data migration and operational acceptance gates are met.

Avoid introducing a full ORM as a shortcut before measuring the scope. The current repository layer contains substantial SQL and queue semantics; a blanket ORM conversion would be a large, unrelated rewrite. First establish the smallest safe database protocol and prove it with representative repositories.

## 6. Phased implementation plan for issues #52–#57

| Phase | Issue | Deliverable | Exit gate |
|---|---|---|---|
| 0 | #5, #6, #3, #4 | Explicit test runtime identity, isolated DB/storage roots, migration and ML readiness CI | Clean CI database init and isolated test paths; infra setup failures separated from code failures |
| 1 | #52 | This compatibility audit plus complete current 001–076 schema/SQL inventory | Reviewed inventory and severity-ranked compatibility plan |
| 2 | #53 | Backend configuration and connection/repository contract; PostgreSQL local service and driver; SQLite remains supported | Representative repositories pass against both backends; no silent fallback |
| 3 | #54 | PostgreSQL-native schema migration lineage | Fresh PostgreSQL creation and sequential upgrades pass; exact decimal and constraint checks |
| 4 | #55 | Offline/restartable SQLite-to-PostgreSQL migration utility | Disposable-copy row counts, PK/FK, exact financial values, idempotency and rollback all verified |
| 5 | #56 | Representative historical market-data and financial query benchmarks | Baseline and post-index plans/timings; indexes justified by measured workloads |
| 6 | #57 | PostgreSQL CI service plus backup/restore verification and operational runbook | CI migration/integration pass; disposable restore verified; production credentials excluded |

## 7. Verification plan required before PostgreSQL becomes primary

- Run schema creation and all migrations from a clean PostgreSQL instance.
- Test every repository family, not only a connection smoke test.
- Run two or more concurrent workers against the same queue and prove no duplicate claims, lost leases or stale-worker writes.
- Test transaction rollback, serialization/deadlock retry policy, connection-pool exhaustion and shutdown cleanup.
- Compare source/destination row counts for every table; verify PK uniqueness and FK integrity.
- Compare critical financial values using exact decimal-safe comparison and preserve raw source strings.
- Validate time-zone and timestamp semantics, JSON payload round-trips and check constraints.
- Benchmark representative ingestion, company/period lookups, historical candle reads and financial statement retrieval.
- Create a PostgreSQL backup and restore it into a clean disposable instance; validate schema, counts and critical records.
- Keep a documented cutover/rollback runbook. Production cutover requires explicit operator approval.

## 8. Known evidence gaps (not claimed as verified)

- No production or developer SQLite database was opened; live row counts, table sizes, integrity checks and actual data quality are unknown.
- No query-plan or throughput benchmark was run; no index or partitioning recommendation is claimed as measured.
- The current 76-file migration declaration inventory is static and manifest-backed; it is not a live schema dump from a database. A full SQL-operation scan across all 70 DB modules remains a follow-up deliverable, so the compatibility list is evidence-backed but not a claim that every query has been exhaustively classified.
- This audit does not yet prove every SQL statement across all repository modules has been catalogued. The next implementation step should add a deterministic scanner and current inventory, then validate its findings with tests.
- No PostgreSQL server, driver, data migration, backup or restore was run in this audit.
- CI status is separate from this static audit. The earlier Python download failures were superseded by the PR #68 run on Python 3.12.16: database initialization passed, and the full suite exposed fixture, dependency and stale-contract failures. The integration resolution records current executable verification; this audit does not certify runtime or database health.

## 9. Files reviewed

- `scrapper/db/database.py`
- `scrapper/db/schema.sql`
- `scrapper/config.py`
- `scrapper/runtime_identity.py`
- `scrapper/db/repo.py`
- `scrapper/db/exchange/filings.py`
- `scrapper/db/documents/processing.py`
- `scrapper/db/migrations/001_initial.sql`
- `scrapper/db/migrations/003_availability_acceptance_periods.sql`
- `scrapper/db/migrations/026_credit_rating_normalization.sql`
- `scrapper/db/migrations/069_worker_claim_fencing.sql`
- `scrapper/db/migrations/075_periodic_dispatch.sql`
- `scrapper/db/migrations/076_retire_crisil_icra_terms_holds.sql`
- `scrapper/tests/test_database_migration_controls.py`
- `scrapper/tests/test_migration_manifest.py`
- `scripts/audit/check_migration_manifest.py`
- `compose.yml`
- `compose.development.yml`
- `scrapper/requirements.txt`
- `scrapper/requirements.lock`
- `scrapper/requirements.test.txt`
- `docs/architecture/2026-10-01/migration-inventory.json`
- `docs/database/migration-inventory-2026-11-08.json`
- `docs/cleanup/baselines/C22.3/migration-manifest.json`

---

**Audit conclusion:** PostgreSQL is the recommended long-term production database, but not a safe immediate replacement. The migration must be a controlled compatibility project across 70 DB modules, 76 SQLite migrations, concurrent worker behavior, and exact financial-data semantics.