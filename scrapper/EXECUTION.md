# Scraper — Small Execution Chunks

_Current chunk: **E01 — Exchange universe and result discovery (complete)**_

This file turns `PLAN.md` into small build units. Each chunk should be completed,
tested, and inspected before starting the next one. Do not combine chunks merely
because their HTML is on the same page.

## Execution rule

For every chunk:

1. Start from the existing saved fixture; do not fetch during parser development.
2. Implement only the named source component.
3. Preserve source-faithful cells before adding normalized observations.
4. Add or update golden expectations and focused validation rules.
5. Run the complete offline suite.
6. Perform at most one opt-in live smoke fetch after offline tests pass.
7. Inspect the accepted JSON export manually.
8. Mark the chunk complete here. Stop before the next chunk.

A chunk is not complete when parsing “mostly works.” It is complete only when its
source rows, columns, display values, normalized values, availability metadata,
validation results, and export are tested.

## Completed foundation

| ID | Status | Deliverable | Proof |
|---|---|---|---|
| F00 | Done | Immutable blobs, fetch observations, parse artifacts and migrations | Same bytes produce multiple fetches but one blob/parse |
| F01 | Done | Company identity, versionable identifiers and verified Screener mapping | LAURUSLABS maps to Screener company `1273603` |
| F02 | Done | Quote and top ratio panel | 9 public ratios with exact display values and `Decimal` strings |
| F03 | Done | Quarterly results | 182 source cells and 156 normalized observations |
| F04 | Done | Validation, accepted pointer, acceptance history and JSON export | Accept/reject/supersede tests pass |
| F05 | Done | Availability semantics and requested/resolved period schema | Migration `003`; historical rows use `first_observed` |

## Screener chunks

### S04 — Profit and loss ✅ Done

- Parse only `section#profit-loss`’s primary annual table.
- Preserve annual and TTM columns as distinct period types.
- Normalize row values, units, missing cells and availability metadata.
- Add golden counts plus first/last Sales and Net Profit assertions.
- Add a validation that annual and TTM columns cannot collide.

Exit: accepted LAURUSLABS export contains the P&L table and all existing tests stay
green.

Proof: `section_code='pnl'` yields 182 source cells and 156 normalized observations
(144 `fiscal_year` + 12 `ttm`); TTM cells never carry a `fiscal_year`/`period_end`;
validations `profit_loss_table_present` and `pnl_annual_ttm_no_collision` pass;
export block `normalized.profit_loss`. 9 offline tests green.

### S05 — Growth blocks ✅ Done

- Parse Compounded Sales Growth, Compounded Profit Growth, Stock Price CAGR and
  Return on Equity.
- Preserve labels such as `10 Years`, `TTM`, `1 Year` and `Last Year` exactly.
- Store unavailable values as missing, never zero.

Exit: all four blocks match golden output and contain availability metadata.

Proof: the four `.ranges-table` blocks inside `section#profit-loss` yield 36
source-faithful cells (each block a distinct `table_position`) and 16
`growth_observations`. Stock Price CAGR `10 Years` is a bare `%`: stored
`value_decimal=NULL`, `missing_kind='blank'`, never `0`. Every row carries
`unit_code='PERCENT_POINT'` and `first_observed` availability metadata.
Validations `growth_blocks_present`, `growth_numeric_cells` and
`growth_unavailable_not_zero` pass; migration `004` adds `growth_observations`;
export block `normalized.growth`. Manifest marks `growth_blocks: capture`. 10
offline tests green.

### S06 — Balance sheet ✅ Done

- Parse only the balance-sheet table.
- Preserve annual periods and expandable-row markers.
- Validate `Total Assets ≈ Total Liabilities` within display-rounding tolerance.

Exit: every visible cell is exported and the balance validation is stored.

Proof: `section#balance-sheet` yields 143 source-faithful cells (13-column header +
10 rows × 13) and 120 `financial_observations` under `section_code='balance_sheet'`,
all `period_type='fiscal_year'` (no TTM) with derived `period_end`/`fiscal_year`.
The four drill-down rows (`Borrowings`, `Other Liabilities`, `Fixed Assets`,
`Other Assets`) carry `is_expandable=1` and their `schedule_key`; the expanded
schedule dataset stays a deferred component. Migration `005` adds
`is_expandable`/`schedule_key` to `financial_observations`; export block
`normalized.balance_sheet`. Validation `balance_sheet_table_present` (error) passes
and `balance_assets_equal_liabilities` (warning) passes for all 12 periods
(Total Assets == Total Liabilities); a corrupted-fixture test proves the warning
fires without blocking acceptance (`validation_status='passed_with_warnings'`).
Manifest marks `balance_sheet: capture`; parser/manifest bumped to `0.2.0`. 13
offline tests green.

### S07 — Cash flow ✅ Done

- Parse only the cash-flow table.
- Preserve negative values, `-0`, percentages and crore units.
- Validate required rows without inventing financial identities not shown by the
  source.

Exit: cash-flow golden output and numeric parsing tests pass.

Proof: `section#cash-flow` yields 91 source-faithful cells (13-column header +
6 rows × 13) and 72 `financial_observations` under `section_code='cash_flow'`, all
`period_type='fiscal_year'` (no TTM) with derived `period_end`/`fiscal_year`.
Negative crore flows keep their sign (`Cash from Operating Activity` Mar 2015 =
`-65`); the signed zero `Net Cash Flow` Mar 2019 = `-0` stays distinct from `0`
with `missing_kind=NULL`. The `CFO/OP` row is `PERCENT_POINT` even though its label
carries no `%` — the unit is now decided per row from the label plus that row's
values (`unit_for_financial_line` extended with an optional sample-values arg); its
Mar 2015 cell is `-24%` → `-24`. The three activity rows carry `is_expandable=1`
with their `schedule_key`; the expanded schedule dataset stays deferred. No
financial identity (e.g. Operating+Investing+Financing = Net Cash Flow) is invented
as a validation. Validation `cash_flow_table_present` (error) passes; export block
`normalized.cash_flow` (section position 5). Manifest marks `cash_flow: capture`;
parser/manifest bumped to `0.3.0`. 14 offline tests green.

### S08 — Annual ratios ✅ Done

- Parse only `section#ratios`’ annual table.
- Distinguish days, percentages, ratios and counts.
- Keep this table separate from the top snapshot ratio panel.

Exit: annual ratio observations cannot collide with top-panel metrics.

Proof: `section#ratios` yields 91 source-faithful cells (13-column header +
6 rows × 13) and 72 `financial_observations` under `section_code='ratios'`, all
`period_type='fiscal_year'` (no TTM) with derived `period_end`/`fiscal_year`. Units
are decided per row by a dedicated `unit_for_ratio_line` resolver — the five
turnover-cycle rows are `DAYS` (60 cells) and `ROCE %` is `PERCENT_POINT` (12
cells); the crore statement default never leaks in (`ratios` covers `DAYS`,
`PERCENT_POINT`, `RATIO`, `COUNT` only). `Working Capital Days` Mar 2015 = `-0`
stays a real signed zero, distinct from `0` (`missing_kind=NULL`). Annual ratios
land only in `financial_observations`; the top-panel `ROCE` metric stays in
`company_metric_observations` under `metric_code='roce'` while the annual row uses
`line_item_code='roce_pct'`, so the two cannot collide. Validations
`annual_ratios_table_present`, `annual_ratios_numeric_cells` and
`annual_ratios_separate_from_panel` (all error) pass; export block
`normalized.annual_ratios` (section position 6). Manifest marks
`annual_ratios: capture`; parser/manifest bumped to `0.4.0`. 15 offline tests
green.

### S09 — Quarterly shareholding ✅ Done

- Parse the quarterly shareholding table only.
- Preserve holder labels, percentages, shareholder counts and source order.
- Tag individual periods `pre_sep_2022`, `xbrl_sep_2022_plus`, or `unknown`.
- Store the Screener classification warning.

Exit: period totals are validated where complete; no row is labelled
`cross_regime`.

Proof: `section#shareholding #quarterly-shp` yields 78 source-faithful cells
(13-column header + 5 rows × 13) and 60 `shareholding_observations` under
`frequency='quarterly'`. The four holder-category rows are `PERCENT_POINT`
(48 cells) and `No. of Shareholders` is `COUNT` (12 cells); the unit is decided
per row. Percentages keep their exact two-decimal display (Promoters Sep 2023
`27.20%` → `27.20`) and counts drop the Indian comma grouping without a float
(`4,02,640` → `402640`). `holder_code` comes from the source's own
`showShareholders('<token>', …)` onclick classification
(`promoters`/`foreign_institutions`/`domestic_institutions`/`public`); the count
row maps to `no_of_shareholders`. Each period is regime-tagged from its own end
date — all 12 periods (Sep 2023–Jun 2026) are `xbrl_sep_2022_plus`; no row is
ever `cross_regime` (that stays a derived two-observation comparison for S10).
The Screener classification warning is stored with the data as a
`classification_warning` source field, not merely logged. Validations
`quarterly_shareholding_table_present`, `quarterly_shareholding_numeric_cells`,
`shareholding_no_cross_regime` and `shareholding_classification_warning_present`
(all error) pass; `quarterly_shareholding_totals_100` (warning) passes for all
12 complete periods (categories total 99.99–100.02, within a 0.1 pp
display-rounding tolerance), and a corrupted-fixture test proves the warning
fires without blocking acceptance (`validation_status='passed_with_warnings'`,
raw source value not "fixed"). Migration `006` adds `shareholding_observations`
(`frequency` is part of row identity so S10 yearly rows coexist); export block
`normalized.shareholding_quarterly` (section position 8). Manifest marks
`quarterly_shareholding: capture` and `yearly_shareholding: deferred`;
parser/manifest bumped to `0.5.0`. 17 offline tests green.

### S10 — Yearly shareholding and comparison rule ✅ Done

- Parse the yearly table separately from quarterly data.
- Add the derived `is_cross_regime_comparison` helper and tests.
- Ensure period frequency is part of observation identity.

Exit: quarterly and yearly rows coexist without overwriting one another.

Proof: `section#shareholding #yearly-shp` yields 72 source-faithful cells
(12-column header + 5 rows × 12) and 55 `shareholding_observations` under
`frequency='yearly'`, parsed by the same `_parse_shareholding_rows` helper the
quarterly table now shares. The four holder-category rows are `PERCENT_POINT`
(44 cells) and `No. of Shareholders` is `COUNT` (11 cells); percentages keep
their two-decimal display (Promoters Mar 2017 `30.64%` → `30.64`) and counts drop
the Indian comma grouping without a float (`56,722` → `56722`). Unlike the
all-post-XBRL quarterly table, the yearly table straddles the Sep-2022 boundary:
Mar 2017–Mar 2022 are `pre_sep_2022` (30 rows) and Mar 2023–Jun 2026 are
`xbrl_sep_2022_plus` (25 rows), each tagged from its own period end; no row is
ever `cross_regime`. The derived `is_cross_regime_comparison(a, b)` helper is a
property of a *pair* of observations — True only when both regimes are known and
differ (`unknown` yields False) — and is proven by comparing Mar 2022 (pre) with
Mar 2023 (post). `frequency` is part of observation identity
(`UNIQUE(parse_id, frequency, row_position, column_position)`), so quarterly (60)
and yearly (55) rows coexist in one table without overwriting one another.
Validations `yearly_shareholding_table_present`,
`yearly_shareholding_numeric_cells` and `yearly_shareholding_no_cross_regime`
(all error) pass; `yearly_shareholding_totals_100` (warning) passes for all 11
complete periods (categories total 99.99–100.02 within 0.1 pp). Export block
`normalized.shareholding_yearly` (source section position 9). Manifest flips
`yearly_shareholding: capture`; parser/manifest bumped to `0.6.0`. 18 offline
tests green.

### S11 — Document-link metadata ✅ Done

- Parse announcements, annual reports, credit ratings and concall link metadata.
- Store source titles, periods, publishers, positions, availability and URLs.
- Do not download PDFs, PPTs or recordings.

Exit: link counts and representative URLs match the fixture.

Proof: `section#documents` yields 168 ordered source-faithful fields and 168
`document_links`: 5 announcements, 13 annual reports, 1 DRHP, 6 credit ratings,
44 concall transcripts, 44 Screener AI summaries, 44 concall PPT links and 11
recording links. Source titles, period labels, publisher claims (`bse`, `care`,
and `screener` only where displayed/owned), exact absolute or relative URLs,
reporting scope and source positions are preserved. Eight visible bare concall
labels with no URL remain explicit `availability_status='unavailable'` rows;
available rows carry their URL. Every row uses `first_observed_at_utc` for
`available_at_utc` with `availability_basis='first_observed'`; partial date labels
are not promoted to publication timestamps. Migration `007` adds the metadata-only
`document_links` table; no PDF, PPT or recording is downloaded. Validations
`document_links_present`, `document_link_source_fields_match`,
`document_link_positions_contiguous`, `document_link_availability_consistent`,
`document_link_required_families_present` and `known_document_link_types` pass;
an unknown concall label is retained as `concall_other`, fails manual review and
cannot be auto-accepted. Export block `normalized.document_links` follows source
section position 10, and representative announcement, FY2026 annual-report,
CARE-rating and Jul-2026 transcript/summary/PPT/recording URLs match the fixture.
Manifest marks `document_links: capture`; parser/manifest bumped to `0.7.0`.
20 offline tests green, and the accepted JSON export was manually inspected.

### S12 — About, links, pros and cons ✅ Done

- Capture company description, website/NSE/BSE links, About text, and source Pros
  and Cons text.
- Mark commentary as source-generated; do not treat it as a numeric fact.

Exit: source-faithful export includes the visible text in source order.

Proof: the company-profile source section contains 6 ordered fields: the page
description (explicitly `metadata`), website/BSE/NSE links from the canonical
desktop rendering, the visible About prose, and its `[1]` source URL. Responsive
mobile link duplicates are not emitted. Normalized `company_profile_links`
contains 4 rows (`website`, `bse`, `nse`, `about_source`) and preserves the BSE
code/NSE symbol plus first-observed availability metadata. Normalized
`company_text` contains 9 non-numeric text observations: description, About, 1
Pro, 4 Cons, the machine-generation disclosure, and its conditional methodology
tooltip. Every Pro/Con is marked `content_origin='source_generated_commentary'`
and `is_source_generated=1` only when the source disclosure says they are machine
generated; a changed-disclosure fixture proves the focused validation blocks
acceptance while preserving embedded values such as `19.0%` as prose. Validations
`company_about_text_present`, `company_profile_links_present`,
`pros_cons_text_present`, and `pros_cons_source_generated` pass. Migration `008`
adds `company_profile_links` and `company_text_observations`; export blocks
`normalized.company_profile_links` and `normalized.company_text`. Manifest marks
`about` and `pros_cons` as `capture`; parser/manifest bumped to `0.8.0`. 22 offline
tests green, and the accepted JSON export was manually inspected.

### S13 — Peer table ✅ Done

- Confirm the first-party request that supplies the peer table.
- Add a separate immutable response/fetch/parse artifact if it is a supplemental
  request.
- Parse it only after saving a dedicated fixture.

Exit: peers are either captured and tested or explicitly marked unavailable with
evidence; a loading placeholder is never accepted as peer data.

Proof: Screener's first-party `company.customisation` JavaScript reads
`data-warehouse-id="12723395"` and requests
`/api/company/12723395/peers/` through an XHR carrying
`X-Requested-With: XMLHttpRequest`. The main fixture's
`Loading peers table ...` node remains evidence of the placeholder and is never
parsed as data; a dedicated saved peer-response fixture supplies the parser.
The supplemental response yields 110 source-faithful cells (11 headers + 8 peer
rows × 11 cells + 11 median cells) and 81 normalized observations (8 companies ×
9 metrics + 9 median metrics). Source company IDs, names, links, source order,
display decimals, typed decimal strings and header-derived units/scales are
preserved; the LAURUSLABS row maps to Screener company `1273603`, and the
`Median: 156 Co.` row remains a distinct `row_kind='median'` summary.
Migration `009` adds `resource_kind`, `supplemental_parse_links` and
`peer_observations`: the peer HTML has its own immutable blob, fetch observation,
`screener-peer-table` parse artifact, source section and validation results, then
links to (rather than masquerades as) the company-page artifact. The accepted
export merges `peer_table` at source position 4, exposes
`provenance.supplemental_resources` and `normalized.peers`, and retains
first-observed availability metadata. Validations
`peer_table_supplement_present`, `peer_table_present`,
`peer_table_not_placeholder`, `peer_table_numeric_cells`,
`peer_table_subject_match` and `known_peer_metrics` pass; a loading-placeholder
parser test and a mismatched-subject fixture prove partial/incorrect data cannot
be accepted. Manifest and parsers bumped to `0.9.0`. 25 offline tests green, and
the accepted JSON export was manually inspected.

### S14 — Screener manifest closure ✅ Done

- Compare the source manifest with the complete page component-by-component.
- Decide chart series and expandable schedules individually: capture now or record
  an explicitly approved deferral.
- Add a manifest-coverage test.

Exit: no visible component is silently omitted.

Proof: manifest `1.0.0` inventories the saved page component-by-component with
76 explicit decisions across the top data card and all 11 main sections, 12
top-level tables, 2 company-profile prose blocks, 4 document families, 12 data
controls, 6 chart choices, 15 financial schedule drill-downs, 8 shareholder
drill-downs and 5 linked panels/actions. Every decision has one of the four
contract statuses (`capture`, `metadata_only`, `conditional`, `deferred`) plus
its source; conditional rows state their access condition, and every deferred
row references one of five affirmative S14 deferral records with a rationale.
The visible Key Points block and login-gated Insights table are therefore no
longer implicit omissions. Chart choices are deferred individually because the
main HTML contains only an empty canvas and request controls; each expandable
financial/shareholder dataset is likewise deferred individually until its own
immutable supplemental fixture exists. The manifest loader rejects invalid
statuses, missing sources/conditions and unapproved deferrals. The fixture audit
compares regions, profile prose, top-level tables, document lists, filters/tabs,
chart requests, financial schedules, shareholder drill-downs and linked
components; mutation tests prove a new section or schedule fails coverage.
Manifest bumped to `1.0.0` (parsers remain `0.9.0` because no parsing behavior
changed). 27 offline tests green; an accepted JSON export using manifest `1.0.0`
was manually inspected (`40` quality rules, all passed).

### S15 — Standalone scope ✅ Done

- Save a LAURUSLABS standalone fixture.
- Run the completed Screener parser against standalone data.
- Verify consolidated and standalone observations and accepted pointers coexist.

Exit: no key collision or scope mismatch is possible.

Proof: dedicated saved fixtures now cover the LAURUSLABS standalone company page
(`88c51f54…`) and its scope-specific first-party peer response for warehouse
`12723394` (`00dd9943…`). The completed parser identifies the page as
`reporting_scope='standalone'` while retaining the same Screener company ID
`1273603` and canonical consolidated URL. The standalone fixture yields 576
financial observations (156 quarterly + 156 P&L + 120 balance-sheet + 72 cash-flow
+ 72 annual-ratio), 16 growth observations, 115 shareholding observations and 168
document links; its peer supplement yields 110 source cells and 81 observations.
The source-faithful Pros and Cons section retains both headings even though the
standalone Pros list is empty, and preserves the three disclosed machine-generated
Cons without inventing a Pro. A single-database test proves consolidated and
standalone parse artifacts, observations, peer artifacts, acceptance events and
accepted pointers coexist under distinct scope keys. Acceptance now checks the
parse's intrinsic scope even for a reused artifact; a consolidated parse presented
as standalone is rejected and cannot create a standalone pointer. Canonical source
identity is taken from the page's canonical link, so processing standalone does not
overwrite the company mapping with a request alias. Company parser bumped to
`1.0.0`; manifest `1.1.0` covers both scope fixtures (peer parser remains `0.9.0`).
30 offline tests green; the accepted standalone JSON export was manually inspected
with 40 quality rules passing.

### S16 — Screener irregular fixture ✅ Done

- Add one company with missing/short history, unusual rows or unavailable values.
- Fix only demonstrated assumptions; do not create a generic parser framework.

Exit: both companies pass offline tests with explicit missing-value behavior.

Proof: dedicated public fixtures cover NTPC Green Energy consolidated
(`source_company_id='1285166'`, warehouse `129816617`) and its first-party peer
fragment. Its deliberately short source history yields 132 quarterly, 60 P&L,
40 balance-sheet, 24 cash-flow and 24 annual-ratio observations; quarterly
shareholding has 7 periods and yearly shareholding only 3. The fixture proves
four distinct unavailable cases without inventing zero: 11 blank quarterly Raw
PDF cells, 2 blank P&L TTM cells, 9 blank annual-ratio cells, and 9 growth cells
whose exact display is a bare `%`; every normalized value is `NULL` with
`missing_kind='blank'`. Its Documents section legitimately omits the whole
Concalls list, so no unavailable concall row is fabricated. Manifest `1.2.0`
marks Concalls conditional and the deferred company-trades action's page presence
conditional; its audit permits those demonstrated absences while still rejecting
unknown document families and required-component omissions. Automatic validation
continues to require announcements, annual reports and credit ratings, while known
concall types are validated whenever present. The supplemental peer fixture yields
99 source cells and 72 observations, including 9 subject-company metrics. A single-database test
proves LAURUSLABS and NTPCGREEN coexist as distinct accepted companies. Company
parser `1.0.0` and peer parser `0.9.0` remain unchanged because parsing required
no fix; package `0.1.4` and manifest `1.2.0` carry the acceptance/contract change.
32 offline tests green; the accepted NTPCGREEN JSON export was manually inspected
with all 40 quality rules passing.

## Trendlyne chunks

### T01 — RBA mapping, fetch and resolved month ✅ Done

- Add verified mapping for Trendlyne company `357598` and symbol `RBA`.
- Fetch and store one immutable response blob.
- Parse the selected page month before any fund rows.
- Store `requested_source_period=latest` and canonical
  `resolved_source_period=YYYY-MM`.

Exit: identity/month mismatch rejects the parse; `latest` never enters an accepted
pointer key.

Proof: the verified mapping binds Trendlyne company `357598`, NSE symbol `RBA`,
slug `restaurant-brands-asia-ltd` and canonical latest URL. One public response
was saved as a 163,670-byte immutable blob with SHA-256 `ce65c6cb…0c065`; the
source-faithful T01 parser emits only 5 identity fields and 3 selected-month
fields and intentionally does not touch the fund table. The displayed month
button, active month link and page heading independently agree on `Jun 2026`, so
the fetch stores `requested_source_period='latest'` and
`resolved_source_period='2026-06'`. Six blocking identity/month validations pass.
Mutated company-ID and active-month fixtures both fail validation, create rejected
acceptance events and leave no accepted pointer. The accepted pointer is keyed by
`2026-06`; a focused assertion proves no `latest` pointer exists. Migration `010`
adds the intrinsic `trendlyne_monthly_pages` record; parser and Trendlyne manifest
are `0.1.0`, package `0.2.0`. 38 offline tests green, and the accepted RBA JSON
export was manually inspected.

### T02 — Fund-table source cells ✅ Done

- Parse headers, merged cells, scheme links/IDs and every visible fund row.
- Preserve current/comparison month columns, dashes, blanks and zeros.
- Add source-faithful golden counts.

Exit: every visible table cell is represented in source order.

Proof: the saved RBA table yields 234 ordered source-faithful cells: 10 physical
header cells across two header rows, 216 cells across all 27 visible fund rows,
and 8 footer cells. Logical placement retains the merged `MF` header
(`rowspan=2`), the `Jun-2026` group (`colspan=5`) and the `May-2026` comparison
group (`colspan=2`), while leaf columns receive period-qualified labels so the two
`Shares Held` and `Month Change %` columns cannot collide. Every fund row has all
8 cells plus a unique numeric source scheme ID parsed from and checked against its
exact Trendlyne NAV URL; the first/last IDs are `6770`/`3289532`. Exact displays
retain `-`, `-%`, blanks, `0`, `0%`, comma grouping and signs, including the raw
footer total `-8,412,818`; no values are normalized in this chunk. Migration
`011` adds source-row scheme identity metadata; export section
`trendlyne_fund_table` and `source_faithful.fund_rows` expose the accepted rows.
Validations `trendlyne_fund_table_present`,
`trendlyne_fund_table_known_structure`,
`trendlyne_fund_source_cells_complete`, `trendlyne_fund_scheme_identity` and
`trendlyne_fund_header_months_match` pass; malformed-row, missing-scheme-link and
header-month mutation tests prove partial or shifted data cannot be accepted.
Trendlyne parser/manifest bumped to `0.2.0`, package to `0.2.1`; manifest advances
only `fund_table_cells` to `capture`. 42 offline tests green, and the accepted RBA
JSON export was manually inspected with all 11 quality rules passing.

### T03 — Normalized fund observations ✅ Done

- Normalize AUM, AUM percentage, shares, month change and change percentage.
- Add availability metadata to every observation.
- Retain all funds; do not permanently filter index/arbitrage schemes.

Exit: normalized rows reproduce the source display values and units.

Proof: all 27 source fund rows are retained and yield 189 normalized
`trendlyne_fund_observations` (7 per fund): 27 AUM, 27 AUM-percentage, 54
shares-held, 27 month-change and 54 month-change-percentage observations. The
selected Jun-2026 period contributes 135 observations and the May-2026 comparison
period contributes 54; both retain their exact source period labels and add
canonical `YYYY-MM`, month-end dates, `period_type='month'` and an explicit
current/comparison role. AUM is `INR_CRORE` with scale `10000000`, percentage
values are `PERCENT_POINT`, and shares/change values are `COUNT`; comma grouping
is removed only from `value_decimal`, while `display_value` remains exact.
Twelve source dashes (including `-%`) are `value_decimal=NULL` with
`missing_kind='dash'`; real zeros remain numeric zero and negative values keep
their sign. Every observation uses the immutable response's
`first_observed_at_utc` for `available_at_utc` with
`availability_basis='first_observed'` and no invented publication timestamp.
Migration `012` adds the normalized table; export block
`normalized.fund_observations` preserves all 27 scheme IDs, including source
index/ETF names without applying a permanent filter. Validations
`trendlyne_fund_observations_complete`,
`trendlyne_fund_observations_match_source_cells`,
`trendlyne_fund_observation_metadata`,
`trendlyne_fund_observation_numeric_values` and
`trendlyne_fund_observation_availability` pass; a corrupted non-numeric cell is
preserved as `missing_kind='parse_error'`, blocks acceptance, and cannot create a
pointer. Trendlyne parser/manifest bumped to `0.3.0`, package to `0.2.2`; manifest
advances only `fund_observations` to `capture`. 46 offline tests green, and the
accepted RBA JSON export was manually inspected with all 16 quality rules passing.

### T04 — Totals and anomaly validation ✅ Done

- Compute buyers, sellers, holders and net change from fund rows.
- Store headline, table total and computed total independently.
- Emit the known RBA sign disagreement as an accepted warning.

Exit: computed net change is reproducible and headline prose is never canonical.

Proof: the visible headline is preserved as an
`untrusted_source_summary` field and its claims are stored independently from the
footer totals and row-derived values. All 27 current-period fund rows reproduce
1 buyer (`month_change > 0`), 10 sellers (`month_change < 0`), 25 holders
(non-missing current `shares_held`, including displayed zero), and net change
`-8412818` using `Decimal`; the footer independently stores total shares
`155895172` and net change `-8412818`. The visible headline's unsigned
`8,412,818` becomes the separate non-canonical claim `8412818`, never a corrected
or canonical total. Migration `013` adds `trendlyne_mf_page_summaries` with exact
headline/footer displays, typed strings, computation-basis metadata and
first-observed availability. Validations prove source/normalized agreement,
recompute all four canonical activity values, reconcile both footer totals to
fund rows, and enforce headline non-canonicality. The known headline sign
disagreement is stored as failed warning
`trendlyne_headline_net_change_matches_computed`, so the artifact is accepted as
`passed_with_warnings`; a corrupted footer-sign fixture fails the blocking table
total rule and cannot be accepted. Export block `normalized.monthly_summary`
keeps headline, table and computed values together but explicitly distinguished.
Manifest and parser bumped to `0.4.0`, package to `0.2.3`; embedded multi-month
charts and next-month insight cards remain explicit deferrals and are not used in
T04 arithmetic. 48 offline tests green, and the accepted RBA JSON export was
manually inspected with 24 quality rules (23 passed, the one expected warning
failed).

### T05 — Month history ✅ Done

- Parse all month links.
- Add one older-month fixture and an entering/exiting-fund case.
- Confirm resolved periods and accepted pointers are month-specific.

Exit: two months coexist; neither overwrites the other.

Proof: both saved RBA fixtures expose all 91 ordered month links from Jul 2026
through Jan 2019, with exact display text/URLs, parsed `YYYY-MM` periods, verified
Trendlyne company ID/symbol/slug, active state and first-observed availability.
Migration `014` adds `trendlyne_month_links`; the source-faithful export adds its
own 91-field `trendlyne_month_history` section and normalized `month_history`
block. The new immutable May-2026 fixture is 178,593 bytes with SHA-256
`78a636d5…2892ba`; it resolves independently to `2026-05` and demonstrates that
historical pages can expose more comparison history than the latest page. Its
10-column table preserves 293 source cells and 243 normalized observations: 135
for May plus 54 each for Apr and Mar. The strict table contract now accepts one
current five-metric group plus one or more complete two-column comparison groups;
partial or shifted groups still fail. The fixture retains a real fund exit for
Edelweiss Equity Savings (`source_scheme_id='22082'`): Apr shares `599,374` → May
shares `0`, with May change `-599,374`; zero remains numeric, not missing. Explicit
historical ingestion accepts canonical `YYYY-MM` requests, converts them only for
the source URL, and rejects a response that resolves to a different month. A
single-database test proves accepted `2026-05` and `2026-06` pointers and exports
coexist with different parse IDs, while no `latest` pointer is created.
Validations `trendlyne_month_history_present`,
`trendlyne_month_history_source_fields_match`,
`trendlyne_month_history_identity`, `trendlyne_month_history_periods`,
`trendlyne_month_history_active_selected` and
`trendlyne_month_history_availability` pass for both fixtures. The accepted May
export has 30 quality rules: 29 pass and only the known headline-sign warning
fails; its row-derived/table net change is `-1387808` while the untrusted headline
claim remains `1387808`. Trendlyne parser/manifest bumped to `0.5.0`, package to
`0.2.4`. 53 offline tests green, and the accepted May JSON export was manually
inspected.

## Pilot and expansion chunks

### P01 — Quality report ✅ Done

- Export section status, validation failures/warnings, unknown labels, row counts,
  hashes, parser versions and accepted versions.

Exit: one report explains every accepted, rejected or manual-review result.

Proof: `python3 -m scrapper.cli quality-report` writes one schema-versioned JSON
report over the append-only `acceptance_events` history. Every result includes
its decision reason and current-pointer status; source-content SHA-256; parser and
manifest versions; per-section parse status, errors and source field/cell/table/
row/column counts; normalized table counts; failed error/manual-review rules;
failed warning rules; and directly queried unknown canonical labels. Current
accepted versions are exported separately with their source/scope/resolved-period
keys, versions and hashes. Supplemental peer parses retain their independent
hash, version, sections, counts and validations instead of masquerading as the
main page. A focused golden fixture contains two accepted results (including the
known non-blocking Trendlyne headline warning), one rejected Trendlyne identity
mismatch and one Screener unknown-document-label manual review; the report
classifies them `accepted=2`, `rejected=1`, `manual_review=1` while preserving the
underlying rejection action. The report body has its own reproducible SHA-256.
Package version bumped to `0.2.5`; 54 offline tests green, the CLI report and
accepted/manual/rejected golden projection were manually inspected, and no live
fetch was performed.

### P02 — Five-company pilot ✅ Done

- Select five structurally varied Screener companies.
- Add Trendlyne mappings only when verified.
- Fetch current pages once and inspect exports manually.

Exit: zero silent missing sections and zero identity mismatches.

Proof: the bounded current-page pilot contains five structurally varied
consolidated companies: LAURUSLABS (mature manufacturer/full document history),
NTPCGREEN (short history, explicit blanks and no Concalls list), HDFCBANK (banking
Revenue statement, no Borrowings drill-down and Government holders), TCS
(asset-light services with Government holders), and ETERNAL (young loss-history
platform with Others/Government holders and no Credit ratings list). Each main
page and its first-party peer fragment was fetched once on 2026-08-04, stored as
an immutable blob, copied into the offline P02 fixture set and pinned by SHA-256
in `p02_five_company_expected.json`. No Trendlyne mapping was added for these
companies because P02 did not independently verify one.

The pilot demonstrated that Credit ratings, Sales versus Revenue, Borrowings and
shareholder drill-down classifications vary legitimately by company. Screener
manifest `1.3.0` records those variants as conditional without permitting unknown
components. Manifest coverage is now the blocking
`screener_manifest_coverage` acceptance rule rather than a test-only audit. A
saved-blob reprocessing command promoted the original bytes under the new
manifest without issuing another HTTP request or inventing a fetch observation.
All five current pointers use company parser `1.0.0`, peer parser `0.9.0` and
manifest `1.3.0`; all have `validation_status='passed'`, zero failed quality
rules, passing identity checks and complete 14-section exports. The five exports
and unified quality report were manually inspected. Package `0.2.6`; 57 offline
tests green.

### P03 — Trendlyne 6–12 month pilot ⏸ Frozen

Status: **frozen by operator**. Orchestration and offline proof are preserved, but
no remaining live batch is authorized. Do not issue further Trendlyne requests,
expand or redistribute its fixtures, or treat P03 completion as a prerequisite
for the official-workbook path.

- Print the exact request plan first.
- Backfill only pilot companies in bounded batches.
- Resume safely after interruption.

Exit: request counts, month coverage and anomalies match the quality report.

Current proof: migration `015` adds immutable P03 plans and resumable request
items. `trendlyne-pilot-plan` is read-only by default and prints all exact URLs;
`--persist` registers the same plan without fetching. `trendlyne-pilot-backfill`
prints the plan before network activity and attempts at most three non-accepted
months per invocation. Accepted pointers are reconciled before each batch, and a
simulated hard interruption proves a `running` month resumes while an accepted
month is not fetched again. Quality-report schema `1.1.0` reports plan request
counts, attempts, item state, accepted/missing month coverage, stored validation
anomalies and pointer mismatches. The registered RBA plan covers Jun-2026 through
Jul-2025: Jun-2026 and the saved May-2026 fixture are accepted, 10 months remain
pending, and the known May headline-sign disagreement is the sole stored pilot
warning. Package `0.2.7`; 60 offline tests were green at the original handoff.
P03 stays frozen indefinitely unless the operator explicitly reauthorizes it.

### P04 — Trendlyne universe expansion ⏸ Frozen

- Preserve the design only; do not run it.
- Do not add mappings, requests, fixtures, or downstream dependence.

Exit: none while frozen.

## Official AMC workbook chunks

These chunks supersede P03 as the active acquisition plan. They use only
operator-supplied local XLS/XLSX files. No AMC crawler, browser automation, or
Trendlyne request is part of this sequence.

### M01 — Import foundation ✅ Done

- Add `scrapper/store/mf_import/{inbox,accepted,rejected,quarantine}` to the
  documented store layout while keeping generated contents ignored by version
  control.
- Add migrations for immutable workbook blobs, import observations, sheet
  metadata, source cells, state history, and accepted workbook pointers.
- Add safe XLS/XLSX type detection, SHA-256 hashing, file-size limits, duplicate
  detection, and rejection of macro execution/external-link evaluation.
- Implement `import-mf` and `import-mf-directory --dry-run` command envelopes.
- Dry-run must make no database, copied-file, or state transition writes.

Exit: a synthetic workbook is hashed and inventoried idempotently; dry-run prints
the exact proposed files and decisions; invalid content is quarantined without a
partial parse.

Proof: migration `016` adds content-addressed workbook blobs, append-only import
observations and state events, sheet/cell inventory tables, scheme identities and
accepted workbook pointers. `import-mf` safely identifies signatures instead of
trusting extensions, hashes bytes, inventories XLSX sheet order/names/visibility
from bounded package XML, stores legacy XLS only as an explicitly unparsed inbox
input, and never loads or evaluates workbook formulas. File, ZIP entry,
uncompressed-size, compression-ratio, encrypted-member, path traversal, XML
DTD/entity, CRC, macro and external-relationship checks run before inventory;
unsafe or malformed content is copied to quarantine with zero sheet/cell rows.
Re-importing the same publisher/hash reuses one immutable blob, import record and
sheet inventory while appending a `duplicate` observation.
`import-mf-directory --dry-run` infers the required
`<publisher>/<YYYY-MM>/` layout, emits sorted exact
hashes/destinations/decisions, and opens an existing database read-only without
creating a database, copied file or transition. The generated
`mf_import/{inbox,accepted,rejected,quarantine}` directories remain covered by
the existing `store/` ignore. Package `0.3.0`; 65 offline tests green. The
synthetic two-sheet CLI JSON was manually inspected and dry-run proved
`db_exists=false`. No network fetch was performed.

### M02 — PPFAS workbook structure and source-faithful cells ✅ Done

- Use the first supplied PPFAS workbook to version a publisher manifest.
- Verify CLI publisher/month against workbook evidence.
- Detect every relevant sheet, scheme identity, merged header, portfolio-table
  boundary, source total, formula, and required column.
- Preserve every in-scope cell with sheet/row/column coordinates, raw value,
  display value, formula, number format, and source order.
- Treat non-portfolio sheets as explicitly classified, not silently skipped.

Exit: the workbook reproduces a golden sheet/table/cell inventory; structural
drift and partial tables go to quarantine.

Proof: the official June-2026 consolidated workbook was obtained from PPFAS's
first-party Portfolio Disclosure page and pinned as fixture SHA-256
`b0e6b22a…d24e`. Although the publisher names it `.xls`, its bytes are a safe
OOXML package; manifest `0.1.0` records that publisher-specific format exception
without weakening generic extension/signature checks or evaluating formulas.
All seven visible scheme sheets (`PPFCF`, `PPLF`, `PPTSF`, `PPCHF`, `PPAF`,
`PPDAAF`, `PPLCF`) are explicitly classified as portfolio sheets; the fixture has
zero silently skipped/non-portfolio sheets. Every sheet independently identifies
`June 30, 2026`, and the detected publisher/month must agree with the CLI.

The source-faithful layer stores all 16,467 declared worksheet cells in sheet,
row and column order, including 7,942 non-empty cells, exact raw/cached values,
formatted display values, formulas, data types, number formats, coordinates and
field roles. It separately preserves 70 merged ranges, 28 formulas with their
cached displays, 19 primary/derivative/supporting table boundaries and every
source subtotal/total/GRAND TOTAL row. Migration `017` adds table, merge,
validation and source-order metadata; nine blocking structural rules pass. Wrong
publisher/month, changed sheet inventory and a removed required ISIN column all
quarantine atomically with zero partial cells. The real workbook is inventoried
as import `1` in `inbox` state (normalization and acceptance remain M03–M04), the
CLI inventory was manually inspected, package `0.3.1`; 70 offline tests green.
No Trendlyne request was issued.

### M03 — Holdings normalization and ISIN matching ✅ Done

- Normalize publisher, scheme, disclosure month, security name, ISIN, quantity,
  market value, NAV percentage, and instrument type.
- Use exact decimal strings and workbook-declared currency/scale.
- Validate ISIN structure and match securities primarily by ISIN.
- Add a versioned, auditable name fallback that cannot auto-accept ambiguity.
- Retain unmatched holdings while excluding them from stock features.

Exit: normalized holdings reconcile one-to-one with accepted source rows and no
float, name heuristic, or missing ISIN can silently create a company match.

Proof: the official June-2026 workbook yields 727 normalized instrument rows in
source order: 593 primary portfolio holdings and 134 derivative positions across
all seven schemes. Display values become exact decimal strings without binary
float conversion; market values are explicitly `INR_LAKH` with scale `100000`,
NAV/AUM percentages remain percentage points, `$0.00%` retains its
`less_than_0_01` qualifier, blank TREPS quantities remain blank, and short futures
retain their parenthesized negative signs. All 586 supplied ISINs pass the
ISO-6166/Luhn rule and converge to 353 security identities; repeated ISINs across
schemes reuse one identity. The seven primary TREPS rows and all 134 derivatives
have no ISIN, remain stored, and are excluded from security-resolved and future
stock-feature views.

Migration `018` adds holdings, security identities/identifiers, valid-ISIN name
evidence, appendable match decisions and resolved/feature-eligible views. Every
holding has one current audited decision. The fallback rule
`exact_normalized_name_v1` can match only one security already anchored by valid
ISIN evidence; a missing-ISIN mutation proves the unique case, a two-candidate
mutation remains unmatched with `manual_review`, and an invalid-checksum mutation
is retained but fails blocking validation and cannot match. Eight focused M03
quality rules pass on the official fixture. The deterministic inspection export
contains 7,002 source row cells plus all 727 normalized holdings; it was manually
inspected while the import correctly remains `inbox` because acceptance belongs
to M04. PPFAS manifest `0.2.0`, import-plan schema `1.2.0`, package `0.3.2`;
75 offline tests green. No network fetch was performed.

### M04 — Scheme registry and acceptance workflow ✅ Done

- Add versioned scheme identities and classifications: `active`, `index`, `ETF`,
  `arbitrage`, `fund_of_funds`, and `unknown`, plus `is_equity_scheme`.
- Preserve all schemes; expose unfiltered and active-equity-only holdings views.
- Implement accepted/rejected/quarantine transitions with append-only events.
- Make duplicate hashes idempotent and superseding versions non-destructive.
- Complete real `import-mf` and resumable directory import behavior.

Exit: one PPFAS month can be dry-run, imported, validated, accepted, re-imported
without duplication, and exported with all schemes retained.

Proof: migration `019` adds append-only scheme-name/classification versions,
workbook and per-scheme acceptance history, plus current-pointer-backed accepted
views. The June-2026 fixture registers all seven PPFAS schemes: three active
equity, three active non-equity, and one arbitrage scheme. All 727 normalized
holdings remain in the unfiltered accepted view; the active-equity view retains
298, while security-resolved primary feature eligibility is 586 across all
schemes and 264 for active equity. The complete 22-rule M02–M04 contract accepts
the official workbook, sets all seven scheme/month pointers atomically, and
exports source cells, holdings, registry classifications, scope counts, and
state history. Invalid-ISIN evidence is retained but transitions `inbox →
rejected` with no partial pointer; structural failures remain quarantined.
Re-importing identical bytes appends only a duplicate observation, while a new
accepted hash preserves the old accepted import and redirects all current views
through seven append-only supersession events. A two-file directory test proves
an interrupted run resumes the completed hash idempotently. PPFAS manifest
`0.3.0`, import-plan/export schemas `1.3.0`/`1.1.0`, package `0.3.3`; 80 offline
tests green. No network fetch was performed.

### M05 — Consecutive-month change engine ✅ Done

- Compare only complete consecutive accepted scheme-month disclosures.
- Emit `new_entry`, `complete_exit`, `quantity_increase`, `quantity_decrease`, and
  `unchanged` at the scheme/security level.
- Store previous/current quantity, exact delta, previous/current NAV weight,
  weight delta, both source import IDs, and completeness evidence.
- Never infer an exit or entry across a missing, rejected, or quarantined month.

Exit: focused two-month fixtures prove all five change classes, zero-versus-
missing behavior, and false-entry/exit prevention.

Proof: migration `020` adds append-only comparison sets and exact
scheme/security change rows. A comparison is generated only when the previous
and current calendar months both have scheme-level accepted pointers, their
workbook imports remain `accepted`, and each import has a complete M04 workbook
acceptance event; otherwise a `skipped` set records the missing/rejected/
quarantined evidence and authorizes no absent-row-as-zero arithmetic. The focused
two-month fixture yields 7 changes covering all five classes: 2 `new_entry`, 2
`complete_exit`, 1 `quantity_increase`, 1 `quantity_decrease`, and 1 `unchanged`.
Absent rows remain `NULL` with `row_presence='absent'`, while explicit source
zeros remain present numeric `0`; both cases classify correctly only after the
pair is complete. Quantities, NAV weights, and deltas use Python `Decimal` and
plain exact decimal strings; every row carries both source import IDs and the
complete comparison evidence. Missing, rejected, and quarantined middle-month
fixtures produce zero change rows, proving May cannot compare directly with July.
Focused rules `m05_consecutive_calendar_months`,
`m05_complete_accepted_disclosures`, `m05_comparable_holding_rows`, and
`m05_exact_change_rows_complete` are stored per comparison set; coverage gaps
fail the disclosure rule and leave row validation explicitly `not_evaluated`.
Input fingerprints make reruns idempotent. Superseding an accepted pointer appends
a new change set, preserves the prior rows, and moves only
`mf_current_scheme_security_month_changes` to the new pair. Acceptance now
refreshes the pair ending at the imported month and an already-accepted following
month. Workbook export schema `1.2.0` includes current pair-level changes and
comparison audit sets; import-plan schema `1.4.0`, package `0.3.4`. 83 offline
tests green, and the focused accepted export was manually inspected. No network
fetch was performed.

### M06 — Stock-level monthly feature views ✅ Done

- Aggregate accepted pair-level changes into holder counts, new entries, complete
  exits, increases, decreases, unchanged counts, gross bought/sold quantities,
  net quantity change, total/changed portfolio weight, and scheme-type counts.
- Define accumulation breadth as new entries plus increases and distribution
  breadth as exits plus decreases.
- Produce both all-scheme and active-equity scopes, including
  `active_fund_accumulation_breadth`.
- Export feature lineage back to accepted workbook hashes and scheme-level deltas.

Exit: every stock-month feature is exactly reproducible from pair-level rows; the
active-equity view excludes passive/arbitrage schemes without deleting them.

Proof: migration `021` adds append-only stock-month feature sets, exact decimal
feature rows, scheme/change/classification inputs, workbook-hash lineage, six
stored validation rules, and pointer-safe `all_scheme`/`active_equity` current
views. Python `Decimal` performs every sum; SQLite never coerces decimal text to
binary floats. The focused seven-scheme fixture produces 9 scheme/security
changes over 2 securities, then 2 all-scheme feature rows, 1 active-equity row,
and 11 exact lineage edges. For the shared stock, the all-scheme row records 6
current holders, 3 entries, 1 exit, 1 increase, 1 decrease, 1 unchanged, gross
bought/sold `11`/`5`, net `6`, total NAV weight `3.2`, weight change `0.6`, and
breadth `4`/`2`; its active-equity row contains only the two active schemes. A
passive/arbitrage-only stock remains in the all-scheme view and is absent from
the active-equity view without deleting its holdings or M05 deltas. An incomplete
seventh scheme makes the whole feature set `skipped`, emits zero partial feature
rows, and leaves reconciliation rules `not_evaluated`. Input fingerprints make
reruns idempotent; a superseded workbook preserves the old feature set while
current views move to the new accepted hashes and M05 sets. The standalone
`derive-mf-features` and `export-mf-features` commands are implemented; feature
export schema `1.0.0` and workbook export schema `1.3.0` expose the compatibility
alias plus accepted workbook hashes and every contributing scheme delta.
Import-plan schema `1.5.0`, package `0.3.5`; 86 offline tests green, and the
focused accepted JSON export was manually inspected. No network fetch was
performed.

### M07 — PPFAS 12-month vertical slice ✅ Done

- Import exactly 12 operator-supplied consolidated PPFAS monthly workbooks.
- Review quarantine/validation output month by month and extend only the PPFAS
  manifest for evidenced format variants.
- Accept all complete months, derive 11 consecutive comparisons, and export
  stock-level all-scheme plus active-equity monthly features.
- Produce a coverage report for workbook, scheme, security match, month pair, and
  feature counts.

Exit: 12 accepted PPFAS months produce 11 auditable monthly change sets and the
specified accumulation features with zero unexplained dropped holdings.

Completion evidence (2026-08-05): the deterministic
`mf-coverage-report` command checks the bounded 12-month window across accepted
workbooks, scheme pointers/classifications, normalized holding and security-match
accounting, current consecutive change sets, both feature scopes, and stored
feature validations. All 12 official consolidated workbooks for Jul-2025 through
Jun-2026 are present locally, immutable, and accepted. The archive evidenced four
legacy BIFF8 workbooks, an Index sheet through Oct-2025, missing publisher row
codes in valid holdings, and the PPLCF launch in Feb-2026; manifest `0.4.0` and the
bounded reader cover those variants without evaluating workbook content. Same-
security rows within one scheme/month aggregate exactly, while the launch month
is excluded only from its non-comparable prior pair and remains audited as a
skip—no fabricated entry. The inspected report is complete: 12/12 accepted
workbook months, 11/11 comparisons, 11/11 feature months, both feature scopes,
and zero unexplained dropped holdings. Import-plan schema `1.6.0`, package
`0.3.6`; 91 offline tests pass.

### M08 — Two-format generalization pilot ✅ Done

- After M07, import two consecutive months from at least two other AMCs with
  different workbook layouts.
- Isolate publisher-specific detection rules behind the shared workbook adapter.
- Re-run all PPFAS golden and feature tests to prevent format-specific regressions.

Exit: three publisher formats share the same normalized contract and each new AMC
produces a correct consecutive-month feature set.

Completion evidence (2026-08-05): official May/June 2026 workbooks are pinned by
SHA-256 for two structurally different publishers: Groww's consolidated
multi-scheme workbook (59 portfolio sheets in May; 61 plus one hidden metadata
sheet in June) and Motilal Oswal's single-fund YO20 workbook. A shared adapter
registry now isolates publisher parsing, normalization, manifest, safety and
scheme-registry rules while retaining the M02–M06 persistence, acceptance,
comparison and feature engines. Groww produces 59 complete scheme comparisons,
two audited launch skips, 3,131 scheme/security changes, 859 all-scheme features
and 197 active-equity features. Motilal Oswal produces one comparison, 29 changes
and 29 features in both scopes. Groww's 152 derivative rows across the two months
remain source-faithful but are explicitly excluded from ISIN matching and stock
features; non-ISIN cash-equivalent identifiers likewise remain auditable. Exact
representative feature assertions cover HDFC Bank for Groww and Ather Energy for
Motilal Oswal. All pre-existing PPFAS goldens pass unchanged. Package `0.4.0`;
93 offline tests pass.

### M09 — Optional mfdata cross-check ✅ Done

- Add only after the official vertical slice is stable.
- Label every row `source='mfdata'` and
  `source_quality='third_party_unverified'`.
- Report gaps and conflicts against official observations.
- Enforce official-first precedence in schema, repository, views, and tests.

Exit: a conflicting third-party fixture is retained as evidence but cannot
overwrite or become preferred over the official workbook value.

Completion evidence (2026-08-05): migration `022` adds immutable saved-response
snapshots, source rows, append-only observations and accepted-version history,
cross-check runs/results, an all-source evidence view, and an official-first
preferred-holdings view. The strict saved-JSON adapter validates explicit
family/month identity, parses all equity/debt/other rows with `Decimal`, retains
each complete source row, converts source INR-crore market values exactly to the
official INR-lakh comparison unit, and labels every row `source='mfdata'` plus
`source_quality='third_party_unverified'`. The focused fixture records one
quantity/NAV-weight conflict and one third-party-only gap: the conflicting value
remains queryable but the official row has precedence rank 1 and stays preferred,
while the resolved gap is exposed only as rank-2 fallback. Schema checks reject a
false official quality label; repository tests prove duplicate snapshots and
cross-checks are idempotent and a later official revision still wins without
deleting third-party evidence. `crosscheck-mfdata` ingests only a local saved
response, writes an immutable content-addressed snapshot, and exports exact
provenance, gaps, conflicts and preferred-source decisions. Report schema `1.0.0`,
mfdata parser `1.0.0`, package `0.4.1`; 97 offline tests pass. No live mfdata or
Trendlyne request was performed, and the accepted JSON report was manually
inspected.

## Phase 2 document and transcript chunks

Start these after S11 provides tested document-link metadata. They may be designed
in parallel with later Phase 1 parsing, but Phase 1's current chunk order remains
unchanged.

### D01 — Artifact schema and primary-source discovery ✅ Done

- Add logical artifacts, discovery URLs, immutable contents and preferred-content
  pointers.
- Discover presentations, credit-rating reports, transcripts and recordings from
  exchange filings, company IR pages and official rating-agency pages.
- Treat Screener links as an additional discovery path, not as the publisher.
- Deduplicate identical bytes while retaining every source URL.

Exit: a dry run shows all candidate URLs and provenance; repeated discovery is
idempotent and cannot overwrite prior artifacts.

Proof: migration `023` adds immutable discovery-page evidence, logical
`document_artifacts`, every `artifact_sources` discovery path, globally
content-deduplicated `artifact_contents`, and a guarded preferred-content pointer.
The offline `document-link-discovery` adapter reads only operator-supplied saved
pages through a versioned JSON manifest; it supports NSE/BSE filings, company IR,
official rating-agency pages, and Screener as a non-primary cross-check. Its
LAURUSLABS fixture produces 8 source-faithful candidate paths from 4 saved pages
(6 primary-source and 2 Screener), covering 3 presentation paths, 3 transcript
paths, 1 rating rationale and 1 recording, deduplicated to 4 conservative logical
artifacts while retaining all URLs. Screener never becomes the publisher.
`discover-documents --dry-run` prints page hashes, exact titles/URLs, source
positions, event/period metadata and classification/identity evidence without
creating a database or snapshot. Real persistence content-addresses all 4 source
pages, stores 4 artifacts plus all 8 provenance paths, and an identical rerun
creates no rows or mutations. Declared type/identity drift fails closed; preferred
content tests prove corrected bytes append and move only the guarded pointer.
Discovery report schema `1.0.0`, parser `1.0.0`, package `0.5.0`; 102 offline
tests pass. The dry-run JSON was manually inspected. No network fetch or linked
document download was performed.

### D02 — Safe document/media downloader ✅ Done

- Download PDF, PPT/PPTX, audio and video with bounded size/time limits.
- Validate response status, content signature and media type rather than trusting
  the filename extension.
- Store original bytes content-addressed with fetch timestamps, redirects and
  hashes.

Exit: HTML error pages cannot be accepted as documents and identical files from
multiple URLs share content bytes without losing URL provenance.

Proof: migration `024` adds append-only `artifact_download_attempts` plus the
many-source provenance join, preserving requested/final URLs, UTC start/fetch
times, bounded elapsed time, selected response headers, the full redirect chain,
declared and signature-detected media types, byte size, SHA-256, diagnostic blob,
validated content pointer, applied limits and a structured result/error status.
`fetch-documents` streams with configurable bounds capped by the configured
256 MiB/120-second defaults, uses independent per-host clients/rate limiters, and
stops a host for the run on `401`, `403`, `429` or unexpected HTML. It accepts
PDF, legacy PPT, PPTX, MP3, FLAC, Ogg, WAV, MP4/QuickTime, MPEG, WebM and AVI only
when the byte signature, declared response type and logical artifact type agree;
the URL extension is never used for acceptance. Complete rejected bodies are
content-addressed for diagnosis but cannot create `artifact_contents`; bounded
partial prefixes may also be retained without promotion. The focused saved-byte
fixture proves an HTML access-denied page named `.pdf` is rejected, while two
distinct presentation URLs returning the same PDF produce 2 immutable download
attempts, 1 response blob/content row and 3 linked discovery paths (BSE, company
IR and Screener), with the redirect retained. Download report schema `1.0.0`,
package `0.6.0`; 107 offline tests pass. The golden JSON report was manually
inspected. No live network fetch was performed.

### D03 — PDF and presentation extraction ✅ Done

- Extract native PDF text and actual PPT/PPTX slide structure.
- Preserve page/slide order, text blocks, tables and image/chart references.
- Render and OCR only pages/slides whose native text is absent or unusable.
- Keep native, OCR and selected text origins separately.

Exit: text PDF, scanned PDF and presentation fixtures reproduce page/slide-level
golden output and record which pages required OCR.

Proof: migration `025` adds versioned/idempotent extraction runs, ordered
`document_pages`, ordered page/slide elements and focused validation results.
Native PDF parsing preserves page order, positioned text blocks, detected tables
and image references; PPTX parsing preserves slide order, titles, text blocks,
table cells, chart/image references and speaker-note availability. Legacy PPT
uses its OLE `PowerPoint Document` record stream to recover ordered native slide
text instead of treating the binary container as flat strings. Native, OCR and
selected text are separate columns with a database constraint tying the selected
value to its declared origin. The deterministic usability rule invokes OCR only
for a missing/unusable native surface: native PDF pages are never rendered,
scanned PDF pages are rendered at a recorded 200 DPI, and picture-only PPTX
slides OCR their embedded rendered source image. Parser, OCR engine/version,
options and immutable `content_id` form the extraction identity, so an identical
rerun creates no page/element rows and performs no OCR. `extract-document`
verifies the stored byte size and SHA-256, records failures rather than returning
a partial success, promotes only a wholly usable extraction, and writes schema
`1.0.0` JSON. Three pinned fixtures and a golden projection cover a two-page
native PDF (including table/image), a one-page scanned PDF and a two-slide PPTX
(native structure plus one OCR slide). All five extraction validations pass; the
real local Tesseract engine reproduced the scanned fixture as
`SCANNED EBITDA MARGIN 24 PERCENT`. A signature-bearing malformed PDF records a
failed extraction with zero pages and cannot become preferred; a post-extraction
byte mutation also fails hash/size verification before reuse. Package `0.7.0`;
113 offline tests green.
The page/slide JSON export was manually inspected. No live network fetch was
performed.

### D04 — Credit-rating normalization ✅ Done

- Classify rating rationale/letter/surveillance/reaffirmation documents.
- Normalize agency, facility/instrument, amount, rating, outlook, rating action
  and action date without discarding the full source text.
- Attach every normalized field to page evidence and flag ambiguous tables for
  review.

Exit: rating history is auditable to the exact report bytes and page; conflicting
or ambiguous actions cannot silently become canonical.

Proof: migration `026` adds idempotent credit-rating normalization runs,
source-faithful rating tables, page-local report passages, normalized actions,
per-field evidence, focused validations and a guarded preferred-normalization
pointer. The pinned two-page native CARE-style rationale has SHA-256
`f1c0ba13…b9bb17`; its full selected page text and seven non-table passages remain
available, including one rationale passage and one previous-rating passage. The
document classifies as `rating_rationale` with agency `care` and explicit action
date `2026-07-15`. Two page tables yield three exact actions: INR-crore amounts
`150.00`, `50.00` and `100.00`, two reaffirmations and one upgrade, with rating
and outlook kept separately. All non-null normalized fields carry page plus
element/row/column evidence back to the D03 extraction and immutable content
hash. The amount unit/scale is accepted only from a recognized table header; it
is never guessed from a bare value. The same extraction/parser/options reuse one
normalization and the original bytes are hash/size verified before reuse.

All four document classes (`rating_rationale`, `rating_letter`, `surveillance`,
`reaffirmation`) have deterministic tests. A duplicate/shifted rating header
retains the full source table as `ambiguous`, produces a manual-review result and
cannot acquire a preferred pointer. Two different same-date actions for the same
facility become explicit `conflict` rows with `is_canonical=0`; neither enters the
current view. Ten focused validations pass on the accepted fixture. Command
`normalize-credit-rating` writes schema `1.0.0` JSON with report-byte provenance,
full selected page text, passages, source tables, actions and field evidence.
Package `0.8.0`; 119 offline tests green. The accepted JSON export was manually
inspected. No network fetch was performed.

### D05 — Official transcript ingestion and preference ✅ Done

- Extract official transcript PDFs/documents into ordered speaker segments where
  the source structure permits it.
- Preserve raw speaker labels and prepared-remarks/Q&A boundaries.
- Mark official text as preferred over generated text for the same event.

Exit: a usable official transcript prevents unnecessary recording transcription,
and its text remains traceable to page ranges.

Proof: migration `027` adds official/machine transcript identities, ordered
segments, explicit section boundaries, focused validations, a guarded preferred
transcript pointer and current/fallback views. The pinned four-page native
official fixture has SHA-256 `530bc24b…a3b83c`; D03 preserves all four selected
pages, while D05 yields five ordered `origin='official'` speaker segments across
pages 2–4. The two prepared-remarks turns and three Q&A turns retain their exact
raw speaker labels and text. One Asha Rao turn spans pages 2–3 and keeps both
source fragments with page, element, origin and bbox evidence. Explicit
`Presentation` and moderator Q&A-transition evidence establish the section
boundary; no participant identity is inferred from the cover roster or speaking
order. A sentence containing a colon is proven to remain continuation text unless
its prefix satisfies the conservative explicit-label rule.

Only official publisher-original PDF content with a wholly usable D03 extraction
can enter `ingest-official-transcript`. Missing section evidence produces
`manual_review`; missing speaker structure produces `failed`; neither becomes
preferred, and the fallback view continues to request recording transcription.
An accepted official transcript sets `recording_transcription_needed=0`. A
pre-existing accepted machine transcript fixture remains stored with its source
media and segment, but the official transcript moves both guarded preference
pointers; database triggers reject attempts to promote the machine version back
over it. Identical content/parser/options reuse one transcript and re-verify the
immutable byte size/SHA-256 first. Eight focused validations pass. The command
`ingest-official-transcript` writes schema `1.0.0` JSON containing full selected
pages, ordered segments, raw labels, prepared/Q&A boundaries, fragment evidence
and fallback status. Package `0.9.0`; 127 offline tests green. The accepted JSON
export was manually inspected. No network fetch or audio transcription occurred.

### D06 — Recording acquisition and audio normalization ✅ Done

- Resolve official/company-hosted audio or video recording links.
- Preserve original media and derive normalized audio as a separate content blob.
- Record duration, codecs and the original-to-derived content relationship.

Exit: a recording-only fixture produces deterministic normalized audio without
modifying or replacing the source media.

Proof: migration `028` adds idempotent recording-normalization identities,
source/derived content relationships, focused validations and the
`accepted_recording_audio` view. `normalize-recording` resolves only D02 content
whose validated download is linked to an official primary discovery path; a
directly attached or non-primary media blob cannot enter D06. The pinned
1.25-second company-hosted WAV has SHA-256 `d041562f…af613`, source codec
`pcm_s16le`, 44.1 kHz stereo and exact duration `1250ms`. FFmpeg `8.1.1` derives
a bit-exact 40,044-byte mono 16 kHz PCM WAV with SHA-256
`93a85376…f361b`; ffprobe records container, codec, stream, channel, sample-rate
and duration evidence for both contents. All 11 focused validation rules pass,
including the `<=50ms` duration check. The artifact keeps the official original
as `preferred_content_id`; the generated WAV is non-official
`generated_derivative` content connected by `relationship_type='normalized_audio'`.
The fixed engine/version/options/source identity makes reruns idempotent without
reinvoking media processing, while both original and derived immutable bytes are
size/hash-verified before reuse. A video-only probe fixture fails closed and
creates neither a derivative nor a relationship. Export schema `1.0.0`, package
`1.0.0`; 131 offline tests green. The accepted JSON export was manually
inspected. No live network fetch or audio transcription occurred.

### D07 — Recording-to-transcript fallback ✅ Done

- Run transcription only when no usable official transcript exists, unless a
  human explicitly requests a comparison transcript.
- Store timestamped segments, detected language, confidence/uncertainty, engine,
  model version, options and source-media hash.
- Keep unknown speakers neutral; do not infer executive names from speaking order.
- Label output `machine_generated` and validate timestamps/duration coverage.

Exit: a recording-only earnings call yields an auditable machine transcript;
re-running the same model/options/media is idempotent.

Proof: migration `029` adds durable machine-transcription run identity, exact
D06 normalization/source-media lineage, a generated-content pointer, language
confidence, duration coverage and per-segment uncertainty. The `transcribe`
command targets a logical transcript artifact and accepts only an event-matched,
accepted D06 normalized WAV whose normalized and publisher-original bytes are
reverified by size and SHA-256. An accepted official transcript skips before the
engine is invoked; `--comparison-requested` is the only override and its output
cannot become preferred over official text.

The local-files-only faster-whisper adapter records package version, a SHA-256
inventory identity for the supplied local model, fixed options, detected
language/probability, timestamps, confidence plus native uncertainty metrics.
Every segment uses neutral `unknown` speakers and `section_kind='unknown'`; no
executive identity or prepared/Q&A boundary is inferred. Ten focused validations
cover non-empty segments, timestamp ranges/order, media-duration bounds,
language, confidence, neutral speakers and duration/edge coverage. Invalid
timelines and engine failures remain queryable but never preferred. Exact
media/engine/model/options reruns reuse the durable run without invoking the
engine, after immutable bytes are reverified. A later official transcript moves
only the guarded preference pointers and leaves the machine content/segments
intact. Export schema `1.0.0`, package `1.1.0`; 138 offline tests green. No live
network access or model download occurred.

### D08 — Document quality report and pilot ✅ Done

- Report coverage by company, period and artifact type; missing official
  transcripts; recording fallbacks; OCR pages; extraction failures and provenance
  conflicts.
- Pilot on companies covering all artifact types before universe expansion.

Exit: every expected presentation, rating report and earnings-call event is either
accepted, explicitly missing, or blocked with a queryable reason.

Proof: `python3 -m scrapper.cli document-quality-report` writes a deterministic,
schema-versioned JSON report over the persisted D01–D07 document pipeline and may
be bounded to one or more repeated `--symbol` selections. Every discovered
artifact is assigned exactly one terminal D08 outcome (`accepted`,
`explicitly_missing`, or `blocked`) with a queryable reason; coverage is grouped
by company, canonical reporting-period/event key and artifact type. The report
retains source URLs and publisher claims, immutable content hashes, download
attempts, extraction/parser versions and validation failures, normalized-rating
status, transcript preference plus machine-run identity, and recording
normalization evidence. Dedicated anomaly blocks enumerate missing official
transcripts, verified recording fallbacks, page/slide-level OCR, extraction
failures and both publisher-claim and credit-rating-action provenance conflicts.
The bounded LAURUSLABS offline pilot covers presentations, credit ratings,
transcripts and recordings and exercises all three terminal outcomes: 4 accepted,
1 explicitly missing and 1 blocked across 4 period/event keys. Its official
transcript is explicitly missing while an accepted D06/D07 recording fallback is
reported; deleting the durable D07 run makes that same machine preference
`blocked` rather than a valid fallback. The golden pilot also proves one OCR
slide, one failed PDF extraction and one conflicting primary-publisher claim are
queryable. Report schema `1.0.0`, package `1.2.0`; 142 offline tests green. The
accepted pilot JSON export was manually inspected. No live fetch occurred.

### D09 — Delayed-artifact reconciliation schedule ✅ Done

- Add durable artifact expectations and append-only check events with explicit
  states for pending, not offered, not applicable, no rows, uncovered,
  inaccessible, failed and unknown results.
- Implement an idempotent `reconcile-artifacts --due` command that claims only due
  work, remains safe when scheduler runs overlap, and persists its next retry.
- Begin with configurable checks at `+2h`, `+6h`, `+12h`, `+24h`, daily through
  day 7 and weekly through week 6 after an evidenced call/event; do not rescan the
  full universe every hour.
- When a recording appears, ingest it through D02/D06/D07. When an official
  transcript appears later, preserve both and move only the preferred pointer.
- Report coverage and retry state by company, period and artifact type.

Exit: a fake-clock fixture progressing from unpublished to recording-only to an
official transcript is reconciled without duplicate artifacts, lost provenance or
an official/machine transcript preference error; a company with no source
coverage is not represented as zero holdings or as a technical failure.

Proof: migration `030` adds evidenced `artifact_events`, durable unique
`artifact_expectations`, append-only `artifact_check_events`, persisted per-
expectation schedules and expiring unique claim tokens. D01 discovery now seeds
event-identifiable expectations idempotently; bare links without a defensible
event identity do not create cross-artifact work. The default bounded ladder is
`+2h`, `+6h`, `+12h`, `+24h`, daily through day 7 and weekly through week 6.
`reconcile-artifacts --due` previews without claiming under `--dry-run`, otherwise
uses `BEGIN IMMEDIATE` leases so overlapping workers cannot take the same row,
and atomically appends the check plus retry/terminal state. The persisted adapter
reports `available` only after the matching D01–D07 accepted handoff; adapter
exceptions become queryable `discovery_failed` checks. `artifact-coverage`
exports company/event-period/artifact status, complete check history, retry and
claim state. A fake clock proves unpublished at `+2h`, accepted recording plus
machine fallback at `+6h`, then accepted official transcript at `+12h`; both
transcripts remain while only preference moves to official, a same-time rerun
claims zero work, and five total check events remain. Separate fixtures keep
`not_offered`, `not_applicable`, `no_rows_reported`, `source_not_covered`,
`inaccessible`, `discovery_failed` and `unknown` distinct; uncovered creates no
fake artifact or zero-data observation. Reconciliation/coverage schema `1.0.0`,
package `1.3.0`; 148 offline tests green. No live fetch occurred.

### Document acquisition recovery + DA-08 storage sizing ✅ Done

Migration `045` moves physical deduplication to its correct boundary:
`response_blobs` remains unique by SHA-256, while more than one logical artifact
may attach its own `artifact_contents` row to the same immutable blob. This
closes the live `Identical content is already attached` worker failures without
copying bytes or collapsing potentially distinct artifact identities.

`document-recovery-report` reports logical artifacts separately from URL jobs
and assigns deterministic recovery actions. `requeue-document-downloads`
requires explicit job IDs and an audited reason and refuses succeeded, pending,
leased, policy-skipped, or already-covered work. `register-document-replacement`
preserves the failed source, adds replacement provenance, and creates a fresh
queue job. `import-manual-document` provides the policy-safe path: it validates
byte signatures and artifact type, content-addresses the file, records the
operator assertion, and closes outstanding URL jobs without inventing an HTTP
attempt.

The next plan chunk, DA-08, is delivered by
`document-storage-projection`. For one explicit scope it reports count,
mean/median/p90/p95/max bytes, artifact coverage, validated job rate,
deduplicated actual bytes, planned-count projections, 20–30% configurable
headroom, current disk free space, and whether the projection fits.

Proof: `tests/test_document_download_recovery.py` covers upgrade safety,
cross-artifact identical bytes, logical recovery classification, dry-run and
audited requeue, replacement URL provenance, signature-validated manual import,
and observed DA-08 projection math.

## Exchange universe chunks

### E01 — Exchange universe and result discovery (complete)

Universe discovery moves from Screener screens to the official exchange record.
Design and operating rules: `../EXCHANGE_UNIVERSE_PLAN.md`.

Delivered:

1. Migration `032_exchange_universe.sql`: dated snapshots, rejected-row
   retention, effective-dated securities and versions, security events, the
   ISIN-keyed company registry and links, symbol/name changes, result filings,
   broadcast watermarks, per-filing queue decisions, and a rebuild of
   `screener_company_queue` that gives every item an `origin`.
2. Header-bound, source-faithful parsers for the NSE equity/SME masters, the BSE
   standardized security master, the headerless NSE symbol-change contract, the
   NSE name-change file, and the NSE/BSE financial-result feeds.
3. ISO 6166 check-digit validation before any ISIN union; no name-based merging
   anywhere.
4. Incremental filing discovery keyed by IST→UTC broadcast instant, idempotent
   across repeated polls of the same feed.
5. Queue bridge that derives the Screener path from the registry (NSE symbol
   preferred, BSE scrip code fallback) and enqueues only new or advanced
   periods; unresolvable identities go to manual review.
6. `exchange-refresh-universe`, `exchange-discover-results` and
   `exchange-universe-coverage` commands, plus non-Screener Compose services.
7. Offline suite `tests/test_e01_exchange_universe.py`, including a legacy-queue
   migration test that proves existing screen items and their events survive the
   table rebuild with foreign keys intact.

## Current handoff

Run after completing E01:

```bash
python3 -m unittest discover -s scrapper/tests -v
```

Expected baseline from the latest implementation: **188 tests passing** (148
before the Screener discovery and exchange universe layers). M01–M09
cover safe local workbook inventory, source-faithful cells, normalized holdings
and ISIN-first matching, versioned scheme classification and acceptance, exact
consecutive accepted-month changes and stock-month feature lineage, plus shared
publisher adapters for PPFAS, Groww and Motilal Oswal and an isolated mfdata
cross-check with official-first precedence. The PPFAS official slice
is complete at 12/12 accepted workbooks, 11/11 comparisons, 11/11 feature months,
and zero unexplained dropped holdings. Feature export schema is `1.0.0`, workbook
export schema is `1.3.0`, import-plan schema is `1.6.0`, package `1.3.0`, PPFAS
manifest `0.4.0`, and both M08 publisher manifests are `1.0.0`. Trendlyne P03/P04
remain frozen. D01–D09 and E01 are complete. The next step is operational, not
structural: confirm each official dataset URL before automating `--url`, refresh
both masters daily, and work the manual-review queue that discovery reports.
