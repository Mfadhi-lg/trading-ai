# Document acquisition integrity gate

Issue [#97](https://github.com/Mfadhi-lg/trading-ai/issues/97) ports the final
acquisition evaluator to native PostgreSQL while retaining SQLite support.

```sh
python3 -B -m scrapper.pipeline.evaluate_document_acquisition_gate \
  --scope-id document-acquisition-full-v1 --output-dir /authorized/report/directory
```

The command uses the configured backend through `scrapper.db.database.connect`
with `read_only=True`. It does not initialize a database, apply migrations, grant
privileges, repair data or change queues. Report output remains subject to the
configured writable-target guard. PostgreSQL startup still requires the existing
explicit schema identity, compatibility and restricted-login checks. This code
change does not authorize production execution or activation.

## Integrity evidence

`acquisition_foreign_keys` retains its meaning: the references in
`document_artifacts`, `artifact_sources`, `artifact_contents`,
`artifact_download_queue`, `artifact_recovery_targets` and
`document_acquisition_closure_states` must be intact. The SQLite branch retains
`pragma_foreign_key_check` filtered to those six child tables.

The PostgreSQL branch checks all 15 reviewed relationships from the native
baseline. Each must have one matching, validated, single-column MATCH SIMPLE FK
in the selected schema, with all four internal enforcement triggers enabled for
ordinary sessions. Missing, unvalidated, disabled or mismatched constraints fail
even when tables are empty. Relations using row-level security or unsupported
relation kinds cannot qualify through these checks. The gate also counts orphaned
non-null references independently of constraint metadata, including corruption
that predates enforcement. Nullable references do not count as orphans. Counts are
per failed reference, matching SQLite's per-constraint violation semantics.

All native reads run in one read-only repeatable-read transaction. The connection
must be idle on entry, and its current schema must match its selected application
schema. The gate leaves the connection idle after its snapshot completes. The
explicit relationship map is regression-checked against migrated SQLite FK
metadata; future schema changes must update and review that contract.

## Reports and handoff

`FINAL_ACQUISITION_GATE.json` remains authoritative. Existing checks, counts and
flattened report fields are retained. Added `database_source` and
`verified_at_utc` fields identify the backend, observed database/schema (or SQLite
path), and verification completion time. `gate_evidence.acquisition_foreign_keys`
records the validation method, constraint problems and orphan count.

Database/permission failures produce RED, `ready_for_research_intelligence=false`,
`database_queries_succeeded=false` and unknown (`null`) counts. Errors expose their
class only, not connection strings, SQL parameters or record contents. Failed
startup reports identify the selected backend with source status `unverified`.
The existing `production_db_path` field remains for compatibility: SQLite retains
its path check; PostgreSQL requires the selected schema's exact production
identity marker and target ID. A disposable test schema cannot become production
closure by supplying a SQLite path. Other mandatory acquisition evidence and
closure/reconciliation checks continue to govern GREEN.

The CLI returns 0 for GREEN and 2 for RED. It creates the RI handoff only for
GREEN. Existing historical handoff files are not erased; consumers must consult
the current authoritative JSON rather than infer readiness from file existence.

## Verification (14 November 2026)

Original `origin/main` code was executed on a synthetic corpus through the public
native read-only connection path and reproduced SQLSTATE `42P01` at the
SQLite-only pragma relation. No production connection was used.

The local wrapper `/private/tmp/issue97-verify.sh` sets `TRADING_AI_ENV=test`,
all application storage roots under `/private/tmp/issue97-test`, PostgreSQL 16
client tools, isolated dependency import paths and the explicitly disposable
`trading_ai_test` database on port 55497. The matching server was initialized
under `/private/tmp/issue97-pg-utf8`; fixtures create and drop only test databases,
UUID schemas and synthetic roles. CI retains its existing isolated service and
storage contract.

Regression coverage includes healthy nullable references, missing and unvalidated
FKs, disabled enforcement, actual orphan rows despite validated constraints,
missing tables/reports, native permission failures and public-factory startup
failure. The required `scripts/audit/native_runtime_smoke.py` includes this module
so missing native prerequisites/skipped coverage cannot silently pass its job.

Checks executed successfully on the final source:

- `sh /private/tmp/issue97-verify.sh -m unittest scrapper.tests.test_final_acquisition_gate -q`
  — 24 tests passed; the independent reviewer also ran all 24 successfully.
- `sh /private/tmp/issue97-verify.sh -m unittest discover -s scrapper/tests -t . -p 'test_postgresql*.py' -v`
  — 191 tests passed, no skips, including 11 new native gate tests.
- `sh /private/tmp/issue97-verify.sh scripts/audit/native_runtime_smoke.py`
  — 18 tests passed, no skips.
- `python3 -B scripts/audit/sqlite_usage.py --index`
  — 173 classified files and 707 source sites passed. Staged bytes preserve the
  unrelated candle-recovery working-tree edits outside this PR.
- `sh /private/tmp/issue97-verify.sh -m unittest scrapper.tests.test_sqlite_usage_inventory scrapper.tests.test_ci_database_isolation -v`
  — 13 tests passed from `/private/tmp/issue97-source`, an exported staged-source
  view with read-only Git metadata access; no uncommitted candle work was copied.
- `sh /private/tmp/issue97-verify.sh -m unittest scrapper.tests.test_database_compatibility_inventory -v`
  — migration source/hash preservation passed (one test).
- `git diff --cached --check` — passed. Final independent read-only review found
  no blocking issue.

Local tests used Python 3.14 and psycopg 3.2.13 with PostgreSQL 16.14; CI uses its
existing pinned Python/dependencies. The first local cluster used SQL_ASCII,
which failed fixture initialization; a fresh UTF-8 test cluster corrected the
prerequisite before the passing runs above. Early targeted testing also caught
and corrected transaction-isolation setup inside adapter savepoints. These
failed attempts are not counted as passed tests. Hosted CI is verified separately
on the PR revision.

No migration or data rollback is required. Reverting the source change restores
the prior behavior, including the PostgreSQL dialect defect; no persistent data
is modified by evaluation.
