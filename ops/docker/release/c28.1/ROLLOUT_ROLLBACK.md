# C28.1 AR-01.2 release review and AR-01.3 proposal

This is an offline package. No production activation is authorized. The exact
reviewed source is `c3ddbbb9c298d07b8b46a362996251417282476a`; the pending
AR-02.1 IST patch is excluded. Its tested fix needs a separate reviewed image
before AR-02 result proof. Never overlay host source onto this image.

Read `release-manifest.json`, the AR-01.2 evidence record and checksum index.
All 21 service definitions are enumerated in `config-mount-map.json`, including
17 main and four operator browser definitions. Each has its own explicit profile,
no automatic restart, 0.5 CPU by default (existing caps retained up to 0.6), 1.5 GiB
memory, 180-second shutdown grace, and a read-only SQLite liveness check.
These are proposed canary limits; test load does not prove production sizing.
Application role progress must be measured separately from database liveness.
`authorized:false` identities deliberately prevent production writes. No profile
may be enabled until its exact scope is authorized. Do not run a blanket Compose up.

## Before separately selected AR-01.3

1. An independent reviewer signs the exact package checksum and proposed bounds.
   Name a recovery operator, 15-minute observation window and durable snapshot
   destination with capacity for the DB and sidecars. Retain the OCI archive in
   durable approved storage; `/private/tmp` is not durable. No registry publication
   is part of AR-01.2. Verify archive SHA256 and OCI/platform/config/layer digests
   before loading and again after load. Require local digest resolution; never
   fall back to a mutable tag, rebuilding, or a remote image with unknown source.
2. Reinventory every Trading AI container/process, Compose project label, image,
   mount, host cron/launchd task and document capacity-file writer. Confirm no old
   or new scheduler, monitor, daemon, browser, downloader or processing owner runs.
   The stopped containers reference temporary C05.3 config paths; do not reuse
   those paths. Any writer/active claim, unknown owner or identity mismatch stops
   the operation. Check all queue leases and in-flight IDs against the fresh DB.
   Preserve them; no lease reset, claiming, seeding or queue draining is authorized.
3. Confirm the **existing** `trading-ai_scraper-data` volume contains the intended
   `/data/scraper.db`, schema 068 (69 ledger identities including archived 065),
   no earlier pending or unknown migration, and no partial 069–075 DDL. Confirm
   `/Volumes/My Passport/passport/trading-ai` is the mounted external document
   store, never a newly created empty directory. Explicit external-volume config
   prevents creation of a replacement DB volume. No source DB writes in preflight.
4. Preserve all old identity/sentinel bytes and config hashes. Snapshot the fully
   fenced database consistently, including any WAL/SHM; use SQLite backup on
   quiescent state or a validated checkpoint followed by copy, as authorized in
   AR-01.3. Record source/snapshot hashes, integrity, exact schema/ledger, all
   original-column row digests, queue counts/leases and FK identities. The audit
   is not a fresh rollback snapshot. Rehearse snapshot restoration on disposable
   storage before touching production. Stop if this cannot be demonstrated.
5. Diff each packaged migration against AR-01.1 checksums. Fresh baseline FK
   violation identities must match the 10,771 recorded orphan queue references
   or have separately reviewed explained changes. No repair/fabricated parents.
   Validate required secrets by presence/readability only; never log their contents.
   The package uses an operator secret reference, not the old secret's contents.
   Check document sentinel and host capacity file (max age 3,600 seconds), at least
   100 GiB and 15% free, before any later download lane starts.

## Proposed finite migration and canary scope

This requires explicit AR-01.3 production migration and runtime authorization;
the proposal itself grants none. Maximum: seven migrations, one migration process,
one read-only CLI canary at a time, three probes over 15 minutes, zero exchange or
publisher requests, zero downloads/extractions/backfills, zero accepted-row changes.

After authorized target review, generate an activation-only copy of the identities
with `authorized:true`; record its exact diff and config hash. Install matching
release sentinels only while writers remain fenced, retaining all prior bytes.
Changing this flag is part of AR-01.3, not an AR-01.2 success claim. Use the candidate
digest and exact target mappings; no production source mounts.

Apply through the image's explicit `init-db` CLI action once. It must see only the
reviewed pending full filenames 069–075; the runner itself applies all pending SQL,
so any additional pending identity is a hard stop. Do not invoke it on partial DDL.
Require expected nullable claim columns, eight empty new tables, four explicit
indexes, processing trigger, and exactly seven new ledger rows (69 to 76). Compare
all original row digests/ledger timestamps, the six implicit uniqueness indexes,
and exact FK violation identities using the AR-01.1 proof methodology. Full scans
may exceed the observation window; complete them before beginning the canary.

Then run a disposable **read-only CLI container**, `restart:no`, networking disabled,
capabilities dropped, no-new-privileges, read-only root, ephemeral `/tmp`, existing
DB volume mounted read-only, no secrets and no document acquisition mounts. Override
the image entrypoint with Python and use `connect(..., read_only=True)` followed by
`check_schema_compatibility`, `SELECT 1`, migration ledger and schema enumeration.
Do not pass `immutable=1` if a WAL is present. The health check alone does not prove
schema or worker progress. Probe at minutes 0, 5 and 15; each must finish within
30 seconds and report the exact source, platform/config digest and schema. Compare
stable original row/queue identities before and after. All production role startup
is still held. This canary proves runtime/schema compatibility only; later AR-02–08
must prove acquisition. No automatic widening is proposed.

## Hard stop and recovery

Stop on any digest/config/source/mount drift; unavailable snapshot; writer/owner
ambiguity; failed probe or schema mismatch; extra/missing migration or object;
unexpected original-row/queue/content change; any **new** FK violation; any
source request or acquisition work; or uncertain restore compatibility. Zero
unexpected mutation is the threshold, not an error percentage.

On interruption, retain failed DB plus sidecars/logs and fence all writers. The
runner is not crash-atomic: 069 can persist one column without its ledger row and
blind retry fails. Never retry partial DDL, fabricate ledger records, or drop columns.
If **no post-snapshot accepted or operational writes exist**, restore the verified
quiescent pre-change snapshot with sidecars handled as a single consistent state,
restore old sentinel/config bytes, verify original schema/ledger/row/FK hashes,
and leave all production services stopped. Rehearse any new migration attempt
on disposable state before a new authorization. This is the demonstrated state
rollback path; C05.3 is retained evidence, not a runtime restart target.

If new writes exist or quiescence cannot be proved, **do not restore the snapshot**.
Keep services stopped, preserve partial state and reconcile exact DDL/ledger/rows.
A separately reviewed forward-recovery operation is required. There is no proven
automatic recovery or previous running C28-compatible release. For schema 075
runtime faults, the safe response is stop and retain 075 state until a corrected,
reviewed C28-compatible digest is available. Never pair old C05.3 with upgraded
state merely to show healthy containers.

Pending jobs, accepted history, original content bytes, hashes, first-observed
times and attempts remain preserved throughout. Shutdown uses graceful TERM then
the reviewed 180-second grace; escalation to force-kill requires pending-claim
reconciliation. No queue deletion, manual acceptance or historical drain.

## Later startup order (separate scopes)

After the CLI compatibility canary passes: AR-02 selects one result-discovery and
acceptance owner; AR-03 selects scope observation; AR-04 selects downloader and
durable processing handoff. Only then select artifact reconciliation and rating
recovery. Scheduler catch-up, artifact-reconciler startup backfill and recovery
target seeding are unbounded relative to this canary and remain disabled. Browser
roles require their own run ID and explicit operator policy authorization.
Stop in reverse dependency order, with admission/schedulers fenced first, then
workers gracefully drained/stopped and claim ownership recorded. Never run the
manual worker alongside its daemon owner or duplicate host-partition downloaders.
