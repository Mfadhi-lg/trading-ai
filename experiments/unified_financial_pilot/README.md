# Unified financial document pilot V0.1

User-revised scope: **four companies, 12 original local PDFs**, two from each authoritative membership manifest. This isolated experiment writes only its own artifact tree. No database, production worker, investment scoring or RunPod integration is added.

## Discovery and reuse

Repository root: `/Users/mohamedfadhi/Fadhi/trading-ai`. Read root README, `docs/STATUS.md`, `docs/phase5/README.md`, D03 parser and tests, existing research source validation, native layout, V1/V2 report code, Qwen table experiment and V10 Docling experiment. The checkout already contains extensive staged/unstaged unrelated work; it was preserved.

Reused at runtime: `scrapper.research.document_intelligence.offline.extract_local` and `deny_network`, which invoke the unchanged D03 PDF parser with OCR disabled. D03 provides ordered page text, original table cells and image inventory. Existing V1/V2, Docling and Qwen artifacts informed the admission boundaries but are never imported as ground truth. No Docling/model processing runs automatically on every page. Existing pdfplumber, pypdf, certifi, Poppler and Python standard library suffice; no dependency installation.

Membership authority: `sample_files-2/{in_q1_focus_list,not_in_q1_focus_list}/manifest.json`, byte hashes pinned. The supplied Focus text is recorded in those manifests. Original archive: `/Volumes/My Passport/passport/trading-ai/artifact_content`, addressed by full SHA256. Additional source identity comes from retained `artifacts/document-acquisition/**/artifact-*.json` exports. PDFs are read and rehashed without modification; no replacements downloaded. Cover metadata annotations in `document_metadata.json` are source metadata only, checked against first-two-page quotes, never company-specific extraction rules.

Development companies: **VIMTALABS, EKC**. Frozen evaluation companies: **IDFCFIRSTB, RECLTD**. All have a rating PDF, investor presentation PDF and transcript PDF. Dates differ: Vimta/IDFC presentations and transcripts are Q1 FY27; EKC is Q4 FY25; REC is Q3 FY25. Credit reports have their own dates and historical periods. Vimta presentation publication day remains unknown. Issuer filing-letter dates, call dates, agency report dates and independently established publication times are separate. Prior research hash matches are retained; none is advertised as genuinely unseen.

## Run offline

Use the repository's existing `scrapper/.venv/bin/python` from the project root:

```sh
scrapper/.venv/bin/python -m experiments.unified_financial_pilot.discover --output /private/tmp/new-unified-discovery
scrapper/.venv/bin/python -m experiments.unified_financial_pilot.runner dry-run --manifest artifacts/unified-financial-pilot-v0.1/pilot_manifest.json --output /private/tmp/unified-dry-run.json
scrapper/.venv/bin/python -m experiments.unified_financial_pilot.runner offline --manifest artifacts/unified-financial-pilot-v0.1/pilot_manifest.json --output artifacts/unified-financial-pilot-v0.1/new-offline-run
scrapper/.venv/bin/python -m unittest experiments.unified_financial_pilot.test_pilot -v
```

The offline runner processes development documents; evaluation gets explicit unprocessed dossiers. Output directories must be new and under the pilot artifact tree. Source windows are generic topic retrieval with surrounding lines, **not scalar financial interpretation**. Native table detection can oversegment decorative layouts and miss borderless tables. `charts_detected=null` means unknown. A completed page is not a completed disclosure review.

## API configuration and smoke

Inspection found `OPENROUTER_API_KEY` configured in repository `.env`; endpoint/model settings were not present there. Prior experiment code uses OpenRouter's OpenAI-compatible `https://openrouter.ai/api/v1` endpoint. This pilot requires explicit endpoint/model environment variables and never loads the full `.env` or prints credentials. Supply the key via `PILOT_API_KEY` or `OPENROUTER_API_KEY` in the process environment. The actual authorized smoke loaded just that existing key in memory.

Exact model and capabilities were verified through the provider's free catalog, retained as `provider_verification.json`. Refresh within 24 hours:

```sh
scrapper/.venv/bin/python -m experiments.unified_financial_pilot.runner verify --catalog-url https://openrouter.ai/api/v1/models/qwen/qwen3.8-27b/endpoints --endpoint https://openrouter.ai/api/v1 --model qwen/qwen3.8-27b --output artifacts/unified-financial-pilot-v0.1/provider_verification.json
```

Environment variables:

- `PILOT_API_ENDPOINT`: explicitly verified OpenAI-compatible base URL.
- `PILOT_API_KEY` or existing `OPENROUTER_API_KEY`: secret; never serialized.
- `PILOT_MODEL_ID`: exact provider ID, verified `qwen/qwen3.8-27b`.
- `PILOT_REQUEST_TIMEOUT`: enforced wall deadline plus socket timeout in seconds; initial smoke 60, corrected continuation 45. The interrupted initial request exposed why a socket timeout alone was insufficient.
- `PILOT_MAX_INPUT_TOKENS`: conservative serialized-byte token upper bound including overhead, smoke 16000. Oversized sections fail without truncation.
- `PILOT_MAX_OUTPUT_TOKENS`: completion limit, smoke 2200.
- `PILOT_BUDGET_USD`: explicit positive hard cap, user approved 0.10.
- `PILOT_MAX_CALLS`: explicit maximum across persistent ledger, user approved 6.

With those configured, the smoke command is:

```sh
scrapper/.venv/bin/python -m experiments.unified_financial_pilot.runner smoke --manifest artifacts/unified-financial-pilot-v0.1/pilot_manifest.json --output artifacts/unified-financial-pilot-v0.1/new-smoke --company VIMTALABS --jobs artifacts/unified-financial-pilot-v0.1/smoke_jobs.json --verification artifacts/unified-financial-pilot-v0.1/provider_verification.json
```

The fixed shared ledger `artifacts/unified-financial-pilot-v0.1/inference` includes attempts, configurations, exact request representations, raw responses, tokens, latency, actual reported costs and reservations. Reservations are persisted before network access; crashes, timeouts and missing cost keep their entire reservation and count against the call limit. No retries. File locking prevents concurrent budget bypass. Cache identity includes document hash, representation, complete messages, prompt version, endpoint and model configuration. Cache hits do not spend. Do not delete the ledger to regain budget.

Pricing uses the conservative maximum advertised active-provider rate with OpenRouter provider price caps. This deliberately overestimates spend; provider-reported costs are logged separately. A generic OpenAI-compatible provider can supply a reviewed verification JSON with its exact base/model/modalities/pricing and timestamp; automatic catalog verification currently supports only the inspected OpenRouter catalog.

Numerical, qualitative and management tasks have distinct prompts. A management packet contains three whole adjacent physical pages; this does not establish that every later correction is covered. `task=visual` explicitly renders one selected original page via Poppler and sends PNG plus native context. The image and representation hashes are logged. Visual packets may exceed the conservative input budget and then stop before payment. The initial smoke uses native text/table inputs; chart interpretation stays unresolved. No enormous multi-task prompt and no arbitrary source truncation.

## Independent review and evaluation

`schema.py` defines the dataclass contract and strict intake. Model-supplied review/status fields are rejected. Exact original-page text/hash matches verify source anchoring only. Values, units, periods, scope, column binding, attribution, qualifications and rating status require independent review. Missing fields stay null. Dash/empty values cannot become zero. All model outputs and native windows initially remain `needs_review`.

Per-company human-review templates contain evidence IDs. Supply completed independent annotations as `{"observations":[...]}`; merge templates across companies when reviewing multiple dossiers. Each check takes `correct`, `incorrect` or `not_applicable`, with explicit reviewer, date, independence, critical unsupported flag and source-reference flag. Independently label material references from originals before consulting model results. Reference labels require original page/quote, one material disclosure per label, and explicit adjudicated evidence matches. Missing labels produce unknown recall, not zero or assumed success. `runner replay` rebuilds dossiers from originals and retained completed responses without any network request; `provider.recover_envelopes` permits removal of a single outer JSON code fence, preserving the raw response and original failure status. It never repairs truncated JSON.

```sh
scrapper/.venv/bin/python -m experiments.unified_financial_pilot.evaluate --manifest artifacts/unified-financial-pilot-v0.1/pilot_manifest.json --run artifacts/unified-financial-pilot-v0.1/vimta-smoke --reviews /path/to/independent-reviews.json --references /path/to/independent-references.json --output artifacts/unified-financial-pilot-v0.1/new-adjudication
```

This produces new source-linked dossiers and per-document correctness, attribution, source-reference and omission counts. Full acceptance is unassessed until every document has independently reviewed observations and complete material references. Independent authorship is a human attestation; code cannot certify reviewer independence. Evaluation inference is intentionally locked at this milestone. Finish both development cases and freeze tested prompts/configuration before adding an explicitly approved evaluation execution path. The implementation snapshot is an audit record, **not an evaluation freeze**.

Consolidate actual results with `python -m experiments.unified_financial_pilot.summarize`. See the artifact-root evaluation summary for measured outcomes and stopping conditions. No end-to-end acceptance is claimed from offline tests or source-window counts.
