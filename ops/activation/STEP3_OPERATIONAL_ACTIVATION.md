# Step 3 controlled recurring activation

The successful GMBREW/TCS canary was retained and not repeated. Its validated
canonical financial worker, immutable application image, authenticated shared
transport and acceptance safeguards are reused. No new application build,
production migration, release-preparation cycle or review was performed.

## Market-cap canary

The prepared single check passed on 9 October 2026. `/company/530025/`, existing
company 3303, had a historical below-floor exclusion with no actual cap recorded.
The fresh Screener observation is **₹26 crore**, below the inclusive **₹580 crore**
floor. It correctly remains `below_floor`; the next check is seven days later.
The scope claim was released, failures remain zero and source evidence references
fetch 78717 / blob 144458. Accepted financial-version fingerprints and counts
were unchanged. No financial queue item was claimed or processed. Existing tests
cover exactly ₹580 eligibility and rising-cap re-entry without deleting history.

## Enabled schedules — Asia/Kolkata

The existing Aqua LaunchAgent and 60-second admission loop now include two Step
3 jobs. There is no standing production writer or additional daemon.

- **Financial:** daily at **HH:15 and HH:45**, maximum **two companies per slot**,
  600-second deadline. The current completed quarter comes from the shared IST
  helper. Only the current standalone official-filing prefix of the canonical due
  queue is eligible. A batch can contain one current item; older items never fill
  its remainder. Completed items are excluded by existing queue policy. Future,
  unsupported or historical prefixes cause an idle/stop outcome rather than a
  broader claim. The canonical atomic claimant and acceptance pipeline remain
  unchanged. Genuine quick-ratio content is checked before ingestion.
- **Market cap:** daily at **HH:00**, excluding **09:00 and 18:00** reserved for
  BSE. Maximum **40 checks per slot**, 600-second deadline. This is at most **22
  slots / 880 checks per awake day**. Oldest due scope rows are checked first.
  The original 4,366 overdue checks are carried as persisted due work; each
  operation remains bounded. Actual progress depends on source failures, new
  due work and desktop/Docker availability. Failed individual checks preserve
  prior eligibility and existing backoff; authentication, access, identity or
  ownership failures stop the affected lane for review.

Cap checks call the existing monitor with shared permits directly. They do not
use the coordinator's global historical-financial priority gate. Their hourly
windows and finite deadline give financial slots independent admission, so old
financial backlog cannot starve cap monitoring. Cap re-entry may update queue or
document-expectation metadata, but historical financial claims and document
consumers remain disabled.

Each clock slot has its own atomic receipt, including its hour/minute, and is
never replayed on restart, failure or wake. Missed calendar slots are skipped;
there is no unbounded catch-up loop. The dispatcher admission lock, shared host
acquisition lock, per-DB scheduler/executor locks, original queue/scope leases
and shared HTTP permits prevent overlapping approved owners and duplicate
claims. Existing image/volume/file/schema/marker identity, WAL/SHM, storage and
financial validation checks precede writes. A 13-minute exchange reservation
also prevents an unexpected Step 3 start close to NSE/BSE source acquisition.
NSE/BSE schedules, API holds and source provenance remain unchanged.

Source access uses the current private session file on each fresh operation;
the mounted file is compared with the host using a private nonced proof and the
financial transport checks its cookie jars. No token values are logged. Expected
below-floor exclusions and publication lag retain normal queue/backoff policy.
Unexpected financial failures halt the financial lane; no consumed slot is
automatically replayed. Claim/permit recovery remains fenced, never destructive.

The Mac must remain awake, the user logged in and Docker Desktop available.
All cadence waits happen outside production writer operations. Run/admission
records, selected IDs, source responses, financial results, scope outcomes and
queue decisions are retained under `artifacts/automatic-acquisition`.

## Verification

**47 focused tests and six subtests passed**, covering current-only selection,
one-item batches, historical/future rejection, independent cap admission, IST
spacing, unique hourly slot budgets, duplicate suppression, shared locks and
inclusive eligibility/re-entry. Existing Linux transport/acceptance evidence
was reused. The unrelated document, extraction, rating and historical workers
remain off.

The first real unattended financial slot, **20:45 IST on 9 October**, passed:
only ONIXSOLAR 18137 and LOTUSCHO 18138 were claimed, both for September 2026.
They were accepted as existing companies 2822 / 3085, parses **34419 / 34421**.
No GMBREW/TCS repeat or historical claim occurred. Ownership checks passed after
the bounded process exited.

The first recurring cap batch and ongoing overdue progress are recorded by the
read-only observer in
`artifacts/automatic-acquisition/step3-unattended-verification.json`. The first
unattended NSE acquisition already passed; BSE's first eligible browser slot
remains 10 October at 09:00 because its prior daily receipt was preserved.

Step 3's operational conclusion is recorded in the accompanying activation
verification after checking the first recurring cap batch. Ongoing backlog
reduction and any per-company exceptions remain monitoring work; they do not
authorize broad financial or document workers.

Evidence: [cap canary](../../artifacts/step3-financial-readiness/prepared-scope-monitor/runtime.log),
[first unattended financial run](../../artifacts/automatic-acquisition/2026-10-09-current-financial-2045/runtime.log),
[schedule configuration](step3_schedule.json),
[focused tests](../../artifacts/step3-financial-readiness/scheduling-focused-tests.log).


## First recurring cap batch — verified

The 21:00 IST launchd-origin batch passed with exactly 40 checks: 39 successful
fresh cap observations and one HEG HTTP 404. Prior HEG eligibility was retained
with canonical error backoff. All scope claims were released and accepted
financial versions remained unchanged. The due pool was 4,561 when the batch
started; it had grown since the 4,366 preparation reference. No global cap-stop
latch was created and no financial queue item was processed by cap monitoring.

Step 3 is **operational within the current-quarter-only scope**: both new lanes
are enabled and have completed real unattended runs. Continuing overdue
reduction and selective HEG source-path follow-up are monitoring/data-quality
work, not another activation or release-review cycle. The separate exchange
observer continues to record first real NSE/BSE evidence without forced calls.

[Operational activation verification](../../artifacts/step3-operational-activation/verification.json).
