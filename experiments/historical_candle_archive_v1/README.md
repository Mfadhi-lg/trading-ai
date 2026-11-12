HCD-01 extends the existing identity and indicator infrastructure. It never
creates or migrates a company master and has no full-universe execution command.

Run from the repository root with Python 3.14 and PyArrow available. The pilot
uses `execute_ta02d.secure_supplier` for credentials and `upstox_candles.url_for`
for provider URLs. Credentials and error bodies never enter evidence files.

`python3 -m experiments.historical_candle_archive_v1.prepare` creates deterministic
universe/manifests from the read-only acquisition-table projection. It preserves
the fixed pilot, whose historical eligibility comes from the retained October
scope. The full-universe plan uses November's latest acquired scope observations.

Identity resolution also uses existing canonical security identifiers and
scraped Screener exchange-code paths. Missing registry links do not imply a
missing company symbol. `repair_identity` updates only archive identity overlays
and the disabled full-universe manifest, preserving prior outputs and leaving the
pilot/company master untouched. Changed provider ISINs retain historical IDs and
require continuity review; missing provider listings remain explicit gaps.

`python3 -m experiments.historical_candle_archive_v1.acquire --limit 1000` runs
only the fixed 20-company pilot. Before each write, the archive checks the actual
external mount/device and free-space reserve. It refuses a missing drive and
never creates an internal fallback. An exclusive writer lock prevents overlapping
acquisition/analysis writers. Allow one writer at a time. Successful requests
are checksum-verified and skipped; saved raw responses recover without a GET.
Transport failures have bounded recovery; authentication/rate-limit HTTP failures
stop acquisition. Manually resuming retains a three-run request attempt ceiling.

The separately authorized HCD-02 full campaign resumes durable SUCCESS index
entries without rereading all completed files on each wake. Interrupted requests
retain checksum recovery and uncertain-delivery stops. Full raw/Parquet checksum
verification remains required in campaign finalization; corruption in completed
files can therefore be detected at finalization rather than startup. Execution
receipts distinguish resumed entries from checksum-verified reuse and report
request-boundary progress during long recovery scans.

`python3 -m experiments.historical_candle_archive_v1.analyze` validates retained
raw evidence and writes separate native/custom definitions and provisional
reconstructions to Passport. It does not issue requests. Source OHLC defects
remain immutable and are quarantined rather than repaired. The calendar includes
official annual holidays and listed exceptions but is explicitly partial;
pre-2022 missing-session coverage is uncertified.

`python3 -m experiments.historical_candle_archive_v1.indicator_check` calls the
preserved EMA, DTR/ATR, DSS, Simple Volume and RS engines. Weekly source/RS/DSS
limitations remain open. Historical-display DTR values are not causal features;
only its explicitly causal channel is suitable for receipt-gated observations.

`python3 -m experiments.historical_candle_archive_v1.cross_source` compares
retained TradingView daily references; differences never trigger automatic
adjustments. `python3 -m unittest experiments.historical_candle_archive_v1.test_archive`
runs synthetic safety/causality tests without credentials or provider requests.

The indexed inventory and every newly acquired OHLCV/indicator value reside on
Passport. Repository artifacts contain research, counts, differences, identities,
checksums and manifests. The review ZIP excludes provider OHLCV and credentials.
No cron, launchd, Automation, ML, Document Intelligence, or full-market backfill
is installed or started. Production scheduling requires pilot acceptance and a
reviewed calendar. The full-universe backfill requires separate explicit approval.

## Reproducible archive integrity audit (issue #21)

Run the software integrity checks without a drive, credentials, provider requests,
or application database:

```sh
python3 -B -m unittest scrapper.tests.test_candle_archive_integrity experiments.historical_candle_archive_v1.test_archive experiments.historical_candle_archive_v1.test_hcd02 -v
```

These tests use synthetic receipts and temporary files. They verify the audit
logic and immutable checksum, timestamp, duplicate, OHLC, session and receipt-time
contracts; they do not certify the contents of a retained historical archive.
The new audit regression tests are also discovered by the default offline CI suite.

The existing `analyze` command checks retained successful pilot requests against
`pilot-manifest.json`. It verifies decompressed raw SHA256 before normalization.
It writes reports **and derived Parquet data** under the existing archive writer
guard, so a retained-data audit needs separate operator authorization. It was not
run against Passport for this issue. Full-campaign finalization and the HCD-02
access interface remain separate; see [HCD02.md](HCD02.md).

Its reports have the following interpretation:

- `historical-coverage-inventory.csv` counts missing scheduled Daily/hourly/4-hour
  sessions even when the entire timeframe is empty. The comparison starts at the
  later of 2022-01-01 and the supplied listing date (2022-01-01 if unknown), and
  ends at `prepare.END`, the frozen pilot end date. Counts are review candidates:
  suspension, no trades, absent acquisition and provider omissions are not
  distinguished by a missing bar. Weekly session coverage is explicitly
  `NOT_EVALUATED_WEEKLY`; its legacy zero counts do not certify completeness.
- `missing-session-review.json` lists missing dates, candles on known closed
  dates, unresolved calendar dates and intraday grid differences with missing
  and unexpected starts. Unresolved calendars are never classified as closed.
  NSE special-session segments replace ordinary hours; breaks are not filled.
  Pre-2022 history and unreviewed calendar exceptions remain uncertified.
- `duplicate-revision-review.json` reports overlapping request timestamps,
  distinguishing exact repeats from changes in any OHLCV **or open interest**
  field. The in-memory comparison uses the latest processed receipt; raw
  responses remain immutable. Duplicates within a single raw response are a
  fatal normalization error, rather than silently deduplicated. Invalid OHLC
  remains quarantined. This pilot report is not the HCD-02 revision selector.

Provider timestamps are preserved verbatim and normalized starts use aware IST
times. Daily timestamps label date buckets, not session opens. Receipt time is
never backdated to candle time; a historical backfill cannot establish delivery
before an old trading decision.

Adjustment policy: preserve provider OHLCV and open interest using exact numeric
values. No local split, bonus, dividend or rights adjustment, volume rescaling,
gap filling or cross-source reconciliation changes source values. Every native
row retains `UNKNOWN_PROVIDER_ADJUSTMENT_BASIS`; this does not certify the
provider series as unadjusted. No certified local adjusted series exists, and
HCD-02 rejects requests for one. Cross-source differences and receipt revisions
are evidence for review, never inferred corporate actions. Certifying an adjusted
series requires separately sourced action dates/factors and versioned lineage;
that work is outside this audit fix.
