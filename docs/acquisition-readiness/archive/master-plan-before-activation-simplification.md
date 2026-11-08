# Trading AI — Research and Document Intelligence Master Plan

**Purpose:** Consolidate the project roadmap, post-cleanup document-lifecycle improvements, and Document Intelligence V1 extraction schema in one navigable reference.

## How to use this document

The current-phase section records the acquisition-readiness gate and its acceptance conditions. Part 1 sets the overall research and ML direction, Part 2 defines operational document-lifecycle behavior, and Part 3 is the V1 contract for facts to extract from credit-rating reports, investor presentations, and concalls.

Where topics overlap, use the current-phase section as the release gate, Part 2 for lifecycle behavior, Part 3 for V1 extraction fields and evidence requirements, and Part 1 for the broader research roadmap.

## Contents

- [Current phase: Acquisition readiness gate](#acquisition-readiness)
- [Part 1: Technical ML and Document Intelligence Roadmap](#technical-roadmap)
- [Part 2: Post-Cleanup Document Lifecycle Plan](#document-lifecycle)
- [Part 3: Document Intelligence V1 Schema](#v1-schema)

<a id="acquisition-readiness"></a>

## Current phase: Acquisition readiness gate

**Status as of 7 October 2026 after the production black-box audit:** Structural cleanup through C28.1 is reported complete, but the acquisition layer is **not ready to freeze**. The read-only production audit is now complete and changes the phase from “prove what is live” to **“fix a bounded set of proven operational/behavioral gaps, then prove the whole chain end to end.”**

The audit established three different truths that must continue to be kept separate:

| Truth layer | Current finding |
|---|---|
| `CODE_CAPABILITY` | The cleaned C28.1 source contains the intended acquisition, recovery, processing and scope mechanisms, with retained cleanup evidence including the previously reported 1,302-test run. |
| `LIVE_DEPLOYED_STATE` | The cleaned C28.1 source is **not deployed**. All Trading AI containers were stopped at the audit cut-off; the stopped core roles used the older `c05.3-release-20261003` image / baseline commit `db7564a1…`. |
| `LIVE_DATA_COVERAGE` | The production DB and bounded document-store reads are healthy enough to audit, but they show stale discovery, stopped workers, incomplete document recovery and a large processing backlog. |

The read-only database copy passed `integrity_check` and `quick_check`, matched the production source hash, and required no WAL recovery. The audit therefore provides a reliable baseline for execution planning. It does **not** authorize starting old services, applying migrations, clearing queues, or backfilling documents.

### What the audit proved

The production audit established the following high-level facts:

- **Latest cleaned release is not live.** Production schema stops at migration `068_exchange_source_access_compatibility.sql`; current source includes migrations 069–075. The C28 processing inbox/work tables and trigger are therefore absent in production.
- **Automatic result discovery is currently blocked.** No scheduler is running. BSE result/master access is held after stored HTTP 403 responses; the latest NSE results attempt was rejected by snapshot guards (`row_count_floor`, `future_effective_date`). No scheduled result-run rows exist for Oct 5–7.
- **The ₹580 crore rule is correct in code but not proven live.** `>= 580` is eligible and is covered by tests, but production contains no observed `below_floor → eligible` transition. The scope monitor is stopped and 3,529 scope checks are overdue, including 1,172 currently eligible companies.
- **Document acquisition has real historical coverage but incomplete current coverage.** For the June 2026 cohort, presentations and transcripts have real discovered/downloaded content, but discovery failures, uncovered sources and pending download work remain. Across all history, presentation and transcript queues contain large pending/terminal populations.
- **Continuous extraction is not live.** Production has 44,434 supported contents with extraction `not_started`; no production processing inbox/work tables exist and there is no processing-worker container. The current cleaned source also still requires explicit work materialization after inbox creation, so the end-to-end automatic handoff remains a behavioral gap.
- **Transcript text extraction exists historically, but transcript structuring does not.** The production DB has zero rows in `transcripts`, `transcript_segments`, `transcript_validations`, `transcript_section_boundaries`, `machine_transcription_runs` and `preferred_transcripts`.
- **Credit-rating acquisition is substantial, not empty.** Historical CRISIL/ICRA browser and alternate-source recovery produced thousands of successful contents. Direct unattended CRISIL/ICRA HTTP acquisition remains policy-blocked. Unresolved recovery targets remain and no recovery worker is currently running.
- **HTML rating storage/extraction is strong structurally but not fully normalized semantically.** There are 6,065 publisher-original HTML rating contents; 5,965 have structural extraction `ok`, 100 are not started, and 176 are classified as unknown rating documents. Generated PDF derivatives remain distinguishable from originals.
- **Agency identity/normalization needs cleanup.** India Ratings normalization is joined through publisher `fitch`, and Acuité is represented through `smera` rather than a literal publisher identity. These must be resolved explicitly rather than silently treated as equivalent.
- **Legacy period sentinel data exists.** `1970-01-01` appears in 3,277 `artifact_events` and 4,742 Screener queue rows. It must never be interpreted as a real reporting period; remediation must be lineage-aware rather than a blind mass update.
- **The failure ledger is a backlog ledger, not 82,972 independent bugs.** It contains 82,972 actionable rows, heavily concentrated in a much smaller set of root causes: stopped runtime, exchange-source failures, overdue scope checks, document discovery/download backlog, extraction-not-started content and unresolved rating recovery.

### Current priority principle

Do **not** attack backlog size first. Restore and prove the **new-result lane** first so historical work cannot consume effort while current-quarter acquisition remains broken.

The priority order for the next execution document is:

```text
P0 — make the cleaned production path safe and alive
    ↓
P0 — restore new/revised result discovery
    ↓
P0/P1 — restore live ₹580 scope observation and prove re-entry
    ↓
P1 — make new document → processing/extraction automatic
    ↓
P1 — close current-quarter PPT/transcript discovery and recovery gaps
    ↓
P1 — correct rating-event / agency / normalization semantics
    ↓
P1 — add visual PPT/page interpretation and transcript structuring
    ↓
P1/P2 — long-tail lifecycle, reporting and historical backlog
    ↓
bounded end-to-end proof campaign
    ↓
ACQUISITION GATE = GREEN
    ↓
freeze acquisition interfaces
    ↓
Document Intelligence V1
```

### Required sequence from this point

1. **Create the acquisition-readiness execution plan from this audited baseline.** The execution document must break work into bounded, reversible chunks with explicit prerequisites, production authorization boundaries, data invariants, rollback and proof criteria. This master plan is not itself permission to apply changes.
2. **Prepare and activate the cleaned C28.1 release safely.** Reconcile migrations 069–075 against the audited production clone, prove compatibility/rollback, build an immutable release, and use a bounded canary. Do not restart the old C05.3 release merely to make services run.
3. **Restore the financial-result lane before backlog work.** Resolve the NSE guard failure and BSE access path/policy state, restore the exchange scheduler, and prove that a new/revised result can reach the Screener company queue and accepted-financial state.
4. **Restore collection-scope observation.** Start the market-cap monitor only under the cleaned release, process stale observations in bounded batches, and prove one real or controlled `below_floor → eligible` transition at the inclusive ₹580 crore threshold re-enables the intended financial/document work.
5. **Make document processing continuous for new content.** Newly saved eligible content must enter durable processing without a hard-coded quarter or manual content-ID selection. Keep quarter-specific runs as finite campaigns only.
6. **Close supporting-document discovery/recovery gaps.** Prioritize current-quarter presentation/transcript failures and state inconsistencies; refresh stale links first, then use BSE/NSE/issuer IR/event recovery, then special/operator recovery where allowed. Keep recovered files attached to the same logical artifact.
7. **Correct rating semantics and agency identity.** Ratings are event-driven; research needs latest-valid-before-cutoff plus prior valid event. Resolve publisher/agency aliases explicitly, preserve `NO_NEW_RATING_ACTION`, and improve normalization before treating rating facts as ML-ready.
8. **Complete document understanding prerequisites.** Add chart/image-aware slide/page extraction with evidence and activate transcript segmentation/validation on extracted official transcripts. Recording download/transcription remains policy-selected, not universal.
9. **Add long-tail lifecycle and action-oriented operations.** Replace the six-week dead end with early retry → long-tail → next-quarter final check, and maintain one unified black-box view that separates backlog, technical failure, access block, honest absence, policy exclusion and manual review.
10. **Run a bounded end-to-end proof campaign and freeze only on GREEN.** Prove a normal company-quarter, a late-publication case and an alternate-recovery case from discovery through accepted financials, document acquisition, immutable storage, extraction/structuring, rating context and final Document-Intelligence-ready state.

### Freeze acceptance conditions

The acquisition gate becomes GREEN only when all of the following are evidenced:

- the immutable cleaned release is the actual production release and production data was not silently rewritten;
- production schema/release identity is known and migration history is reconciled;
- exchange discovery is running and a new/revised result can enter the accepted financial lane;
- BSE/NSE source failures are either resolved or represented by an explicit supported fallback/blocked state that does not silently stop the whole pipeline;
- the ₹580 crore inclusive rule is both documented and proven in live/controlled production behavior, including below-floor → eligible re-entry;
- newly saved eligible documents automatically enter durable processing without a hard-coded reporting period;
- current-quarter PPT/transcript discovery and alternate official recovery are functional, with recovered files attached to the same logical artifact;
- ratings use event-aware semantics and cutoff queries return the latest valid event plus prior valid event rather than inventing quarterly missing ratings;
- agency/publisher identities used by rating normalization are explicit and unambiguous;
- HTML rating originals and derivatives retain correct provenance, and unknown/non-report pages cannot silently become accepted rating facts;
- official transcript extraction can proceed into transcript segmentation/validation, while recording fallback remains policy-controlled;
- chart/image information in relevant presentations has a visual extraction path with slide/page evidence;
- the `1970-01-01` sentinel cannot be interpreted as a real period and its historical meaning is documented/remediated safely;
- the unified operational black box reports actionable failures separately from backlog, honest absence, not-yet-published, policy-excluded and manual-review states;
- the bounded proof campaign reaches the documented `READY_FOR_DOCUMENT_INTELLIGENCE` state without manual data rescue.

Until these conditions are met, historical backfills whose original publication availability cannot be proven must remain marked as exploratory/backfilled rather than represented as point-in-time truth. Market-wide mutual-fund expansion, OHLCV, news and global-market context remain later scope unless Focus V1 specifically requires them.

### Audited production baseline — 7 October 2026

The black-box audit is now the baseline for the forthcoming execution document.

| Area | Audited production truth |
|---|---|
| Runtime | 0 running Trading AI containers; 11 stopped core roles used C05.3 |
| Production schema | Latest migration 068; current source has 069–075 |
| Result discovery | RED: BSE stored 403 block; latest NSE result attempt guard-rejected |
| Screener queue | 6,332 accepted; 842 pending, 838 held below market-cap scope; 463 terminal failures not marked below-cap |
| Scope | 1,887 eligible; 3,552 below floor; 51 unknown; 3,529 checks overdue; 0 observed below→eligible transitions |
| Presentations | 34,668 valid official originals all-history; 25,427 supported contents not started extraction |
| Transcripts | 18,525 valid official originals all-history; 15,987 supported contents not started; transcript segmentation tables empty |
| Recordings | 6,475 metadata artifacts, only 130 saved audio/video contents; reference-only behavior remains intentional by default |
| Processing | 44,434 supported contents `not_started`; production processing inbox/work absent |
| CRISIL | 4,610 valid official originals; 3,339 historical browser successes; 385 unresolved recovery targets |
| ICRA | 2,409 valid official originals; 2,077 historical browser successes; 367 unresolved recovery targets |
| HTML ratings | 6,065 publisher-original HTML contents; 5,965 structural extraction OK; 100 not started; 176 unknown-class |
| Failure ledger | 82,972 actionable rows, dominated by a few shared root causes rather than 82,972 independent defects |

### Interpreting the failure ledger

The execution plan must group failures by root cause and lane before selecting fixes. Do not treat every row as a separate engineering defect.

At minimum group into:

```text
RUNTIME / RELEASE
EXCHANGE SOURCE / SNAPSHOT VALIDATION
SCREENER INGESTION
COLLECTION SCOPE
DOCUMENT DISCOVERY
DOCUMENT DOWNLOAD
PROCESSING / EXTRACTION
RATING RECOVERY
RATING NORMALIZATION / IDENTITY
TRANSCRIPT STRUCTURING
DATA-INTEGRITY / LEGACY SENTINELS
POLICY / MANUAL-REVIEW STATES
```

Priority meanings:

- **P0:** blocks the current/new-result lane or threatens data integrity.
- **P1:** causes research coverage gaps or prevents documents from becoming usable evidence.
- **P2:** historical debt, lower-priority backlog, reporting cleanup or operator convenience.

Normal `NOT_ISSUED`, `NOT_YET_ISSUED`, reference-only and policy-excluded states are not failures.

### Audit evidence that must be retained

The production baseline consists of:

```text
TRADING_AI_ACQUISITION_BLACKBOX_AUDIT_2026-10-07.md
failure_ledger.csv
document_coverage.csv
rating_coverage.csv
company_scope.csv
runtime_roles.csv
```

The audit was read-only: the production DB was copied from the Docker volume to disposable storage, source/copy hashes matched, integrity checks passed, and no workers, migrations, publisher requests, downloads or reprocessing were started.

### Decision rule for every future AR fix

For each issue in the execution plan, classify it before changing code:

```text
already proven correct in code + live state
    → close/document

code exists, production is older
    → deploy/prove before redesigning

real behavioral gap in cleaned source
    → smallest targeted implementation

historical backlog / coverage gap
    → bounded recovery or backfill campaign

honest absence / policy state
    → preserve truth; do not manufacture data

legacy ambiguous data
    → quarantine/classify/remediate with lineage, never blind rewrite
```

Only after the acquisition gate is GREEN should the project proceed to Document Intelligence V1:

```text
ACQUISITION GREEN
    ↓
freeze stable acquisition interfaces
    ↓
DOCUMENT INTELLIGENCE V1
    ↓
structured evidence-backed facts
    ↓
longitudinal histories
    ↓
deterministic Focus replication
    ↓
first tabular ML
    ↓
technical / market / global / news layers later
```


<a id="technical-roadmap"></a>

## Part 1: Technical ML and Document Intelligence Roadmap


#### Purpose

This document captures the current direction for the Trading AI project, especially the **Technical ML**, **Focus-list replication**, **document extraction/intelligence**, **historical learning**, and **market-context** layers.

The goal is **not** to build one giant black-box AI that looks at a chart and says BUY or SELL.

The goal is to build a layered research system that:

1. reliably acquires financial and supporting-document data,
2. converts that information into machine-usable structured features,
3. reproduces the Tutor's Focus-list selection process,
4. studies what happens to those selected stocks after good results,
5. formalizes the Tutor's technical concepts,
6. measures which technical signals and combinations actually add value,
7. adds market, sector, global and news context,
8. ranks trade candidates,
9. validates everything out-of-sample,
10. only later moves toward paper trading and eventual small-scale live deployment.

---

### 1. Core philosophy

The Tutor's concepts are treated as **economic and trading hypotheses**.

We do not blindly assume:

> "VCP works."

Instead we formalize it, detect it historically, and test:

- how often it occurs,
- under what conditions it works,
- what happens when it fails,
- whether it adds information beyond other signals,
- whether the effect survives unseen periods,
- whether the effect survives different market regimes,
- whether the effect survives realistic costs and execution constraints.

The project should always move from:

```text
idea
→ definition
→ measurable features
→ historical test
→ out-of-sample validation
→ forward validation
→ paper trading
→ small live capital
```

---

### 2. Correct development order

The intended order is:

```text
1. Make acquisition reliable
      ↓
2. Make quarter-to-quarter automation reliable
      ↓
3. Build financial feature history
      ↓
4. Build document extraction + structured evidence
      ↓
5. Reproduce Tutor Focus lists
      ↓
6. Build deterministic Focus engine
      ↓
7. Train ML to imitate/refine Focus selection
      ↓
8. Study post-result stock behaviour
      ↓
9. Formalize technical concepts
      ↓
10. Generate technical features from OHLCV
      ↓
11. Test individual signals
      ↓
12. Test signal combinations / confluence
      ↓
13. Add India market + sector regime
      ↓
14. Add US/global/futures context
      ↓
15. Add news/event context
      ↓
16. Build ranking / setup-quality models
      ↓
17. Build entry / exit / invalidation / risk
      ↓
18. Walk-forward and paper trading
      ↓
19. Consider small live deployment
```

---

### 3. Phase 1 — Make the scraper/data acquisition boringly reliable

Before ML, the acquisition layer must be dependable.

The cleanup is reported complete at C28.1. The remaining work is the acquisition-readiness gate above: controlled production evidence, targeted recovery/automation/rating/PPT-vision corrections, and an end-to-end proof campaign. Do not interpret the structural cleanup passing as proof that the cleaned release is active in production or that acquisition is ready to freeze.

The system should correctly handle:

- standalone financials,
- quarterly result dates,
- investor presentations,
- concall transcripts,
- concall recordings,
- credit-rating reports,
- exchange filings,
- late-published documents,
- failed URLs,
- alternate official sources,
- saved-page link recovery,
- operator/browser-assisted recovery where necessary,
- timestamps,
- provenance,
- source identity,
- file hashes,
- document versions,
- retries,
- campaign/recovery evidence.

The system should automatically continue every quarter.

Target behaviour:

```text
new result appears
      ↓
result is ingested
      ↓
financial snapshot created
      ↓
expected supporting documents are registered
      ↓
available documents are downloaded
      ↓
missing documents remain tracked
      ↓
late documents are rechecked
      ↓
failed links move through recovery flows
      ↓
original evidence is preserved
```

The scraper should eventually feel boring:

> new quarter arrives → system runs → financials and supporting documents appear correctly.

---

### 4. Phase 2 — Historical financial feature layer

The ML should **not** receive only the current quarter.

Previous-quarter history is essential.

A current result answers:

> How did the company perform now?

Historical quarters answer:

> Is the company improving consistently, accelerating, slowing, turning around, or producing a one-off result?

For every company at a decision date, keep historical sequences such as:

```text
                    Q-4    Q-3    Q-2    Q-1    CURRENT

Sales growth         8%    12%    17%    23%       31%
PAT growth          10%    14%    19%    29%       38%
OPM                 12.1   13.0   14.3   15.6      17.1
Debt                 420    390    350    300       245
```

The system should also calculate trajectory features such as:

```text
sales_trend
sales_acceleration
sales_consistency
pat_trend
pat_acceleration
margin_expansion
margin_consistency
debt_direction
cash_conversion_trend
receivable_days_trend
working_capital_trend
roce_trend
order_book_trend
export_share_trend
guidance_direction
credit_rating_direction
```

Potential historical windows:

- last 4 quarters,
- last 8 quarters,
- later possibly last 12 quarters where reliable.

The model should see both:

```text
raw historical sequence
+
derived trajectory features
```

---

### 5. Point-in-time rule

This is one of the most important rules in the entire project.

When reconstructing a historical decision date, the model may only use information that was actually available at that time.

Example:

```text
decision date: 15 August 2022
```

Allowed:

- financial results published by then,
- documents available by then,
- market data up to then,
- news available by then,
- ratings available by then,
- previous-quarter history available by then.

Not allowed:

- future quarters,
- later revisions,
- later documents,
- future prices,
- future index membership,
- later rating actions,
- later management guidance.

Otherwise the backtest becomes contaminated by **data leakage**.

---

### 6. Phase 3 — Document intelligence

Downloading documents is not enough.

The future ML cannot meaningfully use:

```text
PPT downloaded = YES
```

It needs structured information.

The document pipeline should become:

```text
immutable original document
        ↓
text / table / metadata extraction
        ↓
structured evidence extraction
        ↓
source-linked claims
        ↓
validation / confidence
        ↓
approved research features
```

The extracted information should keep:

- document ID,
- company,
- period,
- publication time,
- source,
- page/section/span,
- exact supporting evidence,
- extraction method,
- model/version,
- confidence,
- validation status.

Example output from a concall or investor presentation:

```text
revenue_guidance = "15–18%"
order_book_value = 4200 crore
order_book_yoy = +31%
capex_plan = 600 crore
margin_guidance = "improving"
export_share = 42%
capacity_expansion = true
management_tone = positive
credit_rating_direction = improving
key_risks = [...]
```

The AI should **not overwrite accepted financial facts**.

Documents enrich the research layer.

---

### 7. What document extraction should identify

Useful structured fields may include:

#### Management

- revenue guidance,
- margin guidance,
- volume guidance,
- demand commentary,
- order inflow commentary,
- capacity utilization,
- capacity expansion,
- capex,
- management confidence,
- change in management tone,
- repeated promises not delivered,
- revised guidance.

#### Business

- order book,
- order-book growth,
- order-book quality,
- segment contribution,
- geography mix,
- export share,
- customer concentration,
- new products,
- new facilities,
- new markets,
- pricing power,
- raw-material pressure.

#### Financial quality

- working-capital commentary,
- debtor concerns,
- inventory concerns,
- cash-flow commentary,
- debt reduction,
- refinancing,
- interest-cost pressure.

#### Credit-rating documents

- rating,
- outlook,
- upgrade/downgrade,
- liquidity position,
- debt-service coverage,
- key rating strengths,
- key rating weaknesses,
- working-capital intensity,
- customer concentration,
- project risk.

#### Concall/transcript

- guidance,
- analyst questions,
- management answers,
- contradictions,
- uncertainty,
- repeated issues across quarters,
- change versus prior quarter.

The important feature is not only the current document.

The system should later compare documents over time:

```text
Q-3 guidance
Q-2 guidance
Q-1 guidance
Q0 guidance
```

and derive:

```text
guidance_improving
guidance_stable
guidance_cut
management_consistency
order_book_acceleration
risk_worsening
```

---

### 8. Phase 4 — Reproduce the Tutor's Focus list

The first real research target is **not profitability**.

It is:

> Can the system reproduce the Tutor's stock-selection process using the same information the Tutor had?

Start with 3–5 historical quarters where Tutor Focus lists and reasoning are available.

For each quarter compare:

```text
Tutor selected?
System selected?
Why?
```

Example evaluation:

```text
Tutor selected:          28
System selected:         31
Overlap:                 24
Tutor picks missed:       4
Extra system picks:       7
```

Every disagreement is useful.

Investigate:

- what feature was missing,
- what Tutor judgment was not encoded,
- whether a document contained something important,
- whether a metric was interpreted incorrectly,
- whether the Tutor uses a rule that has not yet been formalized.

Three to five quarters are enough to begin **Tutor imitation**, but they are not enough to prove profitability.

---

### 9. Deterministic Focus engine first

Before ML, build an explainable rule-based Focus engine.

Why?

Because we first need to understand the Tutor's logic.

A decision should be explainable:

```text
SELECTED because:
- revenue growth accelerating
- PAT growth accelerating
- margin improving
- debt falling
- cash conversion acceptable
- order book strong
- guidance constructive
```

or:

```text
REJECTED because:
- PAT growth driven by other income
- margins deteriorating
- debtor days worsening
- order book weak
```

Only after that base logic exists should ML learn the fuzzy interactions that are difficult to hand-code.

---

### 10. Phase 5 — ML for Focus selection

The first ML problem is mostly **tabular machine learning**.

One observation should represent:

```text
COMPANY + DECISION TIMESTAMP
```

Example:

```text
ABC Ltd
2025-08-12 18:30 IST
```

Features can include:

```text
current financials
historical financial sequence
trajectory features
document features
credit-rating features
management/guidance features
sector
market cap
```

Target:

```text
Tutor_Selected = 0 / 1
```

Important:

> Absence from a Tutor list should not automatically be treated as a negative label unless we know the Tutor actually reviewed and rejected that company.

Better labels include:

```text
SELECTED
REJECTED
NOT REVIEWED / UNKNOWN
```

---

### 11. Recommended first models

Do not start with a giant Transformer.

Start with:

```text
Logistic Regression / Elastic Net
        ↓
Random Forest baseline
        ↓
LightGBM
        ↓
XGBoost
        ↓
CatBoost
        ↓
small neural network later
```

Why?

Because company-quarter financial/document features are structured tabular data.

Tree-based boosting models are strong at nonlinear relationships such as:

```text
IF
PAT growth is high
AND
margin trend is positive
AND
debt trend is improving
AND
order book is strong
THEN
Tutor selection probability rises
```

---

### 12. Phase 6 — Study what happens after strong results

Once Focus selection is reasonably stable, study post-result behaviour.

For each Focus event calculate:

```text
+1 day return
+5 day return
+10 day return
+20 day return
+60 day return

abnormal return vs Nifty
abnormal return vs sector

maximum favourable excursion (MFE)
maximum adverse excursion (MAE)

time to breakout
breakout occurred?
breakout failed?
stop hit?
```

This creates an **event research dataset**.

The key question becomes:

> What kinds of fundamentally strong companies go on to produce strong market moves?

---

### 13. Technical ML — We do not need screenshots

Technical ML does not need a large manually collected image dataset.

For most Tutor patterns, use raw market data:

```text
timestamp
open
high
low
close
volume
```

The chart is only a visual rendering of these numbers.

So the technical pipeline should start with OHLCV, not images.

---

### 14. Market-data source

Primary candidate:

```text
Upstox
```

Secondary validation source:

```text
FYERS
```

Additional fallback/cross-check sources:

```text
Angel One SmartAPI
Shoonya
```

Use APIs to obtain historical OHLCV and market-context data.

Preferred process:

```text
API
 ↓
download once
 ↓
validate
 ↓
normalize
 ↓
store locally
 ↓
research / backtesting reads local immutable data
```

Do not repeatedly hit APIs during every model training run.

---

### 15. Market-data storage

Use efficient local storage such as:

```text
Parquet
+
Polars / DuckDB
```

Potential scale:

```text
4,000 stocks
×
2,500 trading days
≈
10 million stock-day observations
```

That is manageable locally with good columnar storage.

---

### 16. Historical-universe problem

Do not backtest only companies that exist today.

That creates **survivorship bias**.

The research system should eventually preserve:

```text
which securities existed
which securities were listed
which securities were delisted
instrument identity changes
symbol changes
ISIN identity
corporate actions
```

for each historical period.

---

### 17. Corporate actions

Raw historical prices can be distorted by:

- stock splits,
- bonuses,
- rights issues,
- dividends,
- mergers,
- demergers,
- symbol changes.

We need explicit price semantics:

```text
raw OHLCV
adjusted OHLCV
corporate-action ledger
```

The technical engine must know which representation each feature uses.

---

### 18. Formalizing Tutor technical concepts

Each Tutor lesson should be converted into a **Pattern Specification**.

For every pattern capture:

```text
1. Tutor terminology
2. exact definition
3. mandatory conditions
4. preferred conditions
5. disqualifiers
6. context
7. trigger
8. invalidation
9. measurable OHLCV features
10. deterministic rules
11. subjective/fuzzy components
12. historical label-generation method
13. positive examples
14. negative examples
15. borderline examples
```

This turns Tutor knowledge into a formal language.

---

### 19. Inside candle example

An inside candle can be detected deterministically.

Simplified definition:

```text
current_high < previous_high
AND
current_low > previous_low
```

Then add contextual features:

```text
mother_candle_range
inside_candle_range
close_location
preceding_trend
volume_change
distance_from_resistance
relative_strength
market_regime
```

The detector should output facts, not BUY/SELL:

```text
InsideCandleResult

detected = true
mother_high = ...
mother_low = ...
range_compression = ...
volume_ratio = ...
close_location = ...
```

---

### 20. VCP example

VCP is more subjective.

Convert it into measurable primitives:

```text
strong prior advance
 ↓
pullback 1
 ↓
pullback 2 smaller
 ↓
pullback 3 smaller again
 ↓
volume contracts
 ↓
price tight near resistance
```

Potential features:

```text
contraction_1_pct
contraction_2_pct
contraction_3_pct
contraction_ratio
volume_contraction
distance_from_high
distance_from_breakout
50DMA_slope
200DMA_slope
relative_strength
base_duration
tightness
```

The model may output:

```text
VCP_score = 0.82
```

rather than only:

```text
VCP = true
```

---

### 21. Other Tutor technical concepts

Planned concepts include:

```text
Inside Candle (IC)
OBS
VCP
Static Contraction
OBS with Anima
Relative Strength
Simple Volume
Up/Down Volume
Relative Volume (RVOL)
RVOL colour
RSI
stage analysis
support/resistance
trendlines
triangle breakout
reversal structures
other Tutor-defined patterns
```

Each should first become a deterministic or semi-deterministic feature module.

---

### 22. Rule detector first, ML later

For every technical concept:

```text
Tutor notes
      ↓
formal definition
      ↓
rule-based candidate detector
      ↓
historical scan
      ↓
candidate examples
      ↓
human/Tutor review
      ↓
positive / negative / borderline labels
      ↓
ML pattern-quality model
```

This is better than manually collecting thousands of chart screenshots.

---

### 23. Active-learning approach

We do not need to manually label every chart.

Use an active-learning loop:

```text
rule detector generates candidates
        ↓
model scores candidates
        ↓
review uncertain / important cases
        ↓
correct labels
        ↓
retrain
        ↓
repeat
```

Human effort is concentrated on ambiguous examples.

---

### 24. Images are optional, not required

If later we want visual ML:

```text
OHLCV window
     ↓
standardized chart renderer
     ↓
candlestick image
     ↓
CNN / vision model
```

We can create millions of chart images automatically from historical data.

But this should come later.

Primary technical research should use raw numbers first.

---

### 25. Three separate technical/ML "brains"

#### Brain 1 — Pattern definition

Answers:

```text
Is this an inside candle?
Is this VCP-like?
Is this Stage 2?
Is this static contraction?
```

#### Brain 2 — Setup quality

Answers:

```text
Given:
- strong result
- Stage 2
- VCP
- strong RS
- good RVOL
- strong sector

How good is this setup?
```

#### Brain 3 — Outcome model

Answers:

```text
What historically happened after setups like this?

5-day return?
20-day return?
MFE?
MAE?
failure probability?
time to breakout?
```

Do not collapse all three into one black box.

---

### 26. Technical feature dataset

A company-day or company-event observation could contain:

```text
company_id
decision_timestamp

FUNDAMENTALS
sales_yoy
sales_qoq
pat_yoy
pat_qoq
opm
opm_change
debt_trend
cash_conversion
...

DOCUMENTS
focus_score
guidance_direction
order_book_growth
capex_direction
credit_rating_direction
management_tone
risk_flags
...

TECHNICAL
stage
RS
RVOL
RVOL_colour
U_D_volume
simple_volume
RSI
inside_candle
OBS
VCP_score
static_contraction_score
triangle_score
breakout_distance
support_distance
...

CONTEXT
Nifty regime
sector regime
India VIX
breadth
...
```

Targets:

```text
1d abnormal return
5d abnormal return
10d abnormal return
20d abnormal return
60d abnormal return
MFE
MAE
breakout?
failed_breakout?
stop_hit?
time_to_breakout?
```

---

### 27. Confluence engine

The Tutor's approach appears to rely on **multiple conditions aligning**.

The system should test combinations such as:

```text
Strong Focus result
+
Stage 2
+
VCP
+
RS > threshold
+
RVOL improvement
+
U/D confirmation
+
sector strength
```

But an important warning:

> Five indicators do not necessarily represent five independent pieces of information.

Many technical indicators are derived from the same OHLCV data.

Therefore measure:

- correlation,
- redundancy,
- incremental information,
- feature importance,
- signal additivity.

Example:

```text
Focus only
      ↓ test

Focus + VCP
      ↓ test

Focus + VCP + RS
      ↓ test

+ RVOL
      ↓ test

+ Inside Candle
      ↓ test

+ market regime
      ↓ test
```

If a signal adds no incremental out-of-sample value, remove it.

---

### 28. Ranking rather than exact price prediction

The preferred ML question is not:

> What will this stock's exact price be in 20 days?

Instead:

> Among today's Focus companies, which setups have the strongest expected quality?

Example:

```text
Company A    0.91
Company B    0.88
Company C    0.84
Company D    0.77
...
Company 40   0.12
```

Then research/trading can concentrate on the top-ranked tail.

This matches the intended funnel:

```text
4,000 companies
      ↓
financial screening
      ↓
Focus universe
      ↓
technically interesting
      ↓
high-confluence candidates
      ↓
trade plan
```

---

### 29. Market regime

Later add Indian market context.

Potential features:

```text
Nifty trend
Nifty relative strength
Nifty distance from moving averages
market breadth
advance/decline
India VIX
sector trend
sector RS
sector breadth
index stage
```

Purpose:

```text
great company
+
great setup
```

may behave differently in:

```text
strong bull market
vs
market correction
vs
sector breakdown
```

---

### 30. Global / US context

Later add global context such as:

```text
S&P 500 previous session
Nasdaq / US tech previous session
Dow
GIFT NIFTY
US futures
USD/INR
oil
rates
global risk-on/off regime
```

The model should be able to distinguish:

```text
company/setup failed
```

from:

```text
whole market got hit by a global shock
```

---

### 31. News / event context

News comes after Focus + technical + regime layers are stable.

Potential structured news features:

```text
company_specific_event
sector_event
macro_event
policy_event
regulatory_event
management_change
fraud/governance flag
order win/loss
rating action
litigation
commodity shock
geopolitical shock
```

Do not dump huge amounts of raw news text into V1.

Extract structured, time-stamped event features.

---

### 32. Incremental / ablation testing

Every new layer must earn its place.

Test:

```text
Fundamentals alone
      ↓
+ documents
      ↓
+ technical structure
      ↓
+ volume / RS
      ↓
+ sector regime
      ↓
+ market regime
      ↓
+ global context
      ↓
+ news
```

Measure whether each addition improves out-of-sample performance.

If not, remove it.

---

### 33. Institutional-style signal philosophy

The strongest public institutional pattern is:

```text
domain hypothesis
      ↓
signal
      ↓
statistical test
      ↓
economic explanation
      ↓
redundancy/additivity test
      ↓
out-of-sample validation
      ↓
portfolio/risk layer
```

This is much more credible than:

```text
one giant AI
      ↓
BUY / SELL
```

Large systematic managers publicly describe combining many signals, testing redundancy, using nonlinear ML, NLP, regime awareness, and ensemble approaches.

Our architecture should follow the same principles.

---

### 34. Compute requirements

Current machine:

```text
Apple MacBook Air M5
24 GB unified memory
large local storage
```

This is enough for the first serious versions of:

```text
financial feature engineering
Focus-model training
Elastic Net
Random Forest
LightGBM
XGBoost
CatBoost
small neural networks
document feature datasets
OHLCV feature generation
technical detectors
historical backtesting
walk-forward testing
```

Even something like:

```text
4,000 companies
× 40 quarters
≈ 160,000 company-quarter observations
```

is small for tabular ML.

Similarly:

```text
4,000 stocks
× 2,500 trading days
≈ 10 million stock-day rows
```

is manageable with Parquet + Polars/DuckDB if processed efficiently.

---

### 35. When cloud GPUs become useful

Cloud GPU becomes useful later for:

```text
large CNN chart-image training
large temporal neural networks
Transformer experiments
large document-model fine-tuning
very large hyperparameter searches
```

Recommended approach:

```text
LOCAL MAC
dataset creation
feature engineering
debugging
small models
backtests
walk-forward
experiments

CLOUD GPU
only when a specific workload truly needs it
```

Do not buy expensive hardware before there is a measured compute bottleneck.

---

### 36. Likely first modelling stack

Suggested progression:

```text
Baseline:
Logistic Regression / Elastic Net

Then:
LightGBM / XGBoost / CatBoost

Then:
ensembles

Then, only when justified:
small neural nets
sequence models
CNN/vision models
specialized text models
```

The priority is **correct data and labels**, not model complexity.

---

### 37. Backtesting rules

A valid backtest must account for:

```text
point-in-time data
survivorship bias
delisted securities
corporate actions
real historical universe
fees
slippage
liquidity
execution timing
publication timestamps
market holidays
signal timing
entry timing
stop logic
future-data leakage
```

Use chronological evaluation, not random train/test splitting.

---

### 38. Walk-forward testing

Preferred structure:

```text
Train:
2018–2021

Validate:
2022

Test:
2023
```

then roll forward:

```text
Train:
2018–2022

Validate:
2023

Test:
2024
```

and continue.

A model is interesting only if the effect survives unseen time periods.

---

### 39. Training on 3–5 quarters

Three to five quarters are appropriate for:

```text
Tutor-rule discovery
Tutor imitation
Focus-engine debugging
feature-definition refinement
```

They are **not** enough to establish profitability.

After Tutor logic stabilizes, expand the historical reconstruction much further.

---

### 40. What "working" means

There are several different success levels.

#### Level 1

System reliably reproduces the Tutor's Focus process.

#### Level 2

System identifies setups the Tutor considers high quality.

#### Level 3

Historical testing shows repeatable out-of-sample edge.

#### Level 4

Walk-forward and paper trading remain positive after realistic costs.

#### Level 5

Small live deployment remains profitable and stable.

Each level must be proven separately.

---

### 41. Most important failure modes

Watch aggressively for:

```text
data leakage
survivorship bias
future revisions
poor labels
missing rejects
overfitting
too many correlated indicators
regime dependence
bad split/bonus adjustments
missing candles
bad timestamps
document publication mismatch
using current knowledge on historical dates
unrealistic transaction costs
illiquid-stock assumptions
```

These can make an ML system look excellent in research while being useless live.

---

### 42. Final target architecture

```text
                        ACQUISITION
                            │
             ┌──────────────┴──────────────┐
             │                             │
             ▼                             ▼

      FINANCIAL HISTORY              DOCUMENT STORE
             │                             │
             ▼                             ▼

      FINANCIAL FEATURES          DOCUMENT INTELLIGENCE
             │                             │
             └──────────────┬──────────────┘
                            ▼

                       FOCUS ENGINE
                 Tutor replication first
                            │
                            ▼

                     FOCUS UNIVERSE
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼

         TECHNICAL       MARKET        GLOBAL/NEWS
         FEATURES        REGIME          CONTEXT

       IC / OBS          Nifty          US markets
       VCP               sector         futures
       S-contra          breadth        macro
       RS                VIX            news
       RVOL
       U/D
       RSI
       stage
              │             │             │
              └─────────────┼─────────────┘
                            ▼

                       SIGNAL LIBRARY
                            │
                            ▼

                        BASE MODELS
              Elastic Net / LightGBM / XGBoost
                            │
                            ▼

                         ENSEMBLE
                            │
                            ▼

                       STOCK RANKING
                            │
                            ▼

                     CONFLUENCE ENGINE
                            │
                            ▼

                      TRADE CANDIDATES
                            │
                            ▼

                 ENTRY / EXIT / INVALIDATION
                            │
                            ▼

                     RISK MANAGEMENT
                            │
                            ▼

                     PAPER / SHADOW MODE
                            │
                            ▼

                 SMALL LIVE DEPLOYMENT
```

---

### 43. Immediate priorities

For the project as it stands after the 7 October production audit, priorities should remain:

```text
1. Turn the audited baseline into a bounded acquisition-readiness execution plan
2. Safely deploy/prove the cleaned C28.1 production release and migrations
3. Restore and prove the new/revised-result discovery → Screener acceptance lane
4. Restore ₹580 crore scope monitoring and prove below-floor → eligible re-entry
5. Make new document → durable extraction/processing automatic and quarter-independent
6. Close current-quarter PPT/transcript discovery and alternate-recovery gaps
7. Correct rating-event semantics, agency identity and normalization
8. Activate transcript structuring and visual slide/page extraction
9. Add long-tail lifecycle + action-oriented black-box operations
10. Run the bounded end-to-end proof campaign and freeze acquisition only on GREEN
11. Build the financial historical feature layer
12. Build Document Intelligence V1 → structured evidence
13. Collect Tutor Focus lists and explicit reasoning
14. Reproduce Tutor Focus decisions
15. Build the deterministic Focus engine
16. Train first tabular ML models
```

Technical ML should be designed now, but implemented only after the fundamental/document acquisition gate is genuinely GREEN.

---

### 44. Guiding rule

The system should not become more complex faster than the evidence improves.

For every feature, model or data source ask:

```text
Does this add measurable,
out-of-sample,
non-redundant information?
```

If yes, keep it.

If not, remove it.

The objective is not to build the most complicated trading AI.

The objective is to build the **simplest system that reliably captures the Tutor's edge, validates it historically, and survives forward testing**.

<a id="document-lifecycle"></a>

## Part 2: Post-Cleanup Document Lifecycle Plan


**Status after the 7 October production audit:** Structural cleanup is reported complete at C28.1, but the cleaned release is not yet the production runtime. The document lifecycle has substantial historical data and working components, while live discovery, processing handoff and several recovery/normalization paths are incomplete or stopped.  
**Purpose:** Complete and prove the supporting-document lifecycle on the cleaned architecture, without reopening project-wide structural cleanup or confusing historical backlog with architectural failure.

---

#### 1. Why this work is deferred

The C28.1 canonical integration is reported complete, and the production black-box audit is now complete. Preserve the separation between structural cleanup and behavior changes: do not repeat the project-wide cleanup. The next work is to deploy/prove the cleaned release safely, close only the audited behavioral gaps, and then run the bounded proof campaign. Historical backlog should be processed through campaigns after the new-result lane is protected.

---

#### 2. Current document lifecycle

```text
NEW RESULT
    ↓
SCREENER FINANCIAL SCRAPE
    ↓
FINANCIALS ACCEPTED
    ↓
CREATE DOCUMENT EXPECTATIONS
    ├── PPT
    ├── TRANSCRIPT
    └── RECORDING
    ↓
DOCUMENT AVAILABLE NOW?
    │
    ├── YES
    │     ↓
    │   PROMOTE LINK
    │     ↓
    │   DOWNLOAD QUEUE
    │     ↓
    │   DOWNLOAD
    │
    └── NO
          ↓
      DELAYED RECONCILER
          ↓
      CHECK ONLY DUE ITEMS
          ↓
      SCREENER DOCUMENT-ONLY REFRESH
          ↓
      LINK APPEARED?
          ├── YES → PROMOTE → DOWNLOAD
          └── NO  → SCHEDULE NEXT CHECK
```

This basic design is good and should be preserved. The reconciler already avoids repeatedly checking every company; it works only on document expectations that are due.

---

#### 3. Current weaknesses to fix after cleanup

##### 3.1 Six-week follow-up cutoff

The current delayed-document retry schedule eventually stops after roughly six weeks.

```text
RESULT RELEASED
    ↓
PPT / TRANSCRIPT NOT AVAILABLE
    ↓
EARLY RETRIES
    ↓
6-WEEK LIMIT
    ↓
NO GUARANTEED FUTURE CHECK
```

##### Improvement

Do not simply retry forever. Introduce a lifecycle:

```text
EARLY_RETRY
    ↓
LONG_TAIL_PENDING
    ↓
NEXT_QUARTER_DETECTED
    ↓
FINAL CHECK FOR PREVIOUS QUARTER
    ↓
FOUND?
    ├── YES → DOWNLOAD
    └── NO  → HONEST FINAL / UNKNOWN STATE
```

The system should distinguish active retrying, long-tail waiting, final quarter-close check performed, and still unknown/not found.

---

#### 4. Make processing/extraction continuous and quarter-independent

The production audit proves this is a live gap, not merely a design concern:

- production schema stops at migration 068 and does not contain `document_processing_inbox` or `document_processing_work`;
- 44,434 supported contents are stored with extraction `not_started`;
- there is no production document-processing container;
- current cleaned source migration 072 adds an inbox trigger, but work materialization still requires explicitly selected content IDs and parser/OCR/options versions;
- the configured quarter-processing role remains period/limit scoped around `2026-06-30`.

Therefore the target is stronger than merely removing a hard-coded period:

```text
ELIGIBLE DOCUMENT CONTENT SAVED
    ↓
DURABLE PROCESSING INBOX
    ↓
AUTOMATIC / IDEMPOTENT WORK MATERIALIZATION
    ↓
GENERIC PROCESSING WORKER
    ↓
PDF / PPT / HTML / OCR / TRANSCRIPT PROCESSING
    ↓
NORMALIZATION / SEGMENTATION WHERE APPLICABLE
    ↓
READY STATE
```

The normal live path must not require a human to select content IDs or provide a quarter. Quarter/content selection remains useful only for finite backfill and repair campaigns. New-result work must have priority over historical backlog.

---

#### 5. Use the audited document backlog, not the older 357-check snapshot

The earlier 357-check follow-up was useful at the time, but the 7 October black-box audit is now the governing baseline. Do not use the older 357 number as the execution target.

For the 2026-06 cohort the audit found:

```text
PRESENTATIONS
- 2,033 expectations
- 194 candidate artifacts / 304 source links
- 188 valid official original contents
- 191 extraction OK / 3 partial
- 62 pending period download jobs
- 273 discovery_failed
- 300 source_not_covered
- 1,279 expected_not_yet_published

OFFICIAL TRANSCRIPTS
- 2,033 expectations
- 160 candidate artifacts / 223 source links
- 147 valid official original contents
- 160 extraction OK
- 11 pending period download jobs
- 276 discovery_failed
- 300 source_not_covered
- 1,457 expected_not_yet_published
```

Across all history:

```text
PRESENTATIONS
- 34,668 valid official original contents
- 25,427 supported contents not started extraction
- 14,513 pending download jobs
- 234 retryable
- 1,618 terminal

TRANSCRIPTS
- 18,525 valid official original contents
- 15,987 supported contents not started extraction
- 9,289 pending download jobs
- 1,694 retryable
- 5,353 terminal
```

These are not one homogeneous failure class.

Execution should split them into:

1. **Current-quarter discovery failures** — investigate first because they can cause real coverage loss.
2. **Available/discovered but no valid content** — candidate promotion/download/matching defects.
3. **Source not covered** — determine whether BSE/NSE/issuer IR/event recovery applies; do not call this “not issued.”
4. **Expected not yet published** — keep honest and move through the late-publication lifecycle; do not convert to failure without evidence.
5. **Pending/retryable downloads** — drain only after the current-quarter lane is running and protected.
6. **Terminal historical work** — classify by root cause before any retry campaign.
7. **Downloaded but not processed** — route through the new continuous processing path rather than creating another acquisition campaign.

The transcript cohort also has a stored-state inconsistency: discovered transcript artifacts exist while the expectation table reports zero `available` expectations. The execution plan must include a bounded reconciliation proof for that inconsistency.

---

#### 6. Improve the failed-link recovery funnel

Historic gaps are driven heavily by stale or inaccessible URLs and source changes.

Target recovery order:

```text
KNOWN DOCUMENT URL FAILS
        ↓
REFRESH LATEST LINK
        ↓
WORKING LINK?
    ┌───┴───┐
   YES      NO
    │        │
    ▼        ▼
DOWNLOAD   ALTERNATE OFFICIAL SOURCE
            │
            ├── BSE
            ├── NSE
            ├── ISSUER IR
            └── APPROVED SOURCE
                 │
                 ▼
            FOUND?
            ├── YES → EXISTING DOWNLOADER
            └── NO
                  ↓
            SPECIAL / OPERATOR RECOVERY
                  ↓
            SAME LOGICAL ARTIFACT
```

Recovered content representing the same document must remain attached to the **same logical artifact**.

---

#### 7. Treat document classes differently

##### PPT presentations

Quarter/result-related:

```text
quarter expectation
→ delayed publication checks
→ recovery if necessary
```

##### Official transcripts

Quarter/result-related:

```text
quarter expectation
→ delayed publication checks
→ recovery if necessary
```

Official transcript remains preferred over machine transcription.

##### Concall recordings

Recordings are not equivalent to PPT/transcript documents. The current metadata/reference-only behavior should remain intentional unless a separate policy is introduced.

Recommended future policy:

```text
OFFICIAL TRANSCRIPT EXISTS?
    ├── YES → USE TRANSCRIPT
    └── NO
         ↓
RECORDING AVAILABLE?
    ├── NO → unresolved/unknown
    └── YES
         ↓
IS COMPANY/QUARTER RESEARCH-IMPORTANT?
    ├── NO → keep metadata only
    └── YES
         ↓
DOWNLOAD RECORDING
         ↓
MACHINE TRANSCRIPTION
```

Do not bulk-download every historical recording simply because a URL exists.

##### Credit-rating reports

Ratings are event-driven rather than naturally “one report per company per quarter.” A company may have multiple rating actions in one quarter or none at all. A result event must not create a mandatory quarterly rating-document expectation.

For a decision cutoff, retrieve the latest valid rating report/action available by that cutoff and the prior valid report/action for comparison. If no new rating action occurred, represent `NO_NEW_RATING_ACTION`; do not report a missing quarterly rating. Preserve rating events and documents with event identity first, using quarter only as research context.

---

#### 8. Improve reporting terminology

A single metric such as `Not successfully downloaded` mixes very different situations.

Operational reporting should distinguish, reusing existing states where possible:

```text
FOUND_AND_DOWNLOADED
DOWNLOAD_PENDING
DOWNLOAD_FAILED
ACCESS_BLOCKED
INACCESSIBLE
LATE_PUBLICATION_PENDING
LONG_TAIL_PENDING
ALTERNATE_SOURCE_NEEDED
POLICY_EXCLUDED
REFERENCE_ONLY
DOWNLOADED_NEEDS_EXTRACTION
EXTRACTED
UNKNOWN
```

The goal is simple:

> **Every number should tell us what action, if any, is required.**

---

#### 9. Coordinator priorities

Recommended priority order:

```text
1. NEW / REVISED FINANCIAL RESULTS
2. CURRENT-QUARTER PPT / TRANSCRIPT DISCOVERY
3. CURRENT-QUARTER DOCUMENT DOWNLOADS
4. RECENT FAILED-LINK RECOVERY
5. EXTRACTION REQUIRED FOR RECENT DOCUMENTS
6. LONG-TAIL PREVIOUS-QUARTER RECHECKS
7. HISTORICAL DOCUMENT RECOVERY
8. BACKGROUND / LOW-PRIORITY EXTRACTION
```

Historical recovery must not delay a new quarterly result.

---

#### 10. Target lifecycle after improvement

```text
                    RESULT ACCEPTED
                          │
                          ▼
                DOCUMENT EXPECTATION
                          │
          ┌───────────────┴────────────────┐
          │                                │
     LINK AVAILABLE                   LINK MISSING
          │                                │
          ▼                                ▼
       PROMOTE                       EARLY RETRIES
          │                                │
          ▼                                ▼
      DOWNLOAD                       FOUND LATER?
          │                           ├── YES → PROMOTE
          ▼                           └── NO
       EXTRACT                              │
          │                                 ▼
          ▼                         LONG-TAIL PENDING
      COMPLETE                              │
                                            ▼
                                  NEXT-QUARTER FINAL CHECK
                                            │
                                     ┌──────┴──────┐
                                    FOUND         NOT FOUND
                                     │              │
                                     ▼              ▼
                                  DOWNLOAD     HONEST FINAL
                                                  STATE
```

Broken-link path:

```text
DOWNLOAD FAILED
      ↓
REFRESH CURRENT LINK
      ↓
ALTERNATE OFFICIAL SOURCE
      ↓
SPECIAL RECOVERY IF REQUIRED
      ↓
SAME LOGICAL ARTIFACT
      ↓
CANONICAL DOWNLOAD / STORAGE
      ↓
EXTRACTION
```

---

#### 11. Recommended implementation timing

The ownership boundaries from C14–C22 and canonical integration at C28.1 are reported complete. This is the next phase: implement the targeted behavior changes on that cleaned architecture, capture production evidence, and run the bounded proof campaign. Reopen a cleanup area only if the proof identifies a concrete structural defect.

---

#### 12. Post-cleanup implementation order

```text
AR-01  Prepare and safely activate the immutable C28.1 production release.
       Reconcile migrations 069–075 on an audited clone, prove rollback/compatibility,
       and use a bounded canary. Do not restart C05.3 as the target release.

AR-02  READ-ONLY PRODUCTION BASELINE — COMPLETE (7 Oct 2026).
       Preserve the black-box audit and its five supporting CSVs as the execution baseline.

AR-03  Restore the P0 new-result lane.
       Resolve the NSE snapshot-guard failure, determine the supported BSE 403/access path,
       restore the exchange scheduler and prove new/revised result → Screener queue → acceptance.

AR-04  Restore and prove collection scope.
       Resume bounded market-cap observation, process overdue checks safely, confirm `>= 580`
       eligibility and prove a below-floor → eligible transition re-enables intended work.

AR-05  Make document processing continuous.
       Ensure new eligible artifact content automatically reaches durable processing work,
       generic extraction and downstream readiness without a hard-coded quarter or manual ID selection.

AR-06  Close current-quarter document discovery/recovery gaps.
       Reconcile stored presentation/transcript expectation inconsistencies; refresh stale links first,
       then BSE/NSE/issuer IR/event recovery, then special/operator recovery where permitted.

AR-07  Correct rating-event, agency-identity and normalization semantics.
       Preserve event-driven ratings, latest-valid-before-cutoff + prior event, explicit
       `NO_NEW_RATING_ACTION`, and resolve Fitch/India Ratings + SMERA/Acuité identity ambiguity.

AR-08  Complete document-understanding prerequisites.
       Add chart/image-aware slide/page extraction with evidence; activate official-transcript
       segmentation/validation; keep recording download/transcription explicitly policy-selected.

AR-09  Add long-tail lifecycle and unified operational black box.
       Replace the six-week dead end, classify the 1970 sentinel safely, maintain action-oriented
       failure/backlog states, and protect current-quarter work from historical backlog.

AR-10  Run the bounded end-to-end proof campaign and freeze only on GREEN.
       Include a normal company-quarter, a late-publication document, an alternate-recovery case,
       a live/controlled ₹580 re-entry proof, automatic processing through readiness, and rating context.

After the acquisition gate is green:

DLI-01  Run bounded historical document/recovery campaigns using the proven lifecycle.
DLI-02  Reduce extraction backlog in priority order without starving new-quarter work.
DLI-03  Resolve remaining manual-review/terminal rating normalization cases.
DLI-04  Expand recording transcription only where official transcript is absent and research policy selects it.
DLI-05  Retire or archive legacy operational paths only after live replacement evidence exists.
```

---

#### 13. Success criteria

The acquisition-readiness portion is complete only when the conditions in [Current phase: Acquisition readiness gate](#acquisition-readiness) are met. The broader lifecycle improvements are complete when:

- newly accepted results automatically create correct supporting-document expectations;
- late PPTs/transcripts remain discoverable beyond the current six-week window without aggressive polling;
- previous-quarter unresolved documents receive a defined long-tail/final check;
- every eligible downloaded document can enter extraction without quarter-specific Docker configuration;
- broken URLs first receive link refresh, then alternate-source recovery, then special recovery where appropriate;
- recovered/replacement files retain the same logical artifact identity;
- recordings are not falsely reported as download failures when policy intentionally keeps them reference-only;
- ratings use event-aware semantics instead of pretending every company produces one quarterly rating file;
- dashboards distinguish actionable failures from honest absence, unknown, and policy states;
- new-quarter acquisition outranks archival recovery;
- no accepted financial history or immutable document content is rewritten;
- the production release no longer depends on the stopped C05.3 runtime as the intended target;
- migrations 069–075 are reconciled and only applied through an explicitly authorized release path;
- the new-result lane is demonstrably fresher than the audit baseline and no scheduler date silently disappears;
- new eligible content reaches processing automatically;
- transcript structuring tables receive validated records for eligible official transcripts;
- agency aliases used in rating research are explicit rather than inferred;
- the legacy `1970-01-01` sentinel is prevented from entering research period logic.

---

#### Final principle

The existing document system does **not** need to be replaced.

It already has the important pieces:

```text
expectations
reconciliation
promotion
download queues
immutable storage
extraction
failed-link recovery
alternate-source recovery
browser recovery
```

The next step is to make those pieces behave as **one complete document lifecycle** on the cleaned architecture and prove it through the acquisition-readiness gate.

<a id="v1-schema"></a>

## Part 3: Document Intelligence V1 Schema


#### Purpose

This document defines the first structured document-intelligence layer for the Trading AI project.

The objective is to convert the documents already being acquired by the scraper into **typed, evidence-backed facts that the future Focus ML system can consume**.

The current raw-document families are:

- Investor presentations / PPTs
- Concall transcripts
- Concall recordings when transcripts are unavailable
- Credit-rating rationales / reports

The financial scraper already provides the numerical foundation such as quarterly financials, cash flow, working-capital metrics, debt, margins, shareholding and related financial observations.

The document-intelligence layer should therefore focus primarily on information that is **not fully available from financial statements alone**.

---

### 1. Current priority order

The project should proceed in this order:

```text
1. Identify and prepare the controlled cleaned release
      ↓
2. Capture the read-only production coverage baseline
      ↓
3. Close official recovery, automatic quarter processing,
   rating-event and visual-slide extraction gaps
      ↓
4. Confirm the Focus universe and standalone-only contract
      ↓
5. Complete the bounded end-to-end proof campaign
      ↓
6. Require the acquisition gate to be GREEN; then freeze interfaces
      ↓
7. Build document extraction into structured facts
      ↓
8. Build longitudinal document histories
      ↓
9. Feed verified document facts into Focus ML
```

The immediate goal is not to train ML on raw documents.

The immediate goal is:

> Convert documents into reliable structured information with source evidence.

---

### 2. Is the existing data foundation sufficient?

For Focus V1, the source families are broadly sufficient:

```text
Screener financial history
+
shareholding / institutional data
+
Investor presentation
+
Concall transcript / recording
+
Credit-rating rationale
```

The major unfinished layer is:

```text
RAW DOCUMENT
      ↓
STRUCTURED RESEARCH FACTS
      ↓
ML FEATURES
```

We should not immediately expand scope into:

- annual reports,
- thousands of miscellaneous exchange announcements,
- news,
- macro intelligence,
- global events,

until the core fundamental/document Focus system is working.

---

### 3. General extraction rule

Every document extractor should return **source facts**, not arbitrary summaries.

For example:

```text
AI extracts:
Order book = ₹4,200 crore
Revenue guidance = 15–20%
Installed capacity = 120,000 tonnes
Capacity utilisation = 78%
```

Then Python calculates:

```text
order_book_to_ttm_sales
order_book_growth
guidance_midpoint
guidance_delta
capacity_growth_pct
utilisation_change
```

The division of responsibility should be:

```text
AI       = understand language and extract facts
Python   = arithmetic and deterministic calculations
ML       = learn relationships between features and outcomes
```

---

### 4. Credit-Rating Report Extraction

Credit-rating reports should not be reduced to only:

```text
rating upgrade / downgrade
liquidity
```

They can provide much richer business and financial-risk information.

#### 4.1 Core rating fields

Extract:

- Rating agency
- Instrument / facility
- Facility amount
- Current rating
- Previous rating where available
- Rating action
- Outlook
- Action date
- Watch / review status where applicable

##### Rating action values

Possible structured values:

```text
UPGRADED
DOWNGRADED
REAFFIRMED
ASSIGNED
WITHDRAWN
WATCH_POSITIVE
WATCH_NEGATIVE
OTHER
```

---

#### 4.2 Liquidity

Extract:

- Liquidity assessment
- Available cash / liquid investments where disclosed
- Expected cash accruals
- Upcoming debt repayments
- Undrawn bank lines
- Working-capital availability
- Liquidity concerns
- Agency reasoning supporting the liquidity view

Possible normalized state:

```text
STRONG
ADEQUATE
STRETCHED
WEAK
UNKNOWN
```

The evidence behind the label must also be stored.

---

#### 4.3 Debt and protection metrics

Extract where available:

- Total debt / borrowings
- Debt trend
- Interest coverage
- DSCR
- Gearing
- Debt / equity
- TOL / TNW
- Debt / EBITDA
- Cash accruals
- Debt repayment obligations
- Covenant concerns

Do not force all agencies into the same metric if they report different measures.

Store the original metric name and normalized equivalent where possible.

---

#### 4.4 Working-capital intelligence

Extract:

- Working-capital intensity
- Bank-limit utilisation
- Debtor / receivable cycle
- Inventory holding
- Payable cycle
- Cash conversion concerns
- Receivable concentration
- Delayed collections
- Dependence on working-capital borrowing

Possible features later:

```text
working_capital_pressure
bank_limit_utilisation
receivable_risk
inventory_risk
```

---

#### 4.5 Operating performance

Extract the rating agency's observations on:

- Revenue growth
- Profitability
- Margin trend
- Operating performance
- Business stability
- Scale of operations
- Cash-generation ability
- Earnings volatility

This should remain separate from the audited/accepted financial statements.

It is contextual evidence, not replacement financial data.

---

#### 4.6 Order book and execution

If the rating report discusses it, extract:

- Current order book
- Order-book growth
- Order-book visibility
- Execution horizon
- Project concentration
- Execution risk
- Delays
- Cost overruns
- Dependence on large projects

---

#### 4.7 Capex risk

Extract:

- Capex amount
- Capex purpose
- Funding source
- Debt-funded portion
- Completion timeline
- Project implementation risk
- Cost-overrun risk
- Expected impact on leverage
- Expected impact on capacity / revenue

---

#### 4.8 Concentration risks

Extract:

- Top-customer concentration
- Top-5 / top-10 customer concentration
- Supplier concentration
- Geography concentration
- Segment concentration
- Project concentration

---

#### 4.9 Raw-material and forex exposure

Extract:

- Key raw materials
- Commodity-price exposure
- Imported input dependency
- Forex exposure
- Hedging policy
- Unhedged exposure
- Cost volatility
- Availability risk

---

#### 4.10 Pricing / cost pass-through

Extract:

- Ability to pass raw-material costs to customers
- Pass-through lag
- Contractual escalation clauses
- Fixed-price contract exposure
- Pricing power evidence

Do not infer pricing power merely because margins improved.

---

#### 4.11 Strengths and weaknesses

Store separately:

```text
rating_strengths[]
rating_weaknesses[]
```

Examples:

- strong market position,
- experienced promoters,
- diversified customer base,
- high working-capital intensity,
- customer concentration,
- project risk,
- weak debt protection.

---

#### 4.12 Upgrade / downgrade triggers

Extract exact rating sensitivities where available.

Examples:

```text
UPGRADE_TRIGGER:
sustained improvement in revenue and operating margins
with better debt protection metrics

DOWNGRADE_TRIGGER:
stretch in working capital
or deterioration in liquidity
```

These can later become structured risk indicators.

---

#### 4.13 Default / payment-risk indicators

Extract:

- Payment delays
- Debt-servicing delays
- Covenant breaches
- Default concerns
- Restructuring
- SMA / stress signals where disclosed

---

#### 4.14 Group / promoter support

Extract:

- Corporate guarantee
- Promoter support
- Parent support
- Group financial support
- Inter-company dependence

---

### 5. Important Credit-Rating Rule

A new credit-rating report should **not** be required every quarter.

For a quarter decision date, the system should use:

```text
latest valid credit-rating report
available before the decision date
+
previous rating report
```

Then derive changes such as:

```text
rating_change
liquidity_change
debt_risk_change
working_capital_change
rating_sensitivity_change
```

If there is no new rating report during the quarter:

```text
NO_NEW_RATING_ACTION
```

This must **not** mean:

```text
MISSING_DATA
BAD_COMPANY
NEGATIVE_SIGNAL
```

---

### 6. Investor Presentation / PPT Extraction

Investor presentations can provide information that financial statements do not capture clearly.

#### 6.1 Order book

Extract:

- Current order-book value
- Previous order-book value where disclosed
- Order-book growth
- Order inflow
- L1 / preferred bidder amount if disclosed
- Order pipeline
- Execution horizon
- Order-book coverage
- Order-book split

Possible split dimensions:

- business segment,
- geography,
- product,
- customer type,
- government / private,
- domestic / export.

---

#### 6.2 Order-book derived features

Python can later calculate:

```text
order_book_to_ttm_sales
order_book_growth_qoq
order_book_growth_yoy
order_inflow_growth
execution_visibility_months
```

---

### 7. Capacity

Extract:

- Installed capacity
- Current capacity
- Plant-wise capacity
- Product-wise capacity
- Capacity utilisation
- Planned capacity additions
- Brownfield expansion
- Greenfield expansion
- Ramp-up progress

---

### 8. Capacity expansion

Extract:

- Planned capacity increase
- Expansion location
- Product / business line
- Expected commissioning date
- Current commissioning status
- Expected revenue contribution
- Expected utilisation ramp

---

### 9. Capex

Extract:

- Capex amount
- Capex purpose
- Maintenance capex
- Growth capex
- Funding source
- Debt funding
- Internal accrual funding
- Project timeline
- Expected completion date

---

### 10. Commissioning and execution

Instead of a vague `execution_quality` score, extract specific commitments.

Example:

```text
Q1:
Plant commissioning = March 2026

Q2:
Plant commissioning = March 2026

Actual:
Plant commissioned = February 2026
```

Later derive:

```text
commissioning_on_time = TRUE
```

This creates evidence-backed execution metrics.

---

### 11. Segment and product mix

Extract:

- Segment revenue mix
- Product revenue mix
- Business-unit mix
- High-margin / low-margin segments where disclosed
- New-product contribution
- Product launches
- Product concentration

---

### 12. Geography and exports

Extract:

- Domestic share
- Export share
- Export revenue
- Export countries
- Regional exposure
- Country concentration
- New export markets
- Export growth

---

### 13. Customer and end-market exposure

Extract:

- Top customers
- Customer concentration
- Top-5 / top-10 share
- New customers
- Customer wins
- Customer losses where disclosed
- End markets

Examples:

```text
Auto
Defence
Railways
Construction
Electronics
Pharma
Oil & Gas
Power
Data centres
Exports
```

---

### 14. Raw-material exposure

Extract:

- Important raw materials
- Imported raw materials
- Commodity dependencies
- Supplier concentration
- Availability risks
- Cost pressures

---

### 15. Market position / moat evidence

Extract only when explicitly supported.

Possible facts:

- Market share
- Ranking in industry
- Unique manufacturing capability
- Regulatory approvals
- Certifications
- Distribution reach
- Patents / technology
- Entry barriers
- Customer switching difficulty
- Long-standing customer relationships

Do not let the LLM invent:

```text
moat_score = 9/10
```

Store the actual disclosed evidence.

---

### 16. Growth drivers

Extract explicit future drivers such as:

- New capacity
- New product
- Export expansion
- New geography
- New customer
- Industry demand growth
- Regulation / policy support
- Backward integration
- Operating leverage
- Product mix shift

---

### 17. Concall Extraction

The concall is likely to be the richest source for management expectations.

#### 17.1 Revenue guidance

Extract:

- Guided revenue
- Guided revenue-growth rate
- Guided range
- Guidance period
- Segment-specific guidance
- Confidence / conditionality wording

---

### 18. Margin guidance

Extract:

- EBITDA margin guidance
- OPM guidance
- Gross-margin guidance
- Margin expansion / contraction expectation
- Guidance range
- Guidance period

---

### 19. Volume guidance

Extract:

- Volume growth guidance
- Production guidance
- Shipment guidance
- Capacity ramp guidance

---

### 20. Order-inflow guidance

Extract:

- Expected order inflow
- Expected tender wins
- Order pipeline
- Bid pipeline
- L1 position where discussed
- Conversion expectations

---

### 21. Capex guidance

Extract:

- Planned capex
- Timing
- Funding
- Purpose
- Expected commissioning

---

### 22. Capacity guidance

Extract:

- Capacity expansion
- Utilisation target
- Ramp-up expectations
- Production start date
- Expected contribution

---

### 23. Export guidance

Extract:

- Expected export growth
- Export share targets
- New regions
- New countries
- Customer expansion
- Currency sensitivity

---

### 24. Guidance action

For every comparable guidance item classify:

```text
RAISED
MAINTAINED
CUT
WITHDRAWN
NEW_GUIDANCE
NOT_COMPARABLE
UNKNOWN
```

This should be based on current versus previous guidance.

---

### 25. Demand outlook

Extract:

- Strong / stable / weak demand
- Segment-level demand
- Geography-level demand
- Customer-level commentary
- Seasonality
- Demand recovery / slowdown

Keep supporting text evidence.

---

### 26. Pricing commentary

Extract:

- Price increases
- Price reductions
- Realisation changes
- Discounting
- Competitive pricing
- Customer negotiations
- Pricing pressure

---

### 27. Raw-material commentary

Extract:

- Cost inflation
- Cost deflation
- Commodity trends
- Input availability
- Supply-chain problems
- Imported-input concerns

---

### 28. Pass-through ability

Extract:

- Whether cost increases can be passed through
- Expected lag
- Contractual mechanism
- Customer resistance
- Margin impact before pass-through

---

### 29. Order pipeline and execution timing

Extract:

- Pipeline size
- Expected conversion
- Revenue recognition timing
- Order execution duration
- Project delays
- Customer approvals
- Bottlenecks

---

### 30. Capacity ramp

Extract:

- Plant ramp-up
- Current utilisation
- Target utilisation
- Ramp timeline
- Initial inefficiencies
- Expected margin impact

---

### 31. Customer additions / losses

Extract:

- New customers
- Strategic customers
- Customer wins
- Customer losses
- Qualification status
- Expected contribution

---

### 32. New products

Extract:

- Product name / category
- Launch status
- Customer approval
- Commercial production
- Expected revenue contribution
- Margin profile when disclosed

---

### 33. Working-capital commentary

Extract:

- Receivable issues
- Inventory build-up
- Payable changes
- Working-capital guidance
- Expected normalization
- Customer payment delays
- Project billing cycles

---

### 34. Debt commentary

Extract:

- Debt-reduction plan
- New borrowing
- Refinancing
- Interest-cost outlook
- Debt-funded capex
- Target leverage

---

### 35. Quarter anomalies

Extract reasons for unusual results such as:

- One-off revenue
- One-off expense
- Forex gain/loss
- Inventory gain/loss
- Temporary shutdown
- Commissioning cost
- Exceptional income
- Government subsidy
- Litigation provision
- Raw-material spike

This helps the ML distinguish a repeatable trend from a one-quarter distortion.

---

### 36. Risks and bottlenecks

Extract:

- Demand risk
- Capacity constraints
- Supply-chain issues
- Customer delays
- Project delays
- Regulatory risk
- Commodity risk
- Forex risk
- Financing risk
- Working-capital risk
- Labour / operational risk

---

### 37. Q&A evidence

Prepared remarks and Q&A should be stored separately.

For Q&A capture:

```text
analyst_question
management_answer
speaker
topic
evidence_span
```

Analyst questions often expose risks not emphasized in prepared remarks.

---

### 38. Management commitments

Capture specific forward-looking commitments:

```text
Revenue target
Margin target
Capacity commissioning date
Debt reduction target
Working-capital target
Order-book execution target
Export target
Capex completion date
```

Every commitment should have:

```text
commitment_type
target_value
unit
target_period
source_date
source_document
evidence
```

---

### 39. Longitudinal Management Execution

One of the most important future feature groups should be:

> Did management do what it previously said it would do?

Do not create subjective LLM scores such as:

```text
management_quality = 9/10
management_confidence = 8.5/10
```

Instead derive measurable outcomes.

---

### 40. Guidance hit rate

Example:

```text
Q1 guidance:
Revenue growth = 20%
Margin = 16%

Actual:
Revenue growth = 22%
Margin = 16.4%
```

Derived:

```text
revenue_guidance_hit = TRUE
margin_guidance_hit = TRUE
```

Over time calculate:

```text
guidance_hit_rate
revenue_guidance_error
margin_guidance_error
guidance_raise_frequency
guidance_cut_frequency
```

---

### 41. Commissioning execution

Track:

```text
promised_commissioning_date
actual_commissioning_date
delay_days
```

Derive:

```text
commissioning_timeliness
```

---

### 42. Debt-reduction promises

Track:

```text
promised_debt_reduction
actual_debt_change
```

Derive:

```text
debt_reduction_promise_hit_rate
```

---

### 43. Working-capital promises

Track:

```text
promised_working_capital_improvement
actual_debtor_change
actual_inventory_change
actual_ccc_change
```

Derive:

```text
working_capital_promise_hit_rate
```

---

### 44. Order-book execution

Track:

```text
historical_order_book
expected_execution_horizon
actual_revenue_conversion
```

Derive:

```text
order_book_execution_consistency
```

---

### 45. Evidence Envelope — Mandatory

Do not store only:

```json
{
  "order_book": 4200
}
```

Every extracted research fact needs provenance.

Conceptual schema:

```json
{
  "fact_code": "order_book",
  "value": 4200,
  "unit": "INR_CRORE",

  "period": "2026-Q1",
  "as_of_date": "2026-06-30",

  "source_type": "presentation",
  "document_id": 8342,
  "published_at": "2026-08-07T12:14:00+05:30",

  "page_or_segment": "slide_18",
  "evidence": "source evidence span",

  "extraction_method": "document_intelligence_v1",
  "model_version": "v1",
  "confidence": 0.98,
  "validation_status": "accepted"
}
```

---

### 46. Concall-specific provenance

For concalls preserve:

```text
page / transcript segment
speaker
speaker role
prepared remarks vs Q&A
timestamp if audio transcription
```

---

### 47. PPT-specific provenance

For presentations preserve:

```text
slide number
table / chart / text block
cell or region if applicable
page coordinates if useful
```

---

### 48. Credit-rating provenance

Preserve:

```text
agency
report date
rating table location
rationale section
liquidity section
rating sensitivity section
page
evidence span
```

---

### 49. Validation rule

No extracted fact should become a production ML feature without supporting evidence.

Validation should separately measure:

- Retrieval correctness
- Extraction precision
- Numeric accuracy
- Unit accuracy
- Evidence citation accuracy
- Hallucination rate
- Missing / unknown correctness

---

### 50. Missing document handling

The system must distinguish:

```text
AVAILABLE
NOT_PUBLISHED_YET
NOT_AVAILABLE
NOT_APPLICABLE
DOWNLOAD_FAILED
EXTRACTION_FAILED
```

Missing document must not become a negative business signal.

Example:

```json
{
  "concall_status": "NOT_PUBLISHED_YET",
  "revenue_guidance": null,
  "guidance_extraction_status": "INSUFFICIENT_SOURCE"
}
```

---

### 51. Cross-source contradiction handling

The same fact may appear in multiple sources.

Example:

```text
PPT:
Order book = ₹4,200 crore

Concall:
Order book = approximately ₹4,250 crore

Rating report:
Order book = ₹4,180 crore
```

Do not silently overwrite values.

Store:

```text
multiple claims
source
date
period
confidence
```

Then reconcile with explicit rules.

---

### 52. Historical comparison

The most important future document features are often changes, not absolute values.

Examples:

```text
guidance_change
order_book_growth
capacity_growth
liquidity_change
rating_change
working_capital_risk_change
management_execution_change
```

For every company build longitudinal histories:

```text
guidance_history
order_book_history
capacity_history
capex_history
debt_commitment_history
working_capital_commitment_history
rating_history
liquidity_history
```

---

### 53. ML-ready feature examples

After extraction and Python calculations, Focus ML may eventually receive:

```text
rating_action
rating_outlook
liquidity_state
liquidity_change
working_capital_risk
bank_utilisation
debt_protection_trend

order_book_value
order_book_growth
order_book_to_ttm_sales
execution_horizon
capacity_growth
capacity_utilisation
capex_intensity
export_share

revenue_guidance_midpoint
revenue_guidance_change
margin_guidance_midpoint
guidance_action
demand_outlook
pricing_pressure
raw_material_pressure

management_guidance_hit_rate
commissioning_timeliness
debt_promise_hit_rate
working_capital_promise_hit_rate
order_book_execution_consistency
```

---

### 54. What should NOT be an ML feature directly

Avoid direct features such as:

```text
LLM says management is good
LLM says company has a moat
LLM confidence that stock is attractive
LLM BUY / SELL opinion
generic document sentiment
```

Instead store evidence-backed components.

---

### 55. Immediate scraper audit checklist

Before building document intelligence, first satisfy the [acquisition-readiness gate](#acquisition-readiness), then verify the document-specific evidence below:

#### Investor presentations

- Correct presentation discovered
- Correct company mapping
- Correct reporting period
- Publication timestamp preserved
- Source URL preserved
- File bytes preserved
- File hash preserved
- Tables extractable
- Slides/pages extractable
- Chart/image-heavy slides can be rendered and processed visually even when native text is also present; extracted values retain slide-level evidence
- Missing presentation correctly classified
- Late presentations rechecked

#### Concall transcripts

- Official transcript preferred
- Correct company and quarter
- Publication timestamp
- File preserved
- Text extraction quality
- Speaker structure
- Q&A separation
- Missing transcript correctly classified

#### Concall recordings

- Used primarily when transcript unavailable
- Correct media identity
- Correct company/quarter
- File preserved
- Audio extractable
- Transcription possible
- Transcript generated with provenance

#### Credit ratings

- Relevant rating agencies discovered
- Correct company identity
- Rating history preserved
- Report date preserved
- Rating rationale file downloaded where available
- No false requirement for one report every quarter
- Latest valid report retrievable by decision date
- Previous report retrievable for comparison

---

### 56. V1 Document Schema Summary

#### Credit Rating V1

```text
rating action
outlook
current rating
previous rating
liquidity
debt / coverage
working capital
bank utilisation
capex / execution risk
customer concentration
raw-material / forex risk
pricing / pass-through
order book where present
rating strengths
rating weaknesses
upgrade triggers
downgrade triggers
```

#### PPT V1

```text
order book
order inflow
execution horizon
order-book split
installed capacity
capacity utilisation
capacity expansion
capex
commissioning timeline
export share
geography
customer exposure
end-market exposure
raw materials
segment mix
product mix
market-share evidence
moat / competitive-position evidence
growth drivers
```

#### Concall V1

```text
revenue guidance
margin guidance
volume guidance
order-inflow guidance
capex guidance
capacity guidance
export guidance
guidance action
demand outlook
pricing commentary
raw-material commentary
pass-through ability
order pipeline
execution timing
capacity ramp
customer additions / losses
new products
working-capital commentary
debt commentary
quarter anomalies
risks / bottlenecks
Q&A evidence
management commitments
```

---

### 57. Final Principle

The raw ingredients are mostly already present.

The next challenge is not:

> collect every possible document on the internet.

The challenge is:

> make the current document set reliable, complete where applicable, historically reproducible, and convert each document into strict evidence-backed facts that can be compared over time and safely consumed by ML.

The desired pipeline is:

```text
RELIABLE SCRAPER
       ↓
IMMUTABLE DOCUMENTS
       ↓
STRUCTURED FACT EXTRACTION
       ↓
EVIDENCE VALIDATION
       ↓
LONGITUDINAL HISTORY
       ↓
ML FEATURES
       ↓
FOCUS MODEL
```

This should be the document-intelligence foundation for the first Focus ML system.
