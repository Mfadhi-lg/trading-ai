# Proposed AMFI / AMC Mapping Architecture

_Design only; no migrations or parsers are authorized by this document._

## Source-family boundary

Create three independent source families:

```text
amfi_portfolio_directory
  metadata registry: AMFI mf_id, AMC name, destination disclosure URLs

amc_portfolio_index
  per-AMC discovery page/API: periods, file labels, URLs, posted dates

amc_monthly_portfolio_workbook
  immutable XLS/XLSX/CSV/ZIP response and source-faithful workbook structure
```

Each family owns a manifest, parser/version, response blob, fetch observation,
parse artifact, validations and acceptance policy. An AMFI directory parse may
discover an AMC URL but never masquerades as the publisher of the AMC workbook.

## Reuse from the current architecture

Reuse unchanged:

- `companies`, `security_identifiers` for issuers/securities where appropriate;
- `response_blobs`, `fetch_observations`, `parse_artifacts`;
- `source_sections`, `source_fields`, `source_table_cells`;
- `validation_results`, `acceptance_events`, `accepted_page_versions`;
- availability semantics and immutable content hashes;
- exact decimal strings, typed units and explicit missing kinds.

Do not insert official-source data into `trendlyne_fund_observations`. That table
is source-specific and lacks security ISIN, security name and market value. Add a
generic domain layer and expose a compatibility view for downstream model inputs.

## Proposed logical tables

```text
mutual_funds(
  fund_id PK, legal_name, status
)

mutual_fund_source_identifiers(
  fund_id, source, source_fund_id, name_raw,
  valid_from, valid_to, mapping_status, evidence_parse_id
)

mutual_fund_schemes(
  scheme_id PK, fund_id, canonical_name, status
)

mutual_fund_scheme_identifiers(
  scheme_id, source, source_scheme_id, scheme_isin, name_raw,
  valid_from, valid_to, mapping_status, evidence_parse_id
)

mutual_fund_scheme_plans(
  plan_id PK, scheme_id, option_kind, direct_regular_kind,
  source_plan_id, plan_isin, valid_from, valid_to
)

scheme_classification_versions(
  classification_id PK, scheme_id, effective_from, effective_to,
  category_code, is_index_fund, is_etf, is_arbitrage_fund,
  underlying_index_raw, classification_source,
  classification_rule_version, evidence_parse_id
)

portfolio_disclosures(
  parse_id PK, fund_id, source_period, period_end,
  disclosure_frequency, publisher, source_file_label,
  source_published_at_utc, first_observed_at_utc,
  available_at_utc, availability_basis
)

portfolio_source_sheets(
  parse_id, sheet_position, sheet_name_raw, source_scheme_label,
  mapped_scheme_id, mapping_status, header_row_position,
  footer_row_position, parse_status, error_json
)

mf_portfolio_positions(
  position_id PK, parse_id, scheme_id, source_sheet_position,
  source_row_position, instrument_category_raw,
  security_name_raw, security_isin, mapped_company_id,
  quantity_display, quantity_decimal, quantity_unit,
  market_value_display, market_value_decimal,
  market_value_currency, market_value_scale_multiplier,
  portfolio_percentage_display, portfolio_percentage_decimal,
  missing_kind, first_observed_at_utc, source_published_at_utc,
  available_at_utc, availability_basis,
  UNIQUE(parse_id, source_sheet_position, source_row_position)
)
```

Source-faithful workbook cells remain in a workbook-specific raw layer or in an
extended `source_table_cells` identity that includes sheet position. Formula text,
cached values, merged cells, hidden sheets/rows, units, footnotes and totals must
be retained before normalization.

## Identity rules

1. `fund_id` identifies the mutual fund/AMC relationship, not an investment plan.
2. `scheme_id` identifies the portfolio-owning scheme.
3. Growth/dividend and direct/regular plans are children of a scheme and must not
   duplicate one underlying portfolio unless the source truly reports distinct
   holdings.
4. Security ISIN is the preferred position join key. Missing-ISIN rows retain a
   typed source instrument key and remain unmapped.
5. Company mapping uses effective-dated security identifiers. Security-name
   normalization may propose a match but cannot accept it without evidence.
6. Scheme/fund renames and mergers create effective-dated aliases, never rewrites.

## Metric compatibility mapping

| Official position field | Generic metric | Existing compatibility |
|---|---|---|
| Equity quantity | `quantity_held` | expose as `shares_held` only when `quantity_unit='SHARE'` |
| Market value | `market_value` | new metric; never map to Trendlyne `aum` |
| `% to NAV` | `portfolio_percentage` | may feed the existing percentage-point representation |
| Quantity delta | `month_change` | derived from two accepted positions |
| Quantity delta / prior | `month_change_percentage` | derived with explicit zero/absence handling |
| Scheme category | classification dimension | replaces name-based filtering; never a position metric |

The downstream model view should be source-neutral:

```text
accepted_mf_security_monthly(
  fund_id, scheme_id, source_period, period_end,
  security_isin, mapped_company_id,
  quantity_held, market_value_inr, portfolio_percentage,
  quantity_delta, change_percentage, activity_kind,
  is_index_fund, is_arbitrage_fund,
  source, parse_id, available_at_utc
)
```

This view reads only accepted versions and retains source/parse provenance. A
filter such as “exclude index and arbitrage funds” is a downstream view parameter,
not an ingestion deletion.

## Derived-version architecture

Month-over-month output should identify both parent parses:

```text
mf_position_change_versions(
  current_parse_id, prior_parse_id, scheme_id, security_identity,
  current_period, prior_period, current_quantity, prior_quantity,
  quantity_delta, change_percentage, activity_kind,
  classification_version, corporate_action_status,
  computation_version, validation_status
)
```

Recomputing after a corrected workbook creates a new derived version. The old
derivation remains auditable.

## Manifest contracts

`amfi_portfolio_directory` manifest:

- fund ID/name and AMC legal/display name;
- monthly/fortnightly/half-yearly disclosure destination URLs;
- blank destination fields as explicit source states;
- terms/copyright and observation timestamp;
- no workbook claims.

`amc_portfolio_index` manifest, one adapter per AMC:

- visible filters and selected period;
- every file label, period, URL and posted date;
- pagination/API request contract;
- content-type expectation and consolidated/per-scheme file granularity;
- unavailable, no-row, login and blocked states.

`amc_monthly_portfolio_workbook` manifest:

- workbook/sheet order, visibility and names;
- title, scheme, period and unit headers;
- every visible and hidden data column;
- position rows, subtotal/total rows, notes and formulas;
- security name, ISIN, quantity, market value and `% NAV` columns;
- derivative/debt/equity/cash section boundaries;
- explicit handling for password protection, macros and malformed files.

## Acceptance checks

- response signature matches the declared spreadsheet/archive type;
- workbook identity, AMC, period and requested URL agree;
- every non-empty sheet is captured, deferred with approval, or rejected;
- all source rows/cells are represented before normalized rows;
- normalized values reproduce displays and units exactly;
- scheme mapping is unique or explicitly unresolved;
- duplicate ISINs are permitted only with source-category/row evidence;
- `% NAV` totals are checked with source-aware tolerance and without forced repair;
- current/prior periods are distinct, adjacent when required, and independently
  accepted;
- source publication and availability timestamps follow evidence rules;
- unknown columns, units, scheme labels or classifications block automatic
  acceptance or require manual review.

## Publication and availability semantics

```text
period_end             = portfolio statement date in the workbook
source_published_at    = explicit AMC posted timestamp only
first_observed_at      = first immutable response observation
downloaded_at          = actual acquisition timestamp
available_at           = explicit source publication when defensible,
                         otherwise first_observed_at
regulatory_due_at      = derived month-end + applicable cadence deadline;
                         never substituted for publication/availability
```

The cadence rule is versioned by scheme class and effective date. A page/file
date, filename date and HTTP `Last-Modified` remain separate evidence fields until
validated.
