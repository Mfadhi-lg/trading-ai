# TRADING AI — NORTH STAR & SAFE RESET

**Status:** AUTHORITATIVE PRODUCT DIRECTION / CLEANUP PLAN — not authorization for code, database, production, network, or deletion operations  
**Created:** 26 October 2026  
**Version:** 1.0  
**Proposed repository path:** `TRADING_AI_NORTH_STAR.md` (repository root)  
**Owner:** Project owner; changes require a recorded decision  
**Next permitted step:** Phase 0 — read-only inventory and dependency audit in the actual local checkout

> **One-sentence mission:** Build an auditable Indian-equities research system that converts historical financials, investor presentations, earnings-call transcripts, and credit-rating documents into **verified, point-in-time company-quarter features**, then tests whether those features improve selection from the existing Focus List into a researched Purple List.

## 1. What success means (and what it does not)

This project is **not** an LLM that reads three PDFs and tells us which stock to buy. It is a measured pipeline:

```text
Company + decision cutoff
     │
     ├── Financial statements / accepted source data
     ├── Investor presentations
     ├── Earnings-call transcripts
     └── Credit-rating reports
               ↓
     Source inventory & availability at cutoff
               ↓
     Native parsing; selective table/vision fallback
               ↓
     Targeted AI *candidate* extraction
               ↓
     Deterministic checks + reviewer adjudication
               ↓
     Versioned, source-linked atomic claims
               ↓
     Sector-aware company-quarter ML features
               ↓
     Financial-only baseline vs financial+document models
               ↓
     Walk-forward evaluation → ranked research candidates
               ↓
     Human Purple List decision, with evidence
```

**Key division of responsibility:**

- **AI:** find and interpret *candidate* factual claims with quotations, page/cell references, scope, time, and qualifications.
- **Code:** validate schemas, evidence binding, units, time, consistency, provenance and deterministic calculations.
- **ML:** test whether *historically available, independently validated* features predict specified business/investment outcomes.
- **Human reviewer:** approve uncertain evidence and final Purple List decisions.

**Do not conflate:** document coverage with correct extraction; source citations with logical support; moat with next-year share-price return; not selected with explicitly rejected; backfilled historical data with data actually available at the historical decision time.

## 2. Binding priorities and anti-drift rules

1. **Safety and trustworthiness outrank speed.** Never alter original financials, source documents, original hashes, decision histories, or operational queue data to make a model pass.
2. **One narrow vertical slice before broad rollout.** No full-universe inference, backfill or expensive benchmarking until a small holdout test passes.
3. **One canonical production application.** Preserve `scrapper/` and documented live acquisition/release ownership. A second code tree is a comparison candidate, not a replacement.
4. **Every ML feature is traceable.** No factual feature without versioned input source(s), extraction/review provenance, as-of time, scope and transformation definition.
5. **Missing is not zero.** Distinguish not published, not yet available, not searched, absent after complete search, parser failed, extraction unresolved, and not applicable.
6. **No labels invented by AI.** Mentor selected, mentor explicitly rejected, not selected and unknown are different statuses. A retrospective LLM opinion is not a historical ground-truth label.
7. **Test against a simple baseline.** If document intelligence does not improve over financial-only signals out-of-time, do not promote it.
8. **No multi-agent architecture / automatic trading / fine-tuning as a shortcut.** Reconsider only after a measured failure makes the added complexity necessary.
9. **Hard stop on leakage.** A source must have a defensible public availability timestamp for every historical decision where it is used; collection timestamps and unknown timestamps are recorded separately.
10. **No scope changes silently.** Any proposed detour must state: current phase, measurable blocker, options, cost, and explicit owner decision.

**Scope freeze for first experiment:** Financial statements + credit-rating PDFs + investor PPTs + earnings-call transcripts. Initial prediction work uses the existing Focus universe and historically defined cutoffs. Other document types, news/social feeds, price agents, portfolio automation, and trade execution are out of scope.

## 3. Project state — facts from the *condensed review archive*, not live inventory

The reviewed file is `trading-ai-code-review-compact.zip` (6,278 regular files; roughly 286 MB uncompressed). It is a **selective, dated snapshot**, not proof of what is presently running or the full local workspace. `OLD/`, `exchange-inbox/`, unshared data, mounted document drives and session files are not fully represented.

| Area | Snapshot count | Preliminary handling |
|---|---:|---|
| `scrapper/` | 1,311 | **KEEP; canonical app source and tests** |
| `docs/` | 1,764 | **KEEP evidence; improve index and active/historical labels** |
| `artifacts/` | 2,246 | **CLASSIFY; includes tests, source evidence, experimental outputs** |
| `github-source-candidate-20261023/` | 707 | **REVIEW; compare with canonical code** |
| `ops/` | 191 | **KEEP active operational controls** |
| `experiments/` | 23 | **KEEP or archive with reproducibility record** |

Important findings from existing project records:

- The structural cleanup ledger records **C28.1 complete** for the canonical main-source integration. Repeating the large refactor is **not** the current goal. Consult `TRADING_AI_CLEANUP_EXECUTION.md`, `docs/cleanup/chunks/C28.1.md`, and `docs/cleanup/chunks/ROOT-HYGIENE.md` before proposing a move.
- Previous root-hygiene work already archived a historical candidate and demonstrated that seemingly redundant files can include unique evidence. **Do not repeat historical deletions blindly.**
- The candidate `github-source-candidate-20261023/` has **693 files byte-identical** to same-path files elsewhere in the condensed tree; **10 differ**, and **4 are candidate-only**. These 14 are a mandatory review queue (listed below).
- `README.md` and the October 3 `docs/STATUS.md` contain **dated operational statements**, not live health certificates. A reference to a document-intelligence plan in the shared README is not present at the same path in the compact package; this may be a ZIP inclusion issue and must be checked in the actual checkout before changing links.
- The screenshot shows `.env`, `.screener_csrf`, `.screener_session` and an executable `screener-token` utility. Treat them as **sensitive until verified**. Never copy their contents into logs, AI prompts, GitHub or cleanup reports. The condensed archive includes the script `screener-token`, but not those local session files; presence alone does not establish a credential leak.

## 4. Phase 0 — safe workspace cleanup **BEFORE** new document-ML work

### 0A. Establish the true current state (read-only)

In the **actual working repository**, with a named operator:

1. Record branch, HEAD, full staged/unstaged/untracked/ignored status, and running release/service ownership. Do not stash, checkout, reset or stop services.
2. Identify **tracked vs untracked** and **source vs generated**. The screenshot's `M`, `U` and colored names are clues, not a comprehensive Git audit.
3. Inventory each top-level directory by count and disk usage. Determine references from Docker/Compose, launchers, workflows, imports, migrations, tests, database schemas and documentation.
4. Verify durable backup/restoration of databases, original documents, acquisition state and important retained evidence **without changing production**.
5. Produce `docs/cleanup/review-2026-10-26/INVENTORY.md` and `CANDIDATES.csv` (path, classification, size, tracked status, dependency evidence, unique-content status, proposed disposition, recovery location, reviewer approval).
6. Audit secrets exposure **without reading or printing values**: tracked filenames, index/history exposure, `.gitignore`, archived ZIP inclusion; handle credential rotation privately if exposure is established.

Suggested harmless commands to run locally from repository root (review terminal output before sharing it):

```bash
pwd
 git rev-parse --show-toplevel
 git branch --show-current
 git rev-parse HEAD
 git status --short --untracked-files=normal
 git status --porcelain=v1 --ignored --untracked-files=normal
 du -sh artifacts docs experiments github-source-candidate-20261023 scrapper ops OLD exchange-inbox 2>/dev/null
 git ls-files --error-unmatch .env .screener_csrf .screener_session screener-token 2>/dev/null || true
```

**Output may contain sensitive filenames and private paths. Sanitize before publishing; never print secret contents.** These commands do not mutate Git or files.

### 0B. Explicit candidate-file comparison (do not delete)

Review these 10 differing paths against the canonical root:

```text
ops/host/acquisition_lifecycle.py
ops/host/acquisition_queries.py
ops/host/acquisition_terminal.py
ops/host/run_acquisition_lifecycle.py
ops/host/scraper_lifecycle.sh
ops/host/test_acquisition_observations.py
ops/host/test_acquisition_terminal.py
scrapper/research/general_ai_experiment/runner.py
scripts/audit/check_migration_manifest.py
scripts/audit/test_migration_lineage.py
```

And these 4 candidate-only paths:

```text
.github/workflows/source-regressions.yml
compose.source-example.yml
docs/migration-history/065_exchange_source_access.sql
scripts/audit/run_source_regressions.py
```

For each: assess active references, source intent, whether canonical counterpart changed later, and whether it is necessary for reproducibility. **Never overwrite canonical files just because a candidate differs.** Only after the comparison, preserve the candidate externally with original paths, file modes, hashes and a tested restore method. Then consider removing its active checkout copy in a separately approved cleanup change.

### 0C. Classify before moving anything

| Class | Examples | Policy |
|---|---|---|
| **A: Active code and configuration** | `scrapper/`, `ops/`, `scripts/`, `.github/`, Docker and Compose, root launchers | Keep; no refactor during workspace cleanup |
| **B: Authoritative data and evidence** | Source PDFs, raw HTML, financial records, schemas, DBs, receipts, lineage, ML labels, historical gold sets, approved review records | Never delete; source-of-truth custody and recoverability required |
| **C: Reproducible outputs** | Generated logs, cache, stale intermediate renders, temporary exports | Archive or remove **only** with proof of reproducibility, no consumers, and explicit approval |
| **D: Superseded experiments** | Old parser/model comparison runs, dated failed prototypes | Keep summary + checksums + executable reproduction dependencies; archive bulk safely; don't erase negative results |
| **E: Local secrets/runtime state** | `.env`, cookies, session credentials, live locks, WAL/SHM, mount sentinels | Keep out of exports and Git; never change while associated service is running |
| **F: Duplicated source candidates** | `github-source-candidate-20261023/` | Compare and preserve unique bytes before off-repo archive |

**High-risk paths:** `OLD/` (recorded Focus governance dependencies), `artifacts/` (contains test/evaluation code and evidence), `exchange-inbox/` (input path), `scrapper/store/` (data), active host state, document-drive volumes, live queues, database files and their WAL/SHM. No blanket removal or path relocation.

### 0D. Clean navigation before cleaning history

- Keep **this file** as the sole north-star for the new intelligence/ML objective.
- Keep `README.md` as the operational entry point; eventually add a short link to this file **without altering commands/runtime settings**.
- Keep legacy roadmap, C28 ledger, historical statuses, studies and negative experiments as **historical/dated references**, not competing active instruction documents.
- Add a small *current-vs-historical* index; do **not** rewrite old acceptance decisions or experiment results.
- Archive bulky verified evidence off the active working view only where import/test and preservation checks support it. GitHub candidate review is the first target, not all of `artifacts/`.

### 0E. Cleanup acceptance checks

- [ ] Actual checkout inventoried, including ignored/untracked resources.
- [ ] Working tree changes and operator-owned work preserved.
- [ ] Live acquisition, storage, document retrieval and database identities unchanged.
- [ ] Original file/document/database history is recoverable from verified backups.
- [ ] All 14 candidate differences assessed and recorded.
- [ ] No secret contents exposed; tracked/archived secret paths audited.
- [ ] No dangling active imports, workflows, Compose mounts or links from approved moves.
- [ ] Representative pre/post code tests, preservation hashes and golden-flow comparisons match; unexplained failures block approval.
- [ ] `git diff` contains only selected and reviewed cleanup changes.
- [ ] Owner approves a manifest of every proposed removal/move **before** execution.

**Definition of CLEAN:** one canonical codebase, navigable active docs, recoverable research evidence, no unexplained duplicate code, and stable operational behavior. It does *not* mean few folders or a small Git repository.

## 5. Phase 1 — define evidence/features (only after Phase 0 passes)

Create a feature dictionary of approximately **20–30 high-value initial facts**, split into:

- **Credit rating:** rating/tenor, outlook, rating transition, liquidity, utilization, leverage, agency concerns/strengths.
- **Investor PPT:** order book/inflow, capacity/utilization, capex, product/geographic/customer mix and concentration.
- **Earnings transcript:** quantified guidance, changes to guidance, business drivers, demand, margins, project timing, execution risks, management commitments.
- **Financial:** revenue/profit growth, margin, ROCE, cash conversion, leverage, working-capital measures.
- **Cross-document:** contradictions, guidance vs actual results, repeated risk disclosure, revised estimates, credit deterioration.

Each feature must define: semantic meaning; sector applicability; company/period identity; units; standalone vs consolidated; source priority; availability cutoff; normalization/formula; valid missing states; approval standard; version; and downstream decision it might support. **Do not force unrelated-sector features into zero-filled columns.**

**Deliverable:** `feature_dictionary_v1` and a source-linked schema with a testable example for each field.

## 6. Phase 2 — small extraction and evidence pilot

- Select a **representative**, frozen sample with all three document types where available and varied sectors/layouts; reserve previously unseen documents for blind evaluation. Record sampling bias and unavailable types.
- Prefer existing native parsing; use layout/table/vision escalation selectively. Keep source bytes, page regions, table headers, footnotes and parser provenance.
- One model creates **candidate atomic claims** (not approved facts). Output a strict structured schema with units, dates, company scope, exact references, confidence/uncertainty, conditions and contradictions.
- Perform rule-based checks and **independent** reviewer verification directly against source documents; withhold unverified material claims.
- Benchmark a stronger Qwen vision reader against a credible alternative **on the same frozen examples**, measuring field-level accuracy, important qualifications, citations, latency and cost. No model gets credit for facts added by a reviewer.
- Produce one **fully auditable company intelligence record** joining approved credit, PPT, transcript and financial evidence as the first milestone.

**Pilot gate:** zero accepted *materially wrong* values/scopes/periods in the blind acceptance sample; independently measured precision, recall, abstention, coverage, citation correctness, cost and latency; no claim admitted solely because its JSON schema validates. Set final numeric pass thresholds before looking at held-out answers. The sample is a feasibility test, **not** a production accuracy certificate.

## 7. Phase 3 — ML-ready dataset and honest comparison

**Training unit:** `company_id × historical_decision_cutoff`, optionally linked to quarter/fiscal period.

For each unit, join only data published **on or before** the cutoff. Separate:

- `source_published_at`, `captured_at`, `extracted_at`, `reviewed_at`, and `model_run_at`;
- historical mentor **selected**, explicitly **rejected**, **not selected**, and **unknown** labels;
- labels for *business-quality outcomes* from labels for *investment returns*;
- financial-only baseline vs financial-plus-documents feature sets.

Try baseline heuristics/logistic regression before CatBoost. Use chronological/walk-forward holdouts, comparable universes, sector effects, delisting/survivorship adjustments, and transaction-cost assumptions for return-based tests. Never randomly shuffle future observations into the past. Reject a larger model if it does not beat the financial-only baseline out-of-time.

**Purple List:** a *reviewed shortlist*, not an automatic trading command. Surface score components, data freshness, evidence, exclusions and uncertainty; do not make opaque single-number 'moat scores' from unsupported AI judgments.

## 8. Phases, ownership and explicit STOP gates

| Phase | Action | May advance when |
|---|---|---|
| **0 — now** | Actual-checkout inventory, dependency/secret review, reversible cleanup | Cleanup acceptance checklist complete; operator approves exact moves |
| **1** | Frozen feature dictionary and evidence contract | Each feature has semantics, units, scope, missing states and test cases |
| **2** | Narrow document extraction pilot | Blind source-based accuracy and provenance criteria pass |
| **3** | ML-ready point-in-time dataset | No unacceptable leakage and sufficient valid labels |
| **4** | ML ranking experiment | Out-of-time comparison supports actual incremental value |
| **5** | Controlled expansion | Repeatability, source coverage, cost and runtime constraints demonstrated |

**At the end of each phase**, publish *what changed, what was measured, what failed, exact retained evidence, and next gated action*. An implementation or passing unit tests alone do not prove financial correctness.

**STOP immediately if:** deletion without recoverable evidence is proposed; the only copy of a source or gold answer may move; a runtime service/DB/queue is affected; credentials are exposed; a missing item is silently interpreted as absent; a claim lacks historical availability; data leakage appears; a result requires hindsight; or a large-scale inference run has no agreed budget/acceptance benchmark.

## 9. First task for the coding assistant in the real checkout

> Read `TRADING_AI_NORTH_STAR.md`, `README.md`, `TRADING_AI_CLEANUP_EXECUTION.md`, `docs/cleanup/chunks/C28.1.md`, and `docs/cleanup/chunks/ROOT-HYGIENE.md`. **Execute Phase 0A and 0B as a read-only audit only.** Check the actual checkout—not the compact ZIP. Inventory tracked/untracked/ignored content; active runtime and dependency references; candidate-vs-canonical hashes and differences; original data and retention boundaries; security-sensitive filenames only. Produce a concise proposed cleanup manifest with `KEEP`, `ARCHIVE-CANDIDATE`, `REGENERABLE`, `REVIEW`, and `NEVER-TOUCH`, including dependency evidence and rollback for every move. Do not delete, move, rename, refactor, amend Git state, stop/start services, alter DBs, touch mounts or read/print credentials. Return the report and stop for approval before any mutation.

---

**Last confirmed decision:** Begin with safe project hygiene, not more prompt/model experiments. The first post-cleanup deliverable is a small, verified, source-linked feature dictionary and end-to-end company evidence record—not an ML stock predictor.
