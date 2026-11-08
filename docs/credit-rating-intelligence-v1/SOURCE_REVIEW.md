# Source review and omissions

Review performed offline by Codex on 2026-10-23; this is direct source inspection by the implementation agent, **not independent human certification**. No new inference/provider request was made. Existing reports and experiment artifacts were preserved.

## Identity checks

- Vimta Labs Limited: CARE report **2023-07-11**, six physical pages. Original SHA-256 `7ed9acaec3a58c63fa1bdc78a8c69dc30f84fb9bfbfc85d48d32189a19781156`. Identity matched `three-document-reality-check-v0.4/sources.json` and the original `sample_files-2/in_q1_focus_list/credit_ratings/CARE_Vimta_Labs_2023-07-11.pdf`.
- Everest Kanto Cylinder Limited: CARE report **2024-09-26**, seven physical pages. Original SHA-256 `b0f06214a0b40f2327eb17e18b0ee88fc3f63436bd8430b9c455733781650a05`. Identity matched V0.6 `ekc-source-inventory.json`, V0.7 source freeze and the original PDF on the mounted document volume. This is not a Q4 FY2025 rating report.

Complete retained D03 native text was inspected for both PDFs. Direct PDF renderings were inspected for Vimta pages 1–2 and EKC pages 1, 3 and 4; EKC page 3 was freshly rendered from the hash-verified original. Page 2 EKC paragraphs were reviewed in native source text. Source packets and original raw responses were compared with V0.6/V0.7 audit findings. The exported evidence retains whole source pages for replay claims so paragraph qualifications and table headers are not lost.

## Vimta

Page 1 confirms three facility rows: ₹36.22 crore LT CARE A/Stable; ₹34 crore combined LT/ST CARE A/Stable and CARE A1; ₹1.78 crore ST CARE A1, all assigned. Deterministic output splits the combined row into two tenors with a shared facility ID/amount. The facility limit is not outstanding debt.

Pages 1–2 support sensitivities, consolidated analytical approach, agency outlook expectation, operating strengths, growth/profitability disclosures, gearing and debt coverage. Page 2 explicitly supports approximate consolidated ₹30 crore fixed-deposit liquidity at March 31, 2023 and negligible utilisation of ₹30 crore fund-based limits over the twelve months ended April 2023. The separate scope-unknown operational/ratio passages do not inherit consolidated scope from deposits.

The original focused F05 is accepted in full after direct review as an **undated, scope-unknown agency liquidity rationale**, including negligible scheduled term repayment. This differs explicitly from V0.6's narrowed replacement; that replacement is not imported. F12 and F13 are replayed unchanged, adding the service acquisition and profitability explanation. Original fields and response pointers are retained.

Five baseline records remain queued: A11 overextends standalone scope; A18 loses the net-worth/no-significant-debt-funded-capex bases of an expectation; A20 expands TDGCA beyond the literal source label; A21 merges the liquidity label with scope-uncertain ratios; A24 has unresolved operating-cycle accounting scope. Only the literal liquidity label is separately extracted deterministically.

Material omissions: causal revenue explanation, full conditional capital-structure expectation, working-capital cycle attribution, detailed page-3 regulatory/competition/forex exposures and later annexure details. Full PDF extraction does not repair the pages 1–2 replay scope. Capex, concentration risk, accrual amount and watch/history remain unresolved rather than absent.

## EKC

Page 1 confirms ₹20 crore LT CARE BBB+/Positive assigned, ₹124 crore LT CARE BBB+/Positive reaffirmed with outlook revised from Stable, and ₹46 crore ST CARE A2 upgraded from CARE A3+. The ₹62.76 crore comparator is the prior facility amount, not a previous rating. D04's split-cell amount issue and combined-action ambiguity remain visible: the deterministic ₹124 crore event is queued, while the reviewed original A02 correctly states both actions. The accepted A03 retains the ₹46 crore amount that D04 cannot structurally bind; no parser value is silently patched.

Page 2 supports revenue/volume/realisation explanation, margin comparisons, **standalone** top-five customer concentration and historical consolidated debt metrics. Historical nil net debt is not projected into future years. Full source context retains the expected increase from debt-funded capex.

Page 3 confirms Adequate liquidity. Original A16 is withheld because blanket consolidated scope is unsupported and it omits the explicit FY26–FY27 repayment commencement. Original A15 similarly overextends accounting scope across operational cost/inventory observations. A13's capex scope remains under review. No cash, utilisation, accrual or repayment amount is promoted from those merged candidates.

Page 4 financial values visually match A17, but V1 downgrades that historical trusted-export record to `NEEDS_REVIEW`: it merges multiple periods into a single FY24 field and omits the audited versus unaudited qualification. It is not silently atomized. A18's facility details match the original annexure, including September 2029 maturity.

Material omissions: Egypt implementation/country risk; import/inventory explanation; future debt, liquidity and repayment conditions; agency outlook drivers; operational strengths; consolidation entity list; historical withdrawal. Annexure withdrawal is not a current action. The full financial table remains unaccepted until correctly attributed atomic candidates exist.

## Verification limits

Every accepted disclosure has exact, hash-bound source support and a validation result. Semantic decisions rely on explicit pinned source review; they are not certified by mere quote occurrence or benchmark agreement. Broad full-page context is deliberately retained, but paragraph-level candidate selection and normalized financial atoms remain unfinished. No unsupported or ambiguous candidate in the review queue is included in `claims`. Coverage ledgers distinguish supported findings from material omissions.

## Phase 2 addendum — 2026-10-24

The historical decisions above remain unchanged. [Phase 2](PHASE2.md) records direct page-3 source review and six new independently pinned EKC liquidity atoms. Cash, utilisation, accruals and current obligations are derived from A16; future commencement and the final rationale are explicit source-review additions. A16 remains WITHHELD. Paragraph-level locators retain the full qualifying liquidity context. This is implementation-agent review, not independent human certification.
