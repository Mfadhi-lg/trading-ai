# Finance-only intelligence V1: bounded four-company pilot

Working deterministic prototype, isolated from operational code. Read the
[acceptance decision](ACCEPTANCE.md) before using any result. This directory is
the entire change; no registration, scheduling, production API or workflow
integration is included.

Reports:

- [TVS Motor — present in supplied Focus reference](reports/TVSMOTOR.md)
- [Oracle Financial Services Software — present](reports/OFSS.md)
- [Bajaj Auto — absent](reports/BAJAJ-AUTO.md)
- [India Cements — absent](reports/INDIACEM.md)

The reference is the supplied Q1 FY2027 text, not a fresh independently verified
live list. Absence is not a reviewed rejection. The two present identities also
match the retained resolved company-ID registry. Companies were selected before
calculations, from B0's non-bank cohort, for long common March-year-end history,
retained cell evidence and contrasting businesses. OFSS sells software to
financial institutions; it is not classified as a bank/NBFC. No labels are used
to train or choose financial thresholds.

From repository root, Python standard library only:

```sh
python3 -m experiments.finance_only_v1.pilot --replay
python3 -m unittest discover -s experiments/finance_only_v1/tests -v
python3 -m unittest scrapper.tests.test_company_quarter_features scrapper.tests.test_financial_feature_ownership -v
```

Replay reads the retained four-company evidence bundle and regenerates reports,
without connecting to any database. One reader-denial test is skipped if the
historical snapshot is unavailable; all calculation and evidence tests work
offline. For a full re-extraction from the exact preserved snapshot:

```sh
python3 -m experiments.finance_only_v1.pilot
```

The reader permits only the explicitly pinned AR-01.3B historical snapshot path,
requires non-writable permissions and absent SQLite sidecars, checks its full
SHA256 and 76-entry migration ledger ending at schema 075, sets `sqlite3.Row`,
`query_only=ON` and memory temp storage, and installs a SELECT-only authorizer.
Immutable mode is used solely for this verified cold snapshot. It never opens
production or a configured default path, calls migrations or changes journal
mode. Full pre/post hashes and filesystem metadata are retained in evidence.
The 14.6 GB snapshot is not copied into this experiment.

`company_quarter_features` is the canonical selection boundary: explicit source,
scope, industrial applicability and decision cutoff choose the accepted parse
with acceptance-event/blob lineage. The existing facade exports bounded quarter
features but does not export full annual histories. A local SELECT-only adapter
therefore reads financial observations and source cells **only from that chosen
parse**, after the facade has checked its scope, timestamps and acceptance.
This adapter does not implement a competing accepted-version selector or alter
the shared interface. Source identifier URLs can point at a consolidated alias;
the actual fetch URLs, parse scope and observation scope are retained separately.

`reports/evidence.json` is a reproducibility/audit export, not a new canonical
financial database. It includes original display values, metric codes, units,
missing kinds, period labels, IDs, accepted-version provenance, source cells,
classification rationale and hashes of Focus evidence. `reports/report.json`
contains every annual/quarterly derived result, formula, null status and input
observation IDs. Join those IDs back to evidence for lineage. Source records
remain unchanged. Do not treat the export as publication-cleared data.

## What was inspected

The snapshot is the October 8 preservation baseline documented in the October 22
handoff, with financial captures through early October. Decision cutoff is
2026-10-22 00:00 UTC; that does not make this an October 22 production census.

The availability diagnostic found latest accepted Screener pointers for 2,189
standalone and 986 consolidated companies, each with all five statement sections.
These are **pointer diagnostics**, not a facade-certified eligible universe.
They exclude unresolved/sentinel source periods. They span annual period ends
2003–2026 standalone and 2002–2026 consolidated, with latest statement/quarter
ends June 30, 2026. Interim June balance/cash columns classified `fiscal_year` by
the parser are not proof of annual intervals. Latest-pointer selection is not a
historical decision-time reconstruction or a global completeness guarantee.
`coverage` retains counts, period ranges and numeric counts by scope, section and
period type. `metric_inventory` retains 106 observed scope/section/code/unit
combinations across accepted pointers (includes versions, not deduplicated
company-period facts). No premium commentary or document evidence was used.

Observed fields include quarterly and annual sales, expenses, operating profit,
PBT, interest, depreciation, PAT, EPS and reported margins; equity capital,
reserves, borrowings, assets, investments, fixed assets and CWIP; operating,
investing and financing cash flows, net CF, source FCF and CFO/OP; debtor,
inventory, payable, working-capital and cash-conversion days and source ROCE.
Consolidated finance parses also have differing financing/deposit labels; no
industrial calculations are run on those companies.

Each selected parse has annual March 2015–March 2026 columns in P&L, balance,
cash flow and ratios, and 13 quarter columns June 2023–June 2026. TTM is excluded
from annual calculations. The earliest annual ROE lacks opening equity and is
null. OFSS inventory/payable days are missing; zero is not substituted. Detailed
numeric completeness, scope and identity are in each report and evidence.

## Formula and comparability contract

- Growth: `100*(current/base-1)` requires positive base; signed current losses
  produce signed declines. Zero/negative bases produce `INVALID_COMPARATOR`,
  including loss-to-profit transitions. Absolute earnings remain visible.
- CAGR: `100*((end/start)^(1/n)-1)` requires positive endpoints and every
  consecutive same-month annual column for `n=3` or `5`. It is not calculated
  across gaps, TTM or different scopes. Loss years between positive endpoints
  remain visible in the five-year consistency counts.
- Margins: operating profit/revenue and PAT/revenue, multiplied by 100; revenue
  must be positive. Provider rounded OPM is not silently used as a calculation.
- ROE: PAT divided by average opening/closing book equity (equity capital plus
  reserves), multiplied by 100. Both equity observations must be positive and
  exactly one year apart. This is standalone book-equity ROE; attribution and
  broader minority/preference-equity adjustments are not established.
- ROCE: independently calculated ROCE is `UNSUPPORTED`; current liabilities are
  not separately available. The source ROCE percentage is exposed under
  `source_roce`, with an unverified denominator definition. Equity plus debt is
  not silently substituted for validated capital employed.
- Leverage: borrowings/closing equity and borrowings/total assets; denominators
  positive and borrowings nonnegative. Cash, lease definitions and net debt are
  not established. No cash proxy is inferred from investments or net cash flow.
- Interest coverage: `(PBT+interest)/interest`, includes other income. Zero or
  negative interest is undefined, never infinity. This is not EBITDA coverage.
- CFO/PAT: same-year CFO divided by positive same-year PAT. Negative CFO is
  retained. No latest-quarter PAT or different-year cash flow substitution.
- FCF: retained source FCF. `implied_capex=CFO-source_FCF` is a separately named
  implication with unverified source FCF definition. Direct capex remains
  `UNSUPPORTED`; total investing cash flow is not substituted.
- Working capital: provider days remain labelled as source ratios. Arithmetic
  CCC is separately recomputed as debtor + inventory - payable days. Rounded
  component discrepancies are warnings; missing components remain null.
- Consistency: number of positive PAT/CFO/FCF years in the latest **continuous**
  five annual columns; missing observations suppress the affected count.
  Deterioration flags compare matched annual margins/ROE, annual revenue/PAT
  growth and latest quarterly YoY revenue/PAT. No causal claims are inferred.

Decimal precision is 50 digits during arithmetic; derived outputs round to four
decimal places using half-even rounding. Stored input precision is preserved;
derived intermediate values are also represented at four decimals. No LLM,
float-based financial calculation, imputation or inference service is used.

Annual durations are provisionally inferred from annual section labels and
consecutive March ends; exact start dates and audited twelve-month duration are
not in this schema. Changes in fiscal year, scope, split-adjusted EPS and
restatements require review. Outputs remain conditional on those limits.

## Assessment policy

Five **experimental**, uncalibrated checks: positive 3-year revenue CAGR,
positive 3-year PAT CAGR, ROE >12%, CFO/PAT >0.8, borrowings/equity <1.0. Equality
does not pass the strict thresholds. Show passed, evaluable, total and unknown
checks separately. Missing data never receives a zero score or fabricated pass.
Deterioration is reported separately; it does not alter the five-check count.
The count is a provisional financial profile, not a buy rating or certified
financial-quality classifier. Threshold sensitivity/predictive validity has not
been evaluated. No moat scoring, Purple decisions or automatic Focus actions.

Stop scope is these four companies. Broader coverage diagnostics do not produce
additional company profiles or expand the pilot.
