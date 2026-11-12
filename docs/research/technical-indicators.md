# Technical indicator ownership and definitions

Source inspection: 12 November 2026, baseline `c0025bac6`. This is the canonical
definition and reuse map for the current implementations, not certification of
TradingView parity or a production signal engine. There is no separate indicator
signal engine under `scrapper/`. The [daily historical indicator engine](daily-indicator-engine.md)
now provides a read-only application interface for SMA, EMA, ATR and RVOL over
canonical archive windows. Existing operational chart consumers import the shared
arithmetic from `experiments/earnings_reaction_v1`; the
[experiment lifecycle](../../experiments/README.md) identifies those consumers.

## Shared source-derived arithmetic

Paths below are relative to `experiments/earnings_reaction_v1/`.

| Definition | Implementation to reuse | Exact contract and limit |
|---|---|---|
| Price moving averages | `indicators.pine_average` | SMA/RMA/WMA need n non-null observations; RMA seeds with SMA, then `(previous*(n-1)+x)/n`; EMA seeds with first non-null, alpha `2/(n+1)`. Null inputs retain state. |
| Daily TR / ATR / DTR | `indicators.calculate_daily`, shared `_dtr_arithmetic` | First source TR is null; later TR = max(H-L, abs(H-prior C), abs(L-prior C)). Default ATR14 is current-bar RMA. DTR = H-L; ratio = DTR/ATR, percent = Pine-rounded 100*ratio. Zero/missing ATR gives null. Private helpers are existing internal dependencies, not new public APIs. |
| Source RVOL20 | `indicators.pine_rvol` | Current volume divided by SMA of the preceding 20 volume observations, excluding current; raw ratio and Pine-rounded percent are separate outputs. Null sampling follows `pine_average`; nonpositive baseline gives null. |
| Daily source RS | `indicators.calculate_daily` | `(S[t]/S[t-n])/(B[t]/B[t-n])-1`, dimensionless; default n=123, source=close, RS mean=50, price mean=50, trend base=5. Caller supplies benchmark, with exact daily date alignment. |
| Chart RS | `indicators.calculate_chart_rs` | Same shared ratio-of-returns arithmetic, default n=55 against configured NIFTY MIDSML 400. Exact start/end alignment, no inner join/carry/substitution. Daily and supported intraday contexts only. |
| Intraday display of daily DTR | `indicators.merge_daily_dtr` | `causal_last_completed_daily` uses daily completion <= observation. `historical_display` may use same-day final ATR before its completion and flags that future dependency. Historical display must never feed causal features. Weekly/monthly aggregation is rejected. |
| Saved Simple Volume settings | `simple_volume.simple_volume_features` | Current-inclusive SMA21; RVOL uses last confirmed native daily volume / preceding21 daily mean (weekly mode uses weekly bars). Default direction is close vs open, pocket window=10 chart bars, dry divisor=5. U/D20 always compares previous close, equal close goes into down volume. Daily record/HVE labels require explicit history completeness. Saved-setting references do not establish general parity. |

`calculate_daily` defaults and `calculate_chart_rs` defaults intentionally differ.
Benchmark identity, timeframe, lookback, units, source version and completion
timestamp must accompany every derived field. Arithmetic uses Decimal except
the RS angle's `atan(float(...))`. Pine rounding is nearest with ties toward
positive infinity; Simple Volume display uses exact Fraction arithmetic with
the same upward tie direction.
Missing/warmup stays null. Never change a ratio into a percentage by relabeling.

## Duplicate or overlapping calculations retained separately

| Implementations | Difference / disposition |
|---|---|
| `indicators.wilder_rma` vs `pine_average(..., 'RMA')` | Legacy proxy resets on null; source arithmetic skips null. Retain both to reproduce old outputs; use source helper for source-derived calculations. |
| `daily_rvol` vs `pine_rvol` | Both exclude current volume; legacy requires the exact preceding window without nulls, source uses Pine non-null sampling. Do not replace one with the other in historical reports. |
| `calculate_daily_research_proxies` vs `calculate_daily` | Legacy TR starts at H-L; DTR divides by previous ATR. RS55 is excess return in percentage points; normalized price ratio anchors first observation at 100. These are historical research proxies, not source RS/DTR. |
| `indicator_alternatives.volume_features` vs `simple_volume_features` | Alternative uses chart-volume RVOL, previous-close direction with neutral equal closes and its own record logic. Saved Simple Volume defaults use native daily RVOL and open/close direction. Use the saved-setting function only when that is the declared definition. |
| `indicator_alternatives.intra_rvol` vs `dss_rvol_reconstruction` | Hypothesis A = cumulative volume / preceding full-day mean; B = cumulative / preceding same-offset cumulative mean. DSS reconstruction additionally counts the first chart bar twice in numerator and prior full-session totals. Tested settings carry reference status; other settings remain approximations. No author Pine equivalence is established. |
| `indicator_alternatives.session_profile` vs `svphd.profile` / `coarse_profile` | Both distribute candle volume across price bins and reuse `value_area`; grid and direction semantics differ. SVP HD requires an explicit grid/resolution or coarse candidate. Automatic viewport/resolution parity and POC ties remain unresolved. |
| `subsector_index_v1.core.constituent_features` / `equal_weight_index` EMA vs `pine_average` | Local EMA recurrence repeats the same first-price seed/alpha. Constituent features reset at explicit calendar gaps; the fixed-membership index instead permanently breaks its level chain after missing coverage. Preserve constituent coverage and action-normalization rules; V2 reuses V1 core. |
| `subsector_index_v1.core` RVOL / RS vs source indicators | Constituent RVOL20 excludes current volume, like the preceding-window source ratio on complete data, but resets its local history after calendar gaps. Index/stock RS uses ratio-of-returns with its own aligned calendar/warmups. Preserve those context differences when reusing arithmetic. |
| `technical_integration_v2.build.rotation` vs subsector rotation | Reuses `pine_average` for EMA and computes official-index rotation on its own contiguous coverage chains. Its `relative_strength_vs_nifty50` price ratio is distinct from RS55. Preserve index identity and missing-session handling. |

Reuse is already present: archive `indicator_check.py` and
`technical_fix_v2/integrate.py` import the shared indicator, Simple Volume and
DSS functions. V2 integration imports V1 context helpers; subsector V2 imports
V1 core. Consolidation here establishes ownership and definitions without
rewriting retained research or breaking imports. Any later arithmetic extraction
needs equality tests over gap/null cases and an explicit source/version decision.

The historical source location is
`artifacts/earnings-reaction-v1/ta03r/sources/`, a separately retained bundle
not tracked in this checkout. Root `dtr_atr_rvol.pine` and
`relative_strength.pine` are reference inputs, not a second Python runtime.
Preserve MPL attribution and the DTR source's lack of an explicit license grant;
reuse documentation does not grant distribution rights. Retained source and
reference contracts (`TA03R3_CONTRACT.md`, `TA03R4_CONTRACT.md`) bound the evidence.
No external provider/source rights were revalidated in this task.

## Verification boundary

Synthetic unit tests establish arithmetic, warmups, completion and leakage
behavior; they cannot certify source parity, historical tradability or execution
performance. Retained-source suites may need unavailable original bundles.
No provider request, archive analysis, report generator or live database is needed
to check this ownership decision. Promotion still requires the lifecycle gates.
