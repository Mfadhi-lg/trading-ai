# Retained SQLite operational launchers

Issue #100 classifies the launchers below as historical campaign tools. Their
fixed images, schema expectations, inode baselines and October receipt paths do
not describe the current PostgreSQL database. No production inspection,
activation, backup, restore or deployment was performed for this change.

## Current supported interfaces

Use the existing bounded lifecycle (`./scraper status`, `doctor`, `company`,
`search`, `monitor`) for current observations. On a configured native release,
manual application CLI commands use `ops/host/postgresql_cli.py` through
`run_acquisition_lifecycle.py`, preserving literal arguments and the sealed
release configuration. There is no root Compose fallback when native target
configuration is absent or missing.

Native operational code uses `postgresql_runtime.preflight`, `read_connection`
and `fingerprint`, with the existing physical-storage, identity, ownership and
restricted-role controls. Host readers use `postgresql_release.reader_prefix`.
See [runtime verification](runtime-verification.md) and
[PostgreSQL application configuration](../database/POSTGRESQL_APPLICATION.md).

Native backups use **only** `ops/host/postgresql_backup.py`: its default checks
configuration without database access; `--execute` performs the existing
verified encrypted native dump and requires separate operational authorization.
An old SQLite `quick_check=ok` receipt never certifies a PostgreSQL backup.
`acquisition_recovery_dispatch.py --execute` rejects native configuration before
reading its retained SQLite backup receipt or admitting any work. That retained
recovery workflow has no supported native replacement in this patch.

## Classified wrapper and caller inventory

The following host wrappers are retired. Direct execution and their callable
launcher functions refuse before output creation, locks or Docker invocation:

- `ops/activation/step3_inspect.py` selects `step3_inspect_runtime.py` or
  `step3_detail_runtime.py`.
- `step3_canary_state.py` selects `step3_canary_state_runtime.py`,
  `step3_accepted_details_runtime.py` or `step3_canary_source_runtime.py`.
  The source-only parser still verifies saved response hashes and needs no
  SQLite boundary.
- `phase3_inspect.py` and `phase4_inspect.py` select their matching runtimes.
- `phase4_attempt02_verify.py`, `phase4_attempt03_verify.py` select their
  matching runtimes; `phase4_canary_verify.py` also selects
  `phase4_retry_preflight_runtime.py` and `phase4_fingerprint_preflight_runtime.py`.
- `phase4_trial_preflight.snapshot_trial` selects
  `phase4_trial_snapshot_runtime.py` or `phase4_followup_snapshot_runtime.py`.
  Its callers `phase4_trial_observer.py` and `phase4_followup.py` are retained
  campaign launchers. The trial runtime invokes
  `phase4_routine_preflight_runtime.py`; gap and follow-up runtimes execute
  modified copies of its original snapshot source.
- `ops/benchmarks/run_phase4_throughput.py` selects `phase4_throughput.main`.
  `ops/host/phase3_local_benchmark.py` imports the independent `bench` helper
  but also inspects SQLite sidecars; its direct runtime requires the same
  explicit historical snapshot. The standalone `bench` helper remains available.
- `ops/docker/canary/ar013_readonly_host.py` selects `ar013_readonly_db.main`,
  a pinned schema-068/pending-069–075 diagnostic.
- `acquisition_recovery_backup_runtime.backup`,
  `acquisition_recovery_inspect_runtime.inspect`,
  `acquisition_recovery_integrity_benchmark.benchmark` and
  `acquisition_recovery_processing_state.py` are direct retained entry points;
  no in-repository host wrapper calls them. The integrity benchmark imports
  shared `phase4_documents_runtime.fingerprint` and
  `phase4_routine_runtime.routine_authorizer`; those maintained helpers and their
  native routing remain unchanged.

All activation paths above are under `ops/activation/` unless qualified.
The static candidate callers are also recorded in
[the reviewed source inventory](../database/sqlite-usage.json).

Root `compose.yml` is a retired production topology. Its four
`compose.browser-recovery.yml` services extend the old document worker and
override its entrypoint, so those compatibility launchers also explicitly refuse
startup; their old arguments/run identities remain retained. Every service now exits
with a refusal entrypoint, including explicitly named/manual services. It cannot
start a legacy worker merely because the host's native environment was not
forwarded into an old image. This supersedes startup use of that file; the
retained C05 topology manifests/hashes remain historical evidence unchanged.
`compose.development.yml` and `compose.browser-recovery.development.yml`
explicitly restore the original development entrypoints with its existing isolated development volumes and identities.
Do not use it against production stores.

## Explicit historical runtime replay

The old wrappers bind production volumes and cannot be used for replay. For a
separately authorized isolated Linux container, select a retained input bundle
and mount its snapshot read-only at `/data/scraper.db` (or its directory at
`/data`). Do not mount the live production volume, native sockets or credentials.
Set all three explicit input declarations:

```text
TRADING_AI_ENV=test
TRADING_AI_HISTORICAL_SQLITE_INPUT=/data/scraper.db
TRADING_AI_HISTORICAL_SQLITE_SHA256=<reviewed SHA256 of retained source bytes>
```

Mount this checkout's activation scripts read-only at `/activation`, including
`legacy_sqlite_guard.py` and `acquisition_release.py`. Select the matching original
application image and mount the original campaign's baselines, receipts, source
files and any writable output directory at their original paths. Replay output
must be separate from retained evidence. The guard rejects native environment,
local/selected release targets, manifest-declared missing native targets,
missing declarations, writable effective input mounts, symlinks and hash
mismatches. It emits `HISTORICAL_SNAPSHOT_ONLY` with the input hash on stderr;
the legacy backup receipt additionally declares `source_scope`.

Original SQL, sidecar, inode, ledger, schema, image, source-hash and owner checks
still apply. A copied bundle may fail its original inode or pinned-source checks;
that is a reproducibility limitation, not permission to rewrite the baseline.
No retained bundle replay is certified by synthetic tests. Migration identities
and retained source/evidence files were not rewritten or removed.

## Verification and limitations

`test_legacy_sqlite_launchers` exercises root and wrapper entry points, direct
runtimes, native selection without environment inheritance, snapshot declarations,
hash mismatch and writable child mounts, and recovery refusal before backup
receipt access. Tests mock source input and never inspect production data.
Existing native backup and operational controls remain the authority.

The historical `check_compose_release.validate_identities()` audit already fails
on retained `compose.browser-recovery.yml` hash drift. The root Compose retirement
also intentionally supersedes that old startup topology. Historical manifest
bytes are preserved; this patch does not re-certify the C05 deployment.
Rolling back this source patch restores the old launchers; no schema migration
or data rollback is required. Activating any prepared release is a separate task.

### Validation record (15 November 2026)

- PASS: `sh /private/tmp/issue100-verify.sh -m unittest scrapper.tests.test_legacy_sqlite_launchers scrapper.tests.test_manual_cli_profile scrapper.tests.test_cli_facade scrapper.tests.test_ci_database_isolation scrapper.tests.test_database_compatibility_inventory -v` — 32 tests.
- PASS: `sh /private/tmp/issue100-verify.sh -m unittest scrapper.tests.test_postgresql_backup scrapper.tests.test_postgresql_operations -v` — 20 tests, no skips, including native backup/restore and preservation/reader controls.
- PASS: `python3 -B scripts/audit/sqlite_usage.py --index` — 173 files, 715 sites.
- PASS: `sh /private/tmp/issue100-verify.sh -m unittest scrapper.tests.test_sqlite_usage_inventory -v` — nine tests in an exported staged-source view, preserving unrelated working-tree edits.
- PASS: `TRADING_AI_DOCUMENT_HOST_PATH=/tmp/issue100-documents SCREENER_SESSION_FILE=/tmp/issue100-session docker compose --profile '*' -f compose.yml -f compose.development.yml config --format json` — configuration only; no containers started.
- PASS: `git diff --cached --check`; independent reviewer found and rechecked the effective mount and local benchmark/callable follow-up fixes, then reported no remaining blockers.
- FAIL, reproduced on unchanged `HEAD` source with matching retained canary/config inputs: `sh /private/tmp/issue100-verify.sh -m pytest ops/activation/test_acquisition_recovery.py ops/activation/test_phase4_routine_schedule.py ops/activation/test_step3_schedule.py ops/host/test_acquisition_lifecycle.py -q` — 81 passed, six existing budget/cadence assertions failed.
- FAIL, reproduced on unchanged `HEAD`: `sh /private/tmp/issue100-verify.sh -m pytest ops/activation/test_phase4_followup.py ops/activation/test_phase4_bounded_followup.py -q` — nine passed, one existing expiry assertion failed.
- FAIL, historical audit: `sh /private/tmp/issue100-verify.sh -c 'from scripts.audit.check_compose_release import validate_identities;validate_identities()'` — retained `compose.browser-recovery.yml` hash drift already observed before this patch; its launchers are now explicitly retired too.

The temporary verification wrapper used Python 3.14, existing isolated test
dependencies, `TRADING_AI_ENV=test`, all application write roots under
`/private/tmp/issue100-test`, and a newly initialized PostgreSQL 16 cluster at
`127.0.0.1:55500/trading_ai_test` owned by test role `issue100`. An initial attempt
against a prior temporary test cluster failed because that server was unavailable;
only the new disposable cluster provided the passing native result. No production
systems/data were touched. No historical source bundle replay or deployment was
run. The unchanged-source comparison used an archive of commit `7f14bc142`, with
only the two retained Step 3 canary receipts and fixed follow-up config copied into
the temporary baseline to satisfy its original input paths.

Browser compatibility configuration was also rendered with
`TRADING_AI_DOCUMENT_HOST_PATH=/tmp/issue100-documents SCREENER_SESSION_FILE=/tmp/issue100-session BROWSER_RATING_RUN_ID=offline-issue100-config docker compose --profile '*' -f compose.browser-recovery.yml -f compose.browser-recovery.development.yml config --format json`.
All four isolated development entrypoints match the original browser command;
rendering `compose.browser-recovery.yml` alone confirms four refusal entrypoints.
No containers were started.

## Retained BSE writer (#107)

`bse_retained.py` and `bse_retained_runtime.py` are retired on every backend,
including their callable `main`, `run` and `_run_owned` entry points. The dated
eight-filing writer mounts production storage writable and cannot use the
read-only snapshot replay contract above. Refusal precedes receipt/output access
and the old stop-receipt handlers. Original source/evidence is preserved; no
replay or native use is selected. See the [complete reader disposition and caller
review](retained-reader-dispositions.md) for #107's native and retained entries.
