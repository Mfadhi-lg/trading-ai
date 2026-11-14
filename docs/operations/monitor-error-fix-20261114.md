# Monitor error investigation, 14 November 2026

Result: source correction verified; owner-approved production recovery completed
on 14 November. Initial investigation and deployment observations are separate below.

## Evidence and changes

- The retained financial job `2026-11-11-current-financial-2255/runtime.log`
  stops after preflight with PostgreSQL SQLSTATE 42883, before source requests.
  `financial_screen_refresh.refresh_if_due` still used SQLite `json_extract`
  and `json_set`. It now selects native PostgreSQL JSON lookup and preserves
  existing configuration through a portable JSON update. The public queue,
  refresh intervals and source-request limits are unchanged.
- The monitor previously searched only `execution_failure` records when a
  retained stop lacked error text. `ReceiptFeed.sample` now also reads `stop`
  records. It does not rewrite receipts or clear stops.
- The screen-refresh fixture used a fixed November 4 input clock but saved the
  actual run clock. Its November 5 retry assertion failed when run later. The
  fixture now pins the repository clock to the supplied test time.
- The active release pointer selected `postgresql-bse-config-20261111` when
  inspected. Its document worker and timeout cleanup differ from the checkout,
  which already includes PostgreSQL campaign-directory cursor storage and exact,
  case-insensitive Docker absence handling. Source fixes alone do not deploy.
- The download stop names PID 20259. A read-only process query returned no row;
  its historical role remains unknown. Ownership checks were not weakened.
- CHECKING and absent queue counts remain unknown observations, not evidence
  of successful runtime health.

## Verification

The temporary `/tmp/run-monitor-tests.py` runner sets `TRADING_AI_ENV=test`,
redirects application write paths into a temporary directory, unsets the document
store override, and invokes pytest. Dependencies were installed only under
`/tmp/monitor-error-test-deps`; production runtime dependencies were not changed.

PASS: 77 tests, 5 skipped native tests, 2 deselected legacy release-hash tests,
8 subtests:

```sh
env PYTHONPATH=/tmp/monitor-error-test-deps:artifacts/postgresql-host-runtime-20261111/lib/python3.14/site-packages python3 -B /tmp/run-monitor-tests.py --pytest -q ops/activation/test_financial_screen_refresh.py ops/host/test_acquisition_terminal.py ops/activation/test_stage_recovery.py ops/activation/test_production_ownership.py scrapper/tests/test_period_recovery_sweep.py scrapper/tests/test_postgresql_operations.py -k 'not financial_preserves_image and not financial_refuses_unsealed'
```

PASS: both native financial tests against a newly initialized temporary cluster
with TCP disabled and a private Unix socket. Fixtures use disposable UUID schemas:

```sh
env TRADING_AI_POSTGRESQL_TEST_DSN='postgresql://monitor_test@/postgres?host=/tmp/monitor-errors-pg-20261114&port=55439' PYTHONPATH=/tmp/monitor-error-test-deps:artifacts/postgresql-host-runtime-20261111/lib/python3.14/site-packages python3 -B /tmp/run-monitor-tests.py --pytest -q scrapper/tests/test_postgresql_operations.py -k NativeFinancialPriorityTests
git diff --check
```

Initial checks failed because pytest/pinned historical Python were unavailable;
native setup also needed temporary driver dependencies and sandbox permission
for the local test socket. These were resolved before the passing runs above.
The original broader run exposed two existing image-seal tests that fail with
`Financial application differs from existing verified image:
scrapper/db/database.py`. That file was unchanged by this task. These checks
remain failing, not certified by the filtered pass.

An independent read-only reviewer found no material defect in the source diff.
The native financial regression subsequently passed. No schema migration is
required by this patch. Reverting the source diff rolls back this code change.

## Initial production boundary, before owner approval

Production databases, workers, release pointer and retained stops were not
modified. A deployment must seal and verify the release against its selected
image, include the existing cursor/timeout fixes, and use the existing guarded
activation workflow. Diagnose current ownership and verify each stage before
archiving any stop; do not delete all stops or replay consumed slots. Explicit
production authorization is required by repository AGENTS.md. Full live health
and a fresh queue census remain unverified.

## Owner-approved deployment follow-up

The owner subsequently explicitly approved deployment and guarded recovery.
The prepared release is `monitor-approved-20261114T141317Z`; the previous release
remains retained for rollback. It uses image
`sha256:254001745bba3543daa6561508b8c00e1e4c9f6bd801d54fc646286f3f841795`.
All 517 application file hashes were verified against that image in an isolated
networkless container. The release includes the nine previously reviewed repair
files plus the two source corrections described above; source inventories and
the manifest were resealed. No unrelated workspace changes were deployed.

Independent operational review found a partial stop-archival failure case in
the recovery script. It was corrected to restore moved stops while retaining both
ownership locks; the reviewer confirmed the correction before activation.

PASS: activation and bounded production verification:

```sh
env PYTHONPATH=artifacts/postgresql-host-runtime-20261111/lib/python3.14/site-packages python3 -B /tmp/activate-approved-monitor-release.py
env PYTHONPATH=artifacts/postgresql-host-runtime-20261111/lib/python3.14/site-packages python3 -B /tmp/check-approved-monitor-release.py
```

Initial image checks with a new single-file bind timed out before activation.
The image started without mounts, and a separate read-only release-directory
mount subsequently passed. Hash verification used stdin, retaining isolation.
No Docker Desktop or PostgreSQL restart was required.

Activation completed at 14:38:21 UTC. Both ownership gates passed. A fresh quarter
monitor query completed in 1.539 seconds; native financial selection and the exact
screen-refresh JSON predicate passed using reader credentials. The cursor path
was verified as `/data/campaigns/document-period-recovery.json`.

The four exact stop files were archived with originals retained under
`artifacts/automatic-acquisition/approved-monitor-recovery-20261114T143818Z/`.
No consumed slots were replayed, no budgets broadened, and no ownership guard
weakened. The existing dispatcher schedule resumed. No migrations or new services
were installed. Production was accessed and its release/admission state changed;
these were explicitly authorized. Verification probes used read-only database
credentials; subsequent scheduled acquisition may persist its normal work.

At 14:41:52 UTC, post-deployment monitoring observed a loaded dispatcher, a fresh
heartbeat, a successful live database census, no retained blockers and no
inspection errors. The next scheduled slot was document processing at 20:13 IST.
Machine receipts in that recovery directory preserve timestamps and checksums.

The next scheduled document-processing slot (`2026-11-14-document-processing-2013`)
completed with PASS, exit 0, at 14:44:13 UTC. It recorded 10 processing jobs
(nine `ok`, one `partial`) and checked 33 documents in period recovery without
the former read-only cursor error.

The financial slot (`2026-11-14-current-financial-2015`) completed with PASS,
exit 0, at 14:47:49 UTC: five accepted jobs, one already accepted, and two
`MarketCapBelowFloor` terminal outcomes under the unchanged 580 INR crore policy.
No retained monitor blockers reappeared at that observation. The financial slot
also ran its existing paired download/processing path.

Concrete follow-up: the results-screen refresh safely deferred admission after
one source request because its parser rejected `Sep-26` (`Invalid Last result
date`). This is a distinct source-format issue; it did not stop the financial
lane or authorize guessed periods. This deployment does not claim that new
screen admissions passed. Existing queued financial work did pass.

The scheduled discovery slot (`2026-11-14-document-discovery-2018`) also
completed with PASS, exit 0, at 14:49:40 UTC. The final monitor inspection at
14:50:06 UTC again found no retained blockers or inspection errors and a
successful fresh database census. Runtime verification was refreshed at
`artifacts/runtime-verification/latest.json`, preserving its previous report
when present. This report selected dispatcher/worker metadata; it does not
claim an independent database schema audit.

PASS: final evidence capture:

```sh
env PYTHONPATH=artifacts/postgresql-host-runtime-20261111/lib/python3.14/site-packages python3 -B /tmp/finalize-approved-monitor-recovery.py
git diff --check
```

An already-running terminal retains its loaded Python code; reopen
`./scraper-monitor` to load the new monitor implementation.
