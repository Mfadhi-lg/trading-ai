# Phase 2 prioritized gap register

This register accounts for all 13 Vimta and 20 EKC baseline unresolved fields. It is a workload assessment from retained candidates and canonical source inspection, not an absence certification. Page numbers are physical PDF pages in the hash-pinned baseline manifests. No additional fields were implemented here. `DISCLOSED` still means partial coverage.

Routes: **R** = resolvable through existing retained candidates with new atomic reviews; **S** = resolvable through source-backed deterministic extraction and review; **H** = requires human review of field meaning or binding; **N** = not yet determinable. **A** = requires new AI extraction: no field is demonstrably in this category yet, because complete native sources remain available. An AI request would be premature until retained/source routes are exhausted; none is authorized or made.

Priority 0 is delivered; 1 is the next bounded batch; 2 is subsequent source work; 3 needs a decision or stronger evidence. Routes describe adding a supported finding, not guaranteeing exhaustive coverage.

| Company | Baseline unresolved field | Priority / route | Evidence and remaining work |
|---|---|---|---|
| EKC | liquidity.cash_and_liquid_balances | 0 / R | A16, p3: delivered cash atom, March 31, 2024; unknown scope. |
| EKC | liquidity.bank_limit_utilisation | 0 / R | A16, p3: delivered average maximum utilisation, inclusive limits and independent window. |
| EKC | liquidity.cash_accruals | 0 / R | A16, p3: delivered above-₹130 crore expectation, medium term. |
| EKC | liquidity.repayment_obligations | 0 / R + S | A16 current proposition plus newly reviewed future commencement from p3; condition retained. |
| EKC | liquidity.explanations_and_constraints | 0 / S | p3: newly reviewed adequate liquidity rationale with inventory/accrual qualifications. |
| EKC | assessment.capex_and_execution_risks | 1 / R + S | A13/p2 capex amounts, funding and periods; split uncertain scope. Add p3 Egypt country/execution risk separately. |
| EKC | assessment.working_capital_intensity | 1 / R | A15/p3: split FY24/FY23 inventory and cycle; add import/inventory explanation from the paragraph. |
| EKC | assessment.raw_material_and_forex_risk | 1 / R + S | A15/p3 costs plus distinct forex/selective-hedging passage. Do not propagate consolidated scope. |
| Vimta | assessment.working_capital_intensity | 1 / R | A24/p2: 99 days FY23 versus 122 FY21; debtor/Covid explanation; preserve unknown scope. |
| Vimta | assessment.capex_and_execution_risks | 1 / R | A18/p2 conditional expectation: no significant debt-funded capex is a basis, not an established capex plan. |
| EKC | assessment.accounting_approach | 2 / S | p1 explicit consolidated approach; p6 entity table, extent and rationale need separate locators. |
| EKC | assessment.agency_expectations | 2 / S | p2 Positive outlook paragraph: volume growth, repeat orders, accruals; retain future tense. |
| EKC | assessment.operational_strengths | 2 / S | p2 promoter/market position and entry barriers; source-reviewed qualitative atoms. |
| EKC | assessment.business_weaknesses | 2 / S | p3 Egypt, working capital and forex paragraphs; overlaps other fields, do not duplicate financial totals. |
| EKC | assessment.profitability_explanations | 2 / S | p1 economies of scale; p2 expected volume/revenue-driven margin support; separate observed and expected. |
| EKC | assessment.order_book_and_execution | 2 / S | p2 repeat-order rationale supports qualitative finding only, not an order-book balance. |
| EKC | rating.withdrawal | 2 / S | p5 Annexure-2: historical term-loan withdrawal 25-Nov-21; must remain historical, not current action. |
| Vimta | assessment.revenue_explanations | 2 / S | p2 healthy-growth paragraph: pharma/non-pharma services and orders; no need for new inference. |
| Vimta | assessment.raw_material_and_forex_risk | 2 / S | p3 standalone export exposure, absent active hedge, natural hedge and timing mismatch. |
| Vimta | liquidity.cash_accruals | 2 / S | p2 says healthy accruals; qualitative finding only, no numerical accrual amount established. |
| Vimta | assessment.order_book_and_execution | 2 / S | p2 research/testing order growth, India/overseas; no order-book amount established. |
| Vimta | assessment.concentration_risks | 2 / S | p2 diversified customer base supports qualitative concentration assessment, not customer shares. |
| EKC | assessment.payment_and_covenant_risk | 3 / H | p5 covenants “Not applicable”; review mapping to this broader risk field, never infer no payment risk. |
| Vimta | assessment.payment_and_covenant_risk | 3 / H | p3/p5 covenant NA; review field mapping and scope before admission or absence certification. |
| EKC | assessment.group_support | 3 / N | p1/p6 common management/consolidation does not establish financial support commitments. |
| Vimta | assessment.group_support | 3 / N | p1 financial/operational linkages do not by themselves establish guarantees or support commitments. |
| EKC | assessment.pricing_and_cost_pass_through | 3 / N | p1/p2 lower realisation with softer steel; no established contractual pass-through mechanism. |
| EKC | rating.action_date | 3 / N | p1 report date is not independently established current action effective date; p5 historical dates differ. |
| EKC | rating.watch | 3 / N | Criteria list mentions watch generically; no issuer-specific watch finding established. |
| Vimta | rating.action_date | 3 / N | p1 report date cannot silently become effective action date. |
| Vimta | rating.previous_rating | 3 / N | p4–5 history lacks a usable prior rating; Assigned is not proof of no historical rating. |
| Vimta | rating.watch | 3 / N | p3 criteria label is not an issuer watch action. |
| Vimta | rating.withdrawal | 3 / N | p3 withdrawal-policy mention is not an issuer withdrawal; no action established in history. |

Next stage: reuse the adapter for a bounded working-capital/capex batch (EKC A15/A13 and Vimta A24/A18), preserving unknown scope, distinct periods and conditional expectations. Then extend reviewed table-cell proposals to EKC A17 with audited/unaudited headers. A17 affects already disclosed fields and therefore is additional depth work outside this 33-row unresolved-field register. The unresolved deterministic rating event likewise remains in the review queue, outside unresolved-field counts. Do not use rising acceptance counts as a completeness metric.
