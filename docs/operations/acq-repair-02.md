# ACQ-REPAIR-02: bounded complete results-screen admission

17 November 2026. Baseline `e2d0db63e` (main including merged PR #120),
fast-forwarded into `fadhi`. Unrelated local edits remain excluded from this PR.
PostgreSQL is the production backend; SQLite is checked only for the requested
compatibility contract. No production observation or mutation was performed.

## Cause and scope

`financial_screen_refresh.MAX_PAGES=32` rejected the previously observed page 1/37
before complete capture. PR #120's abbreviated-period parser repair is preserved.
The refresh also previously called `enqueue_recurring_screen_pages`, which could
admit moving-count partial coverage. This refresh now uses the existing strict
`enqueue_complete_screen_pages` contract. Other consumers of recurring admission
are unchanged.

The ceiling is exactly 37 pages, independently constrained by an explicit grant.
The first page consumes a durable request reservation. After its validated page
metadata is retained, every remaining page must fit both the per-capture and
daily allocations before page 2 can dispatch. Failed/interrupted captures keep
reservations; same-day unmetered legacy refreshes defer rather than inventing
historical redirect counts. A new day needs a new explicit grant.

Reservations live in `scrape_runs.config_json`, under the existing refresh code
identity. SQLite uses BEGIN IMMEDIATE; PostgreSQL uses the existing application
writer advisory lock inside a short transaction. No transaction spans HTTP.
The ledger counts all financial-screen refreshes across grant/quarter identities;
it does **not** measure aggregate Screener usage by company/document workers.
Operators must allocate this financial-screen quota from applicable broader
source/day allowances after accounting for those consumers. No existing document
budget is borrowed or raised by this patch.

The caller takes `financial_screen_budget` only from the existing hashed selected
plan. No grant is installed in schedule/configuration by this PR. An absent,
expired, malformed or mismatched grant defers with zero source requests. Grant
fields are:

- `authorization`: nonempty operator approval/evidence reference, not a credential.
- `url`: exactly `https://www.screener.in/screens/3844820/new-results/`.
- `query`: exactly the refresh's current quarter/floor query after its existing
  canonical whitespace normalization of the saved screen.
- `day_utc`: explicitly selected UTC calendar day.
- `max_pages` and `requests_per_capture`: integers 1–37.
- `source_requests_per_day` and `requests_per_day`: separately approved
  financial-screen allocations, each integer 1–148. The 148 ceiling bounds four
  complete six-hour captures; it is not a default grant or a schedule change.

Every page uses its exact numbered URL. Redirect following is disabled for this
capture, so one page cannot consume unreserved redirect requests. The original
8 MiB response bound, REQUEST_TIMEOUT, shared host permits, pacing, cooldowns and
ownership checks remain enforced. The transport's pre-dispatch checkpoint also
checks the grant day, including after waiting for a permit. Observed failed-hop
redirect bytes are retained through the existing partial-evidence API.

Stable page numbers, total pages, result count, exact query/source identity,
valid company/period parsing, no future periods, complete coverage and unique
company evidence precede admission. Original blobs and candidates are retained;
canonical queue identity/deduplication is unchanged. No migration is required.
No downloader, schedule, concurrency, stop receipt or production runtime changed.

## Before/after admission evidence

Synthetic capture: 1833 distinct companies, 36 pages of 50 plus a final page of 33,
using the publisher-style abbreviated-period header and Sep-26 values. The test
replays the same capture against a 32-page ceiling and then the 37-page ceiling.
Before: one page requested, deferred, zero canonical jobs. After: 37 pages,
complete capture, 1833 canonical jobs. Replaying complete evidence adds zero jobs.
This is executable synthetic evidence, not reconstructed publisher history.

The original private retained page described in [ACQ-REPAIR-01](acq-repair-01.md)
is 63033 bytes, SHA256
`42ff2bf8bdaa9a120b3e7843c765bb4c0b5d8029b02e8699ade88bc154639b11`.
Its former `/private/tmp/acq-repair-01/screen-91766.html` path no longer exists.
Original replay is **NOT VERIFIED** in this task. The optional regression takes
`ACQ02_RETAINED_SCREEN`, verifies exact size/hash, parses 50 rows/page 1 of 37/1833
results, and confirms a single retained page cannot admit jobs. It skips when
original bytes are absent. No synthetic substitute is called retained evidence.

## Verification and independent review

The temporary `/private/tmp/acq02-tests.py` harness clears inherited application,
backend and PG configuration, selects test mode and redirects every application
write root to a new temporary directory. It uses an isolated test venv and adds
repository/activation/host paths. HTTP tests use synchronous offline transports.
`/private/tmp/acq02-native.py` creates a new PostgreSQL 16 cluster on a private Unix
socket, supplies an explicitly disposable test DSN, and stops only that cluster.
No production credentials, database, mounts or identity files are used.

Executed verification:

- PASS: `python3 -B /private/tmp/acq02-tests.py -m pytest -q -p no:cacheprovider ops/activation/test_financial_screen_refresh.py scrapper/tests/test_screener_discovery.py ops/activation/test_complete_screen_intake.py ops/activation/test_step3_financial.py ops/activation/test_paired_downloads.py scrapper/tests/test_ci_database_isolation.py`
  — 83 tests passed, 31 subtests passed, one original-source replay skipped.
- PASS: `python3 -B /private/tmp/acq02-native.py -m unittest scrapper.tests.test_postgresql_operations.NativeFinancialPriorityTests -v`
  — three tests, no skips; the new test includes 17 separately isolated schema
  cases, including 1,833-job admission and replay. The cluster was stopped.
- PASS: `python3 -B scripts/audit/check_migration_manifest.py` — canonical
  identities unchanged (77 canonical migrations plus the retained historical one).
- PASS: `python3 -B scripts/audit/sqlite_usage.py --index` — reviewed staged
  source only; unrelated working-tree changes are excluded. The independent
  reviewer checked refresh SQL/backend routing and lexical caller-reference
  changes before refreshing inventory fingerprints.
- PASS: `git diff --cached --check`.
- The initial native fixture setup required sandbox shared-memory escalation;
  temporary DSN formatting and missing pool dependency were corrected in the
  harness. A test-local missing datetime import was fixed before the passing run.
- NOT RUN: original retained HTML replay, production acquisition/canary/deployment,
  production database reconciliation and broad retained-data audits.

 The existing offline
CI already runs financial-refresh tests. The native contracts now also run the
37-page success/replay and failure matrix on fresh UUID schemas.

An independent reviewer identified four draft issues: widened byte/time defaults,
permit-wait midnight crossing, unretained redirect response bytes and uncertain
legacy redirect usage. All four were corrected and covered by regressions.
Re-review found no remaining material blocker; reviewer independently ran the
financial-refresh suite (38 passed, 1 retained-source skip) and diff whitespace
check. Reviewer did not claim independent native execution or production health.

## Guarded deployment/canary proposal — not executed

1. Require passing focused PR CI and recover/replay the original retained HTML
   before treating historical source verification as complete. Prepare a complete
   immutable release from the reviewed commit using `acquisition_release`, not a
   dirty-workspace overlay. Preserve the installed release, manifests, pointer,
   receipts, native target identity and stops.
2. Reconcile PostgreSQL target identity, complete source/image/operator manifests,
   ownership locks, processes/leases and drain state. Diagnose existing financial
   and processing stops independently; this patch does not authorize clearing them.
3. Obtain separate approval for the exact candidate, ownership recovery actions,
   UTC day/window, exact screen/query and a one-capture financial-screen allocation:
   max_pages 37, requests_per_capture 37, source_requests_per_day 37,
   requests_per_day 37. Reconcile broader Screener/day allocations first. Include
   the grant/evidence reference in a newly hashed selected plan. Do not install a
   perpetual grant or automatically extend its day. The normal plan generator
   does not issue grants; explicit reviewed plan preparation is required.
4. Use the existing guarded release activation path only after approved preflights.
   Run at most one complete screen capture within the existing eligible schedule
   and host permits. Retain 37 numbered immutable page responses, stable metadata,
   reserved request units, canonical admission events and the final complete flag.
   Success requires complete valid coverage and no duplicate canonical jobs.
   Any access block, metadata/query change, missing page, budget/day exhaustion or
   ownership ambiguity must stop/defer admission. A 37-page canary needs 37 available
   units; do not begin it on an already consumed or unmetered day.
5. Production idempotence can be checked by bounded native reads of canonical
   identity/event evidence, separately authorized. Do not spend another screen
   capture merely to demonstrate replay. No company/document downloading is part
   of this screen canary.
6. On failure, retain all source/native evidence and reservations. Pause/drain only
   through the existing guarded lifecycle and restore the previous complete
   release through its rollback path after the same ownership/drain preflights.
   Never delete rows, reset budgets, clear stops, switch backends or replay consumed
   slots as rollback. Deployment/recovery/rollback each require explicit approval.

Production systems/data were not touched. No publisher request, migration,
activation, service restart, stop clearing or downloader change was performed.
