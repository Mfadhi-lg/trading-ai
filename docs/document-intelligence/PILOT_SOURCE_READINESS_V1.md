# DI-02 — Real-document pilot source readiness V1

Audit date: 2026-10-26. Status: **PARTIALLY_READY for extraction-pilot preparation; not authorized for inference.** The ten P1 fields in [FEATURE_DICTIONARY_V1.md](FEATURE_DICTIONARY_V1.md), section 9, remain frozen. This is a bounded source audit, not an independently labeled dataset or a production-health assessment.

## Executive decision

**27 hash-verified, readable originals are suitable for independent source labeling**, across 15 issuers: 12 credit reports, seven investor presentations and eight issuer earnings-call transcripts. One additional hash-verified HGS presentation is a conditional reserve pending legal-identity/period review: the manifest therefore contains **28 distinct originals**, 16 issuer identities including that reserve. Copies of the same bytes count once. The exact first set is D01–D27 below; D28 is excluded from its coverage gate.

The five credit fields have enough *source candidates* to begin labeling: 12 LT-rating, 11 outlook, 12 liquidity, nine nonfinancial interest-coverage and five numerical bank-line-utilization document/field positives. These are presence observations, not complete semantic checks. Presentation backlog and inflow each have two positives, both Sona periods; planned presentation capex has zero confirmed positives. Revenue-growth guidance has zero confirmed and three possible positives; margin guidance has one confirmed and two possible positives. **All five presentation/transcript fields are INSUFFICIENT_SAMPLE.** Multiple rows, facilities, years or repeated answers in one PDF do not repair these diversity gaps.

There are **54 confirmed document/field positive candidates**, ten additional possible document/field candidates, and **22 nominated field-specific control cases** (lists below). These counts overlap documents and are not 86 statistically independent trials. All are independently *reviewable*, but **zero independently human-certified DI-02 gold instances have been established**. Credit readiness is provisional until reviewers resolve basis, date, qualifiers and controls; no family has passed an evaluation.

**Genuine blind holdouts established: zero.** Twenty-four manifest sources have retained evaluation/correction exposure; four have preparation exposure and unestablished runtime-model exposure. No unseen-issuer blind sample is certified. The first 27 split into 23 regression and four development sources. An isolated credit regression benchmark could be considered after independent labeling and a separate authorization; the full ten-field blind pilot remains blocked by sample and contamination gaps. Historical ML admission is blocked for every candidate under the strict dictionary policy, independently of extraction usefulness.

## Contracts, method and bounds

Read the [North Star](../../TRADING_AI_NORTH_STAR.md), [accepted dictionary](FEATURE_DICTIONARY_V1.md), and [cleanup audit](../CLEANUP_AUDIT_2026-10-26.md). The cleanup audit protects originals and experiment history; it does not certify live corpus availability or permit operational changes. Relevant implementation contracts inspected were `scrapper/documents/extraction.py`, `scrapper/documents/transcript.py`, `scrapper/documents/availability.py`, `scrapper/research/document_intelligence/schema.py` and `evidence.py`. D03 retains numbered physical pages/slides, native/OCR origins, elements, geometry and tables; DocumentContext binds original hash, company/content IDs and separate source/capture clocks. Evidence validation binds offsets/cells but does not prove semantic entailment, headers, exhaustiveness or publication chronology. Do not inherit older capture-only eligibility as dictionary section 8 certification.

Inventory started with both `sample_files-2/*/manifest.json` records (17 files), cross-checked with original bytes and declared hashes. The `sample_files/` duplicates were not additional observations. Added the five missing-by-hash originals in `artifacts/unified-financial-pilot-v0.1/pilot_manifest.json`, resolved by exact locators on the mounted document drive. Added the GNA Phase 8 and Aether Phase 5 frozen source selections, three targeted original inputs from the Phase 5 source-time audit (Sona FY23, Dynamic Cables, Welspun), and one inspected HGS presentation reserve from the financial-evidence study. Selection is purposeful and biased toward retained research; this is not a random sample or exhaustive drive census.

Read only the top-level document-drive directory and five explicitly inventoried hash-addressed files; did not recursively traverse the external corpus. Hashed selected originals and used installed `pypdf` native-text reading in memory for page-numbered discovery and selected surrounding passages. Inspected relevant persisted canonical records; did not run the application parser, OCR, a new parsing pipeline, model runners, network requests or downloads. Native text is a discovery aid: charts, scans, merged cells and table associations still need original visual review. No complete relevant-source absence review was performed.

Canonical production identities were checked through **retained D03 exports**, not a live database connection. Five external originals bind exact artifact/content/extraction IDs in the CSV. Additional hash-bound IDs/capture receipts come from the retained publication-provenance audit for six sources. Production `/data/scraper.db` was not opened, Docker was not invoked, and `scrapper/store/scraper.db` was not treated as production. Current preferred versions, ingestion acceptance and live-record completeness remain unverified; safe retained records were sufficient for this extraction inventory. There was no need to risk a mutable live-DB read.

Exposure search matched the 24 initially selected original hashes across JSON/JSONL/CSV/Markdown, at most 5 MB per file, in `docs/phase5`, `docs/credit-rating-intelligence-v1`, `artifacts/phase5`, `artifacts/credit-rating-intelligence-v1`, `artifacts/three-document-reality-check-v0.4`, `artifacts/unified-financial-pilot-v0.1` and `experiments`. It returned 1,360 matching retained files. The three additional sources were already hash-bound members of the quality-evaluation/corrections cohort; HGS has a retained financial-evidence report. Checked source manifests, model-request manifests, canonical exports, evaluation and correction inventories. This bounded check does not certify absence of exposure in larger files, omitted formats, private notes, removed runs or external histories. No-match and an old filename containing “unseen” are insufficient evidence of blindness.

## Manifest conventions and source problems

[PILOT_SOURCE_MANIFEST_V1.csv](PILOT_SOURCE_MANIFEST_V1.csv) is the auditable source of exact locators, complete SHA256, byte/page counts, metadata, exposure and all ten field observations. Stable IDs are `DI02-` plus the first 12 original-hash characters; D01–D28 are readable audit aliases only. Full hashes must govern all joins and future freezes. Repository locators are relative to repository root; `/Volumes/My Passport/passport/trading-ai/artifact_content/...` is included only where necessary to reopen the exact external original. No credentials or unrelated home paths are copied.

Empty date/ID cells mean unknown, not not-applicable. Document dates, filing-letter dates and call dates are separately recorded; none is certified public availability. Captured-at values for six originals are explicitly **retained hash-bound fetch observations**, not fresh DB verification. Extraction-completion dates for five D03 exports remain in parser metadata and are not substituted for capture. Local file mtime, PDF CreationDate and the audit read date never become capture/publication receipts. All publication timestamps remain unknown under the required certification standard.

Company name/symbol mappings from source covers and manifests support labeling, but historical legal-entity/security joins are not all certified. SS Steel and Power, Venus Jewel and Finstars have no listed-equity symbol established here; they are useful extraction/control sources without a fabricated ticker or historical Focus membership. HGS full legal-name mapping is inferred from its original HGS/teamhgs.com/Hinduja Global body and requires review. No historical sector taxonomy is certified: sector groups below are audit descriptions of source businesses. All N/A cells are proposed inventory applicability observations requiring human approval, not admitted contract values.

Specific hazards preserved:

- D01 has an October 9 issuer letter enclosing an October 8 rating rationale. D11 similarly has May 31 filing and May 30 rating dates. Do not collapse clocks.
- D04 explicitly reaffirms **and withdraws** ratings. D13's May 2025 advisory explicitly says it is not a reaffirmation and includes an older rationale; the older financial windows remain separate.
- D14 has current negative **watch**, previous Stable outlook, and separate consolidated/standalone financial tables. D15 is a revised release correcting a historical date; its URL's January name does not establish the May version's publication.
- D23 consolidated ICRA and D04 standalone CARE GNA are different source versions, agencies and perimeters, useful for tests but not a naive time series.
- D19–D22 have official-content flags in hash-bound D03 records and issuer filing covers. D07–D10 and D26 have issuer transcript letters. D27 is an issuer earnings transcript with a management roster and a disclaimer giving exchange audio precedence; this audit does not fetch that audio. Official-source evidence supports transcript selection, not independent truth of management forecasts.
- No selected original is missing or hash-mismatched. Original source URLs are unavailable for several archived samples; exact bytes remain usable for labeling. External drive availability is verified only for the five selected files at audit time, not durable backup or corpus-wide health.

## Document and company coverage; exact proposed first set

The first set has 12 credit reports, seven decks and eight transcripts, exceeding four per type. It includes automotive components, industrial cylinders/engineering, steel, chemicals/API, healthcare testing, electrical cables and infrastructure/water; three financial issuers provide sector controls. Layouts include multiple agency rating tables, issuer cover letters, mixed LT/ST rows, charts/bridges, complex financial tables and speaker-labeled Q&A. Conditions, ranges and ambiguous adoption are explicitly located below. Layout diversity is observed structurally, not a certified visual-layout benchmark.

EKC provides Q3/Q4 FY25 decks and calls; Sona provides Q3 FY23/Q3 FY24 decks plus FY24 call. GNA has 2024 and 2026 rating reports. Thus two-period coverage exists for at least two issuers without counting repeated historical columns inside one PDF. Repeated issuer/periods are correlated; source diversity is strongest in credit, weak in presentation backlog/inflow. Financial controls are not additional industrial positive examples.

| Alias | Company / source | Type | Date or period | Assignment |
| --- | --- | --- | --- | --- |
| D01 | IDFC First Bank / Brickwork Ratings; IDFC FIRST Bank filing | credit_rating | 2025-10-08 | REGRESSION |
| D02 | Alivus Life Sciences / India Ratings and Research; Alivus filing | credit_rating | 2025-09-16 | REGRESSION |
| D03 | Vimta Labs / CARE Ratings | credit_rating | 2023-07-11 | REGRESSION |
| D04 | GNA Axles / CARE Ratings | credit_rating | 2024-06-14 | REGRESSION |
| D05 | Vimta Labs / Vimta Labs | presentation | Q1 FY27; historical FY22-FY26 | REGRESSION |
| D06 | Sona BLW Precision Forgings / Sona BLW Precision Forgings | presentation | 2024-01-23 | REGRESSION |
| D07 | Vimta Labs / Vimta Labs | transcript | 2026-07-24 | REGRESSION |
| D08 | Sona BLW Precision Forgings / Sona BLW Precision Forgings | transcript | 2024-05-07 | REGRESSION |
| D09 | Everest Kanto Cylinder / Everest Kanto Cylinder | transcript | 2025-02-25 | REGRESSION |
| D10 | Everest Kanto Cylinder / Everest Kanto Cylinder | transcript | 2025-05-31 | REGRESSION |
| D11 | Walchandnagar Industries / Acuité Ratings and Research; Walchandnagar filing | credit_rating | 2025-05-30 | REGRESSION |
| D12 | REC Limited / CARE Ratings | credit_rating | 2025-03-28 | REGRESSION |
| D13 | SS Steel And Power / CRISIL Ratings | credit_rating | 2025-05-23 | REGRESSION |
| D14 | Venus Jewel / ICRA | credit_rating | 2025-09-24 | REGRESSION |
| D15 | Finstars Capital / Infomerics Ratings | credit_rating | 2025-05-09 | REGRESSION |
| D16 | Everest Kanto Cylinder / Everest Kanto Cylinder | presentation | 2025-02-17 | REGRESSION |
| D17 | Everest Kanto Cylinder / Everest Kanto Cylinder | presentation | 2025-05-23 | REGRESSION |
| D18 | Everest Kanto Cylinder / CARE Ratings | credit_rating | 2024-09-26 | REGRESSION |
| D19 | IDFC First Bank / IDFC FIRST Bank | presentation | 2026-07-25 | DEVELOPMENT |
| D20 | IDFC First Bank / IDFC FIRST Bank | transcript | 2026-07-31 | DEVELOPMENT |
| D21 | REC Limited / REC Limited | presentation | 2025-02-07 | DEVELOPMENT |
| D22 | REC Limited / REC Limited | transcript | 2025-02-14 | DEVELOPMENT |
| D23 | GNA Axles Ltd. / ICRA | credit_rating | 2026-01-09 | REGRESSION |
| D24 | Aether Industries Limited / ICRA | credit_rating | 2026-05-26 | REGRESSION |
| D25 | Sona BLW Precision Forgings / Sona BLW Precision Forgings | presentation | 2023-01-24 | REGRESSION |
| D26 | Dynamic Cables Limited / Dynamic Cables Limited | transcript | 2026-05-18 | REGRESSION |
| D27 | Welspun Enterprises Limited / Welspun Enterprises Limited | transcript | 2023-11-10 | REGRESSION |
| D28 | Hinduja Global Solutions (HGS) / HGS / Hinduja Global | presentation | 2015-11-17 | Reserve only; identity review |

D01–D27 are the **frozen proposed first labeling set**, with exact identities in the CSV. D28 is a reserve, not a replacement or coverage filler. This freezes source selection for preparation; it does not create blind evidence. Changes require a documented new manifest version, reasons and an exposure reassessment before future outputs are examined.

## Ten-field availability matrix

Legend: **C** = `DISCLOSURE_CONFIRMED`; **P** = `POSSIBLE_DISCLOSURE_NEEDS_REVIEW`; **O** = `NO_DISCLOSURE_OBSERVED`; **X** = `NOT_APPLICABLE`; **A** = `NOT_ASSESSED`. `SOURCE_UNAVAILABLE` is supported by the inventory vocabulary but unused because every selected original opened successfully. C means the relevant disclosure wording/row is present; it does not certify value, basis or full qualifications. P needs interpretation or could resolve to a control. O means no positive was observed in the bounded inspection, never complete-document absence. A outside a field's intended source family makes no absence or applicability claim. No cell assigns contract `NOT_DISCLOSED`.

Columns: LT = long-term rating; OUT = rating outlook; LIQ = liquidity; ICR = interest coverage; WC = working-capital limit utilization; OB = order-book value; OI = order inflow; CX = planned capex; RG = revenue-growth guidance; MG = margin guidance. Exact full field names and physical-page references appear in the CSV.

| Document | LT | OUT | LIQ | ICR | WC | OB | OI | CX | RG | MG |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| D01 | C | C | C | X | X | A | A | A | A | A |
| D02 | C | C | C | C | P | A | A | A | A | A |
| D03 | C | C | C | C | P | A | A | A | A | A |
| D04 | C | C | C | C | C | A | A | A | A | A |
| D05 | A | A | A | A | A | O | O | O | A | A |
| D06 | A | A | A | A | A | C | C | O | A | A |
| D07 | A | A | A | A | A | A | A | A | P | O |
| D08 | A | A | A | A | A | A | A | A | O | P |
| D09 | A | A | A | A | A | A | A | A | O | O |
| D10 | A | A | A | A | A | A | A | A | O | P |
| D11 | C | C | C | C | C | A | A | A | A | A |
| D12 | C | C | C | X | X | A | A | A | A | A |
| D13 | C | C | C | C | C | A | A | A | A | A |
| D14 | C | P | C | C | C | A | A | A | A | A |
| D15 | C | C | C | X | X | A | A | A | A | A |
| D16 | A | A | A | A | A | O | O | O | A | A |
| D17 | A | A | A | A | A | O | O | O | A | A |
| D18 | C | C | C | C | C | A | A | A | A | A |
| D19 | A | A | A | A | A | X | X | O | A | A |
| D20 | A | A | A | A | A | A | A | A | O | X |
| D21 | A | A | A | A | A | X | X | O | A | A |
| D22 | A | A | A | A | A | A | A | A | O | X |
| D23 | C | C | C | C | P | A | A | A | A | A |
| D24 | C | C | C | C | P | A | A | A | A | A |
| D25 | A | A | A | A | A | C | C | O | A | A |
| D26 | A | A | A | A | A | A | A | A | P | O |
| D27 | A | A | A | A | A | A | A | A | P | C |
| D28 | A | A | A | A | A | O | O | O | A | A |

Counts below cap each positive at **one document/field**, regardless of facilities, time columns or repeated statements. These are conservative source-screening units; independent labeling may enumerate more atomic claims, but repeated claims do not increase sample adequacy. Controls are nominated passages (including ambiguous qualifications and sector exclusions), not independently proven whole-document negatives. A source may contain both positive and control passages.

| Frozen P1 field | Confirmed document positives | Possible documents | Reviewable control cases | Named controls | Readiness |
| --- | --- | --- | --- | --- | --- |
| `cr_long_term_rating` | 12 | 0 | 2 | D04 p1; D13 p1/p5 | SOURCE_CANDIDATES_SUFFICIENT; labels pending |
| `cr_rating_outlook` | 11 | 1 | 2 | D14 p1/p5; D13 p1/p5 | SOURCE_CANDIDATES_SUFFICIENT; labels pending |
| `cr_liquidity_assessment` | 12 | 0 | 2 | D11 p3; D24 p3 | SOURCE_CANDIDATES_SUFFICIENT; labels pending |
| `cr_interest_coverage_ratio` | 9 | 0 | 3 | D01 p6; D12 p6; D15 p5 | SOURCE_CANDIDATES_SUFFICIENT; labels pending |
| `cr_working_capital_limit_utilization_pct` | 5 | 4 | 2 | D03 p2; D02 p3/p4 | SOURCE_CANDIDATES_SUFFICIENT; labels pending |
| `ip_order_book_value` | 2 | 0 | 2 | D19 p2; D21 p2 | INSUFFICIENT_SAMPLE |
| `ip_order_inflow_value` | 2 | 0 | 2 | D19 p2; D21 p2 | INSUFFICIENT_SAMPLE |
| `ip_planned_capex_value` | 0 | 0 | 3 | D05 p6/p9; D17 p7; D21 p14-p16 | INSUFFICIENT_SAMPLE |
| `ec_revenue_growth_guidance_pct` | 0 | 3 | 2 | D08 p13; D09 p5 | INSUFFICIENT_SAMPLE |
| `ec_margin_guidance_pct` | 1 | 2 | 2 | D20 p10; D22 p13 | INSUFFICIENT_SAMPLE |

The 22 control cases are the sum of the per-field counts, not 22 different PDFs. Positive counts sum to 54; possible counts sum to ten. “Sufficient” here means the source inventory meets the proposed five-document/two-control screening target only. It does not mean independent gold exists, all formulas are known, or blind performance can be estimated. No additional fields, transcript capex substitution for presentation capex, or rating-report backlog substitution for presentation backlog are used to repair sparse fields.

Notable independently reviewable source windows:

- D03 p2/p4, D04 p2/p3, D02 p4, D11 p3, D13 p5/p6, D14 p4, D18 p2/p4, D23 p4 and D24 p4 support ICR discovery. Review actual/projected flags, signs, headers and formulas. Financial-sector ratios printed in D12/D15 remain exclusions from the frozen industrial definition.
- WC positives: D04 p2 (around 79%, statistic unresolved), D11 p3 (fund/nonfund averages), D13 p6 (82% average in older window), D14 p2-p3 (41% average), D18 p3 (about 34%, **average maximum**, inclusive facilities). D02 p3 capacity utilization is a wrong-metric trap; p4 nonfund 19%/unused fund lines requires denominator review. D03 negligible is not zero. D23 headroom and D24 “high” are not disclosed percentages.
- D06 physical p14 (printed slide 13) and D25 physical p13 (printed slide 13) each separate order-book stock, consumed programs and new awards, with a ten-year revenue definition. Review executability, cancellations and remaining ramp-up assumptions. Do not treat several program rows as independent document examples.
- Planned capex has no confirmed presentation positive. D05 p6/p9 is historical cash spending; D17 p7 impairment is not future investment; D21 p14-p16 borrower capex financing is not issuer capex. Useful future-spend passages in rating reports and transcripts remain contextual, outside this P1 presentation sample.
- D07 p16 contains analyst-proposed 20–25% growth, with management discussing missed business rather than clearly adopting that guide. D26 p5/p14/p15 contains medium/long-term 18–20% value-growth statements and refusal of annual guidance; speaker/horizon must be resolved. D27 p8 has inconsistent fiscal wording around analyst-proposed 15–20%. None is promoted to a confirmed annual revenue-guide instance.
- D27 p8 expressly retains **operating** EBITDA margin 10–12%, distinguished from reported EBITDA. D08 p12 hypothetical reinvestment and D10 p3/p7 regional ranges remain possible, not unquestioned guidance. D20 p10 and D22 p13 NIM percentages are sector controls.

## Development, regression and holdout separation

**REGRESSION:** D01–D18, D23–D27; D28 reserve. Hash matches establish historical evaluation/correction exposure. The manifest records per-document evidence; it does not assume every source had every exposure mechanism. Prompt tuning, parser tuning, model comparison and manual correction each have separate YES/UNKNOWN columns. Historical reference files are contamination evidence, not independent labels. Deterministic acquisition parsing alone is not proof of model exposure.

**DEVELOPMENT:** D19–D22. They occur in initial/final/replayed unified preparation manifests and were selected for preparation. Their old `evaluation` split plus `no_hash_match_in_bounded_history_search` does not prove unseen status. No evaluation/model receipt was established here; runtime exposure remains **EXPOSURE_UNKNOWN** in substance, retained explicitly in the CSV. Their authorized preparation exposure is sufficient to exclude them from a new clean holdout, while distinguishing it from demonstrated model evaluation. This audit's source reading is also preparation exposure.

The source-hash audit overrules stale per-document exposure annotations: for example, EKC's credit source D18 had no old hash match in initial unified discovery, but later EKC generalization/model-ladder references match it. Similarly, Phase 8 GNA's earlier “new candidate” report predates its retained live evaluations and corrections. Qwen/Gemini request manifests show prompt/model-comparison preparation; **prepared/not-sent manifests do not prove a paid call was executed**. Later evaluation/correction inventories separately establish regression use. No model-response values were used as gold or to manufacture the availability matrix.

**BLIND_HOLDOUT_CANDIDATE: none established; certified blind: zero.** Do not rebrand any current selection as blind. Before later prompt/rule development, an independent custodian must identify apparently unexposed originals, search exact hashes and issuer aliases against prompt inputs, raw-response source manifests, parser-tuning cohorts, model comparisons, corrections and historical evaluations, record search bounds/unknowns, and freeze hash, issuer group and assignment. Unknown exposure stays `EXPOSURE_UNKNOWN`, excluded from blind claims. Reserve at least one entirely unseen issuer; where issuer-level leakage exists, place its related versions in the same split. Holdout source labels stay with the custodian; developers receive only allowed training sources and the frozen protocol. An empty holdout is the honest current freeze. This audit authorizes no acquisition to fill it.

## Independent human ground-truth protocol

No prior “reviewed”, reference-private, agent-authored, adjudicated or model-agreed record is accepted as independent gold without verified author, independence and source-review provenance. Coding-agent observations here cannot certify themselves. Existing model answers may inform research history after labeling, not seed the reviewers' answer sheet.

1. Assign two named human reviewers independent of prompt/model development, plus a named human adjudicator. Record conflicts/previous exposure. Provide exact original bytes, their hashes, the frozen dictionary and blank source-first forms; hide model outputs, agent candidate answers and private references during primary labeling. Inventory page hints may be used for navigation only after each reviewer performs an independent discovery pass; this prevents those hints defining recall truth.
2. For each document and each applicable field record a document/field review ID, source hash, company/legal-entity evidence, applicability decision and source universe. Record reviewer identity, UTC timestamp with offset, protocol/dictionary version and independence attestation. Begin with `NOT_ASSESSED/PARTIAL`; allow `UNRESOLVED` and missing identity/period rather than guessing.
3. For every atomic disclosure capture exact original text or complete table row plus row/column headers, units and all supporting footnotes/continuations; physical page/slide, printed page/slide if different, element/table/row/column or bounded image region. For transcripts record speaker label, named role, turn boundaries and antecedent question. Preserve original spelling/numeric token; no paraphrase can replace original evidence. Log scan/OCR/layout problems.
4. Record raw and normalized value, currency, scale, sign, comparator, low/high bounds/inclusivity and approximation; separately retain period literal/resolved interval, actual/estimate/forecast basis, horizon, standalone/consolidated/segment/facility/project, agency/issuer/speaker, stance, metric formula and denominator. Keep conditions, financing assumptions, forecasts, restrictions, cancellations and qualifications as linked atoms. A disclosed forecast is a fact about a statement, not a realized outcome.
5. Record existence independently of extractability: disclosed but ambiguous numerical basis is not absent. Distinguish no relevant disclosure, non-applicability with sector evidence, partial/unavailable source, unresolvable disclosure and conflicting same-grain values. A possible observation can resolve positive, negative or unresolved. Never force a range into a midpoint or numerical zero from “negligible”.
6. To mark `COMPLETE_RELEVANT_SOURCE_REVIEW`, explicitly enumerate the exact document/period search universe, every physical page/slide/turn, relevant tables/charts, annexures, footnotes and continuation links; record page-by-page coverage and each reviewer sign-off. Reconcile native text to original visual pages and resolve every material missing/illegible region. External schedules/attachments referenced but not available must be recorded; if capable of changing the field, coverage remains partial. A reviewer may narrow the source universe to the complete supplied PDF, never claim issuer-wide absence. Only after complete review can that field/source-set receive contract `NOT_DISCLOSED` with reviewer, timestamp and coverage attestation. Zero is a positive disclosed value.
7. Compare the two independent annotations before revealing model outputs. Record all disagreements about existence, values, period, scope, attribution, qualifications and coverage; preserve both versions. Adjudicator reopens originals, records decision/evidence/reason/identity/time, or leaves unresolved. Adjudication does not erase disagreement rates or old versions. Freeze label IDs and content hashes, source coverage, unresolved exclusions and adjudication version before evaluation. Any later gold correction must have source-based human provenance and trigger a versioned rescore of every compared system.

Ready now: 27 sources can enter this protocol; no labeling has occurred in DI-02. Full ten-field label coverage and blind labels cannot yet be completed from the confirmed inventory alone. D28 requires identity/period review before admission to labeling set.

## Frozen proposed evaluation design V1

The following design is fixed for a future authorized feasibility run; no performance values are measured here. Dictionary section 9.1 thresholds remain: at least five independently labeled positive source cases plus two field-relevant controls; raw candidate recall **at least 80% per field**; **100% final admitted material value/unit/attribution/period/scope/qualification correctness**; zero unsupported control admissions. Report raw precision without substituting final correctness for it. Missing sample/coverage/holdout is **INCONCLUSIVE**, never a passed field. Additional model choice, pinned parser/configuration, budget/timeout/retry policy and independent reviewer identities must be frozen before authorization; no tuning against held-out answers.

### Units, matching and denominators

An atomic claim key is field × original source version × company/legal entity × period/horizon × scope × facility/segment/project × agency/metric basis/statistic, adding speaker/stance where material. One claim may contain a preserved range. Separate years/facilities may produce separate claims if the source discloses them separately. Repeated mentions of the same claim inside one document deduplicate; repeated URLs or copies never add trials. Different documents count as separate observations but remain clustered by issuer. Identical underlying disclosure repeated across documents is identified and reported as a correlated version, not independent corroboration.

Freeze the matching rules against adjudicated gold before running models. Match one-to-one; correct entity/dimensions and complete material qualifiers are required. Wrong numerical value/unit/scale/period/company/facility/segment/speaker/stance/material qualification makes an incorrect candidate, even if its digits occur somewhere on the cited page. In primary strict scoring it is an FP and leaves its unmatched gold claim an FN. A correct abstention on an unresolvable statement is not a fabricated positive; disclose its coverage cost. Do not score withheld ambiguous source claims as guessed numerical truth.

Only complete-source-reviewed document/field pairs can support exhaustive recall/absence denominators. Report partial pairs separately with provisional discovery counts; exclude them from primary recall, never count unreviewed pages as true negatives. Sample adequacy counts positive **source documents**, capped at one per field, in addition to separately reporting claim counts. New control gold must identify the exact confusable passage and expected rejection/abstention; for whole-document absence it must carry complete coverage.

### A — Raw AI extraction, before corrections

Retain the first raw output and every retry/failure, exact request/source/canonical hashes, model/provider/version, prompt/configuration, run time and cost receipt. No human additions, repaired source answers or hand-completed fields enter this score. If deterministic postprocessing is part of the proposed extractor, freeze and identify it and report the raw-response and postprocessor boundaries separately.

- **Precision:** strict correct deduplicated emitted disclosure claims / all evaluable deduplicated emitted disclosure claims. Label fabricated/nonexistent disclosure separately from wrong value on a real disclosure. Malformed output contributes failure and zero recovery; assess any emitted claims rather than silently dropping a bad response from precision accounting. No emissions gives undefined precision, not 100%.
- **Recall:** strictly recovered adjudicated positive claims / all adjudicated resolvable positive claims in complete reviewed source pairs. An abstention on a resolvable positive is FN. Distinguish omitted claims from emitted-but-wrong claims. Zero gold positives gives undefined recall, not a passing field.
- **Error rates:** report each of false disclosure, wrong numeric token, unit/scale, reporting period/horizon, company/facility/segment, speaker/stance and lost material qualification as count / evaluable emitted claims, plus count / relevant subtype (for example numerical claims) and count / documents. Categories may overlap; do not sum them as disjoint failures. Unsupported citations or header relationships are incorrect support even with a matching number.
- **Control false positives:** inappropriate positive emissions on nominated controls / reviewed controls, and documents with such emissions / control documents. Record attempted guesses on N/A versus ambiguous versus complete-reviewed absence separately. Explicitly emitted unsupported `NOT_DISCLOSED` or N/A decisions are also errors, assessed against reviewed pair-level states rather than counted as positive-disclosure true negatives.
- **Abstentions:** explicit abstention decisions / reviewed requested document/field pairs; report reasons and abstentions on positive pairs separately. Empty/malformed/timeout response is failure, not a voluntary abstention. Multiple atomic claims can coexist with a pair-level abstention on another dimension; record both.
- **Field coverage:** gold-positive document/field pairs with at least one strictly correct recovered claim / all gold-positive pairs; separately report requested-pair response completeness and claim recall. One recovered row does not establish complete field coverage.
- **Latency/cost:** end-to-end elapsed seconds and billed cost per attempted original document, including retries and failures; report median/p95/range, totals and provider token/image counts when available. Failure documents remain in document and cost denominators. Separate labeling/verification cost and avoid invented pricing estimates as measured costs.

### B — Final verified pipeline, after verification

Use the same raw candidate IDs and independently frozen gold, linked to every deterministic check, reviewer correction/addition, rejection, admission and withheld status. Log verification time separately. Reviewers comparing outputs to gold cannot retroactively improve A.

- **Admitted correctness:** correct admitted claims / all admitted evaluable claims; unknown/unresolvable claims must be withheld, not admitted and excluded from the denominator. No admissions is undefined correctness, accompanied by zero yield. Report **false admissions** / all admissions and absolute material-error count, including incorrectly admitted negative/N/A decisions separately at pair level.
- **Human changes:** corrected raw candidates / reviewed raw candidates; human-added correct disclosures / final correct disclosures, and additions / gold disclosures; report counts and cause. Reviewer additions improve B coverage only and never become A true positives.
- **Review burden:** active human minutes per original document, including adjudication/correction and reviewers with zero-change outcomes; report elapsed versus summed reviewer minutes, total/median/range. Candidates requiring review / all deduplicated raw candidates is expected to be **100%** in this pilot because every admission needs original-source review. Report document review incidence too, including documents needing additions despite no model candidate.
- **Withheld/unresolved:** withheld raw candidates / all raw candidates; unresolved reviewed document/field pairs / all reviewed pairs. Record overlap and reasons. Track unresolved gold separately; do not inflate accuracy by withholding almost everything without reporting yield.
- **Yield:** admitted correct atomic claims / all attempted documents; also distinct verified P1 fields produced per document (maximum ten) and gold-positive document/field coverage after review. A document with many facility/year rows must not dominate the distinct-field yield.

Aggregate separately by field, source type, split, issuer, period and layout/control stratum. Report explicit TP/FP/FN and all denominator counts. Provide claim-micro results and unweighted document/issuer macro results; do not average away failed fields or pool development/regression as unseen evaluation. Report paired model differences only on identical frozen sources, representations and gold; no winner from incomparable subsets. Error/missing-output cases stay in the accounting. Small-sample intervals are descriptive; issuer dependence and a zero blind denominator preclude population reliability or unseen-generalization claims. Even zero material errors on a future small sample is feasibility evidence, not proof of production reliability.

## Separate readiness decisions and next gate

**Extraction feasibility:** D01–D27 pass original readability/hash and source-identity screening for independent labeling. They remain useful extraction benchmarks despite missing certified public dates. D28 is conditional for identity/period review. Native/D03 representation fidelity, independent gold and semantic scope still require the procedure above; positive candidate coverage alone is not complete-source review.

**Historical ML eligibility:** **zero admitted / 28 inventoried**. Public version-bound certification is unestablished for every candidate; 22 also have no retained hash-bound fetch time identified in this audit. Six retained fetch observations do not establish all strict public/capture/accepted-lineage gates. No historical decision cutoffs were chosen or tested, no accepted review/transformation lineage exists for DI-02, and historical company/sector/period/scope joins remain incomplete. Use `PUBLIC_AVAILABILITY_UNKNOWN` and retain other reasons rather than backdating from covers or current URLs.

For any future historical cutoff T, require certified version-specific public availability with timezone/precision (latest possible instant for date-only evidence), original capture and source acceptance no later than T, compatible company/period/scope, immutable source lineage, independently verified claims and accepted versioned transformation. Feature creation/review/materialization clocks remain explicit. Later capture does not become strict captured replay; any public-information reconstruction needs separate explicit policy approval and must remain distinct from operational replay. Original publication missing is acceptable for extraction labeling only. Do not loosen section 8, backfill Focus membership or join consolidated observations to standalone financials silently.

Unresolved blockers and exact next action:

1. Assign independent human reviewers/adjudicator; certify no existing DI-02 gold until source-first records and independence are established.
2. Review the 27 proposed sources, including visual tables/footnotes, to turn inventory candidates into complete-coverage labels. Resolve ICR basis, utilization statistic/window, date/version and transcript attribution/stance. The five credit fields have enough discovery cases to attempt this, not a guaranteed five accepted labels.
3. Find additional **already available** suitable original presentations and official transcripts under a separately bounded inventory extension for the five sparse fields. This 28-source audit is not evidence that the whole drive lacks them. A lack of confirmed positives cannot be repaired by cross-type substitution, repeated rows, fabricated labels or acquisition during this task.
4. Establish an independently custodied unseen-issuer holdout and freeze exact identities before further development. Until then, any labeled benchmark remains development/regression only. Unknown exposure cannot become blind through renaming.
5. After labeling, approve a concrete run manifest, compatible representations, model/configuration, all denominators, review workflow and cost/retry ceiling. A separately authorized regression-only credit feasibility run may then proceed if its label/control gates hold. **A full ten-field blind extraction experiment cannot yet be authorized merely by completing labels on this set.** It also needs coverage and holdout gaps closed. Historical ML remains a separate blocked decision.

Only this Markdown and its CSV were created/updated by DI-02. No production code, database, acquisition state, schedules, queues, checkpoints, membership, dictionary or original documents were modified. No acquisition, downloads, inference, paid calls, model scoring/training or independent labeling were run. **STOP: readiness audit complete.**
