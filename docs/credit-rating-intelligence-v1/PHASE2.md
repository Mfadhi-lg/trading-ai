# Atomic disclosure implementation and verification

Implemented `scrapper/research/document_intelligence/credit_atomic.py`, called by the existing V1 builder when a manifest supplies `atomic_candidates`. Original manifests and historical outputs remain intact. The additive candidate packet is a curated source-review input, not a general NLP splitter or semantic entailment model. It supports multiple exact text/table locators through the existing evidence validator. Each proposal supplies its own value, original unit, time basis, scope, attribution, qualifications and conditions; none are inherited from its parent.

An atom pins its parent claim and original candidate hash. Packet identity pins the original PDF and canonical extraction; the manifest pins the packet bytes. Each review pins the entire atom, including field assignments and every supporting locator. Missing reviews stay NEEDS_REVIEW; changed atoms, mismatched identity or invalid evidence are WITHHELD. Scope can be explicitly unknown while the source statement is supported. Component completeness remains separate from evidence admission and field coverage.

Source review performed by Codex on 2026-10-24, not independent human certification: original hash-verified EKC PDF page 3 text was compared directly with retained canonical extraction and original A16. Six derived atoms were reviewed in the complete liquidity paragraph. All use validated paragraph offsets, not model-proposed region IDs. The original A16 remains byte-equivalent in the review queue with its unsupported consolidated scope and WITHHELD decision.

- Cash: ₹64.9 crore at March 31, 2024.
- Utilisation: ~34%, average maximum, fund-based and non-fund-based working-capital limits, twelve months ended June 30, 2024.
- GCA: expected above ₹130 crore in medium term; no realised-period substitution.
- Current principal obligations: none “currently”; literal relative date retained, with future-repayment condition.
- Future repayment: expected from FY26–FY27 onwards given debt-funded capex on standalone and consolidated bases. This scope describes the capex condition; it does not assign consolidated scope to the cash or other atoms.
- Liquidity explanation: adequate/improving expectation; inventory-related working-capital constraint and healthy-accrual basis for servicing upcoming debt.

The future repayment proposition appears in A16's evidence fragments but was omitted from its extracted statement and qualifications. Its derived atom is explicitly `source_review_addition`, not retroactively represented as an original accepted extraction. The final qualifying rationale is also a source addition. The deterministic Adequate label already exists and is not added again. All six new atoms are SUPPORTED; all accounting scopes remain null/UNRESOLVED. Original non-atomic claims retain their historical evidence breadth; no unaudited automatic citation narrowing was performed.

Outputs: [EKC report](../../artifacts/credit-rating-intelligence-v1/phase2/ekc/ekc-credit-intelligence.md), [EKC JSON](../../artifacts/credit-rating-intelligence-v1/phase2/ekc/ekc-credit-intelligence.json), [coverage](../../artifacts/credit-rating-intelligence-v1/phase2/ekc/ekc-coverage-ledger.json), [review queue](../../artifacts/credit-rating-intelligence-v1/phase2/ekc/ekc-evidence-review-queue.json), [Vimta report](../../artifacts/credit-rating-intelligence-v1/phase2/vimta/vimta-credit-intelligence.md).

Verification: 22 tests pass (15 original + 7 focused atomic tests). Both repeated records match exactly; input hashes are unchanged. EKC: 23 accepted, 5 review, 15 unresolved fields; Vimta: 27 accepted, 5 review, 13 unresolved. Every original accepted claim and queued record matches the historical output. The five newly covered liquidity fields remain PARTIAL. Historical omissions are retained and labelled as historical in `source_coverage.atomic_review`.

[Tests](../../artifacts/credit-rating-intelligence-v1/phase2/tests.log) and [replay evidence/input hashes](../../artifacts/credit-rating-intelligence-v1/phase2/replay-evidence.json) are retained. Socket/DNS and SQLite denial remain active; replays use retained canonical sources, no network, inference or database activity. Production and acquisition code are untouched.

```sh
python3 -m unittest scrapper.tests.test_credit_intelligence_v1 scrapper.tests.test_credit_atomic -v
./scripts/credit-intelligence-offline --manifest docs/credit-rating-intelligence-v1/ekc-phase2-manifest.json --output artifacts/credit-rating-intelligence-v1/phase2-repeat-ekc --stem ekc
```

Choose a new output directory for each export. The original EKC manifest still reproduces the 17-claim baseline. Use the Phase 2 manifest for atomic output. See the [single prioritized gap register](PHASE2-GAPS.md) for all 33 baseline unresolved fields and the next bounded implementation batch. No new extraction/model comparison is recommended at this stage.
