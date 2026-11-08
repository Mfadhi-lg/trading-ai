# Trading AI acquisition black-box audit — 2026-10-07

**Audit cut-off:** 2026-10-07 09:09:40 UTC (14:39:40 IST)  
**Scope:** read-only production acquisition, document, release and worker truth.  
**Production database snapshot:** `/source/scraper.db` copied to `/private/tmp/trading-ai-audit-2026-10-07/scraper.db`; SQLite opened only on the disposable copy using URI `mode=ro` and `PRAGMA query_only=ON`.

## How to read the findings

- **SOURCE_CAPABILITY** means what the current canonical checkout implements or tests. It does not establish that a running image contains or executes that code.
- **LIVE_DEPLOYED_STATE** means the Docker containers, image/config metadata, mounts, logs and release identity actually observed. At the cut-off there were no running Trading AI containers.
- **LIVE_DATA_COVERAGE** means records and files in the copied production database and bounded reads from the configured document store. Empty or absent evidence is reported as unknown where appropriate; it is not treated as proof that an issuer did not publish a document.

The database copy passed `integrity_check` and `quick_check`. The production volume and document store were never mounted writable. No database helper, initializer, migration, queue command, worker, publisher request, external fetch, document download or reprocessing was run. No source code was edited. Tests were not run.

## Black-box verdict

| Question | Finding |
|---|---|
| `LATEST_CLEANED_SOURCE_ACTUALLY_DEPLOYED` | **NO** |
| `AUTOMATIC_RESULT_DISCOVERY_HEALTH` | **BLOCKED** — all runtime containers are stopped; BSE result/master access is held after stored HTTP 403s; the latest NSE results attempt failed its snapshot guard. |
| `NEW_DOCUMENT_AUTOMATIC_EXTRACTION` | **NO** in the observed production state. The production schema has no processing inbox/work tables, and no processing worker container exists. Current source materializes work only for explicitly selected content/version; the Compose role is period/limit scoped. |
| **Production DB safe copy** | Integrity `ok`; quick check `ok`; source/copy SHA256 match; no `scraper.db-wal` or `scraper.db-shm` present; WAL recovery was not required. |
| **Document store** | Existing external-disk directory mounted by stopped document workers as a host bind at `/documents`; current path is `/Volumes/My Passport/passport/trading-ai`. |

The main live facts are that the last production containers used the older `c05.3` image and all exited on 2026-10-04, while canonical source includes C28.1. In the copy, the last accepted result period is June 2026; period-labeled presentation/transcript discovery remains incomplete, 44,434 downloaded supported documents have `extraction_status=not_started`, and production has no transcript segmentation records.

## A. Release and runtime truth

### SOURCE_CAPABILITY

Canonical `HEAD` is `0e6ee264931ba1a1e009e0785cf188f5dc9f37e8` on `chore/root-hygiene-pr`. The C28.1 integration commit is `c3ddbbb9c298d07b8b46a362996251417282476a` (tree `eeb21f032e5bb4119279bc891c7342fa99eae7a1`). Its recorded 1,097-file source projection SHA256 is `17168eabb7a1236f8855a5235204f10b138f1da4e8a346057ca019105d6f2302`. C28.1 records production activation as `none`; the later C05.3 release manifest likewise says `reproduced_not_activated`.

At audit completion, Git status showed one tracked deletion (`docs/current/TRADING_AI_MASTER_RESEARCH_AND_DOCUMENT_INTELLIGENCE_PLAN.md`) and untracked cleanup/evidence files, including the preexisting C11–C28 harness artifacts. The audit adds the six requested files under `docs/audits/`. No tracked application source file was modified by this audit. The tracked deletion is reported as observed status; this audit did not restore or edit it.

### LIVE_DEPLOYED_STATE

`docker ps` showed **zero running containers** for Trading AI. Fourteen Compose-project containers were stopped: eleven long-running core roles and three completed browser-recovery runs. Core containers exited with code 137 at about `2026-10-04T18:06:55Z`; the browser runs exited 0 on Oct 1–2. Docker retained health metadata `unhealthy` for exited containers, which is last health state, not a current health probe. There is no current uptime or heartbeat.

All eleven long-running roles used image reference `trading-ai-c053-release@sha256:44da7074157fd2a946c41d064a91ef3f729ebd31ab3bcdf2aa09233c5a794b6b`; image config ID was `sha256:05cf18b61418ace4f5e490ad59e62feb77de43558788fd47ca594c17d60cba77`. Runtime log identity names release `c05.3-release-20261003` and Git commit `db7564a109f1cb34ee4e7d5ea3427716e14cb51e`. The C05.3 manifest itself lists that older baseline commit and marks the reproduced release “not activated.” It is not the C28.1 source projection.

The stopped core containers used `/data/scraper.db` from named volume `trading-ai_scraper-data`. Stopped document roles bound `/Volumes/My Passport/passport/trading-ai` to `/documents`; most were read/write, while the June extraction container’s document bind was read-only. No core container had an application-source bind mount. Three stopped browser containers had a read-only `/Users/mohamedfadhi/Fadhi/trading-ai/scrapper:/app/scrapper` bind, but they were one-off completed runs and are not active.

Current Compose defines default core roles, optional `manual`/`manual-compat` services, and optional `browser-recovery` profile services. None is running. The Compose `document-processing-worker` has **no corresponding container**. The exact service/container comparison is in [runtime_roles.csv](acquisition-blackbox/runtime_roles.csv).

**Explicit release verdict:** `LATEST_CLEANED_SOURCE_ACTUALLY_DEPLOYED: NO`.

## B. Financial result discovery

### SOURCE_CAPABILITY

Canonical Compose defines a continuously restarted `exchange-scheduler` with a retry ladder and weekly reconciliation. It also defines the result-feed and security-master acquisition paths. This capability is not evidence of a current successful run.

### LIVE_DEPLOYED_STATE

The scheduler container is stopped. Runtime logs show a result attempt for scheduled date Oct 4 ending `access_blocked`; no subsequent scheduler records exist for Oct 5–7. The stopped daemon also had no due Screener work in its last logged cycles on Oct 4.

### LIVE_DATA_COVERAGE

| Exchange/feed | Latest successful master | Latest successful result feed | Stored result watermark | Latest observed accepted/result period | Current stored blocker |
|---|---:|---:|---|---|---|
| NSE | `nse_equity_master` 2026-10-03 14:04:42Z; SME master also changed Oct 3 | `nse_financial_results` 2026-10-03 14:04:45Z | Last broadcast `2026-10-01T14:45:12Z`; checkpoint updated Oct 2 | Accepted company state: 2026-06; 2026-09 feed attempts fail validation | Latest Oct 4 scheduled attempt: `guard_failed`, `snapshot rejected: ['row_count_floor', 'future_effective_date']`; earlier Oct 2/3 failures also include `row_count_floor` |
| BSE | `bse_security_master` 2026-09-23 03:41:32Z | `bse_financial_results` 2026-09-16 03:56:01Z | Last broadcast `2026-09-15T10:32:20Z`; checkpoint updated Sep 16 | Latest successful period: 2026-06 | `exchange_source_access_events`: master and results `BLOCKED` since Sep 29; stored fetch error is HTTP 403 |

For BSE, the last recorded results/master schedule attempt is Oct 2 and failed with stored `status=403 classification=rejected`. NSE’s Oct 4 attempt recorded a guard failure. No successful Sep-2026 result period is in accepted company state. `exchange_schedule_runs` has no scheduled dates after Oct 4, so Oct 5–7 are missed scheduler dates; BSE has no successful refresh since Sep 23 (master) / Sep 16 (results).

The result-driven Screener queue has 842 pending rows overall. Of these, 838 carry `collection_disposition=below_market_cap` and are deferred by scope; four pending rows have no such disposition. There are 3,273 terminal Screener failures overall (2,810 marked below-cap and 463 not marked below-cap), 6,332 accepted rows and 2,302 already-accepted skips. For 2026-06 and 2026-03, the stored result-period queue has 39 and 5 pending standalone items respectively, but the current queue’s scope disposition must be read alongside those counts.

**`AUTOMATIC_RESULT_DISCOVERY_HEALTH: BLOCKED`**. Code supports automatic schedules, but no scheduler is running. BSE feeds are held by stored access blocks and NSE’s latest feed is guard-rejected. The Oct 4 data log and Oct 7 audit date establish missed runs; external publication activity after the last stored snapshot is **NOT_PROVEN** because this audit made no network requests.

## C. ₹580 crore collection scope

### SOURCE_CAPABILITY

`SCREENER_MIN_MARKET_CAP_CRORE` defaults to `580` in [config.py](/Users/mohamedfadhi/Fadhi/trading-ai/scrapper/config.py:164). `record_scope` sets `below_floor` only when `cap < floor`; equality is therefore eligible in [collection_scope_repo.py](/Users/mohamedfadhi/Fadhi/trading-ai/scrapper/db/collection_scope_repo.py:28). The test `test_exclusion_blocks_claim_and_rising_cap_requeues` supplies exactly `Decimal('580')`, expects `eligible`, and verifies the due company can be claimed in [test_collection_scope.py](/Users/mohamedfadhi/Fadhi/trading-ai/scrapper/tests/test_collection_scope.py:31). This proves code/test behavior, not production re-entry.

### LIVE_DEPLOYED_STATE

The hourly `market-cap-monitor` container is stopped. No cap-monitor heartbeat has occurred since Oct 4.

### LIVE_DATA_COVERAGE

The production scope floor is `580` crore for all 5,490 current scope rows:

- Eligible: 1,887
- Below floor: 3,552
- Unknown/unobserved cap: 51
- Next check overdue at the audit cut-off: 3,529 (1,172 eligible, 2,306 below floor, 51 unknown)
- Scope observations older than seven days: the same 3,529 rows
- Eligible companies with no accepted Screener queue history: 92; eligible companies with no queue history at all: 15
- Recorded `below_floor` → `eligible` transitions in `screener_scope_observations`: 0
- Currently eligible companies with document jobs still marked `SCOPE_BELOW_MARKET_CAP_FLOOR`: 0
- Still below-floor: 392 companies have 4,618 document jobs with that scope-hold result

The database therefore does **not** show a production below-floor re-entry/requeue event. It shows the code path and a boundary test, but the live evidence has no observed transition, no observed requeue attributable to a transition, and a stopped monitor. A crossing by a company that has not been observed is **NOT_PROVEN**. `company_scope.csv` has all 5,490 companies, the latest and prior relevant observations, queue disposition, and row-level anomalies.

## D. Presentation, transcript and recording coverage

### Status mapping used

The production schema has several separate status fields rather than one universal lifecycle enum. The CSV preserves the stored fields and uses this map:

| Requested concept | Stored field(s) used | Interpretation |
|---|---|---|
| UNKNOWN | `artifact_expectations.availability_status='source_not_covered'`; missing period/artifact evidence | No covered source evidence. Not proof of non-issuance. |
| NOT_ISSUED | No exact production status | Never inferred from a missing candidate. |
| NOT_YET_ISSUED | `expected_not_yet_published` | Stored expectation status only; not independently verified as “not issued.” |
| DISCOVERED | `document_artifacts.acquisition_status='discovered'` and/or `artifact_sources` rows | Candidate/artifact metadata is persisted. |
| ISSUED_NOT_COLLECTED | Available source link with no associated valid content | A measurable link/content gap; publication content itself is not re-fetched. |
| ACCESS_BLOCKED | `artifact_sources.availability_status='inaccessible'`, `automation_status='BLOCKED_TERMS'`, or stored HTTP/error evidence | Stored access/policy state. |
| DOWNLOAD_PENDING / waiting | `artifact_download_queue.status='PENDING'` or `RETRYABLE_FAILURE` | Queue state, not successful acquisition. |
| DOWNLOADED / VALIDATED | `artifact_contents` row; `is_official`, `content_origin`, and `document_artifacts.acquisition_status` | Content existence and stored validation/origin fields. |
| EXTRACTION_PENDING / OK / PARTIAL / FAILED | `artifact_contents.extraction_status` and `document_extractions.extraction_status` | These two tables can differ by one historical failed row; counts below cite the extraction table for completed extraction outcomes. |
| WRONG_DOCUMENT | `artifact_source_evidence_classifications.content_class`, including `UNKNOWN_RATING_DOCUMENT` for rating content | Classifier evidence, not a universal document status. |
| MANUAL_REVIEW | `credit_rating_normalizations.normalization_status='manual_review'`; browser `REVIEW_REQUIRED` | Domain-specific review state. |

Only two period-labeled accepted-result cohorts are present in the live document expectation tables: 2026-06 and 2026-03. 2025-12 and 2025-09 have no period-labeled rows there; older artifacts often have `period_end=NULL`. The blank rows in `document_coverage.csv` are not “not issued” findings.

### LIVE_DATA_COVERAGE — latest period-labeled cohort, 2026-06-30

| Artifact | Expected count | Available expectation | Candidate artifacts / links | Valid official original content | Extraction | Waiting/failure evidence |
|---|---:|---:|---:|---:|---|---|
| Presentation | 2,033 | 181 | 194 / 304 | 188 | 191 OK, 3 partial | 62 pending download jobs; 273 `discovery_failed`; 300 `source_not_covered`; 1,279 `expected_not_yet_published` |
| Official transcript | 2,033 | 0 | 160 / 223 | 147 | 160 OK, 0 partial, 0 failed | 11 pending jobs; 276 `discovery_failed`; 300 `source_not_covered`; 1,457 `expected_not_yet_published` |
| Recording | 2,034 | 0 | 160 / 248 | 0 audio/video contents marked official | 14 audio/video contents exist; all remain `not_started` for extraction/normalization | 2,034 `expected_not_yet_published`; recording download is deliberately disabled by source policy; metadata is not audio acquisition |

These are expected artifact records and discovery states, not an assertion that all companies issue all three documents. The transcript cohort’s 160 discovered artifacts alongside zero `available` expectations is a stored-state inconsistency; it is not evidence that no transcripts were available.

### LIVE_DATA_COVERAGE — all-history aggregates

- **Presentation:** 36,568 logical artifacts; 58,338 source-link rows; 34,668 valid official original contents; 9,989 extraction OK, 106 partial, 13 failed; 25,427 supported contents `not_started`; queue has 14,513 pending, 234 retryable and 1,618 terminal failures. Oldest unresolved download queue item was created Aug 14.
- **Transcript:** 26,320 logical artifacts; 43,170 source-link rows; 18,525 valid official original contents; 6,438 extraction OK, 6 partial, 1 failed; 15,987 supported contents `not_started`; queue has 9,289 pending, 1,694 retryable, 1 stale leased and 5,353 terminal failures. Oldest unresolved queue item was created Aug 14.
- **Recording:** 6,475 metadata artifacts and 10,578 source links, but only 130 audio/video content records across all history. All 130 are `not_started` for extraction; no recording normalization exists. The remaining 6,345 metadata artifacts have no matching audio/video content record. This is URL metadata coverage, not recording download coverage.

Duplicate source URLs are material in persisted source rows: 21,770 presentation and 16,849 transcript duplicate `(artifact_id, document_url)` groups were recorded. Across all artifact contents, 942 content-hash groups have 993 extra rows. These counts describe DB duplication; they do not prove duplicate bytes were stored on disk in every case.

**Discovery/download conclusion:** the stopped reconciler/download roles cannot discover or download new content now. Existing latest-quarter candidates are incomplete and queues contain pending, retryable, and terminal work. Whether additional documents exist at issuers or exchanges is **NOT_PROVEN** without external requests, which were prohibited.

## E. Credit-rating coverage

### SOURCE_CAPABILITY

Current policy rows in `credit_rating_host_policy` mark `crisil.com`, `crisilratings.com` and `icra.in` as `BLOCKED_TERMS` with zero automated concurrency. The policy basis cites the saved website terms/disclaimer references in the source-controlled policy table. Current source routes blocked CRISIL/ICRA artifacts into alternate-source recovery and exposes finite browser recovery only with operator authorization. This audit did not access those publishers.

### LIVE_DEPLOYED_STATE

All direct-rating workers and alternate/browser recovery roles are stopped. The browser run `browser-ratings-20261001` has persisted workers in `STOPPED` state, latest heartbeat Oct 2 18:05:57Z. No current browser or direct-worker heartbeat exists.

### LIVE_DATA_COVERAGE

See [rating_coverage.csv](acquisition-blackbox/rating_coverage.csv) for each publisher’s logical artifacts, source links, direct jobs, source lineage, contents, extraction, normalization and unresolved-target rows. Selected facts:

| Stored publisher/agency | Logical artifacts | Successful direct jobs | Valid official original contents | Alternate targets / candidates attached | Browser success / failed / review | Normalization status |
|---|---:|---:|---:|---:|---:|---|
| CRISIL | 4,719 | 5,186 historical | 4,610 | 4,719 targets; 2,664 candidates; 533 candidates attached | 3,339 / 15 / 50 | 3,391 failed; 145 manual review; no accepted |
| ICRA | 2,489 | 2,918 historical | 2,409 | 2,489 targets; 1,954 candidates; 431 attached | 2,077 / 2 / 0 | 36 failed; 2,041 manual review; no accepted |
| CARE | 3,276 | 3,276 | 3,193 | none recorded | 0 / 0 / 0 | No publisher-group normalization rows |
| Infomerics | 697 | 678 | 663 | none recorded | 0 / 17 / 0 | No publisher-group normalization rows |
| Brickwork | 620 | 620 | 614 | none recorded | 36 / 0 / 0 | 56 failed |
| Fitch (stored publisher) | 1,504 | 1,493 | 1,251 | none recorded | 175 / 0 / 11 | 181 failed |
| SMERA (stored publisher) | 564 | 456 | 447 | none recorded | 0 / 99 / 0 | No publisher-group normalization rows |
| India Ratings | 0 literal publisher artifacts | 0 attributed direct successes | 0 literal publisher contents | none attributed | no literal India Ratings publisher row | 181 failed normalizations are stored with `agency_code='india_ratings'` but joined to publisher `fitch`; attribution is inconsistent |
| Acuité | 0 literal publisher artifacts | 0 | 0 | none recorded | 0 | No literal Acuité publisher row; DB uses publisher `smera`, and this audit does not assume they are the same |

The direct queue has 14,822 rows: 14,627 `SUCCEEDED`, 115 `TERMINAL_FAILURE`, 68 `SKIPPED_POLICY`, 11 `INTERACTION_REQUIRED`, and 1 `RETRYABLE_FAILURE`. The successful direct CRISIL/ICRA rows are historical persisted outcomes; they do not override the current `BLOCKED_TERMS` policy. Current direct acquisition cannot be called successful because the workers are stopped.

For CRISIL, `artifact_download_attempts` ties 4,088 content IDs to `RATING_AGENCY_DIRECT` URLs, 495 to BSE attachments and 33 to NSE attachments; 181 attempt content IDs have no stored retrieval type. ICRA has 1,999 direct, 388 BSE, 31 NSE and 83 unknown-source attempt content IDs. Browser job rows separately name 3,339 successful CRISIL and 2,077 successful ICRA content IDs. These lineage counts may overlap by artifact/content and must not be added as disjoint totals. No issuer-IR recovery candidates are present in the candidate source-type inventory; the stored candidates are BSE/NSE exchange attachments plus a small debt-document set.

Recovery target statuses are 6,456 `ALREADY_SATISFIED`, 204 `CANDIDATE_FOUND`, 379 `NO_OFFICIAL_SOURCE_FOUND`, 164 `LICENSE_OR_MANUAL_REQUIRED`, and 5 `AMBIGUOUS`. Thus 752 targets remain unresolved, including 548 terminal/no-source/manual/ambiguous outcomes. Oldest unresolved target creation is Aug 16. “Already satisfied” means the persisted target criterion is satisfied; it is not a promise that every company has a rating report.

**CRISIL / ICRA conclusion:** both have substantial historical alternate-source and browser recovery evidence, while direct automated HTTP is blocked by the stored policy. Recovery did occur in the past; it is not running now. Coverage gaps remain and the database does not establish that every company has a report.

## F. HTML credit-rating reports

### LIVE_DATA_COVERAGE

- 6,065 original rating contents have `media_type='text/html'` and `content_origin='publisher_original'`.
- Agency/publisher counts: CRISIL 4,069; Fitch 1,318; Brickwork 393; SMERA 110; other stored publishers 0.
- 5,965 HTML originals have extraction status `ok` and pass the five stored structural extraction rules. 100 remain `not_started`.
- The content evidence classifier recognizes 5,793 distinct HTML content IDs as a rating rationale excerpt, full rationale, or rating letter; 176 are `UNKNOWN_RATING_DOCUMENT`; the remainder have no such stored classification. This is semantic classifier evidence, not a blanket human review.
- 6,313 generated PDF derivatives are linked through `html_document_renderings`; **0** rendered outputs are marked `publisher_original`. Another 175 generated HTML derivatives exist. HTML originals remain separately stored and readable in the bounded sample.
- No `credit_rating_normalizations` row joins to an HTML original; accepted/review/failed normalization counts for HTML originals are therefore all zero. PDF-derived and other rating normalization counts remain in `rating_coverage.csv`.

No dedicated generic-error-page status was found in the production schema. A bounded read of `content_id=78` (publisher `fitch`, classified `UNKNOWN_RATING_DOCUMENT`) found an HTML ratings/navigation landing page, not an HTTP error page; it was extracted as HTML and passed structural checks. This shows a non-report/unknown page can be stored under a rating artifact, but does not prove how all 176 unknown-class contents should be classified. Bounded disk reads confirmed the sampled HTML original, June presentation PDFs, and transcript PDFs exist at the store path and their on-disk sizes equal DB sizes.

## G. Concall transcript quality

### SOURCE_CAPABILITY

Current source contains deterministic PDF extraction, transcript parsing/validation and recording fallback pathways. Those capabilities require a worker/campaign path and persisted schema; code capability alone does not create processed transcripts.

### LIVE_DEPLOYED_STATE and LIVE_DATA_COVERAGE

- `transcripts`: 0 rows; `transcript_segments`: 0; `transcript_validations`: 0; `transcript_section_boundaries`: 0; `machine_transcription_runs`: 0; `preferred_transcripts`: 0.
- All-history official transcript content: 18,525 original official content IDs. Deterministic extraction: 6,438 OK, 6 partial, 1 failed. Another 15,987 supported transcript contents remain `not_started`.
- The 2026-06 cohort has 147 official original transcript contents and 160 extraction-OK transcript contents. No transcript has accepted/manual-review/failed segmentation because no transcript segmentation rows exist.
- Explicit speaker segments: 0; persisted Q&A boundaries: 0; machine-generated fallback transcripts: 0; machine fallbacks despite a usable official transcript: 0 recorded cases.
- There are 130 saved audio/video contents across history, all without a recording normalization or machine transcript. Recording URL metadata is much larger than actual media; Compose explicitly retains/promotes recording metadata without automatic audio download.

No extraction or transcription was run for this audit. The bounded sample includes an extraction-OK June transcript PDF (content 7088), a partial historical transcript PDF (content 13071), and a stored HTML rating report (content 1); each sampled file was readable. There is no transcript manual-review record to sample.

## H. PPT / PPTX extraction quality

### LIVE_DATA_COVERAGE

Presentation artifacts have 35,533 PDF contents plus one PPTX-media content in `artifact_contents`. Completed extraction rows are 9,989 OK, 106 partial and 13 failed. A further 25,427 PDF/PPTX contents remain `not_started`; oldest is Aug 15 23:17:34Z. Presentation download queue has 14,513 pending, 234 retryable, 1,618 terminal; many rows are from the older finite `document-acquisition-full-v1` scope, not one active worker’s due count.

Stored page-level coverage includes 348,102 presentation pages/slides, 347,881 with nonempty selected text, and layout JSON on all 348,102. Element rows by presentation type: 10,497,984 `text_block`, 4,365,590 `image` elements with references, and 494,575 `table` elements with rows and references. These are stored extraction outputs, not proof of every image’s visual content being interpreted.

The three partial June content IDs are:

| Content ID | Artifact ID | Current state | Pages/slides | Native | OCR | Unusable |
|---:|---:|---|---:|---:|---:|---:|
| 49289 | 82801 | partial | 31 | 30 | 0 | 1 |
| 49425 | 75371 | partial | 33 | 28 | 4 | 1 |
| 49446 | 71581 | partial | 19 | 17 | 1 | 1 |

Bounded samples: June PDF content 7117 was extraction OK; contents 49289 and 49425 were partial. Their files exist, read the `%PDF-1.x` header, and match DB byte sizes. No extraction was run. Current Compose’s June extraction command is a fixed period/limit role; its container is missing.

## I. Automatic processing and extraction continuity

### SOURCE_CAPABILITY

The C28 source adds migration 072, which creates `document_processing_inbox` and `document_processing_work` and an `AFTER INSERT ON artifact_contents` trigger that records new content in the inbox. Work materialization in [processing.py](/Users/mohamedfadhi/Fadhi/trading-ai/scrapper/db/documents/processing.py:38) is a separate explicit operation: it accepts selected content IDs (1–100) and an explicit parser/OCR/options version. It does not automatically materialize every inbox item. `quarter_processing_role.py` requires a period; `quarter_extract.py` selects a bounded period and limit. Current Compose sets the role to period `2026-06-30`, limit 10.

### LIVE_DEPLOYED_STATE

The running state is empty: all services are stopped and there is no document-processing container. The stopped runtime image is C05.3, not the C28 integrated source. The production database’s latest migration identity is `068_exchange_source_access_compatibility.sql`; it has not applied current-source migrations 069–075, including migration 072.

### LIVE_DATA_COVERAGE

Neither `document_processing_inbox` nor `document_processing_work` exists in the production DB schema. The 44,434 supported PDF/PPTX/credit-rating-HTML contents with `not_started` extraction therefore have no production work-table state. New `artifact_contents` cannot be shown to enter an inbox in this DB; the C28 trigger is absent. No processing role is running. The chain currently stops at **content persistence**; extraction success is limited to content already processed by legacy/other historical paths. Current C28 capability is itself selection- and period-scoped after inbox creation.

**`NEW_DOCUMENT_AUTOMATIC_EXTRACTION: NO`** in live deployed state. The C28 trigger/materializer path is SOURCE_CAPABILITY only; the production schema stops before 072, and its processing worker role is missing.

## J. Worker and queue health

Every expected active production role is stopped; none has a current heartbeat. Key queue state at the cut-off:

| Class | Pending/due | Leased/stale | Retry/deferred | Terminal/review | Completed | Oldest evidence |
|---|---:|---:|---:|---:|---:|---|
| Exchange scheduler | Oct 5–7 scheduled dates have no run rows | 0 | BSE feeds access-held; NSE guard failed | Latest scheduled failures remain stored | NSE result success Oct 3; BSE Sep 16 | BSE access hold Sep 29; latest NSE attempt Oct 3 |
| Screener company queue | 842 pending, 838 below-cap dispositions | 0 claimed rows | 838 below-cap are deferred, not acquisition failures | 3,273 terminal (463 not marked below-cap) | 6,332 accepted | Sep 29 pending non-scope item |
| Market-cap monitor | 3,529 due checks; 1,172 eligible | 0 | 51 unknown cap; 2,306 below-floor due | n/a | n/a | Oct 2 06:23Z due check |
| Artifact reconciliation | 593 `discovery_failed` due | 100 expired expectation claims | 593 next-check times are overdue | 50 failures in last logged batch | 181 available expectations | Oct 3 02:41Z next-check; last work Oct 4 16:52Z |
| Generic document download, accepted scope | 79 pending | 0 in this scope | 275 retryable; 6,202 policy skips | 4,780 terminal | 45,345 succeeded; 12,558 already have content | Aug 14 queue creation |
| Generic download, full campaign scope | 23,743 pending | 2 stale `LEASED` | 1,632 retryable; 6,412 policy skips | 2,319 terminal | 12,733 succeeded | Aug 16; lease expiry Sep 8 |
| Direct rating | Queue has 14,627 succeeded plus 127 unsettled (115 terminal, 11 interaction, 1 retry) | no direct lease | 68 policy skips | 115 terminal / 11 interaction-required | 14,627 succeeded | Direct queue records start Aug 13 |
| Alternate rating recovery | 204 candidate-found; no active source search | 0 active lease evidenced | 379 no-source; 164 license/manual; 5 ambiguous | 548 unresolved terminal/manual/ambiguous | 6,456 satisfied | Aug 16 oldest unresolved target |
| Browser rating recovery | no active jobs | 0 current | finite runs ended | 133 failed, 61 review-required | 5,627 succeeded | Worker rows STOPPED by Oct 2 |
| Document processing/extraction | 44,434 saved contents `not_started`; work queue absent | unknown—no production work table | no retry queue state available | extraction rows: 13 failed presentations, 1 failed transcript | 9,989 presentation and 6,438 transcript extraction OK | content pending from Aug 13–15 |

Generic download host cooldown rows show zero `blocked_until_utc` later than audit time. This does not clear the BSE exchange source-access holds or CRISIL/ICRA terms policy. The separate ICRA credit-rating host health row records `INTERACTION_REQUIRED` from Aug 13. Browser worker heartbeat rows all say `STOPPED`. Full per-role mount/image/status detail and counts are in [runtime_roles.csv](acquisition-blackbox/runtime_roles.csv).

## K. Unified failure ledger and severity

[failure_ledger.csv](acquisition-blackbox/failure_ledger.csv) contains **82,972 rows** representing stored unresolved queue items, failed expectations, failed/review browser jobs, unresolved rating targets, overdue/unknown scope observations, supported content never extracted, latest exchange failures, and one grouped invalid-period integrity anomaly. It records exact stored status/error where available; synthetic backlog rows say why they are actionable. Normal `expected_not_yet_published` and ordinary below-floor eligibility are not called failures.

Severity totals in the ledger:

- **P0 — 9 rows:** current exchange/security-master failures and the 1970-01-01 invalid-period sentinel evidence. The sentinel is present in 3,277 `artifact_events` and 4,742 Screener queue rows; it must not be read as a real reporting period.
- **P1 — 80,582 rows:** new-result/Screener failures and due work; discovery/download backlog; 44,434 extraction-not-started contents; unresolved rating recovery and review work.
- **P2 — 2,381 rows:** overdue below-floor scope checks, unknown observations and lower-priority cleanup/terminal review.

These are row-level actionable records, not 82,972 independent root causes. Queue items can point to the same publisher, company, content or underlying outage.

## L. Black-box summary

| System area | Status | Current coverage | Oldest problem | Failure count | Automatic recovery? | Human action? |
|---|---|---|---|---:|---|---|
| Exchange discovery | RED | NSE last success Oct 3; BSE result Sep 16/master Sep 23; zero running scheduler | BSE 403 hold since Sep 29 | 9 P0 exchange/security/integrity rows | No, scheduler stopped | Yes, source/guard review and release/runtime restoration |
| Screener ingestion | RED | 6,332 accepted; 842 pending, most held below floor | Sep 29 pending non-scope row | 463 non-scope terminal | No, daemon stopped | Yes, retry/access/parse review |
| ₹580 collection scope | RED | 1,887 eligible; 1,172 overdue; no recorded re-entry | Oct 2 due checks | 1,223 eligible/unknown due or unknown | No, monitor stopped | Yes, restore scope observation; verify re-entry from live history |
| Presentations | RED | 188 valid official originals in June cohort; 191 extraction OK/3 partial | Aug 14 pending download | 273 discovery failed; 62 period jobs pending; 25,427 all-history extraction pending | No, roles stopped | Yes, discovery/download backlog |
| Official transcripts | RED | 147 official June originals; 160 deterministic extraction OK; no transcript segments | Aug 14 pending download | 276 discovery failed; 11 period jobs pending; 15,987 not started | No | Yes, discovery and transcript processing |
| Recordings | AMBER | 160 June metadata artifacts; 14 June media files; 130 media files all-history | Aug 14 old metadata/download outcome | 130 media await normalization; 6,345 metadata-only artifacts | Automatic audio download intentionally disabled | Yes, operator-directed acquisition/normalization if desired |
| Direct ratings | AMBER | 14,627 historical queue successes; current direct role stopped; CRISIL/ICRA policy-blocked | Aug 13 queue cohort | 115 terminal, 11 interaction, 1 retry | Other-agency workers can retry only if running and policy permits | Yes for interaction/policy cases |
| CRISIL recovery | AMBER | 533 attached recovery candidates; 3,339 browser successes | Aug 16 unresolved targets | 385 targets unresolved; 15 browser failed/50 review | No current worker; past recovery did run | Yes for license/manual/review and unresolved matches |
| ICRA recovery | AMBER | 431 attached candidates; 2,077 browser successes | Aug 16 unresolved targets | 367 unresolved; 2 browser failed | No current worker; past recovery did run | Yes for license/manual/ambiguous cases |
| Other rating recovery | AMBER | CARE/Infomerics/Brickwork/Fitch/SMERA content exists; browser runs stopped | Aug 13 direct queue cohort | 115 direct terminal; agency-specific browser failures/reviews | Partial when those direct/browser roles ran; none now | Yes for failed/review/mismatched attribution |
| HTML rating extraction | AMBER | 6,065 original HTML; 5,965 structural extraction OK; 6,313 generated PDF derivatives | Aug 13 HTML content cohort | 100 originals not started; 176 unknown document-class records | No current processor | Yes for unknown/non-report classification and normalization gaps |
| Transcript extraction | RED | 6,438 extraction OK; 6 partial; 1 failed | Aug 15 not-started content | 15,987 not started; 0 transcript segments | No | Yes, processing handoff/segmentation |
| PPT extraction | RED | 9,989 OK; 106 partial; 13 failed | Aug 15 not-started content | 25,427 not started | No processing worker | Yes, extraction backlog and partial review |
| Processing handoff | RED | No inbox/work table in production schema | New content stops at persistence | 44,434 supported contents not started | No | Yes, schema/release/role ownership decision |
| Worker health | RED | 0 active Trading AI containers | Containers exited Oct 4 | 11 core roles stopped; browser roles stopped | No | Yes, runtime recovery/release decision |

## Explicit answers

Each answer separates code capability from what the production copy/runtime proves. “Not proven” identifies evidence that cannot be established without external fetches or unavailable live workers.

### 1. Are new eligible financial results being automatically discovered?

- **CODE_PROVES:** canonical Compose/source has a scheduled exchange-results workflow and retries.
- **LIVE_DB_PROVES:** No scheduler is running. NSE last succeeded Oct 3 and the latest scheduled NSE results attempt failed guard checks; BSE results last succeeded Sep 16 and is held after HTTP 403. Oct 5–7 have no schedule rows.
- **NOT_PROVEN:** Whether an exchange published additional results after the last saved feed; no network access was made.
- **Answer:** **No, not in the observed production state.**

### 2. If a company crosses from below ₹580cr to at least ₹580cr, is it actually re-entering collection?

- **CODE_PROVES:** equality qualifies; the 580 crore test expects eligible and due-claimable behavior.
- **LIVE_DB_PROVES:** zero stored below-to-eligible transitions; zero eligible companies currently have scope-held document jobs; the monitor is stopped; 1,172 eligible checks are overdue.
- **NOT_PROVEN:** behavior for a crossing that has not been observed in this DB.
- **Answer:** **No live re-entry is evidenced.**

### 3. Are all discoverable investor presentations being downloaded?

- **CODE_PROVES:** source supports candidate reconciliation and download queues.
- **LIVE_DB_PROVES:** June cohort has 194 candidate artifacts, 188 valid official originals, 62 pending jobs and 273 discovery failures; no worker runs now. All-history has 14,513 pending and 1,618 terminal presentation download jobs.
- **NOT_PROVEN:** Any not-yet-discovered issuer link outside the saved corpus; “all” cannot be established from saved candidates alone.
- **Answer:** **No.**

### 4. Are all discoverable official concall transcripts being downloaded?

- **CODE_PROVES:** source supports official transcript discovery/download from accepted pages.
- **LIVE_DB_PROVES:** June has 160 candidate artifacts, 147 valid official original contents, 11 pending jobs, 276 discovery failures, 300 uncovered-source expectations; all roles are stopped.
- **NOT_PROVEN:** Documents absent from saved source pages.
- **Answer:** **No.**

### 5. Are CRISIL/ICRA reports being recovered where possible?

- **CODE_PROVES:** direct automation is blocked by current saved policy; alternate BSE/NSE/issuer recovery and finite operator-authorized browser paths exist.
- **LIVE_DB_PROVES:** past recovery attached 533 CRISIL and 431 ICRA candidates; browser jobs succeeded for 3,339 and 2,077 respectively. 385 CRISIL and 367 ICRA targets remain unresolved; recovery workers are stopped.
- **NOT_PROVEN:** Whether other alternate-source copies exist outside the saved corpus.
- **Answer:** **Partly in historical data; no recovery is running now.**

### 6. Are HTML rating reports correctly stored/extracted?

- **CODE_PROVES:** current source has HTML rendering and rating-classification paths.
- **LIVE_DB_PROVES:** 6,065 publisher-original HTML contents; 5,965 passed structural extraction checks; 6,313 generated PDF derivatives; no rendered derivative is mislabeled publisher-original; bounded sample paths were readable. No normalization row joins to HTML originals; 100 are not started and 176 classify as unknown.
- **NOT_PROVEN:** Semantic correctness of all 6,065 pages; only bounded content reads were performed.
- **Answer:** **Mostly stored/extracted structurally; rating normalization and semantic classification are incomplete.**

### 7. Are downloaded transcripts being successfully processed?

- **CODE_PROVES:** deterministic extraction and segmentation are available in source.
- **LIVE_DB_PROVES:** 6,438 transcript extraction OK, 6 partial, 1 failed, but `transcripts`, `transcript_segments`, `transcript_validations` and machine transcription runs all have zero rows; 15,987 contents remain not started.
- **NOT_PROVEN:** Transcript speaker/Q&A quality; no segmentation output exists to evaluate.
- **Answer:** **PDF text extraction exists historically; transcript structuring/segmentation is not happening in the observed DB.**

### 8. Are downloaded PPT/PPTX files being successfully processed?

- **CODE_PROVES:** PDF/PPT/PPTX extraction and page/table/image-reference persistence are implemented.
- **LIVE_DB_PROVES:** 9,989 presentation extractions OK, 106 partial, 13 failed, and 25,427 supported contents not started; the worker is missing.
- **NOT_PROVEN:** Visual correctness of all slides; only bounded files were read, and no re-extraction was run.
- **Answer:** **Some historical processing succeeded, but the backlog is large and processing is not currently running.**

### 9. Does every newly downloaded eligible document automatically enter extraction?

- **CODE_PROVES:** C28 migration 072 adds an inbox trigger; work materialization separately requires explicitly selected IDs and parser/OCR version; current Compose worker is period/limit scoped.
- **LIVE_DB_PROVES:** Production schema stops at migration 068, has no inbox/work tables, and has no processing-worker container.
- **NOT_PROVEN:** Any post-C28 production handoff, because C28 was not deployed.
- **Answer:** **No. The observed live chain stops at content persistence.**

### 10. Is the latest cleaned C28 source actually the running production release?

- **CODE_PROVES:** C28.1 source projection is present at integration commit `c3ddbbb9…`; source projection SHA is recorded in its integration manifest.
- **LIVE_DB_PROVES:** no container is running; the stopped runtime containers use C05.3 digest and logs identify commit `db7564a1…`, not C28.1.
- **NOT_PROVEN:** Whether some unobserved host outside this Docker context runs another image.
- **Answer:** **No.**

### 11. What are the top five real blockers right now?

1. **No running production workers and no C28 release:** scheduler, Screener, scope, acquisition and rating roles exited Oct 4; processing role is absent.
2. **Financial discovery is stale/blocked:** BSE HTTP 403 holds and NSE snapshot guard failures; no successful result scan after Oct 3.
3. **Extraction handoff is absent from production:** schema is at 068, processing tables/trigger are missing, and 44,434 supported contents are unstarted.
4. **Collection scope is stale:** 1,172 eligible rows are overdue, no below-to-eligible transition is recorded, and 92 eligible companies lack accepted queue history.
5. **Document acquisition/recovery backlog is material:** 593 discovery failures, 14,513 presentation and 9,289 transcript pending jobs, 752 unresolved rating recovery targets, and terminal/review queues remain.

- **CODE_PROVES:** source contains mechanisms for scheduled discovery, inclusive 580 crore scope re-entry, alternate rating recovery and bounded document processing.
- **LIVE_DB_PROVES:** current runtime/data states above.
- **NOT_PROVEN:** documents or financial results that were never saved, and any runtime outside the inspected Docker context.

## Appendix 1 — safe database copy and validation

The named volume contained multiple DB snapshots. `/data/scraper.db` was the configured production path from stopped-container `SCRAPER_DB_PATH=/data/scraper.db`, and it was the newest main DB by mtime. It was copied with source mounted `:ro`; the only writable mount was the disposable audit directory. There was no app entrypoint or app command in either helper container.

| Source file | Size (bytes) | mtime UTC | Sidecars |
|---|---:|---|---|
| `/source/a0_backup_20260929.sqlite` | 10,922,704,896 | 2026-09-29 08:37:44.401599 | WAL 0 bytes (Oct 1 05:44:23.979877); SHM 32,768 bytes (Oct 1 05:44:39.103325) |
| `/source/a2_post065_backup_20260929.sqlite` | 11,033,886,720 | 2026-09-29 11:29:29.538175 | WAL 0 bytes (Sep 29 11:30:08.039158); SHM 32,768 bytes (Sep 29 15:07:25.776375) |
| `/source/a2_post065_compat_test_20260929.sqlite` | 11,033,886,720 | 2026-09-29 11:43:20.844648 | no sidecars listed |
| `/source/a2_post065_restore_verify_20260929.sqlite` | 11,033,886,720 | 2026-09-29 11:41:39.413479 | WAL 0 bytes and SHM 32,768 bytes (Sep 29 11:42:44Z) |
| `/source/scraper.db` | 14,602,158,080 | 2026-10-05 06:07:47.659852 | no WAL or SHM |
| `/source/scrapper.db` | 0 | 2026-09-29 05:55:08.437748 | no sidecars listed |
| `/source/backups/document-acquisition/scraper-before-date-backfill-20260816T095301Z.db` | 3,026,296,832 | 2026-08-16 09:53:50.874727 | WAL 0 bytes, SHM 32,768 bytes (09:53:51Z) |
| `/source/backups/pre-scope-remediation-20260928T122338Z.db` | 10,344,316,928 | 2026-09-28 12:24:10.375433 | WAL 0 bytes, SHM 32,768 bytes (12:24:10Z) |
| `/source/backups/q2-scope-trial-20260929.db` | 10,347,499,520 | 2026-09-29 04:15:35.260936 | no sidecars listed |

| Copy evidence | Value |
|---|---|
| Source | `/source/scraper.db` |
| Source size | 14,602,158,080 bytes |
| Source SHA256 | `f237daa6f4696f75e2081e93d69a57be6f8d9c097769aaa169a819789c9e4811` |
| Disposable copy | `/private/tmp/trading-ai-audit-2026-10-07/scraper.db` |
| Copy size | 14,602,158,080 bytes |
| Copy SHA256 | `f237daa6f4696f75e2081e93d69a57be6f8d9c097769aaa169a819789c9e4811` |
| SQLite journal mode | `wal` |
| `PRAGMA integrity_check` | `ok` |
| `PRAGMA quick_check` | `ok` |
| WAL recovery needed | No; no source WAL/SHM existed for `scraper.db`, and no recovery/checkpoint was performed |
| Schema | 69 `schema_migrations` rows; latest applied identity `068_exchange_source_access_compatibility.sql`; current canonical source includes migrations 069–075 |
| Processing tables | `document_processing_inbox` and `document_processing_work` absent |

The read-only inventory found 149 tables and 27 views. Selected row counts: `companies` 4,428; `screener_collection_scope` 5,490; `screener_company_queue` 12,749; `artifact_expectations` 25,290; `artifact_sources` 131,903; `document_artifacts` 83,232; `artifact_contents` 78,202; `document_extractions` 33,640; `exchange_schedule_runs` 389; `exchange_result_filings` 25,713; `credit_rating_download_queue` 14,822; `artifact_recovery_targets` 7,208; `browser_rating_recovery_jobs` 5,821.

## Appendix 2 — document store location and bounded reads

Docker inspect identified `/Volumes/My Passport/passport/trading-ai` as a **host bind mount** on external volume `/dev/disk5s1` (`/Volumes/My Passport`), not a Docker named volume. It existed at audit time and contained `artifact_content/` and `content/`. The current filesystem reported 349 GiB available; the host-published capacity JSON was measured at `2026-10-07T04:12:09Z` and reported 375,151,329,280 free bytes of 1,000,168,488,960 total. The store identity file reports production path `/documents`, release identity `c05.3-release-20261003`, role `documents`.

Bounded read samples checked only the selected local paths (mapping the stored `/documents/...` path to the inspected host bind root for existence/readability; no DB path value was changed):

| Content | Type/state | DB bytes | On disk | Bounded read |
|---|---|---:|---|---|
| 49289 | June presentation PDF, partial | 1,316,829 | exists; same size | 64-byte PDF header read |
| 49425 | June presentation PDF, partial | 5,537,439 | exists; same size | 64-byte PDF header read |
| 7117 | June presentation PDF, OK | 5,722,058 | exists; same size | 64-byte PDF header read |
| 7088 | June transcript PDF, OK | 843,601 | exists; same size | 64-byte PDF header read |
| 13071 | transcript PDF, partial | 8,104,317 | exists; same size | 64-byte PDF header read |
| 1 | CRISIL HTML original, OK | 91,810 | exists; same size | 64-byte HTML header read |
| 21993 | presentation PDF, not started | 9,166,963 | exists; same size | 64-byte PDF header read |

No whole-corpus crawl or hash was run.

## Appendix 3 — exact commands and SQL used

### Shell commands

Volume enumeration (harmless shell entrypoint; no app startup; source volume read-only; network disabled):

```sh
docker run --rm --network none --read-only --entrypoint /bin/sh \
  --mount type=volume,source=trading-ai_scraper-data,target=/source,readonly \
  trading-ai-c053-release@sha256:44da7074157fd2a946c41d064a91ef3f729ebd31ab3bcdf2aa09233c5a794b6b \
  -c 'find /source -type f \( -name "*.db" -o -name "*.sqlite" -o -name "*.sqlite3" -o -name "*-wal" -o -name "*-shm" \) -exec stat -c "%n\t%s\t%y" {} \;'
```

Copy and source/copy hashes (only `/audit` writable; only the DB and present adjacent sidecars copied):

```sh
mkdir -p /private/tmp/trading-ai-audit-2026-10-07
docker run --rm --network none --read-only --entrypoint /bin/sh \
  --mount type=volume,source=trading-ai_scraper-data,target=/source,readonly \
  --mount type=bind,source=/private/tmp/trading-ai-audit-2026-10-07,target=/audit \
  trading-ai-c053-release@sha256:44da7074157fd2a946c41d064a91ef3f729ebd31ab3bcdf2aa09233c5a794b6b \
  -c 'set -eu; cp -p /source/scraper.db /audit/scraper.db; sha256sum /source/scraper.db /audit/scraper.db; for f in /source/scraper.db-wal /source/scraper.db-shm; do if [ -f "$f" ]; then b=${f##*/}; cp -p "$f" "/audit/$b"; sha256sum "$f" "/audit/$b"; else echo "ABSENT $f"; fi; done'
```

Stopped-container metadata and status were read with `docker ps -a`, `docker inspect`, and `docker logs --tail ...`; no container was started/stopped by the audit. Compose metadata was read from `compose.yml` and `compose.browser-recovery.yml`. The stopped release manifest and C28.1 evidence were read from `ops/docker/release/c05.3/` and `docs/cleanup/baselines/C28.1/`.

### SQLite connection and validation

The connection was Python’s standard `sqlite3` opened with URI `file:/private/tmp/trading-ai-audit-2026-10-07/scraper.db?mode=ro`, `uri=True`, followed by `PRAGMA query_only=ON`. It was never opened against `/source` after the copy.

```sql
PRAGMA journal_mode;
PRAGMA integrity_check;
PRAGMA quick_check;
SELECT type, name, sql FROM sqlite_master
 WHERE type IN ('table','view') ORDER BY type,name;
SELECT * FROM schema_migrations ORDER BY applied_at_utc;
SELECT name FROM sqlite_master
 WHERE name IN ('document_processing_inbox','document_processing_work');
```

Representative exact audit queries:

```sql
-- Exchange successes and watermarks
SELECT dataset, MAX(finished_at_utc)
FROM exchange_schedule_runs
WHERE outcome IN ('published_changed','published_unchanged')
GROUP BY dataset;
SELECT exchange,dataset,last_broadcast_at_utc,last_snapshot_id,updated_at_utc
FROM exchange_result_watermarks;
SELECT * FROM exchange_source_access_events ORDER BY event_at_utc;

-- Scope state, overdue checks and observed transitions
SELECT eligibility,COUNT(*),MIN(observed_at_utc),MAX(observed_at_utc),
       MIN(next_check_at_utc),MAX(next_check_at_utc)
FROM screener_collection_scope GROUP BY eligibility;
SELECT COUNT(*) FROM screener_collection_scope
WHERE next_check_at_utc < '2026-10-07T09:09:40+00:00';
SELECT COUNT(*) FROM screener_scope_observations a
JOIN screener_scope_observations b ON a.company_path=b.company_path
WHERE a.eligibility='below_floor' AND b.eligibility='eligible'
  AND b.observed_at_utc>a.observed_at_utc;

-- Expectations by accepted period/type/status
SELECT ev.period_end,e.artifact_type,e.availability_status,COUNT(*)
FROM artifact_expectations e JOIN artifact_events ev USING(event_id)
GROUP BY 1,2,3 ORDER BY 1 DESC,2,3;

-- Contents and extraction by artifact type/state
SELECT a.artifact_type,c.media_type,c.extraction_status,c.content_origin,
       c.is_official,COUNT(*)
FROM artifact_contents c JOIN document_artifacts a USING(artifact_id)
GROUP BY 1,2,3,4,5;
SELECT a.artifact_type,e.extraction_status,COUNT(*),
       MIN(e.started_at_utc),MAX(e.completed_at_utc)
FROM document_extractions e JOIN artifact_contents c USING(content_id)
JOIN document_artifacts a USING(artifact_id)
GROUP BY 1,2;

-- Direct rating policy and current queue states
SELECT * FROM credit_rating_host_policy ORDER BY host;
SELECT a.publisher,q.status,COUNT(*)
FROM credit_rating_download_queue q JOIN document_artifacts a USING(artifact_id)
GROUP BY 1,2;
SELECT document_originator,status,COUNT(*),MIN(created_at_utc)
FROM artifact_recovery_targets GROUP BY 1,2 ORDER BY 1,2;

-- Transcript and processing evidence
SELECT COUNT(*) FROM transcripts;
SELECT COUNT(*) FROM transcript_segments;
SELECT COUNT(*) FROM machine_transcription_runs;
SELECT name FROM sqlite_master WHERE name LIKE 'document_processing_%';
```

All supporting CSV files are in `docs/audits/acquisition-blackbox/`. No fixes were applied. The audit stops here.
