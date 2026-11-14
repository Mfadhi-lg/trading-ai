# Maintained index system

The index arithmetic is owned by `scrapper.indices.core`. The V1 experiment
imports that implementation through a compatibility shim; research inputs and
results remain unchanged. No acquisition or production schedule is activated.

## Open the working explorer

The verified local build is:

`artifacts/index-prod-01/build-20261113-v4/charts/index.html`

Open that file directly in a browser. Select a company, granular subsector,
timeframe, full index or leave-one-out peers, broad benchmark and optional NSE
sector comparison. The official-index view exposes all 139 catalog identities,
including four explicitly unavailable histories. Price/EMA, relative strength,
breadth and traded-value panels use the same dates; zooming is synchronized and
hovering updates dated inspection. The pinned existing Lightweight Charts asset
and license are copied locally, so no server, credential or remote request is
needed to inspect this exported build.

The build contains **41 usable custom series**: **5 complete provisional** and
**36 partial** for 1 December 2025–6 November 2026. These are 38 SS04 sets plus
three separately reviewed V2 core sets, representing 40 distinct granular codes
(the strict Transformers and reviewed Transformers core sets differ). All 347
SS04 codes plus the three reviewed sets have explicit coverage dispositions:
309 are unavailable. Candidate classifications are never counted as built indices.

There are **135 official NSE histories** from the existing 139 catalog keys.
BHARATBOND APR30–APR33 remain unavailable. Actual keys, index-point units,
provider timestamps, source hashes, gaps, last observations and age are retained.
Unknown original receipt timestamps stay unknown; retrospective completion is
not evidence of historical delivery or index launch/publication.

All 41 custom sets have 48 completed weekly observations through 30 October.
The final requested calendar week is not a complete Monday–Sunday input window.
Each intraday timeframe has 5 complete provisional and 36 partial series for
17 August–6 November 2026, using completed 09:15 session anchors (4-hour starts
09:15/13:15). The three reviewed core sets have 230/232 valid daily levels and
end on 4 November because retained constituent tails are missing. Missing members
are listed; later prices never silently restart a broken chain.

## Refresh using existing data only

From the repository root, with the correct Passport mounted:

```sh
python3 -B -m scrapper.indices.refresh artifacts/index-prod-01/NEW_BUILD --start 2025-12-01 --end 2026-11-06 --intraday-start 2026-08-17
```

Use a new directory. `refresh` reads the frozen SS04 research DB and canonical
read-only HistoricalStore, validates retained official receipt hashes, builds
all supported groups, writes datasets and charts, then writes `SHA256.json`.
It does not request new data, open the production application database, modify
Passport, install schedules or deploy anything. Supply `--action-review FILE`
for additional frozen action evidence; the JSON may contain an `events` array
using the existing symbol/ex_date/kind/provider_basis/share-multiplier contract.
Conflicting action records fail closed. No supplied evidence is treated as a
complete action feed merely because it exists.

For separate reviewable stages:

```sh
python3 -B -m scrapper.indices.production OUTPUT_DIRECTORY
python3 -B -m scrapper.indices.frames_build OUTPUT_DIRECTORY
python3 -B -m scrapper.indices.chart_export OUTPUT_DIRECTORY
python3 -B -m scrapper.indices.verify OUTPUT_DIRECTORY
node scripts/verify-index-charts.mjs OUTPUT_DIRECTORY
```

`production --reuse-candles PREVIOUS_BUILD` can reuse a preserved local candle
snapshot. Its input hashes are recorded, identities revalidated and any newly
required constituents read through the canonical interface. The generic explicit
JSON-input builder remains available as `python3 -m scrapper.indices INPUT.json
OUTPUT_DIRECTORY`; it is for isolated/offline inputs, not the full Passport flow.

## Method and records

Daily price indices start at 1000 and preserve V1 constant 1/N daily return
weights and daily close rebalancing. Weekly indices derive valid OHLC and
activity from every expected daily input and explicitly rebalance at the weekly
close. Intraday indices rebalance at each completed interval close. Those are
separate period strategies, not silently labelled samples of the daily strategy.
The low-level `service.weekly` function is a separately labelled sampled view of
a daily-rebalanced chain; the explorer's Weekly strategy uses `timeframes`.

Every fixed member must have both current and preceding synchronized closes.
Two distinct companies are required, including after leave-one-out exclusion.
Missing inputs break the price chain permanently; no available-member weight
renormalization, forward filling or retrospective bridge occurs. A base starts
only after all fixed members are listed and first observed. Classification is
retrospective: primary/secondary/unranked exposure evidence, known-at dates and
registry links are preserved, not backdated into historical membership claims.
Ordinary shares are selected using explicit canonical registry links; rights
instruments cannot overwrite their mappings.

EMA10/20 use preserved recursive arithmetic. RS55 requires 56 exact matching
completed observations with the benchmark. The weekly dataset has only 48 weeks,
so weekly RS55 is correctly unavailable/warmup. Intraday MIDSML400 benchmarks
are built only from complete, checksum-verified retained five-minute buckets;
missing intervals and the unavailable Nifty50/sector intraday histories stay gaps.
Daily and weekly sector comparisons require the same expected dates as the index.

Activity is sum(raw close × raw NSE share volume) / 10,000,000 in ₹ crore.
Weekly activity sums daily proxies rather than weekly close × weekly volume.
The aggregate activity ratio divides by the preceding 20 period values, excluding
the current period. This is neither exchange-reported index volume nor exact
turnover nor institutional buying. Breadth requires valid observations from all
fixed members; EMA20 breadth is unavailable before constituent warmup.

Confirmed unadjusted split/bonus events use causal forward share-unit factors.
Unknown adjustment basis, unresolved actions and missing ex-date observations
block calculations. Ordinary dividends are not reinvested. A known weekly
share-unit event currently makes that weekly calculation unavailable pending
daily normalization; it is never silently ignored. Existing limited reviews
have no confirmed share-unit events in their examined window. **Provider
adjustment basis and complete action coverage across the broad universe remain
uncertified**, including dates beyond the limited retained reviews. Therefore
all generated indices retain provisional status.

Each build includes `membership.json`, `action-evidence.json`,
`constituent-candles.json`, `sources.json`, `official.json`, `datasets.json`,
`coverage.json`, timeframe datasets/source revisions/coverage, and chart assets.
The checked summary is `docs/research/index-prod-01/coverage.json`.
No source financial/document records or research classification tables are edited.

## Verification

PASS: 76 tests, including the restored V1 historical tests and V2 regression:

```sh
python3 -B -m unittest scrapper.tests.test_indices_application scrapper.tests.test_indices_official scrapper.tests.test_indices_timeframes scrapper.tests.test_indices_benchmark_intraday scrapper.tests.test_indices_production scrapper.tests.test_subsector_index_methodology experiments.subsector_index_v1.tests.test_indices experiments.subsector_index_v2.test_v2 -q
```

PASS: 5,312 independent Decimal checks of raw constituent return ratios, levels,
value proxies, EMA breadth, preceding-20 activity and RS55 for Tyres, EMS,
Transformers, Carbon Black and Private Banks. Results and representative values:
`artifacts/index-prod-01/build-20261113-v4/independent-verification.json`.

PASS: 22 Chrome checks, no browser exceptions and no external requests. Screenshots
and results are under the build's `chart-verification/` directory. The tests cover
actual selectors, plotted histories, RS, official charts, four-hour anchors,
weekly benchmark completeness and correctly labelled activity axes.

Independent review found identity overwrite, action plumbing, stale-date metadata
and chart benchmark/axis issues; all were corrected and regression checked.
An initial restored-data build failed on a canonical `end_upper_bound` timestamp
variant; the adapter now preserves it. Earlier failed/incomplete local builds are
retained separately and are not the current output. Full repository tests and
production runtime health checks were not run for this scoped implementation.
There are no migrations or runtime activation steps; rollback is source-only.
