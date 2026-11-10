# Read-only repository and runtime verification

Use `python3 -B scripts/runtime-status` from this checkout. It emits a versioned
JSON report to stdout without importing application configuration, initializing a
database, acquiring ownership locks, writing receipts, or starting services.
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
unknown; they do not become successful checks. A database identifier is a hash of
the resolved path, not its private filesystem location. Tool errors, environment
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
- `--database PATH` reads only `schema_migrations.version` from an explicitly
  selected **quiescent** SQLite database or operator-created consistent snapshot.
  It uses `mode=ro&immutable=1`, query-only mode and a SELECT/READ authorizer,
  never the application DB connection. Any WAL, SHM or journal sidecar causes
  unknown without opening SQLite. Immutable reads cannot safely observe an
  actively changing database. Do not use this option on a live writer; arrange
  a consistent snapshot through the separately authorized backup workflow. A
  snapshot result describes that snapshot, not the live database. The comparison
  uses complete filenames and the existing manifest's historical identities,
  reporting pending, unknown and duplicate identities. It does not certify SQL
  checksums, actual schema integrity, or snapshot freshness. No database backend
  or production path is inferred when this option is absent.
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
