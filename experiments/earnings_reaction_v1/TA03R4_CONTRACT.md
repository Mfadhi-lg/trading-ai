# TA-03R4 amendments and comparison contract

TA-03R3 remains historical evidence. The source-before copies and preservation
manifest under ta03r4 retain its implementation identity. Its old builder must
not be used to regenerate historical evidence after these repairs.

## Pocket Pivot repair

At zero-based index 10 there are exactly ten preceding chart bars. Evaluation
now uses i >= pocket. Earlier bars remain null; current V must be strictly greater
than the maximum qualifying down-bar V; a window without a known down bar is
False, not a vacuous pivot. The first retained bar's previous-close direction
remains unknown and cannot itself be classified as down without an earlier close.
No other PP financial or color-precedence definition is changed. The version is
ta03r4-independent-v1. New regression tests cover the first window, earlier bars,
equality, greater-than, no down bar, cutoff and prefix invariance.

## Existing source arithmetic and integration

TA-03R's arithmetic is extracted unchanged to _source_arithmetic and
_dtr_arithmetic in the same indicators.py. calculate_daily keeps its existing
signature/defaults and daily validation. Every retained TA-03R daily ATR, DTR,
RVOL and RS value reconciles against the old CSV after extraction.

calculate_chart_rs validates completed stock and NIFTYMIDSML400 display bars with
exact matching starts/ends. It reuses the same ratio-of-returns, mean, trend and
confirmation arithmetic, with period55 and close. EMA10/20 continue to reuse
pine_average. Initial unequal coverage may be explicitly trimmed at the beginning
of the RS-only stock sequence; missing interior benchmark bars are never dropped,
carried forward or replaced by Nifty50. Other stock indicator histories keep
all requested stock data.

DTR's selector remains W on weekly, M on monthly, D otherwise. Only the four
required D-source display contexts are integrated; weekly/monthly aggregation
fails explicitly and remains a future requirement. merge_daily_dtr separates:

- causal_last_completed_daily: the latest provider daily calculation completed
  by the display-bar close; all dependencies are no later than that close.
- historical_display: a source-semantics reconstruction using the current day's
  final ATR (lookahead_on), combined with confirmed/carried daily range and RVOL
  (lookahead_off). Each row records its ATR dependency and future-use flag.

Historical display fields are forbidden as causal research features. Live and
bar-replay security behavior has not been independently demonstrated. Final
current-session causal/developing DTR is not fabricated. Explicit proposed NSE
session close times are used only for dates in the retained schedule; other
provider daily completions remain conservative. This does not certify complete
exchange calendar coverage or backfilled historical data availability.

## Data request boundary

No authenticated request is permitted until the owner approves the nine-request
retrieval-plan.json digest. An earlier eight-request draft is superseded: the
dated August18 cursor requires twenty prior one-minute sessions beginning July21.
Benchmark five-minute data begins August17, supplying 32 regular sessions before
October1 (64 four-hour bars), enough for 55-bar RS warmup in the October window.
Stock minute windows are four contiguous chunks <=28 inclusive calendar days;
benchmark windows are three such chunks. Daily data extends through October30.
The existing TA-02B request whitelist/authorization remains unchanged.

The nine requests use the original parser with an exact-scope CandleRequest
subclass. Secure token custody reuses execute_ta02d.secure_supplier only after
approval and manifest checks. Curl receives credentials via stdin, never argv,
artifacts or logged exceptions. No redirects/retries. The first benchmark daily
request is the only entitlement probe. A denied/empty/unsupported interval stops
the sequence; there is no automatic substitute or fallback. Exclusive execution
ledger creation prevents a second execution under the same invocation.

## Comparison evidence and tolerances, fixed before retrieval

The 16 owner-provided PNGs have hashes and manual observations in
reference-inventory.json. They are images, not timestamped machine exports. The
09_51_26 image identifies a daily August18 cursor: OHLC 656.25/662.40/650/655.70,
DTR selected-bar RVOL20 18.00, DSS display18.48 and RS55 -0.06. These are rounded
readings only. Latest-bar table values have no printed date and remain unbound;
October30 is a hypothesis, not an independently established timestamp.

Daily OHLC and source-rounded each-bar RVOL use 0.000001 absolute comparison
tolerance; RS's two-decimal display uses 0.005. A MATCHED rounded observation
never implies full-series equivalence. DSS's displayed units are source-unverified:
A/B percentage differences may be measured as hypotheses but remain NOT_COMPARABLE
for equivalence. Ratios and percentages are retained as separate columns.
No source feature is calibrated to those readings. Calibration set is empty;
August18 is the first available dated verification observation.

Blue volume bars are ambiguous because PP and unresolved highest-volume colors
overlap. Exact event agreement is not inferred from blue pixels. The supplied
"Color based on previous close" toggle is OFF, while the preserved independent
research direction uses previous close; this known configuration difference
remains explicit. EMA10/20 are hidden in the screenshot; there is no visible EMA
reference. No original four-hour, 75-minute or hourly chart/export was found, so
all three comparisons remain REFERENCE_MISSING until actual references arrive.

Session profiles remain approximations: fixed INR1 zero-origin bins, uniform
low-high allocation, POC lower-price tie, documented no-overshoot VA70, up/down
from close vs open, final values available only at close. New provider data uses
one-minute bars. This improves input resolution without recovering trade-by-price
or TradingView HD's viewport-dependent detail.

## Execution

Before approval (public metadata only):
python3 -B -m experiments.earnings_reaction_v1.retrieve_ta03r4

After explicit owner approval, execute with the approved manifest SHA256 and a
non-secret owner-approval reference. Then run:
python3 -B -m experiments.earnings_reaction_v1.compare_ta03r4

The builder refuses absent/incomplete data; uses existing engines and renderer;
checks complete proposed-session base coverage; retains causal vs historical
fields; renders real TIPSMUSIC charts; repeats numerical/chart output hashes;
and runs the full research suite. Missing minutes or an unenumerated special
session blocks calculations rather than causing invented observations.

## Authorized execution and final observation

The owner subsequently approved manifest
f23765a09dab8183f0e2ba11e8e76d7186f32d5900a6764d3d50de410efa4b0d.
All nine GETs succeeded, and the one-shot ledger consumes that scope. Do not
repeat retrieval. The final report records actual numerical/visual comparisons.
Undated latest-reference rows now include conditional October30 differences but
remain NOT_COMPARABLE. A changed .DS_Store is recorded as OS metadata; every
other prior evidence file still must pass its original hash. The final builder
also audits 1,386 feature rows with independent Fraction arithmetic. PNG review
copies were rendered twice locally; both renders of each chart are identical.
