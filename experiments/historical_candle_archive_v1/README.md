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
