# Installed automatic acquisition

Installed 9 October 2026 in `gui/501` as `com.trading-ai.automatic-acquisition`.
The Aqua LaunchAgent runs the acquisition-only admission adapter every 60 seconds.
It reuses APScheduler CronTrigger, the existing scheduler's primary NSE time,
shared IST period selection and canonical acquisition workflows. It never calls
the broad scheduler's startup catch-up or pending-task dispatcher.

- NSE: Monday–Saturday, **19:30 IST**. The latest completed quarter and preceding
  quarter are selected from the IST clock. Existing bounds remain two requests,
  no redirects/retries, 30-second HTTP timeout, 10 MiB total input, 6,000 accepted
  rows per quarter (12,000 combined), 100 new queue entries and a 300-second runtime deadline.
- BSE: daily, **09:00 and 18:00 IST**, at most one page attempt per slot, no retries.
  User attestation confirms written consent covers unattended visible-Chrome
  collection, electronic storage and project reuse. The consent document was not
  independently inspected. The existing fresh-context visible Chrome runner and
  permanent `artifacts/bse-browser-results` receipts are reused. One captured
  response goes through the existing retained-response adapter and canonical
  ingestion in the existing BSE image, with up to 6,000 rows and 2,000 new queue entries per run,
  and the same byte/runtime bounds.
  Its writer container has no network. This recent-publications surface does not
  establish complete historical BSE coverage.

Both adapters acquire `.nse-owner.lock`, then perform the existing production
ownership gate and acquire per-DB scheduler/executor OS locks. Existing lease
checks, volume/file identity, exact schema/ledger, physical markers, runtime
identity, canonical deduplication and SQLite cold-start WAL/SHM guards remain in
force. Nonempty WAL, active handles, ownership ambiguity or drift stop execution;
no WAL/SHM deletion, checkpoint, migration or recovery is automatic. Acquisition
writes are limited to the successful BSE operation's metadata/filing/queue table
boundary; financial observations, document processing, ratings and API holds are
outside that boundary. No application image was rebuilt or pulled. The original
release/checksum/review files remain unchanged. The scheduling phase manifest
records the original seals and replacement hashes for the affected host/operator
files; unchanged sealed application inputs still require their original hashes.

The Mac must be awake with this user logged into an Aqua desktop session, and
Docker Desktop must be running. Visible Chrome passed both a direct local-page
check and a check launched by the installed Aqua job. Logging out removes the GUI
execution environment. Sleeping, shutdown or missed minutes do not replay slots
on wake/login. The scope deliberately excludes historical catch-up.

Before an operation starts, an atomic admission directory consumes its clock
slot, including interrupted attempts. Existing browser receipts also suppress
access: the original **9 October legacy daily receipt** consumes today's BSE
budget, so today's evening slot is skipped without a failure latch. The first
eligible scheduled BSE access is **10 October at 09:00 IST**; the next NSE slot is
**9 October at 19:30 IST**. No original receipts were deleted or relocated.

Each admitted run records its start, outcome, runtime output, per-period source
counts, inserted filings, filing decisions, queue decisions and source snapshot/
fetch IDs under `artifacts/automatic-acquisition`. NSE's original operation
evidence directories also remain in use. Failed runs emit structured stderr and
create `NSE.stopped.json` or `BSE.stopped.json`: subsequent slots stay stopped
until an operator resolves the specific failure and explicitly resumes that
exchange. Retain partial writes and all evidence; do not retry a consumed slot
or clear access holds. Scheduler/configuration failures are recorded separately.
No external alert destination or message delivery was configured.

Verification: **56 host tests plus 22 subtests**, **12 Linux WAL/SHM tests**, both
production preflights, installed launchd ticks and Aqua Chrome verification
passed. Production verification made zero new source requests and ingested zero
filings. BSE's canonical retained-source preview passed with 8 accepted filings,
45 excluded rows, 0 rejected rows, 0 new queue entries and 8 reused decisions.
Production counts remained 12,259 BSE filings, 13,483 NSE filings and 12,762 queue
rows; both API holds remained unchanged. No workers or historical tasks started.
The first actual unattended source acquisition remains pending its clock slot.

Evidence: [verification](../../artifacts/automatic-acquisition/verification.json),
[installation](../../artifacts/automatic-acquisition/installation.json),
[consent scope](scheduled-bse-permission.json),
[phase manifest](automatic-acquisition-manifest.json).
