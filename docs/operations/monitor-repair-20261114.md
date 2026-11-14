# Monitor error repair — 14 November 2026

This follow-up preserves the existing PostgreSQL acquisition deployment, queue
history, source budgets and retained stop receipts. Source changes alone do not
repair a running release. No production database query/write or deployment was
used for the diagnosis below.

Confirmed failures and changes:

- DOCUMENT_PROCESSING: retained 11 November runtime failed creating
  `/data/document-period-recovery.json.*`. Native deployment intentionally makes
  `/data` read-only. Native recovery now writes in configured writable CAMPAIGNS;
  the old cursor is read only as an initial continuation when no new cursor
  exists. SQLite keeps its prior state location. Original cursors are preserved.
- FINANCIAL: retained 22:55 job exited2 with PostgreSQL SQLSTATE42883. The priority
  selector used SQLite `json_extract`; revision paths also retained SQLite-only
  JSON and INSERT OR IGNORE syntax. These reviewed statements now choose native
  JSON text expressions/ON CONFLICT for PostgreSQL. Queue identity, accepted
  pointers, event ordering and history limits stay intact.
- DOCUMENT_DISCOVERY: the 14 November timeout cleanup recognized uppercase
  absence diagnostics but rejected exact lowercase `error: no such object:`.
  Recognition is now case-insensitive and anchored to only the expected object;
  daemon errors, other containers and mixed diagnostic failures still block.
  The original495-second creation timeout remains distinct and is not certified
  as a successful discovery run.
- DOCUMENT_DOWNLOAD ownership: PID20259 had exited by13:07:37UTC. At13:08:49UTC,
  no readers remained and only PostgreSQL was running. The workspace had no
  native target, so its ownership guard rejected the exact reader command from
  the active native release. Recognition now uses the exact manifest-bound
  active-release profile, retaining image/mount/security/action checks.
- Quarter monitor: runtime phase of the90-second timeout is not proven. Native
  readers no longer wait on SQLite's sidecar lock. SQLite gate waits are bounded,
  the entire reader (including startup) has a25-second wall-clock budget, query
  deadlines cannot extend that budget, and PostgreSQL statement/lock settings
  apply before identity/login/schema checks. Failed native read-snapshot startup
  closes its connection. Host timeout reporting is concise and retains the prior
  snapshot instead of displaying an entire Docker command.

The nine repaired runtime files matched the active release before editing.
Active baseline: `artifacts/acquisition-releases/postgresql-bse-config-20261111`,
source revision `cb260cd9648606b01388ce8e4aa45d893934e6e2`, application image
`sha256:0ca51062c97b6aa81567fa83c70b57eba171a98c8fab63a7fa0ff8efe158a0f5`.
A narrow repair release can therefore be prepared from those original bytes
plus these reviewed file changes, without activating the new market service.

Verification: isolated financial-priority/monitor/cleanup checks32passed with
2retained-image preparation fixtures deselected; processing/recovery16passed;
portable native wiring/lifecycle17tests OK with4native-DB cases skipped locally.
Independent review additionally verified61tests and20subtests plus10cleanup tests.
No remaining blocking code finding was reported. A new native PostgreSQL contract
exercises priority selection and revision-event settling against the SQLite
fixture; it requires the disposable CI PostgreSQL service, not production.
The two older preparation fixtures fail because their retained SQLite image hash
for unchanged `scrapper/db/database.py` differs from the checkout. The guard was
preserved; those fixtures do not certify a replacement native image.

PR94's first offline CI run executed2019tests and failed only its operational
experiment-import register check. The20reviewed LIVE-FRESH imports are now
registered. Local source audits also ignore the untracked `.venv` dependency
tree rather than parsing vendored packages as application source.

Production repair acceptance must use a newly sealed native image/release,
current ownership checks and existing admission locks. Preserve the old release
and every stop/admission/outcome receipt. Reconcile only the four proven stop
causes after verification; never erase uncertain provider delivery, force-clear
leases, replay consumed jobs or widen source grants. Reader/SQL query validation
must use the restricted role. Any further production action requires the owner's
explicit approval under AGENTS.md. No new market/results LaunchAgent is part of
this repair.
