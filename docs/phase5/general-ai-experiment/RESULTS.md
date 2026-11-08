# Executed Phase 5 architecture experiment

Recommendation: **retain Option A operationally**. This bounded run does not establish a replacement winner. Existing Phase 5 code remains unchanged; no modules were deleted and no model events were admitted to research features or historical ML.

## 1. Model/provider availability

All nine exact cloud IDs were present in the live catalog. Availability is distinct from successful inference. Prices, canonical model slugs, provider tags, supported parameters and observed failures are in [availability-final.json](../../../artifacts/phase5/general-ai-experiment/availability-final.json).

- `google/gemma-4-26b-a4b-it:free` via `google-ai-studio`: 0 valid / 0 returned document responses across all preserved stages; HTTP failures [429]; other failures [].
- `google/gemma-4-31b-it:free` via `google-ai-studio`: 0 valid / 0 returned document responses across all preserved stages; HTTP failures [429]; other failures [].
- `xiaomi/mimo-v2.6-flash` via `makora/fp8`: 32 valid / 32 returned document responses across all preserved stages; HTTP failures []; other failures [].
- `anthropic/claude-haiku-5.5` via `google-vertex/global`: 43 valid / 46 returned document responses across all preserved stages; HTTP failures []; other failures [].
- `deepseek/deepseek-v4.1-flash` via `open-inference/fp4`: 22 valid / 23 returned document responses across all preserved stages; HTTP failures []; other failures ['TimeoutError', 'stalled_provider_interrupted'].
- `mistralai/mistral-small-2603` via `mistral/zdr`: 1 valid / 1 returned document responses across all preserved stages; HTTP failures [429]; other failures [].
- `xiaomi/mimo-v2.6-pro` via `deepinfra/fp8`: 2 valid / 2 returned document responses across all preserved stages; HTTP failures [429]; other failures [].
- `google/gemini-3-flash-preview` via `google-vertex/global/flex`: 0 valid / 0 returned document responses across all preserved stages; HTTP failures [400]; other failures [].
- `google/gemini-3.8-flash` via `google-vertex/global/flex`: 0 valid / 0 returned document responses across all preserved stages; HTTP failures [400, 404]; other failures [].
- `typesafe/jev-1.13`: exact Decisions endpoint resolves and all six classifications completed; response identifies `typesafe/jev-1.13-20260917`. Absent from chat catalog; no silent substitution.
- `mlx-community/Qwen3.5-4B-MLX-4bit`: verified exact checkpoint loaded; first source-packet inference timed out with no completed output. See local measurements below; no substitute.

Free Gemma requests reached upstream rate limits (429). Earlier synthetic ZDR-only free probes returned 404; these are distinct routing observations. Mistral and MiMo Pro had partial smoke success plus 429s, so did not pass the three-family main-stage gate. The reviewed Gemini Vertex Flex routes returned invalid-argument failures even after the one recorded transport correction. These observations do not prove global model incompatibility across every possible provider.

## 2. Actual prompt and inputs

[Common task](../../../artifacts/phase5/general-ai-experiment/development/prompt.txt) and [protocol/schema](../../../scrapper/research/general_ai_experiment/protocol.py) are preserved. Native input uses OpenRouter chat content `file.file_data` with original PDF base64 and `file-parser.pdf.engine=native`. Page input uses full original native text and one selected original full-frame PNG, with page IDs, table/structural information and image omissions. No generated summaries, search or tool calls are supplied. Exact input hashes, requested schema/reasoning settings, provider and returned version/usage are recorded per response. Native engine actually used remains unestablished because the response does not expose sufficient evidence; native requests are labelled accordingly.

Selected page rendering is 144 DPI, maximum 1280 pixels per side, aspect ratio preserved, no crop. Main scope is 17 selected windows on five originals, not all page images. Cloud output cap is 8192 tokens. The local runner used 4096 tokens and an intended 120-second signal ceiling that overran; actual runtime and the subsequent watchdog correction are disclosed below. Structured transport corrections are common, with only necessary endpoint-format changes; original failed smoke attempts remain separate.

See [README](README.md) for reuse interfaces, sharing review, budget controls and reproducible commands. The [PDF documentation](https://openrouter.ai/docs/guides/overview/multimodal/pdfs) documents parser selection and the default OCR eight-image forwarding limitation. That default OCR route was not used.

## 3. Per-model document results

[Measured stage results](../../../artifacts/phase5/general-ai-experiment/measured-results.json) and [per-document tokens, runtime and billed cost](../../../artifacts/phase5/general-ai-experiment/per-document-results.json) retain successful and failed requests. Counts below are transport/source-verification measurements, **not financial-event accuracy**. Source rejection can reflect extraction layout, a compound numeric format, or unsupported semantics; it is not automatically hallucination.

- `anthropic/claude-haiku-5.5` / holdout-native-structured-v2: 3/3 schema-valid attempts; 27 proposed, 7 source-matched, 20 mechanically rejected; 43.93 seconds including failures; known billed $0.0192759.
- `anthropic/claude-haiku-5.5` / main-native-structured-v2: 5/5 schema-valid attempts; 60 proposed, 8 source-matched, 52 mechanically rejected; 85.22 seconds including failures; known billed $0.0234749.
- `anthropic/claude-haiku-5.5` / holdout-pages-structured-v2: 6/6 schema-valid attempts; 35 proposed, 10 source-matched, 25 mechanically rejected; 62.02 seconds including failures; known billed $0.0209489.
- `anthropic/claude-haiku-5.5` / main-pages-structured-v2: 17/17 schema-valid attempts; 84 proposed, 26 source-matched, 58 mechanically rejected; 138.43 seconds including failures; known billed $0.0409236.
- `deepseek/deepseek-v4.1-flash` / holdout-pages-structured-v2: 0/1 schema-valid attempts; 0 proposed, 0 source-matched, 0 mechanically rejected; 120.02 seconds including failures; known billed $0.
- `deepseek/deepseek-v4.1-flash` / main-pages-structured-v2: 16/17 schema-valid attempts; 105 proposed, 68 source-matched, 37 mechanically rejected; 1437.71 seconds including failures; known billed $0.02194371504.
- `mlx-community/Qwen3.5-4B-MLX-4bit` / local-smoke: 0/1 schema-valid attempts; 0 proposed, 0 source-matched, 0 mechanically rejected; 495.92 seconds including failures; known billed $0.
- `xiaomi/mimo-v2.6-flash` / holdout-pages-structured-v2: 6/6 schema-valid attempts; 40 proposed, 9 source-matched, 31 mechanically rejected; 96.08 seconds including failures; known billed $0.01765391.
- `xiaomi/mimo-v2.6-flash` / main-pages-structured-v2: 17/17 schema-valid attempts; 120 proposed, 29 source-matched, 91 mechanically rejected; 182.94 seconds including failures; known billed $0.035668096.

The incumbent standard and focused extractors ran on the same frozen originals. Development counts are 1122 standard and 2344 focused records across 145 pages; output volume is not accuracy. Native/reference coverage is in [native-holdout-reference-coverage.json](../../../artifacts/phase5/general-ai-experiment/native-holdout-reference-coverage.json). Both native profiles have some evidence on four of seven reference windows/facts, but this broad evidence-coverage check does not establish the extracted event, metric or relationship is correct and is not a precision score.

## 4. Financial accuracy and hallucinations

**Complete supported-event precision/recall, event-category precision/recall, numeric accuracy, unit correctness, company/segment/facility correctness, period/actual-versus-forecast correctness, chart relationship accuracy and hallucination rates are not established.** There is no exhaustive independently adjudicated gold set. These metrics are null in the machine-readable results rather than invented from quotation matches. Schema rates, abstentions, latency, tokens, usage costs and mechanical rejection reasons are measured separately. No composite score or direct-document accuracy ranking is claimed.

Source evidence, semantic status and historical eligibility remain separate. All proposed events require semantic review and have historical ML eligibility false. Matching a quote does not prove a facility relationship, rating action, metric association or forecast interpretation. Original inconsistent statements are preserved; no model repairs source values.

## 5. Jev secondary classification benchmark

[Classification results](../../../artifacts/phase5/general-ai-experiment/classification-summary.json) compare six identical original source-verified passages. Jev uses proper `state` and typed `choice` questions at `/api/v1/systemone`, as documented in [the Decisions reference](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request). General models receive the same passages and classification definitions. No PDF is sent to Jev. Existing native categories are cross-walked only from matching primary evidence; its direction field is unsupported, not invented.

All four classifiers agreed on six of six categories. Directions agreed on five of six: static “adequate liquidity” was classified not stated by Jev, neutral by MiMo, and positive by Haiku/DeepSeek. This is agreement, not accuracy or calibrated confidence. There is no demonstrated incremental accuracy benefit justifying a Jev dependency.

- `anthropic/claude-haiku-5.5`: 6/6 valid; 8.43 seconds total; $0.0006227 total.
- `deepseek/deepseek-v4.1-flash`: 6/6 valid; 23.47 seconds total; $0.00012911652 total.
- `typesafe/jev-1.13`: 6/6 valid; 6.05 seconds total; $0.000189630 total.
- `xiaomi/mimo-v2.6-flash`: 6/6 valid; 19.66 seconds total; $0.00035224 total.

## 6. Source-linked correct and incorrect examples

[Open source-review examples](../../../artifacts/phase5/general-ai-experiment/source-linked-examples.html) or inspect [the full qualified events](../../../artifacts/phase5/general-ai-experiment/source-linked-examples.json). Findings are Codex provisional judgments, not independent human ground truth.

- Correct visual quotation rejected: Amines & Plasticizers physical PDF page 17 says a 2.6 MW captive solar plant was commissioned. Flat native extraction interleaves a side card within the sentence, causing exact-span failure. Do not count this as fabrication.
- Correct table relationship requiring region review: Niva Bupa page 1 distinguishes issuer AAA Stable reaffirmed from 500-crore subdebt AA+ Stable assigned. A reconstructed row can fail flat text matching.
- Provisional wrong event category: MiMo labelled a market-share disclosure as orders.
- Provisional attribution/category error: MiMo labelled an ICRA profitability expectation as management guidance.
- Provisional wrong event category: Haiku labelled a lower-carbon/cost-efficiency statement as liquidity/leverage/working capital.
- Useful source discovery: Uniparts annual Mexico business award of $6.5 million, with future supply timing. Annual award value must remain distinct from total order book.

## 7. Actual cost and ceiling

Known response-reported OpenRouter inference cost: **$0.28011474972**. Actual billing is complete: **False**. Actual plus all unresolved maximum reservations: **$4.17402318876**, against the $10 ceiling. Failure/timeout charges are not silently assumed zero. [Ledger](../../../artifacts/phase5/general-ai-experiment/spend-ledger.json) is authoritative for conservative request admission.

No paid OCR parsing was enabled; local preprocessing has no OpenRouter fee. Electricity, internet and hardware depreciation are unmetered, so total economic preprocessing/local cost is unknown. No key/account limit was altered.

## 8. MacBook runtime and memory

Host is Apple Silicon arm64 macOS with 24 GB unified memory. Development canonical preparation/rendering took 13.209 seconds and 556,646,400 bytes process peak RSS. Isolated runtime pins are in [requirements.runtime.txt](requirements.runtime.txt). See local status/results in [availability-final.json](../../../artifacts/phase5/general-ai-experiment/availability-final.json) and [per-document measurements](../../../artifacts/phase5/general-ai-experiment/per-document-results.json). Failed download attempts are retained. Only the exact small requested checkpoint is eligible; no massive substitute was downloaded.

The exact Qwen checkpoint completed download and matched its published weight SHA-256. It loaded but produced no completed output on the first full rating packet. MLX peak allocation was 4,331,380,386 bytes (4.03 GiB); process peak RSS was 1,722,417,152 bytes (1.60 GiB). Observed request elapsed time was approximately 495.92 seconds: the initial 120-second Python alarm was delayed inside native MLX work. This was a timeout-control failure, not a successful bounded 120-second trial. A separate 125-second whole-worker subprocess watchdog was added afterward; it has not been validated on another model inference run. No reduced-context rerun is pooled into equal-input results. The other local smoke families and local-only originals have no completed local results.

## 9. Fresh holdout and repeats

Three original local-archive PDFs were frozen before output inspection: Uniparts Q2 FY25 transcript, Amines & Plasticizers Q1 FY26 presentation, ICRA Niva Bupa. They contain 67 pages. References cover seven provisional facts in six selected windows. Hash exclusion and content-overlap audits exclude prior diagnostics; this is operationally fresh, not guaranteed absent from model training. A restricted candidate presentation was excluded before transmission. No rating-agency files were retrieved again.

The source/figure discovery diagnostic is deliberately limited. Haiku and MiMo page inputs each mechanically recover five of seven frozen facts; two are layout-related false negatives described above. Haiku original-PDF input mechanically recovers one of seven under this exact-span method. DeepSeek returned no valid holdout document response before timeout, so holdout accuracy is unmeasurable. These are not 71%, 14% or 0% financial-accuracy scores. See [holdout-provisional-agreement.json](../../../artifacts/phase5/general-ai-experiment/holdout-provisional-agreement.json). The verifier and references were not tuned after holdout outputs.

Three repeat rounds are preserved in [repeatability-results.json](../../../artifacts/phase5/general-ai-experiment/repeatability-results.json). On the same transcript-page input, Haiku emitted 5/5/7 events and MiMo 8/10/10. On the rating page, Haiku emitted 6/5/7 and MiMo 7/7/7; equal counts do not imply equal fields. DeepSeek emitted 6/6/6, but had two distinct payload hashes because live price filters changed; only matching-hash pairs qualify as identical-input repeats. Exact field-set and category/page/quote-set agreement are reported without treating variation as error.

## 10. Architecture decision

Option A stays operational because independently checked superiority is unproven. This does not establish the incumbent as an accuracy winner. Option B remains a useful research candidate: source-grounded general proposals found complex disclosures cheaply, but semantic category errors and verification gaps remain. Option C has no demonstrated incremental benefit sufficient to justify additional classification/routing complexity. Do not assume hybrid wins.

The next deciding experiment needs exhaustive independently reviewed event labels and a new frozen holdout after any verifier/prompt changes. These holdout documents are now diagnostics and cannot become unseen again. Region-aware evidence verification is a research need, not permission to weaken source checks.

## 11. Retirement analysis only

[Module-level inventory](module-inventory.json) records retention/narrower reuse/test tooling and static direct importers. **No module is currently eligible for retirement.** Retain acquisition, immutable archive, source evidence, provenance, verifier, temporal integrity and historical ML restrictions. Dynamic/external dependency absence has not been established.

Conditional future plan: after demonstrated independently checked unseen superiority, keep the incumbent selectable, shadow-replay frozen fixtures, test downstream consumers and historical admission, then remove only demonstrably redundant interpretation responsibilities in small reversible changes with rollback. No deletion or production migration was performed.

Validation: 83 targeted tests passed. Frozen development/holdout inputs, holdout references/configuration and pre-holdout verifier SHA were rechecked unchanged. Existing `scrapper/research/document_intelligence/` has no diff from this experiment. API keys, runtime and checkpoint weights are not committed.

Final key-level usage increase: **$0.286970434**, versus **$0.28011474972** in completed response records; unallocated difference **$0.00685568428**. Key usage can include concurrent activity, so it is not a per-failure allocation. No account/key limit was changed. See [cost-reconciliation.json](../../../artifacts/phase5/general-ai-experiment/cost-reconciliation.json).
