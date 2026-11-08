# Phase 5 intelligence quality and research readiness

The baseline is **not ready for operational integration, historical bulk processing
or Phase 7 ML**. Correct numeric emissions have low coverage, periods and entity
scope are mostly absent, some statement types are wrong, and public availability
has not been certified. This evaluation preserves the baseline extractor and all
production safeguards; it adds a bounded evaluator and guarded research features.

Open the [interactive local report](../../../artifacts/phase5/quality-evaluation/v1/results/quality-report.html).
It contains the measured results, benchmarks and a filterable reference viewer
linked to original physical PDF pages and inert text from original HTML.
Machine-readable findings are in [evaluation.json](../../../artifacts/phase5/quality-evaluation/v1/results/evaluation.json).

## Dataset and methodology

The [manifest](../../../artifacts/phase5/quality-evaluation/v1/inputs/manifest.json)
pins 11 original documents from eight companies and eight industry groups:
three presentations, four calls, and four rating/credit reports. Nine originals
are PDFs and two are stored HTML. The set covers 216 source pages; accuracy is
scored only in 26 annotated windows on 20 unique physical/logical pages.

Included companies are Sona Comstar, Tata Power, Dynamic Cables, Welspun
Enterprises, Vimta Labs, Reliance Industries, Sarup Industries and Coal India.
Layouts include slide charts, multi-column and multi-row tables, negative values,
restated comparatives, ordinary speaker labels, timestamped speaker headings,
subsidiary figures, very large/duplicated HTML blocks, and combined rating actions.
Reporting periods span FY20 through FY27. Rating originals are permitted CARE,
India Ratings and Brickwork archives. No CRISIL/ICRA source was requested.

[93 source reference disclosures](../../../artifacts/phase5/quality-evaluation/v1/inputs/references.json)
were entered from actual source material, with source quotes, original hashes,
physical pages, normalized values, units, period/horizon, speaker and scope
expectations. PDF layout was visually inspected where indicated; every reference
also passed independent original-byte checks using pypdf or BeautifulSoup.
Expected values do not come from intelligence predictions or the production
numeric parser. Decimal scaling uses manually entered source values.

These are agent source-reviewed annotations, not independently adjudicated human
gold. A second review remains pending. The initial 92-reference set was frozen
before predictions. A subsequent source rereview added the missing issuer upgrade
preceding its withdrawal in the Sona announcement. The initial snapshot is retained
under `inputs/reference-history/`; v1.1 records that correction transparently.
No expected value was changed to match a prediction. This correction does not
increase a success count: the newly included rating event was also missed.

The sample is purposive and challenging, not random or weighted to the eligible
corpus. Results are window-level pilot measurements, not population accuracy.
Real native PPT/PPTX, scanned corporate documents and a paired original/corrected
version of the same filing were not found in this bounded selection. Existing
PPTX/scanned fixtures and controlled temporal contracts cover engineering paths
separately; their results are not included in corporate extraction accuracy.

## Actual extraction results

Precision is TP/(TP+FP); recall is TP/(TP+FN). A core match requires metric,
value/range, currency/unit and statement kind/basis. Period, horizon, speaker and
entity scope are evaluated separately. Numerical equivalence uses Decimal, not
float tolerance. Matching is one-to-one; one returned number cannot satisfy two
period-specific references. Ambiguous numbers are missing accepted extractions.

The scored qualification results are:

- **Financial metrics:** 10 correct, 0 emission qualification failures, 39 missed.
  Precision **10/10 = 100%**; recall **10/49 = 20.4%**. This tiny emitted subset
  does not establish financial-record completeness, entity attribution or general
  accuracy. Total-income/operating-income amounts are evaluated in the financial
  amount family; original source labels remain in the reference quotes and notes.
  They must not silently become sales denominators in research features.
- **Order books:** 3 correct, 1 qualification failure, 8 missed. Precision
  **3/4 = 75%**; recall **3/11 = 27.3%**. This family also includes explicit
  inflows and disclosed order-book/revenue ratios.
- **Management guidance:** 1 correct, 1 qualification failure, 3 missed.
  Precision **1/2 = 50%**; recall **1/4 = 25%**. One failure is unclassified
  question typing, not an invented management forecast.
- **Capex/capacity:** 1 correct, 1 qualification failure, 3 missed. Precision
  **1/2 = 50%**; recall **1/4 = 25%**. This small category combines an amount and
  qualitative expansion/delay events; the failure is an unresolved value basis.
- **Rating events:** 0 accepted, 9 missed/quarantined. Recall **0/9**. Precision
  is **not estimable** because there are no accepted predictions. All four actual
  upgrade/downgrade actions were missed. Assignment, affirmation and withdrawal
  are separately annotated and never relabeled as upgrades.
- **Liquidity/leverage:** 1 correct, 5 qualification failures, 15 missed.
  Precision **1/6 = 16.7%**; recall **1/16 = 6.25%**. This family includes numeric
  gearing/coverage and qualitative assessments/working-capital commentary.

FP here means an emitted object fails the required qualification signature.
The [error ledger](../../../artifacts/phase5/quality-evaluation/v1/results/error-ledger.json)
separates six incorrect committed typing emissions, one incomplete statement type
and one incomplete value basis. Repeated HTML emissions are counted as returned
objects; they are not independent corporate disclosures. There were **zero
fabricated normalized values identified in the scored emissions**. This is not a
claim that all 1,430 emitted objects or unannotated pages have been adjudicated.
Unadjudicated metrics are explicitly excluded instead of assumed correct.

The separately measured attribution results are:

- **Speaker attribution:** 25 correct, 0 incorrect, 10 missing across 35 references.
  Correct among attributed fields: **25/25**; complete-and-correct coverage:
  **25/35 = 71.4%**. Explicitly anonymous management and source-supported unknown
  roles remain unknown; no individual identity is invented.
- **Actual/projected/conditional basis:** 14 correct, 1 incorrect, 68 missing
  across 83 numeric/event references. Correct among emitted fields: **14/15 =
  93.3%**; complete-and-correct coverage: **14/83 = 16.9%**.
- **Reporting period:** 2 correct, 2 incorrect, 69 missing across 73 references.
  Correct among emitted periods: **2/4 = 50%**; complete-and-correct coverage:
  **2/73 = 2.74%**. A long-term guidance horizon is incorrectly placed in the
  reporting-period field; an HTML block imports a date from another disclosure.
- **Time horizon:** 1 correct, 1 incorrect, 2 missing across four references.
- **Entity scope:** none of 40 explicit consolidated/subsidiary/segment scope
  references is represented. Document issuer identity is not proof of fact subject.
- **Exact source evidence:** **93/93 reviewed emitted objects** passed canonical
  page/span/cell citation validation. Semantic correctness remains separate.
  Correct source quotes can accompany incorrectly typed claims.

Five of six source-based negative controls passed. Guidance refusal was not
treated as withdrawal; an analyst's INR500 crore recollection did not become a
verified management forecast; excluded L1 bids did not become awarded orders;
a sensitivity threshold did not become an accepted actual gearing value; and a
new Coal India credit update did not produce a rating action. The failed control
is the working-capital warning on negligible utilization.

## Highest-impact errors and required corrections

1. **Historical order book classified as guidance.** Welspun physical page 3
   reports an existing INR9,100 crore book as of September 30, 2023. The returned
   VERIFIED record says GUIDANCE/PROJECTED. The broad forward-language pattern
   matches the noun `project`. Replace keyword-wide typing with clause-specific
   predicates; protect historical quantities inside sentences mentioning projects.
2. **Healthy working capital becomes a risk warning.** Vimta CARE physical page 2
   says negligible fund-based utilization. The metric's unconditional risk class
   creates a warning. Add explicit polarity/negation and separate observations,
   improvements, pressure and conditional sensitivities. Use categorical events
   only after evaluated coding rules; do not assign severity numbers.
3. **Document-level failure on nonfinancial layout.** Tata Power intelligence
   aborts on `Invalid evidence: revenue`. A detected chart-layout table on page 10
   cites an empty header as a zero-length span. The
   [failing record](../../../artifacts/phase5/quality-evaluation/v1/results/failing-evidence-record.json)
   preserves that trace. Skip empty citations and reject ambiguous layout grids
   safely; do not weaken source validation. Preserve usable pages while marking
   the affected element unsupported. All eight selected references in this
   restated-document case were consequently missed.
4. **Weak period and subject resolution.** Retain fiscal periods, date ranges,
   comparative columns, cumulative horizons, dates and footnote scopes explicitly.
   Add safe context inheritance that stops at scope/speaker/period changes.
   Preserve CGPL and MEPL separately from parent consolidated results and distinguish
   growth/change from levels. Do not compare mixed period or revenue definitions.
5. **Incomplete speaker formats and boundaries.** Add timestamped/alternative
   heading grammars using Reliance as a real failure example. Treat questions and
   their recalled targets separately from management statements. Preserve roster
   roles, prepared/Q&A boundaries and cross-page evidence.
6. **Rating/table coverage and temporal dates.** Support actual agency/header
   layouts, multi-row financial headers and combined facilities/actions without
   concatenating outlooks. A report date, maturity date, previous rating-history
   date and effective rating date must remain distinct. Do not infer a current
   grade or change from a new report. Missing dates remain explicit.

The baseline files were deliberately not altered to improve this challenge
sample's score. Required parser corrections remain future implementation work.
New safety gates reject inadequately qualified proposals before feature use.

## Point-in-time readiness

[Source-time audit](../../../artifacts/phase5/quality-evaluation/v1/results/source-time-audit.json)
records cover dates, call dates/times, report dates, financial periods, prior rating
dates and capture timestamps separately. Examples include Vimta's July 20 call
and July 24 cover; Reliance's January 16 20:00 IST call and January 17 cover;
Coal India's June 30 business-risk as-of and August 28 report; and Sarup's January
21, 2022 **previous** rating date. None is an independently verified publication
timestamp for the stored content version.

**0/11 documents and 0/1,430 emitted objects are certified for historical public
backtesting.** The existing `eligible_at` API proves capture-safe eligibility only.
After capture it can accept a record with unknown publication time. The new
`public_pit_readiness` gate requires a source-validated public timestamp receipt
bound to the exact content hash, plus conservative local capture/ingestion limits.
Its current-rating path also blocks unchecked later events and unknown effectiveness.
[Every emitted record has a readiness disposition](../../../artifacts/phase5/quality-evaluation/v1/results/record-readiness.jsonl).

Real cases cover restated FY20 comparatives published in a later Tata Power
presentation; price/volume correction within the Dynamic call; repeated long-term
guidance without independently supplied earlier evidence; annual versus quarter
figures; and mixed rating announcements. A real two-version corrected filing pair
is still missing. Controlled contracts test supersession before/after cutoffs,
future publication despite early capture, ingestion after cutoff, same guidance,
opposing range bounds, unknown metadata and rating effectiveness. They are
engineering checks, not extraction accuracy or real historical availability proof.

The conservative gate does not yet certify retrospective public backfills captured
later than the prediction date. That needs a separately audited version-specific
public-availability protocol, not a change that simply backdates capture or uses
the latest corrected filing. Default scope, missing-data zeros and stale-current
ratings are prohibited by the new feature path.

## Research features

[Feature examples](../../../artifacts/phase5/quality-evaluation/v1/results/research-feature-examples.json)
preserve original hashes/pages/passages and reference IDs. Seven examples have
source-checked values; four have explicit missing indicators. **These use reviewed
source annotations, not successful automated baseline outputs.** Human adjudication
and public-PIT certification remain pending; all examples block ML admission.

- Order-book growth: Q3FY23 INR238 billion to Q3FY24 INR240 billion, same discounted
  awarded-program definition; `(240/238 - 1) × 100 = 0.840336134454%`, Decimal
  half-even rounding to 12 places. This is reported book growth, not future revenue.
- Order-book/revenue: directly disclosed **9.0x**, Q3FY24 book over FY23 revenue.
  It is not recomputed using incompatible quarterly revenue or total income.
- Capex: Vimta's approximately **INR800 million FY27 budget announcement**, with
  visibility/demand/project phasing conditions. It is not capex already spent.
- Margin outlook: MEPL's explicit expected improvement, retained as a categorical
  statement with no invented numeric target or severity score.
- Execution risk: Dynamic's explicit approval/logistics project delays, retained
  as `DISCLOSED_PROJECT_DELAY`, with no severity score.
- Rating events: Sona facility upgrade and Sarup facility downgrade, retaining
  agency/facility/grade/action, unknown effective date and no current-grade claim.
- Guidance change, liquidity direction, working-capital pressure and demand risk
  remain UNKNOWN where comparable prior statements or evaluated warning evidence
  are absent. Negligible utilization is not pressure; missing is not zero.

Reusable code is in `research_features.py`; automatic proposals require citation,
semantic, period/basis and scope qualification. Comparison methods must also
agree on identity, agency/facility, period/horizon, units and definitions.

## Local benchmarks and scaling

[Per-document/stage measurements](../../../artifacts/phase5/quality-evaluation/v1/results/evaluation.json)
and [summary](../../../artifacts/phase5/quality-evaluation/v1/results/benchmark-summary.json)
use sequential fresh children, one cold measurement and two warm repeats. Each
stage's CPU time is recorded. RSS is the process high-water mark including imports,
not incremental allocation. Timed operations exclude source I/O, serialization,
child startup and persistence. Corporate originals are only these 11 documents.

- Native PDF/HTML extraction completed **11/11**. Summed cold time **6.206 s**;
  summed warm medians **5.832 s**; maximum child RSS **283.2 MiB**.
- Intelligence completed **10/11**. Successful-document cold sum **0.218 s**;
  warm-median sum **0.208 s**; maximum successful-child RSS **44.0 MiB**.
  The failed document is not counted as successful throughput.
- The ten successful matched pairs imply **2.17 documents/s** in preloaded
  compute-only sums. This is not observed sustained production throughput or a
  historical-corpus completion forecast. The sample mixes tiny HTML and larger PDFs.
- Existing PPTX fixture: extraction warm median **5.6 ms**, **65.1 MiB**, partial
  without OCR on its image-only slide. A real corporate PPTX accuracy score is not
  available. Legacy PPT was not benchmarked and remains bounded text recovery.
- Existing scan fixture: OCR disabled yields failed text extraction; **16.4 ms**
  warm median and **115.8 MiB**. Local Tesseract makes text usable at **162.6 ms**
  warm median and **117.1 MiB**. OCR accuracy is not measured by this synthetic
  format benchmark; numerical facts still require validation.
- Hardware is ARM64, ten logical CPUs, Python 3.14.5. No GPU or interpretation LLM
  ran; paid external model calls/cost are zero. Local LLM latency/memory/cost is
  unmeasured, not estimated from these deterministic numbers.

After quality gates pass, start with one bounded local process; reuse matching
canonical D03 extractions by original hash/parser/options instead of reparsing.
Rehearse small manifests of 8–12 documents, an initial 50 MiB/100-page/60-second
document ceiling and a measured memory budget (for example 512 MiB per process).
Quarantine unsupported/timeout cases and measure complete I/O/persistence overhead
before adding concurrency. These are proposed local processing bounds, not activated
workers. They say nothing about publisher request rates or scraping permissions.

## Exact remaining gates

Before Phase 5 operational integration:

1. Independently adjudicate the references and annotation correction; obtain real
   revised-version pairs, native PPT/PPTX/OCR corporate cases and a company/time/layout
   holdout. Freeze the scoring policy and retain per-error/source traces.
2. Implement the required parser/typing/scope/date corrections and their regressions.
   Compare baseline and corrected results separately, then validate on the holdout.
3. Retrieve source/capture/publication/ingestion/version and identity provenance
   from an approved isolated archive or DB copy. Certify public availability rather
   than infer it from a cover or reporting-period date.
4. Qualify records and materialize versioned features with missing indicators,
   evidence references, definitions, denominator checks and conflict/dedup rules.
   Reject unresolved scope, period, basis or rating effectiveness/currentness.
5. Rehearse bounded persistence into the existing architecture on an isolated copy;
   measure I/O, memory ceilings, timeouts, restart behavior, retries and idempotence.
6. Review the corpus eligibility manifest and operational proposal. Worker/scheduler
   integration remains a separate step; no schedule has been created or activated.

Before Phase 7 ML additionally require PIT security/universe/sector membership,
time-aware price/target data, company/time splits, label embargoes where relevant,
leakage audits, feature/version snapshots, reproducible missingness policy and
out-of-sample research validation. Engineering test success is not predictive value.

## Code, validation and preservation

Added `evaluation.py`, `research_features.py`, source annotation/report scripts,
the frozen evaluation corpus and `test_phase5_quality_evaluation.py`.
**79 tests passed in 4.16 seconds**, including 19 quality/gating checks, the original
38 Phase 5 contracts and 22 reused parser regressions. Tests use disposable stores.
The baseline extractor and 99 protected operational source files remain unchanged:
107 combined files were hash-checked. Eleven original volume files and all isolated
copies match their original hashes. No production DB was opened, no source was
requested, and no production worker, schedule, migration or bulk job ran.

Reproduce the bounded evaluation and local report from the repository root:

```sh
phase5_quality_output="$(mktemp -d /private/tmp/trading-ai-phase5-quality.XXXXXX)/results"
scrapper/.venv/bin/python -m scrapper.research.document_intelligence.evaluation run \
  --manifest artifacts/phase5/quality-evaluation/v1/inputs/manifest.json \
  --references artifacts/phase5/quality-evaluation/v1/inputs/references.json \
  --format-fixtures artifacts/phase5/quality-evaluation/v1/formats/manifest.json \
  --output "$phase5_quality_output" --repeats 3
scrapper/.venv/bin/python artifacts/phase5/quality-evaluation/v1/build_report.py \
  --results "$phase5_quality_output"
```

Output is new-only, sources are hash-checked, and the extractor/operational source
baseline must still match. The runner records per-stage errors without silently
dropping them; extraction failures contribute missed references. Full saved
readiness/time-audit and failing-record traces are included with this evaluated
deliverable. No database migration or new production process is needed to reproduce.
