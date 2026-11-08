# Project map and cleanup plan — 30 October 2026

This is a planning baseline for the actual checkout. No existing project files were moved, deleted, unstaged or rewritten. No application command, database connection, acquisition, service change, credential change, commit or push was performed. Newly created review files belong to this review directory only.

## What this project does

Trading AI is an Indian-equities research and data-acquisition project. Its implemented foundation collects financial statements, exchange filings, AMC workbooks and supporting company documents while preserving original inputs, versions and provenance. Document parsing and experimental AI extraction support research. The intended product converts verified financial and document evidence into company-quarter features and evaluates selection within a Focus universe; a validated ranking system and automatic trading are not established deliverables of this checkout.

The product direction is [North Star](../../../TRADING_AI_NORTH_STAR.md). The existing structural refactor reached C28.1; this cleanup should organize custody, repository contents and documentation rather than repeat that refactor.

### Application and operational ownership

- **Operator interface:** root `scraper` and `scraper-monitor` call `ops/host/scraper_lifecycle.sh`. Status, search, company views, logs and lifecycle operations route through host Python modules. Other manual commands can still route through Docker Compose.
- **Scheduling and admission:** `ops/activation/automatic_acquisition.py` owns the bounded host dispatcher, with financial, exchange and document adapters. Scheduling, ownership locks, manifests and release checks are part of the application dependency boundary. A periodic dispatcher can run indefinitely while each admitted batch remains bounded.
- **Canonical Python application:** `scrapper/cli`, `cli_groups` and `commands` expose commands; `sources` handles source adapters; `workflows` coordinates ingestion; `documents` handles discovery/download/extraction/recovery; `db` owns persistence and migrations; `operations` owns workers. Keep the existing package name.
- **Financial and exchange evidence:** Screener and NSE/BSE workflows preserve accepted versions, raw responses/cells, company identity and queue/attempt history. `resources/exchange/exchange_securities_master.csv` is an actual reference input.
- **Document processing:** original documents are separate from extracted text and derived claims. Generic document downloads, rating-agency browser paths, recovery and local processing have distinct responsibilities; similar filenames do not prove duplication.
- **Research:** `scrapper/research`, `experiments`, the credit-intelligence scripts and associated evidence contain financial features, Focus governance and document/AI experiments. Research outputs and tests can depend on frozen local inputs.
- **Compatibility:** `scrapper/pipeline` and other older facades retain command and import contracts. Operationally superseded continuous workers can still have manual callers. Deletion requires a caller/dependency review for the selected module.
- **Build and verification:** Dockerfile, Compose variants, pinned requirements, host browser requirements, audit scripts and `.github/workflows` describe build/manual-runtime/test contracts. The current CI definitions include migration checks and a broader offline suite; a reduced export must demonstrate its declared checks from a fresh materialization.

### Data and machine dependencies

Documented production financial storage is Docker volume `trading-ai_scraper-data:/data/scraper.db`; `scrapper/store/scraper.db` is a separate local store. Original document storage is on `/Volumes/My Passport/passport/trading-ai`. Historical source/config records identify these locations; neither production storage nor the external document corpus was opened in this review.

The macOS LaunchAgent, Docker images/volumes, browser/interpreter environments, local credentials, external drive and configured runtime paths are deployment dependencies outside a source-only Git repository. Files inside `ops/activation`, selected release directories and selected evidence paths participate in runtime identity checks. Moving the checkout or its apparent historical records can therefore break acquisition even when Python imports remain intact.

## Fresh observations and changes since earlier audits

Source: [inventory.json](inventory.json), observed 30 October 2026 at 16:53 UTC. This is a changing workspace, not an atomic snapshot. Counts precede creation of this review package.

- Branch: `chore/root-hygiene-pr`; HEAD: `0e6ee264931ba1a1e009e0785cf188f5dc9f37e8`.
- Filesystem census: **151,989 file entries / 103,982,300,005 logical bytes**, approximately **104.0 GB / 96.8 GiB**. Includes Git metadata, ignored files, the nested candidate repository and file symlinks; excludes directory-symlink targets and external storage. Logical bytes do not measure uniquely reclaimable disk space.
- Index: **26,066 tracked paths**; **828 paths have staged changes**. There are **49 paths with unstaged changes**, overlapping the staged set, and **3,979 nonignored untracked files** represented by 132 porcelain status entries, including directory entries. Preserve all existing work.
- `artifacts/`: **88.02 GB**, including **61.18 GB** under acquisition-readiness, **12.13 GB** under document-acquisition and **11.96 GB** under phase5. These categories mix snapshots, evidence, environments and operational files.
- `docs/`: **9.62 GB**, dominated by **9.50 GB** under cleanup. `.git/` is **4.25 GB**. `OLD/` is **1.19 GB**. `scrapper/` is **748 MB**, including **591 MB** of local store and **127 MB** of environment files.
- Four acquisition-readiness databases are each approximately **14.60 GB**. Equal sizes do not prove identical contents, restore equivalence or obsolescence. No database hashes or integrity checks were performed.
- The index contains **66 blobs of at least 50 MB**, including a 1.04 GB harness archive, a 922 MB patch and two approximately 694 MB OCI archives. Existing ignore rules have not removed these tracked objects.
- The 23 October source candidate still contains **725 payload files**, but current comparison is **663 identical / 56 different / 6 candidate-only**. This supersedes the 26 October comparison for this observation. Preserve the six unique files and review changes before creating another candidate. Exact paths: [candidate-comparison.csv](candidate-comparison.csv).
- There are now **76 canonical SQL migration files**, versus 75 in earlier audit records.
- Current `phase4_schedule.json` has `enabled=true`, `authorization_mode=continuous`, download limit 5, processing limit 10, four daily rating slots and a 457 daily source-request ceiling. `authorization_state` checks continuous mode before finite expiry. An old `expires_at_utc` value alone no longer establishes expiry. The [30 October rollout record](../../../artifacts/acquisition-throughput-rollout-20261030/README.md) documents activation; live service health and successful throughput were not independently checked here.
- `README.md` still links a missing `docs/current/TRADING_AI_MASTER_RESEARCH_AND_DOCUMENT_INTELLIGENCE_PLAN.md`. Its updated-name replacement exists. `docs/STATUS.md` routes through a 3 October snapshot and `docs/current/phases.md` describes an old trial. These need current navigation, while dated evidence stays dated.
- Named-path Git history still includes `.screener_csrf`; the three root secret paths are absent from the current index. Earlier records describe historical exposure. This review did not read credential values, establish validity, scan all Git objects or archives, or query remote exposure.

## What we need and what can leave the development tree

The path groups and decision prerequisites are in [CANDIDATES.csv](CANDIDATES.csv). They are planning groups, not approved recursive deletion patterns. A file may need local retention while being excluded from GitHub.

### Keep as project source

Keep canonical application code, tests, migrations, operator entrypoints, reusable operational tools, dependency locks, build files, CI, reviewed reference inputs and concise current documentation. Keep research implementations that are still being developed or needed to reproduce accepted findings. Preserve migration identities and compatibility contracts.

`ops/activation/` needs individual classification: it contains active source, configuration, seals, state and historical trial material. It should not be copied wholesale into a generic source export or moved wholesale into an archive.

### Keep locally; exclude from GitHub

Keep credentials/session files, databases and sidecars, original downloaded content, runtime receipts/checkpoints/locks, machine-specific activation configuration and restricted datasets outside the publication manifest. These exclusions do not authorize deletion. A proper backup is a separate requirement from GitHub publication.

Some `artifacts/` files are application inputs: the dispatcher reads BSE manifests/verification evidence, the document scheduler reads the Focus label registry, and lifecycle code writes recovery state there. Build a path-level dependency list before relocating these families.

### Archive candidates after verification

The nested GitHub source candidate, root review/sample ZIPs and retired Upstox proposal are plausible first archive items. Preserve candidate-only files, changed versions and nested history. These offer modest savings, but reduce ambiguity about the canonical tree.

The large database snapshots, OCI bundles and cleanup harness archives are the main space investigation. For each, determine creation purpose, present consumers, latest independently recoverable state, matching document/data lineage and available restoration route. Retain required rollback generations; consolidate only when equivalence or an accepted retention decision is demonstrated. The root ZIPs are not established backups of production.

`OLD/` cannot be removed merely because of its name or ignore comment: `scrapper/research/focus/build_p0_governance.py` reads both `OLD/ML_PRO/.../EXCEL-DATA` and `OLD/RESULTS/2026/Q4`. Sample directories also contain research/reference inputs. Externalizing them requires a documented input contract and repaired consumers.

### Regenerable cleanup candidates

Finder `.DS_Store`, pytest metadata and Python bytecode with a corresponding source file are low-impact cleanup candidates. Earlier inspection identified orphan bytecode containing potentially unrecovered former code. Revalidate those exact paths before removal. Browser/Python/model environments are separate investigations: the installed dispatcher may depend on an interpreter outside this tree, and research environments may contain local changes.

## Follow-up: remove the dependency on `OLD/` before archiving it

Owner direction: retain `OLD/`, investigate its callers, and plan removal of the folder dependency first. This follow-up changes the report and classification only; the decoupling is not implemented.

### Exact dependency boundary

A fresh recursive filename census found **937 files** under `OLD/`: PDFs, workbooks, screenshots, webarchives, CSV/JSON/text/Markdown and Finder metadata. No Python, shell, JavaScript/TypeScript, SQL or notebook source files were found. All 47 extensionless files were `.DS_Store`. The folder is a historical input corpus, not the canonical codebase.

Static searches of canonical application, operational, script, experiment and CI source located the hardcoded input paths in **one implementation module**, `scrapper/research/focus/build_p0_governance.py`:

- `build_p0_governance`, around lines 939–950, constructs the Q1 and Q4 directories under `repository_root / OLD/...` and calls the two ingesters. Around line 976 it also supplies Q1 `rejected_candidates.csv` to the candidate-pool builder.
- `_main`, around lines 1511–1514, independently hardcodes the Q4 directory for `--capture-q4-ocr`.
- `scrapper/pipeline/build_p0_governance.py` is a compatibility facade for that owner. Both module entrypoints must retain equivalent behavior after decoupling.
- `--repository-root` can change the prefix today but still requires the full `OLD/...` hierarchy. It does not solve the folder dependency. The lower-level `ingest_q1_focus`, `ingest_q4_focus`, `capture_q4_ocr_evidence` and candidate-pool functions already accept paths; the orchestration and CLI are the main changes needed.
- `--parquet-only` reads existing governance outputs and does not need the original `OLD/` inputs. Supplied Q4 OCR evidence avoids reading the screenshots during ingestion, but its stored source paths still carry historical provenance.

The directly used input set is **19 files / 11,837,166 bytes**, approximately **11.84 MB**, within the approximately 1.19 GB historical folder. Their exact paths, sizes and fresh SHA256 identities are recorded in [OLD_DEPENDENCY_INPUTS.csv](OLD_DEPENDENCY_INPUTS.csv):

- Q1 `stocks.csv`: membership rows and classification; the ingester expects 191 rows.
- Q1 `screener_focus_import.xlsx`: hashed as corroborating evidence. This ingester does not parse its workbook cells, so recording it is not independent workbook reconciliation.
- Q1 `rejected_candidates.csv`: candidate-review pool and source reasons. These rows are not approved negative training labels.
- Q4's 16 top-level PNG screenshots: OCR sources for historical membership, or original evidence behind the retained OCR JSON. The ingester carries a 239-positive expectation; this review did not rerun or certify OCR/membership counts.

Other files under `OLD/` are not read by these identified code paths. That finding is scoped to inspected in-repository consumers; it does not establish that remaining historical documents are redundant or unused by private/manual research.

### Effect on acquisition

`ops/activation/phase4_schedule.py` reads `artifacts/focus_ml/governance/FOCUS_LABEL_REGISTRY_v001.json` through `reporting_focus.membership`. That reader uses resolved company IDs, quarter and labels; it does not dereference each row's `source_file` or open `OLD/`. The dependency chain is therefore:

`historical Focus inputs → explicit governance build → frozen registry → acquisition membership reader`

Removing the build-time path requirement does not require replacing the live registry or changing acquisition schedules. Preserve the installed registry and operational checks during the work. Static inspection does not certify current service health or prove there are no external operator scripts.

### Revised direction: quarterly Focus Lists belong in the database

The owner's follow-up supersedes the input-manifest-only proposal below as the primary architecture. **Import each supplied quarter's list once into versioned database records; have application consumers query those records.** Original screenshots, text and workbooks remain immutable evidence. An explicit input bundle/manifest is useful at the import boundary, but should not remain a recurring runtime requirement for the governance builder or acquisition membership reader.

The owner has confirmed the corrected identities of the two historical lists:

- **2025 Q4:** `OLD/RESULTS/2026/Q4/`. The year in the directory name is incorrect for this list.
- **2026 Q1:** `artifacts/b2-2026-10-01/Q1 2027 FOCUS LIST.txt`. The year in the filename is incorrect for this list.

These owner-confirmed labels govern the planned import. Preserve original paths/names as source metadata; do not derive the list quarter from them. The newest list is unfinished and has not been supplied. Do not create memberships for that newest quarter, treat it as an empty final list, infer its contents, or carry forward an older quarter as its replacement.

**Owner correction recorded: 2025 Q4 and 2026 Q1.** Existing code labels the screenshots `FY2026-Q4`, with period end 31 March 2026, and older records call the text list Q1 FY27. Those existing assignments must not override the corrected list labels or be reused silently during import. Keep list identity separate from fiscal/calendar interpretation, financial period end, list release/decision date and known availability date. The corrected year/quarter labels do not by themselves establish exact period-end or publication dates; leave unsupported dates unknown. Original historical reports and evidence retain their recorded labels, with this correction linked when preparing new outputs.

A fresh parse of the supplied Q1 text file found **389 unique exchange-prefixed symbol entries across 31 supplied headings**, no duplicate tokens and no unrecognized tokens. Its SHA256 is `f90180c0f4f4164c57325c7f69bff45c76bbacf5869c8620d7b5e533961121d6`. This is a source-format count, not 389 verified distinct company identities. The Q1 CSV previously used by the governance builder contains **191 rows**. It must not silently stand in for the owner-designated 389-entry list or be merged into that list. Preserve it as a separate historical source/version pending reconciliation of its scope. Source headings are supplied classifications, not verified sector facts.

No dedicated quarterly Focus List persistence tables were found in the inspected canonical migrations/repository modules. Existing frozen JSON registries and research manifests are separate from a general database-owned quarterly membership model. The production database schema was not opened or checked here.

Proposed logical records, with final schema names to be chosen during implementation:

- **Focus source documents:** immutable original bytes or a verified immutable-content reference, original name, content hash, media type, supplied date and capture/import provenance. Avoid requiring the original absolute filesystem path to retrieve evidence. Preserve original paths as historical metadata, not row identity.
- **Focus list versions:** list identity, raw quarter label, confirmed fiscal/calendar period, version, completeness/review state, supplied/released/available/imported times where known, source references and any superseded version. Keep incomplete/draft imports distinct from owner-confirmed final lists. A final supplied list can still have unresolved company mappings; these are separate states.
- **Focus list entries:** list version, source position/heading, original exchange and symbol or OCR text, original source/page/row evidence, nullable canonical company ID and resolution state/method. Retain unresolved entries without guesses or silent drops. Original-entry count and resolved-company count must be reported separately.
- **Import and resolution evidence:** parser/OCR version, source hashes, import result/counts, reviewer decisions and versioned identity corrections. Keep original raw entries unchanged when resolutions improve. Idempotent import identity includes the source hash and explicit list/version; importing identical bytes into another quarter requires an explicit decision rather than accidental reuse.

Quarterly workflow: receive a source → declare/confirm its quarter and completeness → preserve original evidence → parse text or run OCR → resolve exchange identities against the existing company master → review unresolved/ambiguous entries and counts → accept the version → serve quarter-specific membership from the database. Each later quarter adds a new list/version; it does not require hardcoded quarter constants or edits to folder paths. Corrections append a reviewed revision rather than overwrite previous memberships.

Dependency-removal work should cover these consumers:

1. Add the schema and repository/import layer using the existing database, migration and immutable-content conventions; develop and test in a disposable database first. No production migration or import is part of this report update.
2. Import the owner-designated **2025 Q4** originals and 389-entry **2026 Q1** source into isolated staging under their corrected list labels. Retain the 191-row CSV/workbook and rejected-candidate file as separate historical evidence; their quarter/scope is not corrected by implication, and they do not define the new Q1 list. Review OCR, identity resolution and source/count parity before selecting an accepted version. Review exact period-date mappings separately before any date-sensitive use.
3. Change the governance builder to request an explicit quarter/list version from database records rather than open `OLD/`. Move Q4 OCR capture into the source-import workflow; keep the legacy module facade as an entrypoint where required. The old bounded P0 selector has specific quarter assumptions; general quarterly storage does not automatically generalize or approve that historical experiment.
4. Change `reporting_focus.membership` and the scheduler integration to use the quarter-specific database reader through the existing controlled runtime access pattern. JSON registries may remain reproducible exports for compatibility, but must not be an independently edited authority. Missing/unpublished quarters return an explicit unavailable state, with no previous-quarter fallback.
5. Route reusable research membership readers through the same repository API. The older B2 discovery parser reads the supplied text directly, and `experiments/finance_only_v1/pilot.py` reads a copy under `sample_files-2`; preserve frozen historical runs but make new workflows record the selected database list/version. Do not rerun historical mutation scripts merely to import their reference list.

Acceptance for database ownership: multiple confirmed quarters coexist; exact reimport is idempotent; corrected versions preserve history; raw source bytes/hashes and every entry remain traceable; unresolved symbols do not disappear; missing newest-quarter data remains unavailable; company absence never becomes an approved rejection/negative label; list availability is checked separately for historical use; and the governance/membership workflows run in an isolated checkout without `OLD/` or direct reference-list file dependencies. Verify original-versus-imported membership parity before changing the installed reader. Deploying that reader and applying the migration remain separate operational steps.

**Updated order:** implement the quarterly database import/read contract first, then switch consumers and verify independence from `OLD/`, then consider archival of the folder. Preserve the full historical corpus throughout.

### Earlier input-bundle proposal — supporting import mechanism only

The following was the initial path-decoupling plan. After the owner proposed quarterly database storage, its manifest, hash and provenance controls remain useful for import, but its file-backed builder design is no longer the preferred end state. The database-owned workflow above governs the implementation direction.

1. **Define a small, versioned Focus input bundle outside `OLD/`.** Include the exact 19 originals plus a manifest, and reference the retained Q4 OCR evidence and applicable human approval files. Use a restricted data location rather than embedding the original datasets in the GitHub source tree. Verify each original against the input manifest before treating the bundle as a replacement. Keep `OLD/` intact.
2. **Make the builder consume an explicit manifest.** Add a proposed `--focus-input-manifest` option and corresponding builder parameter resolving Q1 files, Q4 screenshots and optional frozen OCR evidence. Resolve relative physical paths against the manifest, not the checkout or working directory. Remove both hardcoded `OLD/...` constructions, including OCR-capture mode. Do not introduce an automatic fallback or symlink to `OLD/`.
3. **Fail early for missing or mismatched required inputs.** Validate the selected mode's inputs and expected hashes before opening a database or creating partial output. Keep Parquet-only export independent of the original-input manifest. Missing candidates or screenshots must not silently become an empty/negative population. For frozen OCR, explicitly verify its source-hash mapping against the declared bundle; current ingestion trusts the captured hashes without rereading originals.
4. **Separate source identity from physical location.** Existing `source_file` strings contribute to registry `source_hash`, `label_source_id`, and unresolved-row keys. Simply substituting a new directory changes those identifiers even if input bytes are identical. Use a stable manifest source identity with a mapping to physical locations; preserve original provenance and historical evidence unchanged. Any new ID/version scheme needs an explicit old-to-new mapping and a versioned result, rather than silently overwriting a frozen registry.
5. **Document and test the explicit-input workflow.** Update both owner/facade command examples and the external-data contract. Use synthetic fixtures in Git for portable checks; keep the 19 originals local. Review existing FG-00 governance and ownership tests, which cover contracts/facades but do not demonstrate full input-location independence.

Acceptance for the future code change:

- In an isolated checkout that has no `OLD/` directory, configured inputs can build governance output and capture Q4 OCR; Parquet-only mode still works from existing outputs alone.
- With fixed DB fixtures and frozen OCR, relocating the same input bytes preserves company-quarter membership, unresolved states, unknown/negative semantics, source hashes and stable identities according to the agreed mapping. Original-data parity, if needed, uses an isolated restored DB and separate outputs.
- Missing manifest, missing original, hash mismatch and inconsistent OCR provenance produce clear failures without falling back to `OLD/` or changing the live registry.
- Both module entrypoints work. Runtime/deployment source no longer contains a required `OLD/` path; retained historical documentation and provenance strings may still mention it.
- Original `OLD/` bytes, live registry, DB, schedules and acquisition state remain unchanged. No governance rebuild against production is needed to prove path independence.

**Order change:** implement and verify this decoupling before proposing any relocation of `OLD/`. Once it passes, classify the entire folder as retained historical evidence with no required application directory location. Its archival remains a separate, reversible operation; deletion is not proposed.

## Execution sequence and acceptance conditions

1. **Accept this map and define the source boundary.** Use the North Star for product scope, the actual source for callers and current receipts for dated operational outcomes. Keep application, operational state and research evidence ownership explicit. Finish individual review where classifications remain unresolved. Remove the hardcoded `OLD/` input requirement through the follow-up above before any proposed relocation of that folder.
2. **Establish recovery and custody.** Preserve staged, unstaged and untracked work, the nested candidate history and exact selected archive members. For production data, establish a consistent current DB plus matching immutable-content recovery point, record its durable destination, and demonstrate a restore separately. Do not copy a live SQLite main file alone and call it a backup. No durable destination or current restore proof has been verified here.
3. **Repair navigation in a small documentation change.** Make the root README the short entrypoint for current architecture, commands, setup and status. Route to North Star and latest operational evidence; repair the missing plan link and label old status pages as dated. Preserve historical reports. Do not alter schedules while updating text.
4. **Review a concrete first archive batch.** Enumerate exact paths, bytes, hashes, dependencies, destination and restoration mapping. Start with the old source candidate and review exports. Verify a separate retained copy and restore comparison before removing any original. Cache cleanup can follow its own exact list.
5. **Investigate the large storage groups separately.** Review acquisition snapshot generations first, then cleanup harnesses/OCI releases, then research environments/models and generated payloads. Do not infer a deletion budget from current sizes. Repair runtime/test references only in isolated work, with preservation and parity checks.
6. **Prepare a fresh source-only Git candidate.** Use an independent staging directory/history and an explicit path allowlist, incorporating reviewed current changes and migration 076. Reuse the earlier export's mapping and migration-source approach as references, not as a replacement tree. Produce exact inclusion/exclusion lists and a change report against the current canonical checkout.
7. **Verify the candidate from a fresh checkout.** Install locked dependencies into an isolated environment, run migration lineage and disposable-database setup, test CLI routing and bounded scheduler/document behavior with fixtures, and validate links/imports/config contracts. Tests needing private evidence must have reviewed synthetic fixtures or an explicit external-input contract. Report the supported suite and any excluded research/deployment workflows accurately. Do not run broad tests against production mounts or automatically invoke historical campaign scripts.
8. **Publish the accepted candidate to the selected GitHub destination.** Confirm the repository and visibility, review the exact commit/manifest and scan selected files plus their new history. Resolve the documented credential-exposure follow-up and source/fixture sharing decisions. Push the reviewed source lineage once those decisions are complete. Current scope is understanding and planning; no remote was queried or changed.

Acceptance for a clean Git project: one canonical code tree; all intended source changes accounted for; reproducible declared setup and checks; no production data, credentials, environment caches or bulk archives in the manifest/history; external-data requirements documented; current documentation links valid; archive recovery demonstrated for any removed local originals.

## Open decisions and limits

- Durable archive/backup destination and retained database/release generations have not been selected or verified.
- Whether to include each research experiment and its fixtures in the first GitHub version needs a path-level decision. Initial recommendation: retain reusable research source where its dependency contract is clear; keep large/private experimental evidence external.
- GitHub destination and current remote visibility are unverified. A fresh independent source lineage is the recommended approach because the original history mixes large evidence and historical credential material. Do not reset or rewrite the original repository to prepare it.
- The prior [publication decision](../../github-readiness/2026-10-23-export/PUBLICATION_DECISION.md) and [credential handling record](../../github-readiness/2026-10-23-export/CREDENTIAL_REMEDIATION.md) remain unresolved records, not fresh verification. Publication has not been cleared by this planning pass.
- No full dead-code analysis, archive-content comparison, backup restore, secret scan, test execution or production-health check was performed. The existing [26 October audit](../../CLEANUP_AUDIT_2026-10-26.md) supplies deeper historical investigation; fresh observations above supersede its changed counts and finite-schedule finding only.

This plan defines the next reviewable work. It does not declare any large backup unusable or any entire evidence directory disposable without the verification listed above.
