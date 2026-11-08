# Phase 5 accuracy corrections — measured offline implementation

The extractor is materially better on the original evaluation, but **production integration, historical backtesting and ML admission remain blocked**. The independent Himadri holdout still misses all eight numeric references. Historical public timestamps remain uncertified.

Open the [evidence-linked report](../../artifacts/phase5/corrections/v2/report.html). The report includes exact TP/FP/FN counts, attribution, independent versus diagnostic holdout results, original-source examples, provenance and remaining errors. Source annotations remain agent source-reviewed and await independent human adjudication.

The implemented changes stay within `scrapper/research/document_intelligence/`:

- `evidence.py`, `extract.py`: reject empty evidence, quarantine invalid records and unsupported table elements, and preserve valid siblings. The actual Tata Power page-10 failure fixture is covered. No numeric evidence is synthesized and validation remains strict.
- `context.py`, `extract.py`: classify separate claims, remove noun `project` from blanket guidance rules, distinguish conditional forecasts, retain withdrawal evidence and reject analyst numbers as verified management guidance. Favorable working-capital utilization is not a risk warning.
- `schema.py`, `context.py`: add a separate `as_of_date`; reporting periods, target horizons, effective dates and source timestamps remain distinct. Quarter, half-year, nine-month, annual and explicit duration contexts are supported. Agency HTML cannot inherit periods across unrelated claims.
- `extract.py`: read multi-row native financial headers and comparative columns; preserve entity rows, subsidiary scope, restatement citations and separate flow aggregation from ratio levels. Missing native unit declarations remain ambiguous.
- `transcripts.py`: reuse the official segmenter, add timestamped speaker headings, exact whitespace-bearing span recovery, explicit roster/questioner identity evidence, and local context boundaries.
- `ratings.py`: reuse the credit-rating normalizer, interpret facility-specific native agency tables, split explicitly scoped LT/ST actions, retain prior grades where stated, and extract issuer upgrade/withdrawal. Unknown effective dates and facility identities remain explicit. Negative grade suffixes and agency-prefix inheritance within a compound rating cell have generic regression coverage.
- `correction_evaluation.py`: reuse the frozen scoring policy and independent original-byte annotation checks; load the preserved original extractor to reproduce its baseline exactly. No production parser, DB migration, worker or schedule was modified.

## Final measurements

On the unchanged original labels, financial metrics improve from 10/49 to 27/49 (27/27 emitted precision), order books 3/11 to 6/11 (6/6), guidance 1/4 to 4/4 (4/4), capex/capacity 1/4 to 4/4 (4/4), ratings 0/9 to 9/9 recall (9/11 precision), and liquidity/leverage 1/16 to 11/16 (11/14 precision).

Reporting-period correctness rises 2/73 → 34/73, entity scope 0/40 → 22/40, and actual/projected basis 14/83 → 53/83. There are zero emitted period errors under the unchanged scorer, but 39 periods are missing. All four labeled target horizons match. The typed-date supplemental assessment is 35/72 and does not replace the frozen score.

The original document failure rate drops 1/11 → 0/11. Citation validity is 147/147 in original annotated windows, and 1018/1018 for all emitted usable original-set statements. All six negative controls pass. There are 45 newly correct core references and no lost baseline TP. There are two new measured rating FP (the combined CARE facility components omitted from the frozen reference set), three remaining liquidity FP, and a speaker-label mismatch where the source supports analyst Shubhi Gupta but the frozen label remains UNKNOWN. Label gaps are not silently corrected.

The LTTS/HEG source-first holdout initially scores financial 9/12, guidance 1/1 and ratings 1/5 (rating precision 1/3). After the general grade parser fix, diagnostic reruns score 9/12, 1/1 and 5/5. **These reruns are not unseen validation.** The subsequent independent Himadri holdout has 0/7 financial metrics and 0/1 capex. It completes extraction with valid citations but has unsupported double-dash management headings and spaced numeric/fiscal-year tokens. Its pre-feedback result is retained, and the final context safety patch was rerun diagnostically with the same zero numeric coverage. Holdout entity-scope accuracy is not established because these holdout references have no independently established scope labels.

Final focused verification: **123 tests pass**, including 44 correction cases, 57 existing Phase 5 contracts and 22 reused D03/D04/D05 checks. Tests use disposable DB/document roots. [JUnit evidence](../../artifacts/phase5/corrections/v2/validation.xml).

## Provenance and gates

The [bounded audit](../../artifacts/phase5/corrections/v2/publication-provenance.json) read only an existing isolated Oct 8 DB snapshot (`mode=ro`, `immutable=1`, `query_only=ON`). All 14 evaluated versions match content identities and have exact-hash successful acquisition receipts. Those timestamps establish acquisition observations; none certifies original public availability. **Historical public certification: 0/14**, including 0/11 original documents. Receipt IDs, URLs, metadata, hashes and acquisition times are retained. HTTP Last-Modified, cover/report dates, reporting periods and source discovery labels are not promoted into publication certification. Ingestion is not backdated.

All research features keep `ml_admission=BLOCKED_PENDING_PUBLIC_PIT_AND_INDEPENDENT_LABEL_REVIEW`. Effective-date/current-rating gates remain unchanged. A verified report event with unknown effectiveness is not a certified current rating or an admitted ML feature.

## Remaining high-impact work and exact next step

The next bounded offline patch is double-dash/CEO roster grammar and native split-token comparison recovery, with the frozen Himadri document as a regression. Also address Net Income/Net Income margin aliases, ordered multi-value financial comparisons, explicit period/entity anchoring, and duplicate qualitative agency disclosures. Freeze another previously unseen company/time holdout before predictions, independently adjudicate the annotation gaps and measure those fixes.

Tata Power page 35 has the visually explicit “Fig in ₹ Cr” declaration, but the native canonical text omits it. The corrected native table retains Q4FY21/Q4FY20, subsidiary identities and restated comparative evidence while quarantining currency values. A separately measured OCR test on this image-only unit is justified. Vision may help map native slide charts; no model should guess chart values or dates. Speaker grammar, native comparison syntax and publication receipts need deterministic/source-provenance work, not a model. No new OCR or LLM integration ran.

After semantic/scope/period gates pass, the exact integration rehearsal is an **8–12 document shadow export into an isolated copy**, with 50 MiB/100-page ceilings, exact hashes/citations, timeout/quarantine behavior and restart/idempotence measurements. Do not start a production worker, scheduler, bulk job or ML materialization from the present results. Public-PIT certification and independent source accuracy remain mandatory per feature. The October 14 Phase 4 follow-up was not touched.

## Reproduction

Use a fresh output path. Baseline results and all v1 annotations remain immutable.

```sh
scrapper/.venv/bin/python -m scrapper.research.document_intelligence.correction_evaluation \
  --baseline artifacts/phase5/quality-evaluation/v1 \
  --holdout artifacts/phase5/corrections/v2/holdout \
  --frozen-source artifacts/phase5/corrections/v2/baseline-source \
  --output /private/tmp/phase5-correction-reproduction
```

Use `artifacts/phase5/corrections/v2/final-holdout` for the Himadri diagnostic replay. Initial unseen results remain in `first-holdout-results/` and `final-independent-results/`; current parser comparisons are in `v2.0.2-results/` and `final-diagnostic-results/`.

```sh
env TRADING_AI_ENV=test \
  SCRAPER_STORE_PATH=/private/tmp/trading-ai-phase5-corrections-test-store \
  SCRAPER_DOCUMENT_STORE_PATH=/private/tmp/trading-ai-phase5-corrections-test-docs \
  scrapper/.venv/bin/python -m pytest \
  scrapper/tests/test_phase5_accuracy_corrections.py \
  scrapper/tests/test_phase5_document_intelligence.py \
  scrapper/tests/test_phase5_quality_evaluation.py \
  scrapper/tests/test_d03_document_extraction.py \
  scrapper/tests/test_d04_credit_rating_normalization.py \
  scrapper/tests/test_d05_official_transcript.py -q
```

107 protected/baseline source files were checked before and after evaluation. Only four authorized extractor files differ; all operational files match. Original source files and frozen reference hashes are rechecked by the runner. No production DB connection or write, new source request, training job, bulk processing or Phase 3/4 schedule change occurred.
