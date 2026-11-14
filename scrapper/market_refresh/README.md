# Incremental market refresh

`planner.plan_refresh` builds finite Upstox V3 daily requests from verified
current NSE identities and explicit reviewed calendar records. It prioritizes
focus companies, custom-index constituents, official indices, then other eligible
companies. It requests only missing dates in a bounded recent window (10 calendar
days by default), combines consecutive dates up to 28 days and defers requests
beyond the batch budget. It does not restart HCD-02 or infer any live watchlist.

Older gaps require a caller-supplied finite `historical_gaps` mapping and
`history_idle=true`; they are admitted only when no recent requests remain.
Explicit source revisions require `revision_dates` and a new `revision_id` in the
library planner. Existing raw bodies and receipt identities remain retained.

`runner.execute_plan` obtains the existing canonical archive writer lock before
each provider request, releasing it after persistence. A busy HCD-02 writer
defers all new requests before credentials are read. There is no lifetime
acquisition lock. Requests are serial with at least 1.05 seconds between new
calls and a durable 1/second, 450/minute, 1800/30-minute quota ledger. This reserves
headroom under Upstox's documented [standard API limits](https://upstox.com/developer/api-documentation/rate-limiting/)
of 50/second, 500/minute and 2000/30 minutes (checked 14 November 2026). The shared
archive owner prevents overlap with existing HCD acquisition. Other independent
account consumers must be inventoried before activation; their calls cannot be
observed from this local ledger.

Live reconciliation and daily refresh must share the exact `provider_quota_path`
for their provider account. The gate uses a separate short file lock, not a
WebSocket lifetime lock; overlapping reservations and a clock rollback defer.

Successful raw bytes are retained and checksummed before normalization. Saved
raw responses recover without another GET; successful semantic request receipts
deduplicate changed plans on the same acquisition day. A transport interruption
is uncertain delivery and never repeats automatically. HTTP 401/403/429 block the
provider lane. Error bodies and exception text are not saved. Empty or quarantined
responses do not certify coverage; an unchanged empty request is not repeated on
the same day. The next acquisition day can recheck still-missing dates. A
reviewed source-revision identity permits an explicit corrective acquisition.
HTTP/auth/rate-limit blocks are intentionally retained across ticks. Recovery
requires an owner-reviewed configuration/receipt disposition after inspecting the
cause; simply restarting a worker does not clear them or retry uncertain delivery.
There is no command that edits or clears retained blocked receipts. For a
confirmed HTTP rejection after the cause is fixed, the supported fresh-scope
recovery is an explicitly reviewed new configuration selecting a new empty
`state_dir`, preserving the previous directory and selecting the **same** shared
`provider_quota_path` and canonical archive. The new configuration needs a new
digest/activation reference and must retain the prior failure evidence in the
owner's approval record. Canonical stock coverage prevents redownloading accepted
dates. Preserve previous official-index state roots for source-history exports.
Uncertain transport delivery has no automatic or generic reset path: reconcile
that evidence and authorize any new bounded request explicitly. Credential
replacement and source-access authorization are separate operator actions.

Stock rows use the existing `Archive` schema, parser, raw storage and Parquet
contracts under a `LIVE_FRESH_` request namespace. HCD-02 receipts are not changed.
Official-index raw receipts are separately retained and consumed through the
existing official index loader's explicit `incremental_state` argument.

`derived.VerifiedStore` adds reviewed session completion to canonical daily raw
reads without rewriting historical normalization or widening its default
calendar review boundary. `refresh_outputs` invokes maintained Decimal indicator
and custom-index engines with their full requested warmup window, stores each
source revision in a new content-addressed build and atomically updates
`latest.json`. Missing observations block indicators; partial index chains and
corporate-action/retrospective-membership warnings remain explicit. Daily
indicator values stay null until warmup is sufficient.

The existing explorer can consume the new official receipts and reviewed daily
calendar in a new immutable export:

```sh
python3 -B -m scrapper.indices.refresh NEW_BUILD --end YYYY-MM-DD \
  --incremental-state APPROVED_REFRESH_STATE --reviewed-calendar REVIEWED_CALENDAR.json
```

The underlying intraday frames still require complete canonical native hourly
or minute history and keep their existing session/calendar/exclusion contracts.
This command does not fetch providers or silently replace a previous export.

## Supervised entry point

`service.refresh(config_path, now, authorized_sha256=...)` is the finite callback
used by the independent market supervisor. It verifies the exact configuration
SHA256, `owner_approved=true`, `activation_ref`, unexpired `expires_at`, and the
calendar's bound SHA256 before opening a store. It does not install a timer or
create authorization. The secure supplier is the existing Upstox supplier and is
read only when a bounded request is actually admitted. No activation was run.

The configuration contains:

```json
{
  "owner_approved": false,
  "activation_ref": null,
  "expires_at": "2026-11-20T18:00:00+05:30",
  "calendar_path": "/explicit/reviewed-calendar.json",
  "calendar_sha256": "REVIEWED_FILE_SHA256",
  "state_dir": "/explicit/approved-refresh-state",
  "provider_quota_path": "/explicit/approved-market-state/provider-quota.json",
  "max_requests": 100,
  "lookback_days": 10,
  "companies": [{
    "company_id": 1,
    "symbol": "SYNTHETIC_EXAMPLE",
    "instrument_key": "NSE_EQ|VERIFIED_ISIN",
    "identity_verified": true,
    "identity_ref": "REVIEWED_CURRENT_MASTER_SHA256",
    "eligible_since": "2026-11-01",
    "focus": false,
    "index_constituent": false,
    "kind": "STOCK"
  }]
}
```

This is an inert example, not a verified instrument list. Official NSE indices
use `kind=NSE_INDEX` and their exact current `NSE_INDEX|...` keys. Optional
`derived_output`, `derived_start`, `groups` (code and fixed symbol members),
`benchmark`, and `actions` enable full-window derived recomputation. The calendar
contains `days` as an array or date-keyed object. Each usable record needs
`day`, `verified=true`, `status`, `source_refs`, and `sessions` with aware
`opens_at`/`closes_at`. Closed days require `status=CLOSED`; unknown dates never
become closed weekdays or fresh data. Its complete warmup window must be reviewed
when requesting current indicator/index output.

`state_dir/status.json` reports per-company FRESH, STALE, GAP, BLOCKED or UNKNOWN,
latest validated recent daily candle, expected completed trading session,
missing/excluded/unresolved dates, attempts, outcomes and raw-response evidence.
The coverage window is explicit; a recent refresh does not certify full historical
coverage. The live stream owns LIVE status and intraday coverage separately.

## Offline verification

```sh
python3 -B -m unittest scrapper.tests.test_market_refresh \
  scrapper.tests.test_indices_official scrapper.tests.test_technical_features \
  scrapper.tests.test_indices_production -q
```

Actual temporary canonical SQLite/raw/Parquet integration runs when compatible
PyArrow is available; its mount discovery and device checks are replaced only
inside that isolated test. Tests cover complete incremental refresh-to-coverage,
repeat idempotence, interrupted raw recovery, uncertain delivery, quota blocking,
concurrent historical ownership, duplicate workers, invalid OHLC, source revisions,
weekend/holiday freshness, warmup and incomplete custom indices. They make no
provider requests or production data access. Production host and provider canary
acceptance remain separately authorized stages. No schema migration is required.

For automatic explorer publication, an approved child configuration may include
absolute `index_export_output`, `index_start` (or `derived_start`), optional
`index_intraday_start` and `index_action_review`. The daily window is bounded to
732 calendar days. A changed canonical/official source revision produces a sealed
immutable build; unchanged input reuses it. The output `index.html` observes the
atomic latest-build pointer every 30 seconds, including from local files.
Failed attempts are retained and limited to three per source scope. Native
intraday/archive coverage requirements and partial constituent warnings are not
relaxed by publication.
