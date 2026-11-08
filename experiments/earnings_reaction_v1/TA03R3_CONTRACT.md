# TA-03R3 research contract

This extension reuses Candle, Session, the Upstox offline parser and TA-03R
pine_average/calculate_daily. No existing implementation or historical evidence
is replaced. No TA-03R2 files or earlier INDICATOR_CONTRACT_V1.md were found in
the actual research module. Weekly remains required later; it fails explicitly
here rather than silently aliasing another timeframe.

## Availability and session aggregation

Inputs must have aware timestamps, one instrument, unique starts, valid OHLCV,
and completed_at <= observation cutoff. Session schedules must be explicit,
ordered and nonoverlapping. Every scheduled base candle from open through the
cutoff must exist. Holidays are omitted by the caller's evidenced schedule;
special sessions use their explicit opens/closes. This engine does not invent
an exchange calendar. The retained three-date schedule is provisional.

Daily display uses authoritative provider daily buckets, including their
conservative next-midnight completion. Intraday displays anchor to 09:15 IST in
the bounded schedule: 4h starts 09:15/13:15 (240/135 minutes), 75m starts
09:15/10:30/11:45/13:00/14:15, 1h starts 09:15 through 15:15 (last 15 minutes).
A developing aggregate retains the planned end and cannot pass a completed-bar
feature gate. Its OHLCV contains only completed constituent bars. Base bars
cannot be split or interpolated. TV grouping parity has not been established.

## Intra RVOL candidates, ratios x

A = cumulative volume at base-bar end / mean full-day volume of the preceding
20 completed daily observations. Current-day final volume is never a historical
intraday denominator. Daily display A uses final V only at daily completion.
B = cumulative volume / mean cumulative volume at exactly the same elapsed
session endpoint in the previous 20 scheduled sessions. All endpoints and
sessions must exist. No shortened-session carry, interpolation or skipping.
At a display-bar close we sample that endpoint, not average base-bar ratios.
Both reset the numerator at each session open. Missing/zero baselines are null.
A is a hypothesis specified for this task. B follows the published cumulative
mode concept, without TradingView auto-step anchors, HMA or threshold channels.
DSS's protected internal formula remains unresolved. Daily B needs the missing
prior intraday history in this implementation; it is not inferred from daily
volumes whose session/feed equivalence is unproven.

## Independent volume features

Defaults: PP10; average21; relative21; U/D20; turnover50 SMA. Direction is close
versus previous close; equal closes are neutral (chosen convention). First-bar
direction is unknown. A PP volume event needs a full 10 eligible prior bars,
an up close, at least one down close in that window, and current V strictly
greater than their maximum. No price-pattern, entry or trading signal is implied.
The corrected author description uses trading days, not ten down days. On
intraday display this extension uses chart bars and labels this choice as an
unverified generalization; it does not silently aggregate PP to daily.

Volume SMA includes current V. RVOL uses the preceding 21 V values, excludes
current V, and exports both ratio and percent. The mentor's current-inclusion
RVOL toggle is unknown. U/D sums the last 20 chart-bar volumes including current,
partitioned by previous-close direction; zero down-volume yields null. Dry means
V < SMA21/5; high up/down means V > SMA21 with that direction. Equality is not
an event. Missing warmup gives null rather than False. The price-volume product
of SMA(close,50) and SMA(volume,50) is an independently named turnover proxy in
INR, not exact traded turnover. Raw close*volume is also retained.

Daily record_high/low_63d and record_high/low_252d compare strictly to the
previous 63/252 daily observations. Session counts, equality, these windows and
insufficient-history policy are independent choices. record_observed_history is
only a record since the retained series began, never lifetime HVE. The author's
release history changed HVE behavior; no current exact HVE/HVY/HVQ implementation
is asserted. Those original-named outputs remain null. Intraday record windows
are unavailable. highest_volume_pct_observed = 100*V/max(prior retained V), an
explicit research statistic, not a recovered author label.

Color precedence is independently chosen: up-day record (gold), PP (blue), dry
or low record (orange), high up (green), high down (red), otherwise grey. Flags
are retained independently even when colors mask them. A cutoff-aware snapshot
selects latest research record markers; it never deletes past event flags or
backdates a later marker. Bull snort, power, projections, price painting and
one-minute liquidity are disabled and unimplemented as requested. Their supplied
50/500000 settings do not change enabled arithmetic.

## Session profile approximation

Final session profiles require complete base coverage and cutoff >= close.
Fixed INR1 bins are anchored at zero: [k,k+1). V is spread uniformly over the
bar's low-high interval in proportion to overlap. Zero-range bars use their
single price bin. Up/down classification uses close >= open versus close < open.
These are independently selected allocation/bins; five-minute OHLCV cannot
recover trade-by-price. Full row distributions and up/down arrays are retained.
POC is the midpoint of the largest row; POC ties select lower price (choice).
VAL/VAH are included row outer edges. Greedy expansion targets 70%, comparing
adjacent rows; equal volumes choose nearest POC then upper. Stop before a row
would overshoot; always include POC. Actual fraction may be below 70% or above
if POC alone exceeds it. This follows the current published TV value-area steps,
not py-market-profile's overshoot behavior. Zero total yields null levels.

HD dynamically selects lower resolution/detail from chart viewport. Fixed bins,
uniform allocation and five-minute data cannot recreate this. Sessions All
means only retained regular hours here: pre/post-market data is unavailable.
Only final VAH/VAL at session close are plotted; no final level is painted back
into earlier intraday features. POC/histogram/developing profiles remain hidden.
Daily dots identify session finals, not a daily-period profile. Extension is off.

## Existing indicators and independent validation gates

EMA10/20 reuse pine_average EMA (first-value seed); limited retained intraday
history gives different initialization from a long TV chart. DTR/ATR/RVOL daily
reuses calculate_daily; current-day security lookahead is not copied into
intraday charts. RS source supports configured length55/close, but matching
NSE:NIFTYMIDSML400 data and intraday source semantics are missing. Nifty50 is
never substituted. TA-03R2 remains absent: HTF merge, weekly/monthly contexts,
configured benchmark acquisition and TradingView exports are unresolved.

IMPLEMENTED, UNIT_TESTED, REAL_DATA_TESTED, VISUALLY_COMPARED,
NUMERICALLY_COMPARED, EQUIVALENT and CAUSAL_RESEARCH_SAFE are separate gates.
Completed-observation causality does not establish historical feed availability,
corporate-action consistency, exchange calendar certification or backtest admission.
No TradingView numerical tolerances are asserted before comparable references.
Calibration and verification sets are both empty; no fitting has occurred.
