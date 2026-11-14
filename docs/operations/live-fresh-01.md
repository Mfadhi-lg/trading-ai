# LIVE-FRESH-01 implementation and acceptance

Baseline: `origin/main` merge `15b8d72e0`, fast-forwarded into reusable `fadhi`.
The working tree was clean at intake. This delivery supersedes the finite
SCRAPER-FRESH-01 behavior with maintained opt-in modules, without renewing any
existing live acquisition grant or changing deployment.

Implementation:

- [Focus stream and replay](../../scrapper/market_live/README.md): verified explicit
  20–50-company watchlists, V3 binary decoding, account ownership, reconnect and
  stale detection, separate developing/completed candles, bounded REST recovery,
  optional daily instrument overlay and live local chart.
- [Incremental daily refresh](../../scrapper/market_refresh/README.md): missing-date
  planning, universe coverage, shared quota, canonical append-only archive writes,
  source revisions, daily indicators/custom indices and current explorer pointer.
- [Financial/document acquisition](result-document-freshness.md): separately gated
  official polling, retained watermarks, early canonical expectations, finite
  near-term link checks, immediate download queue handoff and measured timings.
- [Supervision](../../ops/market_live/README.md): independent uninstalled Aqua
  template, stop/health/status commands, sleep/session recovery and background
  updates. Existing automatic-acquisition Aqua definition is unchanged.

Set `SCRAPER_MARKET_LIVE_CONFIG` to the explicit reviewed config path to include
receipt-only market status in the existing terminal monitor and technical status.
It opens no database or provider. Optional `document_freshness_report_path` selects
an explicitly generated snapshot timing report; absent evidence stays UNKNOWN.
The standalone market status distinguishes process health from data freshness,
reports subscriptions/message age/completed buckets and includes daily coverage.
Archive-writer conflicts are reported; host HCD progress remains UNKNOWN without
an explicitly authorized current inventory observation. No production inventory
was opened for this implementation.

Read-only runtime observations on 14 November 2026: launchctl showed the existing
bounded dispatcher loaded at 09:43:58 UTC; the selected Docker context had no
running retired Compose workers at 09:49:42 UTC. At 12:50:54 UTC, a sanitized host
process probe found zero Python processes matching the exact HCD acquire/campaign,
automatic-acquisition, new market-live and result-freshness entry points. These
observations do not certify other launchers/hosts, scheduler completion, deployed
revision, database health or document throughput. No backfill was restarted.

Verification commands (all write targets temporary; never production):

```sh
python3 -B scripts/verify-live-fresh
python3 -B scripts/market-live replay --output NEW_EMPTY_TEMP_DIRECTORY
python3 -B scripts/audit/check_migration_manifest.py
plutil -lint ops/market_live/com.trading-ai.market-live.plist.template
git diff --check
```

The verifier creates every application write root under TemporaryDirectory before
importing configuration. Install the repository's locked test dependencies in an
isolated environment. `--dependency-dir` can select an existing offline package
root, with matching Python ABI. Real canonical Parquet tests require PyArrow;
optional loopback WebSocket tests require the separate live dependency lock.
Absent optional dependencies are reported as skips, not certified integrations.

Independent review ran the combined market/indices/features/supervision/runtime
suite: 110 tests OK, one optional WebSocket skip. With the actual hash-locked
WebSocket library and approved loopback binding, all 17 streaming tests passed.
The canonical archive integration passed under Python 3.13 plus matching cached
PyArrow: real temporary SQLite/raw/Parquet acquisition, idempotence and preserved
receipt-gated source revisions. The final wider targeted verifier ran 176 tests successfully, with two optional
integrations skipped (Parquet under Python 3.14 and the uninstalled live library).
The independent matching-ABI Parquet and installed-library runs above exercise
those integrations separately. An attempted full local default
suite stalled and was interrupted; it is not reported as passed. Initial mixed
ABI dependency attempts failed; matching Python/package runs above supersede them.

The complete 20-instrument feed-to-chart replay passed. It validates 375 minutes
per instrument, duplicate/out-of-order snapshots, seven session-anchored hourly
bars, two four-hour bars, one daily bar and 37,500 volume each. An isolated headless
Chromium check displayed 20 instruments and the four-hour chart with no JavaScript
errors. HTTP routes were blocked. Synthetic identity, calendar and candle values
certify software behavior only.

Independent reviews found and fixed disconnect classification, silent-instrument
watchdogs, negative int64 volume, as-of leakage, false REST provider timestamps,
whole-tick ownership, stop/expiry handoffs, lost dependent callbacks, old-ISIN
coverage, absent-hour indicator bridging and per-frame identity reconstruction.
No reviewer approved their own implementation.

Activation acceptance remains separate: select the actual live watchlist; review
current official calendar/security identity and source policies; choose approved
storage, shared account quota and finite grants; verify an awake always-on host;
then explicitly authorize installation and an authenticated provider canary.
Measure sustained feed throughput, illiquid-stock silence, missed-period repair,
real filing/link delays and download throughput. The 15-minute detection target
has not been demonstrated live. No deployment, credential change, production
migration, database/store access or authenticated provider canary occurred.
Rollback reverts source/uninstalled templates; retain all newly acquired evidence
if a later approved activation creates it. Never delete or rewrite HCD receipts.
