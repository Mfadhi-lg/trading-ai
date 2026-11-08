# Historical financial coverage audit

The `financial-coverage-audit` command produces a repeatable, read-only
coverage matrix for accepted Screener standalone statement observations.

## Run

```sh
./scraper financial-coverage-audit
./scraper financial-coverage-audit --symbol RELIANCE --symbol TCS
./scraper financial-coverage-audit --output /tmp/financial-coverage.json --csv-output /tmp/financial-coverage.csv
```

By default the command writes JSON and CSV under the configured exports
directory. It opens the existing database read-only and does not initialize
migrations, repair period labels, fetch source pages, enqueue work, or modify
accepted records.

## Coverage semantics

Each row represents one company × statement section × metric × observed period.
The period universe is derived from periods represented by at least one metric
in that company's statement section and period type. It does not invent periods
that are absent from every row; a separate source-discovery or calendar
completeness audit is needed to establish those gaps.

Statuses:

- `PRESENT`: a numeric value is present and non-zero.
- `PRESENT_ZERO`: a numeric zero is explicitly present.
- `MISSING_VALUE`: the source observation exists but carries a missing marker
  or no usable numeric value.
- `UNAVAILABLE`: the source explicitly marks the value unavailable.
- `MISSING_OBSERVATION`: other metrics establish that period in the statement,
  but this metric has no observation for it.

The original display value, decimal value, unit, scale, parser ID and acceptance
timestamp are retained in the row-level report. Missing values are never
coerced to zero.

This report covers accepted Screener standalone statement observations. It does
not certify consolidated statements, other providers, source periods never
captured, or point-in-time research eligibility. The existing
`screener-period-coverage` command remains responsible for quarter-label
mismatch and targeted quarter refresh workflows.
