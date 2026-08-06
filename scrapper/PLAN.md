# Scraper — Phase 1 Data Collection Plan

_Revised: 2026-08-05_

## 1. Objective

Build a deterministic, local market-data collection system in two phases. The
immediate acquisition priority is now a working mutual-fund holdings path from
user-supplied official AMC portfolio workbooks.

Phase 1 covers these source families:

- Screener company pages, beginning with
  `https://www.screener.in/company/LAURUSLABS/consolidated/`
- official AMC monthly portfolio XLS/XLSX workbooks imported from a local inbox,
  beginning with 12 consolidated PPFAS monthly workbooks.

Phase 1 has one job: collect structured source data completely, preserve what the
source supplied, and also produce clean typed values. For mutual-fund data it also
compares consecutive accepted months and emits stock-level accumulation features.
It does not train a model, interpret PDFs, transcribe audio, summarize text, or
ask an LLM to parse HTML or workbooks.

The output must be reproducible, historically versioned, and auditable back to the
source bytes. It is point-in-time safe only from the first observation onward;
historical periods found on a current page are not assumed to reproduce what was
known at those historical dates.

The completed Trendlyne code and fixtures remain available as historical parser
evidence, but all live Trendlyne backfill stays disabled. Trendlyne is not the
acquisition dependency for the new mutual-fund pipeline.

Phase 2 follows the links discovered in Phase 1 and independently checks primary
sources to collect investor presentations, credit-rating reports, earnings-call
transcripts and earnings-call recordings. Screener is a discovery and
structured-data source; it is not the only document source and is never treated
as the publisher of a linked primary-source document.

Phase 2 discovery is a continuing reconciliation process, not a one-time lookup.
Presentations, call recordings and transcripts are often published hours or days
after results or the call itself. A scheduled backfill worker therefore revisits
only due, unresolved artifact expectations and attaches newly available content
without replacing the earlier snapshot or generated transcript.

## 2. Meaning of source-faithful

Every extracted value has two representations:

1. **Source-faithful representation** — section order, table order, row/column
   order, source labels, period labels, display strings, links, and distinctions
   such as `-`, blank, `0`, `0.00%`, and hidden values are preserved.
2. **Normalized representation** — typed dates, `Decimal`-safe numeric strings,
   currency, unit, scale, reporting scope, period type, and canonical field names.

Example:

```json
{
  "label_raw": "Market Cap",
  "display_value": "₹ 99,031 Cr.",
  "value_decimal": "99031",
  "currency": "INR",
  "scale_multiplier": "10000000",
  "missing_kind": null
}
```

Cleaning must never destroy the source value. A parser bug can therefore be fixed
offline without fetching the page again.

“Exact” is evaluated against saved source bytes, not against a live page or an
operator copy that may change while tests run.

Before implementation, create a versioned source manifest for each page or
publisher-workbook type. It lists every visible component (field, sheet, prose
block, table, chart, collapsed drill-down, filter, and document list), where its
data comes from, and one explicit status: `capture`, `metadata_only`,
`conditional`, or `deferred`. Nothing may be silently omitted. The initial
contract below covers server-visible structured data and local workbook cells;
chart series or click-to-expand datasets must either be added to `capture` or
explicitly accepted as deferred before the source adapter is called complete.

## 3. Hard constraints

- Deterministic HTTP and HTML parsing only. The first proven slice uses
  `requests` + `BeautifulSoup`; `lxml` may replace the parser when fixtures show a
  measurable correctness or performance benefit. `pandas.read_html()` is a
  cross-check or fallback, not the canonical parser.
- No LLM, vision model, Selenium, or Playwright in the initial implementation.
- If a visible section is absent from the response HTML, first identify its normal
  HTML/JSON request and fetch that endpoint directly. A browser is added only when
  a fixture proves direct HTTP cannot retrieve required data.
- Local SQLite in WAL mode plus immutable response files.
- Existing archived code under `OLD/` is not modified.
- Keep the existing Trendlyne code and fixtures intact, but keep all Trendlyne
  network backfill disabled.
- Mutual-fund acquisition is local-file only for the immediate deliverable. Do
  not build a 51-AMC crawler or browser automation.
- Accept XLS/XLSX workbook inputs. Parser libraries must not execute workbook
  macros or external links.
- No parser is allowed to silently return a partial “success”.
- No CAPTCHA bypass, login bypass, or rate-limit evasion. Stop on persistent
  `401`, `403`, `429`, CAPTCHA, or an unexpected login page.
- Check and respect each source’s current terms, robots guidance, and account/data
  permissions before batch operation.

## 4. Phase 1 source contract

### 4.1 Screener

Capture, in the order shown:

- identity: company name, NSE/BSE identifiers, website, about text and source links;
- quote header and quote timestamp as displayed;
- top ratio panel, including authenticated custom ratios when available;
- pros and cons as source text, clearly marked as source-generated commentary;
- peer table if it is returned by an accessible first-party page request;
- quarterly results;
- annual profit and loss plus the four growth blocks;
- balance sheet;
- cash flow;
- annual ratios;
- quarterly and yearly shareholding tables and the classification warning;
- announcements, annual reports, credit ratings, and concall link metadata.

Phase 1 records document metadata and URLs only. Downloading and interpreting the
linked documents is a separate future phase.

Each financial observation includes:

```text
reporting_scope       consolidated | standalone
section_code          quarters | pnl | balance_sheet | cash_flow | ratios | ...
line_item_raw         exact visible label
line_item_code        canonical code when mapped, otherwise null
row_position          source order
period_label_raw      e.g. Jun 2026, Mar 2026, TTM
period_end            parsed date when knowable
period_type           quarter | fiscal_year | ttm | point_in_time | unknown
fiscal_year           derived only when the rule is unambiguous
first_observed_at     first time these exact source bytes were observed
source_published_at_utc source publication time when explicitly available
available_at          earliest defensible system-availability timestamp
availability_basis   source_publication_date | first_observed |
                     exchange_announcement_date | estimated | unknown
display_value         exact visible cell text after documented whitespace handling
value_decimal         canonical decimal string, never binary-float storage
unit_code             INR | INR_CRORE | PERCENT_POINT | DAYS | COUNT | RATIO | ...
scale_multiplier      e.g. 10000000 for crore
missing_kind          blank | dash | hidden | not_applicable | parse_error | null
```

Rules:

- `consolidated` and `standalone` are never merged or allowed to overwrite one
  another.
- TTM, a quarter, and a fiscal year are distinct period types even when labels are
  visually close.
- Percentages are stored as percentage points: `27.20%` becomes `27.20`, not
  `0.272`. A downstream view may derive the fraction.
- `-0`, `0`, blanks, dashes, and missing cells remain distinguishable in the raw
  layer.
- `period_end` describes the reporting period; it is never used as a substitute
  for when information became available.
- Historical rows scraped from a current page are not historically point-in-time
  accurate before `first_observed_at`. If no explicit publication timestamp is
  available, `available_at` defaults to `first_observed_at`, never `period_end`.
- The Sep-2022 XBRL shareholding regime warning is stored with the data. Individual
  rows are tagged only `pre_sep_2022`, `xbrl_sep_2022_plus`, or `unknown`.
  `is_cross_regime_comparison` is derived only when comparing two observations.

### 4.2 Official AMC workbook imports

The primary mutual-fund source is an official AMC monthly portfolio workbook
supplied by the operator. The importer does not crawl AMC sites. Files arrive in:

```text
scrapper/store/mf_import/inbox/<publisher>/<YYYY-MM>/
```

The first vertical slice is exactly 12 consolidated PPFAS monthly workbooks.
Only after that slice produces accepted stock-level monthly features should the
parser add two months each from at least two AMCs with different workbook
structures. Do not expand to all AMCs as part of this pilot.

For each imported file, preserve:

```text
publisher
disclosure_month
source_file
original_filename
content_hash
byte_size
workbook_type
first_observed_at
accepted_at
import_state
```

The original workbook bytes are immutable and content-addressed. Re-importing the
same bytes records an idempotent observation; it does not create duplicate
holdings. The operational states are `inbox`, `accepted`, `rejected`, and
`quarantine`. `inbox` is unprocessed, `accepted` has passed the complete source
and normalized contract, `rejected` was recognized and parsed but failed a
deterministic data validation, and `quarantine` could not be identified or
interpreted safely. No state permits partial downstream use.

Detect publisher and month from explicit CLI arguments plus workbook evidence.
The two must agree. Detect every sheet and table boundary, then preserve cells in
source order before normalizing. One consolidated workbook may contain many
schemes and non-portfolio sheets; no relevant sheet or table may be silently
skipped.

For every holding capture:

```text
publisher
source = amc_workbook
source_quality = official
scheme_name_raw
scheme_code
scheme_category
disclosure_month
security_name_raw
isin
quantity
market_value
percentage_of_nav
instrument_type
source_file
source_sheet
source_row
content_hash
first_observed_at
accepted_at
```

Store the original display text for every source cell separately from normalized
typed values. `quantity`, `market_value`, and `percentage_of_nav` use exact
decimal strings; no binary-float arithmetic is allowed. Formula cells preserve
the formula and cached display value. Units and scale must be resolved from the
workbook, never guessed from a bare number.

ISIN is the primary security-matching key. Company/security-name matching is a
versioned fallback that requires an auditable match decision and manual review
when ambiguous. Holdings without a valid security match remain stored and are
excluded from stock-level features until resolved.

Maintain a versioned scheme registry with:

```text
scheme_management_type = active | index | ETF | arbitrage | fund_of_funds | unknown
is_equity_scheme
valid_from_month
valid_to_month
classification_source
classification_rule_version
```

All schemes remain in the source and normalized layers. The main smart-money view
uses only accepted observations whose registry version classifies them as active
equity. Passive, ETF, arbitrage, fund-of-funds, and unknown schemes remain
queryable through unfiltered views.

Compare only consecutive accepted months with complete comparable disclosures.
For one scheme/security pair:

- a new entry is previous quantity absent or zero and current quantity positive;
- a complete exit is previous quantity positive and current quantity absent or
  zero;
- otherwise classify the quantity delta as increase, decrease, or unchanged;
- an absent row is treated as zero only after both scheme-month disclosures pass
  completeness checks; missing or quarantined workbooks never imply an exit.

Aggregate the pair-level changes by security and month into:

```text
active_fund_holder_count
new_fund_entries
complete_fund_exits
funds_increasing_quantity
funds_decreasing_quantity
funds_unchanged
gross_quantity_bought
gross_quantity_sold
net_quantity_change
total_portfolio_weight
portfolio_weight_change
active_equity_holder_count
passive_holder_count
arbitrage_holder_count
accumulation_breadth
distribution_breadth
active_fund_accumulation_breadth
```

`gross_quantity_bought` is the sum of positive quantity deltas and
`gross_quantity_sold` is the absolute sum of negative deltas.
`accumulation_breadth = new_fund_entries + funds_increasing_quantity` and
`distribution_breadth = complete_fund_exits + funds_decreasing_quantity`.
`active_fund_holder_count` means all schemes currently holding a positive
quantity; `active_equity_holder_count` is the subset classified as active equity.
`passive_holder_count` combines index and ETF schemes, while arbitrage remains a
separate count. The active-fund accumulation variant applies the breadth rule to
active equity schemes only. Exports may expose
`number_of_funds_increasing` as a compatibility alias for the canonical
`funds_increasing_quantity`; it must not be computed independently.
Quantity change is canonical for accumulation; market-value change is retained
but is not a substitute because price movement changes market value.

An optional `mfdata` adapter may later cross-check official rows or fill explicit
gaps. It must label observations `source_quality='third_party_unverified'`.
Precedence is always `official`, then third-party fallback, then missing. A
third-party observation may never overwrite, supersede, or silently merge with an
official workbook observation.

### 4.3 Frozen Trendlyne adapter

The existing Trendlyne parser, normalized observations, validations, fixtures,
and resumable pilot plan are preserved. No live backfill, new request, fixture
expansion, or redistribution is authorized. Downstream code may use its existing
accepted rows only when explicitly requested and must identify them as Trendlyne
source data; they are not official AMC observations.

### 4.4 Phase 2 document and recording sources

Document discovery uses the following priority order:

1. NSE/BSE corporate filings and announcement attachments;
2. the company investor-relations site;
3. the official credit-rating-agency site for rating rationales and releases;
4. Screener document/concall links as a discovery and cross-check source;
5. another source only when its provenance and redistribution rights are known.

Discovery must run against the primary sources as well as Screener metadata. A
document missing from Screener must not therefore become invisible to the system.
When the same bytes are available from multiple URLs, store one content-addressed
blob and retain every discovery URL and publisher claim.

The in-scope artifact types are:

- investor/earnings presentations (`PDF` first, plus actual `PPT`/`PPTX` files);
- credit-rating rationales, rating letters and surveillance/reaffirmation reports;
- official earnings-call/conference-call transcript documents;
- earnings-call audio or video recordings when no usable transcript is available.

Acquisition follows this transcript preference order:

```text
official transcript document
        ↓ unavailable or unusable
official/company-hosted recording
        ↓
machine-generated transcript with timestamps and provenance
```

A recording is not transcribed merely because it exists. If a usable official
transcript is present, keep recording metadata/linkage but prefer the official
text. If only a recording is available, download and hash the original media,
extract/normalize its audio without modifying the original, and create a
timestamped transcript. Record the transcription engine, exact model/version,
language, options, creation time and source-media hash. Machine transcripts are
labelled `machine_generated`; they must never masquerade as issuer-published text.
Speaker names may be assigned only from reliable evidence. Otherwise retain
neutral speaker IDs or `unknown`, and preserve confidence/uncertainty.

PDF/PPT extraction follows this order:

1. native embedded text and document structure;
2. page/slide rendering plus OCR only for pages with missing or unusable text;
3. source-faithful page/slide text, tables and images retained before normalized
   qualitative facts are derived.

For presentations, preserve slide number, title, text blocks, tables, chart/image
references and speaker-note availability. For credit ratings, preserve the full
report and later normalize agency, instrument/facility, amount, rating, outlook,
rating action, action date and rationale passages with page references. For
transcripts, preserve page or timestamp ranges, speaker label, utterance order,
prepared-remarks versus Q&A boundaries when explicit, and transcription origin.

Every artifact is linked to a company and, where defensible, an event date,
fiscal quarter/year and exchange announcement. `period_end`, `published_at`,
`first_observed_at` and `downloaded_at` remain distinct. A filename or URL date is
not accepted as a publication date without validation.

### 4.5 Delayed publication, absence and scheduled backfill

An earnings call is not assumed to happen on the result date. It may happen the
same day or later, and its presentation, recording and official transcript may
each appear at a different time. Discovery must therefore model an expected event
and the availability of each artifact separately.

Absence is data, but all absence is not equivalent. Never store a missing link,
empty table or zero-row result as an undifferentiated `null`. Use an explicit
status with evidence:

| Status | Meaning | Retry? |
|---|---|---|
| `available` | Artifact/data was found and identity-validated | No, except revision audit |
| `expected_not_yet_published` | An announced or otherwise evidenced event exists, but this artifact is not published yet | Yes |
| `not_offered` | The company/source explicitly says it does not provide this artifact or dataset | No, periodic audit only |
| `not_applicable` | The artifact/data cannot apply to this company or period | No |
| `no_rows_reported` | The source explicitly reports no holdings/activity; this is not the same as no coverage | On the next source period |
| `source_not_covered` | The source does not cover this company, scheme or artifact type | No, mapping audit only |
| `inaccessible` | Login, entitlement, robots/terms, removed file or other access boundary prevented verification | Yes only when authorized/actionable |
| `discovery_failed` | Timeout, blocked response, parse drift or other technical failure | Yes with bounded backoff |
| `unknown` | There is not enough evidence to classify the absence | Limited retry, then review |

For example, a company with no Trendlyne mutual-fund page is not automatically a
company with zero mutual-fund ownership. Likewise, no Screener concall link does
not prove that no call, presentation or recording exists. Primary-source checks
and explicit source evidence decide the status.

Add a durable expectation/recheck queue in Phase 2:

```text
artifact_expectations(
  expectation_id PK, company_id, artifact_type, event_id,
  expectation_basis, expected_after_utc, availability_status,
  first_checked_at_utc, last_checked_at_utc, next_check_at_utc,
  attempt_count, terminal_reason, last_error_json,
  UNIQUE(company_id, artifact_type, event_id)
)

artifact_check_events(
  check_id PK, expectation_id, checked_at_utc, source,
  requested_url, result_status, evidence_json, discovered_artifact_id
)
```

The scheduler (cron in deployment, an ordinary idempotent command in code) runs
hourly but fetches only rows whose `next_check_at_utc <= now`. A reasonable
configurable starting policy after the expected call end is `+2h`, `+6h`, `+12h`,
`+24h`, daily through day 7, then weekly through week 6. Known publisher timing
may override this policy. An HTTP 404 or an empty source page is one observation,
not proof of permanent absence.

On discovery, the worker downloads, validates, hashes, parses and links the new
content in one resumable workflow, marks the expectation `available`, and records
the successful check. If an official transcript appears after a machine-generated
one, preserve both and move only the preferred-content pointer to the official
transcript. Every run is idempotent, host-rate-limited, lock-safe and append-only;
overlapping cron executions must not create duplicate artifacts or checks.

The initial universe scrape must also emit a coverage report by company, period
and data/artifact type so legitimately absent, pending, uncovered and failed data
can be distinguished before any downstream metric uses it.

## 5. Storage architecture

Use three layers for every source:

```text
immutable source bytes (HTTP response or local workbook)
        ↓
source-faithful parsed cells/records
        ↓
normalized domain tables and exports
```

### 5.1 Files

```text
scrapper/store/
  scraper.db
  snapshots/
    screener/<SYMBOL>/<scope>/
      <sha256>.html
    trendlyne/<SYMBOL>/<month>/
      <sha256>.html
    amc_workbook/<publisher>/<YYYY-MM>/
      <sha256>.<xls|xlsx>
  mf_import/
    inbox/<publisher>/<YYYY-MM>/
    accepted/
    rejected/
    quarantine/
    documents/<SYMBOL>/<artifact_type>/
      <sha256>.<validated_extension>
    media/<SYMBOL>/<event>/
      <sha256>.<validated_extension>
    derivatives/<source_sha256>/
      extracted.json
      transcript.json
  exports/
    <run_id>/
      source_faithful/<source>/<symbol>.json
      normalized/<source>/<symbol>.json
      quality_report.json
```

The content-addressed source file is immutable. `latest` may be a pointer or
database view, never the only copy. When content is unchanged, a new fetch/import
observation references the existing blob while retaining its new observation
timestamp and submitted path.

Store raw response/workbook bytes so the original encoding, workbook formulas,
and hash are reproducible.

### 5.2 Core SQLite tables

The exact DDL belongs in migrations, but the required logical model is:

```text
companies(
  company_id PK, current_legal_name, status
)

security_identifiers(
  company_id, identifier_type, identifier_value, valid_from, valid_to, is_current,
  UNIQUE(identifier_type, identifier_value, valid_from)
)

source_identifiers(
  company_id, source, source_company_id, source_symbol, source_slug,
  canonical_url, mapping_status, verified_at,
  UNIQUE(source, source_company_id),
  UNIQUE(company_id, source)
)

scrape_runs(
  run_id PK, started_at, completed_at, code_version, parser_version,
  config_json, status
)

response_blobs(
  blob_id PK, content_sha256 UNIQUE, content_type, encoding, byte_size,
  local_path, first_observed_at_utc, created_at_utc
)

fetch_observations(
  fetch_id PK, run_id, company_id, source, reporting_scope,
  requested_source_period, resolved_source_period,
  requested_url, final_url, fetched_at_utc, http_status, etag, last_modified,
  auth_mode, blob_id, fetch_status
)

parse_artifacts(
  parse_id PK, blob_id, parser_name, parser_version, manifest_version,
  parsed_at_utc, parse_status, validation_status,
  UNIQUE(blob_id, parser_name, parser_version, manifest_version)
)

source_sections(
  section_id PK, parse_id, section_code, heading_raw, position,
  parse_status, error_json
)

source_fields(
  section_id, field_position, field_key_raw, display_value, link_url, field_role,
  PRIMARY KEY(section_id, field_position)
)

source_table_cells(
  section_id, table_position, row_position, column_position,
  row_label_raw, column_label_raw, raw_text, normalized_display_text,
  link_url, cell_role, visibility_state, rowspan, colspan,
  PRIMARY KEY(section_id, table_position, row_position, column_position)
)

accepted_page_versions(
  company_id NOT NULL, source NOT NULL, reporting_scope NOT NULL,
  resolved_source_period NOT NULL, parse_id NOT NULL,
  accepted_at_utc, accepted_by, acceptance_mode,
  PRIMARY KEY(company_id, source, reporting_scope, resolved_source_period)
) WITHOUT ROWID

acceptance_events(
  acceptance_id PK, company_id, source, reporting_scope,
  resolved_source_period, parse_id,
  action, accepted_at_utc, accepted_by, acceptance_mode, reason_json
)
```

Official workbook import tables:

```text
mf_import_files(
  import_file_id PK, publisher, requested_disclosure_month,
  detected_disclosure_month, original_filename, submitted_path,
  content_sha256, blob_id, workbook_type, byte_size, import_state,
  first_observed_at_utc, accepted_at_utc, state_reason_json,
  UNIQUE(publisher, content_sha256)
)

mf_workbook_sheets(
  sheet_id PK, import_file_id, sheet_position, sheet_name_raw,
  detected_scheme_code, detected_scheme_name_raw, detection_evidence_json,
  parse_status, table_start_row, table_end_row,
  UNIQUE(import_file_id, sheet_position)
)

mf_workbook_cells(
  sheet_id, row_position, column_position, coordinate,
  raw_value, display_value, formula, data_type, number_format,
  cell_role, normalized_field_code,
  PRIMARY KEY(sheet_id, row_position, column_position)
)

mf_schemes(
  scheme_id PK, publisher, scheme_code, current_name,
  UNIQUE(publisher, scheme_code)
)

mf_scheme_registry_versions(
  registry_version_id PK, scheme_id, scheme_name_raw, scheme_category,
  scheme_management_type, is_equity_scheme, valid_from_month, valid_to_month,
  classification_source, classification_rule_version, accepted_at_utc
)

mf_accepted_versions(
  publisher, scheme_id, disclosure_month, import_file_id, accepted_at_utc,
  accepted_by, acceptance_mode,
  PRIMARY KEY(publisher, scheme_id, disclosure_month)
) WITHOUT ROWID
```

Normalized tables:

```text
company_metric_observations(
  parse_id, reporting_scope, metric_code, label_raw, position,
  display_value, value_decimal, unit_code, scale_multiplier, missing_kind,
  first_observed_at_utc, source_published_at_utc,
  available_at_utc, availability_basis
)

quote_observations(
  parse_id, reporting_scope, quote_timestamp_raw, quote_timestamp_utc,
  quote_timezone, quote_timestamp_status, price_display_value,
  price_decimal, currency, first_observed_at_utc
)

financial_observations(
  parse_id, reporting_scope, section_code, line_item_code, line_item_raw,
  row_position, period_label_raw, period_end, period_type, fiscal_year,
  column_position, first_observed_at_utc, source_published_at_utc,
  available_at_utc, availability_basis, display_value, value_decimal TEXT,
  unit_code, scale_multiplier, missing_kind
)

growth_observations(
  parse_id, reporting_scope, metric_code, metric_label_raw, period_code,
  period_label_raw, display_value, value_decimal, unit_code, missing_kind,
  first_observed_at_utc, source_published_at_utc,
  available_at_utc, availability_basis
)

shareholding_observations(
  parse_id, reporting_scope, frequency, holder_code, holder_label_raw,
  row_position, period_label_raw, period_end, display_value,
  value_decimal, unit_code, classification_regime, missing_kind,
  first_observed_at_utc, source_published_at_utc,
  available_at_utc, availability_basis
)

document_links(
  parse_id, reporting_scope, document_type, period_label_raw, title_raw,
  position, source_url, publisher, availability_status
)

mf_holding_observations(
  parse_id, company_id, source_scheme_id, scheme_name_raw, row_position,
  period_label_raw, period_end, metric_code, column_position,
  display_value, value_decimal, unit_code, missing_kind,
  source_fund_classification, classification_rule_version,
  first_observed_at_utc, source_published_at_utc,
  available_at_utc, availability_basis
)

mf_page_summaries(
  parse_id, period_end, headline_raw, headline_buyers, headline_sellers,
  headline_net_change, table_total_shares, table_total_change,
  computed_buyers, computed_sellers, computed_holders,
  computed_net_change, validation_status,
  first_observed_at_utc, source_published_at_utc,
  available_at_utc, availability_basis
)

mf_official_holdings(
  holding_id PK, import_file_id, sheet_id, scheme_id, disclosure_month,
  source_row, security_name_raw, isin_raw, isin_normalized, company_id,
  quantity_display, quantity_decimal, market_value_display,
  market_value_decimal, market_value_currency, market_value_scale_multiplier,
  percentage_of_nav_display, percentage_of_nav_decimal,
  instrument_type_raw, match_method, match_status,
  first_observed_at_utc, accepted_at_utc,
  UNIQUE(import_file_id, sheet_id, source_row)
)

mf_scheme_month_change_sets(
  change_set_id PK, publisher, scheme_id, previous_month, current_month,
  previous_import_file_id, current_import_file_id, comparison_status,
  reason_code, completeness_evidence_json, engine_version,
  input_fingerprint UNIQUE, generated_at_utc
)

mf_scheme_month_change_validations(
  change_set_id, rule_code, severity, validation_status, details_json,
  PRIMARY KEY(change_set_id, rule_code)
)

mf_scheme_security_month_changes(
  change_set_id, publisher, scheme_id, security_id, isin_normalized,
  previous_month, current_month, previous_holding_id, current_holding_id,
  previous_row_presence, current_row_presence,
  previous_quantity_decimal, current_quantity_decimal, quantity_change_decimal,
  previous_weight_decimal, current_weight_decimal,
  portfolio_weight_change_decimal, change_class,
  previous_import_file_id, current_import_file_id,
  completeness_evidence_json, generated_at_utc,
  UNIQUE(change_set_id, security_id)
)

mf_stock_month_feature_sets(
  feature_set_id PK, publisher, previous_month, current_month,
  aggregation_status, reason_code, completeness_evidence_json,
  engine_version, input_fingerprint UNIQUE, generated_at_utc
)

mf_stock_month_feature_inputs(
  feature_set_id, change_set_id, scheme_id, scheme_classification_id,
  previous_import_file_id, current_import_file_id,
  previous_content_sha256, current_content_sha256
)

mf_stock_monthly_features(
  feature_set_id, publisher, security_id, isin_normalized,
  disclosure_month, feature_scope,
  active_fund_holder_count, new_fund_entries, complete_fund_exits,
  funds_increasing_quantity, funds_decreasing_quantity, funds_unchanged,
  gross_quantity_bought, gross_quantity_sold, net_quantity_change,
  total_portfolio_weight, portfolio_weight_change,
  active_equity_holder_count, passive_holder_count, arbitrage_holder_count,
  accumulation_breadth, distribution_breadth,
  active_fund_accumulation_breadth, generated_at_utc,
  UNIQUE(feature_set_id, security_id, feature_scope)
)

mf_stock_month_feature_lineage(
  stock_month_feature_id, scheme_security_change_id,
  scheme_classification_id, previous_content_sha256, current_content_sha256
)

validation_results(
  validation_id PK, parse_id, rule_code, severity, status,
  expected_value, actual_value, details_json, created_at
)
```

Phase 2 adds these logical tables:

```text
document_artifacts(
  artifact_id PK, company_id, artifact_type, publisher, title_raw,
  event_date, period_end, fiscal_year, fiscal_quarter,
  published_at_utc, first_observed_at_utc, preferred_content_id,
  acquisition_status, transcript_preference_status
)

artifact_sources(
  artifact_source_id PK, artifact_id, discovered_from_source,
  discovery_page_url, document_url, publisher_claim, exchange_announcement_id,
  first_observed_at_utc, last_checked_at_utc, availability_status,
  UNIQUE(artifact_id, document_url)
)

artifact_expectations(
  expectation_id PK, company_id, artifact_type, event_id,
  expectation_basis, expected_after_utc, availability_status,
  first_checked_at_utc, last_checked_at_utc, next_check_at_utc,
  attempt_count, terminal_reason, last_error_json,
  UNIQUE(company_id, artifact_type, event_id)
)

artifact_check_events(
  check_id PK, expectation_id, checked_at_utc, source,
  requested_url, result_status, evidence_json, discovered_artifact_id
)

artifact_contents(
  content_id PK, artifact_id, blob_id, media_type, content_sha256,
  byte_size, extraction_status, is_official, created_at_utc,
  UNIQUE(content_sha256)
)

document_pages(
  content_id, page_or_slide_number, native_text, ocr_text,
  selected_text_origin, extraction_confidence, layout_json,
  PRIMARY KEY(content_id, page_or_slide_number)
)

transcripts(
  transcript_id PK, artifact_id, content_id, origin,
  source_media_content_id, language, engine, model_version,
  options_json, created_at_utc, validation_status
)

transcript_segments(
  transcript_id, segment_position, start_ms, end_ms, speaker_raw,
  speaker_normalized, text_raw, confidence, section_kind,
  PRIMARY KEY(transcript_id, segment_position)
)

recording_normalizations(
  normalization_id PK, artifact_id, source_content_id, normalized_content_id,
  resolution_basis, probe_engine, probe_engine_version, normalizer_engine,
  normalizer_engine_version, options_json, source_duration_ms,
  source_container_formats_json, source_streams_json, normalized_duration_ms,
  normalized_container_formats_json, normalized_streams_json,
  normalization_status, started_at_utc, completed_at_utc, error_json
)

artifact_content_relationships(
  relationship_id PK, artifact_id, source_content_id, derived_content_id,
  relationship_type, created_at_utc
)
```

`document_artifacts` represents the logical event/document; `artifact_sources`
keeps all URLs and discovery paths; `artifact_contents` keeps immutable official
files and generated derivatives. A corrected issuer transcript is a new content
blob linked to the same artifact, not an overwrite. The preferred content pointer
can move while all prior contents remain auditable.

Mutable identity/mapping and accepted-version pointers may be upserted. Fetches are
append-only; blobs and parse artifacts are content/version deduplicated. Normalized
observations belong to a `parse_id`, not a fetch. Parser code must not use
`DELETE ... WHERE symbol = ?` before inserts.

`value_decimal` is declared `TEXT`. Python `Decimal` performs arithmetic and
validation; SQL must not perform numeric arithmetic on this column. A later
analytical export may use DuckDB/Parquet `DECIMAL` columns.

## 6. Fetching and snapshot rules

- One client and independent rate limiter per host.
- Conservative configurable delay with jitter; start at 4–6 seconds per host.
- Honor `Retry-After` and use bounded exponential backoff for transient `5xx` and
  `429` responses.
- Use conditional requests (`ETag`, `Last-Modified`) when supported.
- Record redirects and final URL.
- Store system timestamps as UTC (`fetched_at_utc`, `parsed_at_utc`, and
  `accepted_at_utc`). Normalize the displayed quote time separately as
  `quote_timestamp_raw`, `quote_timestamp`, `quote_timezone`, and
  `quote_timestamp_status`; it is never interchangeable with fetch time.
- Detect login, blocked, consent, CAPTCHA, and structurally unexpected pages before
  parsing.
- Authentication is optional and explicit. Read cookies from a user-owned file
  outside the repository, require restrictive file permissions, never print cookie
  values, and record only `auth_mode=public|authenticated`.
- A public parse and authenticated parse are different snapshots. Missing custom
  ratios on a public page are `unavailable_without_auth`, not zero.
- Cache policy is `offline`, `prefer-cache`, or `refresh`; it is not a single
  ambiguous `--force` switch.
- A failed/partial response is kept for diagnosis but never promoted as a valid
  current snapshot.
- Scheduled discovery selects durable due rows rather than blindly rescanning the
  whole universe. Claiming/locking a due row and recording its result must be safe
  when two scheduler instances overlap.
- Retry timing is persisted in UTC and survives process restarts. Technical
  failures use bounded exponential backoff; expected-but-unpublished artifacts use
  the slower publication schedule in section 4.4.

## 7. Parsing rules

- Parsers are pure functions: `bytes + page_metadata -> validated model`.
- Select sections by stable semantic IDs/attributes first, headings second, and
  CSS position last.
- Preserve duplicate labels by using section + row/column position; never use only
  a label as a primary key.
- Keep label canonicalization in a versioned mapping file. Unknown labels are
  stored and reported, not dropped.
- Parse numbers with `Decimal`; never store financial values through Python
  `float`.
- Normalization is locale-aware for Indian comma grouping, currency symbols,
  percentages, `Cr.`, counts, signs, and Unicode minus/non-breaking spaces.
- Date derivation is versioned. If a source label cannot be resolved without an
  assumption, keep `period_end = null` and emit a warning.
- Every section returns `ok`, `partial`, `missing_expected`, or `failed`, with row
  counts and errors. Overall success requires all required sections for that page
  type to be `ok`.
- Required, conditional, optional, and deferred sections come from the versioned
  source manifest; they are not improvised inside parser code.
- `hidden` means a source-declared cell in an in-scope visible component whose
  value is intentionally concealed or unavailable. It never means “extract every
  DOM node with `display:none`.” Source cells retain `raw_text`,
  `normalized_display_text`, `visibility_state`, `rowspan`, and `colspan` so
  responsive duplicates and merged cells cannot be flattened silently.

Pydantic models validate parser output before a database transaction begins. A
page insert and all its observations commit atomically.

## 8. Validation and quality gates

### 8.1 Generic

- Required page identity matches the requested source identifier.
- Required sections exist and have non-zero rows/columns.
- Extracted source cell count equals fixture cell count.
- Row and column order matches the fixture.
- Every non-empty numeric display value is either typed or explicitly marked with
  a parse error.
- No unknown label, duplicate-key collision, dropped row, or shifted column is
  silent.

### 8.2 Screener

- `Total Assets` approximately equals `Total Liabilities` within display-rounding
  tolerance.
- Shareholding percentages approximately total 100 per period when all required
  categories are present.
- Quarterly, fiscal-year, and TTM periods do not collide.
- Reporting scope in the page agrees with the requested scope.
- Source warning and shareholding regime tags are present across the Sep-2022
  boundary.

### 8.3 Trendlyne

- Current shares minus comparison shares agrees with row month change when all
  values exist.
- Sum of fund-row changes is compared to the total row.
- Derived buyer/seller/holder counts use row changes and are compared to the
  headline only as a warning.
- `-`, blank, and zero are not conflated.
- Company ID, source symbol, selected month, and table header month agree with the
  requested mapping.

These rules remain for preserved fixtures and accepted historical rows; they do
not authorize network access.

### 8.4 Official AMC workbooks

- CLI publisher/month inputs agree with workbook evidence.
- Workbook signature matches XLS or XLSX content; extension alone is insufficient.
- Every detected portfolio table has unambiguous scheme identity and required
  security-name, ISIN, quantity, market-value, and NAV-weight columns.
- Source row/cell counts, coordinates, formulas, display values, sheet order, and
  table boundaries are preserved.
- ISINs pass structural validation when present; invalid or missing ISINs are
  retained but cannot be silently name-matched.
- Parsed numeric cells reconcile with their source display and declared unit.
- Duplicate scheme/security/month rows are either proven source duplicates or
  block acceptance.
- Scheme weights and any source total rows are reconciled within declared
  display-rounding tolerance.
- Month-to-month changes run only across consecutive accepted, complete scheme
  disclosures; a missing month cannot create entries or exits.
- All derived feature totals are reproducible from accepted pair-level changes.
- An official observation always wins source precedence over `mfdata`.

Validation results are stored, not merely logged. `error` blocks promotion;
`warning` permits storage but marks the snapshot as needing review.

### 8.5 Accepted versions

Downstream consumers read only `accepted_page_versions` (or a view built strictly
from it), never “the most recently fetched row.” A parse may be accepted when:

```text
parse_status = ok
validation_status IN (passed, passed_with_warnings)
identity validation passed
all required manifest sections passed
manual approval completed when structural drift or an unknown label exists
```

Default decisions:

| Issue | Decision |
|---|---|
| Trendlyne headline differs from computed fund rows | accept with warning |
| Workbook publisher/month identity mismatch | quarantine |
| Required workbook column or scheme identity missing | quarantine |
| Duplicate official workbook bytes | idempotent no-op plus observation |
| Third-party value conflicts with official workbook | keep official; report warning |
| Balance-sheet display-rounding difference | accept with warning |
| Requested identity differs from page identity | reject |
| Required table missing | reject |
| Unknown new label or structural fingerprint | manual review |
| CAPTCHA, login, or blocked page | reject |

Every accepted, superseded, or rejected decision is appended to
`acceptance_events`. Acceptance also updates the convenient current pointer for one
company/source/scope/resolved-period. It does not mutate or delete its blob, parse
artifact, observations, validations, or acceptance history.

For Trendlyne, the request alias and page period are distinct:

```text
requested_source_period = latest
resolved_source_period = 2026-07
period_end = 2026-07-31
```

The page-selected month must be parsed and identity-validated. Accepted-version
keys always use `resolved_source_period`; the literal `latest` is never canonical.

Official workbook acceptance is separate from page acceptance. An import is
promoted only after the file, every in-scope scheme sheet/table, all required
source cells, normalized holdings, and security-match statuses pass validation.
The pointer is keyed by publisher, canonical scheme identity, and disclosure
month. Superseding a workbook moves only that pointer and appends an acceptance
event; prior bytes, parsed cells, holdings, and features remain auditable.

## 9. Test strategy

All parser and database tests are offline.

Required fixtures before any batch run:

1. LAURUSLABS consolidated Screener page.
2. LAURUSLABS standalone Screener page.
3. RBA latest Trendlyne page containing the known headline/total inconsistency.
4. At least one older RBA month URL.
5. A Screener company with missing/short history or unusual rows.
6. A Trendlyne page with a fund entering/exiting, so dash versus zero behavior is
   tested.
7. Sanitized login/blocked/unexpected-page fixtures.
8. Twelve official PPFAS consolidated monthly workbooks spanning consecutive
   months, supplied locally by the operator.
9. Two consecutive official workbooks from each of at least two other AMCs with
   structurally different sheet/table layouts.
10. Mutated workbook fixtures covering wrong month, missing required column,
    duplicated holding, invalid ISIN, formula cell, merged header, incomplete
    scheme disclosure, and third-party/official conflict.

Tests:

- snapshot/golden tests for source-faithful JSON;
- manifest coverage tests proving every declared page component is captured,
  conditionally absent, or explicitly deferred;
- unit tests for numeric, unit, missing-value, and period parsing;
- section parser tests with exact row/column counts;
- validation-rule tests, including intentionally corrupted fixtures;
- repository transaction/idempotency tests;
- migration tests from an empty database;
- export reproducibility test: the same fixture and parser version produce the
  same normalized output.
- workbook hash/idempotency and zip-bomb/external-link/macro safety tests;
- exact source-cell and normalized-holding golden tests for each workbook format;
- consecutive-month entry/exit/increase/decrease/unchanged tests that prove a
  missing month or quarantined file cannot create a false change;
- active-equity feature-view tests proving passive, ETF, arbitrage,
  fund-of-funds, and unknown observations remain stored but excluded;
- source-precedence tests proving `mfdata` cannot overwrite official holdings.

Live smoke tests are separate and opt-in. They fetch one page per source, write a
new snapshot, compare the structural fingerprint with the latest accepted fixture,
and do not automatically update golden files.

Phase 2 adds offline fixtures for a text PDF, scanned PDF, presentation, rating
report, official transcript and short recording. Tests prove content-hash
deduplication, page/slide order, OCR fallback, official-transcript preference,
recording fallback, timestamp monotonicity and the distinction between official
and machine-generated transcripts. Media transcription is integration-tested on
a short local fixture and is never invoked by ordinary parser unit tests.

Delayed-publication tests use a fake clock and a source fixture that returns
missing, then recording-only, then an official transcript. They prove due-time
selection, persisted retry state, overlap safety, no duplicate artifacts, bounded
termination/review behavior and preferred-content promotion without deleting the
machine transcript. Separate fixtures prove `no_rows_reported`,
`source_not_covered`, `not_offered`, `inaccessible` and `discovery_failed` cannot
collapse into the same missing value.

## 10. Project structure

```text
scrapper/
  PLAN.md
  README.md
  requirements.txt
  config.py
  cli.py
  core/
    http.py
    hashing.py
    numbers.py
    periods.py
    missing.py
    logging.py
  models/
    common.py
    screener.py
    trendlyne.py
    mf_workbook.py
  sources/
    manifests/
      screener_company.yml
      trendlyne_monthly_holdings.yml
      amc_workbook_ppfas.yml
    screener/
      urls.py
      company.py
      metrics.py
      statements.py
      growth.py
      shareholding.py
      documents.py
    trendlyne/
      urls.py
      identifiers.py
      monthly_holdings.py
      history.py
    amc_workbook/
      detector.py
      workbook.py
      tables.py
      ppfas.py
      registry.py
      normalization.py
    mfdata/
      client.py
      adapter.py
  db/
    migrations/
    database.py
    repository.py
  validation/
    common.py
    screener.py
    trendlyne.py
    mf_workbook.py
  exports/
    json_export.py
    quality_report.py
  pipeline/
    scrape_one.py
    scrape_batch.py
    import_mf.py
    derive_mf_features.py
    reconcile_artifacts.py
  documents/
    discovery.py
    availability.py
    downloader.py
    pdf.py
    powerpoint.py
    ocr.py
    classification.py
  transcripts/
    media.py
    transcribe.py
    segments.py
    validation.py
  tests/
    fixtures/screener/
    fixtures/trendlyne/
    fixtures/mf_workbook/
    golden/
```

Keep modules cohesive and normally under 400 lines. The line target does not
justify splitting one responsibility into arbitrary fragments.

## 11. CLI contract

```text
scraper init-db
scraper fetch screener --symbol LAURUSLABS --scope consolidated
scraper fetch trendlyne --symbol RBA --month latest
scraper parse --blob-id <id>
scraper reprocess-screener --symbol ETERNAL --scope consolidated --blob-id <id> --peer-blob-id <id>
scraper validate --parse-id <id>
scraper accept --parse-id <id>
scraper export --run-id <id>
scraper scrape-one --source screener --symbol LAURUSLABS --scope consolidated
scraper scrape-one --source trendlyne --symbol RBA --month latest
scraper backfill trendlyne --symbol RBA --months 12 --dry-run
scraper import-mf --publisher ppfas --month 2026-06 --path scrapper/store/mf_import/inbox/ppfas/2026-06/
scraper import-mf --publisher ppfas --month 2026-06 --path scrapper/store/mf_import/inbox/ppfas/2026-06/ --dry-run
scraper import-mf-directory --path scrapper/store/mf_import/inbox/ --dry-run
scraper import-mf-directory --path scrapper/store/mf_import/inbox/
scraper derive-mf-features --publisher ppfas --through-month 2026-06
scraper export-mf-features --scope active_equity --through-month 2026-06
scraper mf-coverage-report --publisher ppfas --through-month 2026-06 --months 12
scraper discover-documents --symbol LAURUSLABS \
  --manifest /path/to/saved-discovery-manifest.json --dry-run
scraper fetch-documents --symbol LAURUSLABS --artifact-type presentation
scraper extract-document --artifact-id <id>
scraper normalize-credit-rating --artifact-id <id>
scraper ingest-official-transcript --artifact-id <id>
scraper normalize-recording --artifact-id <id>
scraper transcribe --artifact-id <id> --only-if-no-official-transcript
scraper reconcile-artifacts --due --limit 100 --dry-run
scraper reconcile-artifacts --due --limit 100
scraper artifact-coverage --symbol LAURUSLABS
```

Every command supports structured output and a non-zero exit status on failure.
Workbook dry-run reads and validates without writing the database, copying files,
or changing inbox state. Its output lists exact files, hashes, detected
publisher/month/schemes/tables, row counts, validation decisions, and proposed
accepted/rejected/quarantine state. Batch import is resumable by content hash and
produces a final quality summary. Existing Trendlyne network commands remain
present but disabled by operator policy.

## 12. Execution chunks

The architecture above is implemented through the independently testable chunks
in [`EXECUTION.md`](EXECUTION.md). One chunk is completed and inspected before the
next begins. M01–M09 now cover local official-workbook inventory, PPFAS source
cells, exact holdings/security matching, the versioned scheme registry, the
accepted/rejected/quarantine workflow, and auditable consecutive accepted-month
scheme/security changes plus exact stock-level feature views with workbook and
delta lineage, proven across the complete official Jul-2025–Jun-2026 slice, plus
the shared publisher-adapter contract proven with official May/June 2026 Groww
and Motilal Oswal fixtures, plus the optional isolated mfdata cross-check with
official-first precedence. Phase 2 D01 adds offline primary-source artifact
discovery with immutable page evidence, URL provenance and logical/content
identity foundations. D02 adds bounded, signature-validated PDF/PPT/PPTX,
audio and video acquisition with immutable attempt metadata, redirect evidence,
diagnostic rejections and content-hash deduplication across source URLs.
D03 adds idempotent PDF/PPT/PPTX extraction with page/slide provenance,
ordered native text blocks, tables, image/chart references, speaker-note
availability and page-local OCR fallback whose output remains separate from
native and selected text. D04 adds page-evidenced credit-rating classification,
source-faithful tables/passages, exact action normalization and review/conflict
gates that keep unresolved rows outside the preferred canonical view.
D05 adds ordered official-transcript segments with raw speaker labels, explicit
prepared-remarks/Q&A boundaries, page-fragment evidence and an official-first
preference/fallback gate. D06 adds primary-source recording resolution plus a
separately content-addressed, duration/codec-validated 16 kHz mono PCM audio
derivative without replacing the original media. D07 adds an official-first,
event-matched, timestamp-validated machine-transcript fallback with immutable
media/model provenance, neutral speakers and idempotent run identity. D08 adds a
terminal document-quality report and bounded all-artifact pilot. D09 adds
evidenced artifact expectations, append-only checks, overlap-safe due claims,
bounded persisted retries and company/event/artifact retry coverage. **D01
through D09 are complete.**
The unfinished Trendlyne P03 live backfill is
frozen, not a blocker for the official-workbook path. The
immediate PPFAS 12-month milestone and bounded cross-AMC pilot are complete.

## 13. Phase boundaries

Phase 1 remains deliberately limited to structured HTML, local structured
workbooks, deterministic mutual-fund monthly features, and document-link
metadata. It does not block or exclude the Phase 2 pipeline described above.

### Explicitly out of scope for Phase 1

- downloading PDFs, PPTs, or recordings;
- Docling/OCR;
- Whisper transcription;
- LLM-based qualitative extraction;
- CatBoost or any other selection model;
- feature engineering beyond the specified deterministic mutual-fund monthly
  accumulation features, and all ML train/test splitting;
- automatic AMC crawling, browser automation, or a 51-AMC rollout;
- live Trendlyne requests or fixture expansion;
- automatic trading or investment recommendations.

Document/media acquisition, deterministic extraction and recording transcription
are Phase 2 work. LLM analysis, model building and trading decisions remain
downstream work. Phase 1 succeeds when it preserves the completed Screener slice,
produces a complete, clean, versioned PPFAS 12-month official-workbook slice with
stock-level accumulation features, and provides a reliable document-discovery
handoff.

Direct, low-latency NSE/BSE feeds, same-day event metrics, alerting and signal
research are intentionally deferred to [`SCRAPPER_2.0.md`](SCRAPPER_2.0.md). The
Phase 2 artifact reconciliation worker described here is still part of reliable
collection; it does not calculate trading signals.

### Explicitly out of scope for Phase 2 collection

- treating LLM summaries as source facts;
- silently replacing official transcript wording with generated text;
- fabricating speaker identity or inaudible words;
- bypassing paywalls, access controls, CAPTCHAs or media restrictions;
- automatic trading or investment recommendations.

## 14. Definition of done

The immediate mutual-fund acquisition milestone is complete (M07 evidence in
`store/exports/mf_coverage/ppfas/through-2026-06.json`) because:

- 12 consecutive PPFAS consolidated monthly workbooks import from the local
  inbox without a network fetch;
- every accepted workbook is immutable, content-addressed, source-cell faithful,
  and identified by publisher and disclosure month;
- scheme identities and registry classifications are versioned;
- securities match primarily through structurally valid ISINs, with unresolved
  rows retained and excluded from features;
- pair-level consecutive-month changes and stock-level features reproduce from
  accepted holdings using exact decimal arithmetic;
- the active-equity-only view excludes passive, ETF, arbitrage,
  fund-of-funds, and unknown schemes without deleting their observations;
- dry-run, accepted, rejected, and quarantine workflows are tested and
  idempotent;
- no missing or quarantined disclosure creates a false new entry or complete
  exit;
- the PPFAS export contains all specified all-scheme and active-equity stock-level
  features for each valid month pair.

The follow-on format-generalization pilot is complete: two consecutive months
from Groww and Motilal Oswal pass the shared normalized, acceptance, comparison,
feature and lineage contract while preserving their distinct workbook layouts.
The optional `mfdata` adapter is also complete: every row is visibly
third-party/unverified, conflicts and gaps are reported, and no third-party row
can overwrite or outrank an official observation.

The broader Phase 1 foundation remains complete only when:

- every implemented source parser operates from saved fixtures; network-backed
  sources use separate opt-in smoke tests while AMC imports remain local-only;
- raw source bytes and fetch/import metadata are immutable and content-addressed;
- each visible in-scope table cell is represented in source order;
- normalized values retain source display text, unit, scale, period semantics, and
  missing-value meaning;
- consolidated and standalone data cannot overwrite one another;
- preserved Trendlyne mappings and derived totals remain reproducible from the
  existing fixtures without any new network access;
- every accepted record traces through `parse_id` and `blob_id` plus its
  `fetch_id` or `import_file_id` to `run_id`, source URL or submitted local path,
  observation time, content hash, and parser version;
- fetch/import events, source blobs, and parse artifacts are separate, so
  identical bytes are parsed once per parser/manifest version and can be reparsed
  later;
- every observation distinguishes `period_end` from `available_at`; historical
  rows from a current page cannot appear available before `first_observed_at`;
- downstream reads use only an explicitly accepted parse version;
- acceptance history remains append-only even after the current pointer is
  superseded;
- preserved Trendlyne `latest` fixtures resolve to a canonical `YYYY-MM` period
  before acceptance;
- individual shareholding rows never carry a `cross_regime` value;
- schema/parser changes are migration- and fixture-tested;
- partial parses and validation anomalies are queryable and cannot masquerade as
  clean data.

Phase 2 is complete only when:

- primary-source discovery covers exchange filings, issuer IR pages and official
  rating-agency publications for the approved company universe;
- every downloaded document/recording is immutable, content-addressed and linked
  to all discovery URLs;
- PDF/PPT text extraction retains page/slide provenance and uses OCR only as a
  recorded fallback;
- an official transcript is preferred whenever usable;
- a recording-only event produces a timestamped, provenance-rich transcript
  labelled as machine-generated;
- re-running discovery/download/extraction/transcription is idempotent;
- every evidenced but not-yet-published artifact has durable retry state and every
  due retry is bounded, resumable and auditable;
- missing data is classified as pending, not offered, not applicable, no rows,
  uncovered, inaccessible, failed or unknown rather than silently stored as null;
- a later official transcript can become preferred without deleting or rewriting
  an earlier machine-generated transcript;
- no artifact is considered accepted while its company identity, type, period or
  provenance is unresolved.
