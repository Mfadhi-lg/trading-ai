# Phase 3 — bounded working-capital, capex and risk batch

Implemented on 2026-10-24 using the existing `credit_atomic.derive_claims` adapter. No new extraction framework, inference, network, database, acquisition, production integration, scoring or model comparison was used.

Source review: Codex compared the four retained candidates with original hash-verified CARE PDFs (EKC physical pages 2–3; Vimta physical page 2) and retained canonical text. This is an agent source review, not independent human certification. Reviews pin each entire proposed atom, including fields, periods, qualifications and all support passages. Python verifies hashes and exact locators; it does not independently establish semantic entailment. New versioned packets contain their source-review decisions under `reviews`:

- [EKC packet](phase3/ekc-atomic-candidates-v1.json), including the unchanged six Phase 2 A16 atoms.
- [Vimta packet](phase3/vimta-atomic-candidates-v1.json).
- [EKC manifest](ekc-phase3-manifest.json) and [Vimta manifest](vimta-phase3-manifest.json).

The adapter now carries the explicitly supplied disclosure category into derived findings. Coverage links suppress an accepted parent's link for fields covered by accepted children and deduplicate identical atomic propositions; raw claims remain available. This performs exact proposition identity deduplication, not general semantic similarity. Earlier manifests continue reproducing their earlier records.

Newly accepted facts:

- EKC A13: estimated ongoing Mundra/Egypt capex of ~₹307.00 crore including interest capitalisation; around 61% scheduled bank funding during FY25–FY26; conditional below-unity gearing expectation; expected future increase in net debt; Egypt economic/political exposure via EKC Egypt; expected commercialisation by FY26 as a monitorable.
- EKC A15: inventory 186 days FY24 and 163 days FY23; working-capital cycle 221 days FY24 and 198 days FY23; ~four-month inventory policy and import explanation; nearly 60–70% raw-material share of operating expenses; adverse USD/INR exposure; selective hedging with residual volatility.
- Vimta A24: operating cycle 99 days FY23 and 122 days FY21; debtor-realisation explanation and end-FY21 COVID context.
- Vimta A18: CARE's medium-term TOL/TNW expectation depends on net worth accumulation and absence of significant debt-funded capex. It is an agency assessment assumption, not a company investment commitment. The locally established consolidated scope applies to this capital-structure paragraph; it is not propagated to A24.

The nil-net-debt baseline remains in the original accepted financial disclosure and in the expected-increase atom’s evidence; it is not duplicated as a new atom.

Discovery attribution is separate from source admission: EKC adds 7 retained-candidate splits and 7 source-review additions; Vimta adds 3 retained-candidate splits and 1 source-review addition. A18's restored debt-funding premise was absent from the retained statement, so the conditional capex finding is explicitly a source-review addition. The existing TOL/TNW level is not added as another historical financial fact. No fresh AI discoveries were made. Per-atom attribution is recorded in `derivation_basis`; the original candidates and raw responses are unchanged.

Newly covered field groups (all PARTIAL): EKC working-capital intensity, capex/execution risks, raw-material/forex risk; Vimta working-capital intensity and capex/execution risks. EKC accepted disclosures increase 23 → 37 and unresolved field groups 15 → 12. Vimta accepted disclosures increase 27 → 31 and unresolved field groups 13 → 11. Each original five-item review queue is preserved. Acceptance counts measure reviewed statements, not completeness.

Material uncertainties: unknown EKC accounting scope, unknown A24 scope, unspecified EKC total expenditure period, undated raw-material share/policy/hedging practice, and conditional capex/debt/commercialisation expectations. FY25–FY26 applies to scheduled borrowing funding, not automatically to all expenditure. Nil net debt does not mean nil gross debt. Selective hedging does not establish a hedge percentage or complete protection. Original A13/A15/A24/A18 review decisions remain visible; original A16 stays WITHHELD. EKC A17 audit-header binding remains backlog and was not processed.

Genuine source omissions within the bounded passages: no explicit accounting scope for EKC's capex/working-capital/forex statements or Vimta's operating cycle; no total-expenditure start/end window for the ₹307.00 crore estimate; no hedge ratio, contract details or reporting period for selective hedging; no management commitment to avoid debt-funded capex in the Vimta assumption passage. These are local passage omissions, not whole-document absence certifications. No unresolved field was changed to NOT_DISCLOSED.

Outputs are new-only under [Phase 3 artifacts](../../artifacts/credit-rating-intelligence-v1/phase3/): final company records, reports, ledgers and queues under `final/ekc` and `final/vimta`, [coverage comparison and per-atom changes](../../artifacts/credit-rating-intelligence-v1/phase3/coverage-comparison.json), [replay evidence](../../artifacts/credit-rating-intelligence-v1/phase3/replay-evidence.json), and [tests](../../artifacts/credit-rating-intelligence-v1/phase3/tests.log). Phase 1 and Phase 2 outputs were not overwritten.

Validation: 29 tests pass (22 existing plus 7 Phase 3 tests). Tests exercise independent periods, unknown/local scope, estimated versus historical capex, debt-funding conditions, inventory comparisons, forex/hedging attribution, multi-passage support, invalid locators even with a repinned candidate, candidate and parent hash changes, review absence, duplicate/parent coverage and both deterministic offline replays. Every earlier accepted claim and review-queue item matches its baseline exactly. Input bytes remain unchanged across replays.

Reproduce with a new output directory:

```sh
python3 -m unittest scrapper.tests.test_credit_intelligence_v1 scrapper.tests.test_credit_atomic scrapper.tests.test_credit_phase3 -v
./scripts/credit-intelligence-offline --manifest docs/credit-rating-intelligence-v1/ekc-phase3-manifest.json --output artifacts/credit-rating-intelligence-v1/phase3-repeat-ekc --stem ekc
./scripts/credit-intelligence-offline --manifest docs/credit-rating-intelligence-v1/vimta-phase3-manifest.json --output artifacts/credit-rating-intelligence-v1/phase3-repeat-vimta --stem vimta
```

See [remaining unresolved fields](PHASE3-GAPS.md) and the [fresh extraction handoff](PHASE4-FRESH-EXTRACTION-HANDOFF.md). This completes the bounded batch; fresh inference has not begun.
