# Credit Rating Intelligence V1 — offline development slice

Working implementation: `scrapper/research/document_intelligence/credit_v1.py`. CLI: `scripts/credit-intelligence-offline` (or `python3 -m scrapper.research.document_intelligence.credit_v1`). Contract: [output.schema.json](output.schema.json), version `credit-rating-intelligence/1.0.0`. Tests: `scrapper/tests/test_credit_intelligence_v1.py`.

This location holds manifests, explicit source-review attestations, the contract and handoff. Code stays alongside the existing research modules; generated outputs live under `artifacts/credit-rating-intelligence-v1/`. No duplicate extraction platform, migrations or acquisition integration was introduced.

## Run from the repository root

The commands below reproduce the delivered records into **new directories**. Existing outputs are never overwritten. Python 3.14 and the already installed `pypdf` are sufficient for these retained-canonical replays. EKC's inventoried original PDF must be mounted at its existing `/Volumes/My Passport/...` path. No download or document substitution occurs if it is unavailable.

```sh
python3 -m unittest scrapper.tests.test_credit_intelligence_v1 -v
./scripts/credit-intelligence-offline --manifest docs/credit-rating-intelligence-v1/vimta-manifest.json --output artifacts/credit-rating-intelligence-v1/reproduce-vimta --stem vimta
./scripts/credit-intelligence-offline --manifest docs/credit-rating-intelligence-v1/ekc-manifest.json --output artifacts/credit-rating-intelligence-v1/reproduce-ekc --stem ekc
```

For another repeat, choose another new output leaf. Four files are generated per run: company intelligence JSON, readable Markdown with expandable full source support, coverage ledger JSON, and evidence review queue JSON. The JSON embeds its ledger and queue too, so it can be consumed independently.

```python
from scrapper.research.document_intelligence.credit_v1 import build_record
record = build_record("docs/credit-rating-intelligence-v1/vimta-manifest.json")
```

`build_record` reads inputs and returns a dictionary; it does not write files. `export_record` is the only output writer, restricted to new directories under the dedicated artifacts area or `/tmp`. The original source, canonical extraction, original raw model response, review sidecar and source inventory are hash-checked, and rechecked after replay. Python resolves every hash and locator. Socket/DNS and SQLite connections are denied during processing. No inference is called. Fresh PDF extraction delegates to the existing D03 `extract_local` when no canonical file is supplied; that optional route requires the repository's existing PDF/OCR dependencies and was not exercised in this environment, where `pdfplumber` and `pytesseract` are absent.

## Concise reuse inventory

- `schema.py`: unchanged `DocumentContext` and `ResearchEvidence` identities/statuses.
- `evidence.py`: unchanged canonical hashing, exact text/cell locators and fail-closed validator.
- `ratings.py`: existing facility/tenor-specific events, previous-grade handling and action taxonomy. D04 `documents/credit_rating.py` supplies agency, facility rows and amount/unit normalization. Shared LT/ST facility IDs prevent treating the same amount as two independent limits; no amounts are summed.
- `offline.py`: existing network denial and D03 extraction fallback. Existing complete D03 canonical payloads are reused, with pinned file hashes and original PDF page-count/cover checks.
- `claim_candidates.py`: inspected numerical relation/period/scope qualification. Its source-ID-only candidate format differs from the retained prose responses, so it was not forced onto these outputs. It remains the reusable future numerical candidate boundary.
- `source_review.py`: inspected source-unit locators and review-only semantics. Its FTS search is unnecessary for this bounded replay; no new evidence store or index was created.
- V0.6/V0.7 trusted exports, original responses, source packets, source PDFs and omission ledgers were inspected. The runtime reads original response bytes and explicit pinned V1 review attestations, **not reference answers, reference match scores or trusted-export summaries**.

New work consists of the versioned record/coverage contract, retained-response adapter, separate field/evidence states, source-review pinning, deterministic liquidity section-label extraction, output/report generation, executable CLI and offline tests. No old contract or parser was changed.

## Admission and state semantics

`DISCLOSED` means at least one accepted disclosure supports a field; it does **not** mean exhaustive coverage. Every disclosed field is currently marked `PARTIAL` for completeness. `UNRESOLVED` includes no candidate, inadequate processing, unsupported scope, ambiguous binding, or missing qualifications. `NOT_DISCLOSED` requires explicit absence review with adequate complete source coverage. This first slice emits no `NOT_DISCLOSED` findings because exhaustive semantic coverage has not been established. Native text on all pages is not proof of complete extraction.

Evidence decisions are independent: `SUPPORTED`, `NEEDS_REVIEW`, `WITHHELD`. Replayed supported statements map to Phase 5 `EVIDENCED_STATEMENT`, unresolved reviews to `AMBIGUOUS`, withheld claims to `UNSUPPORTED`. Deterministic rating events retain their original `VERIFIED`/`AMBIGUOUS` status. Phase 5 `UNKNOWN`/`NOT_FOUND` must not be converted to absence without a coverage review. No existing Phase 5 statuses were redefined.

Each replay attestation pins the **entire original candidate** and canonical representation, supporting physical pages, field assignments, decision and reason. The whole relevant passages and table headers/footnotes are retained in full-page source regions. Their exact offsets are validated using the existing evidence validator. Original model fragment IDs remain in `original_candidate`; they are not represented as repaired model citations. Pipeline evidence has its own deterministic region IDs, source document hash and canonical hash. The review sidecars are curated trust inputs, not an automated semantic verifier. New/unreviewed candidates stay `NEEDS_REVIEW`; edited candidates or changed source representations cannot reuse old attestations. Keyword or reference matching never authorizes acceptance.

Numerical review covers labels, values, original units, literal periods, accounting scope and qualifications together. Multi-value prose remains a qualified disclosure, not an invented series of normalized financial atoms. Full passages retain comparisons and conditions even when they are not separately extracted. `value` and `period` on replayed claims remain the original model's primary fields; consumers must not apply one period/value to every number in a statement. No currency conversion, acronym expansion or consolidated-scope default is applied.

Company IDs are existing repository research symbols (`VIMTALABS`, `EKC`), explicitly namespaced; they are not invented numeric production database IDs. Report dates are verified against the cover. Publication/filing dates remain unknown. Effective rating action dates remain unresolved where the existing normalizer does not establish them; report date is not silently substituted.

## Delivered results

- [Vimta JSON](../../artifacts/credit-rating-intelligence-v1/vimta/vimta-credit-intelligence.json), [report](../../artifacts/credit-rating-intelligence-v1/vimta/vimta-credit-intelligence.md), [coverage](../../artifacts/credit-rating-intelligence-v1/vimta/vimta-coverage-ledger.json), [review queue](../../artifacts/credit-rating-intelligence-v1/vimta/vimta-evidence-review-queue.json): 27 accepted disclosure records, 5 review items, 13 unresolved field groups.
- [EKC JSON](../../artifacts/credit-rating-intelligence-v1/ekc/ekc-credit-intelligence.json), [report](../../artifacts/credit-rating-intelligence-v1/ekc/ekc-credit-intelligence.md), [coverage](../../artifacts/credit-rating-intelligence-v1/ekc/ekc-coverage-ledger.json), [review queue](../../artifacts/credit-rating-intelligence-v1/ekc/ekc-evidence-review-queue.json): 17 accepted disclosure records, 5 review items, 20 unresolved field groups.

Counts are disclosures, not distinct financial facts: replay and deterministic rating disclosures can overlap. Their evidence/method origins remain separate. Nothing here is production-certified, a completeness certificate, or a current company-health assessment.

See [SOURCE_REVIEW.md](SOURCE_REVIEW.md) for source verification and material omissions, and [HANDOFF.md](HANDOFF.md) for the bounded next step.

## Phase 2 — atomic disclosure adapter

The baseline above remains reproducible unchanged. [Phase 2 implementation and verification](PHASE2.md) adds six independently reviewed EKC liquidity atoms through `ekc-phase2-manifest.json`, preserving A16 as WITHHELD. See the [prioritized remaining-gap register](PHASE2-GAPS.md). No new inference or acquisition changes were made.

## Phase 4 fresh intake

See [implemented fresh extraction workflow](PHASE4.md), [response schema](fresh-response.schema.json), [bundle schema](fresh-bundle.schema.json), and [one-request authorization summary](LIVE-AUTHORIZATION.md). No live inference has been run.
