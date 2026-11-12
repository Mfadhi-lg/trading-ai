# Offline quantitative validation and backtesting

Issues #29 and #30 introduce caller-supplied research APIs in
`scrapper.research.quant`. They do not open databases, fetch data, train a shipped
model, place orders or activate a runtime. Existing pipelines remain unchanged.
Synthetic tests certify software behavior only; no investment result or
historical dataset is certified by this framework.

## Walk-forward evaluation

`validation.walk_forward(observations, windows, fit=..., predict=...,
score=..., evaluation_at=...)` evaluates at least two explicit, chronological,
nonoverlapping out-of-sample windows. Each `Window(train_start, test_start,
test_end)` uses half-open boundaries. Advancing train_start gives rolling
training; retaining it gives expanding training.

Each `Observation` has a unique key, aware decision timestamp, feature-availability
timestamp, label-availability timestamp, immutable Decimal feature tuple and
Decimal label. Availability means the latest availability of **all** dependencies,
including revisions and transformations. Features available after decision time
are rejected. Labels must become available strictly after decision time.
Training rows require decision time in [train_start, test_start) and label
availability **strictly before** test_start, purging overlapping label horizons.
Test decisions fall in [test_start, test_end); their labels must be known by
evaluation_at. Each window must have training and test observations.

`fit(training_rows)` is called anew per window. `predict(model, inputs)` receives
only keys, decisions and features for one decision timestamp at a time (allowing
contemporaneous cross-sectional ranking), then must return a finite Decimal
prediction per row in the same order. Later test features are withheld.
`score(actual_labels, predictions)` returns named
finite Decimal metrics. Regression, classification or per-date ranking metrics
are caller choices. Results include every window, ordered train/test keys,
predictions and metrics; metrics are deliberately not pooled across windows.

Fit scalers, imputers, selectors and hyperparameters using training information
only (nested chronological validation for tuning). Callbacks must not read global
labels, reuse later fitted state or fetch external data. The API cannot sandbox
arbitrary Python callbacks or certify timestamp truthfulness. Training datasets
are caller-owned; avoid mutable shared estimator state. Later out-of-sample rows
may enter later training only once their labels are known.

```python
from decimal import Decimal as D
from scrapper.research.quant.validation import walk_forward

results = walk_forward(
    observations, windows, evaluation_at=evaluation_at,
    fit=lambda train: sum((r.label for r in train), D(0)) / len(train),
    predict=lambda mean, test: [mean for _ in test],
    score=lambda actual, predicted: {
        'mae': sum((abs(a-p) for a, p in zip(actual, predicted)), D(0)) / len(actual)
    },
)
```

## Daily cash-account simulation

`backtest.run_backtest(sessions, targets, assumptions)` uses explicit aware
session open/close timestamps, historical eligible membership with availability,
and per-symbol unadjusted executable open/close prices and timestamped liquidity
volume estimates (`Bar.liquidity_volume`, `Bar.liquidity_available_at`).
`Target` contains decision time, latest information availability and absolute
integer share counts. Information must be available by decision time. Each target
executes at the first supplied session open **strictly after** decision time.
Multiple targets for one execution session, targets beyond the supplied horizon,
future membership and targets outside the historical eligible universe fail.
Omitted positions target zero on that rebalance; sessions without targets hold.

`Assumptions` requires initial cash, brokerage per nonzero fill, proportional
fee rate, adverse slippage rate, volume participation cap, periods per year and
nonempty historical-universe/price provenance descriptions. All money/rates use
finite Decimal values. Arithmetic uses an explicit 34-digit, half-even Decimal
context independent of caller precision, rounding and traps.

- Executed price = open * (1 + slippage) for buys, open * (1 - slippage) for sales.
- Fees = absolute executed notional * fee_rate, plus brokerage_per_fill.
- Shares are capped at floor(liquidity_volume * participation_rate). This is a
  capacity estimate known by the execution open, such as previous-session volume
  or contemporaneously published opening liquidity. Later full-day volume is
  rejected by its availability timestamp. It is a fill approximation requiring
  calibration, not an order-book simulator. Zero estimated volume prevents fills.
- Sales occur before buys; symbols have deterministic alphabetical cash priority.
  Buys are reduced to affordable whole shares including costs. Sales that cannot
  cover their costs plus available cash are not filled. No shorts or borrowing.
- Unfilled differences are reported and expire at session end. They require a
  new target to retry. There is no automatic final liquidation; final holdings
  are marked to close, and hypothetical exit costs are not deducted.

Historical membership must be reconstructed from contemporaneous evidence and
include later delisted/failed securities while eligible. Supply every scheduled
session, all member bars and prices for existing holdings, even after universe
removal. Missing prices fail instead of dropping losing positions. Explicit exit
targets and terminal recovery bars must represent delisting outcomes; a zero
close is allowed for worthless holdings. A zero open/close terminal recovery bar
allows a zero-proceeds sale subject to its liquidity and explicit costs; buys
at zero are rejected.
Membership removal alone does not liquidate an asset. The framework cannot detect
a caller who supplies only today's survivors, omits a session or misstates
provenance. Preserve the input bundle, hashes, calendar, signal/model version and
availability evidence alongside results to reproduce a run.

Use consistent currency/units throughout. This version has no automatic currency
conversion, exchange fee schedule, taxes, lot/tick rounding, corporate actions,
dividends, financing, borrow, spread model, order book, benchmark or market-impact
estimator. Supply economically consistent prices/targets or restrict the period
to one without unsupported corporate actions. Previously adjusted archive data
is not automatically suitable for executable prices. Rates are explicit modeling
assumptions and require calibration; defaults do not hide free execution.

Results retain the version and assumptions, signed fills, cash/holdings/equity
marks, period returns, drawdowns, unfilled quantities and total costs (brokerage,
fees and absolute slippage from open). Return = final equity / initial cash - 1.
Drawdown = equity / running peak - 1, with initial cash included in the peak;
max_drawdown is its nonnegative maximum loss. Returns include the first session
relative to initial cash. Risk uses sample standard deviation of all session
returns times sqrt(periods_per_year); zero-rate Sharpe = mean return *
periods_per_year / annualized volatility, or None when volatility is zero.
After total equity loss, a flat zero-equity session has zero return. Any rebound
from zero equity is rejected because its percentage return is undefined.
The caller must supply equal-frequency sessions for annualization. No annualized
compound return or inference of statistical significance is made.

Verification: `python3 -B -m unittest scrapper.tests.test_quant_research -v`
uses synthetic Decimal reference ledgers, multiple evaluation windows, label
purging, hidden labels, future-data counterexamples, delisting/missing-price
failures, partial fills, zero liquidity and cash limits. No migrations are needed.
Rollback removes this opt-in package, its tests and this documentation.
