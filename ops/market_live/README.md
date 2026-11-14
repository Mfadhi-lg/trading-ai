# Independent market host supervision

This service is prepared source, **not installed or activated**. The existing
`com.trading-ai.automatic-acquisition` Aqua LaunchAgent is unchanged. New live
streams, unattended HTTP acquisition and installation require explicit owner
approval of their concrete configuration and dates.

`scripts/market-live` is a Python entry point; invoke with `python3 -B` when the
checkout has no executable bit. `status` and `health` inspect only explicit local
receipts and lock ownership. They do not load credentials, initialize a database,
contact providers, or discover a production store.

```sh
python3 -B scripts/market-live status --config /explicit/service.json
python3 -B scripts/market-live health --config /explicit/service.json
python3 -B scripts/market-live launchd-template --config /explicit/service.json \
  --output /explicit/review/com.trading-ai.market-live.plist
```

Copy `config.example.json` to an explicit review location and select absolute
paths for state, the separately versioned 20–50-company live watchlist, verified
instrument identities, and an owner-reviewed NSE calendar. Record the exact
calendar bytes in `calendar_sha256` and the identity snapshot in
`identity_sha256`. These snapshots cannot change under an existing approval.
Calendar `days` may be a list of records or
a mapping from ISO dates to records. Each date needs `verified: true`, a `day`,
and `sessions` containing aware `opens_at`/`closes_at` timestamps; closed days
have an empty session list. Missing, unresolved or unreviewed dates block
streaming. Reuse the existing NSE calendar projection and review its official
source references and exceptional sessions before marking a date verified.
The existing calendar documents incomplete exceptional closure review and does
not certify later years automatically.

An approved configuration must have `owner_approved: true`, an `activation_ref`,
and an explicit inclusive `active_from` / exclusive `active_until` interval.
`--authorize` must equal SHA256 of the exact service config bytes. Changes or
expiry stop stream admission while the existing process remains supervised.
No credential value belongs in JSON or the plist; the stream obtains the token
through the existing credential supplier. After owner approval only:

```sh
python3 -B scripts/market-live start --config /explicit/service.json --authorize APPROVED_SHA256
python3 -B scripts/market-live stop --config /explicit/service.json
```

`start` runs in the foreground, or resumes an already running service before
returning `BUSY`. `stop` writes a graceful stop request, never sends a signal to
a PID. A launchd restart preserves that request; only explicit `start` clears
it. The supervisor stays idle while stopped, so `KeepAlive` cannot defeat the
operator's stop. The template's `supervise` action never clears a retained stop.
Template generation writes only the selected review file and does not call
`launchctl`, copy to `~/Library/LaunchAgents`, or create system state.

One independent `.service.lock` covers the supervisor lifetime. `.refresh-admission.lock`
covers each incremental batch; neither is acquisition's owner lock. The stream
runs in a cooperative thread, with a watchdog every five seconds or less. A
close, unresolved session, expiry, stop, or wall-clock gap requests its shutdown.
The stream must honor its stop event within `stop_timeout_seconds` (10 by
default). A detected sleep/disconnection gap restarts it with `recovery_after`;
only provider reconciliation can repair missing candles. An exhausted stream
retry budget stays blocked, avoiding a fresh unbounded retry loop each poll.

To enable background incremental work in the approved config, add
`refresh_config_path` and `refresh_config_sha256`. The child config has its own
bounded grant and exact hash check in `scrapper.market_refresh.service.refresh`.
Both configurations must select the same absolute `provider_quota_path` for
the account's shared REST ledger; differing paths block service admission.
The supervisor calls it independently at `refresh_interval_seconds` (300 by
default). An owner can instead call the bounded entry point explicitly:

```sh
python3 -B scripts/market-live refresh-tick --config /explicit/service.json --authorize APPROVED_SHA256
```

Background failures write exception types rather than raw URLs/credentials.
Each additional provider request rechecks both the parent and child grants and
the parent's stop request. An HTTP request already in flight may finish after a
stop or grant expiry; no subsequent request is admitted. Batches drain without
forcefully killing their threads or discarding their retained response bytes.
`supervisor.json`, `stream_status.json`, `refresh_status.json` and the stream's
`chart.json` remain separate receipts. Supervisor health is process health;
market freshness must come from the stream/coverage reports and the most recent
expected completed session, not the service heartbeat alone.

launchd does not keep a sleeping Mac collecting. Continuous real-time coverage
requires an awake, connected host with the applicable logged-in Aqua session,
or a separately approved always-on deployment. Recovery records gaps; it does
not invent ticks or certify missed candles.

Offline validation:

```sh
python3 -B -m unittest scrapper.tests.test_market_live_supervision -v
plutil -lint ops/market_live/com.trading-ai.market-live.plist.template
python3 -B scripts/market-live replay --output /explicit/isolated/replay
```

Read-only runtime evidence taken for LIVE-FRESH-01 on 14 November 2026:
`scripts/runtime-status --dispatcher --workers` observed the bounded dispatcher
loaded at **09:43:58 UTC**. Docker access was initially unavailable under the
sandbox; an explicitly approved read-only `--workers` probe at **09:49:42 UTC**
found no running retired Compose roles and no unclassified trading-ai roles in
the selected Docker context. These probes do not certify scheduler completion,
host backfill state, throughput, deployed revision, database health, or other
Docker contexts. No production database or document store was opened.
