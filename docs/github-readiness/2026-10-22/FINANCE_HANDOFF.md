# Finance-only intelligence development handoff

**Suitable for cautious read-only development; not yet a certified universal scoring dataset.** Reuse the financial feature interface already integrated by C25.1/C28.1. Do not rebuild collection, migrate schemas, create another financial truth store or implement the intelligence engine as part of this audit.

## Existing access contract

`scrapper.research.features.company_quarter_features(conn, company_id, decision_time, scope='standalone', feature_version='v1', period=..., sector=..., source=..., evidence_kind=...)` is SELECT-only, with a caller-owned read-only connection. Compatibility import: `scrapper.pipeline.ml_contracts.company_quarter_features`; the objects are identical and tested. `build_screener_ml_features` is an older snapshot-metric export without a decision-time argument and is **not the preferred statement-feature interface**. No general finance REST API was established; the Python/SQLite facade is the current supported boundary, while Control Center endpoints concern operational status.

For future development, supply a verified **consistent isolated snapshot**, `sqlite3.Row`, `query_only=ON` and checked migration compatibility. `db.database.connect(path, read_only=True)` avoids configured-directory creation and journal-mode switching; do not call its default writable mode, `init-db` or a migration API against production. Ordinary SQLite read-only mode can interact with WAL/shared memory: use a consistent snapshot or the existing guarded cold-state reader, **never immutable mode against changing production**. Mount financial originals read-only if original-source reconciliation is needed.

Recommended interface output retains company/source identity, exact period and interval, scope, accepted parse and event version, blob/observation IDs and hashes, raw label/display, normalized value, unit/scale, missing/applicability status, first capture/publication/acceptance availability, transformation version and explicit quality reasons. The existing result already carries substantial lineage/status information; any future extension should preserve it and remain bounded/read-only. No new endpoint or interface implementation was added here.

## Data surfaces

- Identity: `companies`, dated/current `security_identifiers`, verified `source_identifiers`, exchange registry/links/security versions. Symbols/URLs are aliases, not company keys.
- Financial acceptance/version: `accepted_page_versions`, immutable `acceptance_events`, `parse_artifacts`, `validation_results`, `screener_company_collection_state`. The current pointer cannot reconstruct revisions alone.
- Statements: `financial_observations` sections `quarters`, `pnl`, `balance_sheet`, `cash_flow`, `ratios`, with line-item raw/code, original display, decimal, period/period type, reporting scope, units/scale, missing-kind and availability fields.
- Evidence: `source_sections`, `source_table_cells`, `response_blobs`, `fetch_observations`, supplementary-parse relationships. Persisted snapshot paths require the matching original financial-source store, not merely a database copy.
- Context: `company_metric_observations`, `quote_observations`, `growth_observations`, `shareholding_observations`, company profile links/text and collection-scope market-cap eligibility. Snapshot metrics do not become historical quarter fundamentals by assigning a date.
- Separate document evidence: artifact/content/source/job/download and extraction records. Original hash and successful parsing do not establish financial fact accuracy or document officialness. Do not substitute experimental model claims for accepted statement observations.

## Measured coverage and limits

The October7/8 AR snapshots each contain4,428 registry companies and9,818,117 **versioned observation rows**, including9,312,808 standalone and505,309 historical consolidated rows. There are6,328 accepted pointers:5,341 standalone and987 consolidated; pointer count is not a distinct-company count. Their financial periods span2002-03-31 to2026-06-30 across77 distinct period ends. This does not prove complete history for every company. Some accepted-period fields contain `1970-01` or `not_applicable`; exclude unresolved/sentinel identity rather than interpreting the string range as genuine coverage.

Rows by section: quarters2,802,646; P&L2,614,776; balance sheet2,031,366; cash flow1,216,212; ratios1,153,117. Period-type totals: fiscal_year6,833,119; quarter2,802,646; ttm182,352. Missing-kind blank444,096 rows; nonblank/missing-kind absence is not by itself numeric completeness. These totals include old/rejected/superseded parses and must not be used as a ready-to-score accepted universe. Schema/count/hash details and group dimensions are in databases.json.

Later production evidence proves June and September2026 financial acceptances (Phase3 and October22 reports), but **no fresh entire production financial coverage census was run**. Latest eligible companies, period completeness and field availability must be measured on a future current consistent snapshot. The read-only October22 operational queue census is not a financial coverage report.

B0 proved exportability for29 purposeful accepted June-company examples, with377 stored-source cells/hash-verified HTML values checked. C25's retained feature cohort is29 rows/464 values and remains exploratory; the broader100-company and primary-filing reconciliation gates were not closed by this audit. All377 B0 observations lacked source-publication timestamps. Present captures can support availability **from actual capture/acceptance**, not historic filing-time/backtest knowledge. Version changes and non-March year ends require explicit interval and cutoff handling. Fresh tests cover scope, nulls, signed losses, units, cutoff/revisions, applicability and facade identity; they do not expand the reconciled cohort.

## Classifications and missing fields

No governed complete issuer-sector taxonomy with effective dates was established. The existing facade accepts `industrial`, `bank`, `nbfc`, `insurer`, `unknown`; the caller must supply reviewed classification/provenance. B0's25 non-bank/four bank-or-NBFC sample groups are sampling aids, not an authoritative company classification table. Exchange industry strings, Focus archetypes and MF scheme classifications are different concepts. Unknowns remain explicit and suppress inappropriate formulas.

For industrial companies, existing features include quarterly revenue/PBT/PAT/EPS, operating profit/margin, QoQ/YoY revenue/PAT, matched annual borrowings/CFO/FCF and conditional debt relationships. Missing or nonpositive comparator rules preserve null/invalid status. For banks/NBFCs, revenue/financing profit/margin have different semantics; deposits apply to banks. Insurers have no established complete industrial substitute. Current fields are insufficient to claim complete asset-quality, capital-adequacy, provision/credit-cost, liquidity, NIM, solvency or embedded-value coverage. Some raw source labels may contain such metrics, but full availability, normalization, interval and independent verification are **not established**. Avoid describing unmeasured fields as absent from every source.

Additional gaps: primary-filing reconciliation, publication timestamp coverage, per-company continuous history, restatement/version comparability, balance-sheet interim versus annual interval validation, financial-year calendars, denominators, audited status, currency/share changes and provenance-qualified classification. Preserve source-unavailable premium commentary as unavailable. ₹580crore collection eligibility is an acquisition rule, not an investment recommendation.

## Development boundary

Finance screening should produce transparent inputs, quality/applicability states and explainable finance-only criteria, separated from moat/document evidence and Focus membership. Do not share an industrial operating-margin/debt formula with banks or insurers. Keep bank/NBFC/insurance-specific modules gated on their own validated inputs. Do not train on unapproved Focus negatives, resurrect superseded B2 thresholds, backdate latest values, or interpret engineering completeness as a buy/quality score.

Smallest subsequent development scope: review the existing facade against a retained consistent snapshot, define company/period/sector coverage and missing-data contracts, and reconcile a bounded cohort against already stored originals. That work needs its own selected scope; this audit implements no engine, score, training or source request.

Late concurrent source: `ops/host/acquisition_queries.py` and the terminal observation modules appeared during finalization and describe bounded read-only status/data queries. They received static inspection only; they are not a verified replacement for the financial feature facade or a newly accepted finance API. Review their exact output/provenance contract separately before depending on them.
