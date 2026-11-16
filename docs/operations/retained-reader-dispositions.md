# Operational reader dispositions (#107)

Source/caller review on 16 November 2026 completes the entry-point selection in
[#107](https://github.com/Mfadhi-lg/trading-ai/issues/107). This is source evidence,
not an observation of installed runtime health or authorization to activate code.
No additional native runtime path is selected here. SQLite snapshot/recovery
interfaces, secondary live/candle stores and offline research copies retain their
existing contracts.

## Native readers already selected

- Root `Dockerfile`: its health command calls `database.connect(read_only=True)`
  and `init_db`'s read-only schema validation, then closes the connection. Native
  configuration/connection/schema failures cannot fall back to SQLite. Both root
  and native-image commands are exercised by `test_container_healthcheck`, whose
  native class is required by `scripts/audit/native_runtime_smoke.py`.
- `ops/host/financial_queue_eligibility.py` and `diagnose_financial_pending.py`:
  public `main` functions use `financial_diagnostic_reader.observation`. Native
  reads use the verified restricted reader; explicit `--sqlite-snapshot` retains
  frozen-input semantics. Installed invocation uses `acquisition_lifecycle.database_status(script=...)`,
  governed by the observer allowlist in `postgresql_release.py`; contracts,
  commands and tests are documented in
  [runtime verification](runtime-verification.md).
- `ops/host/actual_download_audit.py`: `run_actual_download_audit.py` selects the
  sealed reader transport. `main` obtains `document_audit_reader.observation`;
  `audit` replaces its SQLite schema query with `information_schema` on native
  connections. Its dictionary-generated SELECTs and bound parameters use that
  same connection; the SQLite catalogue literal is not executed natively.
- `ops/host/download_batch_evidence.py` and `download_failure_evidence.py`:
  these are direct/manual public scripts; no current in-repository host launcher
  was found. Native tests import both scripts directly. Their public `main` functions use the same observation boundary.
  Exact-ID queries construct only bounded placeholder lists. The failure tool's
  existing dictionary query interface wraps caller-supplied diagnostic SELECTs;
  it runs under restricted, read-only credentials, not a general SQL translator.
  New caller SQL must be reviewed for its selected dialect before use.
- `ops/host/document_backlog_audit.py`: `document_backlog_recovery.py` selects its script;
  `document_backlog_report.py` and the actual-download/evidence tools reference
  its reporting/verification helpers. Public `main`/`census` use the same observation
  boundary. Dynamic filter/placeholder queries keep parameters bound; native
  code uses catalogue alternatives, and the automatic retained SQLite comparison
  is skipped on native connections. Physical source-byte verification remains.

`test_postgresql_financial_diagnostics` and
`test_postgresql_document_audits.NativeDocumentAuditTests` are required smoke
coverage. They exercise public commands, restricted native readers, failure
without SQLite fallback, synthetic parity and catalogue branches. See
[document reader contracts](document-audit-readers.md). These tests do not certify
arbitrary future caller-supplied SQL or retained production records.

## Retained operational readers

[The legacy launcher inventory](legacy-sqlite-launchers.md) names the exact
Step 3, Phase 3, Phase 4, recovery, benchmark and canary wrappers and direct
runtimes, including transitive/generated-source callers. Their dispositions are:

- `phase3_inspect_runtime`, `phase4_inspect_runtime`,
  `phase4_attempt02_verify_runtime`, `phase4_attempt03_verify_runtime`,
  `phase4_canary_verify_runtime`, `phase4_fingerprint_preflight_runtime`,
  `phase4_retry_preflight_runtime`, `phase4_routine_preflight_runtime`,
  `phase4_trial_snapshot_runtime`, `phase4_gap_inspect_runtime` and
  `phase4_followup_snapshot_runtime`: historical snapshot only.
- `step3_inspect_runtime`, `step3_detail_runtime`,
  `step3_accepted_details_runtime` and `step3_canary_state_runtime`: historical
  snapshot only. The separate source-response parser has no application DB.
- `acquisition_recovery_backup_runtime`, `acquisition_recovery_inspect_runtime`,
  `acquisition_recovery_integrity_benchmark` and
  `acquisition_recovery_processing_state`: historical snapshot only; old SQLite
  backup receipts never certify native recovery. Recovery dispatch rejects native
  execution before reading those receipts.
- `ops/benchmarks/phase4_throughput.py` and
  `ops/docker/canary/ar013_readonly_db.py`: callable `main` requires the same
  explicit historical snapshot. Their host wrappers are retired. The independent
  benchmark arithmetic helper retains its existing interface.

`legacy_sqlite_guard.require_snapshot` rejects native configuration/selected
release before legacy reads, then requires an explicit test path, SHA256 and
an effective read-only Linux input mount. Original SQL, sidecar, ledger and inode
checks remain. Dynamic table/column identifiers, PRAGMA, SQLite catalogue queries,
FTS/authorizer behavior and generated inspection source within these readers are
retained SQLite contracts, not SQL approved for native execution.

The maintained `phase4_documents_runtime` and `phase4_routine_runtime` helpers
are distinct from those inspection scripts. Existing backend routing remains:
the guarded writers use `scrapper.db.database.connect` and retain authorizer
controls. `phase4_documents_runtime.fingerprint` dispatches to
`postgresql_runtime.fingerprint` before its SQLite PRAGMA/JSON/rowid path.
Fingerprint identifiers are quoted from catalogue metadata; routine count tables
come from fixed code lists. This
review does not expand their execution scope or certify other dynamic queries in
the application inventory. Future native selections still require caller/SQL
review and disposable smoke coverage.

## Retained BSE ingestion

`ops/activation/bse_retained.py` is the in-repository launcher for
`bse_retained_runtime.py`; its `main` assembles the fixed eight-filing October 9
operation, mounts the production SQLite volume writable and creates production
identity/permission receipts. Runtime `run` calls `_run_owned`, which opens
SQLite and injects a protected canonical writer. This is a historical write
operation, not a read-only snapshot tool. Its recorded permission is dated
provenance, not fresh execution authorization.

Both direct scripts and callable `main`, `run` and `_run_owned` now refuse with
`Retained BSE ingestion retired` before campaign imports, receipt access, output
creation, signal alarms, Docker or database access. Refusal occurs outside the
old exception handlers so it cannot create a new stop receipt. No environment
setting, `--execute`, native target or snapshot declaration re-enables this dated
operation. Original implementation/evidence remains retained. Any future replay
or backend port needs a separately selected implementation and authorization;
there is no supported replay mode for this writer. Other SQLite recovery and
snapshot interfaces remain available under their existing guards.

## Validation and rollback

`test_legacy_sqlite_launchers` covers direct and callable BSE refusal on SQLite
and native configuration, plus existing historical snapshot/recovery boundaries.
The source-only inventory records reviewed hashes and lexical caller locations;
it never reads databases. Unrelated local recovery work is excluded from this
change and from staged-source validation. Reverting this patch needs no data
migration; it would re-enable the dated BSE entry points, so do not deploy that
rollback as an operational recovery procedure.

Validation run on 16 November 2026:

- PASS: `python3 -B -m unittest scrapper.tests.test_legacy_sqlite_launchers -v`
  — 15 tests. The new callable-refusal regression failed before the fix.
- PASS: `python3 /private/tmp/issue107-native.py scripts/audit/native_runtime_smoke.py`
  — 58 tests, no skips, on fresh disposable PostgreSQL 16 storage at local port
  55507; stopped afterward. The wrapper isolated every application write root
  and used the existing test virtualenv. Initial sandbox execution could not
  initialize PostgreSQL shared memory; the authorized rerun passed.
- PASS: `python3 -B scripts/audit/sqlite_usage.py --index`
  — 174 files, 755 sites.
- PASS: `python3 /private/tmp/issue107-offline.py -m unittest scrapper.tests.test_legacy_sqlite_launchers scrapper.tests.test_sqlite_usage_inventory scrapper.tests.test_ci_database_isolation scrapper.tests.test_database_compatibility_inventory scrapper.tests.test_container_healthcheck.ContainerHealthcheckTests -v`
  — 33 tests in an exported staged-source view, with isolated temporary write
  roots. An initial wrapper typo pointed to a nonexistent virtualenv; corrected
  to the existing test virtualenv before this passing run.
- PASS: `git diff --cached --check`; independent read-only reviewer reran all
  15 launcher tests and reported no remaining findings after caller documentation
  corrections. Historical bundle replay, deployment and live health: NOT RUN.

No production systems/data were accessed or changed.
