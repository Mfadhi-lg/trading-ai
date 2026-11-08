# TA-03R indicator correction

The supplied original Pine scripts are now verified by SHA256 and preserved under
[`ta03r/sources`](../../artifacts/earnings-reaction-v1/ta03r/sources/).
`indicators.calculate_daily()` implements their completed-daily arithmetic with
source defaults (ATR14 RMA, RVOL20 percent, ratio-of-returns RS123 against supplied
Nifty candles). `calculate_daily_research_proxies()` preserves the earlier TA-03
formulas, which are historical research proxies, not validated reproductions.
Original TA-03 evidence is unchanged. TradingView chart parity remains unverified.
See the [repair report](../../artifacts/earnings-reaction-v1/ta03r/validation-report.md)
for scope, attribution, formulas and limitations.

Reproduce offline: `python3 -B -m experiments.earnings_reaction_v1.validate_ta03`.
It validates retained source hashes and writes repaired outputs only to `ta03r/`.
The historical orientation below is retained; its earlier source-absence findings
are superseded by the originals supplied for TA-03R.

# Earnings reaction v1 — TA-01

TA-02A adds offline evidence gates in `verification.py` and a five-row manifest
builder in `investigate_sample.py`. Its current result is zero eligible joins;
see the [TA-02A evidence report](../../artifacts/earnings-reaction-v1/ta02a/README.md)
for independent source observations, unresolved publication/instrument/calendar
evidence and the conditional Upstox retrieval boundary. The test command below
runs both task suites. The remaining sections document the preserved TA-01 baseline.

Working experimental code: immutable filing/event projections, explicit-calendar
publication timing, conservative company-period grouping, and a reaction-candle
boundary guard. No production code, financial contracts, governance labels,
acquisition configuration, services, migrations or workers change. No network/API
requests, documents, indicator calculations, signals or ML runs are involved.

Run from the repository root:

```sh
python3 -B -m unittest discover -s experiments/earnings_reaction_v1/tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m artifacts.earnings-reaction-v1.ta01.inventory
```

The second command reproduces the bounded retained-snapshot evidence only. It
requires the existing named snapshot and original Focus evidence in this checkout.
Do not redirect it at a live database. It uses `mode=ro&immutable=1`, refuses
WAL/SHM companions and checks size/mtime and absence of sidecars after reading.
It does not initialize schemas, import the operational database opener, copy or
fully hash the 14.6 GB snapshot. The selected payload has its own SHA-256; this
is not a full database integrity certificate. Queries are bounded by `LIMIT`,
indexed reporting period, a small symbol list, and a VM instruction budget.

## Existing boundaries inspected and reused

All mandatory orientation files exist. Exact paths/hashes are recorded in
`artifacts/earnings-reaction-v1/ta01/orientation.json`.

- `scrapper/sources/exchange/result_filings.py`: canonical `ResultFiling`, filing
  keys, exact result end, raw broadcast and normalized timestamp. `from_result_filing`
  projects its output without a second feed parser. Parser acceptance does not
  prove an original earnings event. NSE revisions may use revised broadcast;
  the BSE announcement CSV may infer standalone scope. Preserve that limitation
  when adapting such records; do not interpret discovery scope as accepted scope.
- `scrapper/db/exchange/filings.py`: inspected `exchange_result_filings` storage,
  collision handling and `resolve_screener_path`. Its operational enqueue logic is
  not called. `from_stored_filing` projects caller-selected rows; it owns no SQL.
  Historical filing ID/key, exchange key, ISIN, exact period, scope and evidence
  remain intact. Native announcement ID has a separate nullable field; a generated
  filing key is not a native announcement ID.
- `scrapper/research/features/financial.py` and the public
  `scrapper.research.features.company_quarter_features()` facade remain authoritative
  for financial features. TA-01 does not calculate financial features or copy their
  SQL. Later calls require an aware decision time, explicit standalone scope,
  source/period disambiguation, and capture/acceptance/revision provenance.
  `PIT_SAFE_FROM_CAPTURE` does not prove historical public availability;
  `EXPLORATORY_BACKFILL` stays exploratory.
- `scrapper/research/governance/focus_p0.py` and
  `scrapper/research/focus/build_p0_governance.py`: inspected the historical symbol,
  exchange identity, quarter and label interfaces. Retained
  `FOCUS_LABEL_REGISTRY_v001.json` and `FG00_SOURCE_MAP.json` provide read-only
  membership evidence. No builder, selector or governance logic is run or changed.
  Current identifier/master links do not establish historical tradability.
- `scrapper/sources/exchange/reporting_periods.py`: confirms the April–March quarter
  convention; source filing periods remain authoritative rather than query periods.

## Contract and timing semantics

`Evidence`, `Identity`, `EarningsEvent`, `Session`, `TradingCalendar`, `EventTiming`
and `EventGroup` are frozen dataclasses. Nested state uses immutable tuples and raw
JSON strings. Evidence retains capture time separately from publication and carries
named historical-availability limitations. Missing native announcement IDs and
unresolved instruments remain null. Timestamps must be offset-aware; adapters
retain the original exchange timezone as `Asia/Kolkata`, alongside raw source text.
A missing, naive or unparseable normalized publication time becomes unresolved.
Unknown source relationships default to `unresolved_candidate`.

A caller supplies a complete, versioned, explicitly evidenced regular-session
calendar with a coverage range. TA-01 supplies no real exchange calendar. Session
opens are inclusive; closes exclusive. Before-open publications first affect the
same session at its open. Intraday publications first affect the containing session
at the publication instant. At/after-close publications first affect the next
explicit session. Weekends and holidays use the next enumerated session. Missing
calendar, venue mismatch, missing timestamp, out-of-range date or insufficient
next-session coverage produces a controlled unresolved result, with no guessed day.

`publication_date` is the venue-local announcement date. `publication_session` is
that day's regular session if present, including an already-closed session.
`market_event_date` is the first affected regular session date;
`first_eligible_session` is that same session, with `observable_from` preventing
intraday events from being available at its earlier open. These describe public
source timing. They are not execution times, usable capture times or a trading
entry convention. Actual price reaction, auctions, special sessions and latency
remain unmeasured; a future calendar must enumerate the intended session regime.

Grouping uses evidenced company identity and exact period across exchange and
reporting scope. It preserves all filings and all revision timestamps. Exactly one
explicit original plus explicit duplicate/amendment links is needed to resolve a
cluster. Multiple originals/scopes or unlinked revisions require review; TA-01 never
selects the earliest/newest row as the original. Unknown companies remain separate
unresolved clusters, with no fuzzy name join. Revisions are not independent samples
and must not replace original financial values at earlier cutoffs. `FilingKind`
records source classification separately from timing and from verification status.

`reaction_candle_eligible` is a limited boundary guard: it blocks missing company or
equity instrument evidence, earlier/straddling candles, and candles ending after the
signal timestamp. A daily candle containing a midday release is not a pure
post-publication reaction input. TA-02 must still validate candle identity, session
membership, gaps and adjustment basis. Passing this guard is not dataset eligibility.

## Verified source findings and limitations

Evidence directory: `artifacts/earnings-reaction-v1/ta01/`.

The retained snapshot is
`artifacts/acquisition-readiness/AR-01.3C/attempt-02-post-canary/scraper.db`.
The local `scrapper/store/scraper.db` has WAL/SHM companions and was not opened.
No production DB was accessed. The sample is purposive, contains 20 real NSE
filing candidates (ten March, ten June), and includes chemicals, auto components,
banking, pharmaceuticals, IT and revision/multiple-scope examples. It is neither a
representative universe nor 20 independently resolved earnings events.

All 20 have stored company identifier corroboration and matching raw/normalized
broadcast times. Three are source-declared amendments and 17 source-declared
originals. None has original feed bytes reconciled at the stored blob path; no
historical issuer-to-tradable-instrument mapping or native announcement ID was
independently established. Independent verification counts for company, timestamp,
instrument, and all three together are therefore **zero**. All candle joins remain
blocked. A record's existence and internal consistency are not verification.

The exact source dates support Q4 FY2026 = 2026-03-31 and Q1 FY2027 = 2026-06-30.
Q2 FY2027 = 2026-09-30 follows the inspected fiscal-period contract, but no September
filing is available in the chosen retained snapshot and no Q2 historical Focus list
was located. This is a coverage gap, not a claim of no announcements or selection.
Do not relabel August announcements of March results as June fiscal results.

`focus-source-manifest.json` hashes original files in place. Q4 has 16 selected-list
screenshots (239-symbol header per retained governance evidence, with OCR ambiguity);
Q1 has 191 selected symbols in `stocks.csv`, corroborated by its workbook. All
available retained source hashes match. Broader Screener exports, derived pools and
rejected-candidate files are separate research-universe evidence without governed
negative-label authority. List publication/capture dates are not earnings dates.
Absent membership is `unknown_not_negative`, never a fabricated negative label.
No historical labels or original files were altered.

## Future market-data boundary — proposed, unimplemented

Searches of actual source paths, ignored/untracked original `OLD/`, experiments,
retained artifact filenames and canonical Python sources did not locate the original
DTR/ATR/RVOL port or Relative Strength port. No versions, port-specific tests or
reference parity evidence can be claimed. No Upstox historical-candle adapter,
OHLCV dataset or Nifty/benchmark series was located in these searched paths.
Vendored Rust `candle-*` libraries are tensor libraries, not market candles.
These are bounded repository findings, not proof about external storage.

`experiments/upstox_fundamentals_pilot/00_access_and_scope.md` and
`BLOCKED_STATUS.md` describe a separate blocked fundamentals pilot; it contains no
market-data implementation. It was left unchanged. Its token/permission assumptions
were not checked or reused. No provider entitlement or current API behavior is
asserted by this task, and no authenticated requests were made.

The minimum future provider-independent candle input must carry:

- Provider instrument key, exchange and an evidenced, effective-dated mapping to
  company/security identity; instrument class and listing/delisting history.
- Interval, timestamp meaning (bar start/end), aware timestamp and original exchange
  timezone; OHLC and volume with numeric units and price currency.
- Explicit trading-session ID, versioned calendar and session membership.
- Expected interval coverage, missing/duplicate/out-of-order bars, partial bars,
  zero-volume conventions, retrieval limits and completeness/gap status.
- Corporate-action adjustment basis, action/version history and raw/adjusted status;
  never mix adjustments across issuer, benchmark or outcome series.
- Provider/source version, requested range, retrieval/receipt time, delay and
  entitlement, immutable retained response hash/reference and retention provenance.

TA-02 prerequisites: separately authorize bounded candle access and retention;
confirm current provider contract/entitlement; supply dated equity instrument maps
and a verified exchange/session calendar; resolve original/amendment relationships
and historical capture limits; recover independently verifiable feed evidence;
obtain September candidates and the missing Q2 list (or explicitly exclude that
cohort); freeze actual universe/list snapshots; choose intervals, gap handling and
corporate-action policy; retain a small golden candle fixture with source provenance.
Indicator ports/reference versions and parity tests must be supplied before any
later indicator integration. Benchmarks require independently mapped series and
matched calendars. No TA-02 component is implemented here.

## Leakage rules for subsequent tasks

Pre-announcement inputs require observation strictly before publication and actual
capture/acceptance/review eligibility by their declared cutoff; later revisions,
list membership, market observations or indicator values cannot enter that snapshot.
Publication alone cannot backdate our knowledge. Financial inputs must preserve the
existing facade's statuses, scales, scopes and versioned lineage.

Post-announcement reaction inputs may use observations at/after publication and no
later than an explicitly declared signal timestamp, subject to actual availability.
Every candle/market observation/indicator dependency must satisfy that interval;
straddling candles require finer data or exclusion. Signal and execution references
must be declared separately from publication/session timing.

Future outcomes start strictly after the declared signal/execution reference;
D+5/D+20 returns, drawdowns and follow-through are outcomes, never earlier inputs.
D+n counts explicit trading sessions, not calendar increments. Neither the outcome
calculator nor a feature engine is implemented in TA-01.

Freeze historical universes including delisted securities, validate effective-dated
company/instrument joins, preserve amended disclosures as later versions, and refuse
future Focus membership at earlier cutoffs. Selected-list performance alone does not
establish a profitable strategy. Tests below certify software behavior on synthetic
fixtures, not source correctness, calendar correctness or trading performance.
