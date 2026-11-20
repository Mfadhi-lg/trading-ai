# ACQ-RELIABILITY-01: stopped acquisition investigation

20–21 November 2026. Acquisition has **not** been restored. A reproduced
exchange/document role-boundary defect has a source-only repair; recovery still
requires separate authorization. Initial runtime metadata observations were around 14:04 UTC,
later than the supplied 11:14–11:15 IST monitor snapshot. Historical failures
must not be described as newly occurring at the monitor timestamp.

## Scope and source baseline

Read `AGENTS.md`, the root README/map, status index, engineering workflow and
runtime verification guidance. Fetched GitHub `main` and fast-forwarded reusable
`fadhi` from `1b7e94515` to `8e2dd705c71a22f91b9dce855fba5673f7149314`
(merged PR #121). Seven pre-existing modified/untracked candle and terminal
files were preserved; none belongs to this repair.

Production inspection was limited to retained receipts, sealed source files,
LaunchAgent/process/container/image metadata and physical mount/identity reads.
No production database connection, worker invocation, publisher request, stop
clearing, credential/grant change, migration or restart was performed.

## Cause and resolution matrix

Each lane entry gives the failing operation, evidence, conclusion and remaining
gate. Paths below are relative to `artifacts/automatic-acquisition/` unless
otherwise stated. Original receipts remain authoritative and unchanged.

### FINANCIAL — ownership stop; historical process identity unknown

- `FINANCIAL.stopped.json` and
  `2026-11-14-current-financial-2045/outcome.json` record STOP at
  **14 November 15:22:01 UTC**: PID **44499**, production target in arguments
  without a verified process role. `retained_slot_replay=false`.
- `execution-started.json` records a successful ownership gate at 15:15:54 UTC;
  `runtime.log` records an idle financial selection followed by paired document
  activity. `step3_schedule.execute_once` performs `owner_gate()` again while
  saving `owner-after.json` after the container command returns; that receipt is
  absent. This is a host ownership failure after application work, not proof
  that financial parsing or persistence failed.
- That same original log records 30 document download attempts, 23 validated
  downloads and 23 successful companion extractions before the ownership STOP.
  These are historical successful acquisitions, not post-recovery measurements.
- `production_ownership.classify_process` fails closed when target arguments
  cannot be attributed to a verified role/handle. The failure receipt omits the
  process executable, complete target arguments and handle evidence. Its PID
  alone cannot establish what process owned that number historically.
- `ps -p 44499 -o pid=,ppid=,lstart=,comm=` returned no process (exit 1) during
  this investigation. Historical identity is **UNKNOWN**, not retrospectively
  classified as safe. No bypass or guard relaxation is justified.
- Gate: recover original process/target evidence if available, otherwise obtain
  review of the unresolved historical ambiguity and a fresh complete ownership
  census, locks and native leases before any separately approved recovery.

### DOCUMENT_PROCESSING — older child identity read failure

- `DOCUMENT_PROCESSING.stopped.json` points to
  `2026-11-16-document-download-0940/runtime.log:11`, at
  **16 November 04:12:38 UTC**. The processing companion exited 2 with
  `Invalid runtime identity file: /documents/.trading_ai_runtime_identity.json`.
- The parent had passed identity and physical storage checks around 04:10 UTC.
  The child independently imports configuration and verifies its own targets.
  `scrapper/runtime_identity.py:_read_json` combines I/O, JSON parsing and
  non-object failures into this message. The retained log cannot distinguish
  them. Historical invalid bytes or a persistent mount mismatch are unproven.
- Current host marker JSON exactly matches the approved production `/documents`
  object, release `c05.3-release-20261003`, roles `["documents"]`.
  `diskutil info -plist '/Volumes/My Passport'` confirms mounted APFS/USB volume
  UUID `60E1204B-CB98-48C4-9398-1D2FBAC9B44A`, matching
  `phase4_documents.py`. These host reads do not certify the historical child
  mount or Docker's current view. No marker was regenerated or overwritten.
- Gate: independently review child mount/configuration provenance and original
  child evidence; verify the same physical target inside the approved sealed
  observer profile before a recovery decision. Any marker repair needs its own
  reviewed procedure and owner authorization.

### DOCUMENT_DISCOVERY — Docker create collision, before application startup

- `2026-11-17-document-discovery-1118/runtime.log:2` reports Docker name
  `/trading-ai-phase4-routine` already used by container
  `2c2f6da0597d89083bf50f883563eeb00787983feaafd504a109af78e80cbffd`.
  Outcome STOP/exit 1: **17 November 05:48:55 UTC**.
- No application stage record exists in that log. This failure cannot establish
  a discovery parser, publisher or SQL defect.
- Both the exact ID and name are now absent from Docker. Their original
  creation time, labels, mounts and activity cannot be reconstructed from the
  conflict message. Do not attribute or remove another container by name alone.
- Gate: reconcile retained admissions, interrupted jobs and native leases;
  review orphan ownership evidence before authorizing any cleanup or stop
  resolution. Do not replay the consumed 11:18 slot.

### DOCUMENT_DOWNLOAD — same Docker create collision

- `2026-11-17-document-download-1108/runtime.log:2` reports the identical name
  and container ID; outcome STOP/exit 1: **17 November 05:38:44 UTC**.
  Both exact logs are identical 609-byte files, SHA256
  `6f938c261f110b3109524ad51945a3c03f065409e6667733f78f285f9501be5b`;
  offline assertions confirm the collision and absence of application stages.
- The preceding `2026-11-17-document-download-0928` timed out after 500.030585
  seconds with only a Docker `Creating` line. `timeout-cleanup.json` classified
  the container as `not_created`, `terminated=true` at its observation.
  A delayed-create race is a **hypothesis**, not proven attribution.
  `phase4_schedule.cleanup_timed_out_container` and its offline tests enforce
  exact absence diagnostics and ownership before cleanup.
- Later jobs reporting an existing stop are guard receipts, not new executions.
  Zero pending downloads in the supplied monitor snapshot does not prove queue
  completeness, discovery progress or acquisition recovery.
- Gate: same ownership/lease reconciliation as discovery; no consumed-slot
  replay or speculative parser patch.

### NSE — zero-redirect policy rejected a response; destination unknown

- `2026-11-16-nse-primary/runtime.log:4–5`, **16 November 14:00 UTC**, requests
  the September integrated-filing-results endpoint, then stops with
  `Exchange recovery redirect limit exceeded`. November 17 is a prior-stop guard.
- `nse_results_runtime.fetch_nse_response` explicitly passes `max_redirects=0`.
  `scrapper/sources/exchange/transport.py:fetch_exchange_recovery_surface`
  rejects a redirect carrying `Location` before returning a `FetchResponse`.
  Original status, response bytes, `Location` and destination were not retained
  in either the job or its retained NSE operation directory.
- Gate: recover saved response evidence from another original source if it
  exists. Otherwise separately authorize an exact, bounded diagnostic request
  that retains the first response and follows zero redirects. Verify official
  endpoint identity and access authorization before proposing any policy change.
  No redirect increase, URL-policy relaxation, TLS bypass or network retry ran.

### BSE — restricted native persistence denied after successful preview

- `2026-11-17-bse-evening/runtime.log:4` records successful preview, then
  SQLSTATE **42501** at **17 November 12:30 UTC**. November 18 is guard-only.
  The retained compose identifies login **`trading_ai_exchange`**, native backend
  and acquisition image `sha256:1bbaccc31e9ee7e912a9bc027cd89463ce85f0ebebe8188b10b476a5ec743e21`.
- Retained `response.raw`: 6,520 bytes, SHA256
  `03fe924eb47529f844d5c0e50ba5501695130e0bf4e6b4e053729beb2fa76e17`.
  Offline parser replay reproduces accepted 7 / rejected 0 / excluded 7.
  The failure occurs after parsing/preview. The application error adapter suppresses
  SQL details, so 42501 alone does not identify a denied statement or grant.
- Persistence/permissions modules and relevant adapter files match the retained
  job's hashes. No already-fixed BSE source difference was found on latest main.
- Offline reproduction is completed below. Original production denied SQL and
  actual server privileges remain UNKNOWN; obtain separately authorized original
  server-error/target evidence and approve replacement-release recovery. Do not
  broaden grants on the strength of an error code.

### Demonstrated BSE-compatible SQL defect and source repair

On a newly created disposable PostgreSQL 16 cluster, the original retained BSE
response parses 7 accepted / 0 rejected / 7 excluded rows. With a synthetic
exchange registry and no eligible application issuer, the existing restricted
exchange policy admits 4 jobs and reuses 3. Adding one synthetic eligible issuer
reproduces **42501, permission denied for table `artifact_events`**. The test-only
server log identifies the denied `INSERT INTO artifact_events`; the stack is
`discover_exchange_results` → `enqueue_result_filings` →
`register_recent_filing_expectations` → `ensure_result_artifact_expectations` →
`register_artifact_event`. Original production server-error evidence remains
missing: this proves a code defect, not retrospective identification of the
production statement or a production grant defect.

`exchange_authorizer` deliberately excludes document event/expectation writes.
The handoff introduced a call across that boundary. On native PostgreSQL the
outer repository transaction rolls back queue decisions when the handoff fails;
the previously persisted response/snapshot remains. Do not describe the entire
operation as all-or-nothing or assume the failed production operation wrote no
evidence.

The repair leaves both authorizers and database grants unchanged. The common
scheduled NSE/BSE adapter explicitly defers document expectations; generic
workflow and repository callers retain default immediate handoff behavior.
`run_automatic_discovery` completes the existing last-day/100-filing idempotent
handoff using its existing document role, after schema, ownership and batch-bound
checks and before due selection. Exact boolean selection is validated before
ingestion. No new HTTP request, worker, concurrency, schedule or source budget is
introduced. Fresh expectations become eligible under the existing due policy.
Filings older than one day are not silently backfilled; recovery of those
handoffs needs a separately reviewed and authorized finite plan.

## Installed release compatibility

`artifacts/acquisition-releases/active.json` selects
`acq-repair-02-20261117T103648Z`. The on-disk and loaded Aqua LaunchAgent both
select that release's `automatic_acquisition.py --tick` every 60 seconds.
`launchctl print gui/501/com.trading-ai.automatic-acquisition` showed loaded,
between ticks (`not running`), last exit 0. That is not proof of lane health.

Already executed work: the selected release's
`ops/activation/automatic-acquisition-manifest.json` records authorization
“Owner explicitly requested latest release in production; preserve stops, no
acquisition canary or grant”, phase `acq-repair-02-reviewed-main`, and the image
and revision below. This later deployment evidence supersedes the deployment
status at publication in the older ACQ-REPAIR-02 PR report. It does not authorize
this incident's recovery. GitHub `main` was checked through the GitHub connector
and still resolves to `8e2dd705c71a22f91b9dce855fba5673f7149314` (PR #121).

At **20 November 18:49 UTC / 21 November 00:19 IST**, the active pointer still
selects that same release. A fresh `launchctl print` confirms that exact tick
entry point, 60-second interval, loaded/exited between ticks, **last exit 2**.
The adjacent retained `launchd.err` entries report prior-stop guard refusals;
this differs from the earlier last-exit observation and is not evidence of a
new acquisition attempt. Fresh `ps -p 44499 -o pid=,ppid=,lstart=,comm=` exits 1
with no process. Historical PID attribution remains UNKNOWN.

Directly importing the installed `postgresql_release` and calling `verify()`
passed all **562 application / 124 operator** inventory entries and the bound
manifest. Configured `source_revision` is latest main
`8e2dd705c71a22f91b9dce855fba5673f7149314`. Local `docker image inspect` finds
the exact pinned application image and the same revision label; no image was
pulled or executed. This verifies local metadata/seals, not running image bytes.

The configured native target is database/schema `trading_ai`, target UUID
`8510d6ba-ee03-4f9f-ad8e-31f17f4ca65d`, with separate exchange, financial,
documents, reader and backup roles. The running `trading-ai-postgresql` container
image matches configured PostgreSQL digest
`sha256:d845e7f0ac8517b9d9868b6d20379f9688ba3676595e50ca7c0b664964b2a760`.
Live database identity, lineage, grants, sessions and leases remain **UNKNOWN**:
no production DB access was authorized or performed.

Only `ops/host/acquisition_terminal.py` and `historical_candle_lifecycle.py`
differ between this checkout and the installed sealed source inventory, matching
the pre-existing local edits **before this repair**. The new exchange/handoff
source changes also differ and are uninstalled. A replacement image and complete
paired operator release from the reviewed commit are required; do not overlay
them onto the installed image or update its seals in place. Earlier financial parser
and complete-screen repairs are installed; they do not resolve ownership stops.

Read-only `lsof` observation of the three existing host lock files found a Python
process with writable descriptors on scheduler/NSE lock files, but no reported
held lock. This momentary observation is not a verified complete ownership gate.
No lock was acquired, released or modified by this investigation.

## Guarded recovery and rollback proposal — not executed

1. Preserve original release pointer, complete manifests, images, six stops,
   admissions, execution receipts, request reservations and consumed-slot history.
   Review any proven code fix independently, run offline/native regressions and
   required CI, and prepare an immutable candidate from the reviewed commit.
2. Obtain explicit approval for restricted production reader observations, with
   exact installed observer profiles and bounded queries: native target/lineage,
   sessions/leases, actionable queue census, original BSE error statement if
   available, and document mount/identity. Do not run arbitrary worker entry
   points as diagnostics. No writes, stop changes or acquisitions in this phase.
3. Resolve each lane's evidence gates above. If the historical PID/container
   provenance cannot be recovered, record that limitation and require owner
   acceptance of a reviewed fresh ownership/lease/mount assessment; do not invent
   historical attribution. Separately approve any exact cleanup or identity
   procedure. No speculative grants or identity recreation.
4. Owner must approve the exact candidate, lane-specific stop-resolution action,
   UTC/IST operating window, source URLs/consent, request allocation, selected
   bounds, canary success criteria and rollback. No new grants or budget increases
   are installed by this investigation. Keep the scheduler unchanged until that
   explicit decision. Archive original stops immutably before approved resolution.
   For review, the installed ceilings are 10 financial companies, 5 discovery
   selections with at most 2 official requests, 30 downloads and 30 processing
   items per configured batch; NSE retains its two-request/100-new-job operation
   and BSE its one-capture/2,000-new-job operation. These are ceilings, not proposed
   new budgets or authorization to run full batches. Select smaller exact canary
   limits where the existing adapter supports them, reconcile shared permits and
   daily reservations, and independently seal the selected plan before approval.
5. Run one existing bounded operation per approved lane at a **fresh eligible
   slot**, within existing source policy and unconsumed budgets. Start with local
   processing only after identity verification; discovery/download depend on
   verified ownership. BSE must consume only approved retained/new capture input;
   NSE keeps zero redirects. Financial screen capture needs its existing explicit
   exact-day grant and a full capture allocation; do not borrow document budgets.
6. Measure baseline and after each actual authorized operating period: admitted
   attempts, successful source captures, new/reused canonical filings/jobs,
   newly stored/verified bytes, completed extractions, pending/due/retry/blocked/
   leased/terminal queues and oldest actionable age. Report arrivals and completed
   work separately so net backlog change cannot hide stalls. Reconcile lease
   expiries and duplication from receipts, with request reservations intact.
   Observe at least the actual relevant scheduled operating windows; a successful
   tick or zero pending-download count is insufficient. Post-recovery results are
   **NOT MEASURED**, because recovery has not been authorized.
7. Any identity drift, ambiguous ownership, privilege error, access block,
   response-policy failure or budget exhaustion ends the canary. Retain partial
   commits, source bytes, reservations and receipts. Pause/drain and restore the
   preceding complete release only through a separately approved guarded rollback.
   Never delete rows, replay slots, reset request counts, broaden grants, switch
   backends or rewrite stop history as rollback. No data migration is proposed.

Historical candle campaign recovery remains outside these six lanes. Its supplied
14 November inventory and STOPPED state were not treated as current native health;
the user's unrelated local candle recovery work remains preserved.

## Verification and outstanding work

This report does not certify restoration or post-recovery queue movement.

PASS on isolated test roots, Python 3.14 and newly created PostgreSQL 16 clusters:

```sh
python3 -B /private/tmp/acq-reliability-offline.py scrapper/tests/test_e01_exchange_universe.py scrapper/tests/test_phase4_discovery_selection.py ops/activation/test_production_ownership.py ops/activation/test_exchange_stage_recovery.py scrapper/tests/test_runtime_identity.py
# 79 passed, 4 subtests passed
python3 -B /private/tmp/acq-reliability-offline.py ops/activation/test_nse_results.py ops/activation/test_stage_recovery.py -k 'not sealed_image_cannot_execute_unpackaged_application_changes and not financial_preserves_image_ingest_hash_and_does_not_mount_document_overlay and not financial_refuses_unsealed_host_document_source'
# 19 passed, 3 deselected, 8 subtests passed
python3 -B /private/tmp/acq-reliability-native.py --smoke
# 60 passed, zero skips; temporary cluster stopped afterward
python3 -B /private/tmp/acq-reliability-native.py --tests scrapper.tests.test_postgresql_exchange_handoff scrapper.tests.test_postgresql_operations.NativeObservationTests -v
# 4 passed, zero skips, including scheduled adapter deferral assertions
python3 -B /private/tmp/acq-reliability-offline.py scrapper/tests/test_ci_database_isolation.py scrapper/tests/test_database_compatibility_inventory.py
# 5 passed, 80 subtests passed
python3 -B scripts/audit/sqlite_usage.py --index
# PASS: 174 classified files / 755 sites; unrelated local changes excluded
git diff --cached --check
# PASS after final staging
python3 -B scripts/audit/check_migration_manifest.py
# PASS: 77 canonical migrations, unchanged historical collision allowance
```

Local wrappers are isolated test launchers under `/private/tmp`, not production
entry points. They scrub application/database environment variables, select test
mode, point all writable roots at fresh temporary directories, and use the
repository hash-pinned test dependencies plus native dependencies and the
CI-pinned PyArrow 26.0.1. The native wrapper creates its own PostgreSQL 16 cluster
on loopback port 55497 and database `trading_ai_test`, and stops it in `finally`.
The checked-in CI reproduction is `scripts/audit/native_runtime_smoke.py` with
its required disposable test environment. The new native regression explicitly
blocks official and Screener source calls and SQLite fallback; due selection is
real SQL. The new case is included in the mandatory smoke suite.

Failures observed and resolved during verification: sandbox shared-memory/DNS
restrictions required host permission for the isolated server/dependency setup;
missing local document dependencies were installed only into `/private/tmp`;
initial test-only connection URL/target-identity/due-limit mistakes were corrected;
the first complete smoke attempt refused ten backup/candle cases because its
DSN database was `postgres` rather than guard-required `trading_ai_test`. The
corrected full run passed all 60; no test safety guard was weakened.

Three additional legacy-seal checks **FAIL** in the broader focused invocation:
`test_sealed_image_cannot_execute_unpackaged_application_changes`,
`test_financial_preserves_image_ingest_hash_and_does_not_mount_document_overlay`,
and `test_financial_refuses_unsealed_host_document_source`. They encounter old
sealed-source metadata and an unchanged `scrapper/db/database.py` hash mismatch
before their intended assertions. They are disclosed and excluded from the
19-test subset above, not claimed passed. Their obsolete seal fixtures were not
rewritten as part of this incident. Full repository and retained research audits
are NOT RUN.

Independent reviewer checked the original six logs/stops and installed seals,
reviewed the design and implementation, and independently ran:

```sh
python3 -B /private/tmp/acq-reliability-offline.py scrapper/tests/test_e01_exchange_universe.py::HandoffInputTests scrapper/tests/test_phase4_discovery_selection.py
# PASS: 7 tests, 4 subtests
```

Review corrected an unexpected-network test risk by adding fail-fast transport
entry-point mocks, and a standalone test import dependency. No material code
blocker remains. The reviewer did not independently execute native tests.

Remaining owner decisions and permissions are concrete: approve bounded native
reader observations for identity/lineage/sessions/leases/queues and original BSE
error evidence; review the physical child mount and unresolved ownership/container
provenance; authorize a zero-follow NSE first-response diagnostic only if original
saved evidence cannot be recovered; then approve the exact reviewed replacement
release and lane-specific stop-resolution/canary plan described above. Older
filing handoffs outside the one-day helper require a separate finite recovery
review. None of these approvals is implied by the source repair or PR.

No production DB connection, write, migration, grant/credential change, source
request, stop resolution, scheduler change, restart or new acquisition was made
by this investigation. Production metadata/retained evidence were read only.
All original stops and consumed slots remain preserved. Post-recovery queue
health is **NOT MEASURED / BLOCKED pending authorized recovery**.
