# HCD-02 historical archive interface and operation

HCD-02 appends to the HCD-01 Passport archive and its existing SQLite/Parquet
index. It does not migrate the company database. HCD-01 evidence and technical
and Document Intelligence files have a separate preservation manifest under
`artifacts/historical-candle-archive-v1/hcd02/`.

Use Python with the existing dependencies (`PYTHONPATH=/tmp/hcd01-deps` in this
workspace). Every archive entry point verifies the external volume. Disk identity
checks and external writes require the host's sandbox permission. A missing or
replaced Passport has no internal-storage fallback.

```python
from experiments.historical_candle_archive_v1.access import HistoricalStore, indicator_segments

store = HistoricalStore()
try:
    history = store.read(
        555, "4-hour", start="2026-10-26", end="2026-10-30",
        mode="retrospective", definition="owner",
        decision_time="2026-10-31T00:00:00+05:30",
        revision_asof="2026-11-06T00:00:00+05:30",
    )
    candles = indicator_segments(history)
finally:
    store.close()
```

Supported timeframes are `Weekly`, `Daily`, `4-hour`, and `1-hour`. Owner 4-hour
bars end at 13:15 and 15:30 on ordinary sessions. Hourly bars start at 09:15,
10:15, 11:15, 12:15, 13:15, 14:15 and 15:15; the last lasts 15 minutes. Actual
special-session segments replace ordinary hours. Split sessions are aggregated
from minute evidence and never bridge the closed break. If constituents are
missing or invalid, the session is excluded. BSE owner bars remain excluded
until its own session calendar is verified; native BSE candles are accessible
with `definition="native"` and explicit quality flags.

The result includes clean `rows`, `exclusions`, original provider values and
timestamps, source response hashes, receipt revisions, quality flags and
`available_adjusted_versions`. No certified local adjusted series currently
exists, so this last field is empty and requesting one raises an error. Provider
values are preserved without local adjustment; that does not certify that the
provider itself supplied exchange-unadjusted prices.

Exact-value daily timestamp aliases collapse only if all OHLCV and open-interest
values agree. All originals remain in raw storage and the access result. Different
daily values stay quarantined. Bad daily constituents also exclude the containing
weekly native bucket. Intraday owner construction rejects invalid constituents.
`indicator_segments` refuses an unbounded window or one with known exclusions,
including entire missing post-2022 NSE sessions. Calendar completeness, adjustment
flags, listing continuity and missing pre-2022 sessions still require review;
passing the guard is not a claim that the source history is exhaustive.

Retrospective mode selects a provider revision using `revision_asof` (latest if
omitted), then limits interval completion by `decision_time` (now if omitted).
It permits retrospective experiments under those explicit revision assumptions.
It never rewrites `observed_at` to the old candle time. Set
`mode="strict_delivery"` and supply `decision_time` to require that the actual
receipt precede the historical decision. Fresh backfills cannot pass an old
delivery cutoff. Reconstructed intraday prefixes are not historical live
snapshots. Owner completed bars do not include provisional reconstructions.

Acquisition commands reuse the retained request IDs and immutable raw receipts:

```sh
python3 -m experiments.historical_candle_archive_v1.rollout defect
python3 -m experiments.historical_candle_archive_v1.rollout rollout
python3 -m experiments.historical_candle_archive_v1.rollout special
python3 -m experiments.historical_candle_archive_v1.rollout operation
python3 -m experiments.historical_candle_archive_v1.rollout review
```

`rollout` is the owner-authorized 100-company scope. Successful requests verify
their hashes and skip the GET. A durable raw receipt recovers normalization without
another GET. A single writer owns the inventory. Isolated HTTP 400/404/422 responses
are retained and the stock is held while other stocks continue. Authentication,
quota, server, transport, storage and serious integrity failures stop the run.
The runner caps transport recovery, serial pacing, and a persisted rolling quota;
its quota budget must not be shared with an uncoordinated acquisition process.
An inspected SPICEJET response exposed one negative integer volume. HCD-02 can
retain a finite negative source number exactly in a `QUARANTINED_NUMERIC` row;
all clean selectors reject it. HCD-01's default parser rejection is unchanged.
Nonfinite values, unexpected types/shapes and fractional volume quarantine the
response and hold that stock; other stocks continue under the owner's later
instruction. Exact all-zero placeholders remain quarantined. Authentication,
storage and archive checksum failures still halt writes. The original integrity
stops and their evidence reviews remain recorded.

After scaled validation passes and the owner explicitly authorizes the exact
83,688-request manifest, `full --authorize-manifest-sha256 DIGEST` processes the
remaining requests in automatic resumable batches. The digest must match the
unchanged HCD-01 full manifest and the validation must pass. The current run does
not infer that approval from authorization of the initial rollout.

The separate session-repair supplement contains one-day minute requests for
March 2 and May 18, 2024. `special` covers the initial 90 NSE companies (40 requests
are reused from pilot defect evidence). The full supplement covers the retained
NSE identities and has a separate digest. With explicit owner authorization of
both scopes, add `--authorize-special-manifest-sha256 DIGEST` to the `full` command;
the minute supplement then runs automatically after the native manifest, with
the same resumable batch behavior. BSE calendar evidence is a separate unresolved
requirement. Neither phase enables schedules.

No cron, launchd or Automation is installed. The disabled checkpoint specification
contains 09:15, 09:30, 13:20, 14:00, approximately 16:00 IST, and completed weekly
reconciliation. Queue start times do not imply whole-universe completion at those
times. `scaled-validation.json` reports whether incremental operation may be
enabled; unresolved calendar and adjustment issues keep it disabled.
