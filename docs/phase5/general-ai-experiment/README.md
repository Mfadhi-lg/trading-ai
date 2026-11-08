# Phase 5 general AI architecture experiment

The experiment ran against original local documents using the existing OpenRouter key. The implementation is isolated under `scrapper/research/general_ai_experiment/`; it does not replace or disable existing Phase 5 code. No production feature, ML admission, scheduler, Phase 3 or Phase 4 changes were made.

Early setup snapshots (`cloud-blocker.json`, `key-cap-check.json`, download-failure records) are superseded by the final report and budget policy; they remain audit history. The existing key is intentionally uncapped.

Read [RESULTS.md](RESULTS.md) for the executed comparison and limitations. Machine-readable records live in [the experiment artifacts](../../../artifacts/phase5/general-ai-experiment/). Original responses and earlier transport failures are retained, including rejected proposals and billing reservations.

## Existing interfaces reused

`documents` canonical PDF extraction provides immutable native page text, element/table coordinates and page mapping. `document_intelligence.extract.extract_intelligence(DocumentContext, extraction, focus_disclosures=...)` supplies both incumbent baselines on the same frozen originals. `schema.ResearchEvidence` and `evidence.text_evidence/validate_evidence` supply existing evidence identities, original spans and numeric validation. `source_review.source_units` and its renderer provide source locators and review links. The experiment reuses rendering rather than scraping or creating model-generated summaries.

`transcripts.parse/roster/exact_body` separate management statements, analyst questions and original speaker spans. `ratings.events` retains facility-aware rating actions; `charts`, `native_financial_tables` and `numbers` retain structured metric, table and scale interpretation. `claim_candidates.build_packet/deterministic_proposals/qualify` are existing proposal/qualification seams. The new general event taxonomy is broader, so the adapter produces separate review-only event records rather than changing those existing contracts.

`temporal.available_at/eligible_at` and `research_features.public_pit_readiness/qualified_fact` retain public-availability and historical admission restrictions. A report date is not exchange publication evidence. None of the new proposals has historical ML eligibility, even when its literal quotation matches. Entity/facility relationships, action interpretation, period, business direction and other unproven semantics remain provisional. No LLM serves as definitive verifier.

## Reproduce and inspect

Run from the repository root. Only `OPENROUTER_API_KEY` is read from the environment or the existing `.env` assignment; dotenv content is never executed, printed or embedded in artifacts.

```sh
scrapper/.venv/bin/python -m scrapper.research.general_ai_experiment.runner catalog
scrapper/.venv/bin/python -m scrapper.research.general_ai_experiment.runner cloud --stage smoke --strategy pages --limit 3
scrapper/.venv/bin/python -m scrapper.research.general_ai_experiment.runner cloud --stage main --strategy pages --limit 100
scrapper/.venv/bin/python -m scrapper.research.general_ai_experiment.runner cloud --stage main --strategy native --limit 5
scrapper/.venv/bin/python -m scrapper.research.general_ai_experiment.runner cloud --stage holdout --strategy pages --limit 18
scrapper/.venv/bin/python -m scrapper.research.general_ai_experiment.classification
scrapper/.venv/bin/python -m scrapper.research.general_ai_experiment.final_report
```

The runtime/checkpoint preparation is separate from inference. The exact 4B checkpoint is pinned to revision `32f3e8ecf65426fc3306969496342d504bfa13f3`. The local runner is offline, sequential, limits MLX allocations/cache and requests a 120-second signal alarm plus a separate 125-second whole-worker watchdog. The initial signal-only trial overran and failed; the watchdog was added afterward and has not been revalidated on inference. No larger checkpoint is a substitute.

Execution skips preserved requests, including failed or uncertain-billed ones. The runner only permits one explicitly requested compatibility retry for earlier HTTP 400/404 transport failures. It does not retry rate limits/timeouts automatically. Completed costs and unresolved worst-case reservations share one persistent $10 ledger and an exclusive execution lock. The user's key is intentionally uncapped; the experiment does not require or alter account/key limits. Every paid request rechecks live endpoint capabilities/prices, pins its reviewed provider, disables fallback and reserves a conservative entire-context maximum charge. Unknown additional fees block execution.

## Source rights and input controls

All 11 requested development originals are identified by SHA-256 and true content type, with 145 pages canonically extracted. Four issuer Everest Kanto PDFs and the ICRA Venus Jewel report were approved for this private research transmission after reviewing notices. ICRA expressly allows use with acknowledgement; attribution is retained. Six other rating reports remain local-only where external-processing rights are unestablished; CRISIL expressly restricts redistribution without prior consent. There was no original-source re-scraping.

Paid routing requests require data collection denied and ZDR. Free Gemma probes require data collection denied; public-source routing does not require ZDR, while account-global policy remains unchanged. Observed policy mismatch and upstream rate-limit failures are distinct. Account-level settings are not weakened. No private portfolio data or unrelated files are sent.

Strategy A sends the original PDF via `file.file_data` and explicitly requests the native file parser. Actual engine/provider preprocessing is not independently exposed by successful responses and is marked unestablished; no silent OCR fallback or text-only substitution is permitted. The OpenRouter default Mistral OCR pipeline documents an eight-forwarded-image limit, so it is not used for a complete visual PDF benchmark.

Strategy B gives original native text from every page, original page/document identifiers, existing selected-page structure/table text, and one selected full-frame PNG per window. Rendering is 144 DPI, aspect ratio preserved, maximum 1280 by 1280 pixels, with no crops or generated summaries. Main comparison covers 17 frozen selected windows, **not every page image**. Each packet lists omitted images. Full native PDF and selected-page comparisons are separate experiments. Local output is capped at 4096 versus cloud 8192 tokens for memory/runtime control; disclose that difference rather than claiming fully identical inference settings.

The common task and schema are in `protocol.py`, with the exact input prompt in each frozen dataset's `prompt.txt`. The structured-v2 transport revision uses equivalent schema enforcement where supported, JSON object format otherwise, and only advertised reasoning/temperature controls. Earlier unstructured smoke outputs remain separate. No per-candidate financial prompt tuning or JSON repair is performed.

## Evaluation boundaries

The source-specific checklist covers all 13 requested challenges and links original pages. Those notes are provisional, not an independent answer key, and never enter model input. Fresh local-archive originals, references, page selection, configuration and verifier hashes were frozen before holdout outputs. A restricted candidate presentation was excluded before transmission and replaced before the final freeze. Prior diagnostics were excluded by hash and content-overlap audit; this is operationally unseen to this experiment, not a claim about model training data.

Seven holdout facts in six windows have provisional source-reviewed labels. Their mechanical quote/figure discovery measure is a lower-bound diagnostic, not complete supported-event accuracy. There is no exhaustive independently adjudicated event set. Precision/recall, numeric/unit/entity/time/chart correctness and hallucination rates therefore remain unestablished rather than being inferred from quotation matches. Literal rejection can be a layout false negative. Preserved source-linked examples distinguish that from provisional category errors.

Jev uses exact `typesafe/jev-1.13` at `/api/v1/systemone`, with `state` and typed `choice` questions. The exact endpoint resolves despite absence from the chat catalog; the response returns its dated canonical version. Six identical verified passages go to Jev and three general classifiers, with the existing deterministic evidence/category crosswalk included. No direction rule was invented for the incumbent. Jev is excluded from document-reading comparisons.

Repeatability compares three replicates per frozen page, input hashes and exact event/evidence sets. Disagreement is not automatically an error; temperature zero is not assumed deterministic. No single composite score hides failures.

## Retention decision

[module-inventory.json](module-inventory.json) inventories each Phase 5 module and static direct importers. It distinguishes retention, narrower reuse and research tooling; dynamic/external consumers have not been ruled out. No module is currently authorized for retirement. Preserve original acquisition/archive, canonical evidence, verification, provenance, temporal rules and ML admission restrictions.

The current recommendation is **Option A operationally** because replacement superiority has not been demonstrated. This is not a claim that the incumbent is the most accurate reader. General-model-first and hybrid remain research alternatives. Only after exhaustive independently checked fresh-source evaluation could a reversible retirement plan be proposed: retain incumbent behind a feature flag, replay frozen fixtures in shadow mode, validate downstream consumers, and roll back by selecting the incumbent. No deletion or rewrite is proposed now.
