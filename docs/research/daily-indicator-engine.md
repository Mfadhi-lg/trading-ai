# Daily historical indicator engine

`scrapper.research.features.technical.daily_indicators` provides version
`historical-daily-indicators-v1`. It consumes a
caller-owned canonical `HistoricalStore` from
`experiments.historical_candle_archive_v1.access`; it never instantiates that
store, acquires data, writes features or opens a database itself.

This is a host-side interface requiring the repository's `experiments` package.
Current Docker images exclude that package; this module is not supported inside
those images. Packaging and production deployment remain separate work.

```python
from scrapper.research.features.technical import daily_indicators

# store is an explicitly authorized, caller-owned HistoricalStore.
features = daily_indicators(
    store, company_id=123, start='2026-10-01', end='2026-10-30',
    decision_time='2026-10-31T00:00:00+05:30',
)
```

Parameters count completed daily observations. Defaults are price_period=20,
atr_period=14, volume_period=20; all must be positive integers.

- SMA: arithmetic mean of the current and preceding n-1 closes. First n-1
  values are null.
- EMA: first close seeds the sequence; alpha=2/(n+1), then alpha*close +
  (1-alpha)*previous EMA. Available from the first close.
- True range: max(high-low, abs(high-previous close), abs(low-previous close)).
  First value is null because no previous close was supplied.
- ATR: Wilder RMA of true range, seeded by the mean of n known true ranges,
  then (previous ATR*(n-1)+TR)/n. First n output rows are null.
- RVOL: current volume / mean of the preceding n volumes, excluding current.
  First n rows and zero baselines are null. Output is a dimensionless ratio.

All calculations use Decimal and the existing shared research primitives.
Prices and ATR retain source price units; no rounding or conversion is applied.
No RSI, signals, order placement or benchmark RS is provided by this version.

The source result, including original rows, receipt revisions, quality flags,
access mode, adjustment assumptions and requested window, accompanies the
indicator rows. Parameters and formula version are explicit. Inputs are not
mutated. Seeds depend on the supplied start date: select sufficient history
before slicing output for display. There is no automatic prefetch.

Only bounded NSE daily windows within the archive calendar review coverage
(2022-01-01 through CALENDAR_REVIEW_THROUGH) are accepted. Known exclusions
reject the entire request; there is no filling, dropping or bridging missing
sessions. Missing/null/nonfinite OHLCV, inconsistent OHLC, mixed instruments,
unordered/duplicate starts and bars completing after decision_time are rejected.
Empty valid windows return empty indicators. Strict delivery is the default;
retrospective access requires mode='retrospective'. Completion alone does not
prove historical availability. The archive access API remains the authority for
calendar and receipt eligibility, and its partial calendar review and uncertified
provider adjustment flags remain visible. Corporate actions may distort values;
these features do not certify a backtest or production readiness.

Verification uses synthetic rows and hand-calculated reference vectors in
`scrapper/tests/test_technical_features.py`. No retained archive access is needed.
The package boundary is new; existing research consumers and acquisition paths
are unchanged. No migration or runtime activation is required. Rollback removes
this module, its tests and documentation.
