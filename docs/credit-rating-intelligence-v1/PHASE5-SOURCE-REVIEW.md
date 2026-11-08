# Full-rationale source review

No Aether claims have been generated or admitted. This procedure applies after the separately authorized single request. The original PDF, native catalog, table matrices and page images must remain unchanged.

1. Verify the prepared packet offline using `credit_fresh.verify_prepared`. Verify the supplemental [source packet](../../artifacts/credit-rating-intelligence-v1/phase5/source-packet.json) descriptors before viewing its review aids. If any identity differs, stop review and retain the discrepancy; do not repair the frozen packet.
2. Retain the raw response and bundle. Use `admit` into a **new** directory with no reviews first. Malformed/truncated provider output is an intake failure, not evidence of missing disclosure. Unsupported/invalid candidates remain in the review queue. Do not issue a retry.
3. Review every material atom against the original page image and full paragraph/table context. Check company, agency versus issuer attribution, facility and tenor, current versus prior action, exact date/financial period, units, standalone/consolidated scope, future versus historical status, all conditions and qualifications. Review each independent atom even when several share a paragraph.
4. Use the field checklist to revisit omissions. At least inspect ratings/action pages 1/5/7, business strengths/weaknesses pages 1–3, liquidity page 3, consolidated financials/scope pages 4/7, leverage and working capital pages 1–4, capex pages 1–3, and sensitivities across pages 3–4. These are navigation aids, not a cap on extraction. Review every page for additional material items. Never infer source absence from an empty model output or blank parsed table cell.
5. Page 4's financial values and approach comments are absent from the detected cell matrices but present in native text. Cite intact native row lines together with column headers, units, accounting scope and footnotes. Check these associations visually. If native support is insufficient, use the explicit visual correction path below. Page 5's historical headers and apparent rating inconsistencies must remain literal; do not normalize them into plausible but unsupported ratings.
6. Copy `review-template.json` into a new file. Pin each entire atomic proposal using `canonical_hash(atom)`, select `SUPPORTED`, `NEEDS_REVIEW` or `WITHHELD`, and give a specific source-review reason. The packet already pins PDF, canonical and candidate-bundle identities. Review metadata is a trusted human input, separate from provider output; a hash proves integrity, not truthfulness. Run `admit --reviews ...` into another new directory and replay twice.
7. Recompute the supplemental coverage checklist from accepted claims and the queue. Keep `MISSING_CANDIDATE`, `EVIDENCE_FAILURE`, parser gaps and pending review distinct. Any `NOT_DISCLOSED` assessment requires a separate explicit full-document review with every page, the catalog hash, reviewer, reason and exact-source-review flag. An accepted claim only establishes partial field coverage, not exhaustive recall. Do not present the existing V1 ledger as an exhaustive-absence certification.

After authorization and generation, initial offline intake is:

```sh
PYTHONPATH=/tmp/credit-phase4-deps ./scripts/credit-intelligence-fresh admit \
  --prepared artifacts/credit-rating-intelligence-v1/phase5/prepared \
  --bundle artifacts/credit-rating-intelligence-v1/phase5/prepared/candidate-bundle.json \
  --output artifacts/credit-rating-intelligence-v1/phase5/live-unreviewed
```

## Damaged-text or visual-only corrections

Keep the original provider candidate and its decision. Create a new atomic packet through the existing `atomic_candidates` manifest input. Use a distinct `atomic_key`, the retained parent's `claim_id` and `canonical_hash(original_candidate)`, and `derivation_basis='source_review_addition'`. Copy no unverified scope, date, unit or qualification from a parent. The new statement is a reviewer-derived assertion, not a new AI discovery.

Retain the unchanged native locator(s) in `support`, even if their text is damaged. With `credit_visual.bind_visual`, bind the original PDF, context, canonical extraction, those locators, physical page, crop coordinates and a new page-image path. Coordinates are rendered-page top-left points at two pixels per point; the binding retains the full image and hashes the crop. Existing frozen page images may be reused if byte-identical. Put each returned binding in `visual_sources`. The source QA packet provides bindings for the page 4 financial table and page 5 history as optional review aids; they are not admissions or transcriptions.

After inspecting that exact original page/crop and all relevant headers/context, the trusted review entry must contain:

```json
{
  "candidate_sha256": "canonical_hash of the entire separately versioned atom, including visual_sources",
  "decision": "SUPPORTED",
  "reason": "Specific source support, scope, period, attribution and qualifications reviewed",
  "verification_method": "visual_pdf_inspection",
  "reviewer": "actual reviewer identity",
  "reviewed_at": "actual review timestamp",
  "exact_source_review": true
}
```

Pin the new atomic packet with a file descriptor in a **new** manifest, preserving the original manifest, bundle, reviews and exports. `credit-intelligence-fresh replay --manifest ... --output ... --stem ...` applies this packet through `credit_atomic.derive_claims` and exports V1. Every binding is revalidated against the original PDF and stored image during replay. A missing image is not recreated during admission; changed source, coordinates, image or candidate fail closed. Native text is never replaced by the corrected assertion. Do not use this mechanism to promote Shriram's existing withheld candidate; it remains unchanged.

For an executable synthetic example of the entire visual correction/export flow, inspect `scrapper/tests/test_credit_complete.py::test_visual_correction_exports_through_v1_and_replays` and the retained `offline-validation-final/synthetic-visual` packet. Those test reviews are explicitly synthetic and confer no trust on real-company facts.
