# Trading AI operational recovery — 22 October 2026

Lifecycle commands now operate the existing bounded Aqua dispatcher. No live
shutdown/wake demonstration, pipeline rebuild, new acquisition request, queue
reset, database migration, worker restart or Git push was performed by this task.
The current acquisition run remains authorized under its existing limits.

**GitHub backup is blocked:** a credential remains in reachable local history.
There is no new backup commit or verified remote backup of these changes.

## Observed system and progress

The installed agent is `com.trading-ai.automatic-acquisition` in
`~/Library/LaunchAgents/com.trading-ai.automatic-acquisition.plist`, loaded in
`gui/501`, with an Aqua session, `RunAtLoad` and a 60-second interval. It executes
`ops/activation/automatic_acquisition.py --tick` using the existing pinned Python
environment. An idle launchctl state of `not running` is normal between ticks;
it does not mean the minute schedule is unloaded. The installed plist and source
manifest were checked against the current definition.

Observed progress, without initiating a new download or financial scrape:

- The 18:45 IST financial slot completed two claimed jobs with two accepted
  outcomes, covering retained September and June reporting periods under the
  existing priority policy.
- The 19:00 IST market-cap slot completed 40 checks at 19:03:29 IST. Its active
  `trading-ai-step3-cap` container was observed with a writable production volume;
  the first status inspection correctly deferred its database census.
- A later idle census passed ownership, SQLite sidecar/kernel-lock and lease
  checks. It observed 26,112 exchange filing rows and 26,019 filing enqueue
  records. These are row counts, not distinct company counts.
- That census observed 959 pending company jobs (6,371 accepted, 3,276 terminal
  failures and 2,302 already accepted skips); generic document downloads had
  23,838 pending, 1,931 retryable failures and two retained `LEASED` rows. No
  acquisition lease was unexpired. Rating downloads had 17 pending, one
  retryable failure and 11 requiring interaction. Processing inbox/work each
  contained three rows. These totals include legacy/historical work and are
  not a claim that every row is eligible for the current window.
- The existing restart report's verified CARE PDF and lineage were retained.
  This task did not independently acquire another document.

The census timestamp was **2026-10-22 19:08:50 IST**. Counts are a timestamped
snapshot, not permanent queue totals. Detailed evidence is in
`artifacts/operational-recovery/live-status-before-integration.json`.

**BSE is blocked:** its 18:00 slot retained
`BSE browser failed: EMPTY_OR_REJECTED_ROWS` and `BSE.stopped.json`. Other lanes
continued. The latch and original failed receipts were not cleared or retried.
Old error lines in launchd stderr are historical; status reports current latches
and timestamps rather than treating the last log line as overall health.

## Actual schedule and authorization

All schedule times are Asia/Kolkata; admission considers the current minute only.
There is no missed-slot replay or automatic catch-up.

- NSE primary: Monday–Saturday, 19:30. Later legacy ladder rungs are disabled.
- BSE: daily 09:00 and 18:00, subject to its retained stop latch and access budget.
- Financial: hourly at :15 and :45, at most two companies per slot. Existing
  current/late/revised priorities and separately limited historical allowance
  remain intact. Historical slots are 00:45, 06:45, 12:45 and 18:45, at most one
  per reserved slot and four per day.
- Market cap: hourly at :00 except 09:00 and 18:00, at most 40 checks.
- Documents, while authorized: discovery at 00:05/06:05/12:05/18:05; downloads
  hourly :25; processing hourly :35; patrol 02:05; rating 03:05. Existing limits
  remain discovery two, download/processing one, concurrency one and historical
  document capacity zero.
- Document window expires **23 October 2026, 14:42:05.845758 IST**. Wake reports
  expiry and never changes the window. The separate acquisition cutoff remains
  9 October. A future document window requires separate authorization.

The final status evidence includes its own next-run timestamp. Run
`./scraper status` for the next slot at the time of operation; do not use a saved
report's next-run timestamp as a current schedule.

## Current, required and legacy services

The current system has no permanently running acquisition writer. The minute
host dispatcher invokes finite NSE/BSE, financial/cap and document adapters.
They share `.scheduler-admission.lock`, `.nse-owner.lock`, existing per-database
ownership locks and database leases. Database writers are the admitted bounded
runtime containers; the host BSE browser captures evidence before its bounded
writer container ingests it.

Docker Desktop, the existing sealed images, the production data volume, the
pinned Python environment, a logged-in Aqua desktop, visible Google Chrome,
Screener session configuration and My Passport are still required dependencies.
Current transient Compose projects are `trading-ai-nse`,
`trading-ai-current-financial-once` and `trading-ai-phase4-routine`. The dispatcher
never needs the old continuous workers to be restarted.

All observed persistent Trading AI containers were stopped: scheduler, daemon,
market-cap-monitor, artifact-reconciler, document-download-worker,
rating-recovery-discovery/download, CARE/India Ratings/other credit-rating
workers, june-extraction-worker, CRISIL/other browser-rating workers and browser
extraction. `screener-master-backfill-v2` was also stopped. They are retained as
legacy/manual containers. Other projects' WordPress, PostgreSQL, Redis and
similar containers are unrelated; none was stopped or restored here.

Shared legacy modules remain necessary: exchange ingestion/filing identity,
Screener company ingestion, financial queues and priority selection, document
repositories/download transports/reconciliation/extraction, host access policy,
SQLite ownership/sidecar guards and browser adapters. No such module or container
was deleted. The manually invoked CLI and rating monitor remain available.

## Storage identities and locations

Production SQLite is `/data/scraper.db` in Docker volume
`trading-ai_scraper-data`, whose Linux mountpoint is
`/var/lib/docker/volumes/trading-ai_scraper-data/_data`. Its observed size is
retained in the census guard. `/data/snapshots` and `/data/exports` also contain
production evidence and exports. This volume is separate from the host file
`scrapper/store/scraper.db`, which exists with WAL/SHM sidecars and is not the
current production dispatcher database. Never substitute that host database.
Additional retained database snapshots exist below
`artifacts/acquisition-readiness/AR-01.3*`; their existence is not proof of a
current valid backup.

External documents are under `/Volumes/My Passport/passport/trading-ai`, including
`artifact_content`. The required volume UUID is
`60E1204B-CB98-48C4-9398-1D2FBAC9B44A`. Verification checked the exact external
mount, physical runtime identity, `.trading_ai_document_store`, four saved
original hashes and physical APFS capacity. Observed free space was about
375.16 GB, above the 150.03 GB reserve. A directory at the expected path, a
similarly named drive, or a `My Passport 1` mount does not pass identity checks.

## Implementation and operational behavior

`./scraper` remains the entrypoint. `ops/host/scraper_lifecycle.sh` routes status,
wake and shutdown through the dispatcher's existing pinned Python environment.
Start/resume/unpause route to wake; stop/pause route to graceful shutdown. The
old `.scraper_shutdown_state`, bridge-container setting and host-browser marker
are never consumed for restoration. Broad legacy worker CLI shortcuts are
explicitly refused.

A narrow dispatcher change adds a check for
`artifacts/automatic-acquisition/lifecycle-paused.json` inside the scheduler lock
and before direct slot admission. This is admission control only; stage logic,
source policy, queue safeguards, budgets and write fences are unchanged. The
source and manifest were reconciled while both existing owner locks were held.
Original bytes, hashes and reconciliation evidence are preserved under
`artifacts/operational-recovery/gate-integration/`. No lifecycle transition was
performed during integration; subsequent minute ticks passed manifest checking. The naturally scheduled 19:15
financial slot completed successfully at 19:16:09 IST after integration; the
final live status inspection passed at 19:18:37 IST, with its next slot reported
as document-download at 19:25 IST. This was observed, not triggered manually.

**Status** reads installed/loaded dispatcher identity, schedules, receipts,
active stage containers, latches, document authorization and physical storage.
Its queue census uses a networkless, temporary container with the existing image
and a read-only production volume. Cold-state locks/sidecars are verified before
an immutable SQLite connection and checked again after reading. Scratch query
storage is memory/isolated tmpfs; no production checkpoint or sidecar write is
performed. Census is deferred for an active owner or within 120 seconds of the
next scheduled batch. It reports the reason and the latest timestamped document
queue receipt where available, never claiming that old counts are live. A
partial inspection returns nonzero/`DEGRADED`; consult `inspection_errors` before
interpreting that as a pipeline failure. `pipeline_health` separately shows
blocked stages or paused admissions. The cold-state census does not run a full
14+ GB database integrity scan during acquisition.

**Shutdown** serializes lifecycle operators, preserves the previous active
configuration, durably closes admissions and waits up to 660 seconds for bounded
work. It never bootouts/kills a running tick or stops arbitrary containers.
If ownership, relevant containers, file handles, SQLite state or unexpired leases
remain, it fails and leaves admissions paused for inspection/retry. It does not
clear queues, expire leases, remove volumes or delete SQLite sidecars. Once
quiescent it syncs and rechecks host document-store handles. The dispatcher stays
loaded as an inert minute timer, with the persistent marker preventing admission
across logout/reboot. There is no current long-lived worker needing a stop.

A successful shutdown reports `safe_to_shut_down_mac: true`. It intentionally
reports `safe_to_disconnect_drive: false` until the operator ejects the drive
through macOS: acquisition quiescence alone cannot certify physical removal.
Finder must confirm ejection before unplugging. A failed shutdown never reports
either operation safe. Other apps can still hold drive files; Finder's eject
check remains necessary.

**Wake** verifies source/manifest and installed/loaded dispatcher identity,
duplicate LaunchAgent configurations, correct drive UUID/markers/reserve/hashes,
Docker and image availability, desktop/browser dependencies, owner locks,
containers, database cold state and leases. It resumes only a dispatcher that
was active in its saved current configuration. If its label is unloaded, it can
bootstrap that exact saved LaunchAgent under both locks. It never starts a
container or the legacy host browser. An already awake idle dispatcher is a
no-op; an active owner produces a refusal rather than another writer. Changed
configuration requires explicit reconciliation. Missed schedule opportunities
are counted without replay. Document expiry remains in the output even when
financial/exchange scheduling can resume. A failed wake preserves the pause.

## Tests and evidence

Focused host lifecycle and dispatcher tests passed **39 tests**; **12 Linux-only
sidecar tests were skipped on macOS**. The same Linux sidecar guard was exercised
successfully by the live read-only container census.

```sh
python3 -B ops/host/run_acquisition_lifecycle.py status
```

This runner uses the same environment as the LaunchAgent. The test invocation
uses that environment's Python with the prepared plist's `PYTHONPATH`:
`python -B -m pytest -q ops/host/test_acquisition_lifecycle.py
ops/activation/test_automatic_acquisition.py ops/activation/test_sqlite_sidecar_guard.py`.

Isolated coverage includes active-work draining and timeout, repeated lifecycle
calls, missing/wrong drive identity/sentinel/capacity, duplicate owner locks and
plists, loaded-definition drift, unchanged queue/SQLite bytes, interrupted slot
non-replay, expired-window reporting, authorization drift, exact-label bootstrap,
legacy state ignoring and shell shortcut routing. Docker bind aliases and parent
mounts of the document store are detected. No production worker cancellation was
used as a test.

Read-only live status verified the real loaded agent, current writer container,
progress receipts, storage identity, cold SQLite state and queue counts. Later
status close to a scheduled slot correctly deferred its census. Initial sandbox
restrictions and isolated query temporary-storage errors were not bypassed or
misreported as successful inspections.

All **1,875 existing completed receipt files** captured before gate integration
matched their SHA-256 afterwards, including retained BSE receipts. Evidence:
`artifacts/operational-recovery/receipt-preservation.json`. Ongoing scheduler
heartbeat/log files are intentionally excluded from the immutable receipt set.

## GitHub backup audit and stop

The GitHub connector confirmed account `Mfadhi-lg`, repository
`Mfadhi-lg/trading-ai`, visibility **private**, default branch `main`, and push
permission. Both local origin URLs match
`https://github.com/Mfadhi-lg/trading-ai.git`. Local branch remains
`chore/root-hygiene-pr`, HEAD
`0e6ee264931ba1a1e009e0785cf188f5dc9f37e8`; it was not replaced or switched.
There were extensive pre-existing staged and unstaged changes and 26,066 indexed
files. The existing index was not committed, reset or cleaned.

The historical `.screener_csrf` file contains 32 nonempty bytes in commit
`e71d1ee168d4a2b5c155aa6302347aeaad841e46`. Current HEAD deletes it, but that
commit remains reachable. Its value is not reproduced. Whether that historical
credential reached an existing remote branch has not been established. Treat it
as exposed; renew/invalidate the associated session credentials during an
approved maintenance window without breaking the active run.

Additional source-backup hazards include tracked extracted-document JSON blobs
larger than 100 MiB, tracked PDFs and generated ZIP/TAR bundles, staged OCI release
archives and untracked financial document samples, database snapshots and runtime
trees. Ignored `.env`/session files and SQLite files must remain excluded.
`.gitignore` was strengthened for SQLite sidecars, locks, recovery/dispatcher
runtime state, authentication state and identified generated archives; this does
**not** remove anything already tracked or committed. An initial secret-pattern
match in an HTML CSS asset name was a false positive; the separately verified
historical CSRF file is the actual stop condition.

**No backup commit, branch creation, push, history rewrite or remote deletion was
performed.** Audit work stopped at the confirmed exposure; no clean audit is
asserted for the remaining archives/history. Machine-readable redacted evidence
is `artifacts/operational-recovery/github-audit.json`.

A follow-up must establish a clean source-only lineage without rewriting the
existing repository: for example, a reviewed clean snapshot on a new independent
backup branch, or a branch based on a separately audited safe remote ancestor.
That decision is not permission to push the current history. Audit every staged
source file and required safe configuration, record exclusions, scan the entire
proposed reachable history, and verify a new branch/commit remotely after a
normal non-force push. Keep the current worktree/index untouched.

To clone the repository's **existing** source state:

```sh
git clone https://github.com/Mfadhi-lg/trading-ai.git trading-ai-restored
git -C trading-ai-restored rev-parse HEAD
```

This does not restore this task's unpushed work or production data. Once a clean
backup exists, clone its documented backup branch and verify the recorded full
commit SHA before restoring configuration/data. There is currently no such new
backup commit to provide. Source restore must not automatically run Docker
Compose, install an agent or authorize acquisition.

## Separate database, documents, configuration and state backup plan

No database or document backup was copied by this task. The following requires
a separately approved maintenance/backup operation and sufficient destination
capacity on a second physical device, followed by an encrypted offsite copy.
A copy on My Passport alone is not protection against losing My Passport.

1. Use the current shutdown command; require its quiescence verification and
   retain the pause throughout a consistent database/document backup. Do not
   stop Docker Desktop until the database export has finished. If ownership or
   sidecars are ambiguous, stop the backup operation and investigate.
2. Export `/data/scraper.db` using SQLite's backup API into a new destination
   database from a guarded one-off container with the existing image. Mount the
   source volume read-only, destination backup directory separately writable,
   disable network and never run migrations. A cold immutable source is allowed
   only after the existing empty-WAL/kernel-lock guard passes. An online backup
   would instead need SQLite's normal WAL-aware connection/backup protocol;
   never use `immutable=1` against a changing database. Do not `cp` a live main
   file, delete/checkpoint its sidecars, or copy a running Docker Desktop VM disk
   as a substitute for a SQLite-consistent export.
3. Open the **backup copy** independently and run `PRAGMA integrity_check`,
   compare schema/migration ledger and representative queue/document/financial
   counts, record SHA-256/bytes/time/source identity, and test a restore into an
   isolated volume. Include `/data/snapshots`, required exports and other retained
   evidence paths needed by database references. Inventory the host
   `scrapper/store` database separately; do not assume it is safe to copy merely
   because it is not the dispatcher's production target. It has sidecars.
4. While the same pause is held, copy My Passport's document tree to the second
   device with metadata-preserving tooling and a resumable manifest. Inventory
   all database-referenced content paths; hash copies against persisted content
   SHA-256 values and verify counts/bytes. Keep a manifest tying this document
   snapshot to the database export. Do not execute a broad live `rsync --delete`.
   Preserve sentinels/physical identity evidence separately; do not transplant
   old mount authorizations onto an unverified replacement drive.
5. Create a restricted, encrypted configuration bundle: `.env`, session/CSRF
   files, required secrets, prepared/installed plist, pinned interpreter/package
   versions, image IDs/digests, runtime identity/store markers, ownership/schema
   baselines, manifest, permissions and schedule JSON. The scheduler also depends
   on retained canary/authorization evidence referenced by `step3_schedule.json`,
   `step3_financial.py`, BSE manifests and storage preflight; inventory those
   references and include the required files. None of these secrets or private
   documents belongs in GitHub. Reauthorize credentials on another machine.
6. Back up runtime evidence separately: admission/outcome/source receipts,
   permanent BSE daily-slot receipts, stop latches, capacity evidence, lifecycle
   previous configuration/pause marker and checksum manifests. Do not reuse live
   PIDs, OS lock ownership or `.scraper_shutdown_state` to restart workers.
   Preserve interrupted receipts so consumed slots are not replayed.
7. Restore-test offline with no network/scheduler and an isolated volume. Verify
   document references, SQLite integrity, schema and queue counts. Restore
   production mount identities, interpreter paths, images and permissions only
   after review. Resume with wake after required validation. Keep daily versions
   during reporting, several weekly versions and an encrypted offsite copy;
   record each verification and periodically repeat a restore test.

## Exact operator commands: office/home travel

Run in the existing checkout on this Mac:

```sh
cd /Users/mohamedfadhi/Fadhi/trading-ai
./scraper status
./scraper shutdown --wait-seconds 660
```

Wait for shutdown's final JSON. Proceed with a normal Mac shutdown only if
`safe_to_shut_down_mac` is true. If it fails, admissions remain paused: inspect
its blocker and rerun status/shutdown; do not unplug the drive, kill writers,
clear leases or remove sidecars. After successful quiescence, eject My Passport
in Finder and wait for macOS confirmation before disconnecting it.

At the destination, connect the same drive, log into the Mac desktop and start
Docker Desktop normally. Verify the expected mount, then:

```sh
cd /Users/mohamedfadhi/Fadhi/trading-ai
diskutil info '/Volumes/My Passport'
./scraper wake
./scraper status
```

If wake reports document expiry, financial/exchange schedules can continue under
their existing authority, but no new document window is granted. If wake fails,
resolve the named identity/space/configuration/owner blocker and retry. Do not
start scheduler/daemon/old document containers as a workaround. Missed jobs are
reported, not replayed.

For explicit required compatibility functions after the current dispatcher has
been safely paused, retain the documented manual Compose profiles; for example,
`docker compose --profile manual-compat run --rm --no-deps exchange-reconciliation`
provides the legacy reconciliation report. `./scraper ratings` retains the
manual monitor, and `./scraper --help` retains CLI discovery. Any compatibility
writer requires its own established authorization/ownership checks and must
finish before wake. Continuous legacy services must not be started through
travel/lifecycle shortcuts.

## Remaining limitations

The code is tested and the read-only live integration is verified; an actual
shutdown/wake transition was intentionally not exercised on the active reporting
run. Shutdown can remain blocked by an unrelated external-drive consumer,
unexpired lease, nonempty WAL or unknown owner. Such blockers are reported, not
repaired automatically. Census near a schedule is intentionally partial to avoid
stealing a scheduled slot. macOS ejection is still required for physical drive
removal. BSE remains stopped pending separate source diagnosis. Document authority
expires at the time above. The GitHub backup remains blocked and these changes
are not remotely backed up. No scoring, AI extraction feature, dashboard redesign
or acquisition-pipeline rebuild is included.
