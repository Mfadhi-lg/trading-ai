# Trading AI Document Intelligence Feature Dictionary V1

**Task:** DI-01 · **Date:** 2026-10-26 · **Contract:** `document-intelligence-features/1.0.0-draft` · **Status:** design for review; no implementation or ML admission authorized.

## 1. Executive objective and boundaries

Convert financial statements, credit-rating reports, investor presentations and earnings-call transcripts into source-linked company intelligence, then independently validated, point-in-time ML inputs. Those inputs may later help research business durability, execution and financial risk within the governed Focus universe. Human reviewers decide Purple List inclusion. A disclosed management forecast is a fact about what management said, not verified future performance.

This dictionary defines **29 fields: 11 credit, 9 presentation and 9 transcript fields**. Of these, 27 are Layer 1 disclosures and two are Layer 2 calculations/comparisons. **Ten Layer 1 fields** form the proposed first extraction pilot. Financial statement observations remain owned by existing financial interfaces; this document does not duplicate their dictionary. Layer 3 predictions, scoring, models and investment decisions are out of scope.

Only this document is created. No acquisition, download, schedule, database, parser, experiment, inference, paid request, infrastructure or production change is part of DI-01. The user's explicit dictionary request authorizes this design step; it does not assert that every North Star cleanup gate has passed or authorize a subsequent pilot run.

## 2. Existing components: verified findings and reuse limits

“Verified” below means inspected repository source/contracts or an explicitly identified retained report. Historical measurements were read, not rerun; current production health and document availability were not inspected.

- [North Star](../../TRADING_AI_NORTH_STAR.md) establishes evidence, cutoff, sector and human decision boundaries. The [cleanup audit](../CLEANUP_AUDIT_2026-10-26.md) preserves canonical `scrapper/`, originals, experiments and governance dependencies; it certifies no cleanup execution or research readiness.
- Existing D03 canonical extraction supplies original-byte identities, pages/slides, elements, tables, native/OCR origins and layout references. [Phase 5 inventory](../phase5/README.md) identifies `scrapper/documents/extraction.py`, PDF/PowerPoint parsers, `documents/transcript.py` speaker segmentation and `documents/credit_rating.py` facility/tenor normalization. Reuse representations and provenance, subject to document-level validation. Native extraction completeness and table alignment are unproven.
- [`DocumentContext` / `ResearchEvidence`](../../scrapper/research/document_intelligence/schema.py) already represent original hashes, company/content IDs, dates, units, scope dimensions, speaker, parser/model version and statuses. [`evidence.py`](../../scrapper/research/document_intelligence/evidence.py) provides `canonical_hash`, `text_evidence`, `cell_evidence`, `validate_evidence` and `reconcile`. These check exact source binding and some normalization, and expose duplicates/conflicts. They do not prove semantic entailment, correct header association or exhaustive cross-document reconciliation. An image/element reference alone cannot support a numerical value.
- [Credit V1](../credit-rating-intelligence-v1/README.md), [output schema](../credit-rating-intelligence-v1/output.schema.json), [fresh atomic response schema](../credit-rating-intelligence-v1/fresh-response.schema.json) and `credit_atomic.derive_claims` provide atomic candidate boundaries, original-response retention, pinned review attestations, review queues and separate disclosure/evidence states. `credit_visual.py` and `credit_coverage.py`, described in [Phase 5 preparation](../credit-rating-intelligence-v1/PHASE5.md), bind original-PDF review and distinguish omissions from evidence failures. Reuse these concepts and validators after compatibility checks, not their broad 36-field agenda. Reviewed replay is not fresh-model accuracy; acceptance is partial coverage. [Phase 3 gaps](../credit-rating-intelligence-v1/PHASE3-GAPS.md) retain unresolved rating dates, financial-table headers and overbroad mappings.
- [`temporal.py`](../../scrapper/research/document_intelligence/temporal.py) supplies `available_at`, `eligible_at` and `compare_guidance`. Guidance comparison checks both records, company, management, metric, scope, horizon, unit and public chronology. Its capture-based eligibility can operate without a publication timestamp: **it is insufficient by itself for this dictionary's historical-public gate**. Extend the design at an adapter boundary later; do not reinterpret old statuses.
- [`research.features.financial`](../../scrapper/research/features/financial.py) exposes `company_quarter_features`, `CompanyQuarterFeatures` and `QuarterFeature`: explicit company/decision time/scope/version, nullable values, applicability, units, formulas and input lineage. Existing financial scope is explicitly standalone; consolidated document claims must not be joined to it silently. [Financial interface notes](../../scrapper/research/features/README.md) distinguish `PIT_SAFE_FROM_CAPTURE` from public historical proof and exploratory backfill.
- [`document_evidence`](../../scrapper/research/features/DOCUMENT_EVIDENCE.md) exposes recorded D09 readiness/check evidence through existing IDs and as-of time. Readiness is not content correctness or publication proof; its checks lack independent immutable transaction-time proof. [Focus governance](../../scrapper/research/governance/README.md) retains approved universe/label/identity/cutoff ownership. Positive membership requires sourced evidence; a nonmember is not a mentor rejection, and unknown/technical failure is not a negative. B2 draft thresholds remain superseded/audit-only. DI fields must not redefine Focus eligibility or historical labels.
- [Accuracy corrections](../phase5/accuracy-corrections.md) report valid citations alongside zero numeric recovery on an unseen Himadri holdout and no certified original public timestamps for 14 evaluated versions. Later regression recovery is not unseen validation. [Qwen/Gemini comparison](../../artifacts/three-document-reality-check-v0.4/challenger-comparison/qwen-vs-gemini-comparison.md) reports high narrow numeric recovery but weak complete citation support, missing source attribution, incomplete headers and lost qualifications. [Qwen rescore](../../artifacts/three-document-reality-check-v0.4/challenger-comparison/qwen-offline-rescore.md) finds attribution/scope/certainty defects without establishing an exhaustive hallucination rate. Do not claim all rejections are fabricated numbers. [Other-model results](../phase5/general-ai-experiment/RESULTS.md) distinguish schema/source matching from financial accuracy. No model winner is established for this contract.
- [Unified pilot evidence schema](../../experiments/unified_financial_pilot/schema.py) and [evaluation procedure](../../experiments/unified_financial_pilot/README.md) preserve atomic evidence and separate independent source labels from model proposals. Review attestation cannot mechanically prove reviewer independence. Reuse evaluation dimensions and exposure tracking; no historical experiment is a production implementation.

**Recommendation:** one versioned research export envelope referencing existing artifact/content/company/extraction IDs, with no database migration. Existing validators are reusable checks within a stricter admission contract, not a complete verifier.

## 3. Layers, observation grain and shared field specification

### 3.1 Three separate layers

**Layer 1 — verified disclosure facts:** source-supported reported values, agency opinions and management statements. “Verified” certifies extraction, attribution, period, scope and qualifications. Preserve `assertion_origin = issuer_reported | management_statement | rating_agency_opinion | analyst_question | analyst_interpretation`. Verification does not convert an opinion into an independently measured business fact. Analyst questions/interpretations are contextual evidence, never issuer guidance or admitted actuals.

**Layer 2 — derived and interpretive features:** deterministic calculations with versioned formulas and input IDs, or separately reviewed interpretations. This dictionary specifies only `ip_order_book_revenue_coverage` and `ec_guidance_revision` as independent Layer 2 fields. Numeric/category encodings of Layer 1 records are downstream transformations, not additional disclosure fields. No generic sentiment, moat, quality or pricing-power score is approved.

**Layer 3 — ML predictions:** future earnings growth probability, financial deterioration risk or relative quality ranking. No Layer 3 field, label, model or score is implemented or defined as ground truth here.

### 3.2 Mandatory inherited rules for every field

Each entry in section 4 consists of its local rules **plus all rules in this section and sections 5–8**. This inheritance is normative, including missing states, historical availability and verification; no entry may drop them.

- **Grain:** one `feature_name × company × source_version × period/effective_time × scope × relevant_dimensions` observation. Facility, tenor, rating scale, project, plant, product, segment, geography, metric basis and target horizon remain dimensions, not values concatenated into a company scalar. Repeated dimensions create records, not new dictionary fields. Preserve source taxonomy and any reviewed mapping version.
- **Period:** retain literal label plus resolved start/end, period kind (`instant`, `quarter`, `half_year`, `nine_month`, `fiscal_year`, `rolling_window`, `effective_event`, `target_interval`) and fiscal-calendar evidence. Report date is not reporting period or rating-action date. Unknown dates remain null. Project schedules may retain unresolved literal horizons; unresolved periods block time-based comparisons and ML admission.
- **Scope:** `standalone | consolidated | subsidiary | facility | segment | project | plant | unknown`, with legal-entity perimeter and business scope separately recorded. Facility scope also needs rated legal entity and agency analytical/consolidation approach. Record eliminations/JV inclusions when relevant. Never default to consolidated or infer whole-company coverage from a segment. Unknown material scope permits a review record, not a comparable ML value.
- **Types and normalization:** decimal strings for numbers; ranges use `low`, `high`, bound inclusivity and `approximate`; open bounds remain open, not guessed point estimates. Preserve original value, currency, unit, scale, sign and measurement basis. Money normalizes to base currency units: INR lakh ×100,000; INR crore ×10,000,000; million ×1,000,000. No FX conversion in V1. Ratios use `x`; percentages use percent points on 0–100 scale; changes use `percentage_points` or `basis_points` (100 bp = 1 percentage point), never a level. Negative growth/margins are possible. Impossible values or unusual ratios trigger review, not clipping. No midpoint, annualization or annual growth conversion by default.
- **Evidence/method:** candidate discovery may later use existing native/table/OCR/model paths. Every admitted pilot observation requires original-source human review plus deterministic identity, locator, normalization and schema checks. Model confidence, agreement, matching keywords or schema validity is never proof. AI confidence may be retained as diagnostic metadata only. OCR numbers and chart readings require original-image verification. Text extraction alone does not establish attribution or table relationships.
- **States:** every field uses the separate value, process, verification, coverage and temporal states in section 7. Zero is a disclosed value. `NOT_DISCLOSED` requires complete relevant-document absence review; unknown sector cannot yield `NOT_APPLICABLE`. Incomparable periods/scopes are separate records; incompatible same-grain disclosures are conflicts, not averages. Local rejection conditions supplement these rules.
- **History (H):** every entry requires a version-specific source availability receipt, capture time and source/review lineage. Admission to a historical cutoff also requires section 8; unknown publication cannot be repaired from a cover date. Event/revision fields require the prior/current evidence stated locally. Stale observations retain age, never become “current” automatically.
- **ML boundary:** “direct numeric” below means the verified, normalized disclosed number can be a candidate column after identity/applicability/cutoff gates, with its unit, basis, scope and state masks. It does not authorize materialization. Categorical, textual, event and structured-range records require a named, deterministic/versioned transformation or separate reviewed coding. No text-to-number LLM score. Null state masks remain distinct.

## 4. Prioritized intelligence dictionary

`P1` identifies the ten pilot fields. `P2` is the subsequent bounded dictionary backlog, not permission to implement. Credit ordering favors issuer/facility specificity; presentation ordering favors backlog and capital commitments; transcript ordering favors quantified guidance over broad narrative.

### A. Credit-rating reports — 11 fields

#### 01. `cr_long_term_rating` — P1 · Layer 1

- **Meaning/type/source:** raw categorical long-term grade assigned by the named rating agency to the named issuer/instrument/facility; credit-rating report. Preserve prefixes, modifiers, credit enhancement, provisional and issuer-noncooperating/watch qualifiers; it is not a company-wide credit score.
- **Unit/time/scope/sector:** agency domestic/global scale and long-term tenor; no numeric unit. Effective action date plus report observation date, separately. Rated legal entity, facility/instrument and analytical perimeter required. All rated sectors, including financials, with scale comparability checks; unrated status requires evidence rather than an empty search.
- **Evidence/verification:** full rating row with agency, instrument, tenor and header/footnotes; deterministic grade parsing plus original-page review. Split compound LT/ST cells only when each component is explicit and correctly associated.
- **States/history/ML:** shared states/H; unknown effectiveness permits a dated report observation but blocks current/as-effective rating. Categorical encoding requires a versioned agency/scale/tenor map; no cross-scale ordinal pooling.
- **Reject/review:** wrong entity/facility, lost minus sign, parent grade attributed to issuer, combined grade forced into LT, or missing material qualifier. Multiple agency grades are separate observations, not a conflict by default.

#### 02. `cr_short_term_rating` — P2 · Layer 1

- **Meaning/type/source:** raw categorical short-term agency grade for a specific instrument/facility; credit-rating report.
- **Unit/time/scope/sector:** preserve agency scale, ST tenor and suffixes such as `+`; effective date and report observation date; rated entity/facility/perimeter. All rated sectors, separately from LT.
- **Evidence/verification:** rating row, tenor/facility header and qualifications; grade parsing and original-page association review.
- **States/history/ML:** shared states/H; no ST rating inferred from an LT grade. Deterministic agency/scale-specific categorical encoding required; no LT conversion.
- **Reject/review:** missing suffix, commercial-paper grade assigned to bank facility, shared LT/ST facility amount counted twice, or effective date guessed from report date.

#### 03. `cr_rating_outlook` — P1 · Layer 1

- **Meaning/type/source:** raw agency opinion on outlook: preserve label and map only explicit `stable`, `positive`, `negative`, `developing` to a versioned vocabulary; credit-rating report. Other labels remain unmapped pending review.
- **Unit/time/scope/sector:** categorical/no unit; observation/effective dates; agency/scale/entity/tenor/facility linkage. All sectors with an applicable outlook regime; watch status is a separate qualifier, not synonymous with outlook.
- **Evidence/verification:** exact outlook clause/row and linked grade/facility context; deterministic mapping plus agency-attribution review.
- **States/history/ML:** shared states/H. An explicitly inapplicable outlook is `NOT_APPLICABLE`; absent outlook is not “stable.” Deterministic categorical encoding required.
- **Reject/review:** management business outlook substituted for agency outlook, watch-negative converted to negative outlook, or outlook copied across unrelated facilities.

#### 04. `cr_rating_action` — P2 · Layer 1

- **Meaning/type/source:** raw event: `assigned`, `reaffirmed`, `upgraded`, `downgraded`, `withdrawn`, `outlook_revised`, `watch_action`, or reviewed unmapped action; credit-rating report. Preserve previous/current grade and action wording when stated.
- **Unit/time/scope/sector:** event/no unit; explicit effective date and public observation date; same agency, scale, tenor and facility/entity. All rated sectors.
- **Evidence/verification:** action row and previous grade/history where available; agency normalization plus source review. An expressly stated upgrade can be accepted without inventing its absent prior grade; numeric notch movement cannot.
- **States/history/ML:** shared states/H; unknown effective date blocks event-timed ML. Event encoding or notch delta requires reviewed scale ordering and both comparable grades; assignment/withdrawal is not a notch delta. No change inferred from document frequency.
- **Reject/review:** assignment called upgrade, reaffirmation called improvement, historic action treated as present, or cross-agency comparison called movement.

#### 05. `cr_liquidity_assessment` — P1 · Layer 1

- **Meaning/type/source:** raw categorical agency liquidity classification plus supporting rationale atoms; credit-rating report. Preserve exact agency label; canonical mapping remains agency-specific, not a universal liquidity score.
- **Unit/time/scope/sector:** no numeric unit; assessment as-of date and each supporting actual/forecast period; agency analytical perimeter, cash/revolver restrictions and group access. All rated sectors where agency explicitly assesses liquidity.
- **Evidence/verification:** classification heading plus connected cash, accrual, debt-service, line headroom and conditional support passages where present. Review negation, restrictions, expected versus realized cash and continuation across pages. No classification inferred from isolated cash figures.
- **States/history/ML:** shared states/H; a classification with truncated material rationale remains insufficient evidence. Deterministic label encoding required; rationale stays attributed text. Do not derive cash/debt ratios from unreviewed support numbers.
- **Reject/review:** “adequate subject to refinancing” becomes unconditional adequate, sanctioned lines called available cash, or source opinion called independently verified solvency.

#### 06. `cr_interest_coverage_ratio` — P1 · Layer 1

- **Meaning/type/source:** raw numerical disclosed interest coverage with explicit numerator/denominator basis; credit-rating report. EBIT/interest and EBITDA/finance cost are different dimensions.
- **Unit/time/scope/sector:** `x`; exact annual/interim interval, actual/estimated/projected basis and legal-entity/accounting perimeter. Nonfinancial operating companies; conventional coverage generally not applicable to banks/lenders/insurers unless a separately approved sector definition exists.
- **Evidence/verification:** value, ratio label, method/definition, period columns, scope and units; Decimal normalization plus visual table/footnote review. Unknown formula remains a review disclosure, not a comparable ratio.
- **States/history/ML:** shared states/H. Explicit zero preserved; undefined denominator is not zero. Direct numeric only within the same verified basis; no recomputation using unrelated financial values.
- **Reject/review:** DSCR substituted for coverage, comparative-year swap, estimate relabeled actual, missing `x`, or agency-adjusted ratio merged with statutory ratio.

#### 07. `cr_leverage_ratio` — P2 · Layer 1

- **Meaning/type/source:** raw numerical disclosed leverage, dimensioned by exact basis: total debt/tangible net worth, TOL/TNW, net debt/EBITDA or other explicitly defined ratio; credit-rating report. These are never interchangeable columns.
- **Unit/time/scope/sector:** `x`; balance-sheet date and any earnings denominator interval; entity/accounting/agency-adjustment perimeter. Nonfinancials initially; financial-sector leverage requires a separate reviewed definition.
- **Evidence/verification:** ratio and numerator/denominator definitions, debt inclusions, period, table headers and qualifications; normalize and review. Debt amount in prose is support, not automatically a leverage ratio.
- **States/history/ML:** shared states/H; negative net worth/EBITDA or zero denominator goes to review for meaningfulness, not clipped or imputed. Direct numeric only for approved basis-specific columns; otherwise withhold.
- **Reject/review:** TOL/TNW labeled debt/equity, net/gross debt confusion, expected leverage called actual, or conditional future capex exclusion lost.

#### 08. `cr_working_capital_limit_utilization_pct` — P1 · Layer 1

- **Meaning/type/source:** raw numerical utilization of sanctioned working-capital limits, with `average | peak | point_in_time` statistic; credit-rating report. This is not cash conversion or working-capital intensity.
- **Unit/time/scope/sector:** percent, denominator limit and fund/nonfund facility basis; exact observation/window start/end; borrower, lender/facility aggregation and group scope. Nonfinancial borrowers with limit disclosures; unknown lending structure is unresolved, not automatic N/A.
- **Evidence/verification:** complete utilization sentence/table, statistic, window, limit definition and qualifiers; percent normalization and review. “Average maximum” requires literal preservation and adjudication, not guessed statistic.
- **States/history/ML:** shared states/H; unutilized facility is zero only if explicitly disclosed. Direct numeric within matched statistic/facility basis. Do not average lenders or infer headroom as unrestricted cash.
- **Reject/review:** average read as peak, 12-month window inferred from report year, peak omitted, or utilization above 100% silently clipped.

#### 09. `cr_agency_strength` — P2 · Layer 1

- **Meaning/type/source:** raw textual atomic strength cited by agency, such as customer relationships, market position or operating capability; credit-rating report. Store one supported proposition and its reasons, not a positive sentiment summary.
- **Unit/time/scope/sector:** text/no unit; assessment date plus any explicit historical/expected horizon; named entity/segment/perimeter. All rated sectors with sector context.
- **Evidence/verification:** exact agency passage, subject and qualifications; semantic review separates agency inference from underlying issuer-reported numbers. Industry commentary is not automatically issuer strength.
- **States/history/ML:** shared states/H; silence does not mean no strengths. Requires a frozen, human-adjudicated categorical taxonomy before any ML use; no count-of-bullets quality feature by default.
- **Reject/review:** agency opinion relabeled actual moat, promoter experience assigned to firm operating age, or omitted conditional support.

#### 10. `cr_agency_weakness` — P2 · Layer 1

- **Meaning/type/source:** raw textual atomic agency concern about concentration, cyclicality, execution, funding or other business/financial vulnerability; credit-rating report.
- **Unit/time/scope/sector:** text/no unit; assessment date and explicit affected interval; entity/segment/project/perimeter. All rated sectors, preserving sector-specific risk meaning.
- **Evidence/verification:** full concern and mitigating/conditional clauses; agency attribution and negation review. Generic methodology and disclaimers are not issuer risk disclosures.
- **States/history/ML:** shared states/H; no concern extracted is not “no risk.” Requires reviewed risk taxonomy and deterministic encoding; mitigants cannot erase the underlying concern.
- **Reject/review:** industry risk assigned to unrelated segment, favorable utilization tagged distress, or conditional downside presented as realized deterioration.

#### 11. `cr_rating_sensitivity_trigger` — P2 · Layer 1

- **Meaning/type/source:** raw event/structured conditional trigger for potential upgrade/downgrade: direction, metric/qualitative condition, comparator, threshold/range, duration and conjunctions; credit-rating report.
- **Unit/time/scope/sector:** preserve native units; normalize only explicit numerical thresholds using shared rules. Assessment date and forward condition horizon; agency/facility/entity/perimeter. All rated sectors.
- **Evidence/verification:** entire sensitivity including cross-page continuation, sustained-duration wording and AND/OR conditions; reviewed structured relation retains exact text.
- **States/history/ML:** shared states/H; unspecified thresholds remain text. Deterministic relation encoding required; distance-to-trigger is not approved without comparable actuals and a separate formula. A trigger is not a prediction.
- **Reject/review:** `above` becomes `below`, conjunction dropped, “sustained” omitted, or potential downgrade represented as an actual action.

### B. Investor presentations — 9 fields

#### 12. `ip_order_book_value` — P1 · Layer 1

- **Meaning/type/source:** raw numerical outstanding executable orders as defined by issuer; investor presentation. Preserve firm/provisional orders, cancellations, JV share, tax and unexecuted portion definitions; pipeline/opportunity size is separate context.
- **Unit/time/scope/sector:** currency/base units; exact stock as-of date; standalone/consolidated plus segment/project perimeter. EPC, capital goods, defense, infrastructure and other backlog businesses; recurring-service deal values qualify only with explicit executable backlog definition. Usually N/A for retail banking/FMCG without a comparable concept.
- **Evidence/verification:** number, backlog label, date, unit, scope and footnotes, including charts/headers; visual association review and normalization. “Orders worth” without outstanding/execution basis is unresolved.
- **States/history/ML:** shared states/H; zero only explicitly disclosed. Direct numeric within the same currency/definition; no carry-forward freshness assumption.
- **Reject/review:** bids called orders, gross group backlog called attributable share, inflows substituted for balance, or slide-wide currency/scope guessed.

#### 13. `ip_order_inflow_value` — P1 · Layer 1

- **Meaning/type/source:** raw numerical new orders booked over an interval, with gross/net-cancellation basis; investor presentation.
- **Unit/time/scope/sector:** currency/base units; exact quarter/YTD/year interval; entity/segment/project and attributable interest. Same backlog sectors as field 12.
- **Evidence/verification:** inflow label, interval, units and cancellations/one-off qualification; table-column/footnote review and normalization. An individual order award is not total company inflow.
- **States/history/ML:** shared states/H; direct numeric when definitions align. Never subtract YTD values or annualize unless a separately versioned transformation establishes compatible inputs.
- **Reject/review:** YTD reported as quarter, LOI treated as firm order, repeated announcement counted twice, or gross/net basis lost.

#### 14. `ip_order_book_revenue_coverage` — P2 · Layer 2

- **Meaning/type/source:** derived numerical backlog relative to trailing annual revenue: verified field 12 at date D divided by accepted revenue for the 12 months ending D. Primary anchor is presentation; denominator comes from the accepted financial statement interface. A disclosed issuer ratio is comparison evidence, not automatically this calculation.
- **Unit/time/scope/sector:** `x`; exact backlog instant and contiguous four quarters or fiscal year ending that same date. Match currency, accounting scope, segments and JV/tax basis. Backlog sectors only; unknown or incomparable basis blocks calculation.
- **Evidence/verification:** all numerator/denominator input IDs and source evidence; formula `order_book / trailing_12m_revenue`, Decimal arithmetic and independent reconciliation. No future quarter, interpolated revenue or segment/company mixing.
- **States/history/ML:** shared states/H on every input; zero numerator/positive denominator gives zero; denominator ≤0 gives unavailable/invalid denominator. Requires this versioned formula. Do not label `x` as guaranteed years of revenue.
- **Reject/review:** quarter revenue denominator, mismatched balance date, post-cutoff financial revision, or missing bridge between issuer and statutory revenue definitions.

#### 15. `ip_operating_capacity` — P2 · Layer 1

- **Meaning/type/source:** raw numerical installed/commissioned usable operating capacity; investor presentation. `installed`, `operational`, `effective` remain separate basis dimensions; planned additions remain future context.
- **Unit/time/scope/sector:** physical units and time denominator (e.g., tonnes/year, MW, beds, units/year); explicit as-of date; plant/product/segment/entity. Manufacturing, utilities, hospitals and capacity-based services; no universal capacity scalar across sectors.
- **Evidence/verification:** capacity figure with unit, plant/product and operational state; normalize only exact within-dimension conversions (e.g., thousand tonnes to tonnes), review commissioning/availability qualifiers.
- **States/history/ML:** shared states/H; idle capacity is not zero installed capacity. Direct numeric within identical physical/basis dimensions. Do not sum heterogeneous products, MW with MWh, or bed capacity with occupancy.
- **Reject/review:** planned capacity treated as operational, annual throughput called nameplate capacity, or capacity from different plants duplicated.

#### 16. `ip_capacity_utilization_pct` — P2 · Layer 1

- **Meaning/type/source:** raw numerical utilized share of explicitly defined capacity; investor presentation.
- **Unit/time/scope/sector:** percent; exact averaging interval and denominator basis; same plant/product/entity as capacity definition. Capacity businesses with meaningful utilization; unknown denominator requires review.
- **Evidence/verification:** utilization figure, capacity basis, period and operating constraints; source review plus normalization. No derivation from volume/capacity in V1.
- **States/history/ML:** shared states/H; direct numeric for matched basis. Above 100% may reflect debottlenecking or shifts and needs review, not automatic rejection or clipping.
- **Reject/review:** exit rate substituted for quarter average, aggregate utilization inferred from one line, or planned utilization called achieved.

#### 17. `ip_planned_capex_value` — P1 · Layer 1

- **Meaning/type/source:** raw numerical issuer plan for future capital expenditure; investor presentation. Preserve project total versus remaining spend versus incremental outlay, maintenance/growth basis and financing/approval conditions.
- **Unit/time/scope/sector:** currency/base units or structured range; explicit target interval/deadline and statement date; entity/project/segment plus attributable share. All sectors with explicit capex plans; acquisitions, loan growth and R&D expense are excluded unless explicitly part of disclosed capex definition.
- **Evidence/verification:** capex amount, spend basis, project, timing, approvals and funding conditions; normalize and review footnotes/continuations. Business expansion is context tied to project, not assumed capacity realization.
- **States/history/ML:** shared states/H; plan withdrawal differs from zero spend. Direct numeric for scalar comparable spend basis; ranges require deterministic bound columns. No allocation of multiyear total to individual years.
- **Reject/review:** cumulative budget treated as next-year spend, actual spend called plan, unapproved proposal called committed, or currency/range/remaining cost omitted.

#### 18. `ip_segment_revenue_share_pct` — P2 · Layer 1

- **Meaning/type/source:** raw numerical revenue percentage by issuer product/business segment; investor presentation. Record taxonomy and revenue denominator; volume/EBIT mix is not revenue mix.
- **Unit/time/scope/sector:** percent; exact revenue interval; accounting scope, named segment/product and elimination/netting basis. All sectors with meaningful segment disclosure, including financials only under an explicit revenue definition.
- **Evidence/verification:** chart/table labels, legend, denominator, units and period; each value-to-category association reviewed. Do not estimate unlabeled wedges.
- **States/history/ML:** shared states/H; incomplete categories remain partial, not “other = remainder” by default. Direct numeric in stable reviewed segment columns; taxonomy changes block automatic trend joins.
- **Reject/review:** stacked labels swapped, subsidiary sales double counted, share forced to sum to 100, or external versus intersegment revenue mixed.

#### 19. `ip_geographic_revenue_share_pct` — P2 · Layer 1

- **Meaning/type/source:** raw numerical revenue share by country/region or domestic/export grouping; investor presentation.
- **Unit/time/scope/sector:** percent; revenue interval; entity/segment and geography basis (customer destination, billing location or asset domicile). Applicable where geographic revenue split is disclosed; assets-by-country is not revenue.
- **Evidence/verification:** chart/table category, value, period and geography/denominator definition; normalization and original-slide review.
- **States/history/ML:** shared states/H; no exports is zero only with explicit disclosure. Direct numeric under stable reviewed geography/basis; overlapping regions are not summed.
- **Reject/review:** market-size geography called issuer sales, customer location confused with subsidiary location, or exports inferred from foreign-currency receipts.

#### 20. `ip_customer_concentration_pct` — P2 · Layer 1

- **Meaning/type/source:** raw numerical revenue share from top N customers or a named customer/group; investor presentation. Preserve N, grouping and customer/related-party definition.
- **Unit/time/scope/sector:** percent; revenue interval; entity/segment and revenue denominator. Customer-contract businesses; for banks, depositor/borrower concentration is a different metric and outside this field.
- **Evidence/verification:** concentration sentence/table including N and denominator; normalize and review. Customer logos/list length establish no numerical concentration.
- **States/history/ML:** shared states/H; absent named customers is not zero concentration. Direct numeric in distinct top-1/top-5/etc. columns; no conversion between N values.
- **Reject/review:** largest contract value mistaken for customer revenue, top-five treated as top-one, anonymous groups assumed independent, or customer base count used as concentration.

### C. Earnings-call transcripts — 9 fields

#### 21. `ec_revenue_growth_guidance_pct` — P1 · Layer 1

- **Meaning/type/source:** raw numerical forward revenue growth guidance expressly given/adopted by management; earnings-call transcript. Preserve `guidance | target | aspiration | conditional_expectation` stance; only explicit guidance is eligible for a guidance column.
- **Unit/time/scope/sector:** percent growth/range with YoY/QoQ/CAGR and nominal/organic/constant-currency basis; statement date, target interval and comparison base. Entity/accounting/segment scope required. All sectors with meaningful revenue growth; financial-sector income basis must be explicit.
- **Evidence/verification:** speaker identity/management role, full answer, antecedent question if needed, horizon and uncertainty. Numeric normalization and original-transcript review; “double-digit” stays text, not invented 10–99% bounds.
- **States/history/ML:** shared states/H; no guidance/refusal is not zero or withdrawal. Scalar direct numeric; ranges require preserved low/high columns; no midpoint. Unknown horizon or forecast basis blocks ML.
- **Reject/review:** analyst estimate adopted without explicit management confirmation, hopeful statement upgraded to firm guidance, actual growth called forecast, or source's refusal/conditions omitted.

#### 22. `ec_margin_guidance_pct` — P1 · Layer 1

- **Meaning/type/source:** raw numerical management guidance for a specified margin level or change, preserving EBITDA/EBIT/PAT, adjusted/statutory and revenue denominator; earnings-call transcript.
- **Unit/time/scope/sector:** level in percent or change in percentage points/bp; exact target interval, reference interval for change and statement date; entity/segment/accounting perimeter. Nonfinancials initially; bank NIM and insurer margins require separate sector metric definitions rather than generic mapping.
- **Evidence/verification:** full management answer, relevant question, metric definition, base and qualifications; normalize and review level-versus-change distinction.
- **States/history/ML:** shared states/H; scalar levels can be direct numeric in basis-specific columns; changes and ranges require deterministic distinct columns. A change without verified base cannot produce a level.
- **Reject/review:** 150 bp becomes 150%, PAT called EBITDA, gross margin substituted, “maintain” interpreted as growth, or adjusted margin silently merged with statutory.

#### 23. `ec_capex_guidance_value` — P2 · Layer 1

- **Meaning/type/source:** raw numerical management forward capex guidance; earnings-call transcript. Keep annual/project total/remaining spend, organic/acquisition and commitment status distinct.
- **Unit/time/scope/sector:** currency/base units or range; statement date and spend horizon; company/accounting/project/segment perimeter. All sectors with explicit capex guidance under field 17's exclusions.
- **Evidence/verification:** management attribution, complete answer/question, budget period, funding and approvals; normalize and review. Presentation and transcript statements remain separate source observations even if equal.
- **States/history/ML:** shared states/H; scalar direct numeric on matched basis; range needs bound columns. No double counting across sources, no annualization of project total.
- **Reject/review:** historical spend substituted for future budget, financing availability treated as capex, or management's qualification lost in a terse answer.

#### 24. `ec_demand_commentary` — P2 · Layer 1

- **Meaning/type/source:** raw textual management observation/expectation about demand and stated growth drivers, including volumes, customer spending, launches or expansion; earnings-call transcript. Explicitly distinguish observed orders/demand from forecasts.
- **Unit/time/scope/sector:** text/no unit; statement date plus observed/forward interval; entity/segment/product/geography. All sectors with sector-specific demand concepts.
- **Evidence/verification:** full management passage, question/antecedents and comparison basis; review driver attribution, negation, channel inventory and customer-versus-industry commentary.
- **States/history/ML:** shared states/H; silence is unknown. Requires reviewed taxonomy with observed/expected and direction dimensions before ML; no automatic sentiment or demand score.
- **Reject/review:** management optimism treated as realized sales, segment strength generalized to company, or recovery without specified comparison base coded as growth.

#### 25. `ec_pricing_power_evidence` — P2 · Layer 1

- **Meaning/type/source:** raw textual management claim concerning realized pricing, discounting, retention or contractual cost pass-through, with any quantitative support preserved as separate qualified atoms; earnings-call transcript. Field records evidence relevant to pricing power, not a Boolean conclusion that a moat exists.
- **Unit/time/scope/sector:** text/no unit; statement date and actual/expected pricing interval; product/customer/segment/geography and currency/mix basis. All sectors with meaningful price-setting; regulated prices need explicit regulatory context.
- **Evidence/verification:** full statement, cited magnitude/conditions where present and attribution; review realized versus proposed increases and price versus mix/cost effects.
- **States/history/ML:** shared states/H; no increase is not “no pricing power.” Requires independently reviewed coding; no scalar pricing-power score or causal inference from margin expansion.
- **Reject/review:** commodity inflation called discretionary pricing, proposed hike called realized, cost pass-through called margin expansion, or omitted volume/customer-retention tradeoff.

#### 26. `ec_competitive_positioning_claim` — P2 · Layer 1

- **Meaning/type/source:** raw textual management claim about market share, entry barriers, switching costs, qualifications or differentiation; earnings-call transcript. Preserve denominator/geography and independent-source references as references, not verified corroboration.
- **Unit/time/scope/sector:** text/no unit; statement date and claimed effective interval; entity/product/market/geography. All sectors; regulated licenses and customer approvals require contextual scope.
- **Evidence/verification:** complete management passage, specific supporting examples and limits; review market definition and promotional/superlative language. Industry market growth is not issuer market-share growth.
- **States/history/ML:** shared states/H; absence is unknown, not no moat. Requires reviewed claim taxonomy and separately collected corroboration before any business-quality interpretation; no automatic moat label.
- **Reject/review:** “among leaders” converted to number one, company claim called independent fact, or geography/segment restriction omitted.

#### 27. `ec_execution_commitment` — P2 · Layer 1

- **Meaning/type/source:** raw event/structured management commitment: named project/action, observable milestone, target date/window, responsible entity and dependencies; earnings-call transcript. Includes business expansion/commissioning commitments, not generic growth optimism.
- **Unit/time/scope/sector:** event with any amount/physical-unit target separately typed; statement date and deadline; project/plant/segment/entity. All sectors with observable milestones.
- **Evidence/verification:** full answer/question and dependencies; review firm commitment versus aspiration, commercial production versus trial/approval and calendar versus fiscal year.
- **States/history/ML:** shared states/H; unknown deadline remains review-only for timing features. Requires deterministic event coding. Execution success/failure requires later independently accepted actual evidence and is not asserted here or backdated into predictors.
- **Reject/review:** “hopefully” removed, approval treated as production, fiscal/calendar year guessed, or later completion used to rewrite original commitment certainty.

#### 28. `ec_operating_risk_disclosure` — P2 · Layer 1

- **Meaning/type/source:** raw textual management disclosure of supply-chain, customer, regulatory, financing or execution constraints and mitigants; earnings-call transcript. One risk proposition per atom, including realized/potential status.
- **Unit/time/scope/sector:** text/no unit; statement date and affected interval; entity/project/product/geography. All sectors with sector-specific taxonomy.
- **Evidence/verification:** full risk statement, conditional language, mitigants and attribution; review company specificity. Analyst allegations stay questions unless management explicitly confirms.
- **States/history/ML:** shared states/H; no risk mentioned is not risk-free. Requires reviewed taxonomy and deterministic actual/potential coding; no automated severity score.
- **Reject/review:** hypothetical downside called current disruption, supplier issue generalized to all production, or “no material impact” omitted while preserving only the threat.

#### 29. `ec_guidance_revision` — P2 · Layer 2

- **Meaning/type/source:** derived event comparing two verified management guidance statements for identical metric/basis, target horizon, currency/unit and scope: `increased`, `decreased`, `unchanged`, `withdrawn`, `mixed_bounds`, `incomparable`. Transcript anchor; prior transcript or presentation admissible with explicit management guidance attribution. A revision is numerical direction, not an investment judgment.
- **Unit/time/scope/sector:** event plus old/new bounds and changes in percent points or native currency units; both statement/public dates and same target interval; same entity/accounting/segment. All sectors with comparable approved guidance definitions.
- **Evidence/verification:** both full source records and qualifiers; deterministic comparison plus review. Scalar is equal bounds. Increased means both bounds nondecreasing and at least one strictly higher; decreased is converse; equal bounds unchanged; opposing bound moves `mixed_bounds`. Explicit withdrawal requires source evidence; missing new guidance never establishes withdrawal.
- **States/history/ML:** shared states/H for both inputs; unknown prior statement gives insufficient evidence, not “new guidance.” Requires versioned comparison/encoding; both inputs must predate decision cutoff, and chronological public ordering must be established. Open bounds without comparable ordering remain incomparable.
- **Reject/review:** full-year versus next-year comparison, organic versus reported growth, range midpoint substituted, rhetoric change treated as numerical revision, or later statement leaking into earlier features. Broader commentary change requires separate reviewed interpretation and is not this event.

## 5. Minimum evidence contract and integration boundary

The following is a **proposed export contract**, not a new SQL schema or an assertion that existing records contain every field. Missing required provenance may be retained for research review with explicit reasons; it cannot pass the corresponding admission gate.

### 5.1 Source identity and provenance

- `contract_version`, `feature_name`, immutable `observation_id`, `candidate_id/hash`, and `source_version`. Candidate changes require a new hash and review; retain original candidate and any reviewer addition separately.
- `company_id` with namespace, issuer legal name, security/ISIN/symbol where known, and identity-mapping evidence/version. Existing research symbols are not invented production numeric IDs. Unresolved issuer/subsidiary/parent mapping is quarantined before company-quarter joining.
- Existing `document_id/artifact_id`, `content_id`, extraction ID when present; original filename/URL, **original bytes SHA-256**, canonical representation hash and actual byte retrievability. A canonical-text hash cannot replace original hash. PDF and HTML versions are separate artifacts.
- `document_type`, source institution/publisher, issuer, rating agency or transcript publisher as applicable. Publisher identity and speaker/assertion origin are separate. Distinguish official transcript from machine-generated transcription; first pilot uses official issuer/exchange transcripts, with generated sources excluded from primary acceptance.
- Distinct `report_date`, `source_publication_at`, `captured_at`, `ingested_at`, `extracted_at`, `reviewed_at`, and publication-receipt identity/hash/method. Use aware UTC timestamps, retain original timezone/precision and any uncertainty interval. Unknown values stay null with reasons; never substitute a financial period or PDF metadata date.

### 5.2 Claim and supporting source

- `assertion_origin`, `statement_kind` (`actual_reported`, `agency_assessment`, `guidance`, `target`, `aspiration`, `conditional_expectation`, `question`, `interpretation`), speaker name/role and source-cited role evidence where applicable.
- Literal and normalized value, unit/currency/scale, range/operator/sign, metric basis, actual/estimated/projected basis, period/horizon, entity/accounting/business scope and dimensions required by its dictionary entry.
- One-based physical page/slide; printed label separately; element and exact text offsets or table ID/row/column plus **all necessary labels, headers, units, footnotes and continuation references**. Transcript speaker turn/section, answer and relevant preceding question; timestamps/line IDs if available. Exact supporting text is retained, not just a paraphrase or page number.
- For visual evidence: original hash, full page-image hash, renderer/version, dimensions, coordinate system and bounding box/crop hash; crop must contain the complete supporting context. For OCR: text origin, OCR version and reviewed original image. Unlabeled chart heights cannot produce exact numbers.
- `qualifications`, conditions, negations, approximation, exclusions, dependencies, unresolved reasons, contradiction group and linked alternative observations. Split compound statements into independently scoped atoms; shared passages may support several atoms, but one scalar/period may not stand for all numbers in prose.
- Parser/model/provider/prompt/configuration versions when used, canonicalization/transformation version, run ID and retained raw response reference. Use explicit `not_used` for model metadata where appropriate; do not fabricate a model version for deterministic extraction.

### 5.3 Verification record

Preserve separate `identity_check`, `locator_integrity_check`, `normalization_check`, `semantic_support_check`, `attribution_check`, `period_scope_check`, `qualification_check` and final decision, with reviewer identity/time, method, candidate/source hashes and reason. Decision states: `PENDING`, `VERIFIED_DISCLOSURE`, `NEEDS_REVIEW`, `REJECTED`. Verification requires all relevant checks; a skipped check is not a pass. Reviewer additions are attributed to the reviewer and never credited to model extraction recall.

Retain legacy statuses unchanged. An old `VERIFIED` or `EVIDENCED_STATEMENT` is an input to revalidation, not automatic `VERIFIED_DISCLOSURE`. `NOT_FOUND`/`UNKNOWN` is not certified absence. Exact locator integrity cannot prove entailment, full coverage, reviewer independence or original source truthfulness.

### 5.4 Integration without production schema changes

Later, an isolated sidecar/export may reference canonical document tables (`document_artifacts`, `artifact_contents`, extractions/pages/elements) and immutable evidence IDs. No duplicated document store is needed. Proposed feature records carry `company_id`, decision cutoff, period, reporting scope, feature/transformation version, nullable value, status and input IDs, analogous to `QuarterFeature`. Rich evidence and categorical/event/range dimensions stay in the sidecar; do not squeeze them into the existing scalar interface or alter its statuses.

Join only on verified identity, compatible period/scope and eligible source version. Financial statements remain authoritative for the accepted financial denominator, but an agency-adjusted ratio or issuer-reported alternative measure is retained with its own basis rather than overwritten. No blanket source precedence resolves conflicts. The document contract adds inputs; document absence must not invalidate otherwise accepted financials, decide Focus eligibility, or invent Purple List/mentor labels.

## 6. Sector applicability and feature selection rules

Applicability is determined per company/segment and as-of date using evidenced business activity and an approved taxonomy/version, not only today's exchange sector. Unknown historical sector gives `APPLICABILITY_UNRESOLVED`. Record sector mapping evidence and approval; do not infer N/A from missing disclosures.

- **Backlog businesses:** order book/inflows/coverage apply when executable orders have a stable definition. Pipelines, assets under management, retail loan books and market opportunity are excluded from backlog features. Realty bookings require separate definition review before admission.
- **Manufacturing/capacity businesses:** preserve physical dimensions, plant/product basis and operational state; utilization across heterogeneous products is incomparable. Hospitals/utilities may use sector-specific units; a sector-specific mapping must precede ML columns.
- **Banks, NBFCs and insurers:** agency ratings/outlooks/liquidity and attributed narrative can apply. Industrial interest coverage, leverage, order coverage and generic EBITDA margin guidance do not automatically apply. Use explicit sector-specific financial definitions in a later contract; do not repurpose deposit concentration as customer revenue concentration.
- **Services/asset-light firms:** reported backlog can apply only when scope and executability are explicit. Planned capex may apply; no capacity figure is inferred from headcount. Low capex is not missing or N/A if a zero/small budget is expressly disclosed.
- **Conglomerates:** segment applicability does not imply consolidated comparability. Keep segment records separate; no sum or weighted rollup without compatible units, eliminations and a versioned formula.

Statistical usefulness remains an untested hypothesis. Prefer stable definitions within a company/sector before cross-sector comparisons. No sector-inapplicable feature may be zero-filled or contribute to a quality score.

## 7. Missing, conflict and process behavior

Use orthogonal states so, for example, an extracted zero can be verified yet unavailable at a historical cutoff.

**Value state:** `DISCLOSED` (including exact zero), `NOT_DISCLOSED`, `NOT_APPLICABLE`, `UNRESOLVED`, `CONFLICTING`. Non-disclosed states carry null normalized value; preserve incompatible candidate values in their evidence records, not a chosen scalar.

**Process/reason:** `NOT_SEARCHED`, `DOCUMENT_UNAVAILABLE`, `SOURCE_NOT_COVERED`, `EXTRACTION_FAILED`, `MISSING_CANDIDATE`, `INSUFFICIENT_EVIDENCE`, `PERIOD_UNRESOLVED`, `SCOPE_UNRESOLVED`, `APPLICABILITY_UNRESOLVED`, `INVALID_DENOMINATOR`, or explicit reviewed reason. More than one reason may apply. A malformed/empty model response is extraction failure/missing candidate, not absence.

**Coverage:** `NOT_ASSESSED | PARTIAL | COMPLETE_RELEVANT_SOURCE_REVIEW`. `NOT_DISCLOSED` requires reviewer, source/catalog hashes, every relevant page/slide/turn and annexure/continuation checked, no unresolved layout gaps capable of hiding that metric, and a defined document/period search universe. It means absent from that reviewed source set, not absent everywhere. Positive extraction alone establishes partial coverage. `NOT_APPLICABLE` needs applicability evidence and reviewer approval.

**Temporal state:** `ELIGIBLE | AFTER_CUTOFF | PUBLIC_AVAILABILITY_UNKNOWN | CAPTURE_AFTER_CUTOFF | VERSION_UNRESOLVED | STALE_OR_FRESHNESS_UNASSESSED`, with a reason and policy version. Multiple reasons may be retained. This state does not erase a verified source value.

**Conflict handling:** first distinguish different agency/facility, actual/forecast, metric basis, period, scope, provisional/final, currency and revisions. Those usually represent distinct observations. True incompatible same-grain claims retain both sources and become `CONFLICTING`; neither feeds a scalar ML column until adjudicated. A correction supersedes only from its defensible availability time and with a linked reason; source order, newest capture, majority vote and model confidence do not resolve it. Derived features inherit blocked state from any required input. Never average conflicting values.

**Illustrative examples (invented, not company facts):** an explicitly disclosed order book of ₹0 is numeric zero; a missing order-book candidate after parser failure is `UNRESOLVED/EXTRACTION_FAILED`; reviewed absence is `NOT_DISCLOSED`; a bank without a comparable backlog concept can be `NOT_APPLICABLE` after sector review. A ₹2,800 crore backlog normalizes to INR 28,000,000,000. Two incompatible same-date/scope backlogs remain a conflict. A correctly extracted 15–18% growth guide published after cutoff remains `DISCLOSED + VERIFIED_DISCLOSURE + AFTER_CUTOFF`, with no historical feature value.

## 8. Historical cutoff and version safeguards

Two clocks must remain distinguishable: when exact source information was publicly available, and when our system possessed/accepted it. Financial reporting period, document cover date, HTTP Last-Modified, PDF creation date and today's URL are insufficient public availability evidence on their own.

1. **Bind public evidence to exact version.** Preserve an issuer/exchange/agency publication or filing receipt tied to content/version, with URL/source, timestamp/precision and receipt hash. If only a date is defensible, use the latest possible instant in the documented publisher timezone/uncertainty interval; no same-day intraday eligibility before that bound. Unresolved timezone/version binds stay blocked.
2. **Default to strict captured replay.** At decision cutoff T, require certified public availability, original-byte capture and any relevant source acceptance/ingestion timestamps ≤T, with immutable lineage. Later capture cannot be backdated. Existing capture-only interfaces remain capture-only; their status does not satisfy public certification. Missing publication gives `PUBLIC_AVAILABILITY_UNKNOWN` even if captured early.
3. **Separate replay modes.** A source recovered today with independently certified historical public version may eventually support an explicitly approved `PUBLIC_INFORMATION_RECONSTRUCTION`, but remains distinct from strict captured replay. This dictionary authorizes no such historical admission and no assumption that today's source was identical then.
4. **Separate source and feature knowledge.** Record extraction/review/transformation times even when source predates T. A retrospectively built export must say `RETROSPECTIVE_SOURCE_RECONSTRUCTION`, with frozen rules and blind evaluation; it is not proof that a live feature existed at T. Operational feature replay additionally requires verification/materialization ≤T. Do not fit transformations or select features using held-out future outcomes.
5. **Replay revisions as of T.** Choose only versions available under the selected policy. Later restatements, corrected transcripts, amended ratings and later contradictory claims cannot rewrite an earlier snapshot. Duplicate pages/URLs do not create independent corroboration. Rating effective date remains separate from publication: a future-effective action may be a known announced event, not the effective rating at T.
6. **Do not silently roll forward.** Company-quarter attachment uses explicit observation date and latest eligible matching record, with source age and freshness policy. A rating is an as-of observation unless a validated lifecycle establishes effectiveness/currentness; withdrawal is not a numeric zero. Annual numbers do not become quarterly facts. No unapproved age limit or forward fill is assumed.
7. **Derivations inherit every input cutoff.** Coverage ratio and guidance revision need all source inputs available, compatible and verified; availability is at least the maximum required input availability. No future revenue denominator or later guidance may enter earlier rows.
8. **Keep labels/universe separate.** Join governed historical Focus membership/decision cutoffs and identity only when evidenced as of that time. Unknown/nonselected/explicitly rejected remain distinct. Later mentor/Purple decisions and actual outcomes belong to later labels, never disclosure facts or historical predictors.

These are recommended admission requirements. Existing retained reports demonstrate timestamp gaps; this design does not claim to have repaired them.

## 9. First extraction pilot: ten fields and observable tests

The first pilot comprises **01, 03, 05, 06, 08, 12, 13, 17, 21 and 22**. Ratings/outlook/liquidity test explicit agency statements; coverage/utilization test financial-risk definitions and windows; backlog/inflows test stock-versus-flow; capex tests commitments and horizons; growth/margin guidance test management attribution, ranges and basis. These are plausibly useful and verifiable in suitable sectors. Actual cross-document availability remains to be measured; no corpus-wide prevalence is claimed.

### 9.1 Shared acceptance protocol

Before running a later pilot, freeze exact originals, document inventory/exposure, cutoff policy, parser/configuration, dictionary version and independently authored source labels. Proposed minimum is 12 originals (four per type) across at least four issuers and two suitable nonfinancial sector groups, plus a financial-sector N/A control; require at least two reporting periods for two issuers. Reserve at least one previously unseen issuer's documents from prompt/rule development. Include ranges, negation/conditions, multi-column/table or chart layouts and an absent/failed/late-source case. Known evaluated documents serve regression only. If real sources lack a required case, record the gap; illustrative fixtures do not count as real-document accuracy.

**Proposed gate, frozen before viewing blind outputs:** at least five independently labeled positive disclosure instances for each pilot field, and at least two field-relevant negative/ambiguous controls. A field with fewer cases is inconclusive. Measure candidate-only and post-review results separately. Require ≥80% candidate recall per field on these frozen labels, 100% final admitted material-value/unit/attribution/period/scope/qualification correctness, and zero unsupported admissions on controls. Report numerator/denominator, abstentions, partial coverage and reviewer additions per field; do not aggregate away a failed field. Small-sample passage establishes feasibility only, not production accuracy. A missed recall threshold narrows/revises the pilot rather than authorizing guesses.

**Numerical standard:** exact source token/bounds/sign/operator and exact deterministic unit conversion to disclosed precision; no tolerance for crore/lakh, basis-point, year-column or decimal-placement errors. Approximate disclosures retain approximation; they are not judged against unknowable unrounded actuals. **Attribution:** correct issuer/agency/speaker and stance. **Period/scope:** exact interval, basis and perimeter, or explicit abstention when unresolved. **Qualifications:** all material restrictions/conditions included, even where the number is correct. Every accepted instance must reopen to complete original support, not merely a valid page or substring. A missing required check or material error withholds the observation.

### 9.2 Field-level pilot acceptance criteria

1. **`cr_long_term_rating`:** recover exact agency grade/modifier and correct LT facility/entity; physical row and qualifiers validate. Combined LT/ST control must split correctly or abstain; no facility amount double count. Unknown effective date remains unknown and blocks effective/current-rating use. Wrong minus sign or agency attribution fails the instance.
2. **`cr_rating_outlook`:** reproduce exact agency outlook and link to the correct grade/facility/date. Management “positive outlook” and credit-watch controls must not become rating outlook. Missing outlook remains missing/N/A only with evidence; never default stable. Preserve noncooperating/provisional qualifications when relevant.
3. **`cr_liquidity_assessment`:** exact agency classification plus all material supporting conditions/restrictions; reviewer checks full liquidity section and continuation. A statement conditional on refinancing must retain that condition; unutilized bank lines cannot become cash. Correct category with missing condition fails.
4. **`cr_interest_coverage_ratio`:** exact ratio/`x`, actual versus estimate, fiscal/interim column, analytical scope and numerator/denominator basis. DSCR and swapped-year controls are rejected. A value whose basis cannot be verified is withheld from comparable ML inputs, even if its digits match.
5. **`cr_working_capital_limit_utilization_pct`:** exact percentage/range, average/peak/point statistic, window and limit/facility scope. Preserve qualifiers and denominator basis; a peak mislabeled average fails. Explicit zero passes as zero; ambiguous statistic/window requires abstention.
6. **`ip_order_book_value`:** exact value, currency/scale, as-of date and entity/segment scope; executable/firm status and tax/JV/cancellation qualifications preserved. Opportunity pipeline or inflow control must not enter backlog. For an illustrative ₹2,800 crore source, normalized INR must equal 28,000,000,000 exactly.
7. **`ip_order_inflow_value`:** exact value/unit and interval, gross/net-cancellation basis and entity/segment. YTD versus quarter and individual order versus total inflow controls remain distinct. Repeated order announcements cannot inflate accepted values; complete footnotes required.
8. **`ip_planned_capex_value`:** exact currency amount/range and future spend horizon; project total/remaining/incremental basis, entity/project and approval/funding conditions retained. Historical spend and multiyear total controls cannot become next-year guidance. No ungrounded annual allocation.
9. **`ec_revenue_growth_guidance_pct`:** exact low/high or scalar, growth basis, comparison interval and target horizon with source-cited management role. Analyst question and aspiration controls must remain separate. Illustrative “15–18%, subject to approvals” must retain both bounds and condition; a lone midpoint fails.
10. **`ec_margin_guidance_pct`:** exact margin metric and level/change basis, range, horizon and scope. A 150 bp improvement stays a 1.50 percentage-point change; it cannot become a 150% margin or an inferred level without verified base. Preserve management uncertainty and adjusted/statutory basis; analyst-only numbers fail.

### 9.3 Acceptance for the remaining fields

Every P2 field must pass the same evidence/attribution/period/scope/qualification gates before later admission, plus its section 4 rejection tests. Specifically: ST grades preserve tenor/suffix; rating actions preserve action/history; leverage preserves formula; strengths/weaknesses remain agency opinion; triggers retain comparator/duration/conjunctions; capacity/utilization preserve physical denominator and operating state; segment/geographic/customer shares preserve category/N/denominator and do not force totals; capex guidance preserves spend basis; demand/pricing/competition/risk preserve attribution, observed/expected status and limits; commitments preserve milestone/deadline/dependencies. The two derived fields must reproduce exactly from eligible input IDs, reject incompatible/invalid inputs and pass revision/cutoff replay. These are contract acceptance conditions, not tests run in DI-01.

## 10. Open decisions and readiness

**Enough information exists to design and prepare a small real-document verification pilot after dictionary review. Enough evidence does not yet exist to certify historical ML inputs or production extraction.** Do not start extraction as part of this task.

Foundational requirements to resolve for the next task:

- Freeze a real-source inventory with retrievable original bytes/hashes and identity/perimeter mapping; check mounted/archive availability rather than relying on prior paths. Establish publisher/official-transcript provenance and document exposure/holdout status.
- Obtain version-bound public availability receipts and defensible capture/ingestion lineage. Choose replay mode explicitly. Unknown publication is acceptable for an extraction-only feasibility case clearly excluded from historical ML, not for historical feature admission.
- Assign independent human source labeling/adjudication, freeze acceptance thresholds and labels, and cover omissions as well as emitted candidates. Existing agent-reviewed references and historical source attestations do not automatically meet independence requirements.
- Approve the minimal export-to-existing-ID mapping and validator compatibility after this contract is challenged; determine final status vocabulary in an adapter without modifying legacy semantics. No new persistence decision is needed to approve this dictionary.
- Approve historical sector/perimeter mapping, freshness policy and dimension-specific transformations before any company-quarter materialization. Resolve standalone financial versus consolidated document joins through explicit compatible sources, never a scope default.

Definitions intentionally awaiting narrower decisions:

- Agency-specific rating/outlook/liquidity mappings, enhanced/noncooperating treatment and facility aggregation/current-rating lifecycle. Raw labels are defined now; universal ordinal scales are not.
- Which interest-coverage/leverage formulas are sufficiently documented and frequent to support comparable columns; financial-sector alternatives remain excluded pending their own contract.
- Backlog executability, JV/tax basis and revenue reconciliation by sector; field 14 deliberately requires a matching-date, matching-scope denominator rather than a guessed bridge.
- Stable capacity units, product/segment/geography taxonomy changes and concentration customer grouping; no cross-sector canonical scalar is assumed.
- Treatment of open-ended qualitative guidance, comparison of targets versus formal guidance and financial-sector margin concepts. Raw wording is retained; numeric conversion/stance promotion is prohibited.
- Reviewed qualitative taxonomies for demand, pricing, competition, agency assessments and risks. These fields are useful evidence contracts, but are not yet approved numeric ML features or moat judgments.

**Stop condition reached:** dictionary only. No source extraction, inference, API call, implementation, scoring, training, migration, production integration or pilot execution follows DI-01.
