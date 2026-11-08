Current operation: [24 October repair](../../artifacts/acquisition-repair-20261024/REPORT.md). Financial scraping accepted JINDALSAW and URBANCO with the updated session; financial claim loss now defers a consumed slot instead of permanently stopping the lane. BSE resumed after retained-input validation and ingestion, with explicit non-quarterly SN/DN exclusions. Bounded document stages run until 25 October 18:43 IST; a new rating PDF was saved and its bytes/hash verified. Generic presentation/transcript verification was idle. No stop flags remain. Older disabled/prepared statements below are retained dated history.

Historical authenticated Step 3 retry: **GMBREW and TCS accepted for September 2026**. [Actual retry outcome](STEP3_RETRY_OUTCOME.md).

# Automatic acquisition installed — 9 October 2026

NSE runs Mon–Sat at 19:30 IST. BSE visible Chrome runs daily at 09:00 and 18:00 IST under confirmed written-consent scope. The acquisition-only Aqua LaunchAgent is installed and verified; see [operational details](AUTOMATIC_ACQUISITION.md). No document, extraction, rating or historical backlog workers are enabled.

Step 3 financial processing is [prepared and verified](STEP3_FINANCIAL_READINESS.md), with a two-company current-quarter canary and separate cap-only check. Neither operation has been executed. First real unattended exchange attempts are monitored from existing logs and receipts by the acquisition tick.

The following is the retained initial one-shot preparation reference; its historical disabled/unproven status is superseded by the current schedule and verification above.

# Selective NSE activation

Only `nse-results` is prepared. The replacement immutable image contains the shared date-driven application changes on the accepted combined AR-02 baseline. [Replacement manifest](../docker/release/ar02-period-20261008/release-manifest.json). The original package and acceptance evidence are retained unchanged. The standalone profile has one service and no default services; scheduling remains disabled.

Offline check, from the repository root:

```sh
python3 ops/activation/nse_results.py
```

Prepared production activation command, for a later selected bounded NSE operation:

```sh
python3 ops/activation/nse_results.py --execute
```

This command is prepared, **not executed**. It requires the replacement image to be loaded and the existing `trading-ai_scraper-data` volume. It will not pull, build, initialize a database, migrate or install physical sentinels. All other 21 candidate roles retain `authorized:false`. Only this service mounts the prepared `authorized:true` core identity and the accepted read-only release sentinel configs; underlying marker bytes are separately checked against completed AR-01.3 evidence before writes. The external document drive is not mounted and no document-acquisition role is enabled.

The runner verifies the retained baseline ACCEPT seal, replacement configuration/source checksums, image/source labels, volume identity and relevant Docker/host/scheduled owners. Unrelated Docker services are allowed. Inside the container, it verifies DB file identity, absent unexpected sidecars, exact schema/ledger and RELEASED control receipt, runtime targets and physical markers. Existing scheduler/executor OS locks prevent a second exchange owner. SQLite schema/target guards and queue claim fencing remain intact.

Automatic query priorities use the current Asia/Kolkata date: latest completed quarter followed by the preceding quarter for late publications/revisions. The scheduler uses the same helper. Optional `--period YYYY-MM` selects a single explicit quarter for testing/recovery. Query periods do not filter NSE acceptance: actual source period/broadcast metadata drives validation and persistence, including earlier-quarter filings.

At most two official NSE requests, no redirects or retries, 30-second request timeout; response ingestion capped at a total of 10 MiB, 6,000 accepted filing rows and 100 new queue entries across all selected periods. A 300-second runtime deadline stops the adapter. The existing source-access policy, parser and snapshot guards validate the response before business writes; the exact same response is reused for ingestion without another request. No guard override is exposed. The runner processes no scheduled tasks or company backlog. NSE identity conflicts remain manual-review outcomes and stop automatic continuation.

The host records before-state, runtime output and outcome under `artifacts/acquisition-readiness/operations/nse-*`. BSE access-event history is checked for unexpected changes and queue deltas must match the bounded result. A later-period failure may follow a committed earlier snapshot. A nonzero exit means stop: preserve logs and partial state, inspect what committed, and resolve the concrete failure before retrying. Do not delete WAL/SHM, restore old068 state, clear holds, retry automatically or broaden a budget to force success. Additive acquisition does not authorize destructive recovery; destructive work needs current backup/recoverability and preserves accepted writes.

Select role → automatic checks → bounded operation → recorded outcome → continue or stop. Once an operation's scope is approved, its internal checks and steps require no new administrative approvals. This preparation gives no production execution authorization. Live result acceptance, scheduler continuity and operator alert delivery remain unproven.

The legacy `compose.yml` and sealed candidate are historical/other-role entrypoints. Use the runner above for this scope; do not use a broad `docker compose up`. Configuration rollback removes only this activation profile/identity; it changes no production state. After actual business writes, retain schema075 and accepted rows and use forward recovery rather than an old database restore.
