# TRADING AI — MASTER ROADMAP V4
## Existing financial data → First Focus engine → Experimental ML → Document intelligence

**Date:** 29 September 2026 (IST)  
**Status:** Single authoritative coding-agent roadmap; **execution must be gated**, not launched as one giant assignment.  
**Supersedes:** `TRADING_AI_SINGLE_MASTER_ROADMAP_2026-09-29(1).md` (v3), the earlier master handoff, and the standalone Upstox feasibility pilot. Archive earlier plans to avoid conflicting instructions. Preserve existing code, databases, source records, review patches and test evidence.  
**Reporting quarter:** FY27 Q2, 1 July–30 September 2026; most corresponding company results become available later.  
**Primary objective:** Put the already collected standalone financial history to work, release an explainable fundamental Focus system, determine if a supervised ML selector is *actually supportable*, and systematically improve **missing/late supporting documents** without rewriting the financial acquisition system.

> **Owner-confirmed premise:** Financial data for the intended company universe is already scraped. The unresolved coverage problem is supporting **investor presentations (PPT), concall recordings/transcripts, credit-rating reports and related research documents**. Do **not** initiate another all-company financial-data acquisition project, a replacement financial database, a broad official XBRL migration or a full Upstox pilot. Small checks that financial records are usable, correctly scoped and time-aligned are still essential for trustworthy ML; **do not recast those checks as a new financial collection campaign**.

> **Current release status:** A0 was reported completed before the latest incident. A2 has a tested **review patch only**, not an approved or operational deployment. An unapproved bind-mounted code change triggered **migration 065** on the live local database; the agent reported **two backfilled BSE BLOCKED events**. The old application source was restored and no service was restarted; therefore A2 retry suppression **is not live**. A2's reported final offline verification passed **152 tests and 20 subtests**, plus `git apply --check`, but used host Python 3.14.5 rather than live Python 3.12.14 and did not exercise the complete pinned dependency environment. Treat all this as **agent-reported until checked against actual local artifacts/runtime**. BSE machine access remains blocked and permissions remain unverified. Do not imply approval or successful live deployment.

---

# 1. EXECUTE IN THIS ORDER

**Stop giving the coding agent the entire roadmap as one task. Issue one bounded assignment at a time.** Independent development may proceed on an isolated database copy while approved live collection continues.

| Order | Actual roadmap ID | Priority | Action | What must exist before advancing |
|---|---|---|---|---|
| **0** | **A0 residual safety** | Mandatory, tightly bounded | Verify the consequences of migration 065, take a **new** consistent backup of the *current* database if needed, and make development fully isolated from production bind mounts. Do not turn this into another multi-day re-audit. | No unexplained ongoing live mutation; isolated cloned working environment; serious risks escalated. |
| **1** | **B0** | **Start immediately** | Read-only **ML usability** check of existing financial history, timestamp fidelity, sample accuracy and actual Focus-label inventory. Financial collection completeness is **not** the central task. | A compact data card, cutoff limitations, label inventory and exact feature contract. |
| **2** | **B1** | **Primary code work** | Build/reuse an existing-observation → company-quarter financial-feature exporter. No new source integration. | End-to-end tested feature output on a small, representative existing-company cohort. |
| **3** | **B2** | **Primary code work** | Owner approves and freezes explicit rules; implement the deterministic Focus engine and a first sample shortlist. **Prepare the rules while B1 is built.** | Source-grounded `PASS/FAIL/REVIEW/INSUFFICIENT_EVIDENCE` with reasons and immutable rule version. |
| **4** | **B3** | Conditional experimental ML | Train/compare small interpretable models **only if real dated positives and reviewed negatives exist**. Otherwise build the label-capture process and keep the rules engine. | Honest `GO / EXPLORE_ONLY / NOT_READY` study against the frozen rules, with leakage controls. |
| **5** | **B4** | Live Q2 release | As verified Q2 results arrive, generate timestamped quantitative selections. **Do not wait for B3**; B4 may start as soon as A1/A3 + B1/B2 are safe. | A demonstrated new-result/revised-result release with reproducible cutoff and missing-document status. |

**Run in parallel, without delaying B0–B2:**

- **A1 + essential A3:** Maintain existing permitted Q2 collection and verify high-impact integrity failures (wrong quarter, partial refresh overwriting old data, same-quarter corrections). Keep the live worker running under the current approved code; changes require separate safety approval.
- **C0:** Inventory already stored supporting documents and genuine missing/unknown/issued-but-uncollected cases. Start read-only on the isolated copy as soon as it exists. Do not read/LLM-process all 180 GB.
- **D0:** Use C0 evidence to prioritize **document-only** recovery. Begin D1 on selected valuable gaps after its dependencies and permission checks, not on every company at once.
- **A2:** Keep the reviewed patch safely staged. Investigate the migration incident and perform production-version compatibility tests in a **separate short safety work item**. Schedule deployment later with explicit approval; do not block B0–B2 on a BSE-403 quality-of-life patch if the baseline system is safe.
- **C1 → C2:** Run narrow, verified document intelligence on the existing corpus after C0; compare financial-only vs document-enriched results once B2 exists.

**One-agent practical sequence:** `A0 incident containment (short) → B0 → B1 → B2 → B3 if labels permit`; slot `C0` after B0 or delegate it in parallel. Begin `B4` when new results and B1/B2 are ready. Return to A2 deployment and targeted D1 recovery on separate approved changes.

**Parallelism is permission, not a demand.** A lone agent must never modify the production worker while preparing a development patch.

---

# 2. NON-NEGOTIABLE OPERATING RULES

1. **No production changes without explicit approval.** Running workers must not auto-discover review-only migrations. No live `init-db`, unapproved service restart, blind migration replay or production bind-mounted development.
2. Reuse existing company/ISIN registry, accepted financial rows, Screener historical observations where permitted, scheduler, artifact inventory, content hashes, document downloads, extraction, recovery jobs and `scrapper/commands/quarter_documents.py`.
3. Create **one** financial feature interface and **one** document-evidence interface. Do not duplicate databases, attachment jobs or pipelines. Retain evidence from multiple sources and immutable original/corrected versions.
4. Do not infer that every company issues every document. **Not issued, not yet issued, discovered only, published but not collected, blocked, corrupt, wrong period, successfully collected and unknown** are distinct states. A missing optional document must **not** automatically disqualify a company.
5. Use standalone financials, actual reporting periods, original units, company/period IDs, known publication and first-observed timestamps, and verified revision history. No future values in earlier decisions. A present-day historical page is *not* inherently point-in-time.
6. Financial-only results are independently useful. Documents add an **optional, later research-enriched release**; they never retroactively rewrite an earlier quantitative decision.
7. Separate **mentor/owner Focus-selection imitation** from **future stock-return prediction** and later technical trading. A model matching a few old lists is not proof of profitable alpha.
8. Source access and storage must be permitted for the intended use. Do not attempt to defeat BSE/NSE or issuer-site access controls; do not assume a publicly visible file licenses bulk machine acquisition.
9. Every task returns actual implemented/reported status, concrete current code paths, tests, remaining gaps and next smallest task. Stop expanding plans unless a verified defect requires a change.

---

# 3. STREAM A — SYSTEM SAFETY AND INCOMING Q2

## A0 — Existing production audit + bounded migration-065 incident containment

**Status:** Earlier audit reportedly completed; **incident addendum outstanding**. Do not repeat the old full audit unless evidence is missing.

- Confirm the active code, active `schema_migrations`, service health, accepted-financial row integrity and whether migration 065 is additive and compatible with current *older* running code. Verify the exact effects and two reported backfilled BSE BLOCKED events against read-only evidence. Do not drop the table or restore an old A0 backup automatically: doing so may discard legitimate subsequent records.
- Take and *restore-test* a **new consistent database backup** reflecting the current post-065 state before any future approved production deployment. Reuse an already valid, current post-incident backup if one exists; don't force unnecessary disruption.
- Identify why changing bind-mounted development files allowed an **already running** worker to apply a new migration. Move review/patch/test workflows to an isolated checkout and non-live database clone. Document the mechanism preventing recurrence.
- **Safety stop:** If the existing worker cannot safely run against the migrated database or any accepted data has been damaged, prioritize emergency containment and escalate before unrelated writes. If safe, continue independent B/C work immediately.

**Deliverable:** `A0_MIGRATION_065_INCIDENT_ADDENDUM.md`, current backup/restore proof or verified equivalent, isolation mechanism and `SAFE_TO_CONTINUE_ISOLATED_DEV: YES/NO`.

## A1 — Preserve the current Q2 results fast lane

- Maintain the **existing approved** new-quarter financial refresh rather than redesigning it. Verify a real or offline-fixture result retains correct issuer/ISIN, standalone scope, report period, validated acceptance and previous accepted history.
- New results and their quantitative decisions have priority over archival PPT/transcript recovery. Document current source limitations rather than claiming full automated new-result coverage.
- Where source access is temporarily blocked, preserve existing accepted history and report operational exposure. Do not fetch restricted endpoints through unofficial browser workarounds.

**Deliverable:** `A1_Q2_FAST_LANE_CHECK.md` containing one source-validated example or offline simulation, actual last-success/blocked source status and explicit known limitations. **Keep it bounded.**

## A2 — Access-block classification/retry patch: REVIEWED, NOT DEPLOYED

**Known staged artifacts reported by agent:**

- `artifacts/a2-2026-09-29/A2_REVIEW.patch`
- `artifacts/a2-2026-09-29/A2_REVIEW_HANDOFF.md`
- `artifacts/a2-2026-09-29/SOURCE_ACCESS_MATRIX.md`
- `artifacts/a2-2026-09-29/AFFECTED_COMPANY_REPORT.md`
- `artifacts/a2-2026-09-29/affected_companies.csv`

**Reported patch behavior:** BSE HTTP 403 → `SOURCE_ACCESS_BLOCKED` persisted per source, source-specific scheduled retry/catch-up suppression, existing scheduler alert output, evidence-required explicit operator resume, no destructive checkpoint/snapshot/queue changes. Reporting distinguishes never-refreshed, access-blocked, HTTP 404/410 artifact absence and other failures. The affected-company report measures actual **BSE source exposure**, not missing historical financial statements.

**Outstanding before deployment:**

1. Verify migration-065 compatibility and review the precise patch and its effects in the *active* checkout.
2. Recreate live Python **3.12.14** and pinned dependencies in isolated CI/container; test the exact patch against a restored **post-065** database copy. Earlier host Python **3.14.5** offline results are not production-runtime proof. Keep integration tests offline unless a separately permitted/approved live test is specified.
3. Prove 403 suppression and controlled resume, alerts/status correctness, snapshot/checkpoint immutability, compatibility with already-backfilled events, separation from unrelated source jobs, idempotent migrations and non-regression.
4. Require a **separately approved maintenance window**, explicit deployment runbook, post-deployment observations and rollback that **does not automatically reverse schema 065 or restore stale backups**.

**Now:** freeze the patch and leave active services under approved code until a release review. **A2 is not on the critical path to B0/B1/B2.** Original official XBRL file import is **not** required to complete this scoped A2 safety fix.

**Deliverable:** `A2_PROD_COMPATIBILITY_AND_RELEASE_GATE.md`, including a separate `READY_TO_REQUEST_DEPLOY_APPROVAL: YES/NO`; never say *deployed* based on test results.

## A3 — Targeted revision safety + document/status completeness

**Inspect before coding:** existing `scrapper/db/exchange_repo.py` revision/manual-review logic, migration 064 and 065 interactions, accepted-version provenance and `scrapper/commands/quarter_documents.py`.

- Check exact duplicates, later corrected same-quarter submission, changed bytes with equal timestamp, cross-exchange observation and late source refresh; identify what **already** passes and patch **only proven high-impact defects**.
- Keep original/revised facts and true availability dates. A fresh source request that does not yet contain an amended value must not be recorded as acceptance of the amendment.
- Extend the **existing** quarter readiness report as needed. Financial acceptance and **supporting-document readiness must be separate columns**, with document status `UNKNOWN / NOT_ISSUED / NOT_YET_ISSUED / DISCOVERED / ISSUED_NOT_COLLECTED / ACCESS_BLOCKED / DOWNLOADED / VALIDATED / WRONG_DOCUMENT` (or equivalent existing enum mapping).
- Use offline fixtures and isolated DB. Production activation requires the same release discipline as A2.

**Deliverable:** `A3_REVISION_AND_STATUS_PROOF.md`, minimal tested patch only if necessary, sample readiness export.

---

# 4. STREAM B — FIRST FINANCIAL FOCUS ENGINE AND ML PROOF

## B0 — *Usability* audit, not another financial acquisition audit

**Dependency:** Safe read-only access / isolated copy after A0 containment. **Start next.**

**Owner premise:** existing financial data covers the intended company universe. Accept this as the acquisition status. Verify the *narrow conditions needed for valid model inputs* without doing another weeks-long all-company completeness study.

**Minimum work:**

1. Inspect existing accepted financial schema and `scrapper/pipeline/ml_contracts.py` (or active equivalents). Identify already calculated fields and reusable query paths. Do not build a second finance database.
2. Define the version-1 feature contract from the owner's **actual** standards: comparable QoQ/YoY growth, margins, PBT/PAT/EPS, applicable debt/credit, cash conversion, balance/cash-flow figures at their *actual* filing intervals, relevant trend/sector features. Flag fields as `PRESENT / DERIVABLE / SPECIAL-SECTOR / NOT-REQUIRED / UNVERIFIED` rather than calling every unavailable optional ratio an acquisition failure.
3. Validate a **small representative sample** of existing company-quarter rows (suggest 20–30 across major sectors, historic revisions, negative values and varying statement types). Increase only if a demonstrated error requires it. Independently check original evidence where already held; do not initiate new bulk filing downloads.
4. Assess historical point-in-time confidence: when was each old source captured, when did the company disclose it (if evidenced), and do revisions contaminate retrospective values? Mark **exploratory-only** periods clearly.
5. Inventory *real* Focus decisions by actual quarter/date: selected firms, **explicitly reviewed rejects if recorded**, unknown/unreviewed firms, list edits and freeze times. Absence from a Focus list does **not** automatically create a negative label.
6. Produce a few human-readable sample rows and an exact definition of what can be exported without touching production. Time-box work; don't require a 100-company audit to write the first feature exporter.

**Deliverables:** `B0_EXISTING_DATA_ML_CARD.md`, `B0_FEATURE_CONTRACT.csv`, `B0_LABEL_INVENTORY.md`, `B0_SAMPLE_CHECK.csv`, `B0_PIT_LIMITATIONS.md`.

**Exit:** A clear `CAN_BUILD_FINANCIAL_FEATURES: YES/NO`, a minimal feature list, important timestamp/label limitations and only actual blocking data defects. Do not mistake *all companies have rows* for *the past is point-in-time safe*.

## B1 — Source-independent feature exporter over EXISTING finance records

**Dependency:** B0 initial contract; no document recovery or vendor integration dependency.

Implement the smallest compatible existing-code extension (reuse `ml_contracts.py` if practical) with a logical signature equivalent to:

`company_quarter_features(company_id, decision_time, scope='standalone', feature_version='v1')`

- Pull from stored **accepted** historical financial observations; no repeat scraping, mass reparse or extra canonical database unless the existing schema demonstrably cannot support lineage.
- Align exact period and standalone scope; distinguish three-month quarter from six-month/YTD; preserve original unit, source version, data quality and known first-available cutoff.
- Start with a small cohort (e.g. 20–30 companies) covering supported industrial sectors before broad run. Handle banks/NBFCs/insurers through specific applicability definitions rather than industrial formulas by default.
- Version all derived ratios and retain raw input references. Missing does not mean zero. If original historical availability is unknown, flag output **exploratory/backfill**, not authentic as-of history.
- Emit an explicit feature status per company-quarter so downstream rules can return `INSUFFICIENT_EVIDENCE` rather than invent a pass/fail.

**Deliverables:** tested feature-export code, `B1_FEATURE_DICTIONARY.md`, `B1_FIRST_WORKING_COHORT.csv`, `B1_RECONCILIATION_AND_TESTS.md`.

**Exit:** Real existing financial records produce accurate, replayable company-quarter features on the agreed sample.

## B2 — Explainable deterministic fundamental Focus engine: FIRST PRODUCT

**Dependency:** B1 working cohort + owner rule approval. Draft rules **in parallel** with B1.

Define and freeze the actual owner-approved company selection standard—do not infer numerical thresholds from old mentions. Keep exchange registry, operational collection, fundamental research and eventual liquidity/trading criteria conceptually separate. The historically mentioned **₹580 crore collection buffer** and proposed **~₹700 crore fundamental cohort** are **not the same rule**; owner must specify current policy. Keep unknown dated cap unknown.

- Apply declared financial metrics and suitable sector comparisons to accepted B1 features. Track original source versions, feature version, cutoff, as-of eligibility and each criterion's exact reason.
- Output `PASS / FAIL / REVIEW / INSUFFICIENT_EVIDENCE`; optionally apply the owner's Blue/Cyan/Purple categories **only after their actual definitions are provided and agreed**.
- Give every listed company in the processed cohort a decision or honest exclusion reason. Save decisions immutably; do not let a corrected quarter silently rewrite a prior issue.
- Missing optional PPT, concall or rating reports can trigger a later `RESEARCH_PENDING` flag but never automatic fundamental rejection.
- **Freeze timing:** The Sep 2026 quarter ends **30 September**, results generally follow. If the pre-result freeze happens after relevant information appears, record the actual later freeze and what was already known; do not label contaminated tests prospective.

**Deliverables:** `B2_FOCUS_RULES_V1.md`, versioned rule manifest, tested deterministic selector, sample auditable Focus output and decision-replay fixture.

**Exit:** A usable financial-only Focus shortlist from the existing database, not merely another proposal document.

## B3 — Supervised ML *only if defensible labels and historical cutoffs exist*

**Dependency:** B0 real-label count, B1 source-backed features, B2 frozen deterministic baseline.

**Objective A:** Learn/reproduce independently recorded mentor or owner Focus-selection decisions. **Do not confuse this with return prediction.**

- If actual dated decisions include both selected firms and **reviewed nonselected firms**, compare logistic/regression baseline and a constrained interpretable boosting challenger, using the **same eligible population, cutoff and metrics** as B2.
- Split chronologically by decision quarter; audit repeated issuers, sector composition, class imbalance, data source dates, restatements and market-cap as-of history. A present-day finance snapshot is exploratory unless shown to reflect the information originally available.
- Preserve the next genuinely unobserved reference list as a prospective test. Track all false positives/negatives with per-criterion and evidence attribution; do not present in-sample overlap as model success.
- If only a few positive Focus Lists exist or true reviewed negatives/timestamps are missing, **stop at the rules engine, create a label-capture workflow and run only clearly marked exploratory analysis**. Never label every unlisted company as a rejection.

**Deliverables:** `B3_ML_FEASIBILITY.md`, reproducible experiment ledger, comparison to B2 if justified, `GO / EXPLORATORY_ONLY / NOT_READY_FOR_SUPERVISED_ML` conclusion, explicit next missing evidence.

## B4 — Ongoing Q2 Focus releases (does not wait for B3)

**Dependency:** A1/A3 integrity, B1 exporter, B2 frozen rule manifest and an actually available verified new Q2 result.

- `QUANT`: a timestamped issuer-level decision when Q2 standalone numbers have really arrived and passed validation. Companies not yet reported stay pending; do not manufacture a full final Q2 Focus List in advance.
- `RESEARCH_ENRICHED`: a separate later decision when valid PPT/transcript/rating claims become available. Preserve the earlier QUANT release, explicit as-of times and reasons for changes.
- Compare genuinely prospectively to owner/mentor selections **only if** features and model were frozen ahead of those labels.

**Deliverables:** dated release files, per-company decision/evidence IDs, pending/new/revised summary and one reproducible live/fixture demonstration.

---

# 5. STREAM C — MAKE THE EXISTING DOCUMENT CORPUS USEFUL

## C0 — Actual document inventory and issuance-aware gap classification

**Start alongside B0** on a copied/read-only database and existing blob metadata. The ~180 GB is an **owner-reported total**, not proof that all files are unique or correctly indexed. Do not re-download or LLM-process everything.

- Inspect and reuse existing artifact expectation/discovery/download/rating/recovery logic, content hashes, and `quarter_documents.py`. Produce **company × quarter/event × document type** records for PPT, call recording, transcript and detailed rating reports. A rating action announcement is not the same as a full analytical rating report; a link to a call is not captured audio.
- Reconcile discovered URL versus downloaded bytes versus parsed text; actual publisher/source, issuer mapping, relevant quarter/event, publication/first-observed date, hash and extractability.
- Use source-grounded classifications: `VERIFIED_FILE / DISCOVERED_NOT_DOWNLOADED / ISSUED_NOT_DISCOVERED (only if evidenced) / SOURCE_ACCESS_BLOCKED / FAILED_OR_BAD_FILE / WRONG_PERIOD_OR_COMPANY / CONFIRMED_NOT_ISSUED (only with evidence) / UNKNOWN / NOT_YET_ISSUED`.
- Produce actual unique-company and quarter-level counts, by document type and relevant Focus cohort. Audit duplicated physical bytes separately from legitimate cross-source evidence. Rank potential *impact* for missing material; never assume every issuer has every document.

**Deliverables:** `C0_DOCUMENT_TRUTH.md`, `C0_DOCUMENT_MATRIX.csv`, `C0_GAP_REASONS.csv`, `C0_SAMPLE_GOLD_SET.csv` (bounded representative, verified source files).

**Exit:** An actionable document-specific recovery queue and a valid small extraction sample, **not** a blanket percentage that confuses never-issued docs with collection failures.

## C1 — Verified Research Intelligence on a small existing sample

Use C0's already collected verified files. Deterministically extract native text/tables where supported. Use LLMs for qualified narrative interpretation with **fact-level evidence**; never trust an uncited financial number extracted from prose over verified structured financial facts.

Start with a few question types: management guidance vs later delivery, order book quality, capacity and capex, margin explanations, material risks and agency rationale/credit changes. Record original exact quoted span/page, source identity, statement qualifier, value/unit, relevant period, known availability date, model/extractor version and human verification. Cache by file hash + extractor/model version.

**Deliverables:** `C1_RESEARCH_CLAIMS.jsonl`, `C1_EXTRACTION_ACCURACY.md`, sampled human gold references and cost/error report.

**Exit:** Demonstrated verifiable claims, honest extraction failures and repeatability. No full-corpus rollout yet.

## C2 — Measure whether document enrichment helps

Compare financial-only B2 outputs and an enriched research version on the **same comparable issuers**. Stratify document availability, subsector and company size. Test whether the model simply favors well-documented businesses. Publish changed decisions and exact cited research evidence in a **new** dated release. If improvement is not demonstrated, retain RI as review assistance rather than a mandatory selection feature.

**Deliverables:** `C2_INCREMENTAL_VALUE.md`, before/after decisions, bias audit and a clear expand/hold decision.

---

# 6. STREAM D — TARGETED **DOCUMENT** RECOVERY, NOT A FINANCIAL REWRITE

## D0 — Decide which real document gaps matter

**Dependency:** C0 inventory. Prioritize `ISSUED_NOT_DISCOVERED`, `DISCOVERED_NOT_DOWNLOADED`, corrupt/wrong-period attachments and verifiable late documents for the actual fundamental research cohort (especially companies whose decisions could change if the missing evidence is recovered). Require real issuer/event evidence before declaring `NOT_ISSUED`.

**Deliverable:** `D0_PRIORITIZED_DOCUMENT_GAPS.csv` with company, quarter/event, document type, proof of publication if present, existing source attempts, lawful feasible source and exact recovery priority.

## D1 — Reuse existing acquisition/recovery tools for those documents

Before writing code, audit existing `scrapper/sources/documents/*`, promotion, downloader, rating-specific recovery, expectations and immutable blob storage. Fix the **smallest observed** failures. Authorized sources may include permitted exchange disclosures, issuer investor-relations publications, rating-agency reports or owner-provided licensed files. Respect each site's/API's actual permitted use; no BSE Akamai avoidance, cookie borrowing or browser impersonation.

- Route recovered artifacts to the **same existing logical document/immutable blob/indexed extraction path**; don't deploy a new generalized website crawler or second downloader by default.
- Track `downloaded`, `unavailable`, `not issued`, `blocked`, `wrong issuer`, `wrong period`, `duplicate link`, `duplicate bytes`, `needs human review`; retain every source reference and retry reason.
- Run one selected Focus-cohort recovery pilot first; compare before/after unique valid files and incremental useful research claims rather than simply GB added.
- Keep document recovery on a separate bounded queue so it cannot delay live financial result refreshes.

**Deliverables:** `D1_RECOVERY_PILOT.md`, targeted improvement patch if genuinely needed, existing-queue integration tests, after-recovery coverage matrix and unresolved permission blockers.

## D2 — Expand only demonstrated successful recovery methods

On the basis of D1 yield, extend to additional companies and quarters **incrementally**, with deduplicated content hashing and source-friendly permitted request limits. Freeze any method that begins creating wrong-company/quarter documents, repeated 403 loops or untraceable claims. Add targeted operator import for an original research document only if the existing path cannot accept the **needed** medium and the owner has permission.

**Deliverable:** per-cohort valid-document yield, processing cost/time, unresolved reason matrix and documented operating limits.

## D3 — RETIRED FROM THE ACTIVE PLAN: Upstox / generic financial-provider pilot

The older v3 plan reserved D3 for the Upstox feasibility pilot and D2 for general official financial XBRL parsing. **Those assignments are now inactive and must not be auto-executed.** Company financials are reportedly already collected; these products don't directly solve the PPT/concall/rating gap. Reopen a **separate owner-approved financial-data investigation** only if a later real data audit reveals a specific indispensable missing quarterly numeric field/source. No default financial-provider integration, broad official XBRL project or further purchase evaluation.

---

# 7. LATER WORK: HISTORICAL ML RIGOR AND TRADING (E0–E2)

**E0 — Historical PIT repair:** Selectively repair original availability timestamps/revision histories *only for needed training periods*; record historical universe, delistings, dated market cap/shares and label provenance. The goal is a defensible prospective ML experiment, not processing every archived file.

**E1 — Trading research, separate objective:** Obtain appropriately authorized historical daily prices, adjusted corporate actions and realistic transaction costs; benchmark a simple trend/breakout entry after fundamental filtering using chronological splits, liquidity/cost constraints and paper trades. A mentor selection model is not itself a profit prediction model. No live order execution.

**E2 — Controlled model iteration:** immutable experiments and decisions; evaluate offline challengers against frozen incumbents on newly observed labels/outcomes. Never deploy self-modifying rules from short streaks of paper trades.

---

# 8. EXACT ENGINEERING HANDOFF CONTRACT

**For every task:** `IMPLEMENTED / PARTIAL / BLOCKED / NOT_STARTED`, active branch/commit, list of *existing* code reused, exact files changed, fixture and real-data evidence distinguished, test command/results, production impact, migration/rollback, next **single** smallest task. Avoid broad planning reports unrelated to the task.

**No giant upload:** Run local read-only queries and provide small aggregate CSV/Markdown exports. Do not upload the ~180 GB corpus or private credentials. For numerical/sample verification, share only a small set of lawfully usable source files when a specific doubt remains.

### The first agent assignment — issue THIS now

```text
Read TRADING_AI_MASTER_ROADMAP_V4_2026-09-29.md. It replaces the old v3 master and Upstox pilot.

Do not deploy A2, restart services, run new production migrations or touch production bind-mounted source.

First perform only A0's SHORT incident containment gate:
- Read-only verify migration 065's effects and current worker/database compatibility.
- Confirm a current restorable post-incident backup and a development environment isolated from production mounts.
- Escalate any demonstrated data-integrity or runtime danger. Do not perform another broad architecture review.

Then immediately execute B0 against an isolated, read-only copy of our EXISTING financial data.
ALL intended companies already have collected financials; DO NOT investigate Upstox, new financial sources, broad XBRL extraction or complete financial re-collection.

For B0 return only:
1. B0_EXISTING_DATA_ML_CARD.md
2. B0_FEATURE_CONTRACT.csv
3. B0_LABEL_INVENTORY.md
4. B0_SAMPLE_CHECK.csv and B0_PIT_LIMITATIONS.md
5. One specific, small proposed B1 implementation change, reusing existing code.

If parallel independent capacity exists, start C0 document metadata inventory separately.
Do not wait for A2's undeployed patch or for all documents before implementing financial-only B1/B2.
```

### Current status board — must be updated against actual repository evidence

| ID | Status entering this roadmap | Next action |
|---|---|---|
| A0 | Previous audit reported; migration-065 incident addendum **outstanding** | **Short safety gate now** |
| A1 | Existing collector reportedly running; fresh Q2 proof still to be shown | Parallel bounded integrity check |
| A2 | **Review patch only**, not deployed; incident + production-version tests outstanding | Hold patch; schedule separate approved deployment |
| A3 | Existing partial revision handling, actual new-version behavior needs focused replay | Parallel bounded fixture audit |
| **B0** | Not yet proven on real local corpus in this handoff | **Execute next** |
| **B1** | Existing `ml_contracts.py` reportedly partial; live state must be checked | **First feature code change after B0** |
| **B2** | Rules/selection plan; no demonstrated complete first Focus run in supplied review | **First working product** |
| B3 | Label/PIT dependent | Experimental, conditional |
| B4 | Depends on real new results + B1/B2 + safe Q2 path | Begin independently of B3 when eligible |
| C0 | Corpus extensive by owner report; validated per-company/quarter document readiness unknown | Read-only inventory alongside B0 |
| C1–C2 | Evidence quality and marginal value unproven | Small verified samples after C0/B2 |
| D0–D2 | Actual targeted research-document gaps unknown | Triggered by C0; no broad re-scraping |
| D3 | **Inactive/retired** | Do not execute |
| E0–E2 | Later work | Wait on relevant gates |

---

# 9. REGRESSION / ACCEPTANCE MATRIX

**Safety:** migration 065 coexistence with current code, post-incident consistent backup and restore, no migration on dev patch, scheduler stays isolated, no automatic stale-database restoration.

**Q2:** new quarter, no partial-overwrite loss, correct standalone scope, genuine same-period amendment detection, same-timestamp different-content event, cross-exchange duplicate event with separate provenance, no false accepted revision, correct UTC/IST availability.

**A2 release (separate):** access-block vs 404 vs temporary failure vs never-refreshed; no endless 403 retries; accepted history/checkpoints unchanged; resume demands operator evidence; unrelated sources continue; tests reproduce Python 3.12.14 live pinned environment.

**Financial features:** appropriate subsector; quarter vs YTD; cash-flow reporting cadence; zero vs missing; original/revised versions and as-of behavior; derived metric formula/input lineage. First accepted sample manually reconciled where source evidence available.

**Selection/ML:** deterministic rule replay; explicitly evaluated rejected stocks separated from unassessed; no future filing/corrected result or currently calculated ratio used for earlier historical cutoffs; chronological validation with frozen next quarter; no exaggerated predictive-performance conclusion from a few lists.

**Documents:** published-not-collected vs confirmed-not-issued vs unknown; link vs downloaded bytes; recording URL vs actual audio; rating announcement vs detailed report; wrong issuer/period; duplicates; documented source permission; exact citations and extraction claim accuracy; missing-document bias.

**Release requirement:** Any production-affecting change requires explicit owner's go-ahead and tested, non-destructive rollback. Review tests passing **never** authorize deployment implicitly.

---

# 10. REFERENCES AND SUPERSESSION

This v4 is a **re-prioritization grounded in the owner-confirmed facts and the earlier uploaded v3 roadmap**. It does **not** claim to have inspected the newly reported A2 artifact files or live database; actual patch and incident details above come from the agent report in the conversation and need runtime verification.

- Superseded operational document: `TRADING_AI_SINGLE_MASTER_ROADMAP_2026-09-29(1).md`, v3, 542 lines (task labels A0–A3, B0–B4, C0–C2, D0–D3, E0–E2).
- Superseded earlier documents: `TRADING_AI_MASTER_ROADMAP_AGENT_HANDOFF_2026-09-29(1).md`; `TRADING_AI_UPSTOX_FUNDAMENTALS_FEASIBILITY_PILOT_2026-09-29(1).md`.
- Agent-reported A2 review artifacts under `artifacts/a2-2026-09-29/`. The code diff, current deployment and migration details are not independently verified by this document.
- Existing designs `TRADING_AI_FOCUS_ML_PLAN_V5.md`, `TRADING_AI_RESEARCH_INTELLIGENCE_PLAN_V5.md`: consult for already-approved business semantics/field dictionaries if they exist locally, **but do not let older scheduling instructions override this roadmap**.

**Controlling instruction:** **Build B0 → B1 → B2 on existing financials. Maintain Q2 safely. Audit/recover the supporting documents in parallel. Experimental ML only when data timestamps and labels support it. Treat A2 as a staged safety patch, not today's critical path.**
