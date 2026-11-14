# SQLite source inventory and native runtime gate

Issue [#96](https://github.com/Mfadhi-lg/trading-ai/issues/96) is a source
classification and regression boundary, not a subsystem migration or runtime
activation. The deterministic [inventory](sqlite-usage.json) records each source
path, symbol, line, connection/SQL/dynamic-query evidence, source and scope hashes,
candidate callers, store, intent, backend guard, responsible module maintainers,
classification, disposition and follow-up. It contains source metadata only: no
database access, credentials, query results or source SQL is emitted.

The initial review identified 59 non-test Python files with direct connections,
plus extensionless `scripts/document-freshness` and embedded Python in the root
Dockerfile healthcheck. The broader inventory also includes dialect literals,
dynamic execution sites and configuration references. A site is a review candidate,
not necessarily an executed query or a native compatibility defect.

## Reproduce and maintain

```sh
python3 -B scripts/audit/sqlite_usage.py
python3 -B -m unittest scrapper.tests.test_sqlite_usage_inventory -v
python3 -B scripts/audit/sqlite_usage.py --candidate /tmp/sqlite-usage-candidate.json
```

The scanner uses `git ls-files` and reads tracked working-tree source. Stage new
source files before scanning. If unrelated local edits must be preserved, use
`--index` to scan staged bytes instead. CI checks the checked-out source normally;
`--index` is a local review option, not a CI exception. It never stages files.

Review the candidate diff before updating `sqlite-usage.json`. For each changed
site trace its callers, actual connection factory and SQL selection. Record the
store, read/write intent and explicit backend guard or retained debt. Verify native
alternatives with disposable smoke tests when the site can run on PostgreSQL.
Update `reviewed_source_sha256` to the reviewed `source_sha256` only after this
review. Candidate generation preserves old review acknowledgments; generating a
candidate alone cannot acknowledge changed source. New paths remain UNCLASSIFIED.
Remove stale entries only after tracing the replacement/removal.

The gate fails on unclassified paths, changed source/sites/caller references,
stale entries or missing review metadata/acknowledgments. An unguarded SQLite JSON
query added even to an already classified native module changes its evidence and
fails the gate. Backend-specific branches remain permitted when explicitly
reviewed and exercised. Importing `sqlite3.Row` or exception types is permitted.
This is a maintained review gate; maintainers can explicitly accept documented
debt. It is not a semantic SQL validator or an automatic proof of backend safety.

## Dispositions

- Live application: preserve `database.connect`, `postgresql_runtime`, campaign,
  quarter and diagnostic native dispatch and supported SQLite fallbacks.
  `repository_backend.statement` selects explicit dialect alternatives; many
  repository/command/view sites use it without a local conditional branch.
- Secondary operational store: `live.sqlite3` and historical candle
  `inventory.sqlite3` remain separate SQLite contracts. The application backend
  setting does not migrate these stores.
- Historical snapshot: explicit quiescent freshness/ledger readers, the research
  catalog and retained research sources/copies remain SQLite. Some research modules
  write isolated research copies and in-memory clones; this classification does
  not authorize executing them or rewriting retained evidence.
- Compatibility/test: disposable CI configuration and scanner/parser patterns are
  reviewed separately from live connections.
- Transfer/admin: immutable transfer sources, migration ledgers, queue-identity
  rehearsals and explicitly authorized sidecar/backup tools retain SQLite behavior.
- Ephemeral scratch: in-memory FTS5 and migration-baseline stores need no native port.

Legacy/manual operational readers and the root Dockerfile healthcheck have no
established native backend guard. Their baseline records that limitation, rather
than certifying compatibility or inferring inactivity from dated names. Port or
retained/manual-only disposition is tracked by
[#107](https://github.com/Mfadhi-lg/trading-ai/issues/107). Application dynamic SQL
without locally established routing also requires review there before native
expansion. Issues #63 and #64 concern engineering quality and domain contracts,
respectively; they are not subsystem port issues.

## Native execution evidence

The existing PostgreSQL workflow retains its disposable PostgreSQL 16 service and
job-specific storage roots. In addition to the application suite it runs:

```sh
python -B scripts/audit/native_runtime_smoke.py
```

This requires `TRADING_AI_ENV=test`, an explicitly disposable
`TRADING_AI_POSTGRESQL_TEST_DSN`, psycopg and PostgreSQL backup/restore clients.
It runs operational query/snapshot/catalogue/financial JSON regression tests and
the campaign/recovery reader adapters in `test_native_operational_readers.py`,
outside the existing `test_postgresql*.py` discovery pattern. New reader checks
forbid SQLite connection fallback. Native work uses UUID schemas from the existing
test fixture and mocked source transport; no external source requests are needed.
Missing prerequisites, errors, skipped tests or an empty suite fail this required
smoke command. An ordinary offline run may still skip native fixtures; those skips
are not passed native coverage. The diagnostic fixture explicitly closes its
captured connection during cleanup; this test does not certify adapter lifecycle
ownership beyond its read-only query behavior.

## Scope and limitations

The scan covers tracked `.py` and Python-shebang extensionless launchers under
`scrapper/`, `ops/`, `scripts/`, `experiments/`, root launchers/Dockerfiles and
shell/SQL/Compose/workflow configuration. Test directories/files, SQLite and
PostgreSQL migration directories and vendor directories are excluded; existing
migration inventories/tests remain authoritative for immutable migrations. Other
retained trees (`artifacts/`, `OLD/`, historical source checkouts) are outside this
runtime-source gate and must be reviewed before selecting code from them.

AST detection includes module/from-import aliases, dbapi2 and simple assignment
aliases. SQL patterns include SQLite catalogues, PRAGMA, JSON, date/time,
conflict and aggregate constructs; some patterns also match portable/native SQL
or non-SQL text. Dynamic execute/executescript/executemany arguments are conservative
candidates. Embedded/config scans are textual and do not parse shell heredocs or
prove their language. Caller references are lexical import/path candidates, not
an authoritative call graph or evidence of live use. Branch hints are hashed
syntax context, not validated guards. Function and whole-file hashes bind the
reviewed evidence; helper changes in other files still need impact review.

Reflection, arbitrary reassignment, wildcard imports, runtime imports, external
templates, generated SQL/code and installed retained images cannot be exhaustively
resolved statically. Symlinks are not followed. Native smoke covers selected
operational adapters, not all inventory sites. No production compatibility,
runtime health or migration readiness claim follows from this inventory.

## Verification record (14 November 2026)

All checks used tracked source or disposable test storage. The existing unrelated
candle-recovery working-tree edits were preserved; inventory generation used
staged bytes and scanner/isolation tests used an exported staged-source view.

- PASS: `python3 -B scripts/audit/sqlite_usage.py --index` — 172 files, 705 sites.
- PASS: `python3 -B -m unittest scrapper.tests.test_sqlite_usage_inventory scrapper.tests.test_ci_database_isolation -v`
  — 13 tests on Python 3.12 in the staged-source view. Python 3.12 and 3.14
  candidate source/site/caller hashes also compared equal.
- PASS: `sh /private/tmp/issue96-verify.sh scripts/audit/native_runtime_smoke.py`
  — seven tests, no skips, on a newly initialized temporary PostgreSQL 16.14 cluster.
- PASS: `sh /private/tmp/issue96-verify.sh -m unittest discover -s scrapper/tests -t . -p 'test_postgresql*.py' -v`
  — 180 tests, no skips, including backup/restore against the explicitly disposable
  `trading_ai_test` database on local port 55496.
- PASS: `sh /private/tmp/issue96-verify.sh -m unittest scrapper.tests.test_repository_backend scrapper.tests.test_screener_observation_repo scrapper.tests.test_exchange_coordinator scrapper.tests.test_periodic_coordinator scrapper.tests.test_claim_fencing scrapper.tests.test_document_processing_work scrapper.tests.test_ci_database_isolation -v`
  — 84 tests.
- PASS: `python3 -B -m unittest scrapper.tests.test_database_compatibility_inventory -v`
  — canonical migration source/hash preservation check.
- PASS: `git diff --cached --check`. Independent read-only review found no blocking
  findings; review did not claim to have executed native tests.

The temporary verification wrapper set `TRADING_AI_ENV=test`, every application
write root under `/private/tmp/issue96-test`, the explicitly disposable test DSN,
temporary dependency import paths and matching PostgreSQL 16 clients. Native
tests ran on local Python 3.14 with psycopg 3.2.13; CI uses its existing Python 3.12
and declared dependency versions. Initial local runs exposed missing test
dependencies and the backup drill's required database-name guard; those
prerequisites were corrected before the passing runs above. No safety guard was
weakened. No production database, persistent runtime, deployment or acquisition
was accessed or changed. No migration or rollback is required for this source-only
guardrail; reverting its added files/workflow commands removes it.
