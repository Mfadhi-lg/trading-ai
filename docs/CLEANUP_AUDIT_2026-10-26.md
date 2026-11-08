# Trading AI repository cleanup audit — 26 October 2026

## 1. Executive summary and audit boundary

**Decision: preserve the operational system; approve no blanket deletion.** The canonical application remains `scrapper/`. Most disk usage is retained databases, acquisition records, historical engineering evidence and research runtimes, rather than competing application source.

**VERIFIED filesystem observation at 2026-10-26T10:07:26.771026+00:00:** **148,731 file entries**, comprising **148,701 regular files and 30 file symlinks**, with **102,792,168,876 logical bytes (102.792 GB; 95.733 GiB)**. Summed file allocation is **103,212,769,280 bytes (96.124 GiB)**. These totals precede this report. Include root `.git`, ignored/untracked data and the candidate's nested `.git`; exclude external Docker volumes, the external document drive, sibling archives, directory metadata and symlink-directory targets. `du -sk .` independently observed 100,791,544 KiB during inspection; filesystem activity and directory accounting explain small differences. APFS clones, compression, hard links and Git history mean these are not guaranteed uniquely reclaimable physical bytes.

**VERIFIED Git identity:** branch `chore/root-hygiene-pr`, HEAD `0e6ee264931ba1a1e009e0785cf188f5dc9f37e8`. Status at report inventory: `{' M': 24, 'A ': 823, 'AM': 2, 'RM': 1, 'MM': 2, '??': 2992}` (porcelain XY; untracked expanded to files). There are substantial pre-existing staged changes and uncommitted source/evidence. HEAD alone cannot recover this workspace. The checkout changed during read-only observation (additional recovery files appeared); this is an interval inventory, not an atomic filesystem or database snapshot. Do not attribute concurrent operator/worker changes to this audit.

The installed and loaded Aqua LaunchAgent invokes `ops/activation/automatic_acquisition.py --tick` every 60 seconds. At the separate read-only runtime observation it was idle between ticks, with last exit code 0 and 370 runs. Docker listed **15 retained Trading AI containers, all exited**, and no running Trading AI container at that moment. Idle job/container states do not establish current ingestion health, queue eligibility or successful future runs. The retained `last-tick.json` inspected here was dated `2026-10-26 07:00:46.305997+00:00`, older than report time; investigate freshness separately without restarting anything.

**First proposed batch:** externally preserve the independent source candidate, the two root ZIP exports and the retired five-file Upstox proposal, then verify restoration before removing their workspace copies. Combined logical footprint is **91,622,241 bytes**. This is a small, understandable navigation improvement. Larger savings remain gated investigations.

### Sources and authority

All four requested root documents exist and were inspected: `TRADING_AI_NORTH_STAR.md`, `README.md`, `TRADING_AI_MASTER_ROADMAP_V4_2026-09-29.md`, `TRADING_AI_CLEANUP_EXECUTION.md`. Also inspected C28.1, ROOT-HYGIENE, retention/readiness records, current operation guidance, research READMEs, source/configuration, CI and live filesystem/Git metadata. No applicable root/ancestor/canonical-source `AGENTS.md` was found. Two vendor `AGENTS.md` files exist inside the isolated TeleOCR Rust dependency/source subtree; they do not govern canonical repository source or this report.

The North Star controls product direction: financial + credit/PPT/transcript evidence, verified point-in-time company features, financial-only comparisons and human Purple List decisions. V4's deterministic Focus-first order and C28's structural completion are historical context. C28 completion does not authorize rerunning a refactor, deleting retained evidence, activating services or promoting prototypes. The user's single-report instruction takes precedence over North Star's suggested two-file inventory output; no extra inventory/CSV/canvas was created.

**Methods/limits:** no application imports, tests, CLI status/doctor commands, inference, collection, DB opens, migrations or environment activation were run. Only filesystem/Git reads, static searches, bounded exact comparisons, archive metadata/member reads and read-only Docker/launchctl inspection. The runtime reads initially hit sandbox boundaries and succeeded after tool approval. No credential values were opened or printed. ZIP secret checks inspect member names, not payloads; they are not a full embedded-secret scan. No massive dataset or four 14.6 GB DBs were fully hashed. No database backup was created by this audit.

## 2. Live repository structure and size breakdown

Counts below use one metadata traversal and root Git index/ignore membership. Tracked files remain tracked even when `.gitignore` matches them. The candidate's files are untracked relative to the parent repository, even though its own independent index is clean. Git internals are never disposable untracked files.

| Area | File entries | Logical size | Tracked / ignored / untracked | Classification | Purpose and dependency evidence |
| --- | --- | --- | --- | --- | --- |
| `artifacts` | 134,710 | 86,844,763,703 B (80.880 GiB) | 23,440 / 108,685 / 2,585 | MIXED: KEEP_ACTIVE / KEEP_REFERENCE / NEEDS_INVESTIGATION | Acquisition receipts, operational state, frozen evaluations, snapshot DBs, OCI releases, research environments; classification below. |
| `docs` | 2,086 | 9,623,341,299 B (8.962 GiB) | 1,895 / 30 / 161 | KEEP_REFERENCE; current controls KEEP_ACTIVE | Plans, retained patches/harnesses, migration identity evidence and current operational guidance; not disposable reports. |
| `.git` | 2,419 | 4,253,684,224 B (3.962 GiB) | N/A (Git internals) | KEEP_ACTIVE / PROTECTED | History, index/staged work, refs and worktree administration; not an untracked cleanup target. |
| `OLD` | 937 | 1,193,505,792 B (1.112 GiB) | 0 / 937 / 0 | KEEP_REFERENCE; Focus input paths KEEP_ACTIVE | Original financial/market/Focus material; governance builder still reads two subtrees. |
| `scrapper` | 7,440 | 747,229,183 B (712.61 MiB) | 643 / 6,714 / 83 | KEEP_ACTIVE; store KEEP_REFERENCE/protected | Canonical Python application, tests/fixtures, source manifests, migrations; local historical store and research environment. |
| `[root files]` | 22 | 84,189,619 B (80.29 MiB) | 12 / 7 / 3 | MIXED; see root manifest | Operational wrappers/config, authoritative direction, credentials/runtime state, historical plans and review ZIPs. |
| `sample_files-2` | 28 | 20,393,773 B (19.45 MiB) | 0 / 28 / 0 | KEEP_REFERENCE | 17 originals and membership manifests; consumed by current research prototypes. |
| `sample_files` | 14 | 8,740,512 B (8.34 MiB) | 0 / 14 / 0 | KEEP_REFERENCE | 11 original PDFs and source manifests; benchmark inputs. |
| `github-source-candidate-20261023` | 744 | 7,641,010 B (7.29 MiB) | 0 / 0 / 744 | ARCHIVE_CANDIDATE | Independent publication candidate with divergent source and its own Git repository; preserve complete tree. |
| `experiments` | 54 | 5,693,104 B (5.43 MiB) | 5 / 19 / 30 | KEEP_REFERENCE; Upstox ARCHIVE_CANDIDATE | Two bounded financial/document research prototypes and one retired blocked provider proposal. |
| `ops` | 257 | 2,391,951 B (2.28 MiB) | 61 / 70 / 126 | KEEP_ACTIVE; dated verification KEEP_REFERENCE | Installed host dispatcher/lifecycle, pinned release identities, adapters, ownership guards and recovery tooling. |
| `resources` | 1 | 397,816 B (0.38 MiB) | 1 / 0 / 0 | KEEP_ACTIVE | Canonical exchange reference CSV locator input. |
| `.pytest_cache` | 5 | 120,498 B (0.11 MiB) | 0 / 5 / 0 | DELETE_CANDIDATE | Pytest-generated cache; explicit approval and no active pytest owner required. |
| `scripts` | 12 | 74,767 B (0.07 MiB) | 7 / 2 / 3 | KEEP_ACTIVE / KEEP_REFERENCE | Migration/import audits, cumulative materializer and credit-intelligence CLI wrappers. |
| `.github` | 2 | 1,625 B (0.00 MiB) | 2 / 0 / 0 | KEEP_ACTIVE | Migration-lineage and offline ML-readiness CI; depend on retained evidence and fixtures. |
| `exchange-inbox/` | 0 | 0 B | empty; path ignored | KEEP_ACTIVE | Existing read-only /inbox bind mount in root/manual/development Compose; absence of files is not permission to remove the directory. |

### Root-level exact inventory

Secrets/state rows describe presence, size and custody only. `screener-token` is a protected credential-management utility; its name alone is not evidence of a stored credential.

| Root path | Bytes | Git status | Classification | Reason |
| --- | --- | --- | --- | --- |
| `scraper` | 158 | tracked | KEEP_ACTIVE | Canonical direction, entrypoint, dependency/build or deployment configuration. |
| `.screener_csrf` | 32 | ignored | KEEP_ACTIVE / PROTECTED | Credential/session or retained lifecycle state; metadata only. |
| `sample_files-2.zip` | 18,652,381 | ignored | ARCHIVE_CANDIDATE | Dated share/export; preserve original archive and metadata, then remove local copy only after approval. |
| `.DS_Store` | 10,244 | ignored | DELETE_CANDIDATE | Finder metadata; no source consumer in bounded code/config search. |
| `Dockerfile` | 1,674 | tracked | KEEP_ACTIVE | Canonical direction, entrypoint, dependency/build or deployment configuration. |
| `TRADING_AI_NORTH_STAR.md` | 20,357 | untracked | KEEP_ACTIVE | Canonical direction, entrypoint, dependency/build or deployment configuration. |
| `.scraper_shutdown_state` | 386 | ignored | KEEP_ACTIVE / PROTECTED | Credential/session or retained lifecycle state; metadata only. |
| `compose.browser-recovery.development.yml` | 1,081 | tracked | KEEP_ACTIVE | Canonical direction, entrypoint, dependency/build or deployment configuration. |
| `README.md` | 5,897 | tracked | KEEP_ACTIVE | Canonical direction, entrypoint, dependency/build or deployment configuration. |
| `.dockerignore` | 430 | tracked | KEEP_ACTIVE | Canonical direction, entrypoint, dependency/build or deployment configuration. |
| `.gitignore` | 1,404 | tracked | KEEP_ACTIVE | Canonical direction, entrypoint, dependency/build or deployment configuration. |
| `trading-ai-code-review-compact.zip` | 65,324,707 | ignored | ARCHIVE_CANDIDATE | Dated share/export; preserve original archive and metadata, then remove local copy only after approval. |
| `.env` | 190 | ignored | KEEP_ACTIVE / PROTECTED | Credential/session or retained lifecycle state; metadata only. |
| `TRADING_AI_MASTER_ROADMAP_V4_2026-09-29.md` | 37,589 | tracked | KEEP_REFERENCE | Historical decisions, cleanup evidence routing or sharing provenance. |
| `scraper-monitor` | 156 | tracked | KEEP_ACTIVE | Canonical direction, entrypoint, dependency/build or deployment configuration. |
| `.screener_session` | 33 | ignored | KEEP_ACTIVE / PROTECTED | Credential/session or retained lifecycle state; metadata only. |
| `compose.development.yml` | 4,227 | tracked | KEEP_ACTIVE | Canonical direction, entrypoint, dependency/build or deployment configuration. |
| `screener-token` | 1,425 | untracked | KEEP_ACTIVE | Canonical direction, entrypoint, dependency/build or deployment configuration. |
| `SHARE_MANIFEST.md` | 11,007 | untracked | KEEP_REFERENCE | Historical decisions, cleanup evidence routing or sharing provenance. |
| `compose.yml` | 15,633 | tracked | KEEP_ACTIVE | Canonical direction, entrypoint, dependency/build or deployment configuration. |
| `TRADING_AI_CLEANUP_EXECUTION.md` | 98,185 | tracked | KEEP_REFERENCE | Historical decisions, cleanup evidence routing or sharing provenance. |
| `compose.browser-recovery.yml` | 2,423 | tracked | KEEP_ACTIVE | Canonical direction, entrypoint, dependency/build or deployment configuration. |

### Artifact families: every existing immediate directory

Classification is conservative where only bounded dependency checks were possible. Root artifact HTML/JSON files are retained source/acceptance evidence (`KEEP_REFERENCE`); root Finder metadata is separately listed below.

| Artifact directory | Entries | Logical size | Classification | Evidence / handling |
| --- | --- | --- | --- | --- |
| `artifacts/a0-2026-09-29/` | 15 | 151,782,603 B (144.75 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/a2-2026-09-29/` | 12 | 743,499 B (0.71 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/acquisition-readiness/` | 58 | 61,184,732,601 B (56.983 GiB) | KEEP_ACTIVE + KEEP_REFERENCE | Dispatcher/runtime guards read state, rollout, source/identity or baseline evidence in these acquisition families; preserve paths and locks. |
| `artifacts/acquisition-recovery-20261026/` | 51 | 31,672,073 B (30.20 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/acquisition-repair-20261024/` | 71 | 510,686 B (0.49 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/acquisition-restart-20261022/` | 94 | 804,963 B (0.77 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/automatic-acquisition/` | 3,665 | 16,221,301 B (15.47 MiB) | KEEP_ACTIVE + KEEP_REFERENCE | Dispatcher/runtime guards read state, rollout, source/identity or baseline evidence in these acquisition families; preserve paths and locks. |
| `artifacts/b0-2026-09-29/` | 14 | 213,662 B (0.20 MiB) | KEEP_REFERENCE | Financial eligibility, Focus membership, governance and historical comparison evidence; superseded rules are audit-only, not waste. |
| `artifacts/b0-2026-09-30/` | 7 | 59,012 B (0.06 MiB) | KEEP_REFERENCE | Financial eligibility, Focus membership, governance and historical comparison evidence; superseded rules are audit-only, not waste. |
| `artifacts/b1-2026-10-01/` | 8 | 901,468 B (0.86 MiB) | KEEP_REFERENCE | Financial eligibility, Focus membership, governance and historical comparison evidence; superseded rules are audit-only, not waste. |
| `artifacts/b2-2026-10-01/` | 16 | 2,067,277 B (1.97 MiB) | KEEP_REFERENCE | Financial eligibility, Focus membership, governance and historical comparison evidence; superseded rules are audit-only, not waste. |
| `artifacts/b2-discovery-2026-10-01/` | 45 | 898,288,949 B (856.68 MiB) | KEEP_REFERENCE | Financial eligibility, Focus membership, governance and historical comparison evidence; superseded rules are audit-only, not waste. |
| `artifacts/bse-browser-integration-2026-10-09/` | 6 | 121,444 B (0.12 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/bse-browser-investigation-2026-10-09/` | 2 | 28,270 B (0.03 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/bse-browser-results/` | 10 | 153,152 B (0.15 MiB) | KEEP_ACTIVE + KEEP_REFERENCE | Dispatcher/runtime guards read state, rollout, source/identity or baseline evidence in these acquisition families; preserve paths and locks. |
| `artifacts/bse-ownership-fix-2026-10-09/` | 6 | 13,445 B (0.01 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/bse-production-ingestion-2026-10-09/` | 17 | 292,416 B (0.28 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/bse-production-ingestion-ownership-fixed-2026-10-09/` | 19 | 306,406 B (0.29 MiB) | KEEP_ACTIVE + KEEP_REFERENCE | Dispatcher/runtime guards read state, rollout, source/identity or baseline evidence in these acquisition families; preserve paths and locks. |
| `artifacts/bse-production-ingestion-preflight-2026-10-09/` | 2 | 4,786 B (0.00 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/bse-production-ingestion-wal-validated-2026-10-09/` | 17 | 339,947 B (0.32 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/bse-visible-runner-verification-2026-10-09/` | 7 | 104,618 B (0.10 MiB) | KEEP_ACTIVE + KEEP_REFERENCE | Dispatcher/runtime guards read state, rollout, source/identity or baseline evidence in these acquisition families; preserve paths and locks. |
| `artifacts/bse-wal-guard-fix-2026-10-09/` | 7 | 26,614 B (0.03 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/coverage-audit-2026-10-01/` | 34 | 84,005,612 B (80.11 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/credit-rating-intelligence-v1/` | 852 | 58,172,996 B (55.48 MiB) | KEEP_REFERENCE; environments NEEDS_INVESTIGATION | Frozen sources, source review, paid responses, benchmarks/negative results and experimental tools; tests/docs reference outputs. No bulk deletion. |
| `artifacts/document-acquisition/` | 23,225 | 12,131,361,398 B (11.298 GiB) | KEEP_REFERENCE | Historical acquisition/extraction decisions and progress; large JSON volume alone does not establish duplication. |
| `artifacts/focus_ml/` | 23 | 961,782 B (0.92 MiB) | KEEP_REFERENCE | Financial eligibility, Focus membership, governance and historical comparison evidence; superseded rules are audit-only, not waste. |
| `artifacts/icra-download-test/` | 1,261 | 153,557,856 B (146.44 MiB) | KEEP_REFERENCE; .venv NEEDS_INVESTIGATION | Retained browser recovery source/logs plus ignored environment; protected campaign record, not just a test cache. |
| `artifacts/operational-recovery/` | 12 | 458,230 B (0.44 MiB) | KEEP_ACTIVE + KEEP_REFERENCE | Dispatcher/runtime guards read state, rollout, source/identity or baseline evidence in these acquisition families; preserve paths and locks. |
| `artifacts/phase3-completion/` | 74 | 200,132 B (0.19 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/phase4/` | 392 | 8,723,845 B (8.32 MiB) | KEEP_ACTIVE + KEEP_REFERENCE | Dispatcher/runtime guards read state, rollout, source/identity or baseline evidence in these acquisition families; preserve paths and locks. |
| `artifacts/phase5/` | 103,193 | 11,961,924,669 B (11.140 GiB) | KEEP_REFERENCE; environments NEEDS_INVESTIGATION | Frozen sources, source review, paid responses, benchmarks/negative results and experimental tools; tests/docs reference outputs. No bulk deletion. |
| `artifacts/rating-exception-recovery-20261001/` | 23 | 1,606,758 B (1.53 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/rating-link-refresh-20261001/` | 2 | 191,125 B (0.18 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/screener-supplemental-access/` | 14 | 40,997 B (0.04 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/step3-financial-canary-2026-10-09/` | 10 | 45,674 B (0.04 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/step3-financial-readiness/` | 45 | 176,871 B (0.17 MiB) | KEEP_ACTIVE + KEEP_REFERENCE | Dispatcher/runtime guards read state, rollout, source/identity or baseline evidence in these acquisition families; preserve paths and locks. |
| `artifacts/step3-financial-retry-2026-10-09/` | 10 | 114,658 B (0.11 MiB) | KEEP_ACTIVE + KEEP_REFERENCE | Dispatcher/runtime guards read state, rollout, source/identity or baseline evidence in these acquisition families; preserve paths and locks. |
| `artifacts/step3-operational-activation/` | 1 | 5,911 B (0.01 MiB) | KEEP_REFERENCE | Dated acquisition investigation/activation/recovery evidence; uniqueness and full dependency closure not established, so no move/delete proposed. |
| `artifacts/three-document-reality-check-v0.4/` | 882 | 104,804,219 B (99.95 MiB) | KEEP_REFERENCE; environments NEEDS_INVESTIGATION | Frozen sources, source review, paid responses, benchmarks/negative results and experimental tools; tests/docs reference outputs. No bulk deletion. |
| `artifacts/unified-financial-pilot-v0.1/` | 501 | 46,985,954 B (44.81 MiB) | KEEP_REFERENCE; environments NEEDS_INVESTIGATION | Frozen sources, source review, paid responses, benchmarks/negative results and experimental tools; tests/docs reference outputs. No bulk deletion. |

### Other important overlaps and subareas

- `scrapper/store/`: **590,982,179 B (563.60 MiB)**, 912 entries at initial inventory; exports about 487.2 MB, snapshots 88.0 MB and MF imports 12.0 MB. The local `scraper.db` is 3,833,856 B with WAL/SHM present. Its small size does not make it disposable. It is separate from the documented production Docker database.
- `OLD/ML_PRO/`: 916 entries / 1,181,715,113 B; `OLD/RESULTS/`: 20 / 11,782,483 B. Data dominates; historical folder naming and `.gitignore`'s “superseded” comment are misleading about active input dependencies.
- `docs/cleanup/`: 1,176 entries / 9,504,210,608 B at initial inventory. `baselines/` is 9,503,086,562 B. Keep chunks and routing/indexes; investigate individual heavy harness files rather than moving this subtree wholesale.
- `docs/acquisition-readiness/`: 764 entries / 55,756,715 B. Includes seals, reviews and source checksums explicitly read by activation guards; `KEEP_ACTIVE` plus historical evidence.
- `docs/github-readiness/`: 83 entries / 29,591,858 B; publication/readiness history, `KEEP_REFERENCE`. `docs/audits/`: 6 / 31,981,165 B; retained audit evidence, `KEEP_REFERENCE`.
- `docs/current/`, `docs/operations/`, `docs/architecture/`, `docs/research/`, `docs/phase5/` and `docs/credit-rating-intelligence-v1/`: current guidance, contracts or reproducibility records. Keep; later navigation/link repair is a separate change.
- `ops/activation/` is only about 1.87 MB including caches; it is operationally critical despite containing one-shot preparation and historical verification scripts. `ops/docker/release/` seals and config, `ops/host/` lifecycle/monitor and `ops/runbooks/` remain. `ops/campaigns/` and `ops/benchmarks/` are retained operational/reference tools; no individual dead-code finding supports removal.
- `resources/exchange/exchange_securities_master.csv`: 397,816 B, tracked. `scrapper/sources/exchange/reference.py:locate_reference_file` prefers this exact path, retaining a legacy root fallback. Do not remove it because an old hygiene record describes removal of the former root duplicate.
- `exchange-inbox/` is empty but still mounted as `/inbox:ro` in root/manual/development Compose definitions. No disk saving justifies removing an operational input path.

## 3. Authoritative implementation and operational ownership map

“Operational implementation” identifies source ownership and proven callers/configuration. It does not assert that every function is currently scheduled or that host bytes equal all pinned container image bytes.

| Capability | Canonical source and real entrypoints | Persistence/configuration | Activation evidence and limits |
| --- | --- | --- | --- |
| Company master / security identity | scrapper/sources/exchange/security_master.py, reference.py, datasets.py; workflows/exchange/universe.py and reference_import.py; db/exchange/securities.py, snapshots.py; db/repo.py company/source-identifier functions. CLI exchange/reference dispatch and explicit import workflow. | resources/exchange/exchange_securities_master.csv; companies, security_identifiers, exchange security/version/snapshot mappings; SCRAPER_DB_PATH. | Canonical workflows, CLI and resource locator are verified. Universe/master refresh capability exists; result-feed scheduling alone does not prove periodic refresh of every master dataset. |
| Financial acquisition | ops/activation/automatic_acquisition.py -> step3_schedule.py -> step3_financial.py / step3_financial_runtime.py; workflows/screener/company_worker.py and ingest.py; sources/screener/company.py, statements.py, assembly.py; core/http.py. | Screener cookie supplied through file, never printed; response_blobs, parse_artifacts, financial_observations, source-table cells, accepted_page_versions; financial/company queues and collection scopes. | Installed dispatcher references current bounded financial/cap lanes. Historical facade pipeline/screener_vertical.py delegates to canonical workflows and preserves contracts; it is not a removable duplicate. |
| Exchange announcement discovery | automatic_acquisition.py -> nse_results.py / nse_results_runtime.py; bse_scheduled_runtime.py and sources/exchange/browser_results.py; result_filings.py, reporting_periods.py; workflows/exchange/universe.py and schedule.py. | Pinned ar02-period release and combined-release review; exchange filings/snapshots/access events, enqueue records, source policy and owner locks. | Loaded dispatcher and current source establish admission ownership. Visible Chrome BSE + bounded writer, finite NSE intake; legacy exchange coordinator/scheduler remains callable but is not the installed continuously running owner. |
| Document discovery and downloading | ops/activation/phase4_schedule.py, phase4_documents.py, phase4_routine_runtime.py -> operations/workers/automatic_documents.py, commands/automatic_documents.py; workflows/documents/discovery.py, screener_refresh.py, download.py; documents/download/transport.py, isolated_http.py, tls.py. Generic and direct-rating/browser recovery remain distinct. | document_artifacts / artifact_contents; artifact_download_queue, rating/recovery attempts, host permits/health, immutable document store; phase4_schedule.json and retained rollout/identity evidence. | Finite document authorization in current JSON expired 25 October 2026 18:43:41 IST. Source now supports continuous mode but current configuration does not select it. Do not extend authorization or infer activation from enabled=true. |
| Financial normalization / features | sources/screener/statements.py + core/nums.py normalize accepted raw-cell data; validation/screener.py / screener_rules.py; db/screener/observations.py and acceptance.py; research/features/financial.py:build_screener_ml_features and company_quarter_features. | Versioned parse/blob/observation lineage, units/missing states, standalone scope and acceptance/capture cutoff; pipeline/ml_contracts.py is compatibility facade. | C25/B1 canonical feature interface retained and tested historically. Capture-aware eligibility is not certified public-availability PIT. experiments/finance_only_v1/engine.py adds bounded pilot formulas, not a competing production store or released screener. |
| Parsing/extraction and document intelligence research | documents/extraction.py, pdf.py, powerpoint.py, html.py, ocr.py; pipeline/extract_document.py; operations/workers/document_processing.py. Research only: research/document_intelligence/offline.py, extract.py, source_review.py, credit_*.py; research/general_ai_experiment/runner.py; scripts/credit-intelligence-offline, -fresh, -questions; experiments/unified_financial_pilot/. | document_extractions, document_pages/elements, processing jobs/inbox; originals remain in artifact_content. Research outputs under artifacts/phase5, credit-rating-intelligence-v1 and unified-financial-pilot-v0.1. | Production deterministic parsing is separate from AI candidate extraction. Research scripts may issue paid calls when explicitly run; none run here. Phase 5/credit reports do not certify production or ML feature quality. |
| Focus governance / historical evaluation | research/focus/build_p0_governance.py; research/governance/focus_p0.py; research/features/financial.py; legacy governance/focus_p0.py and pipeline/build_p0_governance.py facades. Retained B0/B1/B2/discovery tools and four-company financial pilot. | OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/EXCEL-DATA; OLD/RESULTS/2026/Q4; artifacts/focus_ml/governance; artifacts/b2-2026-10-01/Q1 2027 FOCUS LIST.txt and resolution evidence; sample_files-2 membership manifests. | Governance/selection tooling and positive membership evidence exist. No production-approved deterministic B2 Focus release, defensible complete negative labels, validated historical universe or ML ranking service was established. B2 threshold draft is explicitly superseded, incomplete and audit-only. |

### Configuration, schedules and deployment dependencies

- Root `scraper` and `scraper-monitor` delegate to `ops/host/scraper_lifecycle.sh`, which routes to `run_acquisition_lifecycle.py`, `acquisition_terminal.py` and `acquisition_lifecycle.py`. Continuous legacy worker shortcuts are explicitly refused. Other manual CLI commands can still dispatch through root Compose; these compatibility paths are not dead code.
- Installed plist: `~/Library/LaunchAgents/com.trading-ai.automatic-acquisition.plist`, label `com.trading-ai.automatic-acquisition`, Aqua session, RunAtLoad and 60-second interval. Interpreter is `/Users/mohamedfadhi/.cache/uv/archive-v0/-WyGD_mChAFVX11K/bin/python`; loaded definition also uses an external uv site-packages path. These external environments are protected runtime dependencies, not repository cleanup savings.
- Static schedule: NSE Monday–Saturday 19:30 IST; BSE daily 09:00 and 18:00. Financial :15/:45, at most two companies per slot, historical allowance at 00:45/06:45/12:45/18:45 (one per slot, four/day). Cap :00 except 09:00/18:00, at most 40 checks. Current-minute admission with no calendar catch-up. Finite document schedule, when authorized: discovery at 00:05/06:05/12:05/18:05, download :25, processing :35, patrol 02:05, rating 03:05; current finite window is expired. This is source/config evidence, not a trigger to execute jobs.
- Critical config: `ops/activation/automatic-acquisition-manifest.json`, `step3_schedule.json`, `phase4_schedule.json`, `scheduled-bse-permission.json`, prepared Compose/runtime identity files, `.scheduler-admission.lock`, `.nse-owner.lock`, retained rollout/seal/baseline files and lifecycle pause/previous state. Configuration guards hash source and historical review paths; relocating apparently historical evidence may block current acquisition.
- Documented production database: Docker volume `trading-ai_scraper-data`, in-container `/data/scraper.db`, with `/data/snapshots` and `/data/exports`. The host `scrapper/store/scraper.db` is not a substitute. Current census, schema/ledger and volume restoration were not run here; storage identity is supported by reviewed source and dated operation records.
- External documents: `/Volumes/My Passport/passport/trading-ai`, including `artifact_content`, UUID/physical sentinel/capacity checks. Docker Desktop, pinned images, logged-in Aqua session, visible Chrome, Screener session and host capacity publisher remain dependencies. No external corpus traversal or relocation was performed.
- Root `compose.yml` retains pinned C05.3 image `44da7074157f…`; activation code selects later accepted images, exact hashes and approved read-only overlays. Root Dockerfile, lockfiles and browser requirements are build/recovery inputs. Presence of a Docker service definition does not establish active ownership. Do not run broad Compose startup/rebuild or prune images/volumes.

## 4. Protected assets and backup prerequisites

**KEEP untouched:** financial/market history, original HTML/PDF/PPT/audio/workbooks, source URLs/hashes, acquisition/access/acceptance records, failed/retry/unavailable decisions, queues/leases/checkpoints/locks, local and Docker databases plus WAL/SHM, immutable content/version lineage, Focus membership/approvals/decision evidence, original benchmark inputs and results (including failures), migrations, Git history/index and all uncommitted work.

Credential presence verified without values: `.env`, `.screener_csrf`, `.screener_session`; all three are absent from the current Git index and ignored. `screener-token` exists untracked and is a protected utility. Filename-only Git history inspection shows `.screener_csrf` in two reachable commits; `.env` and `.screener_session` have no matching filename commits in this bounded query. This corroborates the historical-exposure concern but does not reveal values, prove present credential validity, or certify all renamed/embedded secrets. Preserve local files; any private credential/history remediation requires its own authorized task. No push/publication is cleared by ignore rules or by this audit.

Both root ZIPs lack exact `.env`/session/CSRF and cookie/storage-state named members in the bounded filename scan; content-level embedded-secret absence is **unverified**. Do not publish the exports or new retention archives on that basis.

**Existing recovery evidence (not a new backup):** `artifacts/acquisition-recovery-20261026/raw/backup.json` reports PASS, 14,662,606,848 B, quick_check=ok and source_unchanged=true, at `/recovery-backups/acquisition-recovery-20261026-20261026T094742Z.db`. This is an in-container/configured path; its host/volume mapping, durability, full hash and independent restore are not verified by this audit. `raw/archive-verification.json` reports 12/12 sampled items passed, not full-corpus recovery certification. Do not treat either as authority to discard older snapshots.

The sibling archive `../trading-ai-retained-cleanup-archive-2026-10-07/` exists. Current full-file SHA256 checks match ROOT-HYGIENE's recorded hashes for `cleanup-candidate.tar.gz` (`8514e12167e6e774f82b2f50467908921d51f2992d5a17a296c0f1ed3add3c7c`), `root-local-retention.tar.gz` (`4403e7b9dd98b3262c3526e25ac1781d2e6223ad6bd8644e69ad04d14f96412d`) and `MANIFEST.json` (`f79959dd817fb7164b241bb1ea4ee5980c0adbdb8741291c7735812156a14189`). This verifies those archive files' identity today; it does not newly verify all restored members, current DBs, research data or the new publication candidate.

Before any removal: capture original relative paths, sizes, modes, timestamps, symlink targets and SHA256 at an owner-selected durable destination; preserve parent Git history plus staged/index/unstaged/untracked work and the candidate's complete independent Git repository; verify archive plus member hashes; restore selected members into a separate disposable review directory and compare bytes/modes; keep recovery instructions and reference routing. Databases require an independently validated consistent backup and restore plan; copying a live DB file without coordinated SQLite backup/sidecar handling is not acceptable.

## 5. Active versus historical experiments

| Area | Classification / disposition | Verified role, outcomes and dependencies |
| --- | --- | --- |
| experiments/finance_only_v1/ | KEEP_REFERENCE; completed bounded prototype | 19 entries / 5,289,735 B at initial inventory. Four-company standalone deterministic financial profiles, evidence export and acceptance limits. pilot.py reads the exact AR-01.3B snapshot and sample_files-2 Focus reference/resolution CSV. ACCEPTANCE.md accepts local research only; original HTML reconciliation was 0/4 and independent financial certification remains open. Preserve engine, source-cell evidence, tests and negative/unknown results. |
| experiments/unified_financial_pilot/ + artifacts/unified-financial-pilot-v0.1/ | KEEP_REFERENCE; recent bounded research implementation | 30 code/config/cache entries / 399,226 B; artifact bundle 501 / 46,985,954 B initially. Four-company/12-original sample uses membership manifests, offline.extract_local/deny_network and existing D03 parsing. Provider/runner can call inference when explicitly invoked; do not run. Preserve development/evaluation boundaries, review attestations, source geometry and outcomes. |
| experiments/upstox_fundamentals_pilot/ | ARCHIVE_CANDIDATE | Five files / 4,143 B. Blocked September access/scope proposal, no Python adapter; V4 explicitly retired this provider detour. Archive original blocked/negative findings rather than delete them or reopen testing. |
| scrapper/research/document_intelligence/ + artifacts/phase5/ | KEEP_REFERENCE; supported research code stays canonical | Offline extraction, atomic candidates, ratings/transcripts, numeric/native layout, temporal/provenance checks, source review, evaluation and numerous CDM/model comparisons. Tests directly read offline-demo, quality-evaluation, corrections, bottleneck and generalization inputs. Do not mistake generated HTML/JSON for disposable output. |
| scrapper/research/general_ai_experiment/ + docs/phase5/general-ai-experiment/ | KEEP_REFERENCE; bounded model-comparison record | Original prompts, native/page packets, raw successful/failed paid responses, usage/cost, holdout reference/config freezes and verifier versions. Local Qwen checkpoint load/timeout is a negative result worth retaining; no completed inference or improved accuracy is inferred from installed weights. |
| docs/credit-rating-intelligence-v1/, artifacts/credit-rating-intelligence-v1/, credit_*.py and scripts/credit-intelligence-* | KEEP_REFERENCE; current research capability | Evidence-reviewed disclosure/atomic/fresh/two-stage/question variants are separate experiment stages and CLI entrypoints. Similar names do not establish redundancy. Preserve original paid responses, reviewer decisions and withheld claims; no production or complete financial certification follows from counts. |
| artifacts/three-document-reality-check-v0.4/ | KEEP_REFERENCE | 882 entries / 104,804,219 B. Source/diagnostic evidence; not a basis to expand model runs or erase unsuccessful approaches. |
| artifacts/b0-*, b1-*, b2-*, b2-discovery-*, focus_ml/ | KEEP_REFERENCE | B1 canonical feature work was ported selectively. B2_SUPERSESSION_RECORD explicitly labels former threshold/freeze artifacts SUPERSEDED_PROVISIONAL and incomplete selector audit-only. Positive membership, eligible-not-selected and mentor rejection remain distinct. No verified production Focus/ML release established. |
| artifacts/a0-*, a2-*, coverage/recovery/browser evidence | KEEP_REFERENCE; some files also KEEP_ACTIVE | Incident/acceptance/lineage and recovery history. Current guard paths consult selected retained records. No bulk campaign retirement inferred from timestamps. |

**Recorded accuracy limits:** Phase 5 README reports corrected known Himadri regression 8/8 but no unseen improvement (1/29 fresh holdout references), 0/20 public-release certification in that comparison, and blocked production/backtesting/ML admission. These are retained experimental findings, not independently recomputed scores. The financial-only prototype likewise is not an approved selection score. Preserve original evaluation generations, including failed/negative results and reviewer corrections.

## 6. Duplicate and redundant-file analysis

### 6.1 Independent GitHub source candidate: full payload comparison

**VERIFIED live result:** 744 entries total / 7,641,010 B: **725 payload files + 19 nested Git metadata files**. Payload comparison against same root-relative canonical paths: **696 byte-identical, 23 divergent, six candidate-only**. No content-name heuristic was used. SHA256 and exact byte comparisons were computed for these small source files. Same-path mode differences: **0**. Candidate independent HEAD is `272a1da16ab56afee737e3e83efb3cce32b0f8f1`, with clean nested Git status at inspection.

The North Star's compact-snapshot figures (693 identical / 10 differing / four candidate-only out of 707) do not describe the current live tree. The earlier audit pass found 697 identical / 22 divergent; by the final inventory, `ops/activation/test_phase4_routine_schedule.py` had also diverged. Final hashes below reflect the report-generation observation, not a frozen worktree. Revalidate before acting. Its 14 named review items are all accounted for below. No differing version is promoted or overwritten. Runtime/CI source searches across canonical `scrapper`, `ops`, `scripts`, `.github`, experiments and root configuration found no consumer that imports or mounts the candidate directory; historical publication/readiness references must be retained/routed when archiving. Absence of a static caller does not prove absence of a private operator workflow, so external preservation and owner approval remain mandatory.

Every divergent payload file, relative to candidate root:

| Relative path | Candidate / canonical bytes | Candidate SHA256 | Canonical SHA256 | Assessment |
| --- | --- | --- | --- | --- |
| `.gitignore` | 419 / 1,404 | 33c9bcd03bb834ed9bc87f77bd43e00b5853c80a9306008269a84fa1522f5784 | b2b36f0a3abe50eb96b4a7455bb25ff08168543084262a157c51eb5d8241169d | Publication exclusion policy differs from operational checkout; do not replace root rules. |
| `README.md` | 2,083 / 5,897 | 737b3e2095d55e3092985dd2b9b833bfcc7bdbe39dec095337bae09bb164183a | 8fb3f7c015640b12c33733f96059f7fcea61b847e08d750cdaddf365f96e4a7d | Candidate describes blocked source publication and subset regressions; main describes host operations. |
| `docs/cleanup/baselines/C22.3/migration-manifest.json` | 40,549 / 40,471 | 5669a32d34972ff924718ef2e46de865e8116cdfda27d73e9b63900072950a18 | b1d48a7919cc2983aa1f289cdb6e99a86eef1ac062f232756e1c500fdb79f521 | Candidate adds plain SQL source_file routing for historical 065; main resolves retained A2 tar member. |
| `ops/activation/automatic_acquisition.py` | 19,205 / 21,474 | d671c816826f45d41b3ce5d07980240339c233f8be2e24019a1bec6b7ade687d | a2dff3f5fc762167cd5319142368ab5c2816250052840036626eab626dc25fc7 | Main adds durable atomic receipts, claim-loss deferral, typed document failures and elapsed timing. |
| `ops/activation/financial_priority.py` | 9,287 / 10,431 | bffa1b7bf12ff42ad369476deb6e4e6e8ace1f71c932b1bb18c923aec1952bc5 | de584dbde5c264dd09da995067c7aa158594523ec01e1ffa5a533135f87bc446 | Main changes financial queue alias/priority handling; operational behavior difference, not cosmetic. |
| `ops/activation/phase4_documents.py` | 10,428 / 10,564 | d4fc573de7ecca50d8f3d58bd09f48e88caaf575090c9355fcf6edb84d78b93d | 62de0ba60d8915f9c6f14ea32af2e71bcd40ba8dcd3c2a974b3015be30ceda30 | Main adds explicit My Passport mount-presence refusal. |
| `ops/activation/phase4_routine_runtime.py` | 6,724 / 8,240 | 098d7f75af52dcc8cdc6f574e34bf8803a8df266ed413afac932a37fd3897755 | 002577f55334843671d7b67d9b79c79e9541dd5ccccaa8c28d8b564f8f6c4b82 | Main expands SQL write fencing, timing, optional preservation fingerprinting and typed failure entrypoint. |
| `ops/activation/phase4_schedule.py` | 10,428 / 13,527 | ea1109e2b1d1099d7f49890ee60fb85e3b0df915f61d8faa454cbbcb47d0ae3e | cfd64d5921cef23bde50cb28f0898a539e705ac8c7d40822fe060c8856c733ad | Main adds explicit finite/continuous authorization handling, daily stage reservation and environment deferral; current JSON remains finite and expired. |
| `ops/activation/step3_financial_runtime.py` | 19,026 / 19,487 | faad5d79d0735b67e516d4e3261e9fb5c113ce99f564b909e70e5fd1c07cf414 | 92ec1a3294a6c62514fa732b2db816940ac3514527c1ed561ebfab667e72ad2b | Main adds entrypoint classification: lost claims defer with 75; other exceptions stop. |
| `ops/activation/test_automatic_acquisition.py` | 4,591 / 6,020 | 2dad28ddd535d36dc2cfb83ab5f363a520b0055844a040b99c45d98971c54d5d | befe3d76a5ecd0056d09d308b024c505438bb81ca4fc47070cde85a7eebcd70d | Main has added claim-loss and non-claim-failure regression cases. |
| `ops/activation/test_phase4_routine_schedule.py` | 1,957 / 1,985 | 850f06aef76f866f2677a5c5aeda99f80e61ff179166f2ce26edfc1eee59d7f0 | 3fb9a82bbf1ee291a6dad69bd6eff017c121b77f586aebb1d4e394265a745f84 | Main explicitly pins authorization_mode=finite in the routine schedule test fixture; this additional divergence appeared during concurrent source work. Preserve both versions. |
| `ops/host/acquisition_lifecycle.py` | 21,837 / 22,262 | b9893b15bc1ea8ec098001a95444ecd5d95decd4ffda4b554c0c01ce400101f2 | 7751754273271e7b00e29151c3c2e003925aee61ee188b7e076b7507f56a73fd | Main fixes renderer mount location and reports finite document authorization expiry distinctly. |
| `ops/host/acquisition_queries.py` | 9,719 / 13,637 | 9658b1086949d73665ea9dff7b4f4eaec6f16e05170663c7caf8d215cdc8e14a | 21796cc3311b3967304b60c6fa5a5b9ecd9f6a2879a742f6fa1c5d6bc459ec21 | Main adds quarterly coverage, retry diagnostics and company completion catalog; fixes renderer lookup. |
| `ops/host/acquisition_terminal.py` | 30,333 / 46,948 | b28d1a519db760c95100f015a5fced5a75cbadbbea6aee9d6418e67e3cfbb479 | 98db4373b8b42566b5e32ad3b5a29ae08104768cf46604e3e33b86400b4b1d0f | Main adds compact terminal, coverage/blocker explanations, activity distinction and cached company suggestions. |
| `ops/host/run_acquisition_lifecycle.py` | 1,159 / 1,178 | 3c5e3b50a23223cec0ff5f3c6dfaa4d9e9a745ee471284c5bc9c96aad9eba2a2 | d0ca1720e3b0335507fb559b732738b51afd0b319cd2bb4bd69b306b19b9288c | Main routes coverage and bse to terminal entrypoint. |
| `ops/host/scraper_lifecycle.sh` | 1,448 / 1,461 | 48c6bd446c5cf8ed9ec6f5424d5e4f4ba39ce19a3cb76cd6d4534bc709db80c1 | 1f01aa553254a3176b1f67afb202f87015241dbe631a27d22819426e4e39967f | Main exposes coverage and bse dispatch cases. |
| `ops/host/test_acquisition_observations.py` | 11,465 / 15,134 | 9e2923a6e42c756e7079769ced84aa756a6334cac08417b38c9e5f2a8f2d30b2 | 0a4a3163afda85719136640650067404e1bf077250b089d348881cd04bac34f0 | Main adds coverage, retry-state and identifier-catalog regression cases. |
| `ops/host/test_acquisition_terminal.py` | 5,846 / 12,118 | 8d5e40acdfd757f1ad89f3ad4f8cca5dd5e8547b239ff6ce927e45bf865b1fe3 | 82c486c9df1653ff78832bde54c1854f02d10fdeeb0fa890c2dc81bdfe909efc | Main adds mount, small-terminal, activity and completion regressions. |
| `scrapper/research/general_ai_experiment/runner.py` | 34,873 / 34,944 | 8df8a3ec89443e3759050b50de614ebee1dc5176bfdc742e5149043e4db1b7c5 | 2d903f29d4293e371989b474860559e6b1acd8a8a044a2ca6bd65cfb752f0f14 | Main request helper optionally returns raw HTTP bytes (return_raw); candidate only decodes JSON. |
| `scrapper/sources/exchange/result_filings.py` | 14,725 / 14,739 | f1541b4bb1059c8f1fff57d39eaf01fd4cf44bbe19fc4344cd45f8df1869bc42 | fe6884d488788f5a74524d7e4349addb70659589ecd5232c2ef2df520893e23f | Main explicitly excludes SN/DN non-quarterly codes; candidate excludes a smaller code set. |
| `scrapper/workflows/documents/download.py` | 22,445 / 22,654 | 53eeb0c32d00af78ad59207cc8372fb2656d747446fc77e5af9bde562f913114 | 1b8e9308420cce925ed158a23b09ecea784a4db0cac040447b3a6c6f93742a1e | Main re-raises SQLite/storage failures alongside OwnershipLost rather than consuming publisher retry. |
| `scripts/audit/check_migration_manifest.py` | 8,606 / 8,442 | 5eeb00680f5f2975410c760c5ecf3f096c3747a544a6cb735ca2dd36846de57b | 75c9887ad8cc87f08d340e0d19cf5834a7de2e8e02218faf301d962db08e6630 | Candidate supports historical source_file SQL; main reads historical archive member. |
| `scripts/audit/test_migration_lineage.py` | 5,904 / 5,196 | 0e39ecf30c7f2414ee6280769645e9e255074ad0a2d975ed2c5c711031b0c6f7 | e2da72aa80d40c9657094222d66952b65e0aaca95cd028632b6e597f9b0989ec | Candidate adds two tests for plain-SQL historical identity and tamper rejection. |

Every candidate-only path:

| Relative path | Bytes | Candidate SHA256 | Assessment |
| --- | --- | --- | --- |
| `.github/workflows/source-regressions.yml` | 492 | 16520c67c1bc7827ff34eb63092cf09d6271f91eb17a373f7b06fb965ad97989 | Candidate CI invokes isolated subset regression runner; retain together. |
| `compose.source-example.yml` | 423 | 75c37ebc51b15a489d3e53386265a38a4b225e93cfd01d86db1c0770eda2d038 | Isolated source-publication Compose example; not production topology. |
| `config/local.env.example` | 352 | 37a9e0250b2ae0e0c5b5bc27fd3bc3daea57fe21d04783ff72cd6ff30d0dc352 | Example configuration; contents not printed; preserve with publication bundle. |
| `docs/PUBLICATION_BOUNDARIES.md` | 2,847 | d6fdf77bdfc3e8135dd9b97be251cedd06327543a00bd2a56683271529976359 | Unique publication restrictions and portability/reproducibility limitations. |
| `docs/migration-history/065_exchange_source_access.sql` | 2,605 | aff1227db2661437a5e70f8f66f3d2ca4c3783e66f700e3b050e0c5a901e0b9d | Byte-identical to canonical retained A2.tar.gz historical member; not an active migration to install. |
| `scripts/audit/run_source_regressions.py` | 3,924 | b5da675cfaf831c914e53b9d1e318501b6a6cb08f2de5fefcf18eac42b5a51b0 | Candidate-only network/SQLite-constrained subset runner; excluded fixtures mean it does not replace complete canonical tests. |

`docs/migration-history/065_exchange_source_access.sql` was also compared directly to `docs/cleanup/baselines/C03.1/A2.tar.gz` member `review/scrapper/db/migrations/065_exchange_source_access.sql`: **exact bytes match** (SHA256 `aff1227db2661437a5e70f8f66f3d2ca4c3783e66f700e3b050e0c5a901e0b9d`). The main manifest retains it as historical identity; the active canonical `065_operator_browser_rating_recovery.sql` is a separate migration. Preserve both identities; never renumber, merge or install the candidate historical SQL as cleanup.

The candidate's divergent migration checker, manifest and tests form a coherent export adaptation supporting a plain historical SQL file. The six unique publication/example/CI files belong with that export. Operational differences in the main tree include later error classification, source-policy, mount, coverage/terminal and storage-failure handling; cherry-picking candidate files over main would risk regressions. Complete archival is safer than deleting only the 696 equal files, which would destroy a self-contained historical publication snapshot.

### 6.2 Samples and ZIP exports

- The two live sample directories hold **28 PDF occurrences but 17 unique PDF hashes**. All 11 PDFs in `sample_files/` have exact copies in the membership-organized `sample_files-2/` tree: **8,732,029 B** duplicated PDF payload. Both directory layouts have active research consumers and distinct manifests/membership context. **KEEP_REFERENCE; no deduplication move or symlink substitution proposed.**
- `sample_files-2.zip`: 18,652,381 B compressed; 63 regular members / 20,391,359 uncompressed B. **26 payload members byte-match live same-path files**; root `.DS_Store` differs; **36 AppleDouble `__MACOSX` members are archive-only**. Its complete bytes are not redundant. Preserve original externally before removing root copy.
- `trading-ai-code-review-compact.zip`: 65,324,707 B compressed, 6,278 members / 286,439,551 uncompressed B. It is a selective historical review deliverable. This audit used live filesystem/code evidence; ZIP metadata was only used to classify the export. Do not rely on it as the live backup.

### 6.3 Large apparent repetition: not proven duplicates

- Four snapshot DBs total **58,408,796,160 B**: AR-01.3 pre-migration `raw/scraper.db` and `validation/scraper.db` are each 14,602,158,080 B; AR-01.3B `post-migration/raw/scraper.db` and AR-01.3C `attempt-02-post-canary/scraper.db` are each 14,602,240,000 B. No full hashes/content equivalence computed. Different stages/lineage and a hardcoded research consumer prohibit deduplication based on equal sizes.
- Four retained release OCI archives are about 693.94 MB each, with differing sizes and release identities. Do not call them duplicates or discard rollback packages without release-custody verification.
- `docs/cleanup/baselines/C20.3/harness.tar` is 1,038,407,680 B; `C28.1/preintegration.patch` is 921,903,296 B. Other per-chunk harnesses are large. The cumulative materializer and historical checks depend on original source/patch/archive contracts. No full cross-harness content equivalence was established.
- `artifacts/document-acquisition/final-ops/d03-progress/`: 19,790 entries / 11,279,972,071 B. A deterministic spread sample hashed 73 JSON files / 33,542,330 B (2 MiB/file, 32 MiB total budget); **zero exact duplicate groups in that sample**. This is not a full-corpus uniqueness certificate, and it provides no evidence for deletion. Repeated progress records can retain different decisions/state.
- Large Torch library files in separate Docling/MinerU environments have equal sizes. They were not hashed against each other; independently installed packages, interpreter linkage and environment recovery remain unverified. No hardlink or cross-environment deduplication is proposed.

## 7. Exact cleanup candidate manifest

**No row authorizes action now.** “Conditional archive” means operationally isolated enough to propose, but a verified retained copy, restore test, path routing and explicit approval must precede removing its active copy. `NEEDS_INVESTIGATION` rows are a hold list, not a deletion list. Sizes overlap for parent/child investigations and must not be added indiscriminately.

| Exact candidate path | Entries / size | Classification | Proposed action | Evidence | Known dependencies | Risk | Safe to act? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `github-source-candidate-20261023` | 744 / 7,641,010 B (7.29 MiB) | ARCHIVE_CANDIDATE | Copy complete directory including .git to external retention; verify; later remove active copy. | 725 payload files: 696 equal, 23 divergent, 6 candidate-only; independent clean Git HEAD; no runtime path consumer found. | Historical publication/readiness docs must resolve via retained archive; preserve all unique files and nested history. | LOW operational / MEDIUM evidence | Conditional archive only; NOT safe to remove until backup/restore verified. |
| `trading-ai-code-review-compact.zip` | 1 / 65,324,707 B (62.30 MiB) | ARCHIVE_CANDIDATE | Preserve original ZIP outside active workspace; remove local copy after verification. | Dated 6,278-member selective export; no executable source/config consumer found; not live-checkout backup. | North Star/share provenance refers to this exact snapshot; retain original bytes and path mapping. | LOW operational / MEDIUM evidence | Conditional archive; never assume snapshot fully reconstructs live tree. |
| `sample_files-2.zip` | 1 / 18,652,381 B (17.79 MiB) | ARCHIVE_CANDIDATE | Preserve original ZIP externally; later remove root copy. | 26 payload members match live paths exactly; Finder metadata differs and 36 AppleDouble members are archive-only. | Keep sample_files-2 directory/manifests; retain original ZIP metadata rather than call it wholly redundant. | LOW | Conditional archive; NOT classified safe for deletion. |
| `experiments/upstox_fundamentals_pilot` | 5 / 4,143 B (0.00 MiB) | ARCHIVE_CANDIDATE | Archive complete five-file blocked proposal; retain index/reference. | Only scope/blocker notes, ignore file and empty placeholders; V4 D3 explicitly retires provider pilot; no Python implementation or source caller found. | Historical decision/rights/access evidence; no current token-absence claim made. | LOW | Conditional archive after restore and documentation reference routing. |
| `.pytest_cache` | 5 / 120,498 B (0.11 MiB) | DELETE_CANDIDATE | Approve removal of this cache after no active pytest owner and retained copy/manifest. | Pytest CACHEDIR.TAG and metadata; root ignore rule; materializer excludes cache; no application/runtime consumer found. | Developer pytest state only; recreate by later explicitly isolated test invocation. | LOW | Technically disposable; approval and active-owner check still required. |
| `artifacts/phase5/document-parsing-v10/env-mineru` | 35,689 / 1,145,756,989 B (1.067 GiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | Installed research dependency environment, 35,689 entries; package installation and wheel pins not replay-tested. | V10 offline benchmark scripts; original outputs/source pages must stay. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `artifacts/phase5/document-parsing-v10/env-docling` | 30,659 / 1,133,019,282 B (1.055 GiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | Installed research dependency environment, 30,659 entries; not a proved regenerable cache. | V10 Docling comparison scripts and model/runtime versions. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `artifacts/phase5/document-parsing-v10/mineru-home` | 43 / 858,210,166 B (818.45 MiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | 858 MB model/assets directory; no bounded complete origin/revision/hash recovery proof. | MinerU benchmark runtime and reproducibility. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `artifacts/phase5/document-parsing-v10/docling-models` | 66 / 701,229,877 B (668.74 MiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | 701 MB local model/assets; equality to another directory not verified. | Docling comparison model versions and network-independent replay. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `artifacts/phase5/general-ai-experiment/.venv` | 14,336 / 797,491,457 B (760.55 MiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | 797 MB isolated MLX/research runtime; no environment reconstruction performed. | General-AI local benchmark; paid/failed responses remain original evidence. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `artifacts/phase5/general-ai-experiment/checkpoint` | 24 / 3,061,133,768 B (2.851 GiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | 3.061 GB checkpoint bundle; local.py verifies reviewed identity/hash and uses directory. | prepare_local.py/local.py, local-checkpoint-review and failed/timeout benchmark evidence. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `artifacts/phase5/generalization/v3/teleocr-runtime/target` | 2,987 / 1,355,364,062 B (1.262 GiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | 1.355 GB Rust build tree; likely rebuilding opportunity but release binaries and exact build recovery not checked. | run_teleocr_local.py/probe_teleocr_runtime.py and isolated runtime scripts; vendor/code changes may be unique. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `artifacts/icra-download-test/.venv` | 1,244 / 152,596,185 B (145.53 MiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | 152.6 MB old browser environment; no complete recreation/dependency closure established. | Retained recovery scripts; not the configured installed dispatcher interpreter. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `scrapper/.venv` | 4,951 / 127,235,142 B (121.34 MiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | 127.2 MB ignored local environment; docs invoke this for research/tests. | Research commands and possibly manual operator workflows; installed dispatcher uses separate uv environment. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `docs/cleanup/baselines` | 1,045 / 9,503,086,562 B (8.850 GiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | 9.503 GB retained evidence; 68 .tar files total 8.279 GB and 87 patches total 926 MB; not proven duplicates. | CI/check_migration_manifest.py defaults to C22.3; historical 065 uses C03.1/A2.tar.gz; materializer and reports require harnesses. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `artifacts/document-acquisition/final-ops/d03-progress` | 19,790 / 11,279,972,071 B (10.505 GiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | 19,790 files / 11.280 GB cumulative progress; bounded hashes of 73 files (33,542,330 B) found no exact duplicate group. | Historical extraction decisions, attempted work and progress evidence; not proof of reproducibility or dispensability. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `artifacts/acquisition-readiness/AR-01.3` | 4 / 29,898,252,300 B (27.845 GiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | Two 14.602 GB pre-migration DBs plus release material; equal DB sizes are not equality proof. | Migration/recovery baseline, database/queue lineage, backup/rehearsal records. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `artifacts/acquisition-readiness/AR-01.3B` | 1 / 14,602,240,000 B (13.599 GiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | One 14.602 GB cold post-migration DB. | experiments/finance_only_v1/pilot.py hardcodes this snapshot and verifies expected hash. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |
| `artifacts/acquisition-readiness/AR-01.3C` | 1 / 14,602,240,000 B (13.599 GiB) | NEEDS_INVESTIGATION | Retain in place; investigate future off-workspace retention/rebuild separately. | One 14.602 GB post-canary DB. | Post-canary/lineage evidence; no independent restore/hash comparison run. | HIGH for evidence/DB; MEDIUM for isolated environments | NO. Not an authorized archive/deletion set. |

### Small cache-only deletion proposals

1. `.pytest_cache/`: 120,498 B (0.11 MiB), five entries. Reproducible pytest metadata; no application data. No tests were run to regenerate it.
2. Source-backed bytecode in the exact cache directories below: **931 files / 15,425,877 B**. Companion `.py` exists for each included file and package/build configuration excludes bytecode; Python can regenerate it. Confirm no active Python/pytest operation relies on directory writability and back up/manifest selected files first. Only `.pyc` with the checked source companion is proposed; do not remove a whole mixed directory or environment.
3. Finder metadata listed below: **57 exact files / 454,884 B**. Standard macOS directory-view metadata; no canonical application/config consumer found in bounded search. These are optional low-risk removals with owner approval; preserving their archive copies avoids loss of operator view metadata. Do not expand to arbitrary hidden files.

**Orphan bytecode is NOT in the deletion set**: `scrapper/screener/__pycache__/urls.cpython-314.pyc`, `scrapper/__pycache__/cli.cpython-313.pyc`, `scrapper/__pycache__/cli.cpython-314.pyc`, `scrapper/documents/__pycache__/recovery.cpython-314.pyc`, `scrapper/documents/__pycache__/recovery.cpython-313.pyc`, `ops/host/__pycache__/control_center_db.cpython-314.pyc`. No same-name `.py` companion exists; these may retain former code bytes. Classification **NEEDS_INVESTIGATION**, preserve until original source/history or archived reconstruction is proved. This is why a blanket `__pycache__` removal is not recommended.

| Exact cache directory | Entries / bytes | Exclusions and action boundary |
| --- | --- | --- |
| `experiments/finance_only_v1/__pycache__/` | 3 / 52,455 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `experiments/finance_only_v1/tests/__pycache__/` | 1 / 24,839 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `experiments/unified_financial_pilot/__pycache__/` | 14 / 248,493 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `ops/activation/__pycache__/` | 63 / 952,717 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `ops/docker/canary/__pycache__/` | 2 / 28,783 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `ops/host/__pycache__/` | 2 / 15,215 | Delete only source-backed .pyc after approval; orphan exceptions below remain. |
| `scrapper/__pycache__/` | 10 / 232,255 | Delete only source-backed .pyc after approval; orphan exceptions below remain. |
| `scrapper/cli/__pycache__/` | 2 / 21,010 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/cli_groups/__pycache__/` | 16 / 211,247 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/commands/__pycache__/` | 15 / 174,436 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/core/__pycache__/` | 19 / 102,545 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/db/__pycache__/` | 56 / 852,450 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/db/documents/__pycache__/` | 19 / 180,383 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/db/documents/recovery/__pycache__/` | 12 / 179,704 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/db/exchange/__pycache__/` | 12 / 155,491 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/db/screener/__pycache__/` | 7 / 80,849 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/documents/__pycache__/` | 50 / 558,705 | Delete only source-backed .pyc after approval; orphan exceptions below remain. |
| `scrapper/documents/discovery/__pycache__/` | 4 / 42,785 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/documents/download/__pycache__/` | 18 / 129,052 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/documents/readiness/__pycache__/` | 6 / 13,034 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/documents/recovery/__pycache__/` | 4 / 33,990 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/domain/__pycache__/` | 4 / 13,327 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/exports/__pycache__/` | 35 / 427,387 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/governance/__pycache__/` | 4 / 62,759 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/operations/coordinator/__pycache__/` | 3 / 40,497 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/operations/workers/__pycache__/` | 13 / 131,385 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/pipeline/__pycache__/` | 63 / 868,564 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/research/__pycache__/` | 5 / 34,369 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/research/document_intelligence/__pycache__/` | 46 / 990,525 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/research/features/__pycache__/` | 2 / 31,665 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/research/general_ai_experiment/__pycache__/` | 16 / 263,431 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/screener/__pycache__/` | 2 / 875 | Delete only source-backed .pyc after approval; orphan exceptions below remain. |
| `scrapper/sources/__pycache__/` | 2 / 428 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/sources/amc_workbook/__pycache__/` | 14 / 245,103 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/sources/documents/__pycache__/` | 16 / 145,358 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/sources/exchange/__pycache__/` | 20 / 238,756 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/sources/mfdata/__pycache__/` | 6 / 26,172 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/sources/screener/__pycache__/` | 34 / 249,127 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/sources/trendlyne/__pycache__/` | 8 / 77,838 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/tests/__pycache__/` | 257 / 6,620,453 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/tests/fixtures/documents/__pycache__/` | 1 / 16,604 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/validation/__pycache__/` | 8 / 168,171 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/view/__pycache__/` | 15 / 363,325 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/workflows/__pycache__/` | 2 / 500 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/workflows/documents/__pycache__/` | 12 / 102,823 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/workflows/exchange/__pycache__/` | 6 / 118,319 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scrapper/workflows/screener/__pycache__/` | 6 / 83,166 | Source companions exist; cache-only candidate, pending no-owner/restore check. |
| `scripts/audit/__pycache__/` | 2 / 22,631 | Source companions exist; cache-only candidate, pending no-owner/restore check. |

| Exact Finder metadata path | Bytes |
| --- | --- |
| `.DS_Store` | 10,244 |
| `scrapper/.DS_Store` | 10,244 |
| `artifacts/.DS_Store` | 10,244 |
| `artifacts/three-document-reality-check-v0.4/.DS_Store` | 10,244 |
| `artifacts/b2-2026-10-01/.DS_Store` | 8,196 |
| `artifacts/document-acquisition/.DS_Store` | 6,148 |
| `artifacts/credit-rating-intelligence-v1/.DS_Store` | 8,196 |
| `sample_files-2/.DS_Store` | 8,196 |
| `sample_files-2/in_q1_focus_list/.DS_Store` | 10,244 |
| `sample_files-2/not_in_q1_focus_list/.DS_Store` | 8,196 |
| `OLD/.DS_Store` | 8,196 |
| `OLD/ML_PRO/.DS_Store` | 8,196 |
| `OLD/ML_PRO/DATA/.DS_Store` | 8,196 |
| `OLD/ML_PRO/DATA/RESULTS/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/.DS_Store` | 10,244 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/.DS_Store` | 26,628 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/OIL-EXPLORATION/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/POWER/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/PACKAGING/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/EMS/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/METALS/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/WIRES/.DS_Store` | 10,244 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/WIRES/STLTECH/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/FINANCIALS/.DS_Store` | 10,244 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/FMCG/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/ASSET-MTG/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/REALTY/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/AUTO/.DS_Store` | 8,196 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/HEALTHCARE/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/IT/.DS_Store` | 8,196 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/IT/FRACTAL/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/COMMODITY/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/OTHERS/.DS_Store` | 16,388 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/OTHERS/WEWORK/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/OTHERS/WENDT/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/OTHERS/VTL/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/OTHERS/XPROINDIA/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/OTHERS/VGUARD/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/OTHERS/WPIL/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/OTHERS/CRISIL/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/CHEMICALS/.DS_Store` | 10,244 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/GOLD/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/DATA-CENTER/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/TEXTILES/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/ALCOHOL/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/PLYWOOD/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/BANKS/.DS_Store` | 10,244 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/BANKS/SOUTHBANK/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/PHARMA/.DS_Store` | 10,244 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/AUTO-ANCILLIARY/.DS_Store` | 12,292 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/AUTO-ANCILLIARY/SCHAEFFLER/.DS_Store` | 6,148 |
| `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/INDUSTRIAL-PRODUCTS/.DS_Store` | 6,148 |
| `OLD/RESULTS/.DS_Store` | 8,196 |
| `OLD/RESULTS/2026/.DS_Store` | 6,148 |
| `OLD/RESULTS/2026/Q4/.DS_Store` | 8,196 |
| `OLD/RESULTS/2027/.DS_Store` | 6,148 |

## 8. Proposed archive destinations and custody

Proposed, **not created**: sibling `../trading-ai-retained-cleanup-archive-2026-10-26/`, with separate preserved roots for `github-source-candidate-20261023/` (including its `.git`), `root-exports/` (original ZIPs), `experiments/upstox_fundamentals_pilot/` and `optional-cache-retention/`. An independent backup medium/location is needed before claiming protection from disk failure; a sibling directory on the same volume provides workspace separation only. Owner chooses storage, access controls and retention before execution.

Retain original paths/modes/mtime/symlinks and cryptographic file/archive manifests. Verify fresh restore into a different review directory; never extract over canonical source, active mounts or an existing database. A later approved documentation change can add archive/member routing while keeping historical records immutable. Do not publish archives or copy credentials into new bundles. Do not remove the original until both archive and restore verification pass.

Larger historical baselines, document-acquisition records and experiment bundles may eventually use external evidence storage with a small in-repository catalog. They currently contain actual path consumers. Any such proposal needs its own exact member manifest, dependency adaptation and isolated verification; it is outside the first cleanup batch and not approved by this report.

## 9. Expected disk savings and practical limits

| Potential batch | Logical bytes removed from active workspace | Qualification |
| --- | --- | --- |
| First archive-only batch: candidate + two root ZIPs + Upstox proposal | 91,622,241 | About 91.6 MB; requires verified external retention. Same-volume archival alone does not free machine disk. |
| Optional pytest + source-backed bytecode + exact Finder metadata | 16,001,259 | About 16 MB, low-risk conditional candidates; exclude six orphan bytecodes and every virtual environment. |
| Four historical DB snapshots | 58,408,796,160 | Protected, not approved savings; no full equality, consumer adaptation or restore proof. |
| V10 two environments + model asset roots | 3,838,216,314 | Investigation ceiling only; no confirmed rebuild/rehydration; originals/results excluded. |
| General-AI environment + checkpoint | 3,858,625,225 | Investigation ceiling only; checkpoint has known benchmark consumer and reviewed hash. |
| TeleOCR target build tree | 1,355,364,062 | Potential rebuild cache; exact executable/source/toolchain dependency proof still required. |
| Cleanup .tar harness payloads | 8,278,902,831 | Evidence relocation potential only; not proven redundant. Git history means working-copy removal will not eliminate existing object storage. |
| D03 progress snapshots | 11,279,972,071 | Protected historical decisions/progress; no safe disposable subset established. |

No savings claimed for `.git` (about 4.25 GB logical), databases, originals, working stores, queues, archived decisions or active runtime dependencies. Git history rewrite, GC/prune and Docker/image/volume pruning are not part of this plan. Total machine disk savings cannot be calculated without archive placement, APFS sharing/compression and external storage inventory.

## 10. Proposed execution order and rollback strategy — future authorization only

1. **Freeze the approved manifest, not live collection.** Record fresh branch/HEAD, index/staged bytes, unstaged/untracked/ignored inventory and baseline metadata immediately before any approved action. Reconcile concurrent recovery work first. Do not stash/reset/check out or pause jobs just for cleanup.
2. **Establish custody.** Verify durable backup of uncommitted work, independent candidate history and selected export/proposal payloads. Record original SHA256/mode/mtime, archive hash and exact restoration mapping. Require human approval of this concrete manifest/destination.
3. **First isolated archive batch.** Copy the candidate, root ZIPs and retired Upstox proposal to approved external retention. Verify every member, including all 23 divergent and six unique candidate files, and nested Git refs/history. Restore to a separate directory and compare; only then remove exact approved workspace paths. Do not change canonical Python, config, source manifests, schedules or databases.
4. **Optional cache batch under separate explicit approval.** Recheck exact cache/Finder paths against current filesystem and active owners. Remove only source-backed bytecode and selected disposable metadata; retain the six orphan bytecodes. No broad glob over artifacts, hidden files, environments or stores.
5. **Navigation repair separately.** Propose a small doc change linking North Star, correcting the missing plan link and marking expired/dated operational claims. Preserve historical outcomes as historical. Do not change command behavior, schedule limits or authorization while fixing text.
6. **Investigate high-volume roots one at a time.** Prove consistent DB backups/consumers first; distinguish release rollback bundles from cache, rebuildable environments from unique model/source changes and historical results. An isolated replay on restored data can be proposed later. Do not run it against production as part of cleanup.
7. **Post-change acceptance for a later authorized cleanup.** Compare pre/post selected source/config/data hashes, current Git diff and declared path references. Run only appropriate isolated tests with disposable paths, network disabled and no production mounts; tests are not required or run for this read-only audit. Verify unchanged service/plist/DB/document/queue ownership using harmless observation. Any unexplained difference blocks progression.

**Rollback:** for each approved archive/removal, verify retained manifest then restore exactly that path with its original bytes/modes into an absent destination. Never overwrite new concurrent work; compare and resolve manually. Do not restore old DBs over accepted new writes, roll back migrations, replace the source tree with the publication candidate, reset the Git index or restart legacy workers. If a retained path is needed unexpectedly, restore its exact member first; stop further cleanup until dependency evidence is corrected. Keep archives until the owner explicitly accepts a verified retention/expiry decision.

## 11. Documentation contradictions and unknowns

**VERIFIED contradictions / drift:**

- North Star's 707-file candidate comparison is a dated compact-snapshot observation; live candidate is 725 payload files plus nested history, with 23 differing and six unique files. This audit supersedes those counts, not North Star's product direction.
- ROOT-HYGIENE's “one scrapper tree” statement described 7 October. The new publication candidate now contains another `scrapper/` plus its own Git repository. It is a comparison/archive candidate, not a replacement operational app.
- README opens with 22 October operation/window ending 23 October; activation README references 24 October repair/window ending 25 October. Current `phase4_schedule.json` is enabled but finite and expired at 25 October 18:43:41 IST. Source's added continuous-mode support is not current authorization. No window was extended here.
- README links `docs/current/TRADING_AI_MASTER_RESEARCH_AND_DOCUMENT_INTELLIGENCE_PLAN.md`, which is **missing in the live checkout**. Present file is `..._PLAN_AUDIT_UPDATED.md`; Git records an existing rename/edits. Do not recreate the missing path or silently rewrite historical plans. All four specifically requested root files are present.
- Root README accurately routes current lifecycle to bounded host dispatch; older `scrapper/README.md`, Compose roles and configuration contain continuous-worker/legacy scheduler behavior. Their existence does not mean they run. Observed Trading AI containers were exited; LaunchAgent remained loaded.
- `.gitignore` calls OLD archived/superseded, but the active governance builder reads original data there. Ignored does not mean unused or backed up. `.gitignore` also cannot remove already tracked historical artifacts: 23,440 artifact files were tracked at initial inventory.
- Roadmap V4's “first deterministic Focus product” and future-domain outlines must not be interpreted as completed products. B2 supersession, four-company pilot acceptance limits and public-timestamp/label gaps prevent that claim. Phase 5 and later credit/model research now exist despite older blanket “ML/document AI deferred” wording; they remain research, not certified production features.
- Prior retention README descriptions of dependency manifests/migration counts are historical: actual root Dockerfile installs `requirements.lock`, and canonical `db/migrations/` currently has **75 SQL files** plus a separately preserved historical identity. Do not replay or renumber historical migrations based on old counts.
- A retained recovery receipt reports a newer DB backup; it is not independent proof of durable, complete current DB/document/queue recovery. Some older retention records depended on `/private/tmp`; temporary evidence is not a new backup guarantee.

**UNKNOWN / NEEDS_INVESTIGATION (no assumptions promoted to fact):**

- Fresh DB schema/row totals, exact current queue state/leases, primary financial/document completeness, external original-byte recoverability, and current successful collection health. No database or production application command was run.
- Why last inspected acquisition heartbeat predates audit time despite loaded LaunchAgent and successful last exit; job idle state is expected between ticks but stale receipt freshness needs its own non-mutating operational check. No repair attempted.
- Mapping and durability of `/recovery-backups/...db`, full backup hashes, independent restore, and how much current accepted state each retained DB snapshot covers.
- Full equivalence of giant DBs, OCI packages, per-chunk harnesses or cumulative JSON generations. No duplication inferred from names, equal sizes, dates or common schema.
- Complete reproducibility/offline retrieval of Python/Rust environments and model bundles; any local modifications, executable references, model redistribution/licensing constraints and operator use must be checked before deletion.
- Any private operator-only use of the publication candidate or exports; static no-caller evidence supports proposed archival, not irreversible disposal.
- Full secret exposure across renamed files, logs/embedded strings/all Git objects/archives; only named-path checks were performed. Publication remains uncleared.
- A current remote/offsite backup of this staged/untracked checkout. Historical records say GitHub backup was blocked; this audit neither pushes nor certifies present remote backup.

## 12. Approval queue and stop condition

Owner authorization is needed for the **exact first archive manifest and durable destination**, optional cache removal with explicit orphan exclusions, any documentation repair, and every future data/evidence/environment relocation. Any runtime, credential/history, DB/queue, deployment or backup operation requires its own defined scope. No ordinary application source refactor is proposed.

**Audit result: no cleanup executed.** This report is the sole created file. No existing file was intentionally deleted, moved, renamed or rewritten; no code, ignore policy, schedules, service configuration, databases or collected records were changed by the audit. No tests, paid inference, experiments, commits or publication were initiated. Stop here and wait for explicit cleanup authorization.
