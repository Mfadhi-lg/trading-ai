# Read-only repository and runtime verification

Use `python3 -B scripts/runtime-status` from this checkout. It emits a versioned
JSON report to stdout without initializing a database, acquiring writer ownership
locks, writing receipts, or starting services. The default imports no application
configuration; explicit native observations use the verified application reader.
Exit zero means the report was generated, **not** that production is healthy.

By default only Git and repository migration filenames are inspected. Runtime
claims remain `unknown`. It never discovers database paths or reads a production
identity, release pointer, credential file, or external drive automatically.
`HEAD` is the default intended revision; select `--intended-revision main` or an
explicit reviewed SHA to compare against another locally available revision.
The command does not fetch Git refs. A dirty checkout is a warning.

## Authoritative location and freshness

The selected repository's Git revision and migration source files describe code.
They do not describe deployment. The authoritative record of last runtime
observation is an operator-generated report saved locally as
`artifacts/runtime-verification/latest.json` (create this report directory in an
authorized writable location before redirecting stdout). These reports are local
operational evidence, not source files; do not commit private runtime reports.
No such production report is created or certified by this source change.

Every claim includes `status` (`verified`, `warning`, `unavailable`, `unknown`),
`source`, `method`, and `observed_at_utc`. A verified claim certifies only the
stated method at that moment. The report has no aggregate healthy flag. Missing
permissions, unsupported tools, unsafe database access and missing evidence yield
unknown; they do not become successful checks. A database identifier hashes the
resolved SQLite path or verified native database/schema/target identity; private
locations, role names and credentials are not emitted. Tool errors, environment
variables and arbitrary migration ledger strings are not emitted.

Refresh after an authorized deployment, mount change, migration or dispatcher
configuration change, and before relying on runtime assumptions for operations.
The default maximum age is one hour, adjustable with `--max-age-seconds`.
Archive the preceding report before replacing `latest.json`. Pass the archived
file through `--previous-report` to classify its age. Even a recent saved report
is historical evidence and is never promoted into a current live claim. A missing
report means last verified production status is unknown. Future-dated or malformed
reports are unknown. Timestamps depend on the operator host clock.

`docs/STATUS.md` and `docs/operations/operational-recovery-20261022.md` are dated
historical snapshots. Their original contents remain preserved. Neither they nor
the README can establish present runtime health.

## Explicit runtime observations

Only select targets for which you have read authorization. Production database
access still requires explicit owner authorization under `AGENTS.md`. The issue
implementation and CI use synthetic fixtures; this feature does not authorize a
production verification run.

- `--container NAME` reads only a running container flag and its OCI
  `org.opencontainers.image.revision` label via filtered `docker inspect`. It
  compares a valid full SHA against the intended revision. Metadata is
  self-reported, so this is a warning with comparison results, not an attestation
  of deployed bytes. Missing labels and stopped/inaccessible containers are
  unknown. Host dispatcher code may differ from container code; its deployed SHA
  remains unknown without independent release evidence.
- `--native` requires `TRADING_AI_DB_BACKEND=postgresql` and explicitly provisioned
  `DATABASE_URL`, `TRADING_AI_POSTGRESQL_SCHEMA`, `TRADING_AI_POSTGRESQL_TARGET_ID`
  and `TRADING_AI_ENV`. Use a restricted reader login; production also requires the
  exact authorized runtime identity. This uses the application factory's native
  identity/login validation, native migration checksums and actual schema fingerprint,
  then verifies compatibility inside a repeatable-read READ ONLY transaction.
  It reports the native `_postgresql_migrations` lineage, never the legacy SQLite
  ledger. Connection, permission, target or lineage failures are `unknown` with
  `matches_expected=null`; there is no fallback. A 25-second wall-clock budget
  includes connection setup and compatibility checks. This option is explicit
  host configuration: it does not follow the installed release pointer or discover
  credentials. Default invocation remains offline even if PostgreSQL is configured.
- `--database PATH` preserves the legacy selection interface. When PostgreSQL is
  configured it requests the native observation above and ignores the legacy path.
  Otherwise it reads a **quiescent**, sidecar-free SQLite input with
  `mode=ro&immutable=1`, query-only mode and a SELECT/READ authorizer.
- `--sqlite-snapshot PATH` explicitly selects frozen SQLite even in a PostgreSQL
  environment. It cannot be combined with `--database` or `--native`. SQLite
  results carry `mode=frozen_sqlite_snapshot` and describe only the selected input,
  never current PostgreSQL status. Any WAL, SHM or journal sidecar causes unknown
  before opening SQLite. Arrange consistent snapshots through the separately
  authorized backup workflow; immutable reads cannot observe active writers safely.
  Complete filename comparison includes the existing historical identities and
  reports pending, unknown and duplicate identities. This does not certify SQL
  checksums, actual schema integrity or snapshot freshness.
- `--dispatcher` uses `launchctl print` for the current user's existing
  `com.trading-ai.automatic-acquisition` service. It recognizes the loaded
  dispatcher script and `--tick` argument. This verifies loaded mode only, not
  scheduler health, gates, schedule hashes, job completion or source revision.
- `--workers` uses `docker ps` with project/service labels only, on the selected
  Docker context. It identifies legacy Compose roles from `compose.yml` and
  `compose.browser-recovery.yml`, excluding the lifecycle's three current bounded
  adapter projects. Unknown trading-ai roles cause a warning. An empty list only
  establishes absence of those running containers in that context; other Docker
  contexts and host workers remain unknown. No workers are started or stopped.
- `--document-store PATH --document-mount MOUNT` checks directory read access,
  the required `.trading_ai_document_store` sentinel, and the explicitly selected
  enclosing mount point. Results distinguish `readable`, `unmounted`,
  `inaccessible`, `unavailable`, and unknown. Without a mount argument, readable
  storage is a warning because mount identity is unknown. A missing path alone
  does not prove the drive is unmounted. No write probe, capacity publication or
  readiness assertion runs; capacity, write access and content integrity remain
  unverified.

Example with an isolated migration snapshot and document fixture:

```sh
python3 -B scripts/runtime-status --intended-revision main \
  --database /path/to/consistent-snapshot.db \
  --document-store /path/to/document-fixture
```

Synthetic/mocked regression checks, requiring no runtime access:

```sh
python3 -B -m unittest scrapper.tests.test_runtime_status -v
```

No migration or rollout is required. Rollback consists of reverting the source
change; there is no production state to undo.

## Financial diagnostic readers (#98)

`ops/host/diagnose_financial_pending.py` and
`ops/host/financial_queue_eligibility.py` now select the configured authoritative
backend. Native reads reuse `postgresql_runtime.read_connection`: restricted
reader identity validation and a repeatable-read READ ONLY transaction, with a
25-second request limit. They bypass SQLite paths, sidecar checks and locks.
The existing eligibility/lease/disposition rules and historical filing cutoff
remain unchanged; the pending report now uses the shared reporting calendar
instead of permanently reporting September 2026. Its selected `period` is included.

Existing top-level report fields remain, with additive `status`,
`database_source` (backend/mode), and `observed_at_utc` metadata. Unavailable
observations exit 1 with `status=unknown`, no partial counts and no error details.
`--accepted-versions` and `--screen-evidence` retain their existing report fields.
Explicit `--sqlite-snapshot PATH` supports a quiescent, sidecar-free frozen input
and labels it accordingly. Installed default SQLite mode retains its cold sidecar
and reader ownership checks. Neither reader clears stops, renews leases or writes
queue state. A selection reason describes these existing diagnostic rules; it is
not permission to claim a job.

Installed invocation uses `acquisition_lifecycle.database_status` with script
`/host/diagnose_financial_pending.py` or `/host/financial_queue_eligibility.py`
(and the latter's existing mode flag). The wrapper already follows the installed
release. In a native release it uses the sealed `postgresql_release.reader_prefix`
with `--network=none`, read-only storage, the private socket and reader-only
credential mount. The observer allowlist accepts exactly those diagnostic scripts
and existing flags; snapshot overrides and arbitrary flags are not approved
installed observer profiles. Do not pass `--live` to these direct script calls.
No new service, deployment or default production probe is introduced.

The implementation is source-tested only. Production target availability, installed
release contents and live data reconciliation require separately authorized
observations; this document does not certify them.

## #98 verification record (15 November 2026)

The original `origin/main` runtime-status function reproduced the defect on a
synthetic SQLite ledger: with PostgreSQL selected it still reported SQLite as
`verified`. No production source or database was opened for this reproduction.

Validation used PostgreSQL 16.14, Python 3.14 and existing temporary dependencies.
The `/private/tmp/issue98-verify.sh` wrapper set `TRADING_AI_ENV=test`, every
application write root under `/private/tmp/issue98-test`, the explicitly disposable
`TRADING_AI_POSTGRESQL_TEST_DSN` for `trading_ai_test` on localhost port 55498,
and matching PostgreSQL clients. It did not supply production credentials.
CI retains Python 3.12 and its existing isolated PostgreSQL service.

- PASS: `sh /private/tmp/issue98-verify.sh -m unittest discover -s scrapper/tests -t . -p 'test_postgresql*.py' -v`
  — 197 tests, no skips.
- PASS: `sh /private/tmp/issue98-verify.sh scripts/audit/native_runtime_smoke.py`
  — 24 tests, no skips, including all six new native diagnostic tests.
- PASS: `sh /private/tmp/issue98-verify.sh -m unittest scrapper.tests.test_postgresql_financial_diagnostics -v`
  — six tests rerun after the final source adjustment.
- PASS: `sh /private/tmp/issue98-verify.sh -m unittest scrapper.tests.test_financial_diagnostics scrapper.tests.test_runtime_status scrapper.tests.test_postgresql_operations.NativeReleaseWiringTests -v`
  — 28 offline/status/wiring tests, including mounted-layout imports.
- PASS: `python3 -B scripts/audit/sqlite_usage.py --index`
  — 172 classified files, 705 sites. The source scanner now sees the shared reader
  instead of direct SQLite connections in the two financial entry points.
- PASS: `sh /private/tmp/issue98-verify.sh -m unittest scrapper.tests.test_sqlite_usage_inventory scrapper.tests.test_ci_database_isolation -v`
  — 13 tests in an exported staged-source view, preserving unrelated local edits.
- PASS: `sh /private/tmp/issue98-verify.sh -m unittest scrapper.tests.test_database_compatibility_inventory -v`
  — one migration source/hash preservation test.
- PASS: `git diff --cached --check`; independent read-only implementation review
  found no remaining blockers after strengthening no-fallback assertions.

Hosted CI, installed production commands and live database observations were not
run as part of local verification. No production systems/data, migrations,
deployment or acquisition were touched. No data migration is required for this
source change; rollback is a source revert followed by a separately authorized
release if ever deployed. Existing unrelated candle-recovery work was preserved
and excluded from this change.
