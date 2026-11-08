# Phase 4 — fresh-document extraction adapter

Implemented the D03 → frozen regions → injected provider → retained originals → atomic source review → V1 export connection. **No live inference was run or authorized.** This is working software with synthetic offline verification, not evidence of live extraction accuracy.

## Frozen fresh source

Shriram Properties Limited (SHRIRAMPPS), local acquisition identity `local-acquisition:shriram-credit-rating-2025-10-30`, dated 30 October 2025. Original SHA-256: `71908f4b1194a5361f69bc94aa7e5d0fe92f1d73e087074fbfdc7c93413acc87`.

The existing issuer PDF has one physical page and usable native text. It reports CRISIL's assessment; it is an issuer notification, not a full agency rationale. The saved acquisition archive links the PDF and documents its earlier acquisition-only inspection. Searches for company name, symbol and hash found no exposure in the Phase 5, Vimta/EKC or held-out experiment directories listed in the [selection receipt](../../artifacts/credit-rating-intelligence-v1/phase4/source/selection.json). This is a repository exposure audit, not a guarantee about activity outside this workspace. No prior company AI answer was read or used. Two other local acquisition PDFs were publisher “NO FILE TO VIEW” placeholders and were not selected.

Selection was frozen before request construction. Report date comes from the dated notice, not a rating action date. [Prepared artifacts](../../artifacts/credit-rating-intelligence-v1/phase4/prepared/freeze.json) include D03 canonical JSON, catalog, source-only request, exact transport payload, empty review file and manifest. There are 43 addressable regions across physical page 1/1 and four parser-detected table elements. Four regions containing replacement characters are UNRESOLVED. All page text and complete detected table matrices are retained as shared surrounding/header context. No OCR, spelling repair, page omission or document download occurred. Native extraction can still have layout/header defects; semantic review remains necessary.

## Interfaces and invariants

- `credit_candidate_provider.build_region_catalog(context, extraction)` enumerates actual canonical line offsets and table cells with `text_evidence`, `cell_evidence`, `attach_evidence` and `validate_evidence`. Locator syntax is the existing `p…:e…:r…:c…:start-end`; global identity is the PDF hash + canonical representation hash + locator. Catalog stores parser provenance, exact canonical text, native origin, page context and table matrices. Canonical file hash and canonical representation hash remain distinct.
- `build_request(context, catalog, fields)` contains only identity, source catalog, V1 field contract and extraction instructions. No expected answers, historical experiments or reviews enter it.
- `generate_candidates(provider, request)` accepts a callable returning `(raw_response_bytes, receipt)`. It retains exact response bytes (hex in the bundle), raw hash, source request and request hash. Malformed output is retained, not semantically repaired. The live CLI also saves the original response file before parsing.
- `load_candidate_bundle(path, context, extraction, reviews=None)` recomputes the catalog and request, verifies hashes and document identity, validates the strict DTO, resolves IDs exclusively in Python and retains each original proposal through `replay_candidate`. Each valid proposal becomes its own retained parent and an atom passed to `credit_atomic.derive_claims`; the atom includes a whole-original candidate hash. Invalid individual proposals are WITHHELD, with originals and errors in the queue. Corrupt envelopes/identity/raw receipts fail intake; their saved bytes remain available for inspection.
- `credit_v1.build_record` accepts optional `candidate_bundle` and `fresh_reviews` descriptors, alongside historical `responses` and `atomic_candidates`. Fresh provenance is `generated_ai`; offline replay still records zero inference calls. Parent originals remain visible with their original decisions, but atoms alone carry field links. Existing coverage deduplication suppresses repeated propositions without summing them.

The [response schema](fresh-response.schema.json) requires separate atoms with category, field, statement, literal value/unit, period and kind, explicit scope or null, attribution, statement kind, conditions, qualifications and region IDs. Unknown scope is not filled from parents. Multiple atoms can cite one paragraph. Extra page/table/offset/hash fields are rejected. The [bundle schema](fresh-bundle.schema.json) describes immutable transport retention; runtime validation additionally enforces source/request equality and semantic enum relationships.

Exact source matching is necessary but insufficient. A false attribution, wrong valid region, mixed-period inference or omitted premise can still have exact citations; it remains NEEDS_REVIEW until separately reviewed. Empty provider output never certifies source absence. An admitted atom with unknown scope remains component-UNRESOLVED and field coverage stays PARTIAL. Unreviewed/partial extraction and damaged regions cannot support fresh admission.

## CLI

Use the existing repository Python dependencies. For this session only, missing `pytesseract` and `pdfplumber` were installed into `/tmp/credit-phase4-deps`; prefix commands with `PYTHONPATH=/tmp/credit-phase4-deps` in this environment. No repository dependency was added. OCR remains disabled.

```sh
./scripts/credit-intelligence-fresh prepare \
  --selection artifacts/credit-rating-intelligence-v1/phase4/source/selection.json \
  --output artifacts/credit-rating-intelligence-v1/phase4/another-prepared-directory
```

Preparation verifies the frozen original and acquisition receipts, literal company/date on the cover and physical page count. All outputs are new-only under development artifacts or `/tmp`. A cover mismatch fails for review; aliases/dates are not silently repaired.

The prepared live invocation is in [LIVE-AUTHORIZATION.md](LIVE-AUTHORIZATION.md). It requires the exact payload hash and fixed spending reservation. It reuses `general_ai_experiment.runner.request` as transport only, with a backward-compatible `return_raw` option; it does not invoke benchmark preparation or orchestration. One model, one provider, no retries/fallback/tools/search/Batch. An exclusive reservation persists after errors/timeouts; a second send from that preparation is refused. HTTP error bytes are retained where available. The request is outside the offline guard; admission and replay deny network and database connections.

After a separately authorized generation:

```sh
./scripts/credit-intelligence-fresh admit \
  --prepared artifacts/credit-rating-intelligence-v1/phase4/prepared \
  --bundle artifacts/credit-rating-intelligence-v1/phase4/prepared/candidate-bundle.json \
  --output artifacts/credit-rating-intelligence-v1/phase4/live-unreviewed

./scripts/credit-intelligence-fresh replay \
  --manifest artifacts/credit-rating-intelligence-v1/phase4/live-unreviewed/manifest.json \
  --output artifacts/credit-rating-intelligence-v1/phase4/live-replayed \
  --stem shrirampps
```

`admit` exports the record, Markdown, ledger, queue, resolved atomic proposals and an empty review template. `export` has the same arguments as `replay` and deterministically rebuilds from pinned input receipts. Files remain linked by absolute local paths and hashes; moving the corpus requires deliberate new manifests, not silent relocation.

## Reusable source review

1. Open the original hash-verified PDF and corresponding canonical page. Review each material proposed atom against full context, table headers, entity attribution, period, scope, units, conditions and qualifications. A matching quote alone is not a source review. Do not certify whole-field absence from a bounded passage.
2. Copy `review-template.json` to a **new** review file. It binds PDF hash, canonical hash and candidate-bundle hash. In `reviews`, add an entry keyed by `atomic_key` from `atomic-review-proposals.json`, with `candidate_sha256 = evidence.canonical_hash(entire_atom)`, `decision` (`SUPPORTED`, `NEEDS_REVIEW` or `WITHHELD`) and a specific `reason`. Record reviewer identity and review basis in that reason or additional packet metadata. Review files are explicit trusted inputs; Python checks integrity, not reviewer truthfulness or authorization.
3. Do not change original candidates or raw responses. Reject incomplete atoms or create separately versioned reviewer-derived atomic proposals using the existing `atomic_candidates` contract, retaining original parent ID/hash and `derivation_basis=source_review_addition`. Pin the entire corrected atom in its own review. Do not call a reviewer correction a fresh AI discovery.
4. Rerun `admit` with `--reviews path/to/new-review.json` into a new output directory. Missing reviews leave candidates NEEDS_REVIEW; hash changes fail closed. Review queue parents retain original decisions. Then replay that new manifest twice offline and compare exports.

## Verification and limits

[Tests](../../artifacts/credit-rating-intelligence-v1/phase4/tests.log): 43 pass (29 historical + 14 new). The new suite covers real D03 extraction of a synthetic PDF, injected valid/invalid responses, table/header cataloging, invalid region/page/table/quote fields, changed identities/hashes, historical/expectation distinctions, null scope, independent periods, conditional debt/capex, incomplete qualifications, unsupported statements, whole-atom admission, duplicate prevention, raw preservation, truncation, explicit authorization, one-attempt reservations and deterministic replay.

[Replay evidence](../../artifacts/credit-rating-intelligence-v1/phase4/replay-evidence.json) records byte-identical JSON, Markdown, ledger and queue exports for both Vimta and EKC against Phase 3 final outputs, plus two synthetic fresh replays. [Synthetic artifacts](../../artifacts/credit-rating-intelligence-v1/phase4/synthetic/admitted/manifest.json) use a separate invented company/PDF; no synthetic answer was added to the fresh company's request. The synthetic review is only a software test.

Two extra broad test modules (`test_d03_document_extraction`, `test_document_evidence_interface`) could not import because their shared extraction pipeline imports PowerPoint's unavailable `olefile` dependency. Their [import-limit log](../../artifacts/credit-rating-intelligence-v1/phase4/additional-suite-import-limitations.log) is retained; they are not counted as passing. The new end-to-end test directly ran D03 PDF extraction successfully. No PPT/transcript work was undertaken.

No acquisition dispatcher, production data/database, Focus List decision or previous credit output was changed. Live accuracy and material source review of generated disclosures remain the next task after authorization.
