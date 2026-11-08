# DI-04 — First real annotation trial

2026-10-27 · **AWAITING_HUMAN_LABELING** · D04 only · REGRESSION ONLY

Prepared forms exist; actual human submissions: **0**; adjudicated reference labels: **0**. No human annotation validation or protocol usability pass is claimed. Work stops pending completed human-reviewed CSVs.

## Source identity

D04 is CARE Ratings, GNA Axles, 14 June 2024. Its locator and stable ID match the D04 row in [CREDIT_SOURCE_SET_V1.csv](../CREDIT_SOURCE_SET_V1.csv):

- Original: `sample_files-2/in_q1_focus_list/credit_ratings/CARE_GNA_Axles_2024-06-14.pdf` (relative to repository root).
- Stable document ID: `DI02-f8a52347b31f`; audit alias: `D04`.
- Original SHA256, recomputed from original bytes: `f8a52347b31fa3efa6cfdb78c85157c6c037692712b7c98ed242c85fdd1cc6df`.
- Byte size: **156944**, matching the catalog. No original bytes were changed.

This establishes byte identity with the cataloged document. The human must still verify the displayed issuer, agency, date, rated legal entity and source evidence in the complete original PDF.

## Prepared forms and human handoff

[D04_COVERAGE_TEMPLATE_V1.csv](D04_COVERAGE_TEMPLATE_V1.csv) contains the existing five D04 rows, preserving the original headers and pair IDs. Only existing pair/document/hash/field metadata is populated. All reviewer, evidence, applicability, value, qualification and decision cells remain blank. [D04_CLAIM_TEMPLATE_V1.csv](D04_CLAIM_TEMPLATE_V1.csv) retains the existing claim header with no claim rows. These are prepared forms, not submissions or labels.

The established [labeling guide](../CREDIT_LABELING_GUIDE_V1.md) and [feature dictionary](../../FEATURE_DICTIONARY_V1.md), especially sections 3, 4A, 5, 7 and 8, govern review. Protocol: `DI03-credit-labeling/1.0.0`; dictionary: `document-intelligence-features/1.0.0-draft`. The D04-only scope replaces the guide's instructions to review all 12 documents; definitions and existing schemas remain unchanged.

1. A human coordinator assigns the available human reviewer(s); none are assigned or presumed available by this report. Give each person a private copy of these two forms, the original PDF and the normative guide/dictionary definitions. Withhold historical evaluation links/results, model answers, candidate page hints and other reviewers' answers. Do not follow the dictionary's historical evaluation links during independent discovery. No AI agent may fill in for a human reviewer.
2. Import every CSV column as text and retain UTF-8 CSV headers. Save private working copies as `<reviewer>_D04_coverage_v1.csv` and `<reviewer>_D04_claims_v1.csv`. Preserve all five seeded identity columns. Use the guide's reviewer-specific coverage and claim ID conventions and record protocol/dictionary versions.
3. Independently inspect the complete original visually, first physical page through last, including tables, annexures, headers, footnotes and continuations. Verify document/legal-entity identity with source evidence. Keyword search alone cannot establish absence. Record prior exposure and an honest human independence attestation.
4. Complete one coverage decision for each of `cr_long_term_rating`, `cr_rating_outlook`, `cr_liquidity_assessment`, `cr_interest_coverage_ratio`, and `cr_working_capital_limit_utilization_pct`. Record applicability/evidence, value state, process reasons, coverage status, verification decision and field sign-off separately. Enumerate inspected physical pages and gaps; a positive discovery alone does not establish complete coverage.
5. Add an atomic `observation` row for every distinct disclosed facility, period, scope, basis or statistic, linked to its coverage row. Keep repeated mentions linked as duplicates and incompatible same-grain alternatives as conflicts. Use linked `support` rows for material liquidity rationale and other qualifications; use `control` rows only for confusable evidence actually discovered. List observations/support in `claim_ids` and controls in `control_ids`. Retain full evidence text, physical page references, table/row/column headers, footnotes and continuations. Record dates, literal values, units, periods, scopes, conditions and restrictions only when supported. Leave material unknowns unresolved with reasons; never guess a scalar, mapping, formula, period or denominator.
6. Use exact guide vocabularies: value states `DISCLOSED`, `NOT_DISCLOSED`, `NOT_APPLICABLE`, `UNRESOLVED`, `CONFLICTING`; coverage `NOT_ASSESSED`, `PARTIAL`, `COMPLETE_RELEVANT_SOURCE_REVIEW`; verification `PENDING`, `VERIFIED_DISCLOSURE`, `NEEDS_REVIEW`, `REJECTED`. Absence requires complete relevant-source review; N/A requires evidence and approval. Blank does not mean absent. Use the guide's process-reason and individual-check vocabularies.
7. Record actual UTC start/end times and active minutes, excluding breaks. For this trial, enter each field's active minutes in `active_review_minutes`; allocate shared whole-document reading once across the five rows and explain the allocation in `coverage_signoff`, so their sum is the document total. Do not copy the full document total into all five rows. Include human feedback on confusing/unnecessary columns and whether qualifications, scopes and periods could be recorded without guessing, in a signed submission note with total active minutes. No time is estimated by the coordinator.
8. Submit the two CSVs and timing/feedback note privately. Store received originals under `first-trial/submissions/<reviewer>/` with exact bytes preserved; record filenames, receipt time and full SHA256 separately. Do not put completed answers into the pristine templates. If two people review, seal both submissions before revealing either; use private access-controlled handoff rather than a shared answer workbook. If only one submits, mark the receipt and summaries **PRELIMINARY SINGLE-REVIEW — REGRESSION ONLY**, non-adjudicated; do not rewrite the submitted CSV to add that label.

## Submissions, validation and adjudication

No completed human files were supplied in this task. The labeling directory contains only the DI-03 guide/catalog/templates; a filename search in `docs/`, `artifacts/` and `sample_files-2/` found no applicable D04 human submissions. Unrelated historical review artifacts were not imported or treated as labels. External reviewer files, if any, have not been supplied. No actual submission validation, comparison, adjudication or accepted-answer freeze has occurred.

After receipt, validate CSV syntax/row width and required headers against the unchanged V1 schemas; D04 IDs/hash and the five field pairs; reviewer/version/attestation/sign-off and timing metadata; required values by state; exact status vocabularies; unique record IDs and all coverage, parent, support, duplicate, contradiction and control links. Check evidence locators against the original, quoted context/headers/continuations, completeness declarations and page logs. Report missing evidence, omissions, broken links and material unknowns separately; retain submissions unmodified and request versioned human corrections. Do not repair source judgments automatically.

With two submissions, compare claims and omissions by field and observation dimensions, including disagreements in qualifications and coverage. Use the existing [adjudication template](../CREDIT_ADJUDICATION_TEMPLATE_V1.csv); a human adjudicator must reopen the original and record evidence, decision, reasons and unresolved exclusions for all five pairs. Preserve both submissions and separate adjudication/correction records. One submission remains preliminary calibration evidence. No reference answers are frozen now.

## Usability evidence and completion

- Actual human review time: **not measured**.
- Identified human source-supported claims: **not measured**; zero submitted claim rows, not a finding of no source claims.
- Unresolved human claims: **not measured**; no claim inventory exists.
- Fields with completed human review coverage: **0/5**.
- Confusing/unnecessary columns and ability to capture qualifications/scopes/periods: **not assessed by a human**. No evidence supports removing columns yet; no schema simplification is made. Only the essential D04 row filter and timing instructions were applied.

Completion gates:

1. Completed human source annotation: **No — awaiting submission**.
2. Independently reviewed labels: **No — no human submissions**.
3. Resolved material disagreements: **Not assessed — no comparison/adjudication**.
4. Frozen accepted reference answers: **No**.
5. Sufficient evidence to begin a one-document Qwen regression test: **No**. Human evidence is missing. The existing DI-03 run gate also requires five positive documents and two accepted field-relevant controls per tested field; a one-document calibration cannot satisfy that broader gate. This trial changes no thresholds or dictionary rules and authorizes no run.

**Next action:** a real human independently reviews the original and returns the two completed D04 CSVs with actual timing and feedback. Stop here. No inference, modeling, production/database changes or expansion to the other reports was performed.
