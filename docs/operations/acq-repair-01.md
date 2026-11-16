# ACQ-REPAIR-01: results-screen repair and download evidence

16 November 2026. Source baseline: `7625ebb40` (current main including merged
PR #119), fast-forwarded into `fadhi`; unrelated local edits remain preserved.

## Delivered change and limits

The results-screen parser now accepts exact English `Mon-YY` alongside existing
numeric `YYYYMM` and zero-decimal forms. `Sep-26` means `2026-09`. Two-digit years
always mean `2000 + YY`, including `69 -> 2069`; no library pivot, locale or
current-date inference is used. Only standard title-case three-letter English
months and two ASCII year digits are accepted. Full month names, alternate case,
separators and nonzero numeric fractions remain rejected. Calendar validity is
unchanged; the financial refresh separately rejects periods newer than its
current reporting-quarter fence. Parser and configuration versions are `1.0.2`.

Original bytes, raw cells, company identity, reporting scope and completeness
checks are unchanged. No queue policy, cadence, budget, concurrency, migration or
runtime setting changed. There is no evidence-backed downloader patch in this PR.

The original retained screen was replayed **unchanged** into isolated fixture
storage. Before: parser rejection on `Sep-26`, refresh deferred, zero admissions.
After: 50 rows parse correctly, capture remains partial (1,833 results across 37
pages), refresh defers at the unchanged 32-page cap, zero admissions. This proves
source-format recovery, **not restored admission of that full retained screen**.
The original screen is private; it has not been committed or converted into an
invented complete capture. A synthetic, complete mixed-format screen proves two
canonical September standalone jobs are admitted once on SQLite and PostgreSQL.
A replay reuses those identities; future/malformed/duplicate/rejected pages do
not gain admission. The real pagination constraint needs a separate scoped
source-budget decision before claiming complete financial admission recovery.

## Authorized retained and current evidence

Authorized observations: 16 November 2026, 12:41–12:44 UTC. Native reader used the installed manifest-bound image/profile, reader credentials, network=none, read-only root and source mounts, REPEATABLE READ READ ONLY, 20-second statement timeout and 2-second lock timeout. Transaction read_only was on. No source HTTP, acquisition, migration, stop clearing or service restart occurred. Full rows, original HTML and canonical identity joins remain private under the mode-0700 temporary evidence directory.

The supplied date and “0/30” do not uniquely identify a batch: several Nov15 retained slots match. This investigation explicitly selects the latest matching retained slot, 23:08 IST, ending 17:40:45 UTC. Capacity30; selected30; completed claims30; exact native persisted attempts30; validated contents0. Worker outcomes: HTML13, network_error15, timeout2. The zero-success condition is real acquisition failure, not an all-pre-dispatch permit deferral. No administrative deferrals or storage exceptions were reported in that batch. FAILED_JOB is an outcome classification while process exit0 means the bounded worker completed.

Native evidence refines causes:
- TLS certificate verification failures (5): www.southindianbank.com, www.navkarcfs.com, www.moldtekpackaging.com, www.thephoenixmills.com, www.ltgroup.in.
- DNS resolution failures (10): www.magma.co.in, www.kvb.co.in, www.primeinfobase.in, cdn.stfc.in, varunpepsi.com, www.teamleasegroup.com, d1kxiaxkq0bgmn.cloudfront.net, hcgel.com, www.teamhgs.com, www.indiabullshomeloans.com.
- Requests timeout (2): recindia.nic.in, pfcindia.com.
- HTTP200 unexpected HTML (13): www.ltfs.com, www.oberoirealty.com, mahindralogistics.com, www.sunteckindia.com, www.tatapower.com, www.trivenigroup.com, www.mahanagargas.com, www.strides.com, dixoninfo.com, www.ashokleyland.com, www.gokaldasexports.com, www.raymond.in, www.welspunindia.com.

The canonical ID→host→attempt join is private. All30 native attempt classifications match the retained summary. Exact historical retryable/terminal disposition is UNKNOWN; current queue rows can have later attempts, so current statuses or aggregate deltas cannot establish that historical split. No policy-blocked outcome is recorded in this batch. HTML does not establish a valid landing-page resolver target, and DNS/TLS failures do not prove stale URL versus local transport configuration. No downloader correction is independently reproduced; TLS validation and policy remain unchanged.

Forty-one owner-specific host permit reservations were released in the batch interval. The installed host-permit schema has no dispatch-event ledger, and its permit API has no dispatch method. Exact outbound dispatch count is UNKNOWN; reservations are not dispatch receipts. Thirteen final HTTP200 responses and nine retained redirect responses prove at least22 received responses. Error paths can discard redirect chains; exact response count is UNKNOWN. Sixty daily request units were reserved, not measured as consumed HTTP requests.

Retained screen original: 63,033 bytes, SHA256 42ff2bf8bdaa9a120b3e7843c765bb4c0b5d8029b02e8699ade88bc154639b11, HTTP200 observed 14 November at14:45:21.557044 UTC. Local original: /private/tmp/acq-repair-01/screen-91766.html. Physical bytes match the retained blob hash. Source query and run configuration match after whitespace normalization. All50 captured result rows contain Sep-26. Metadata reports page1/37, total1833. The existing32-page bound still prevents the actual full screen refresh from completing; changing it is outside this repair. A bounded lookup found no alternate November14 v2 screen response. Parser repair alone must not be reported as restored complete production admission.

Active release pointer selects cli-consistent-20261115T160550Z. Full installed source/config/native-target attestation passed at12:43:42.205280 UTC. The native target was observed through the restricted reader; identifiers and credentials remain private. Retained batch image sha256:254001745bba3543daa6561508b8c00e1e4c9f6bd801d54fc646286f3f841795. Its four relevant worker/transport/persistence/HostSession files matched batch-manifest hashes.

Current local control-file observation at approximately12:43 UTC: financial retained STOP remains from14 November, reason ambiguous host production ownership; document-processing retained STOP remains from16 November04:12 UTC, reason invalid document-store runtime identity. No retained document-download, NSE, BSE or market-cap stop files found. Absence of stop files does not certify lane health. Both existing scheduler-admission and production-owner lock files were immediately available under nonblocking inspection; locks were released without mutation. The exact historical PID referenced by the financial stop was absent at the read-only ps check (exit1, no rows), but this does not authorize clearing the stop or establish complete ownership safety. No production rows or runtime controls were changed.

## Verification actually executed

The local `/private/tmp/acq-repair-tests.py` wrapper removes inherited application,
backend and PG configuration, selects test mode, redirects every application
write root beneath a temporary directory and uses the existing isolated test
venv. Its PYTHONPATH includes the repository, `ops/activation` and `ops/host`,
matching the operator CI test environment. No test makes publisher requests.

- Expected FAIL against the main-baseline parser injected in memory:
  `python3 -B /private/tmp/acq-repair-tests.py /private/tmp/acq-before-tests.py`.
  The new normalization/admission regressions reject the old implementation;
  `Sep-26` defers admission. No source file was reverted for this reproduction.
- PASS: `python3 -B /private/tmp/acq-repair-tests.py -m pytest -q scrapper/tests/test_screener_discovery.py ops/activation/test_financial_screen_refresh.py`
  — 37 tests, 28 subtests. Independently repeated by the separate reviewer.
- PASS: `python3 -B /private/tmp/acq-repair-tests.py /private/tmp/acq-repair-01/replay-screen.py`
  — original retained-page before/after comparison above; both isolated DBs
  retain zero admissions, for distinct documented reasons. Private replay output
  is `screen-comparison.json`; original HTML/hash and exact production joins stay
  in mode-0700 `/private/tmp/acq-repair-01`, outside Git.
- PASS: `python3 -B /private/tmp/acq-repair-tests.py -m pytest -q -p no:cacheprovider scrapper/tests/test_screener_discovery.py ops/activation/test_financial_screen_refresh.py ops/activation/test_complete_screen_intake.py ops/activation/test_paired_downloads.py scrapper/tests/test_ci_database_isolation.py`
  — 48 tests, 31 subtests. An initial run lacked `ops/host` in the temporary
  harness PYTHONPATH and failed one import; correcting the harness to match CI
  resolved it without changing application code.
- PASS: `python3 -B /private/tmp/issue106-native.py -m unittest scrapper.tests.test_postgresql_operations.NativeFinancialPriorityTests -v`
  — two tests, no skips, including mixed numeric/abbreviated native admission.
- PASS: `python3 -B /private/tmp/issue106-native.py scripts/audit/native_runtime_smoke.py`
  — 58 tests, no skips, including transfer/restore, native reader/queue/document
  contracts. The wrapper clears inherited application/PG settings, creates a new
  disposable PostgreSQL 16 cluster and isolated write roots, runs synthetic
  fixtures, then stops only that cluster. Initial sandbox shared-memory refusal
  was followed by an approved run; no production credentials or mounts were used.
- PASS: `python3 -B scripts/audit/check_migration_manifest.py` — unchanged
  canonical migration identities.
- PASS: `python3 -B scripts/audit/sqlite_usage.py --index` — 174 classified
  files, 755 sites after independent review of the CI fingerprint and lexical
  caller-reference updates. The initial gate correctly rejected stale review
  evidence. `git diff --cached --check` also passed. Staged-source selection
  preserves unrelated local edits; checked-out CI source uses the ordinary gate.
- Independent code review found no material blocker and specifically confirmed
  that the retained 37-page screen still defers. Reviewer did not claim native
  execution or production health. The financial-refresh regressions are added
  to the existing isolated GitHub offline workflow.

Full retained-data audits, deployment, live acquisition and throughput acceptance
are NOT RUN. CI results are attached to the focused PR, not inferred from local
passes. This source change requires no data migration or data rollback.

## Bounded deployment, canary and rollback proposal — not executed

1. Review/merge the focused PR and passing CI. Preserve the old installed release,
   its manifest, active pointer, LaunchAgent configuration, stops and all receipts.
   Prepare a complete immutable candidate through
   `acquisition_release.prepare_release` from the reviewed source snapshot, not
   the dirty workspace; preserve installed target/configuration and reconcile the
   complete application/image/operator manifest. Review the exact candidate diff
   and hashes before asking for deployment authorization. No package overlay or
   manifest-only reseal is an acceptable deployment.
2. Before activation, independently recheck native target identity, source/image
   hashes, both ownership locks, active processes/leases and document-store
   runtime identity. Diagnose financial ownership and processing identity stops
   separately. Do not clear either merely because a historical PID disappeared.
   Current lock availability is only a timestamped observation.
3. Obtain explicit approval for the exact candidate, recovery actions, selected
   source/job identities, request units/bytes, time window and success criteria.
   Use the existing rollback-capable `acquisition_release.activate_release` only
   after separate ownership, active-process/lease and drain preflights succeed.
   The activation function itself checks the manifest and two locks; it does
   not inspect process/lease ownership or drain workers. Do not replay consumed slots,
   broaden request budgets, activate polling or restart unrelated services.
4. The unchanged full-screen limit blocks this retained 37-page capture. Do not
   run a live screen canary expecting successful admission until that separate
   decision is resolved or a genuinely complete in-bound capture is observed.
   A financial canary should then use at most one eligible canonical company job
   in its next approved normal slot, with existing source permits and fences;
   retain source bytes, queue events and accepted financial receipts.
5. A separately approved document canary should select at most one eligible
   policy-permitted canonical job on a supported publisher, within the existing
   timeout/redirect/byte/request limits. Record permits, actual dispatch/hops,
   response/media validation, persisted attempt, content ID, saved byte count,
   SHA256 reread and downstream handoff. No new dispatch instrumentation is
   implemented here; if the installed ledger cannot measure a requested metric,
   report UNKNOWN rather than equating permits with dispatch. Do not retry the
   old 30 URLs as a diagnostic or disable TLS verification.
6. On failed preflight, ownership ambiguity, target mismatch or canary corruption,
   stop admissions via the existing guarded lifecycle; drain known owned work.
   Restore the preserved previous complete release/pointer/LaunchAgent using the
   canonical activation rollback path after the same separate ownership,
   active-process/lease and drain preflights. Preserve
   all newly acquired evidence and native rows; never revert to an old SQLite
   store, delete stops/data or reset budgets. Recovery and rollback require the
   explicit approvals above; neither is executed by this PR.

Production status: the owner authorized restricted read-only observation only.
Production rows, original retained bytes and metadata were read; no production
business data, stops, services, release pointers or runtime controls were changed.
The existing schedule may continue independently. Sustained throughput and live
fresh-result admission remain unverified acceptance stages.

## CI runner compatibility follow-up

The initial PR PostgreSQL jobs failed before tests at client installation:
`E: Unable to locate package postgresql-client-16`. The runner log identifies
Ubuntu 26.04 selected by `ubuntu-latest`. This PR pins only that native job to
`ubuntu-24.04`, whose [official runner inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)
includes PostgreSQL 16. The disposable 16.14 service, matching client-major
installation, isolation, credentials and required tests are unchanged. The
reviewed workflow source/scope fingerprints are refreshed. This corrects the
observed CI environment failure; it does not change PostgreSQL architecture or
production configuration. CI outcome is reported on PR #120.
