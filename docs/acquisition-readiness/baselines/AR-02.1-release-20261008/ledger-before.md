# Trading AI — acquisition readiness execution

**Version:** 1.2  ·  **Created:** 7 October 2026  ·  **Updated:** 8 October 2026  ·  **Baseline:** production black-box audit dated 7 October 2026  ·  **Current action:** AR-01.3 COMPLETE — required read-only minutes0/5/15 probes PASS; AR-02.1 remains offline COMPLETE / PREPARED for separate activation

**Objective:** Safely move the audited Trading AI production system from the stopped C05.3 runtime to the cleaned C28.1 release, restore and prove the new-result and document-acquisition lanes, close the bounded behavioral gaps established by the audit, and reach the documented `READY_FOR_DOCUMENT_INTELLIGENCE` state before freezing acquisition interfaces.

**Execution status:** AR-00 and clone-only AR-01.1 are COMPLETE; AR-01.2 remains PREPARED for its exact accepted package. AR-01.3's [fresh snapshot/restore A](../acquisition-readiness/chunks/AR-01.3A-pre-migration-snapshot-restore-proof.md), [production migrations/schema075 B](../acquisition-readiness/chunks/AR-01.3B-migration-only-production-activation.md) and [corrected control canary C](../acquisition-readiness/chunks/AR-01.3C-attempt-02-execution.md) passed their selected scopes. **AR-01.3 parent acceptance is COMPLETE** after the required three-probe 15-minute series passed with unchanged production state. [Formal closure](../acquisition-readiness/chunks/AR-01.3-parent-acceptance-20261008.md). Earlier preflight/retry blockers are historical, not current. AR-02.1 is **PREPARED: offline gate COMPLETE, production activation NOT_STARTED**. Both authentic CSVs are now retained with matching hashes; original parser/guard replay passes, and the 94-test suite remains passing. The zero-row response stays rejected. [Criterion reconciliation and evidence resolution](../acquisition-readiness/chunks/AR-02.1-evidence-resolution-20261008.md). Operational services remain stopped in the latest retained/current container inventories; no patched result lane is live. Other later chunks remain NOT_STARTED. This session executed only the outstanding read-only parent probes and formal closure; stop after recording it. Listing a chunk is not authorization to activate it.

**Governing source:** [Trading AI Research and Document Intelligence Master Plan — Audit Updated](TRADING_AI_MASTER_RESEARCH_AND_DOCUMENT_INTELLIGENCE_PLAN_AUDIT_UPDATED.md). In case of overlap, its Current Phase and freeze acceptance conditions govern; Part 2 governs document lifecycle behavior; Part 3 governs the future V1 extraction contract. The existing [cleanup execution ledger](../../TRADING_AI_CLEANUP_EXECUTION.md) governs completed structural cleanup and compatibility/preservation constraints. This file governs the next acquisition-readiness execution sequence.

## 1. How this ledger governs execution

This plan converts the audited production baseline into bounded execution work. It is not a substitute for code review, deployment runbooks, operator instructions, or production authorization. Complete the smallest independently verifiable unit; do not combine unrelated lanes to save a rollout.

Use the following statuses:

```text
NOT_STARTED  no work has begun
IN_PROGRESS  selected work is underway
PREPARED     offline change/release is reviewable but not activated
COMPLETE     that chunk's stated gate has passed, with evidence retained
BLOCKED      a concrete prerequisite or external condition prevents progress
DEFERRED     explicitly postponed with reason and owner/trigger
SUPERSEDED   replaced by a documented, approved chunk
```

`PREPARED` is not production success. A production chunk becomes `COMPLETE` only after its exact authorized activation scope and post-activation proof pass. Tests or a successful local migration do not imply permission to deploy or mutate production.

Record **offline gate**, **production activation** and **production acceptance** separately. A repair whose offline gate passes is PREPARED even while activation is unselected or blocked. A purely offline chunk may be COMPLETE for its declared scope. Parent production gates remain incomplete until every applicable criterion passes; a smaller successful subchunk does not silently close them.

**Offline dependencies:** use the required source contract, schema/fixture version and retained input identities. Runtime startup, a named recovery operator, live worker heartbeats and production snapshots are activation prerequisites, not prerequisites for independent offline code, replay, query or documentation work. Chunk-specific behavioral/data dependencies still apply where the offline proof actually consumes another chunk's output. Independent offline chunks may proceed within the user's authorized scope without waiting for unrelated production gates or requesting another checkpoint after each routine unit. This does not select unrequested chunks.

**Reuse verified evidence:** link the exact release/source/config/input identity and prior proof rather than repeating an accepted build, restore rehearsal, migration or test suite for unchanged inputs. Reopen only the affected proof when those inputs change, evidence is missing/corrupt, a new failure appears or the requested scope changes its assumptions. Before a new production operation, still perform fresh live identity, ownership, sidecar/lease and relevant baseline checks; reuse never substitutes historical observations for current safety. An application patch requires review of its new immutable candidate, while unchanged migration/recovery evidence may remain valid.

For each selected subchunk, record: exact scope and authorization; source commit and working-tree snapshot; runtime/release and schema identity; data baseline; allowed and forbidden paths; expected behavior and invariants; proof procedure and actual output; evidence paths and hashes; rollback target and pending-work treatment; unresolved limitations; next single smallest chunk.

## 2. Audited baseline and current state

The table below is the historical 7 October audit, not today's runtime/schema inventory. Subsequent retained proof establishes fresh snapshot/restore A, production schema075 B, and the sole RELEASED control receipt C. See the current execution status and linked reconciliation; preserve the historical baseline unchanged.

The baseline is the read-only production audit, not an assumption that C28.1 is live. The database copy passed integrity checks, matched its production source hash, and required no WAL recovery. No workers, migrations, publisher requests, downloads, backfills, or reprocessing were started for that audit.

| Area | Audited state at 7 October 2026 |
|---|---|
| Runtime | 0 Trading AI containers running; 11 stopped core roles used older `c05.3-release-20261003` / baseline commit `db7564a1…` |
| Cleaned source | C28.1 is reported complete in source; it is not deployed |
| Production schema | Latest migration 068; source includes migrations 069–075; C28 processing inbox/work and trigger are absent in production |
| Result discovery | RED: BSE has stored HTTP 403 access blocks; latest NSE results attempt was rejected by snapshot guards; no scheduled result runs for Oct 5–7 |
| Screener ingestion | 6,332 accepted; 842 pending, including 838 held below market-cap scope; 463 terminal failures not marked below-cap |
| Collection scope | 1,887 eligible; 3,552 below floor; 51 unknown; 3,529 checks overdue; no observed below-floor → eligible transitions |
| Document coverage | June 2026 cohort has real PPT/transcript discoveries and downloads, with discovery failures, uncovered sources and pending downloads remaining |
| Processing | 44,434 supported contents have extraction `not_started`; no production processing inbox/work tables or processing-worker container |
| Presentations | 34,668 valid official original contents all-history; 25,427 supported contents not started extraction; 14,513 pending download jobs |
| Transcripts | 18,525 valid official original contents all-history; 15,987 supported contents not started; 9,289 pending download jobs; transcript structuring tables empty |
| Recordings | 6,475 metadata artifacts and 130 saved audio/video contents; reference-only behavior is intentional by default |
| Ratings | 4,610 valid CRISIL and 2,409 valid ICRA originals; 385 and 367 unresolved recovery targets respectively; direct unattended agency HTTP remains policy-blocked |
| HTML ratings | 6,065 publisher-original HTML contents; 5,965 structural extraction `ok`, 100 not started, 176 unknown-class |
| Data quality | `1970-01-01` appears in 3,277 artifact events and 4,742 Screener queue rows; lineage-aware classification/remediation is required |
| Failure ledger | 82,972 actionable rows, concentrated in shared root causes; do not treat as 82,972 independent bugs |

Retain and link the audit report and its five supporting evidence files:

```text
TRADING_AI_ACQUISITION_BLACKBOX_AUDIT_2026-10-07.md
failure_ledger.csv
document_coverage.csv
rating_coverage.csv
company_scope.csv
runtime_roles.csv
```

Exact retained paths and SHA256 values are confirmed in the [AR-00 evidence index](../acquisition-readiness/baselines/AR-00/evidence-index.json): the report is directly under `docs/audits/`, and all five CSVs are under `docs/audits/acquisition-blackbox/`. The originals were preserved. See [SHA256SUMS](../acquisition-readiness/baselines/AR-00/SHA256SUMS) for reproducible byte verification.

## 3. Execution order and ledger

Dependencies are intentional. Keep the current/new-result lane ahead of historical backlog. Do not make acquisition GREEN based on source capability or historical coverage alone.

| ID | Bounded responsibility | Production/behavioral dependency (see offline rule) | Current status |
|---|---|---|---|
| AR-00 | Preserve and index read-only production baseline | Audit complete | COMPLETE — [audit and index evidence](../acquisition-readiness/chunks/AR-00.md) |
| AR-01.1 | Reconcile production clone schema and migrations 069–075 | AR-00 | COMPLETE — [clone rehearsal and recovery evidence](../acquisition-readiness/chunks/AR-01.1.md) |
| AR-01.2 | Prepare immutable C28.1 release, config and rollback package | AR-01.1 | PREPARED — [verified package](../acquisition-readiness/chunks/AR-01.2.md), [independent ACCEPT / PASS](../acquisition-readiness/baselines/AR-01.2/independent-review.md) |
| AR-01.3 | Activate cleaned release through bounded production canary | AR-01.2 + exact production authorization | COMPLETE — unchanged A/B/C PASS plus minutes0/5/15 read-only series PASS; [formal closure](../acquisition-readiness/chunks/AR-01.3-parent-acceptance-20261008.md) |
| AR-02.1 | Repair and prove NSE snapshot validation for result discovery | Retained source inputs for offline; AR-01.3 + reviewed patched release for activation | PREPARED — offline COMPLETE, authentic CSV replay PASS; production activation NOT_STARTED; [evidence](../acquisition-readiness/chunks/AR-02.1.md) |
| AR-02.2 | Resolve BSE 403 path or supported explicit blocked/fallback state | AR-01.3 | NOT_STARTED |
| AR-02.3 | Restore scheduler and prove new/revised result acceptance | AR-02.1, AR-02.2 | NOT_STARTED |
| AR-03.1 | Restore bounded market-cap scope observation and catch-up | AR-01.3 | NOT_STARTED |
| AR-03.2 | Prove inclusive ₹580 crore and below-floor re-entry behavior | AR-03.1 | NOT_STARTED |
| AR-04.1 | Complete automatic durable processing handoff for new content | AR-01.3 | NOT_STARTED |
| AR-04.2 | Prove generic processing, recovery and downstream readiness | AR-04.1 | NOT_STARTED |
| AR-05.1 | Reconcile current-quarter PPT/transcript discovery state | AR-02.3, AR-04.1 | NOT_STARTED |
| AR-05.2 | Close bounded current-quarter failed-link and alternate-source cases | AR-05.1 | NOT_STARTED |
| AR-06.1 | Correct event-aware rating and cutoff query semantics | AR-01.3 | NOT_STARTED |
| AR-06.2 | Resolve publisher/agency aliases and normalization states | AR-06.1 | NOT_STARTED |
| AR-07.1 | Add evidence-backed visual slide/page extraction path | AR-04.1 | NOT_STARTED |
| AR-07.2 | Activate official transcript segmentation/validation path | AR-04.1 | NOT_STARTED |
| AR-08.1 | Implement long-tail document expectation lifecycle | AR-05.1 | NOT_STARTED |
| AR-08.2 | Unify operational state reporting and failure root-cause views | AR-02.3, AR-04.1, AR-05.1 | NOT_STARTED |
| AR-08.3 | Classify and prevent legacy period sentinel misuse | AR-00 | NOT_STARTED |
| AR-09 | Run bounded end-to-end proof campaign and decide GREEN/RED | AR-02 through AR-08 | NOT_STARTED |
| AR-10 | Freeze acquisition interfaces after GREEN | AR-09 GREEN + explicit freeze selection | NOT_STARTED |

AR-00 is a completed audit activity, not evidence that the production service is healthy or ready. Production lane ordering remains in force. Apply the section1 offline rule to every dependency below: a dependency on production completion does not prevent independent offline work. User-authorized multi-chunk offline work may proceed through its scope; production operations retain their own concrete authorization and bounds.

## 4. Global invariants and production boundaries

These apply to every chunk. Add chunk-specific invariants in its evidence record.

1. **One source of truth for live identity.** Before any activation, prove the image digest, source revision, config/environment, schema version, role ownership and active scheduler/worker identity. No mixed old/new owner topology.
2. **No silent production data rewrite.** Preserve accepted financial versions, first-observed and acceptance timestamps, source lineage, original document bytes/hashes, logical artifact IDs, queue history, attempts and campaign evidence.
3. **No old-runtime restart as a shortcut.** C05.3 is not the target release. Do not restart it to make containers show as running.
4. **Migration discipline.** Reconcile filenames, ordering, checksums, dependencies and effects for 069–075 against an audited clone. Do not blindly apply, renumber, replay or skip migrations. Production application is a separately authorized operation with tested rollback/forward-recovery.
5. **Single ownership.** Do not run old and new scheduler, scope monitor, downloader, or processing worker concurrently unless a documented fencing/ownership proof explicitly permits it.
6. **Bounded work.** Use a named canary/cohort, maximum row/content count, time window, concurrency and stop condition for any production catch-up, recovery, extraction or backfill. Preserve new-result priority.
7. **Honest states.** Keep `UNKNOWN`, not issued, not yet published, unavailable, blocked, policy-excluded, reference-only, retryable, terminal and extraction failure distinct. Never manufacture a document, rating event, financial version or success state.
8. **No broad recovery before live lane.** Do not drain historical queues or launch full-corpus extraction to improve headline counts before the current/new-result path is protected and measured.
9. **Record before/after.** Capture bounded before/after counts and row identities, expected deltas, source/runtime identity and evidence. Row-count changes from real new arrivals are not automatically drift.
10. **Stop on guard failure.** Snapshot guards, identity mismatches, unexpected schema/data deltas, queue ownership ambiguity or rollback uncertainty stop the selected chunk. Preserve logs and state; do not bypass the guard to force progress.

This document authorizes documentation and offline preparation only. Production startup, deployment, migrations, exchange/publisher requests, queue mutation, market-cap catch-up, document downloads, reprocessing, sentinel remediation and interface freeze each require their own concrete selected scope and authorization under the applicable runbook/session. An approval for one scope does not authorize the next.

## 5. Chunk definitions and acceptance gates

### AR-00 — read-only production baseline

**Status:** COMPLETE as of 7 October 2026, including repository evidence indexing. [Evidence record](../acquisition-readiness/chunks/AR-00.md); [index](../acquisition-readiness/baselines/AR-00/evidence-index.json); [checksums](../acquisition-readiness/baselines/AR-00/SHA256SUMS). Retained DB main-file hash was reproduced and matched the audit; source integrity/source hash remain audit-recorded observations. Disposable-copy sidecar differences and live-identity limitations are documented in the evidence record.

**Scope:** Read-only audit of runtime roles, schema, acquisition, scope, documents, ratings, processing and failure ledger using a verified disposable DB copy.

**Gate/evidence:** DB copy hash matches source; SQLite integrity and quick checks pass; no WAL recovery; audit report and five supporting evidence artifacts retained. Confirm source paths and SHA256 values in the evidence index. No publisher requests, workers, migrations or data mutation.

**Rollback:** None; read-only. If a copied artifact's provenance/hash cannot be reproduced, mark that evidence item unverified and take a new authorized read-only snapshot before relying on it.

### AR-01.1 — reconcile schema and migrations 069–075 on audited clone

**Status:** COMPLETE, 7 October 2026, clone only. [Evidence record](../acquisition-readiness/chunks/AR-01.1.md); [verification](../acquisition-readiness/baselines/AR-01.1/verification.json); [checksums](../acquisition-readiness/baselines/AR-01.1/SHA256SUMS). Exact schema/ledger effects, row preservation, trigger and baseline recreation passed. Baseline FK violations and non-atomic interruption recovery are documented; neither production release nor production recovery is approved here.

**Purpose:** Establish exactly what the production schema contains and what each pending source migration would change before packaging or activating C28.1.

**Prerequisites:** AR-00 evidence index; clean source revision identified; migration files 069–075 and prior schema manifest captured; disposable production clone with verified source/copy identity.

**Scope:** Offline/read-only comparison first, then disposable-clone migration rehearsal only if that rehearsal is part of the selected subchunk. Inspect migration order, duplicate/conflicting identifiers, preconditions, triggers, indexes, data transforms and downgrade/forward-recovery behavior. Rehearse on a disposable clone and verify post-migration schema and representative invariants. No production database connection that can write.

**Acceptance gate:** A signed-off manifest maps every migration file to its intended schema effect and dependency. Rehearsal succeeds from the audited 068 baseline, or records a precise blocker and revised safe path. Rollback or forward-recovery is demonstrated on disposable state; source hash and row-level invariants remain accounted for. The C28 processing inbox/work schema and trigger are explicitly verified.

**Data invariants:** No accepted financial/document data rewritten or dropped; migration ledger unambiguous; immutable content hashes and logical IDs preserved; no old/new worker writes during rehearsal.

**Rollback:** Discard disposable clone and recreate from verified baseline; retain rehearsal logs, snapshots and migration checksums. Production remains untouched.

**Evidence:** `docs/acquisition-readiness/chunks/AR-01.1.md` plus schema-before/after manifests, migration checksums, clone identity, commands/results and rollback proof.

### AR-01.2 — prepare immutable C28.1 release and rollback package

**Status:** PREPARED, 7 October 2026; user-supplied independent review ACCEPT / PASS recorded against the exact release, source identities and reviewed checksum-index hash `8c629df53e11e78c1ab5331a59f24e80e74df766e211bc30d5073018a9fad02e`. [Review record](../acquisition-readiness/baselines/AR-01.2/independent-review.md). Exact C28.1 OCI candidate built/loaded/verified; all 641 image source files match; 17 container and four host checks pass; 21 roles are explicitly profiled and write-locked. Source-manifest JSON byte hash and canonical projection hash are now distinct, reconciling the concurrent AR-01.3 stop without changing source/image bytes. Independent review gate passed. Production activation NONE; authorization to start AR-01.3 NO. [Evidence](../acquisition-readiness/chunks/AR-01.2.md).

**Purpose:** Produce a reviewable release package for the exact cleaned source, with reproducible identity and compatibility facts.

**Prerequisites:** AR-01.1 complete; C28.1 integration evidence; dependency lock/config inventory; production runtime and data mount map read-only captured.

**Scope:** Build/pin an immutable image from the reviewed C28.1 source. Reconcile environment variables, secrets references, volumes, role commands, health checks, resource limits and startup/shutdown order. Prepare release manifest, migration manifest, activation sequence, rollback/forward-recovery runbook and canary observation plan. Do not start services or apply production migrations.

**Acceptance gate:** Image digest maps to exact source commit/tree; all required roles and config are enumerated; secrets are referenced without disclosure; production data mounts and writable paths are explicit; migration order is validated; previous-compatible release and state-compatibility path are identified; runbook has stop/rollback thresholds and one-owner scheduler/worker transition. Independent review signs the package as `PREPARED`.

**Rollback:** No production change occurred. Discard or rebuild the candidate if digest/source/config mismatch; retain prior release identity as rollback target.

**Evidence:** `docs/acquisition-readiness/chunks/AR-01.2.md`, immutable image digest, source manifest, config/mount map, migration list, health-check output and reviewed rollout/rollback plan.

### AR-01.3 — activate C28.1 through a bounded production canary

**Status:** COMPLETE, 8 October 2026, for the bounded migration/runtime/control-canary gate. A/B/C evidence remains unchanged. The required read-only probes at minutes0,5,15 all pass, each under15 seconds, with identical post-C DB hash, schema075, 76 ledger records, markers, control receipt and stopped owners. No migration, write canary or offline test was repeated; production writes0/source requests0/operational startups0. [Formal closure and raw proof](../acquisition-readiness/chunks/AR-01.3-parent-acceptance-20261008.md). Historical preflight/retry/reconciliation records remain retained; no broader acquisition lane is activated.

**Purpose:** Replace the stopped C05.3 target with the cleaned release safely and establish the real live schema/runtime before lane repairs.

**Prerequisites:** AR-01.2 review passed; exact production activation and migration scope authorized; rollback operator and observation window available; pre-change snapshot and queue ownership baseline captured.

**Scope:** Follow the reviewed deployment runbook exactly. Apply only the authorized migration set, then activate one bounded canary role/cohort before widening. Keep a single scheduler and single owner per queue. Start only needed roles in reviewed order. Do not perform result backfills or historical document drains in this chunk.

**Acceptance gate:** Production reports the exact pinned release, expected schema version and compatible configuration; health checks and role heartbeats pass; no duplicate owners; new writes are limited to expected canary behavior; no accepted history or immutable content has unexpected change; queue and error deltas are within reviewed thresholds through the observation window. Widen only if the authorization/runbook explicitly allows it; otherwise stop at canary.

**Applicability:** For the explicitly selected finite DB inspector/control canary, process exit and application/schema receipts establish finite-process liveness; recurring role heartbeats apply when a scheduler or worker is actually selected. Do not start an unrelated service merely to generate a heartbeat. This clarification preserves operational heartbeat tests and the runbook's three probes at minutes0,5,15. Corrected C's approved two-minute observation passes C only and does not replace that parent test. Generalized business-write recovery is required before enabling real business writes; the completed isolated claim proof uses its reviewed token-fenced forward path.

**Stop/rollback:** Stop on schema drift, identity mismatch, failed health, duplicate scheduling, unexplained queue growth/loss, unexpected data mutation or rollback uncertainty. Roll back to the last compatible image/config following the reviewed procedure. If migrations are not safely reversible, use the rehearsed forward-recovery path; never restore an old image against incompatible schema without evidence. Preserve queued work and classify any in-flight claims.

**Evidence:** `docs/acquisition-readiness/chunks/AR-01.3.md`, release and migration identities, canary cohort, before/after metrics, health/role logs, change record and rollback result or explicit no-rollback reason.

### AR-02.1 — repair NSE snapshot validation

**Status:** PREPARED, 8 October 2026. Offline gate COMPLETE: authentic CSV hashes, original parser/guard replay and controlled sparse/malformed/future-date cases pass; 94 existing tests pass. Production activation NOT_STARTED; the pinned accepted image excludes this patch. The zero-row feed remains held, floor1 unchanged, no override. [Evidence record](../acquisition-readiness/chunks/AR-02.1.md), [reconciliation/replay](../acquisition-readiness/chunks/AR-02.1-evidence-resolution-20261008.md).

**Purpose:** Correct the observed result-snapshot guard rejection without weakening data-integrity protection.

**Offline prerequisites:** Exact retained rejected/known-good bytes and guard evidence, parser/source contract and controlled fixtures. These are satisfied. **Production prerequisites:** AR-01.3's applicable gate passed, reviewed immutable release containing the patch, current identity/ownership/recovery checks and exact selected request/write/cohort bounds. Offline completion does not require production activation.

**Scope:** Diagnose `row_count_floor` and `future_effective_date` findings separately. Determine whether source format/data, parser, stale reference data or guard threshold caused each finding. Make the smallest offline code/config adjustment, with fixtures for valid snapshots, legitimate sparse snapshots, malformed/truncated snapshots and future-dated records.

**Acceptance gate:** Known-good snapshots remain accepted; malformed/truncated snapshots remain rejected; future dates cannot enter accepted history; a legitimate current snapshot passes without a guard bypass. Rejected input remains traceable, and the production gate can be configured/actioned without suppressing an unrelated source failure.

**Proof phases:** Offline parser/guard acceptance is proven using authentic originals plus labelled controlled derivatives, including snapshot effective-date boundaries and rejection-side-effect fixtures. Production acceptance retains the full gate above: the patched release must be deployed within selected bounds, and a legitimate current source snapshot must be shown passing without bypass. Historical June acceptance and a controlled sparse fixture are not a live current-period event. Header-only September bytes remain held; no official no-publication state is inferred. No acceptance assertion or threshold is reduced by separating these phases.

**Data invariants:** No silent snapshot replacement or historical result loss; original source bytes/hash and rejection reason remain recorded; effective-date logic remains point-in-time safe.

**Rollback:** Restore prior parser/guard configuration and retain rejected snapshot as evidence. If source shape remains ambiguous, keep source explicitly held and proceed only with a documented supported fallback decision in AR-02.3.

**Evidence:** `docs/acquisition-readiness/chunks/AR-02.1.md`, fixture matrix, before/after parser outputs and guard rationale.

### AR-02.2 — resolve BSE access path or explicit blocked/fallback state

**Purpose:** Establish a supported BSE result-access path, or represent the access block as an explicit operational state that does not silently halt unrelated lanes.

**Prerequisites:** AR-01.3 passed; stored 403 response and source policy context retained; permitted official access paths documented.

**Scope:** Classify whether 403 is a temporary/access configuration issue, publisher policy restriction, or unsupported unattended path. Use only permitted official mechanisms. Where access cannot be restored, implement/configure a visible `ACCESS_BLOCKED`/fallback state and operator action; do not evade access controls or disguise it as no results.

**Acceptance gate:** Supported access is proven with bounded official requests and a valid result fixture, or a documented blocked state alerts an operator and does not stop NSE or unrelated acquisition. Retry/cooldown behavior does not hammer the blocked host. No access policy bypass.

**Rollback:** Return to the previous safe hold/circuit state; preserve 403 responses and retry history. Do not switch to an unapproved source.

**Evidence:** `docs/acquisition-readiness/chunks/AR-02.2.md`, access decision, permitted-source proof or explicit block/fallback behavior.

### AR-02.3 — restore scheduler and prove result acceptance lane

**Purpose:** Demonstrate that new and revised results flow from exchange discovery through the accepted financial lane.

**Prerequisites:** AR-02.1 and AR-02.2 gates passed or have explicit supported held/fallback statuses; one scheduler owner verified; Screener queue acceptance contract documented.

**Scope:** Restore the exchange scheduler under C28.1. Use controlled fixtures first, then a bounded live cycle. Trace a new/revised source result through source capture, parse, snapshot validation, company mapping, Screener queue, financial scrape and accepted version. Keep other source failures isolated.

**Acceptance gate:** A controlled result and an eligible live result (when available in the bounded window) are deduplicated and traceable end to end; a revision creates the expected new accepted version without rewriting prior history. Scheduler heartbeat, next run and failure alerts are visible. If no live event occurs, record fixture proof and keep the real-event portion `IN_PROGRESS`; do not claim full live proof.

**Rollback:** Pause only the faulty scheduler/source after preserving its state; maintain single ownership; leave accepted versions and queue history intact. Resume from documented checkpoint after repair.

**Evidence:** `docs/acquisition-readiness/chunks/AR-02.3.md`, trace IDs, source and accepted-version lineage, scheduler run IDs, dedupe/revision outputs and failure isolation proof.

### AR-03.1 — restore bounded market-cap scope observation

**Purpose:** Resume safe, current observations for collection eligibility and process overdue checks without allowing backlog catch-up to starve new results.

**Prerequisites:** AR-01.3 passed; market-cap source and timestamp semantics known; monitor ownership and rate limits explicit; baseline scope export retained.

**Scope:** Activate one C28.1 scope-monitor owner. Define a small batch size, age ordering, source freshness rule, max runtime and pause condition. Process overdue checks in bounded batches; prioritize current/new-result companies. Do not mass-change eligibility from stale or unknown market values.

**Acceptance gate:** Every attempted check records source/time/result; retries and unknowns remain distinct; eligible/below-floor/unknown counts reconcile against row identities; work advances within the cap; monitor restarts without duplicate jobs. Stop before the production write cap if any mismatch occurs.

**Rollback:** Pause monitor and retain cursors/attempts; restore the prior eligibility state only for rows changed by the selected batch if a proven defect is found, with row-level lineage. No blanket reset.

**Evidence:** `docs/acquisition-readiness/chunks/AR-03.1.md`, batch manifest, cap/cursor, row-level delta, source freshness and priority proof.

### AR-03.2 — prove inclusive ₹580 crore and re-entry

**Purpose:** Verify the documented `market_cap >= ₹580 crore` rule, including re-entry from below-floor to eligible.

**Prerequisites:** AR-03.1 passed; exact threshold currency/unit/as-of contract confirmed; safe controlled company fixture or eligible real transition selected; downstream work re-enable contract known.

**Scope:** Exercise values below, equal to and above the threshold, plus unknown/stale values. Demonstrate that a below-floor company crossing the threshold is re-evaluated and intended financial/document work resumes once, idempotently. Use a controlled production-safe fixture if a natural transition is unavailable; do not falsify a real company's market data.

**Acceptance gate:** `<580` is below; `=580` and `>580` are eligible; unknown/stale values do not become eligible by default; a real/controlled below-floor → eligible event is persisted with timestamps and emits the intended work exactly once. If only fixture proof is available, live behavior remains explicitly unproven until observed under production inputs.

**Rollback:** Remove or disable only the controlled fixture and its synthetic work by its recorded IDs; preserve real observations. Revert the selected rule change if boundary cases fail.

**Evidence:** `docs/acquisition-readiness/chunks/AR-03.2.md`, boundary-case outputs, transition trace and downstream queue evidence.

### AR-04.1 — make processing work automatic for new eligible content

**Purpose:** Close the handoff gap so saving supported eligible content creates durable, idempotent work without manual content-ID or quarter selection.

**Prerequisites:** AR-01.3 passed; migration 072 behavior and schema are reconciled; processing eligibility policy and content/version identity defined; one processing queue/worker owner planned.

**Scope:** Implement the smallest source change needed so content save creates or reconciles a durable inbox item and idempotent processing work with parser/OCR/options versions. Generic worker accepts supported PDF/PPT/HTML/transcript content. Keep quarter/content-scoped selection for finite repair/backfill campaigns only. New-result work must outrank historical backlog.

**Acceptance gate:** Save → inbox → work materialization is crash-safe, repeatable and observable; duplicate save/reconcile does not duplicate work; failed worker claim is recoverable; unsupported content is classified; original/derivative handling remains correct. A newly acquired eligible content item needs no operator-supplied quarter or ID to reach queued state.

**Data invariants:** Content bytes/hash and artifact identity immutable; extraction cache key includes input content hash plus parser/OCR/options versions; no bulk enqueue of the 44,434-item backlog in this chunk.

**Rollback:** Disable the new producer/worker path while preserving inbox/work rows and making no deletion of pending work. Resume the compatible prior path only if it cannot create duplicate ownership; otherwise hold and reconcile claims before restart.

**Evidence:** `docs/acquisition-readiness/chunks/AR-04.1.md`, migration/contract review, crash-window/idempotency cases, queue proof and bounded canary plan.

### AR-04.2 — prove processing to downstream readiness

**Purpose:** Verify generic extraction and downstream readiness for newly saved content and controlled recovery cases.

**Prerequisites:** AR-04.1 passed; worker health, resource limits, priority and stop controls established.

**Scope:** Process a small representative cohort across supported types (PDF, PPT, HTML, official transcript), including one recoverable failure. Keep historical content out unless explicitly selected as a finite campaign. Validate normalization/segmentation only where that content type requires it.

**Acceptance gate:** Each test content has a traceable durable work state, extraction result/version, evidence or honest failure/unknown status, and terminal readiness decision. Restart and retry behavior is idempotent. No content is marked ready solely because bytes exist or extraction returned empty output.

**Rollback:** Pause worker, preserve claims and outputs, return queued work safely to retryable state using fencing semantics; do not delete partial evidence.

**Evidence:** `docs/acquisition-readiness/chunks/AR-04.2.md`, content IDs/hashes, worker traces, extraction outputs and state transitions.

### AR-05.1 — reconcile current-quarter PPT/transcript discovery state

**Purpose:** Repair expectation/artifact state inconsistencies and establish an actionable current-quarter discovery list.

**Prerequisites:** AR-02.3 and AR-04.1 passed; audit cohort and expectation export retained; reporting period and official document classification confirmed.

**Scope:** Bound work to the current-quarter cohort selected in the execution record. Reconcile discovered artifacts, source links and expectation statuses; specifically investigate the audited transcript inconsistency where discovered artifacts coexist with zero `available` expectations. Split candidates into discovery failure, uncovered source, pending download, not yet published, and downloaded-needs-extraction states.

**Acceptance gate:** Every selected expectation has a lineage-backed state and next action or an honest non-action state; artifact/expectation mappings are explainable; no duplicate logical artifacts are created; no not-yet-published item is mislabeled failure. Record remaining cohort counts by root cause.

**Rollback:** Revert only erroneous expectation/status updates from the captured row-level before image; preserve artifact IDs, links and attempt history. Do not delete candidate artifacts.

**Evidence:** `docs/acquisition-readiness/chunks/AR-05.1.md`, selected cohort manifest, before/after reconciliation and category counts.

### AR-05.2 — close bounded current-quarter recovery cases

**Purpose:** Prove the current-quarter failed-link recovery funnel and same-artifact attachment.

**Prerequisites:** AR-05.1 passed; approved official sources and request limits documented; canonical downloader/storage and artifact matching behavior known.

**Scope:** Select a small, representative set: stale URL refresh, official alternate source (BSE/NSE/issuer IR/event source as applicable), and an unresolved case. Use special/operator recovery only where permitted and recorded. All recovered copies for the same document attach to the same logical artifact; original bytes/source provenance and each attempt remain distinct.

**Acceptance gate:** Refresh precedes alternate-source lookup; recovered content passes identity, company, period and document-type checks; download/hash/storage completes through canonical path; one unresolved case remains truthfully blocked/unknown when no official content is found. No bulk cohort retry.

**Rollback:** Stop recovery on identity ambiguity or source policy issue; retain attempted URL and response evidence; detach only a proven misassociation using a lineage-aware correction record, never delete original content.

**Evidence:** `docs/acquisition-readiness/chunks/AR-05.2.md`, per-artifact recovery trace, source policy, hashes and logical artifact IDs.

### AR-06.1 — correct event-aware rating semantics

**Purpose:** Ensure ratings are modeled as events and queried as-of a cutoff, not expected every result quarter.

**Prerequisites:** AR-01.3 passed; current rating event/document tables and query callers inventoried; point-in-time availability contract recorded.

**Scope:** Implement/repair event identity and cutoff selection so research gets latest valid rating action available before cutoff plus the prior valid event. Preserve `NO_NEW_RATING_ACTION` when no new action occurred. Keep rating source/document retrieval separate from quarterly result expectations.

**Acceptance gate:** Multiple same-quarter events, no event, invalid/withdrawn event, late publication and cutoff-boundary fixtures select correctly. No after-cutoff report leaks into history. No-new-action never becomes missing/bad-company/negative signal. Prior history remains immutable.

**Rollback:** Restore prior query path behind a compatibility boundary while preserving event records; do not convert existing events to quarterly rows as rollback.

**Evidence:** `docs/acquisition-readiness/chunks/AR-06.1.md`, query fixtures, caller inventory and point-in-time traces.

### AR-06.2 — resolve agency identity and normalization

**Purpose:** Make publisher identity, rating agency identity and normalized rating facts explicit.

**Prerequisites:** AR-06.1 passed; alias inventory and unresolved/manual-review population retained; publisher-original/derivative provenance available.

**Scope:** Resolve India Ratings being joined through publisher `fitch` and Acuité represented via `smera` through explicit, versioned mappings. Separate publisher, agency, report issuer and document class. Revisit unknown-class HTML and only promote rating facts with validated issuer/event identity and evidence. Preserve original HTML and generated PDF derivatives as separate origins.

**Acceptance gate:** Mapping is deterministic, auditable and reversible; no silent aliases; known examples map correctly; ambiguous documents remain `MANUAL_REVIEW`/unknown; structural extraction `ok` does not alone imply accepted rating facts.

**Rollback:** Revert mapping version and reclassify only records changed by that version; preserve evidence and derivatives.

**Evidence:** `docs/acquisition-readiness/chunks/AR-06.2.md`, alias table/version, labeled sample, changed-row manifest and normalization proof.

### AR-07.1 — add visual slide/page extraction with evidence

**Purpose:** Provide a visual path for chart/image-heavy PPT slides and relevant document pages while retaining source evidence coordinates.

**Prerequisites:** AR-04.1 passed; target document formats and rendering dependencies/licensing/cost constraints reviewed; evidence envelope contract fixed.

**Scope:** Implement an offline/rendered-image extraction path for a bounded representative set. Preserve native text/table extraction and add rendered slide/page evidence, slide/page number and region/coordinates where practical. No bulk visual processing.

**Acceptance gate:** Selected chart/image facts cite source content hash, page/slide and visual evidence region; numeric/unit extraction is independently validated; unreadable charts return unknown/low-confidence/manual review instead of invented values. Native text and visual claims remain attributable and do not silently overwrite one another.

**Rollback:** Disable visual extraction dispatch while retaining rendered evidence and versioned outputs; deterministic text extraction remains available.

**Evidence:** `docs/acquisition-readiness/chunks/AR-07.1.md`, test corpus manifest, render config/version, evidence examples, accuracy review and cost/runtime notes.

### AR-07.2 — activate official transcript segmentation and validation

**Purpose:** Turn extracted official transcript text into validated segments with speaker/Q&A evidence.

**Prerequisites:** AR-04.1 and AR-04.2 passed for official transcript extraction; transcript schema and identity matching reviewed.

**Scope:** Segment a bounded official-transcript sample into sections/speakers/Q&A and write validation outcomes. Recording download and machine transcription remain opt-in under a separate research policy; this chunk does not bulk-download recordings.

**Acceptance gate:** Company, event/quarter, publication and document identity are preserved; section boundaries and speaker labels carry text offsets/evidence; uncertain speaker attribution remains unknown; validation failure does not mark transcript ready. Production transcript tables receive records only within separately authorized activation scope.

**Rollback:** Disable segmenter and retain versioned outputs/validation records; do not remove extracted transcript text or overwrite source bytes.

**Evidence:** `docs/acquisition-readiness/chunks/AR-07.2.md`, bounded transcript list, segmentation examples, validation results and policy statement.

### AR-08.1 — implement long-tail document expectation lifecycle

**Purpose:** Replace the six-week dead end with early retry, long-tail waiting and a next-quarter final check.

**Prerequisites:** AR-05.1 passed; due-item scheduler semantics and reporting states documented.

**Scope:** Add explicit lifecycle states and bounded due scheduling: early retries → long-tail pending → next-quarter final check → found/downloaded or honest final/unknown state. Do not poll indefinitely or repeatedly query every company.

**Acceptance gate:** Due-only scheduling, retry caps, backoff and next-quarter final check are deterministic and restart-safe; late publication is found; not-found remains distinct from technical failure and not-yet-published; new-quarter work has priority. Existing statuses transition without erasing attempt lineage.

**Rollback:** Revert scheduler/state transition version while preserving due dates and attempts; requeue affected items based on stored transition history.

**Evidence:** `docs/acquisition-readiness/chunks/AR-08.1.md`, state transition table, clock-driven fixtures and bounded production cohort proof.

### AR-08.2 — unify operational black-box reporting

**Purpose:** Make every operational number point to a lane, root cause, truth category and next action.

**Prerequisites:** AR-02.3, AR-04.1 and AR-05.1 passed; lane/state dictionaries and root-cause rules agreed.

**Scope:** Report the minimum categories: runtime/release; exchange/snapshot; Screener ingestion; scope; discovery; download/access; processing/extraction; rating recovery/normalization; transcript structuring; integrity/sentinel; policy/manual review. Separate actionable failure, backlog, honest absence, not-yet-published, policy exclusion/reference-only, access block and manual review.

**Acceptance gate:** Counts reconcile to bounded source rows; examples link to underlying records; retryable vs terminal vs held states are distinguishable; common root cause groups avoid double-counting one issue as many bugs; dashboard/report is useful without implying all historical backlog is urgent.

**Rollback:** Retain previous report path and snapshot exports; restore prior reporting version if reconciliation fails. Reporting changes do not mutate source operational state.

**Evidence:** `docs/acquisition-readiness/chunks/AR-08.2.md`, state mapping, sample report, count reconciliation and operator action notes.

### AR-08.3 — classify and contain the 1970-01-01 sentinel

**Purpose:** Ensure legacy `1970-01-01` values cannot be interpreted as real reporting periods, using lineage-aware remediation.

**Prerequisites:** AR-00 evidence plus row-level lineage exports for the 3,277 artifact-event and 4,742 Screener queue occurrences; all consumers of period fields inventoried.

**Scope:** Trace origin and use of each sentinel population; define explicit unknown/legacy-period classification and prevent sentinel from period matching, quarter assignment, cutoff queries or ML feature construction. Prepare row-level remediation/quarantine plan; mutate production only in a separately selected and authorized data-fix subchunk.

**Acceptance gate:** Automated/query-level guards exclude sentinel from real period logic; fixtures show legacy rows remain discoverable as legacy/unknown; remediation has expected row IDs and reversibility; no blind mass update or inferred date substitution.

**Rollback:** Revert query/guard change if it breaks valid period selection; for any authorized row changes, restore by row-level before image and lineage event. Never restore a sentinel as a real date.

**Evidence:** `docs/acquisition-readiness/chunks/AR-08.3.md`, consumer inventory, row-level classification manifest, guard proof and any separate authorization record.

### AR-09 — bounded end-to-end acquisition proof campaign

**Purpose:** Prove the complete path needed for acquisition readiness using representative, traceable cases.

**Prerequisites:** AR-01 through AR-08 selected gates passed; release/runtime/schema current; operational dashboards and stop/rollback controls live; campaign cohort and bounds reviewed.

**Scope:** Run a finite campaign covering: (1) normal company-quarter; (2) late-publication PPT or transcript; (3) alternate official-source recovery; (4) new/revised result through accepted financials; (5) controlled/real inclusive ₹580 re-entry; (6) automatic content → durable processing → extraction/structuring/readiness; (7) rating latest-valid-before-cutoff plus prior event and no-new-action case. Each case has explicit maximum records, time, concurrency and source request budget.

**Acceptance gate:** Each case reaches its declared terminal state with complete lineage from discovery/source to accepted financial/document evidence and readiness. No manual data rescue. Unknown/blocked/not-issued cases remain honest. Expected queue/data deltas reconcile; production remains on pinned C28.1; no duplicate scheduler/worker; all required freeze criteria in the master plan are evidenced. Any unmet item keeps gate RED and produces a bounded follow-up chunk.

**Rollback:** Stop campaign; preserve checkpoint/claims and accepted history; leave successfully acquired immutable content and lineage intact; pause only campaign-owned work. Do not delete partial but valid evidence. Resume only from reconciled checkpoint.

**Evidence:** `docs/acquisition-readiness/chunks/AR-09.md`, campaign manifest, case trace IDs, before/after reconciliations, release/schema identity, decision record GREEN/RED and remediation links.

### AR-10 — freeze acquisition interfaces after GREEN

**Purpose:** Record stable contracts and handoff to Document Intelligence V1 only after the acquisition gate is fully green.

**Prerequisites:** AR-09 explicitly GREEN; every freeze acceptance condition in the master plan links to evidence; unresolved limitations are listed and judged non-blocking under that plan; freeze selection/authorization recorded.

**Scope:** Freeze versioned interfaces for accepted financial results, company/period/scope, logical artifacts, immutable content, processing/readiness, rating events/cutoffs, transcript structures and evidence envelopes. Record compatibility policy, deprecation rules, owners and change-control process. Do not build V1 extraction/model features in this chunk.

**Acceptance gate:** A traceable acceptance matrix maps every criterion to evidence and accountable interface; consumers can use versioned contracts without acquisition-table coupling; known limitations and safe extension process are documented. Master-plan gate marked GREEN only with complete evidence.

**Rollback:** Reopen a versioned interface only through a documented compatibility change; preserve prior contract and migration path. Freeze does not erase source history or approve broad reprocessing.

**Evidence:** `docs/acquisition-readiness/chunks/AR-10.md`, signed acceptance matrix, interface schemas/version IDs, owner map and handoff note.

## 6. Acquisition GREEN acceptance matrix

AR-09 cannot pass until each requirement is backed by a linkable proof artifact. A code review or unit fixture alone cannot satisfy a live production criterion; where the master plan allows controlled proof, label it clearly and retain any remaining live observation as a limitation.

| Criterion | Required evidence owner |
|---|---|
| Immutable cleaned release is the actual production release; no silent production data rewrite | AR-01.2 / AR-01.3 |
| Production schema and release identity known; migrations reconciled | AR-01.1 / AR-01.3 |
| Exchange discovery runs and new/revised result enters accepted financial lane | AR-02.1–AR-02.3 |
| BSE/NSE source failures resolved or explicit supported fallback/block state | AR-02.1 / AR-02.2 |
| Inclusive ₹580 rule documented and proven, including below-floor → eligible re-entry | AR-03.1 / AR-03.2 |
| Newly saved eligible documents automatically enter durable processing | AR-04.1 / AR-04.2 |
| Current-quarter PPT/transcript discovery and alternate official recovery work; same logical artifact retained | AR-05.1 / AR-05.2 |
| Rating cutoff yields latest valid event plus prior; no-new-action is explicit | AR-06.1 |
| Publisher and agency identities are explicit and unambiguous | AR-06.2 |
| HTML originals/derivatives preserve provenance; unknown/non-report pages do not become accepted rating facts | AR-06.2 |
| Official transcript extraction proceeds to segmentation/validation; recording fallback is policy-controlled | AR-07.2 |
| Relevant PPT chart/image facts have visual evidence path | AR-07.1 |
| `1970-01-01` cannot be interpreted as a real period; historical meaning documented safely | AR-08.3 |
| Operations distinguish failures, backlog, honest absence, not-yet-published, policy, access and manual-review states | AR-08.2 |
| Normal, late-publication and alternate-recovery cases reach ready state without manual data rescue | AR-09 |

## 7. Failure classification and decision rule

Group work by root cause and lane before selecting fixes. Do not work the raw 82,972 ledger rows as independent tickets.

```text
RUNTIME / RELEASE
EXCHANGE SOURCE / SNAPSHOT VALIDATION
SCREENER INGESTION
COLLECTION SCOPE
DOCUMENT DISCOVERY
DOCUMENT DOWNLOAD / ACCESS
PROCESSING / EXTRACTION
RATING RECOVERY
RATING NORMALIZATION / IDENTITY
TRANSCRIPT STRUCTURING
DATA INTEGRITY / LEGACY PERIOD SENTINELS
POLICY / MANUAL REVIEW STATES
```

For each finding:

```text
correct in code and live → prove and close/document
code exists but release older → deploy/prove before redesign
behavioral gap in cleaned source → smallest targeted implementation
historical backlog → finite recovery/backfill campaign after live lane is safe
honest absence or policy state → preserve truth and report it
ambiguous legacy row → classify/quarantine/remediate with lineage
```

Normal `NOT_ISSUED`, `NOT_YET_ISSUED`, reference-only and policy-excluded states are not technical failures. Point-in-time claims must use source publication/observation availability, not a later backfill timestamp represented as historical availability.

## 8. Operational proof and release reporting

Every production-affecting subchunk records the following before any change:

- exact source commit/tree, image digest, configuration identity, schema/migration version and active role map;
- exact authorized service/role/cohort, operation, data scope, time window and maximum request/write/processing counts;
- source DB/snapshot identity and bounded row/content IDs; immutable content hashes when documents are involved;
- one-owner proof for scheduler, monitor and queue workers;
- stop thresholds, operator on-call, rollback/forward-recovery path and in-flight work treatment.

After the change, record actual values and link raw evidence. Compare state by stable IDs and expected transitions, not only aggregate counts. Label each claim `SOURCE_ONLY`, `CLONE_REHEARSAL`, `CONTROLLED_FIXTURE`, `PRODUCTION_CANARY` or `PRODUCTION_LIVE_EVENT` so code capability is never confused with deployed behavior or live coverage.

## 9. Per-subchunk evidence template

Create one evidence file for each selected subchunk at `docs/acquisition-readiness/chunks/AR-xx.y.md` (or the corresponding AR-xx path):

```markdown
# AR-xx.y — selected responsibility
Status: NOT_STARTED / IN_PROGRESS / PREPARED / COMPLETE / BLOCKED / DEFERRED / SUPERSEDED
Offline gate: NOT_STARTED / IN_PROGRESS / COMPLETE / BLOCKED / not applicable
Production activation: NOT_STARTED / exact selected scope and result / not applicable
Production acceptance: pending criteria / COMPLETE with evidence / not applicable
Reused verified evidence and exact identity; changed inputs requiring new proof:
Selected authorization and exact scope:
Baseline source commit/tree + uncommitted snapshot manifest:
Baseline image/config/schema/runtime identity (if applicable):
Baseline data snapshot and bounded row/content manifest:
Prerequisites checked:
Allowed paths/change types + forbidden paths:
Behavior preserved:
Deliberate behavior change:
Data/queue invariants and expected deltas:
Production activation: none / exact approved scope and record:
Canary/cohort, caps, observation window and stop thresholds:
Commands/procedures and actual outputs:
Verification results and acceptance gate:
Evidence paths + checksums:
Rollback/forward-recovery and pending-work treatment:
Known limitations / unresolved root causes:
Next single smallest chunk:
```

Do not mark a chunk complete before its evidence file exists and its gate is met. Record blockers as specific missing prerequisites (for example, unsupported BSE access policy or unavailable live revised result), not generic “needs investigation.”

## 10. Change ledger

| Date | Chunk | Status change | Evidence | Decision / next selected chunk |
|---|---|---|---|---|
| 2026-10-07 | AR-00 | Audit COMPLETE; evidence index pending | 7 Oct production black-box audit and five supporting files; exact paths/hashes to index | Index artifacts, then select AR-01.1 |
| 2026-10-07 | AR-00 | Evidence index pending → COMPLETE | [AR-00 record](../acquisition-readiness/chunks/AR-00.md), [six-artifact index](../acquisition-readiness/baselines/AR-00/evidence-index.json), [SHA256SUMS](../acquisition-readiness/baselines/AR-00/SHA256SUMS) | Retained DB hash matches audit; production untouched. AR-01.1 next, not selected |
| 2026-10-07 | AR-01.1–AR-10 | NOT_STARTED | This execution plan only | No production or data-changing work is authorized by this entry |
| 2026-10-07 | AR-01.1 | NOT_STARTED → IN_PROGRESS → COMPLETE | [Clone rehearsal record](../acquisition-readiness/chunks/AR-01.1.md), [verification](../acquisition-readiness/baselines/AR-01.1/verification.json) | User selected AR-01; first subchunk completed. 069–075 applied on clone; 148 original tables unchanged; baseline restored exactly. 10,771 preexisting FK violations preserved; partial-migration failure/recovery documented. Stop for review; AR-01.2 next, not selected |

| 2026-10-07 | AR-02.1 | NOT_STARTED → offline preparation → BLOCKED | [AR-02.1 record](../acquisition-readiness/chunks/AR-02.1.md), [stored evidence](../acquisition-readiness/baselines/AR-02.1/stored-evidence.json), [94 passing tests](../acquisition-readiness/baselines/AR-02.1/tests.log) | User selected AR-02. Prepared IST observation-date fix; empty feed remains rejected. AR-01.3 prerequisite unmet and original CSV unavailable locally. AR-02.2/AR-02.3 untouched; next prerequisite is AR-01.2. Production untouched. |

| 2026-10-07 | AR-01.3 | NOT_STARTED → read-only preflight → BLOCKED | [AR-01.3 record](../acquisition-readiness/chunks/AR-01.3.md), [preflight](../acquisition-readiness/baselines/AR-01.3/preflight.json) | User selected AR-01.3. AR-01.2 independent review/evidence missing and source-manifest hash mismatches candidate manifest. OCI archive hash matches; 15 project containers stopped. Production untouched; finish/reconcile AR-01.2 before resuming activation. |

| 2026-10-07 | AR-01.2 | NOT_STARTED → offline package verified → BLOCKED at independent review | [Package record](../acquisition-readiness/chunks/AR-01.2.md), [release manifest](../../ops/docker/release/c28.1/release-manifest.json), [verification](../acquisition-readiness/baselines/AR-01.2/package-verification.json) | Exact C28.1 immutable OCI verified; 641 source hashes, 17 container + four host tests pass; 21 write-locked roles, zero defaults; JSON/projection hash ambiguity reconciled. Independent signature required for PREPARED. Production untouched; AR-01.3 not resumed. |

| 2026-10-07 | AR-01.2 | BLOCKED at independent review → PREPARED | [Independent ACCEPT / PASS](../acquisition-readiness/baselines/AR-01.2/independent-review.md), [accepted checksum index](../acquisition-readiness/baselines/AR-01.2/reviewed-package/SHA256SUMS), [package record](../acquisition-readiness/chunks/AR-01.2.md) | User supplied independent acceptance for exact c28.1-ar012-20261007 identities and checksum-index SHA256 8c629df53e11e78c1ab5331a59f24e80e74df766e211bc30d5073018a9fad02e; all 43 hashes reproduced. Exact reviewed package retained. Production activation NONE; AR-01.3 authorization NO and all preflight conditions still required. |

| 2026-10-07 | AR-01.3 retry | BLOCKED → reselected preflight → BLOCKED | [Retry evidence](../acquisition-readiness/chunks/AR-01.3-retry-20261007.md) | Historical checkpoint; later A/B/C proofs supersede its missing preconditions for their bounded scopes. Accepted AR-01.2 checksums/source hash passed; no migration occurred in this retry. |
| 2026-10-08 | AR-01.3A / B / C | COMPLETE / PASS for each selected scope; parent closure pending | [A](../acquisition-readiness/chunks/AR-01.3A-pre-migration-snapshot-restore-proof.md), [B](../acquisition-readiness/chunks/AR-01.3B-migration-only-production-activation.md), [corrected C](../acquisition-readiness/chunks/AR-01.3C-attempt-02-execution.md), [reconciliation](../acquisition-readiness/chunks/AR-02.1-evidence-resolution-20261008.md) | Index existing completed proof: fresh snapshot/restore, migrations075 with original-row/FK preservation, control claim/release and two-minute observation. Three-probe 15-minute parent series remains; no operations repeated by this reconciliation. |
| 2026-10-08 | AR-02.1 | BLOCKED offline evidence → PREPARED; offline COMPLETE; activation NOT_STARTED | [Authentic capture and replay](../acquisition-readiness/chunks/AR-02.1-evidence-resolution-20261008.md) | Both original CSV hashes match DB lineage. Known-good4351/1 accepted/rejected counts reproduce; genuine empty feed remains rejected. Patched release/current-result activation proof remains separately pending. |
| 2026-10-08 | Ledger v1.1 | Offline and activation gates separated | Section1 reuse/dependency rules and unchanged chunk acceptance gates | User requested simpler procedures. Independent authorized offline work may proceed; exact verified inputs retain their passed proofs. Fresh activation safety, all original acceptance tests, production ordering and AR-09/10 gates preserved. Stop at this AR-02.1 checkpoint; no later chunk executed. |

| 2026-10-08 | AR-01.3 parent | Outstanding observation → COMPLETE | [Formal closure](../acquisition-readiness/chunks/AR-01.3-parent-acceptance-20261008.md), [final PASS](../acquisition-readiness/baselines/AR-01.3-parent-20261008/final.json) | Exactly three read-only probes at offsets0.211506,300.351345,900.209197s passed. Same DB hash, schema075/ledger/control/markers; all owners stopped. Unchanged A/B/C reused; no migrations, writes, source requests, workers or offline tests repeated. Stop; no next chunk selected. |

## 11. Immediate next smallest step

AR-02.1's **offline gate is COMPLETE**. Its authentic raw-input blocker is closed, and it is PREPARED for separate release/activation review. The real header-only response stays rejected. No additional offline prerequisite needs reopening. Independent offline chunks may proceed when selected by the user under section1; AR-02.2/AR-02.3 remain unexecuted in this session.

AR-01.3 is formally COMPLETE: its original minutes0,5,15 read-only compatibility/identity/stability probes passed, reusing unchanged A/B/C evidence. No parent acceptance blocker remains. Finite-process liveness is covered by command receipts; real scheduler/worker heartbeats remain criteria of their later activation. [Formal closure](../acquisition-readiness/chunks/AR-01.3-parent-acceptance-20261008.md). Stop at this checkpoint; the completion does not select the next production operation.

For AR-02.1 production acceptance, prepare/review a new immutable candidate containing the date patch; the already accepted AR-01.2 image does not contain it. Preserve that accepted package and reuse unchanged migration/restore evidence. A separately selected bounded activation must prove a legitimate current snapshot with the patched release, preserving rejection, traceability and source isolation. Do not interpret historical originals or controlled derivatives as a new live result. The 10,771 historical FK identities and non-atomic migration/recovery constraints remain accounted for. Current session production delta is zero; keep all retained snapshots, original bytes and control receipt.

The acquisition gate remains **RED / NOT READY TO FREEZE** until AR-09 proves every required condition. Document Intelligence V1 and ML execution remain downstream of AR-10.
