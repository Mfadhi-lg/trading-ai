# Project report index

All project Markdown documents found in the current roadmap, canonical package documentation/research, artifacts and experiments. Vendored dependency documentation and duplicated review copies are excluded.

Status quotations below are dated evidence, not automatic deployment certification. See REPOSITORY_REVIEW.md for reconciled status and supersession.

## TRADING_AI_MASTER_ROADMAP_V4_2026-09-29.md

Source: [TRADING_AI_MASTER_ROADMAP_V4_2026-09-29.md](/Users/mohamedfadhi/Fadhi/trading-ai/TRADING_AI_MASTER_ROADMAP_V4_2026-09-29.md)

Lines: 348. SHA256: `2c3470400d56ff960e37020fe8e75828cce0911a0436717e8be31d4f7377423e`.

- Line 5: **Status:** Single authoritative coding-agent roadmap; **execution must be gated**, not launched as one giant assignment.  
- Line 6: **Supersedes:** `TRADING_AI_SINGLE_MASTER_ROADMAP_2026-09-29(1).md` (v3), the earlier master handoff, and the standalone Upstox feasibility pilot. Archive earlier plans to avoid conflicting instructions. Preserve existing code, databases, source records, review patches and test evidence.  
- Line 10: > **Owner-confirmed premise:** Financial data for the intended company universe is already scraped. The unresolved coverage problem is supporting **investor presentations (PPT), concall recordings/transcripts, credit-rating reports and related research documents**. Do **not** initiate another all-company financial-data acquisition project, a replacement financial database, a broad official XBRL migration or a full Upstox pilot. Small checks that financial records are usable, correctly scoped and time-aligned are still essential for trustworthy ML; **do not recast those checks as a new financial collection campaign**.
- Line 12: > **Current release status:** A0 was reported completed before the latest incident. A2 has a tested **review patch only**, not an approved or operational deployment. An unapproved bind-mounted code change triggered **migration 065** on the live local database; the agent reported **two backfilled BSE BLOCKED events**. The old application source was restored and no service was restarted; therefore A2 retry suppression **is not live**. A2's reported final offline verification passed **152 tests and 20 subtests**, plus `git apply --check`, but used host Python 3.14.5 rather than live Python 3.12.14 and did not exercise the complete pinned dependency environment. Treat all this as **agent-reporte
- Line 31: - **A1 + essential A3:** Maintain existing permitted Q2 collection and verify high-impact integrity failures (wrong quarter, partial refresh overwriting old data, same-quarter corrections). Keep the live worker running under the current approved code; changes require separate safety approval.

## artifacts/a0-2026-09-29/DEPLOYMENT_TRUTH.md

Source: [artifacts/a0-2026-09-29/DEPLOYMENT_TRUTH.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/a0-2026-09-29/DEPLOYMENT_TRUTH.md)

Lines: 35. SHA256: `cc29e049bb86efb7413c94aae76811c0066ab8eb95cb565215f4d8935109c2ac`.

- Line 3: **Status: IMPLEMENTED for the A0 read-only audit.** Evidence is from the running local Docker stack and a consistent SQLite backup taken on 29 September 2026. It is not a claim about any remote deployment. No live application row, schema, queue, or source configuration was changed.
- Line 9: - Twelve Compose containers were running and healthy at inspection: scheduler, discovery, daemon, artifact reconciler, market-cap monitor, document download, June extraction, rating recovery discovery/download, and three credit-rating workers. One-off manual Compose profiles remain configured but were not invoked.
- Line 18: - The latest BSE financial-results and security-master schedule runs both failed with **HTTP 403** at 2026-09-29 05:54 UTC. The latest passed BSE results snapshot dates to 16 September and the latest passed BSE master snapshot to 23 September. NSE result and master snapshots passed on 29 September around 05:54 UTC. Screener's last stored HTTP 200 was 08:35 UTC. See [source_runtime_status.csv](source_runtime_status.csv) for dated status counts. The BSE route is access blocked; a missing row cannot be interpreted as no filing.
- Line 33: The current research eligibility is a **dated** Screener collection-scope observation with a configured ₹580 crore floor; observations span 6 August to 29 September 2026. It is not an owner-approved frozen Focus universe. The 100-company file is a targeted database audit sample. Independent primary-source value reconciliation, historical publication-time proof, document-byte verification, and label inventory remain B0 work. Source licensing/entitlement was not established by this audit. No network access workaround was attempted.

## artifacts/a0-2026-09-29/backup_restore_evidence.md

Source: [artifacts/a0-2026-09-29/backup_restore_evidence.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/a0-2026-09-29/backup_restore_evidence.md)

Lines: 15. SHA256: `e487440722443206c2f34ce3d4024e5058629c45f24c6fe550f789209cbf53f3`.

- Design, reference, or supporting evidence; no matching status assertion.

## artifacts/a0-2026-09-29/coverage_baseline.md

Source: [artifacts/a0-2026-09-29/coverage_baseline.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/a0-2026-09-29/coverage_baseline.md)

Lines: 34. SHA256: `dc037410ac0938d10f0cf2565c321a537a1a69105d90e73941199a1d1d32763a`.

- Line 3: The database snapshot was created around **14:07 IST**. The aggregate collector ran later against that fixed copy. “Accepted” below means a stored standalone `accepted_page_versions` row for the exact June 2026 source period; it does **not** mean every mandatory financial field is validated. June 2026 is Q1 FY27. September 2026 Q2 FY27 had not ended on the snapshot date.
- Line 13: Exchange-universe coverage separately reports **5,919 exchange registry companies** and 8,396 current security links. These are different populations from the 4,423 `companies` table records and the 1,883 research-eligible scope records. Linked cohorts do not exhaust all company records. The comparison count proves only that at least one prior-year quarter observation exists in the accepted Q1 parse; it does not certify a full required-feature set or true historical availability.
- Line 15: The existing `quarter_documents` report covered the same **1,883** eligible companies for period end **2026-06-30**: 1,790 accepted standalone, 13 with a mapped exchange route requiring result-feed refresh, and 80 requiring exchange-identity resolution under that command's current rules. Those 93 are **not** evidence that results were unpublished. The raw command output is in `q1_document_inventory/company-quarter-completeness.csv` and `financial-gap-ledger.csv`.
- Line 17: The existing exchange coverage command returned `partial`: 25,704 stored filings, including 14,532 standalone and 11,172 consolidated; 519 manual-review enqueue decisions and 349 filing-identity collisions. The existing Screener discovery command returned `partial` for all 4 stored screen runs. The artifact coverage command returned `complete` for its own report contract but still counted more than 3,400 discovery failures and 9,332 pending retry events. None of these statuses should be read as complete financial or research readiness.
- Line 21: [financial_sample_100.csv](financial_sample_100.csv) uses deterministic ID hashing and explicit cohort quotas. It contains 28 BSE-only, 56 dual-listed, 10 NSE-only and 6 other/unmapped companies; 53 are eligible, 45 below the current floor and 2 have unknown eligibility. There are 12 below ₹580 crore, 10 from ₹580 to below ₹700 crore, 43 at or above ₹700 crore, and 35 with no usable cap value. Twenty have a Q1 document **candidate**; candidate presence does not prove a correct or readable document.

## artifacts/a0-2026-09-29/source_error_summary.md

Source: [artifacts/a0-2026-09-29/source_error_summary.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/a0-2026-09-29/source_error_summary.md)

Lines: 10. SHA256: `03e2b102dd8ed190491c552d5774d5160527c00aa45ee69a0c0ce93afaab1dc0`.

- Line 3: Source state is from the consistent SQLite backup created 29 September 2026 around 14:07 IST. The full sanitized grouped history, including last success/error times and counts in the preceding 72 hours, is [source_runtime_status.csv](source_runtime_status.csv).
- Line 5: - **BSE financial results:** seven stored `fetch_failed` schedule runs contain HTTP 403, five in the preceding 72 hours. Latest failure: **2026-09-29 05:54:07 UTC**. Latest passed stored source snapshot: **2026-09-16 03:55:52 UTC**. The active route is blocked; its absence of newer rows cannot establish no filing.
- Line 6: - **BSE security master:** seven stored `fetch_failed` schedule runs contain HTTP 403, five in the preceding 72 hours. Latest failure: **2026-09-29 05:54:05 UTC**. Latest passed stored source snapshot: **2026-09-23 03:41:32 UTC**.
- Line 8: - **Screener:** latest stored HTTP 200 fetch **2026-09-29 08:35:49 UTC**. In the preceding 72 hours the stored fetch table contains 9,002 HTTP 200 and 92 HTTP 404 observations. The 404 count is not a company-publication status.
- Line 10: The source-status audit did not make a fresh network request, verify entitlement, or change retry behavior. It classifies the recorded BSE response as an access failure, not a parser failure or an unpublished result.

## artifacts/a2-2026-09-29/A2_DEPLOYMENT_RUNBOOK.md

Source: [artifacts/a2-2026-09-29/A2_DEPLOYMENT_RUNBOOK.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/a2-2026-09-29/A2_DEPLOYMENT_RUNBOOK.md)

Lines: 75. SHA256: `1418caa0eb589a546fce8ac2a431bda7c3551f4ba8f327ffa1b7cbe64998acc5`.

- Line 1: # A2 deployment runbook — prepared, not authorized
- Line 6: review. Use this runbook only after explicit deployment approval.
- Line 10: 1. Review [MIGRATION_065_INCIDENT_REVIEW.md](MIGRATION_065_INCIDENT_REVIEW.md)
- Line 16: 2. Review [A2_REVIEW.patch](A2_REVIEW.patch) in an isolated checkout. Its
- Line 18:    profiles. `init_db()` must refuse newly visible migrations on the configured

## artifacts/a2-2026-09-29/A2_REVIEW_HANDOFF.md

Source: [artifacts/a2-2026-09-29/A2_REVIEW_HANDOFF.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/a2-2026-09-29/A2_REVIEW_HANDOFF.md)

Lines: 76. SHA256: `4ddd1e8bee7c86c2dd5e3cfde656e0a4c09a3c28e65a54e47645af02bc722442`.

- Line 1: # A2 review handoff — 29 September 2026
- Line 3: **Status:** implementation and offline tests complete in an isolated review copy;
- Line 4: application deployment awaits owner approval. [A2_REVIEW.patch](A2_REVIEW.patch)
- Line 32: - `git apply --check artifacts/a2-2026-09-29/A2_REVIEW.patch` passed against
- Line 38: During development, `scrapper/` was discovered to be bind mounted into running

## artifacts/a2-2026-09-29/AFFECTED_COMPANY_REPORT.md

Source: [artifacts/a2-2026-09-29/AFFECTED_COMPANY_REPORT.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/a2-2026-09-29/AFFECTED_COMPANY_REPORT.md)

Lines: 29. SHA256: `136411d79c6e6ba703ee7955b212d8b5819c5c523b0feb0374d2e11cbd35d3d6`.

- Line 17: its exchange routes, stored source-specific standalone filing counts, BSE enqueue
- Line 19: direct *source-discovery exposure* while the BSE feed is blocked. A current NSE

## artifacts/a2-2026-09-29/MIGRATION_065_INCIDENT_REVIEW.md

Source: [artifacts/a2-2026-09-29/MIGRATION_065_INCIDENT_REVIEW.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/a2-2026-09-29/MIGRATION_065_INCIDENT_REVIEW.md)

Lines: 102. SHA256: `f20b5717c6df62e72dad9291e8058127dfd93eaabf57f815f407cc3865ca9ed1`.

- Line 1: # Migration 065 incident review — 29 September 2026
- Line 17: application did not log which process acquired the migration lock at 09:29:23,
- Line 20: explains how a visible review file was applied.
- Line 22: The mounted source files were restored immediately. Current inspection found
- Line 25: request was made during this review.

## artifacts/a2-2026-09-29/PRODUCTION_COMPATIBILITY_TEST.md

Source: [artifacts/a2-2026-09-29/PRODUCTION_COMPATIBILITY_TEST.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/a2-2026-09-29/PRODUCTION_COMPATIBILITY_TEST.md)

Lines: 77. SHA256: `3c974da12484deb016385681462582cc4da488f69430de0853dcc703f3e4d872`.

- Line 3: **Result: PASS in isolation. No A2 application deployment was performed.**
- Line 14: - All test containers used `--network none`. The reviewed code and test scripts
- Line 21: The exact contents of [A2_REVIEW.patch](A2_REVIEW.patch), represented in the
- Line 22: isolated `a2-review/` tree, were tested with pinned pytest 9.1.1:
- Line 30: The migration-gate test inserted a new review SQL file into a temporary

## artifacts/a2-2026-09-29/SOURCE_ACCESS_MATRIX.md

Source: [artifacts/a2-2026-09-29/SOURCE_ACCESS_MATRIX.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/a2-2026-09-29/SOURCE_ACCESS_MATRIX.md)

Lines: 44. SHA256: `13b7116e5e2494fcad6a3d6d6d4beae246cc6cdb785577c33c243af44b551f7b`.

- Line 20: The review patch makes the scheduler persist a BSE 403 as `source_access_blocked`, stop further
- Line 22: continues other sources. The patched status output reports `ACCESS_BLOCKED` separately
- Line 27: contained it; the source files were restored immediately afterward. See the
- Line 28: A2 review handoff for this deployment incident and the current release gate.
- Line 31: if the measured company/source gap warrants it. After approved machine access is

## artifacts/b0-2026-09-29/B0_EXISTING_DATA_ML_CARD.md

Source: [artifacts/b0-2026-09-29/B0_EXISTING_DATA_ML_CARD.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b0-2026-09-29/B0_EXISTING_DATA_ML_CARD.md)

Lines: 58. SHA256: `d6adb6554a6615a74d43edafacd724e5f73a40afcf0951440e2942e5be98e080`.

- Line 15: - Reused the A0 [100-company sample](../a0-2026-09-29/financial_sample_100.csv); selected **29** accepted June 2026 companies for purposeful variation. The strata are sampling aids, not approved sector classifications: 23 dual-linked, 2 BSE-only, 3 NSE-only, 1 other/unmapped; 25 eligible under the dated collection scope, 3 below its floor, 1 eligibility unknown; 25 non-bank and 4 bank/NBFC examples. Two companies have negative quarterly PAT. The full 100-company validation remains **OPEN**.
- Line 18: - `source_table_cells` comparison matched **377/377** selected observations, including negative PAT/EPS and finance-sector labels. All **29** already stored HTML snapshots were copied read-only from the existing container to temporary files, checked against database SHA-256 hashes, then independently parsed by [verify_html.py](verify_html.py): **377/377** observed values matched [B0_ORIGINAL_HTML_CHECK.csv](B0_ORIGINAL_HTML_CHECK.csv). HTML company names matched the registry in **29/29** cases ([B0_IDENTITY_SPOT_CHECK.csv](B0_IDENTITY_SPOT_CHECK.csv)). This verifies the sampled values against the original stored Screener pages; it is not reconciliation to external primary filings.
- Line 35: 1. The 29 current quarter rows passed scope, period, source-cell, unit, scale, timestamp-order, accepted-event and source-link checks. This is a **proof of exportability**, not a guarantee that every accepted value is economically correct. All 29 original stored HTML blobs were independently reparsed for the selected values; external primary filings were not downloaded or reconciled. Parser validation was `passed` for 5 accepted parses and `passed_with_warnings` for 24. The latter warnings were mostly missing company-profile links; Veefin's identity-symbol warning was checked against its hash-verified HTML name and BSE code 543931, which matched the stored identity mapping.
- Line 40: 6. The present-day Screener page does not prove historical point-in-time availability. The selected captures range from 6 August to 23 September 2026; all 377 tested observations have `source_published_at_utc=NULL`. See [B0_PIT_LIMITATIONS.md](B0_PIT_LIMITATIONS.md).
- Line 56: The B1 interface should query these accepted observations with explicit company, decision cutoff, scope and feature version. It should retain original/corrected versions, original units and annual intervals, plus a missing/sector-applicability status. No new source integration or finance database is indicated by B0.

## artifacts/b0-2026-09-29/B0_LABEL_INVENTORY.md

Source: [artifacts/b0-2026-09-29/B0_LABEL_INVENTORY.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b0-2026-09-29/B0_LABEL_INVENTORY.md)

Lines: 17. SHA256: `e5fb72cb4c3b43333504680392ac32e3a13822e558f730be4cb3e6857c1e20c3`.

- Line 13: The Q1 derived `rejected_candidates.csv` has 400 candidates, but [FG00_SOURCE_MAP.md](../focus_ml/governance/FG00_SOURCE_MAP.md) says their eligibility and mentor decision are unapproved; each remains `UNKNOWN` with null outcome. The partial Q1 decision-universe file separately records 30 unknown rows. Neither set supplies reviewed negative labels. Q4 has no approved negative-candidate universe. Absence from either Focus List is **not** rejection.
- Line 15: **List edits/freeze:** only the v001 extracted registry and source-file hashes are available here. There is no dated immutable history of additions/removals, reviewer decisions, or pre-result freeze. The screenshot capture time and source-file mtime can bound when an artifact existed; neither establishes the mentor's decision cutoff.
- Line 17: **B3 consequence:** `CAN_TRAIN_SUPERVISED_ML: NO` on these labels. Before supervised imitation, record the reviewed eligible universe, explicit accepted/rejected decisions, decision timestamps, list edits, and a frozen future test list. No negative labels were fabricated in B0.

## artifacts/b0-2026-09-29/B0_PIT_LIMITATIONS.md

Source: [artifacts/b0-2026-09-29/B0_PIT_LIMITATIONS.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b0-2026-09-29/B0_PIT_LIMITATIONS.md)

Lines: 26. SHA256: `f5366268d055652a35eff8d413b1c0145166b0696b0fcc92adf1fd8b4e3d1aff`.

- Line 1: # B0 point-in-time limitations — 29 September 2026
- Line 5: Point-in-time status depends on a **decision cutoff**. [B0_PIT_CLASSIFICATION.csv](B0_PIT_CLASSIFICATION.csv) classifies the 377 selected source-backed observations at two explicit perspectives:
- Line 13: The first two statuses are conditional on different cutoffs, so they are not contradictory or additive. The latest June 2026 accepted parses in the cohort were first observed from **6 August through 23 September 2026**. The 2025 comparative and annual cells in those parses are retrospective values. An earlier accepted parse may establish a later as-of version, but it cannot be backdated to a company's original filing without independent publication evidence.
- Line 17: - FY2026 Q4 screenshots carry 2 August 2026 capture filenames. The selected backup's first captures begin 6 August. These current accepted pages are **retrospective only** for a decision represented by those screenshots; the actual selection/freeze may be earlier and is unknown.
- Line 18: - FY2027 Q1 `stocks.csv` has no decision date or freeze field. Its filesystem modification time is 3 August 2026, but that is not an authoritative freeze. The current financial captures begin later. Historical feature availability relative to the mentor's real Q1 decisions is **unknown/not proven**.

## artifacts/b0-2026-09-30/B0_EXISTING_DATA_ML_CARD.md

Source: [artifacts/b0-2026-09-30/B0_EXISTING_DATA_ML_CARD.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b0-2026-09-30/B0_EXISTING_DATA_ML_CARD.md)

Lines: 52. SHA256: `b4a7ba5f8aea213f7ecf297b5ea99131708f86eaff13dba862380736ca41c484`.

- Line 3: **Status: PARTIAL — retained evidence validated; fresh database verification unavailable.**
- Line 11: The existing [migration incident review](../a2-2026-09-29/MIGRATION_065_INCIDENT_REVIEW.md) records an additive schema change, two BSE BLOCKED events, a consistent post-065 backup and restored integrity check. [Compatibility evidence](../a2-2026-09-29/PRODUCTION_COMPATIBILITY_TEST.md) records old-code operation against a disposable post-065 copy under Python 3.12.14. These are prior reports, not current runtime observations.
- Line 24: - 17 actual label-source files hash-match the registry: 16 Q4 screenshots and the Q1 CSV. Registry contains 430 positive rows, 426 resolved company-quarter rows and 367 distinct resolved issuers. No approved reviewed negative or verified freeze timestamp is present. Source-hash verification does not verify OCR correctness or historical identity resolution.
- Line 32: The prior version audit reports 23/29 companies with multiple accepted parses and eight with changed financial cells. All 377 sampled observations lack publication timestamps. A present-day capture of old numbers is exploratory before its first observed/accepted time. [PIT limitations](B0_PIT_LIMITATIONS.md) describe this restriction. Annual cash flow and balance data retain their own reporting ends; Huhtamaki's unverified later balance interval must not silently become an annual feature.
- Line 34: Examples from retained output (money in INR crore, EPS in INR): TVS Motor revenue 13,896, PBT 1,589, PAT 1,174, EPS 24.71; Kaya PAT −15 and EPS −9.99; Bandhan revenue 5,631 with financing-sector applicability. These are captured Screener evidence, not independently reconciled primary filings.

## artifacts/b0-2026-09-30/B0_LABEL_INVENTORY.md

Source: [artifacts/b0-2026-09-30/B0_LABEL_INVENTORY.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b0-2026-09-30/B0_LABEL_INVENTORY.md)

Lines: 21. SHA256: `eee768e0f36ae4492e19bce9dc8fb4c2870b31ecd43352311c7a70caf538a3f0`.

- Line 5: # B0 Focus-label inventory — retained 29 September evidence, reviewed 30 September 2026
- Line 17: The Q1 derived `rejected_candidates.csv` has 400 candidates, but [FG00_SOURCE_MAP.md](../focus_ml/governance/FG00_SOURCE_MAP.md) says their eligibility and mentor decision are unapproved; each remains `UNKNOWN` with null outcome. The partial Q1 decision-universe file separately records 30 unknown rows. Neither set supplies reviewed negative labels. Q4 has no approved negative-candidate universe. Absence from either Focus List is **not** rejection.
- Line 19: **List edits/freeze:** only the v001 extracted registry and source-file hashes are available here. There is no dated immutable history of additions/removals, reviewer decisions, or pre-result freeze. The screenshot capture time and source-file mtime can bound when an artifact existed; neither establishes the mentor's decision cutoff.
- Line 21: **B3 consequence:** `CAN_TRAIN_SUPERVISED_ML: NO` on these labels. Before supervised imitation, record the reviewed eligible universe, explicit accepted/rejected decisions, decision timestamps, list edits, and a frozen future test list. No negative labels were fabricated in B0.

## artifacts/b0-2026-09-30/B0_PIT_LIMITATIONS.md

Source: [artifacts/b0-2026-09-30/B0_PIT_LIMITATIONS.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b0-2026-09-30/B0_PIT_LIMITATIONS.md)

Lines: 28. SHA256: `fa407a5aa59b82665ca7812fde0e2d5c09b12edb83a1b6e63b1dfc511750d633`.

- Line 3: # B0 point-in-time limitations — retained 29 September evidence, reviewed 30 September 2026
- Line 7: Point-in-time status depends on a **decision cutoff**. [B0_PIT_CLASSIFICATION.csv](B0_PIT_CLASSIFICATION.csv) classifies the 377 selected source-backed observations at two explicit perspectives:
- Line 15: The first two statuses are conditional on different cutoffs, so they are not contradictory or additive. The latest June 2026 accepted parses in the cohort were first observed from **6 August through 23 September 2026**. The 2025 comparative and annual cells in those parses are retrospective values. An earlier accepted parse may establish a later as-of version, but it cannot be backdated to a company's original filing without independent publication evidence.
- Line 19: - FY2026 Q4 screenshots carry 2 August 2026 capture filenames. The selected backup's first captures begin 6 August. These current accepted pages are **retrospective only** for a decision represented by those screenshots; the actual selection/freeze may be earlier and is unknown.
- Line 20: - FY2027 Q1 `stocks.csv` has no decision date or freeze field. Its filesystem modification time is 3 August 2026, but that is not an authoritative freeze. The current financial captures begin later. Historical feature availability relative to the mentor's real Q1 decisions is **unknown/not proven**.

## artifacts/b1-2026-10-01/B1_FEATURE_DICTIONARY.md

Source: [artifacts/b1-2026-10-01/B1_FEATURE_DICTIONARY.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b1-2026-10-01/B1_FEATURE_DICTIONARY.md)

Lines: 62. SHA256: `06cb8a0d94c5d68a21ab49ea87e03441f120e1407f70286a7849ab1f63e63999`.

- Line 3: Implemented extension: [ml_contracts.py](../../b1-review/scrapper/pipeline/ml_contracts.py). Review patch targets the existing `scrapper/pipeline/ml_contracts.py`; the legacy `build_screener_ml_features` behavior is preserved. This uses the existing database schema, with no provider integration or second finance store.
- Line 8:     period=None, sector='unknown', source=None, evidence_kind='stored_records',
- Line 12: The caller supplies a read-only SQLite connection on an isolated copy, ideally in a consistent read transaction. `decision_time` requires an aware ISO timestamp. `period` selects a reporting month; omission selects the latest accepted reporting month available at the cutoff. `sector` is explicit (`industrial`, `bank`, `nbfc`, `insurer`, `unknown`); the exporter never infers it from a company name or collection threshold. A production cohort needs a governed applicability map. `source` disambiguates independently accepted sources; there is no provider preference or network fetch.
- Line 14: The structured result includes `company_id`, actual period end, cutoff, standalone scope, immutable feature version, evidence status, completeness, reasons, accepted-source/parse/blob provenance, individual features, dated raw quarter history, and optional snapshot context. Each feature includes value, status, unit, scale, period end/type, effective availability, raw input dictionaries and formula. JSON serialization uses `dataclasses.asdict`; decimals are exported as strings, missing values as `None` (empty CSV cells).
- Line 43: The parse, blob capture and acceptance timestamps must be valid, aware, nonfuture and ordered. Each observation must have standalone scope and a valid availability timestamp no earlier than its first observation or evidenced publication. An observation after the cutoff is excluded. Scope/provenance breaches and malformed timestamps raise; they are never silently imputed.

## artifacts/b1-2026-10-01/B1_RECONCILIATION_AND_TESTS.md

Source: [artifacts/b1-2026-10-01/B1_RECONCILIATION_AND_TESTS.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b1-2026-10-01/B1_RECONCILIATION_AND_TESTS.md)

Lines: 46. SHA256: `87712b35790c0fa6eb17025808d58bb73926104753392a1e434314a87fd09737`.

- Line 3: **Status: IMPLEMENTED as an isolated review extension; original-database reconciliation remains PARTIAL.**
- Line 5: **B1_CAN_PROCEED_TO_B2: PARTIAL.** A deterministic consumer can now use the interface, statuses and 29-row exploratory cohort for offline development. A PIT-backed or broad production selection claim still requires the separate B0 original-database/source-byte closure, governed sector applicability and owner-approved B2 rules. B2 and supervised ML were not started.
- Line 7: Branch/base: `main`, `e71d1ee168d4a2b5c155aa6302347aeaad841e46`. Because the active source is configured as a production bind mount, changes were staged in `b1-review/`, rather than written into the active `scrapper/`. [B1_REVIEW.patch](B1_REVIEW.patch) targets only the existing ML contract and its new regression tests. The review directory contains unchanged supporting Python/schema files copied from the base; it is a development checkout, not another operational pipeline/database. No Compose file or A2 patch was changed.
- Line 13: cd b1-review
- Line 19: [B1_TEST_OUTPUT.txt](B1_TEST_OUTPUT.txt) contains the final test log. `git apply --check artifacts/b1-2026-10-01/B1_REVIEW.patch` also passed against the active checkout; the patch was not applied. Active `scrapper/pipeline/ml_contracts.py` SHA-256 remained `49d9c80ebe6d246923ee5de871d76cbe51f1b6cfb203d8b66aaa375f8f0311e8`.

## artifacts/b2-2026-10-01/B2_COHORT_COMPARISON.md

Source: [artifacts/b2-2026-10-01/B2_COHORT_COMPARISON.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-2026-10-01/B2_COHORT_COMPARISON.md)

Lines: 34. SHA256: `de34fe77ec131e856bc3ec5cf7a0aec40fef5018dc64a7e48e7247db116a42da`.

- Design, reference, or supporting evidence; no matching status assertion.

## artifacts/b2-2026-10-01/B2_DECISION_CARDS.md

Source: [artifacts/b2-2026-10-01/B2_DECISION_CARDS.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-2026-10-01/B2_DECISION_CARDS.md)

Lines: 872. SHA256: `4ffe8592347a88831082514c18fa6fbc4f9c2c1afc6dab1cb4ef5e9f16629584`.

- Line 5: **REVIEW** — SECTOR_MAP_UNAPPROVED; OWNER_RULES_PENDING; NO_APPLICABLE_FINANCIAL_RULES
- Line 35: **REVIEW** — SECTOR_MAP_UNAPPROVED; OWNER_RULES_PENDING; NO_APPLICABLE_FINANCIAL_RULES
- Line 65: **REVIEW** — SECTOR_MAP_UNAPPROVED; OWNER_RULES_PENDING; NO_APPLICABLE_FINANCIAL_RULES
- Line 95: **REVIEW** — SECTOR_MAP_UNAPPROVED; OWNER_RULES_PENDING; NO_APPLICABLE_FINANCIAL_RULES
- Line 125: **REVIEW** — SECTOR_MAP_UNAPPROVED; OWNER_RULES_PENDING; NO_APPLICABLE_FINANCIAL_RULES

## artifacts/b2-2026-10-01/B2_FOCUS_RULES_V1.md

Source: [artifacts/b2-2026-10-01/B2_FOCUS_RULES_V1.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-2026-10-01/B2_FOCUS_RULES_V1.md)

Lines: 79. SHA256: `ba66e21fcf07d0fb8510b0ba1a55cd053c5c3b65389a34fe62146c5c6a360853`.

- Line 9: **Current evidence status:** B1 historical cohort remains `EXPLORATORY_BACKFILL`.
- Line 15: `FAIL` → `INSUFFICIENT_EVIDENCE` → `REVIEW` → `PASS`
- Line 19: - **REVIEW:** no hard fail; mandatory evidence exists; at least one borderline/special/sector/short-history/base-effect condition exists.
- Line 20: - **PASS:** all mandatory applicable rules pass and no review condition exists.
- Line 37: For the cash-conversion rule, use `sum(CFO) / sum(Operating Profit)` over matched completed financial years. Do not substitute an ambiguous vendor ratio. For 3–4 valid completed FY, calculate the available cumulative ratio and route to `REVIEW_SHORT_HISTORY` if no hard fail is observed. For fewer than 3 completed FY because the company is genuinely new, use `REVIEW_NEW_LISTING_OR_SHORT_HISTORY` rather than fabricating five-year history.

## artifacts/b2-2026-10-01/B2_RULE_DECISIONS_REQUIRED.md

Source: [artifacts/b2-2026-10-01/B2_RULE_DECISIONS_REQUIRED.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-2026-10-01/B2_RULE_DECISIONS_REQUIRED.md)

Lines: 35. SHA256: `bfb36b393f0a7f09dfa7abac4b21741dfcba93fbf2db763c2301ddb3092729b0`.

- Line 1: # B2 rule decisions required
- Line 3: Status: DRAFT_PENDING_OWNER. No approved numerical financial selection standard was found. Original-database verification is independent; it does not prevent this isolated B2 implementation.
- Line 7: - Roadmap V4 §B2 (lines 153–165): owner approves financial rules; standalone B1 evidence; one PASS/FAIL/REVIEW/INSUFFICIENT_EVIDENCE per company; sector-appropriate comparisons; missing mandatory evidence never becomes zero; immutable decisions; optional PPT/concall/rating cannot reject a financial selection. Actual freeze time and previously visible information must be disclosed.
- Line 8: - B1 dictionary/contract: signed observations, exact quarterly comparators, positive growth denominator, latest common annual statement end, source/parse/blob/observation references, feature version v1, independent completeness and availability classification. Unknown/insurer applicability is insufficient; incompatible metrics are N/A. Snapshot ratios cannot stand in for historical statement evidence.
- Line 15: - `b1-review/scrapper/pipeline/ml_contracts.py` implements the B1 observation/availability contract; 464 retained values/nulls were reconciled. No financial thresholds were added.

## artifacts/b2-2026-10-01/B2_SUPERSESSION_RECORD.md

Source: [artifacts/b2-2026-10-01/B2_SUPERSESSION_RECORD.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-2026-10-01/B2_SUPERSESSION_RECORD.md)

Lines: 13. SHA256: `50ec12e843fd40d0359e9615518838bed0bf3e5a090a76775a057f5dfef0f51c`.

- Line 5: The owner explicitly stopped and superseded the previous threshold-baseline assignment. These exact artifacts are preserved only for audit history and have logical status **SUPERSEDED_PROVISIONAL**:
- Line 11: Their original embedded approval/freeze text is historical and does not govern the current analysis. `B2_V1_FREEZE_RECORD.json` records the earlier snapshot verification, not a current rule freeze. The interrupted `b2-review/scrapper/pipeline/focus_v1.py` implementation is audit-only, untested, incomplete and not used by the discovery runner. No frozen V1 Focus baseline was run. The earlier draft cohort contains review-only outputs and is not this selected-versus-not-selected analysis.

## artifacts/b2-discovery-2026-10-01/B2_BASELINE_FINDINGS.md

Source: [artifacts/b2-discovery-2026-10-01/B2_BASELINE_FINDINGS.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-discovery-2026-10-01/B2_BASELINE_FINDINGS.md)

Lines: 38. SHA256: `a1f7bfedeb60d50d702bf1266402ea3a894d3ed7c46fe6de6cc4b4b9d64f478d`.

- Line 5: **RETROSPECTIVE_EXPLORATORY · PIT_NOT_VERIFIED.** Actual Q1 mentor cutoff and research-universe contract are unknown. Absence means absent from the supplied reference, never mentor rejection. No strict historical NOT_SELECTED group is established. No proposed ₹700 crore rule or superseded deterministic rules are used.
- Line 11: 2. **Weak/no observed distinction:** annual_fcf, cfo_op_3y, cfo_pat_3y, cfo_op_5y, cfo_pat_5y, debt_equity_book_proxy, borrowings_yoy_pct, cfo_op_latest, cfo_pat_latest, fcf_pat_latest, fcf_3y_sum, debtor_days, debtor_days_yoy_delta, inventory_days, inventory_days_yoy_delta, days_payable, days_payable_yoy_delta, cash_conversion_cycle, cash_conversion_cycle_yoy_delta, profit_cagr_3y_pct, profit_cagr_5y_pct. Historical market cap, valuation and ownership have no valid comparison evidence; that is unknown, not proof of no effect.
- Line 19: 6. **Absent financial lookalikes:** Bajaj Finance Ltd ↔ Home First Finance Company India Ltd (mean shared IQR distance 0.030, 4 metrics); State Bank of India ↔ Indian Bank (mean shared IQR distance 0.036, 4 metrics); Yes Bank Ltd ↔ Karnataka Bank Ltd (mean shared IQR distance 0.050, 4 metrics); Axis Bank Ltd ↔ DCB Bank Ltd (mean shared IQR distance 0.056, 4 metrics); Capital Small Finance Bank Ltd ↔ Bank of Maharashtra (mean shared IQR distance 0.057, 4 metrics). These are absent-reference companies with uncertain historical eligibility, not proven historical non-selections or mentor false positives.
- Line 23: 8. **Candidate deterministic rules:** association hypotheses and evidence grades appear in B2_CANDIDATE_RULES_FROM_EVIDENCE.md. No rules are owner-approved or frozen. Thresholds in the superseded V1 artifact were not applied or tuned.
- Line 25: 9. **Additional research:** dated mentor universe/list context, issuer sector and corporate-action comparability first. For adequately measured but unexplained selections, later targeted existing PPT/concall evidence on guidance, orders, capex, execution and ratings could test hypotheses. No document factor is inferred merely from absent quantitative patterns; no document LLM processing occurred.

## artifacts/b2-discovery-2026-10-01/B2_CANDIDATE_RULES_FROM_EVIDENCE.md

Source: [artifacts/b2-discovery-2026-10-01/B2_CANDIDATE_RULES_FROM_EVIDENCE.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-discovery-2026-10-01/B2_CANDIDATE_RULES_FROM_EVIDENCE.md)

Lines: 93. SHA256: `b9861c5c0f6f39022f86242a09c05187fc4f9d47def5b4eabfcfbfcb6716b175`.

- Line 3: **RETROSPECTIVE_EXPLORATORY · PIT_NOT_VERIFIED.** Actual Q1 mentor cutoff and research-universe contract are unknown. Absence means absent from the supplied reference, never mentor rejection. No strict historical NOT_SELECTED group is established. No proposed ₹700 crore rule or superseded deterministic rules are used.
- Line 5: These are association hypotheses for owner review, not approved criteria, rejection explanations, thresholds or a fitted model. No STRONG_EVIDENCE claim is made from one retrospective population. Bands are pooled quantiles or user-requested illustrations; no threshold search optimizes Focus recovery.
- Line 11: Exceptions: individual distributions overlap; turnarounds and source coverage can differ. Sector-specific financials are separately stratified, and operating ratios are suppressed for lending/financial-other formats. All observations are retrospective; mentor reasons and historical eligibility are unknown. EPS also lacks corporate-action verification.
- Line 17: Exceptions: individual distributions overlap; turnarounds and source coverage can differ. Sector-specific financials are separately stratified, and operating ratios are suppressed for lending/financial-other formats. All observations are retrospective; mentor reasons and historical eligibility are unknown. EPS also lacks corporate-action verification.
- Line 23: Exceptions: individual distributions overlap; turnarounds and source coverage can differ. Sector-specific financials are separately stratified, and operating ratios are suppressed for lending/financial-other formats. All observations are retrospective; mentor reasons and historical eligibility are unknown. EPS also lacks corporate-action verification.

## artifacts/b2-discovery-2026-10-01/B2_DECISION_UNIVERSE.md

Source: [artifacts/b2-discovery-2026-10-01/B2_DECISION_UNIVERSE.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-discovery-2026-10-01/B2_DECISION_UNIVERSE.md)

Lines: 14. SHA256: `cb62b9abf11beab749f027c321df03f7c58725c439bef42edcd054c4f8a5fb1a`.

- Line 3: **RETROSPECTIVE_EXPLORATORY · PIT_NOT_VERIFIED.** Actual Q1 mentor cutoff and research-universe contract are unknown. Absence means absent from the supplied reference, never mentor rejection. No strict historical NOT_SELECTED group is established. No proposed ₹700 crore rule or superseded deterministic rules are used.
- Line 5: STRICT_UNIVERSE_STATUS = NOT_RECONSTRUCTABLE_YET. Strict NOT_SELECTED count: 0; this means not established, not that every company was selected.
- Line 9: Broad membership requires an existing resolved NSE/BSE security identifier and does not exclude missing June data, uncertain listing, financial companies, or uncertain instrument/market cap. Current-snapshot inactive/not-yet-listed companies are separately excluded; those facts are not backdated to Q1. June comparison requires the existing B1 accepted-version contract to return usable June features. No June result and source failures remain distinct and do not become comparison negatives. No current market cap substitutes for historical cap. Unknown mentor scope/listing/instrument eligibility remains explicit on every row. Exchange registry spans August–September; current absence from a mast
- Line 14: Coverage limitation: 5919 exchange ISIN registry entries are represented in the exported identity evidence. 1508 ISIN entries lack a resolved financial-company mapping; they remain separate audit rows in B2_DECISION_UNIVERSE.csv and never become financial comparison negatives. Only 2073/4401 resolved broad candidates have accepted June standalone features. Acquisition/processing scope, issuer coverage, listing format and reporting cadence can affect which companies have data; complete-case selection frequencies are not whole-market selection probabilities. The existing ₹580 processing configuration may influence stored coverage, but no market-cap floor is applied by this analysis. Historical

## artifacts/b2-discovery-2026-10-01/B2_FOCUS_LIST_RESOLUTION.md

Source: [artifacts/b2-discovery-2026-10-01/B2_FOCUS_LIST_RESOLUTION.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-discovery-2026-10-01/B2_FOCUS_LIST_RESOLUTION.md)

Lines: 12. SHA256: `1ae2d52081d2ea00511d031d13e5b23c92010ffb4fa732ad438c954c99569f6f`.

- Line 3: **RETROSPECTIVE_EXPLORATORY · PIT_NOT_VERIFIED.** Actual Q1 mentor cutoff and research-universe contract are unknown. Absence means absent from the supplied reference, never mentor rejection. No strict historical NOT_SELECTED group is established. No proposed ₹700 crore rule or superseded deterministic rules are used.
- Line 12: Every source token and heading is retained in B2_FOCUS_SYMBOL_RESOLUTION.csv. RESOLVED requires unique company ID plus at least one linked ISIN. ISIN ambiguity is retained as an array; exchange-prefix mismatch is explicit. An underscore/hyphen alias is applied only to a uniquely matching verified exchange identifier; no fuzzy company-name matching. Unresolved symbols remain in the universe and explanation files. Historical symbol/ISIN effectiveness at the unknown mentor cutoff remains unverified.

## artifacts/b2-discovery-2026-10-01/B2_SELECTED_VS_NOT_SELECTED_SUMMARY.md

Source: [artifacts/b2-discovery-2026-10-01/B2_SELECTED_VS_NOT_SELECTED_SUMMARY.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-discovery-2026-10-01/B2_SELECTED_VS_NOT_SELECTED_SUMMARY.md)

Lines: 24. SHA256: `fd8a8f2b9630880c54e9abf43dd044e9060685ddf4b22d0aa5debcfbc5632be8`.

- Line 3: **RETROSPECTIVE_EXPLORATORY · PIT_NOT_VERIFIED.** Actual Q1 mentor cutoff and research-universe contract are unknown. Absence means absent from the supplied reference, never mentor rejection. No strict historical NOT_SELECTED group is established. No proposed ₹700 crore rule or superseded deterministic rules are used.
- Line 21: No causal attribution, generalizable prediction or strict historical eligibility follows from these differences. Annual quality ratios are source-reported definitions, whose economic formula is not independently audited; leverage is explicitly a book-equity proxy. Structural absence and applicability contribute to whole-group missingness. CSVs separately report applicable denominators, N/A counts and applicability-adjusted missingness. Market cap/valuation and ownership are unavailable rather than zero. The 1,085-row August Screener screen is a query-selected subset with an unapproved query contract; it is not used as the comparison universe or as a new source of negatives.
- Line 24: Coverage limitation: 5919 exchange ISIN registry entries are represented in the exported identity evidence. 1508 ISIN entries lack a resolved financial-company mapping; they remain separate audit rows in B2_DECISION_UNIVERSE.csv and never become financial comparison negatives. Only 2073/4401 resolved broad candidates have accepted June standalone features. Acquisition/processing scope, issuer coverage, listing format and reporting cadence can affect which companies have data; complete-case selection frequencies are not whole-market selection probabilities. The existing ₹580 processing configuration may influence stored coverage, but no market-cap floor is applied by this analysis. Historical

## artifacts/b2-discovery-2026-10-01/B2_VALIDATION.md

Source: [artifacts/b2-discovery-2026-10-01/B2_VALIDATION.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-discovery-2026-10-01/B2_VALIDATION.md)

Lines: 20. SHA256: `829736723c8124b469df1889ab8db95a7731ab0e7859cd96c805fbef64c088dd`.

- Line 3: Status: **PASS for offline arithmetic and cross-file consistency**. This is not original-source-byte verification or historical PIT certification.
- Line 6: - Cross-file checks cover every source Focus token, identity counts, explanation/pattern counts, comparison population membership, explicit empty strict universe, unresolved exchange-ISIN audit rows, feature statuses, missing versus N/A, available counts/medians, illustrative interaction arithmetic, and absent-reference lookalike identity/coverage.
- Line 7: - The B1 exporter replayed all **464/464** retained feature values/nulls for the same 29 IDs against the existing original A0 backup. This is an original-backup value replay, not a source-byte audit, complete revision-history reconstruction, or closure of the independent B0/B1 provenance item. Existing retained B1 data remains EXPLORATORY_BACKFILL. The entire discovery analysis remains RETROSPECTIVE_EXPLORATORY / PIT_NOT_VERIFIED.
- Line 8: - All three superseded provisional artifacts retain their recorded hashes, including the exact supplied V1 JSON SHA256. They are not used by the discovery analysis.
- Line 20: `B2_VALIDATION.json` records artifact and script SHA256 values. Source/review scripts use the Python standard library. The exporter ran in the existing Docker Python runtime, without applying the B1 review patch to active source. The arithmetic/check scripts ran on host Python 3.14.5; this is not a full production dependency-suite claim.

## artifacts/b2-discovery-2026-10-01/README.md

Source: [artifacts/b2-discovery-2026-10-01/README.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-discovery-2026-10-01/README.md)

Lines: 53. SHA256: `4fcddb4df39b0c7d65af3efba16ba103e7d6684688b9d46e1d05281de35ece27`.

- Line 3: Current objective: Q1 FY27 selection-association discovery. **RETROSPECTIVE_EXPLORATORY / PIT_NOT_VERIFIED**. Strict historical universe is not reconstructable yet. Companies absent from the supplied list are not proven historical mentor non-selections or rejections.
- Line 5: The previous V1 threshold artifacts remain unchanged and logically SUPERSEDED_PROVISIONAL. No deterministic threshold baseline or supervised model was run for this objective.
- Line 10: - [Candidate associations for owner review](B2_CANDIDATE_RULES_FROM_EVIDENCE.md)
- Line 12: - [Universe definition and limitations](B2_DECISION_UNIVERSE.md)
- Line 25: - [Unexplained or incompletely measured Focus selections](B2_UNEXPLAINED_FOCUS_SELECTIONS.csv)

## artifacts/b2-discovery-2026-10-01/review-v2/B2_BASELINE_FINDINGS_V2.md

Source: [artifacts/b2-discovery-2026-10-01/review-v2/B2_BASELINE_FINDINGS_V2.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/b2-discovery-2026-10-01/review-v2/B2_BASELINE_FINDINGS_V2.md)

Lines: 177. SHA256: `a4453a0059bc38d9907e15bff16f9de2b59c9dfa5fc7f512859e911149bef064`.

- Line 1: # B2 baseline findings V2 — reconciliation/review only
- Line 3: **RETROSPECTIVE_EXPLORATORY / PIT_NOT_VERIFIED. STRICT_UNIVERSE_STATUS = NOT_RECONSTRUCTABLE_YET.** No model, document processing, mentor rules or financial thresholds were frozen. Absents are reference-list nonmembers with unproven historical eligibility, not mentor rejections.
- Line 9: - **384** have usable comparable June features. The four resolved but un-compared symbols are **VMARCIND, MONOLITH, OBSCP, TAJGVK**. They lack usable June rows in the saved accepted export; this does not establish that the issuers did not report.
- Line 10: - **146 diagnostic pattern matches = 108 STRONG_QUANT_MATCH + 17 GROWTH_WITH_WEAK_QUALITY + 13 TURNAROUND + 8 QUALITY_WITH_MODERATE_GROWTH**. All 146 are resolved and compared.
- Line 11: - **182 pattern-unmatched residuals**: all resolved and compared. The old label FINANCIALS_DO_NOT_EXPLAIN_SELECTION was too strong. Correct interpretation: **unclassified by these chosen diagnostic patterns**, not proof that financial data cannot explain selection.

## artifacts/coverage-audit-2026-10-01/COVERAGE_AUDIT_FINDINGS.md

Source: [artifacts/coverage-audit-2026-10-01/COVERAGE_AUDIT_FINDINGS.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/coverage-audit-2026-10-01/COVERAGE_AUDIT_FINDINGS.md)

Lines: 72. SHA256: `53371fd9c2c6505c01969ef22041ab4faae3be7890e4b08ec81b596de4ad7dad`.

- Line 3: Completed as an isolated reconciliation using official NSE CM MII reference data and stored observations. **No live BSE access, no BSE bypass, no Screener requests, no database writes, queue changes, migrations or service restarts were performed by this audit.** No B2 rules were frozen, models trained or documents processed.
- Line 7: **668 ISINs have no stored exchange registry entry**, but 657 of them have no normally eligible equity token in this file. The **11 normally eligible registry gaps** are in `COVERAGE_MISSING_REGISTRY.csv` with the eligibility flag. These are missing registry entries, not a claim that every such entity belongs to the collection scope or historical mentor universe.
- Line 9: **1,866 ISINs have no resolved internal company identity** after exact ISIN, official NSE symbol, verified Screener canonical-path and stored versioned BSE-code/security-ID reconciliation. Of these, **1,067** have a normally eligible equity token. This set overlaps the registry-gap set; do not add the counts. `COVERAGE_UNRESOLVED_IDENTITIES.csv` retains all paths, scope evidence, queue states and exact unresolved reasons.
- Line 13: **65 currently scope-eligible, normally eligible ISINs lack an accepted June 2026 standalone page. All 65 already have accepted standalone numeric records for another period.** Most are SME-series securities; absent June rows can reflect reporting frequency or source/period labeling. This audit does not establish that a June result was required or published. See `COVERAGE_CURRENT_SCOPE_GAPS.csv`; this is a quarter-specific review list, not a missing-company list or an instruction to fetch.
- Line 17: Reference date: **30 September 2026**, latest combined file identified from retrieved [NSE metadata](https://www.nseindia.in/api/daily-reports?key=CM). Stored database read-only transaction: **2026-10-01T07:05:16.291067+00:00** through **2026-10-01T07:05:19.927581+00:00**. Supplemental immutable security-version attributes were read afterward and joined only by the version IDs in this snapshot. No live BSE data is needed.

## artifacts/coverage-audit-2026-10-01/NSE_MII_SCHEMA_REVIEW.md

Source: [artifacts/coverage-audit-2026-10-01/NSE_MII_SCHEMA_REVIEW.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/coverage-audit-2026-10-01/NSE_MII_SCHEMA_REVIEW.md)

Lines: 17. SHA256: `ba6dc3e757fd8810027ac8a5743341ad2f8d6d69173e5c4fab92999a84a6b338`.

- Line 1: # NSE MII schema review
- Line 5: The gzip expands to **120 named columns and 47,330 rows**, all exactly 120 cells. The existing `NSE_MII_SECURITY_FILE` parser was tested on the decompressed bytes and **correctly refused** the file: required logical `name` has no declared alias for `FinInstrmNm`. Its symbol/series aliases also lack `TckrSymb` / `SctySrs`. It expects an addressable NSE symbol or BSE code; this report provides NSE provisioned tokens and `$` symbols instead of actual BSE scrip codes.
- Line 7: No production parser was modified. `NSE_MII_OBSERVED_SCHEMA_V1.json` records the complete exact header contract. The audit reader refuses changed headers, ragged rows, duplicate headers and unknown meanings in instrument/delete/permitted/status enums. No column positions are guessed.
- Line 9: Explicit audit bindings: `FinInstrmNm`=name, `TckrSymb`=symbol, `SctySrs`=series, `ISIN`=checksum-validated ISIN. Equity candidates require `SctyTpFlg=0`, `DelFlg=N` and one of the existing parser's equity series EQ/BE/BZ/IQ/SM/ST; fund ISINs are kept separately. Unknown/special series are retained in a separate 43-ISIN reconciliation, including trusts. Different share-class ISINs remain separate, so the denominator is equity security identities rather than a proven unique legal-issuer count.
- Line 11: [NSE MSD67344](https://nsearchives.nseindia.com/content/circulars/MSD67344.pdf) defines `PrtdToTrad=2` and `$` symbols as BSE-exclusive. Symbols may be truncated, so they are never stripped into a fabricated BSE/Screener identity. `FinInstrmId` is a token, never a BSE scrip code. Exact ISIN joins use stored BSE codes/identity versions. The companion official NSE-only file has 37,928 tokens: its exact token difference from the combined file is 9,402 rows, all independently matching the BSE-exclusive flag and suffix.

## artifacts/document-acquisition/ac-final-01-20260817T052931Z/12_TRANSCRIPT_COVERAGE.md

Source: [artifacts/document-acquisition/ac-final-01-20260817T052931Z/12_TRANSCRIPT_COVERAGE.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/ac-final-01-20260817T052931Z/12_TRANSCRIPT_COVERAGE.md)

Lines: 38. SHA256: `eee27db9d20d44e01ca8479d5c94eb41d7b3898e42d1cf57669c50fc134fb11d`.

- Line 6: - OFFICIAL_TRANSCRIPT_NOT_OFFERED: 0
- Line 10: - UNKNOWN_REQUIRES_RECONCILIATION: 0
- Line 12: Recordings discovered: 6415
- Line 13: Recording fallback required: 42
- Line 14: Fallback not required: 5480

## artifacts/document-acquisition/ac-final-01-20260817T052931Z/13_RECORDING_FALLBACK_REPORT.md

Source: [artifacts/document-acquisition/ac-final-01-20260817T052931Z/13_RECORDING_FALLBACK_REPORT.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/ac-final-01-20260817T052931Z/13_RECORDING_FALLBACK_REPORT.md)

Lines: 8. SHA256: `4583525d958bd3859ed3825d6473b5661a4363c413502682a759036c7e94da65`.

- Design, reference, or supporting evidence; no matching status assertion.

## artifacts/document-acquisition/ac-final-01-20260817T052931Z/DOCUMENT_ACQUISITION_FINAL_GATE.md

Source: [artifacts/document-acquisition/ac-final-01-20260817T052931Z/DOCUMENT_ACQUISITION_FINAL_GATE.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/ac-final-01-20260817T052931Z/DOCUMENT_ACQUISITION_FINAL_GATE.md)

Lines: 57. SHA256: `42ce771c302e1f853616138f7988dd59d467b5798842c4d154ed498e550990da`.

- Line 3: DOCUMENT_ACQUISITION: **RED**
- Line 19: - no_unknown_called_recovered: PASS
- Line 29: - blocked_cra_requests_zero: FAIL
- Line 46: - transcript_unknown_reconciliation_count: 0
- Line 47: - recording_unknown_reconciliation_count: 0

## artifacts/document-acquisition/closure-20260816T095301Z/00-production-db-audit.md

Source: [artifacts/document-acquisition/closure-20260816T095301Z/00-production-db-audit.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/closure-20260816T095301Z/00-production-db-audit.md)

Lines: 14. SHA256: `9a8d0045bc2a121f607f24791d7ac88dbf5bea1d2f9274b15cb4b73431bd2426`.

- Line 9: - Acquisition-critical FK violations: 0. No legacy exchange rows were repaired.
- Line 10: - All 9,784 blocked CRA direct sources are marked `BLOCKED_TERMS`.

## artifacts/document-acquisition/closure-20260816T095301Z/02-recovery-dry-run.md

Source: [artifacts/document-acquisition/closure-20260816T095301Z/02-recovery-dry-run.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/closure-20260816T095301Z/02-recovery-dry-run.md)

Lines: 5. SHA256: `1b7e5fc6db61115f6fe5bdf6c63d282cb283723dbde740e67d050fdf7048570c`.

- Line 3: The confirmed production DB contains 6,954 blocked logical credit-rating artifacts: 4,544 CRISIL and 2,410 ICRA. Accounting reconciles exactly as 0 already satisfied + 6,954 targets + 0 quarantined.
- Line 5: The first dry-run exposed zero event dates because the D01 promoter preserved `document_links.period_label_raw` only in source evidence. Strict complete dates were available for 5,566 blocked artifacts with zero conflicts. After a tested, idempotent promoter backfill, event-date completeness is 5,566/6,954; the remaining yearless or malformed labels stay unresolved. Identity completeness is 6,839/6,954. Rating, outlook, instrument, and amount completeness are zero because blocked documents have no usable normalized content yet.

## artifacts/document-acquisition/closure-20260816T095301Z/04-p0-scope-report.md

Source: [artifacts/document-acquisition/closure-20260816T095301Z/04-p0-scope-report.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/closure-20260816T095301Z/04-p0-scope-report.md)

Lines: 19. SHA256: `b50901837817644f3755cf6ae9639b572cc9be1f72ec7c4db4ec90133ace0598`.

- Line 3: Status: **RED — NOT EXECUTED**
- Line 7: Creating labels from current exchange membership, company names, or sector names would fabricate `outcome_label` or `archetype` and violate the P0 gate. Therefore no P0 scope was registered, no queue was materialized, and `04-p0-manifest.json` is deliberately marked non-queueable.
- Line 9: Required input to resume: a governed company-ID-based Q4/Q1 decision-universe extract with `outcome_label` and approved `analysis_archetype`, or an already approved 30–50-company P0 membership. Once supplied, validate and dry-run it inside the production container:

## artifacts/document-acquisition/closure-20260816T095301Z/08-p0-extraction-report.md

Source: [artifacts/document-acquisition/closure-20260816T095301Z/08-p0-extraction-report.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/closure-20260816T095301Z/08-p0-extraction-report.md)

Lines: 5. SHA256: `0ca63c50a71822625cc3e81d20b91d8d5deb2c5976bc0f8bf94006a5515b298a`.

- Line 3: P0 extraction was **not executed** because no governed P0 scope could be built.
- Line 5: The production DA-06 canary nevertheless proved the real D02 → D03 handoff: three credit-rating PDFs were extracted successfully, spanning four pages (three native-text pages and one OCR page). One document classified as `RATING_ACTION_INTIMATION` and recovered its target. Two classified as `UNKNOWN_RATING_DOCUMENT`; both remained unrecovered with zero coverage and became available to remaining source groups.

## artifacts/document-acquisition/closure-20260816T095301Z/10-storage-projection.md

Source: [artifacts/document-acquisition/closure-20260816T095301Z/10-storage-projection.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/closure-20260816T095301Z/10-storage-projection.md)

Lines: 5. SHA256: `5bda9765c954945f20fdc1da3b2c977a1576b56a9f81c92373ca5ce2f97c5857`.

- Line 3: Status: **NOT EXECUTED**. There is no P0 scope from which to measure type-stratified P0 sizes or project P1 without guessing.
- Line 5: At the stop boundary the external store had 994,610,155,520 total bytes and 563,483,934,720 free bytes. The configured operational reserve was 149,191,523,328 bytes, leaving 414,292,411,392 bytes usable after reserve. The three validated DA-06 canary PDFs occupied 2,648,484 unique bytes.

## artifacts/document-acquisition/closure-20260816T095301Z/11-recovery-production-report.md

Source: [artifacts/document-acquisition/closure-20260816T095301Z/11-recovery-production-report.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/closure-20260816T095301Z/11-recovery-production-report.md)

Lines: 5. SHA256: `d9bf191c9443888dc199dbbb0c0bfba525ad2cfb3e05e79e2e429a5a062c6aa3`.

- Line 3: The deterministic dry-run and a 30-target final canary completed. The production scale run was **not started**, preserving the required canary-before-scale and P0-before-scale order.
- Line 5: At the stop boundary: 6,935 targets were pending, 18 ambiguous, and one recovered. There were five HIGH and twenty AMBIGUOUS candidate rows across all three canary iterations. Evidence classifications comprised one rating-action intimation and two unknown documents; unknowns were not counted as recovered.

## artifacts/document-acquisition/closure-20260816T095301Z/13-p1-coverage.md

Source: [artifacts/document-acquisition/closure-20260816T095301Z/13-p1-coverage.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/closure-20260816T095301Z/13-p1-coverage.md)

Lines: 3. SHA256: `f626ce669011412adb1d0b10c42ddfc3c22dacf89cf1a85b715918c33ff9b0a4`.

- Line 3: Status: **NOT EXECUTED**. P0 did not reach GREEN, and the governed Q4/Q1 decision-universe input is absent. No Parquet coverage file was created because an empty or synthetic table would not be real corpus coverage.

## artifacts/document-acquisition/closure-20260816T095301Z/14-transcript-gap-report.md

Source: [artifacts/document-acquisition/closure-20260816T095301Z/14-transcript-gap-report.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/closure-20260816T095301Z/14-transcript-gap-report.md)

Lines: 3. SHA256: `eddce81567739b7febc9ce8c45a6be4503c034f869c58c6505da056177c25302`.

- Line 3: Status: **NOT EXECUTED**. No recordings were queued or downloaded. Transcript gaps can only be measured after a governed P1 core corpus exists.

## artifacts/document-acquisition/closure-20260816T095301Z/FINAL_GATE_REPORT.md

Source: [artifacts/document-acquisition/closure-20260816T095301Z/FINAL_GATE_REPORT.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/closure-20260816T095301Z/FINAL_GATE_REPORT.md)

Lines: 244. SHA256: `0cdf606a2933927a2e5c405c1aeefd24a4a5431b53bc0296fdc07a43774e8b54`.

- Line 24: `PRAGMA integrity_check` returned `ok`. `PRAGMA foreign_key_check` returned 10,771 legacy violations: 5,395 in `exchange_screener_enqueues` and 5,376 in `screener_company_queue_events`, both referencing `screener_company_queue`. Acquisition-critical violation count was zero. These rows were not repaired or changed.
- Line 36: - direct/redirect/browser blocked-host controls.
- Line 46: LIVE VALIDATION REQUIRED:
- Line 62: - `scrapper/tests/test_credit_rating_acquisition.py`
- Line 76: No direct CRA source was enabled or fetched. The rebuilt long-running generic worker was restored on its original `accepted-screener-concall` scope; it cannot claim `da06-rating-recovery` or a P0 scope.

## artifacts/document-acquisition/final-closure-20260816T113743Z/00_ENVIRONMENT_AUDIT.md

Source: [artifacts/document-acquisition/final-closure-20260816T113743Z/00_ENVIRONMENT_AUDIT.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-closure-20260816T113743Z/00_ENVIRONMENT_AUDIT.md)

Lines: 21. SHA256: `27773b3b9bd099488cdf7190bee87941405e874a8cce4de71534ee644e336d54`.

- Line 10: - Acquisition-table FK violations: **0**. No legacy exchange rows were repaired.
- Line 18: - Configured reserve: 149,191,523,328 bytes (the larger of 100 GiB and 15%).

## artifacts/document-acquisition/final-closure-20260816T113743Z/01_REGRESSION_TEST_REPORT.md

Source: [artifacts/document-acquisition/final-closure-20260816T113743Z/01_REGRESSION_TEST_REPORT.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-closure-20260816T113743Z/01_REGRESSION_TEST_REPORT.md)

Lines: 48. SHA256: `b21f38a6111cc2b8947593fee3ef94a16aef3d6778913b783cd120e19c5fffad`.

- Line 13: The focused set covered D01 promotion/discovery, D02, D03, rating normalization/acquisition, durable scopes and leases, recovery, DA-06 contracts/registry/adapters/matching/attachment/lifecycle, D09, external-store behavior, duplicate reuse, redirects and blocked hosts, and recording policy.
- Line 46: targeted D03/DA-06/closure run passed 33/33, including credit-rating HTML,
- Line 47: direct evidence classification, unknown-evidence behavior, recovery lifecycle,

## artifacts/document-acquisition/final-closure-20260816T113743Z/02_ACQUISITION_P0_REPORT.md

Source: [artifacts/document-acquisition/final-closure-20260816T113743Z/02_ACQUISITION_P0_REPORT.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-closure-20260816T113743Z/02_ACQUISITION_P0_REPORT.md)

Lines: 75. SHA256: `0bc833c1304c8d4141ca5edb745eaa23b3015ccbd761656cb92df39cdbbb20f2`.

- Line 11: - credit_rating: 41
- Line 17: - artifact:credit_rating: 41
- Line 22: - edge:unknown_rating_evidence: 2
- Line 27: - shape:redirect: 26
- Line 30: - source:blocked_cra_original: 35

## artifacts/document-acquisition/final-closure-20260816T113743Z/03_EXTERNAL_STORE_FAIL_CLOSED_REPORT.md

Source: [artifacts/document-acquisition/final-closure-20260816T113743Z/03_EXTERNAL_STORE_FAIL_CLOSED_REPORT.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-closure-20260816T113743Z/03_EXTERNAL_STORE_FAIL_CLOSED_REPORT.md)

Lines: 14. SHA256: `e2d2c5b4d5fdeda1df50e0775927f09d3f75977f7e1a4e1392239be10cc1083a`.

- Line 3: Gate: **FAIL_CLOSED_EXTERNAL_STORE = PASS**
- Line 5: A controlled temporary document-store mount was supplied without the required sentinel. Worker startup failed before network dispatch or content persistence with a missing-sentinel error.
- Line 12: - Normal `/documents` configuration restored: yes

## artifacts/document-acquisition/final-closure-20260816T113743Z/05_P0_RESTART_REPORT.md

Source: [artifacts/document-acquisition/final-closure-20260816T113743Z/05_P0_RESTART_REPORT.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-closure-20260816T113743Z/05_P0_RESTART_REPORT.md)

Lines: 19. SHA256: `f4144852dc6fd976b8f7b036eee7d67f43b43de67526d6f79ff2bdf7f722a922`.

- Line 3: Gate: **DOWNLOAD_RESUMPTION = PASS**

## artifacts/document-acquisition/final-closure-20260816T113743Z/07_FULL_RATING_EXTRACTION_PROGRESS.md

Source: [artifacts/document-acquisition/final-closure-20260816T113743Z/07_FULL_RATING_EXTRACTION_PROGRESS.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-closure-20260816T113743Z/07_FULL_RATING_EXTRACTION_PROGRESS.md)

Lines: 47. SHA256: `dd5a562ff4e53d27a9970507ebe5ebea2c73f6a0e2103bfdf01f5e03f72dddbe`.

- Line 9: - partial: 0
- Line 21: - partial: 0
- Line 29: ## credit_rating
- Line 33: - partial: 0
- Line 47: - UNKNOWN_RATING_DOCUMENT: 74

## artifacts/document-acquisition/final-closure-20260816T113743Z/07_P0_EXTRACTION_REPORT.md

Source: [artifacts/document-acquisition/final-closure-20260816T113743Z/07_P0_EXTRACTION_REPORT.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-closure-20260816T113743Z/07_P0_EXTRACTION_REPORT.md)

Lines: 45. SHA256: `d997dd42a6183b2de106aeec80131b4d99a18907e5d730dbf73194077bce3406`.

- Line 9: - partial: 0
- Line 21: - partial: 0
- Line 29: ## credit_rating
- Line 33: - partial: 0
- Line 45: - UNKNOWN_RATING_DOCUMENT: 2

## artifacts/document-acquisition/final-closure-20260816T113743Z/08_P0_STORAGE_REPORT.md

Source: [artifacts/document-acquisition/final-closure-20260816T113743Z/08_P0_STORAGE_REPORT.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-closure-20260816T113743Z/08_P0_STORAGE_REPORT.md)

Lines: 63. SHA256: `afcc8f7d0b82b84ec36548dff02f286f8d5e523dbcc49b5223f57df530d60139`.

- Line 25: ### credit_rating

## artifacts/document-acquisition/final-closure-20260816T113743Z/09_FULL_ACQUISITION_SCOPE.md

Source: [artifacts/document-acquisition/final-closure-20260816T113743Z/09_FULL_ACQUISITION_SCOPE.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-closure-20260816T113743Z/09_FULL_ACQUISITION_SCOPE.md)

Lines: 29. SHA256: `258047ca505696756785d5ffb87d5bfc960d509f99bd3840be8fecb1034737f2`.

- Line 10: - credit_rating: 13424
- Line 16: - ALREADY_ACQUIRED: 24843
- Line 18: - POLICY_BLOCKED_DIRECT: 5
- Line 19: - RECOVERY_REQUIRED: 6028
- Line 25: - focus_governance_fields_required: False

## artifacts/document-acquisition/final-ops/DA_FINAL_OPS_STATUS.md

Source: [artifacts/document-acquisition/final-ops/DA_FINAL_OPS_STATUS.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-ops/DA_FINAL_OPS_STATUS.md)

Lines: 139. SHA256: `7dcef826c2b105767b060f6414d321f8b9e871f8915ca0521ce5727d8d25db11`.

- Line 1: # DA-FINAL-OPS — status
- Line 6: final reports and the machine gate remain correctly deferred while real download,
- Line 59: 924 automated requests reached blocked CRA hosts (922 `www.crisil.com`,
- Line 90: headroom, so the run now fits inside its configured reserve.
- Line 94: Measured as the runner selects work — one preferred content per artifact:

## artifacts/document-acquisition/final-ops/RETRYABLE_FAILURE_DIAGNOSIS.md

Source: [artifacts/document-acquisition/final-ops/RETRYABLE_FAILURE_DIAGNOSIS.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-ops/RETRYABLE_FAILURE_DIAGNOSIS.md)

Lines: 76. SHA256: `406ea752581a18371af9d68160d375632ee999d83cf618a61a5ac059bfbe74d7`.

- Line 14: - Code change required: **False**

## artifacts/document-acquisition/final-ops/d03-progress/D03_PROGRESS_da06-rating-recovery.md

Source: [artifacts/document-acquisition/final-ops/d03-progress/D03_PROGRESS_da06-rating-recovery.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-ops/d03-progress/D03_PROGRESS_da06-rating-recovery.md)

Lines: 47. SHA256: `84663124aa400c7bac97e60d4fc9ca25b796b7973cfa6e938e6dee314d8212c8`.

- Line 9: - partial: 0
- Line 21: - partial: 0
- Line 29: ## credit_rating
- Line 33: - partial: 0
- Line 47: - UNKNOWN_RATING_DOCUMENT: 367

## artifacts/document-acquisition/final-ops/d03-progress/D03_PROGRESS_document-acquisition-full-v1.md

Source: [artifacts/document-acquisition/final-ops/d03-progress/D03_PROGRESS_document-acquisition-full-v1.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-ops/d03-progress/D03_PROGRESS_document-acquisition-full-v1.md)

Lines: 47. SHA256: `7449e53bd0e43cfb4c65b07e9af632e040dfba6c498050f4675b947ee51a78d2`.

- Line 9: - partial: 0
- Line 21: - partial: 6
- Line 29: ## credit_rating
- Line 33: - partial: 0
- Line 47: - UNKNOWN_RATING_DOCUMENT: 708

## artifacts/document-acquisition/final-ops/d03-smoke/D03_SMOKE.md

Source: [artifacts/document-acquisition/final-ops/d03-smoke/D03_SMOKE.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/final-ops/d03-smoke/D03_SMOKE.md)

Lines: 47. SHA256: `d78f3e4af018167eb8395abd74668cb80b5fa4996791e7568ecbefd791091294`.

- Line 9: - partial: 0
- Line 21: - partial: 0
- Line 29: ## credit_rating
- Line 33: - partial: 0
- Line 47: - UNKNOWN_RATING_DOCUMENT: 15

## artifacts/document-acquisition/p0/P0_SELECTION_REPORT.md

Source: [artifacts/document-acquisition/p0/P0_SELECTION_REPORT.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/document-acquisition/p0/P0_SELECTION_REPORT.md)

Lines: 44. SHA256: `9a55a2613fdb117ef150a1562439c1f69f5f2b132b35c3cef69d73a316703a14`.

- Line 11: - credit_rating: 41
- Line 17: - artifact:credit_rating: 41
- Line 22: - edge:unknown_rating_evidence: 2
- Line 27: - shape:redirect: 26
- Line 30: - source:blocked_cra_original: 35

## artifacts/focus_ml/governance/FG00_SOURCE_MAP.md

Source: [artifacts/focus_ml/governance/FG00_SOURCE_MAP.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/focus_ml/governance/FG00_SOURCE_MAP.md)

Lines: 50. SHA256: `6cc787bbd33bb5115e408c9c5d0475b23f83955416f7ffb5745608f8226ab0cd`.

- Line 16: Accordingly the preliminary universe diagnosis is `PARTIAL_UNIVERSE_AVAILABLE` (case C). The production exchange data can support identity, listing-date, instrument, and result-availability evidence, but snapshots begin on 2026-08-06 and cannot reconstruct the mentor’s earlier selection boundary.
- Line 18: ## Required fields
- Line 46: - The 400 `rejected_candidates.csv` rows remain `decision_universe_state=UNKNOWN`, `outcome_label=NULL` until approval.
- Line 50: The machine-readable source map contains source hashes and the full field-level status record.

## artifacts/focus_ml/governance/FG00_UNIVERSE_DIAGNOSIS.md

Source: [artifacts/focus_ml/governance/FG00_UNIVERSE_DIAGNOSIS.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/focus_ml/governance/FG00_UNIVERSE_DIAGNOSIS.md)

Lines: 5. SHA256: `e09c14e831e9e12ac707d3a4fac9e44678632cb772e8545df67ebfbaebb6799d`.

- Line 3: Classification: **C. PARTIAL_UNIVERSE_AVAILABLE**.

## artifacts/focus_ml/governance/FOCUS_ML_GOVERNANCE_PARKED.md

Source: [artifacts/focus_ml/governance/FOCUS_ML_GOVERNANCE_PARKED.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/focus_ml/governance/FOCUS_ML_GOVERNANCE_PARKED.md)

Lines: 5. SHA256: `0cec4c3b53cc987a388779d873cfe714dc096cd15f1861ce8258a300b09817f0`.

- Design, reference, or supporting evidence; no matching status assertion.

## artifacts/focus_ml/governance/P0_GOVERNANCE_GATE_REPORT.md

Source: [artifacts/focus_ml/governance/P0_GOVERNANCE_GATE_REPORT.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/focus_ml/governance/P0_GOVERNANCE_GATE_REPORT.md)

Lines: 63. SHA256: `9d62f747555cb60a420e4cd14a03c479adcb10221f513fc07cea2f1a1f056eb8`.

- Line 17: state: PARTIAL
- Line 19: Q4: positive=236, negative=0, excluded_identity_unresolved=3, unknown=0, accounting_total=239
- Line 20: Q1: positive=190, negative=0, excluded=1, unknown=30, accounting_total=221
- Line 25: provisional/review/unknown: 50 targeted rows
- Line 43: - PASS: unknown_universe_has_null_label

## artifacts/focus_ml/governance/P0_GOVERNANCE_REPORT_v001.md

Source: [artifacts/focus_ml/governance/P0_GOVERNANCE_REPORT_v001.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/focus_ml/governance/P0_GOVERNANCE_REPORT_v001.md)

Lines: 14. SHA256: `21bb2b12b46d7d8bd06b956ac639d047c61da6a95352aa6a93411b60ab59183f`.

- Line 5: The source diagnosis is `PARTIAL_UNIVERSE_AVAILABLE`. Positive labels are frozen. Unapproved negatives remain UNKNOWN/NULL and the manifest is intentionally non-queueable.

## artifacts/focus_ml/governance/P0_NEGATIVE_APPROVAL_REQUIRED.md

Source: [artifacts/focus_ml/governance/P0_NEGATIVE_APPROVAL_REQUIRED.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/focus_ml/governance/P0_NEGATIVE_APPROVAL_REQUIRED.md)

Lines: 3. SHA256: `5cbaeb128b31ce6178501a9055d7b520a26bf61af4c080dac04d5f7bc12926e3`.

- Line 1: # P0 Negative Approval Required
- Line 3: The Q1 rejected-candidate file is contemporaneous supporting evidence, not an authoritative mentor decision universe. Every candidate remains `UNKNOWN` with `outcome_label=NULL`. Review only the targeted rows in `review/DECISION_UNIVERSE_APPROVAL_REQUIRED.csv`; approve at least 20 well-evidenced rows to support the intended balanced 40-company pilot.

## artifacts/focus_ml/governance/review/APPROVAL_INSTRUCTIONS.md

Source: [artifacts/focus_ml/governance/review/APPROVAL_INSTRUCTIONS.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/focus_ml/governance/review/APPROVAL_INSTRUCTIONS.md)

Lines: 18. SHA256: `a45a5f8003b2b3fb5a14c361632436a4139111c7772b6e9237772761db7b9411`.

- Line 5: Edit only `approve_as_decision_universe_member` in `DECISION_UNIVERSE_APPROVAL_REQUIRED.csv`. Allowed values are `YES` or `NO`; add optional notes. `YES` means the company genuinely belonged to the mentor's Q1 decision universe at the cutoff, not merely that it was absent from Focus. Leave uncertain rows blank.
- Line 9: For each required company, set `approve=YES` to approve the proposed archetype, or put one allowed value in `override_archetype` and explain it in notes. Allowed archetypes: OPERATING_COMPANY, PROJECT_EPC, MANUFACTURING, BANK, NBFC, INSURER, IT_SERVICES, PHARMA, CONSUMER, OTHER. Use `approve=NO` when the row should not enter P0.
- Line 14: docker compose run --rm -T --entrypoint python -v "$PWD:/workspace" -w /workspace daemon -m scrapper.pipeline.build_p0_governance --db /data/scraper.db --repository-root /workspace --output-dir /workspace/artifacts/focus_ml/governance --q4-ocr-evidence /workspace/artifacts/focus_ml/governance/Q4_OCR_EVIDENCE_v001.json --decision-approvals /workspace/artifacts/focus_ml/governance/review/DECISION_UNIVERSE_APPROVAL_REQUIRED.csv --archetype-approvals /workspace/artifacts/focus_ml/governance/review/ARCHETYPE_APPROVAL_REQUIRED.csv --created-at-utc 2026-08-16T11:00:00Z

## artifacts/icra-download-test/README.md

Source: [artifacts/icra-download-test/README.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/icra-download-test/README.md)

Lines: 54. SHA256: `5c3f7b244368701eda71372a216479c91515a16d80d7ebcc0b4e0519c20582af`.

- Line 11: is retained separately. Wrong-company reports are withheld for review.
- Line 28: The finite CRISIL, other-agency, and extraction workers are configured in
- Line 51: There is intentionally no headless ICRA worker running: the publisher blocked

## artifacts/rating-exception-recovery-20261001/report.md

Source: [artifacts/rating-exception-recovery-20261001/report.md](/Users/mohamedfadhi/Fadhi/trading-ai/artifacts/rating-exception-recovery-20261001/report.md)

Lines: 16. SHA256: `b574a52c3404cbf9c00d124f64b6690f8bab8ae6ccd05a8561c43e0b5863a6d3`.

- Line 1: # Credit-rating recovery — 1 October 2026
- Line 3: Recovered and saved 138 of the 140 audited ICRA and CRISIL exception jobs: all 28 CRISIL reports and 110 ICRA reports.
- Line 10: An additional CRISIL report discovered during continued downloading is also unavailable: Shriram Properties, 30 July 2024. Both CRISIL domains return “File not found.” The issuer archive's October 2025 notice is a different report and was not substituted.

## experiments/upstox_fundamentals_pilot/00_access_and_scope.md

Source: [experiments/upstox_fundamentals_pilot/00_access_and_scope.md](/Users/mohamedfadhi/Fadhi/trading-ai/experiments/upstox_fundamentals_pilot/00_access_and_scope.md)

Lines: 52. SHA256: `997e7acf5a1e51ad6abd5095a5e5df42d851cf1afc687c8db3346d5735c433fc`.

- Line 4: **Status:** BLOCKED before live API testing
- Line 31: - Analytics Tokens are read-only, valid for one year, free, and support Fundamentals without static-IP allowlisting; the documentation does not establish the project's retention, redistribution, automated-volume, or private-ML permissions.
- Line 44: - Raw licensed responses and generated outputs are gitignored.
- Line 45: - No token, bearer header, account credential, or raw provider response was written to disk.
- Line 47: ## Required owner action to unblock U2

## experiments/upstox_fundamentals_pilot/BLOCKED_STATUS.md

Source: [experiments/upstox_fundamentals_pilot/BLOCKED_STATUS.md](/Users/mohamedfadhi/Fadhi/trading-ai/experiments/upstox_fundamentals_pilot/BLOCKED_STATUS.md)

Lines: 8. SHA256: `baebdf14ac7324e4f1d620eaa48961779cf986bc201b18e47fa8ad5621be9499`.

- Line 1: # Upstox Fundamentals Pilot — Blocked Status
- Line 5: The pilot cannot proceed to live testing because the required `UPSTOX_ANALYTICS_TOKEN` is not present and account/contract permissions for automated requests, response retention, and private ML/research use are not confirmed.

## scrapper/EXECUTION.md

Source: [scrapper/EXECUTION.md](/Users/mohamedfadhi/Fadhi/trading-ai/scrapper/EXECUTION.md)

Lines: 1351. SHA256: `75f0e569879e40a8bea09a4c3829c4e63b86e3961553b2aca9da93b734f3459f`.

- Line 66: `growth_observations`. Stock Price CAGR `10 Years` is a bare `%`: stored
- Line 80: Exit: every visible cell is exported and the balance validation is stored.
- Line 87: schedule dataset stays a deferred component. Migration `005` adds
- Line 92: fires without blocking acceptance (`validation_status='passed_with_warnings'`).
- Line 100: - Validate required rows without inventing financial identities not shown by the

## scrapper/PLAN.md

Source: [scrapper/PLAN.md](/Users/mohamedfadhi/Fadhi/trading-ai/scrapper/PLAN.md)

Lines: 1354. SHA256: `2c4b09b897ec56c4a01b098db20f026f524c95b0855a99ed048dc76b02ad649a`.

- Line 18: Phase 1 has one job: collect structured source data completely, preserve what the
- Line 33: Phase 2 follows the links discovered in Phase 1 and independently checks primary
- Line 34: sources to collect investor presentations, credit-rating reports, earnings-call
- Line 36: structured-data source; it is not the only document source and is never treated
- Line 77: data comes from, and one explicit status: `capture`, `metadata_only`,

## scrapper/README.md

Source: [scrapper/README.md](/Users/mohamedfadhi/Fadhi/trading-ai/scrapper/README.md)

Lines: 596. SHA256: `e1324f38f4436903b71d0c71d7d65efeba371c3fc37e2dd54f259883e2f9f1fe`.

- Line 14: frozen. Do not issue new Trendlyne requests or expand/redistribute those fixtures.
- Line 16: Phase 2 D01 discovers investor presentations, credit-rating reports,
- Line 21: whose response status, declared media type, byte signature and logical artifact
- Line 22: type must agree before immutable content is registered. D03 extracts native PDF
- Line 23: pages and PPT/PPTX slide structure, preserving ordered text blocks, tables,

## scrapper/SCRAPPER_2.0.md

Source: [scrapper/SCRAPPER_2.0.md](/Users/mohamedfadhi/Fadhi/trading-ai/scrapper/SCRAPPER_2.0.md)

Lines: 183. SHA256: `9adb2fb44647ebf5d60cf846d9b8719628f490d12426ab6d6ab8f8251dc98b7d`.

- Line 3: _Status: future exploration; explicitly not in the current implementation scope._
- Line 23: As of this document's review date, both exchanges advertise official corporate
- Line 24: data products. BSE describes API-delivered corporate financials, disclosures,
- Line 27: delivery options. Product access, pricing, licensing, redistribution rights and
- Line 36: storage, processing and redistribution.

## scrapper/research/amfi_amc_feasibility.md

Source: [scrapper/research/amfi_amc_feasibility.md](/Users/mohamedfadhi/Fadhi/trading-ai/scrapper/research/amfi_amc_feasibility.md)

Lines: 180. SHA256: `0ec5ed567463e2e8056fec7d85fa331ff00f1c3e8c701799261729b7712f19f0`.

- Line 44: file ingestion for model use is blocked unless the intended use is confirmed to
- Line 46: notice and file-level notices require an independent review; the fact that a
- Line 75: entered  = prior absent/zero and current > 0
- Line 84: `unknown`, not a zero position. Corporate actions, ISIN replacement, mergers,
- Line 102: - AMC indexes may be server-rendered, client-rendered, API-backed, ZIP archives,

## scrapper/research/amfi_amc_mapping_architecture.md

Source: [scrapper/research/amfi_amc_mapping_architecture.md](/Users/mohamedfadhi/Fadhi/trading-ai/scrapper/research/amfi_amc_mapping_architecture.md)

Lines: 221. SHA256: `f6397aa028d3d9328a6b51f6a3f4e974df905f47c5e60a540f58bf45b2bbe965`.

- Line 43:   fund_id PK, legal_name, status
- Line 48:   valid_from, valid_to, mapping_status, evidence_parse_id
- Line 52:   scheme_id PK, fund_id, canonical_name, status
- Line 57:   valid_from, valid_to, mapping_status, evidence_parse_id
- Line 81:   mapped_scheme_id, mapping_status, header_row_position,

