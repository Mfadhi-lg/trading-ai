# TRADING AI — DEEP ARCHITECTURE & CLEANUP BLUEPRINT V1

**Baseline:** `trading-ai-share-2026-10-01(7).zip`  
**Purpose:** Restructure the current project for maintainability and scale **without changing what the system can do, without re-scraping existing history, and without redesigning the production database during the cleanup.**

---

## 0. Audit scope and confidence

This review did not treat the repository as a generic "Python cleanup." I structurally scanned every production Python module, measured module/function size and dependency shape, inspected the active Docker/CLI entrypoints, and traced the live acquisition, delayed-document, download, failed-link, alternate-source, rating, and browser-recovery workflows. I also read the current canonical `README.md`, `PLAN.md`, `EXECUTION.md`, `SCRAPPER_2.0.md`, the AMFI design/research notes, the Upstox experiment notes, the root Master Roadmap V4, and compared the A2/B1/B2 review trees against the canonical source.

The codebase is **not a copy-paste spaghetti rewrite candidate**. The structural scan found no exact duplicate production Python modules, no exact duplicate long function implementations, and no internal multi-module circular-import groups. The debt is concentrated in oversized orchestration modules, weak ownership boundaries, code/runtime artifacts spread across the repository, Docker being used as a workflow scheduler, and several independent recovery mechanisms that are valid but not organized under one domain.

---

# 1. Executive diagnosis

The project currently has three different architectures overlaid on one another:

1. **The original scraper architecture** — source adapters → parse → validate → store.
2. **The operational acquisition architecture** — queues, schedulers, retries, result detection, delayed artifacts, download workers, rating workers, reconciliation.
3. **The research platform architecture** — mutual funds, governance, ML contracts, Focus work, document intelligence.

All three are legitimate. The problem is that the physical project structure did not evolve with them.

The correct cleanup is therefore:

> **preserve behavior and data; reorganize ownership; split orchestration; consolidate process classes; make recovery one coherent domain; isolate runtime state; and keep old import paths as temporary compatibility façades while callers migrate.**

The cleanup must **not** be a database rewrite, a new scraper, a new downloader, or a package-wide rewrite.

---

# 2. Current repository anatomy — where the project is scattered

Current root responsibilities are mixed:

```text
trading-ai/
├── scrapper/                         # canonical application code
├── a2-review/scrapper/               # stale near-copy + migration safety patch
├── b1-review/scrapper/               # partial near-copy + feature work + accidental venv
├── b2-review/scrapper/               # Focus work
├── experiments/                      # Upstox feasibility experiment
├── compose.yml                       # production-ish process topology + workflow loops
├── compose.browser-recovery.yml      # finite browser-rating campaign
├── Dockerfile
├── scraper                           # 280-line host control plane shell script
├── scraper-monitor                   # operator alias/wrapper
├── exchange_securities_master.csv    # reference data with hidden UI bootstrap dependency
├── dump_cache.py                     # ad-hoc DB/debug utility
├── test_dedup.py                     # ad-hoc root probe, not canonical test suite
├── test_load_cache.py
├── test_sqlite.py
└── TRADING_AI_MASTER_ROADMAP...
```

The physical problem is not merely "files outside `scrapper`." The root currently owns:

- process lifecycle;
- Docker orchestration;
- browser-recovery lifecycle;
- reference data;
- debug scripts;
- review worktrees;
- experiments;
- runtime state paths;
- project documentation.

At the same time, `scrapper/` owns application code, operations, command handlers, pipeline orchestration, persistence, source adapters, research, monitoring and UI.

That means there is no clean answer to:

> "Where does runtime orchestration belong?"

or:

> "Where does document recovery belong?"

The cleanup has to establish those answers.

---

# 3. Current canonical package — size and pressure points

The canonical package contains roughly **171 production Python modules and ~56k production LOC**, plus ~22k test LOC.

The largest concentration of code is:

```text
pipeline/      ~14.4k LOC
db/            ~12.3k LOC
sources/        ~8.8k LOC
documents/      ~5.5k LOC
exports/        ~3.8k LOC
view/           ~2.9k LOC
commands/       ~2.7k LOC
validation/     ~2.1k LOC
```

This tells us something important: **`pipeline/` has become the catch-all application/service layer and `db/` has become a second catch-all.**

The original project plan says modules should normally stay below roughly 400 lines. The current tree has dozens above that threshold. The worst examples include:

```text
cli.py                              ~2103
db/exchange_repo.py                 ~1583
sources/screener/company.py         ~1577
pipeline/build_p0_governance.py     ~1536
db/document_recovery_repo.py        ~1415
db/mf_workbook_repo.py              ~1205
validation/screener.py              ~1077
validation/trendlyne.py             ~1004
view/shell.py                        ~844
db/screener_discovery_repo.py        ~821
documents/downloader.py              ~788
db/repo.py                           ~764
pipeline/scheduler.py                ~747
pipeline/screener_vertical.py        ~739
commands/browser_rating_recovery.py  ~690
pipeline/document_download_recovery  ~658
pipeline/run_document_download...    ~640
```

This is not evidence that the algorithms are bad. It means modules have absorbed too many responsibilities.

---

# 4. The actual financial-result lifecycle today

There are **two result-discovery entry paths** that converge on the same Screener company queue.

```text
PATH A — SCREENER RESULT SCREEN
Screener result-screen discovery
        │
        ▼
identify company + result period
        │
        ▼
screener_company_queue
```

```text
PATH B — OFFICIAL EXCHANGE RESULT FEED
Exchange scheduler
        │
        ▼
BSE/NSE result filing discovery
        │
        ▼
resolve company / Screener path
        │
        ▼
screener_company_queue
```

Then both converge:

```text
screener_company_queue
        │
        ▼
Screener company worker
        │
        ├── fetch company page
        ├── fetch peers
        ├── fetch quick ratios
        └── optional commentary
        │
        ▼
store raw fetch/snapshot/evidence
        │
        ▼
parse
        │
        ▼
validate
        │
        ├── rejected → retain evidence/reason
        │
        └── accepted
              │
              ▼
        persist accepted financial version
              │
              ▼
        update collection/period state
              │
              ▼
        create/promote supporting-document links
```

This is conceptually good and should survive cleanup unchanged.

### Important nuance

The continuously running "new result" trigger today is primarily the exchange scheduler path. Screener result-screen discovery exists but is configured as a manual/on-demand Compose profile. Screener remains the main rich financial ingestion source after a company/period has been queued.

The cleaned architecture should make this explicit:

> **Result event detection is one capability with multiple producers; financial ingestion is another capability.**

Neither producer should own the entire downstream workflow.

---

# 5. Screener ingestion — what should be preserved and what should be split

`pipeline/screener_vertical.py` currently combines:

- HTTP/fetch orchestration;
- raw-response persistence;
- parse calls;
- validation;
- acceptance/version persistence;
- company/period checks;
- document promotion/queuing;
- export/report side effects.

The main `ingest_screener_response()` function is almost 300 lines.

The behavior is valuable. The boundary is not.

Target:

```text
workflows/screener/
├── fetch.py
├── ingest.py
├── acceptance.py
├── worker.py
└── result_events.py
```

with the source parser remaining under:

```text
sources/screener/
```

The service should read almost like:

```text
fetch evidence
→ parse
→ validate
→ persist accepted/rejected observation
→ publish/update quarter state
→ invoke document-link handling
```

The current behavior must be retained; only the ownership changes.

---

# 6. Screener parser — a major structural split candidate

`sources/screener/company.py` is ~1,577 lines and has absorbed nearly the entire Screener company-page domain.

It currently contains, in one file:

- source dataclasses / parsed-company model;
- identity/quote parsing;
- ratio parsing;
- quick-ratio parsing;
- commentary;
- company profile;
- pros/cons;
- statement parsing;
- growth parsing;
- shareholding parsing;
- document-link parsing;
- final page assembly.

The original project plan anticipated a much cleaner split. Recreate that split without changing parse behavior:

```text
sources/screener/
├── models.py
├── identity.py
├── metrics.py
├── profile.py
├── statements.py
├── growth.py
├── shareholding.py
├── document_links.py
└── company.py            # compatibility façade + assembly only
```

`company.py` should temporarily re-export old public symbols so current callers keep working while imports migrate.

This is a high-value refactor because it reduces edit blast radius while keeping source semantics identical.

---

# 7. Delayed supporting documents — the part that implements the user's intended lifecycle

The delayed-artifact design is one of the stronger parts of the current project and **must not be simplified away**.

The existing logic is effectively:

```text
accepted quarter/result
        │
        ▼
create artifact event / expectations
        │
        ▼
is expected document available now?
        │
     ┌──┴──┐
    yes    no
     │      │
     ▼      ▼
promote    next_check_at
 link         │
     │        ▼
     │    artifact reconciler
     │        │
     │        ▼
     │    re-fetch Screener in
     │    DOCUMENT-ONLY mode
     │        │
     │        ├── link appeared → promote
     │        └── still absent → schedule next due check
     │
     ▼
download queue
```

The critical design detail is **document-only re-fetch**. The reconciler may revisit a company page to discover late PPT/transcript links, but it does not replace the accepted financial observation. It verifies company, period and standalone scope, then updates document state.

That is exactly the behavior needed for:

> result now, supporting documents later, next quarter eventually arrives.

Preserve this logic and move it under one obvious ownership area:

```text
documents/reconciliation/
```

or:

```text
workflows/documents/reconciliation.py
```

Do not bury it inside generic scraper orchestration.

---

# 8. Document link promotion and normal download flow

The current path is:

```text
source-specific document link
        │
        ▼
promote_document_links
        │
        ▼
logical artifact/event
        │
        ▼
accepted-document queue policy
        │
        ├── PPT → queued
        ├── official transcript → queued
        ├── credit rating → rating-specific path
        └── recording → metadata first / not automatically downloaded
        │
        ▼
download queue
        │
        ▼
generic document worker
        │
        ├── claim
        ├── host fairness/throttle
        ├── request/download
        ├── media validation
        ├── content hash
        ├── immutable persistence
        ├── HTML→PDF derivative where appropriate
        ├── update logical artifact
        ├── recovery reconciliation
        └── retry/terminal outcome
```

This is good reuse.

The problem is that `run_document_download_worker.py` now owns too much of this in one function/module.

Split **the orchestration**, not the semantics:

```text
documents/download/
├── models.py
├── transport.py
├── media_validation.py
├── persistence.py
├── retry_policy.py
├── host_health.py
└── post_success.py

workflows/documents/
└── download_worker.py
```

The worker should become:

```text
claim → execute download contract → persist outcome → run post-success/repair hooks
```

This will allow normal downloads and recovery downloads to share execution without forcing them into identical queue semantics.

---

# 9. The recovery subsystem — it is scattered, but it is NOT one duplicate feature

This is the most important cleanup distinction.

There are several recovery paths because there are several fundamentally different failure modes. Deleting them as "duplicates" would damage capability.

They need to become one **recovery domain with named strategies**.

---

## 9A. Late-publication reconciliation

Current ownership:

- `db/artifact_reconciliation_repo.py`
- `pipeline/reconcile_artifacts.py`
- result-artifact/expectation helpers.

Problem being solved:

> The document may not exist yet.

This is **not a failed download**.

Target:

```text
documents/reconciliation/
├── expectations.py
├── schedule.py
└── service.py
```

Preserve due-row scheduling and source-friendly retry cadence. No blind universe rescans.

---

## 9B. Failed/stale URL recovery

Current:

`pipeline/document_download_recovery.py`

Capabilities include:

- recovery report;
- requeue failed downloads;
- register replacement URL;
- import manual replacement document.

Problem:

> We know the logical artifact, but the current URL/download path failed.

Target:

```text
documents/recovery/failed_download.py
```

Keep explicit/audited operator actions.

---

## 9C. Alternate official-source recovery

Current implementation spans:

```text
documents/recovery.py
documents/recovery_matching.py
sources/documents/bse_recovery.py
sources/documents/nse_recovery.py
sources/documents/issuer_ir_recovery.py
sources/documents/debt_recovery.py
db/document_recovery_repo.py
pipeline/alternate_source_recovery.py
exports/document_alternate_source_recovery.py
```

Problem:

> The direct/original source is unusable or missing, but the same document may be available from BSE/NSE/issuer IR/other approved evidence.

This subsystem is conceptually correct because recovered files are attached to the **same logical artifact** and sent through the existing downloader instead of creating a second corpus.

Target:

```text
documents/recovery/
├── models.py
├── matching.py
├── alternate_sources.py
└── policies.py

sources/documents/
├── bse.py
├── nse.py
├── issuer_ir.py
└── debt.py

db/documents/recovery/
├── targets.py
├── leases.py
├── attempts.py
├── candidates.py
└── reconciliation.py
```

`db/document_recovery_repo.py` at ~1,415 lines is one of the highest-value persistence splits.

---

## 9D. Saved-Screener-page quarter-link repair

Current:

`commands/recover_quarter_document_links.py`

Problem:

> A saved Screener response has valid document links, but the full financial parse did not meet acceptance/manifest requirements.

The repair correctly avoids accepting bad financial data merely to recover the links. It independently verifies accepted company/period evidence and promotes document links.

This is a useful surgical repair and should remain.

Target:

```text
documents/recovery/saved_screener_page.py
```

with only a thin CLI wrapper.

---

## 9E. Retry/failure diagnostics

Current:

`commands/diagnose_retryable_failures.py`

This is not an acquisition engine. It is an operations/reporting tool that classifies failures and recommends recovery actions.

Target:

```text
operations/recovery_reports.py
```

Do not mix it into worker execution.

---

## 9F. Quarter readiness / repair tooling

Current:

`commands/quarter_documents.py`

This performs quarter-level readiness/inventory/repair decisions and contains a sizeable amount of SQL/service logic.

Target:

```text
documents/readiness/
├── queries.py
├── inventory.py
└── repair.py
```

with a thin CLI command.

This will also become the natural interface for future Focus/document-enrichment logic instead of research code querying raw artifact tables directly.

---

## 9G. Operator-authorized browser rating recovery

This is the newest and most structurally scattered recovery path.

Current implementation is spread across:

```text
scrapper/commands/browser_rating_recovery.py
scrapper/commands/browser_rating_headed.py
scrapper/commands/browser_rating_host.py
scrapper/view/browser_rating_monitor.py
compose.browser-recovery.yml
root ./scraper
root artifacts/icra-download-test/.venv
root artifacts/... PID/log/lock state
Docker document-download-worker bridge
```

Its actual workflow is:

```text
operator starts recovery
        │
        ▼
host-side Python/Playwright
        │
        ▼
visible browser against rating source
        │
        ▼
claim job through JSON bridge
        │
        ▼
download/validate report bytes
        │
        ▼
docker exec into hard-coded document worker
        │
        ▼
persist through existing download/artifact path
        │
        ▼
close/reconcile original queues
        │
        ▼
optional extract + rating normalize
```

Good parts to preserve:

- explicit operator authorization;
- durable run/job ledger;
- lease/heartbeat;
- host cooldown;
- PDF/HTML identity validation;
- persistence through the existing immutable artifact path;
- closure of the original failed queue;
- extraction/normalization reuse.

Structural problems:

- application service, host lifecycle and runtime state are mixed;
- host launcher hard-codes a container name;
- a run ID is embedded in host-side logic;
- a private `.venv`, logs, PID and lock files live under the repository's `artifacts/icra-download-test`;
- Docker exec is used as an implicit IPC protocol;
- Compose files are doing campaign orchestration.

Target:

```text
scrapper/documents/recovery/browser_rating/
├── models.py
├── ledger_service.py
├── validator.py
├── recovery_service.py
└── bridge_protocol.py

ops/host/
└── browser_rating_runner.py

ops/docker/
└── compose.browser-recovery.yml
```

Runtime:

```text
$TRADING_AI_RUNTIME/browser-recovery/
├── env/
├── logs/
├── pid/
├── locks/
└── downloads/
```

A later improvement can replace hard-coded `docker exec` IPC with a stable command/socket/queue interface, but **do not rewrite this before the behavior is frozen by tests**.

---

# 10. Database layer — preserve schema, fix ownership

The current schema is large because the system has accumulated real capabilities. That alone is not a reason to redesign it.

Do **not** normalize/rewrite the 67-migration schema during this cleanup.

The persistence problem is code ownership:

- `db/exchange_repo.py` is ~1,583 lines;
- `db/document_recovery_repo.py` is ~1,415;
- `db/mf_workbook_repo.py` is ~1,205;
- `db/repo.py` has a ~400-line `store_parsed_company()`.

Additionally, DB modules import concrete parser/source/document types. This means persistence knows too much about source implementation.

Introduce a small neutral domain-contract layer **only for types already shared across boundaries**:

```text
scrapper/domain/
├── company.py
├── periods.py
├── financials.py
├── documents.py
└── events.py
```

Then source parsers produce neutral records and repositories persist them.

Do not build an enterprise object model. The goal is to remove imports such as:

```text
db → concrete Screener parser implementation
db → concrete document parser implementation
```

not to rewrite all SQL.

---

# 11. Critical migration/runtime safety problem

Current `init_db()` is not just "initialize a connection." It creates migration metadata, scans migration files, and applies unseen migration filenames.

Because ordinary code paths call it, making a new migration visible to a live bind-mounted container can modify the configured DB.

The A2 review contains a useful migration-gate concept, but it is stale and must not be merged wholesale.

There is an additional migration-history hazard: the live incident roadmap refers to `065_exchange_source_access.sql`, while the current canonical tree has a different `065_operator_browser_rating_recovery.sql`. Migration identity is based on the filename, not merely numeric prefix.

Therefore:

> **freeze migration behavior before structural code movement.**

Required direction:

```text
connect_db()                  # connection only
assert_schema_compatible()    # read/check only
migrate_database()            # explicit operator action only
bootstrap_test_database()     # explicit test/dev helper
```

A normal worker must never discover/apply migration files as a side effect.

Do not renumber or delete historical migration records casually. First inventory actual production `schema_migrations`, then reconcile lineage under a dedicated maintenance gate.

---

# 12. The hidden UI write that should be removed

`view/shell.py` contains an `ensure_exchange_securities_seeded()` path that can auto-seed the DB from root `exchange_securities_master.csv` when the table is empty and suppress exceptions.

That violates a useful boundary:

> **views should read; import/bootstrap workflows should write.**

Target:

```text
resources/exchange_securities_master.csv
```

plus explicit:

```text
scraper exchange-import-security-master ...
```

or equivalent application service.

The shell then becomes read-only with respect to bootstrap data.

---

# 13. Docker — why there are so many services

The current main Compose defines roughly sixteen service roles, including manual commands and persistent workers.

Several are not really "services." They are CLI jobs expressed as Compose service blocks.

Current categories:

### Manual/on-demand jobs
- Screener discovery
- exchange universe refresh
- exchange result discovery
- exchange reconciliation report
- one-shot Screener worker

These do not need permanent named services. They can be run against one application image.

### Long-running operational roles
- exchange scheduler
- market-cap monitor
- Screener daemon
- artifact reconciler
- normal document downloader
- rating alternate-source discovery
- recovery-scoped document downloader
- CARE rating worker
- India Ratings worker
- other rating worker

### Campaign/temporary roles
- hard-coded June 2026 extraction worker
- browser CRISIL/ICRA/other recovery
- browser extraction campaign

Why it grew this way is understandable: each new reliability problem got isolated in a new container. But Compose is now encoding business scheduling through shell loops such as:

```text
while true
  run command
  sleep
done
```

That means Docker Compose is functioning as part workflow engine, part scheduler, part process manager and part application configuration.

That should be simplified.

---

# 14. Target process topology — consolidate process classes, not necessarily every instance

Do not collapse everything into one monolith.

Target roughly these **process classes**:

```text
1. coordinator
   - periodic exchange discovery
   - market-cap/collection-scope maintenance
   - delayed-artifact due scheduling
   - housekeeping / queue seeding

2. screener-worker
   - consumes company-quarter financial refresh jobs

3. document-worker
   - generic immutable document acquisition
   - supports normal/recovery scope through queue metadata

4. rating-worker
   - rating-specific resolution/download behavior
   - can run multiple replicas/host partitions if host throttling requires it

5. extraction-worker
   - consumes extraction jobs, no hard-coded quarter loop

6. recovery-worker (only if alternate-source workload justifies isolation)
   - BSE/NSE/issuer-IR document recovery

ON DEMAND:
7. browser-recovery
   - explicit operator campaign only
```

The key point:

> CARE / India Ratings / other may still run as separate **instances** for host isolation, but they should be the same worker class configured differently, not three separate architectural subsystems.

Similarly, normal and recovery document downloads can share a common execution contract without immediately merging their database queues.

---

# 15. Root `scraper` script — it is now a control plane

The root shell wrapper is no longer just a convenience script. It owns:

- start/stop/pause/resume;
- project-container discovery;
- external document-store safety/sentinel checks;
- shutdown/wake state;
- browser recovery lifecycle;
- PID/log handling;
- special stop timing;
- CLI forwarding.

That is too much implicit application infrastructure in one shell script.

Target separation:

```text
ops/lifecycle/
    app.py / shell helper        # Docker lifecycle + external-store checks

ops/host/
    browser_rating_runner.py     # host-only browser process

scrapper/cli/
    ...                          # application business commands
```

Keep a tiny `./scraper` façade if the user likes the UX, but it should delegate rather than contain business logic.

---

# 16. Runtime data must be conceptually outside source code

Current configuration can default `STORE` under `scrapper/store`. Browser runtime also lives under root `artifacts/...`.

Even if gitignored, runtime state should not be conceptually part of the code checkout.

Target:

```text
$TRADING_AI_RUNTIME/
├── db/
├── documents/
├── snapshots/
├── exports/
├── logs/
├── browser-state/
└── backups/
```

Do **not** physically move the live corpus during code cleanup.

First make paths explicit and point the cleaned application to the existing locations. Physical storage migration, if ever needed, is a later operational project.

---

# 17. CLI — the safest first large code split

`cli.py` is ~2,103 lines with dozens of commands and a ~1,142-line dispatcher.

It eagerly imports most of the application, which couples unrelated domains and optional dependencies.

Target:

```text
scrapper/cli/
├── __main__.py
├── main.py
├── screener.py
├── exchange.py
├── documents.py
├── ratings.py
├── mutual_funds.py
├── research.py
├── operations.py
└── legacy.py
```

Each module:

- registers its subcommands;
- lazily imports the service it needs;
- maps args to a service call;
- contains no SQL/parser/business logic.

Keep `python -m scrapper.cli ...` compatible during transition.

---

# 18. Exact high-value module splits

## `db/exchange_repo.py`

Target:

```text
db/exchange/
├── snapshots.py
├── schedules.py
├── securities.py
├── corporate_changes.py
└── result_filings.py
```

## `db/document_recovery_repo.py`

Target:

```text
db/documents/recovery/
├── targets.py
├── leases.py
├── attempts.py
├── candidates.py
└── reconciliation.py
```

## `validation/screener.py`

Target:

```text
validation/screener/
├── identity.py
├── scope.py
├── financials.py
├── shareholding.py
├── documents.py
└── aggregate.py
```

## `db/repo.py`

Split company/source-fetch persistence from financial/metric/version/document persistence.

## `core/http.py`

Keep generic transport primitives in core; move exchange/Screener-specific request behavior beside those source adapters.

## `commands/browser_rating_recovery.py`

Separate ledger/recovery service, browser fetch/validation, persistence/reconciliation and CLI concerns.

## `db/mf_workbook_repo.py`

Split workbook source inventory, holdings persistence, scheme mapping and acceptance/change persistence. Lower priority than live financial/document paths.

## `pipeline/build_p0_governance.py`

Move under research/governance and split later. It is large but not a reason to block operational cleanup.

---

# 19. Target project structure

Compatibility-first destination:

```text
trading-ai/
│
├── scrapper/
│   ├── cli/
│   ├── config.py
│   │
│   ├── core/
│   │   └── truly shared primitives only
│   │
│   ├── domain/
│   │   ├── company.py
│   │   ├── periods.py
│   │   ├── financials.py
│   │   ├── documents.py
│   │   └── events.py
│   │
│   ├── sources/
│   │   ├── screener/
│   │   ├── exchange/
│   │   ├── documents/
│   │   ├── amc_workbook/
│   │   └── mfdata/
│   │
│   ├── db/
│   │   ├── migrations/
│   │   ├── common/
│   │   ├── screener/
│   │   ├── exchange/
│   │   ├── documents/
│   │   └── mutual_funds/
│   │
│   ├── documents/
│   │   ├── discovery/
│   │   ├── readiness/
│   │   ├── reconciliation/
│   │   ├── download/
│   │   ├── recovery/
│   │   │   ├── failed_download.py
│   │   │   ├── alternate_sources.py
│   │   │   ├── saved_screener_page.py
│   │   │   └── browser_rating/
│   │   ├── extraction/
│   │   ├── ratings/
│   │   ├── transcripts/
│   │   └── media/
│   │
│   ├── workflows/
│   │   ├── screener/
│   │   ├── exchange/
│   │   ├── documents/
│   │   └── mutual_funds/
│   │
│   ├── operations/
│   │   ├── coordinator/
│   │   ├── workers/
│   │   ├── monitoring/
│   │   └── health/
│   │
│   ├── research/
│   │   ├── features/
│   │   ├── focus/
│   │   └── governance/
│   │
│   ├── exports/
│   ├── view/
│   └── legacy/
│       └── trendlyne/
│
├── ops/
│   ├── docker/
│   │   ├── compose.yml
│   │   ├── compose.dev.yml
│   │   └── compose.browser-recovery.yml
│   ├── host/
│   │   └── browser_rating_runner.py
│   └── lifecycle/
│
├── resources/
│   └── exchange_securities_master.csv
│
├── scripts/
│   └── debug/
│
├── docs/
│   ├── architecture/
│   ├── roadmap/
│   ├── research/
│   └── archive/
│
├── experiments/
│   └── archive/
├── Dockerfile
└── README.md
```

This is a **directional target**, not a command to move 171 modules in one commit.

---

# 20. How to reach that structure without breaking the system

The migration strategy is more important than the final folder names.

For every substantial move:

```text
OLD PUBLIC MODULE
        │
        ▼
extract one responsibility into NEW MODULE
        │
        ▼
old module imports/re-exports new implementation
        │
        ▼
existing tests still call old path and pass
        │
        ▼
move production callers one group at a time
        │
        ▼
tests pass
        │
        ▼
when old path has zero real callers, remove façade
```

This gives us a compatibility bridge instead of a flag-day restructure.

No massive import rewrite.

---

# 21. Ordered cleanup program

## Phase 0 — Freeze and prove recoverability

After current downloads/recovery reach the agreed stopping condition:

- stop active writers cleanly;
- record active images/processes/config;
- backup and restore-test the DB;
- inventory document corpus/queues;
- tag the exact source state;
- preserve current logs/reports needed to explain unfinished jobs.

**No code refactor yet.**

## Phase 1 — Harvest review trees

Preserve unique A2/B1/B2 work as branches/patches/commits.

Do not merge copied trees wholesale.

Then remove copied project trees and embedded environment from the canonical root.

## Phase 2 — Runtime safety

Before moving modules:

- remove production source bind mounts;
- make migration application explicit;
- separate connection/schema check from migration;
- reconcile migration 065 history;
- create a dev-only Compose override if hot source mounts are still useful.

## Phase 3 — Root/runtime cleanup

Move docs, experiments, debug tools and host ops into clear ownership areas.

Move future browser runtime state outside repository.

Remove hidden UI seeding; replace with explicit reference-data import.

## Phase 4 — Split CLI

No behavior change.

This produces a clean front door and enables lazy domain imports.

## Phase 5 — Stabilize core live workflows

In this order:

1. Screener parser split.
2. Screener ingestion/service split.
3. Exchange repository split.
4. delayed-artifact reconciliation ownership.
5. generic document download worker split.
6. document recovery repository split.
7. recovery strategy organization.
8. browser-rating recovery separation.

Run domain tests after each.

## Phase 6 — Process/Docker consolidation

Do not collapse processes before code ownership is clean.

Replace YAML shell loops with explicit `--serve` workers/coordinator.

Turn manual services into one-shot commands against the application image.

Retire the hard-coded June extraction process.

Consolidate rating worker **code class**, retaining separate replicas/host partitions where operationally useful.

## Phase 7 — Research/legacy organization

Forward-port B1/B2 work to cleaned main.

Move governance/ML/Focus into `research/`.

Freeze/archive Trendlyne if truly inactive; do not delete until caller/operator audit proves it safe.

## Phase 8 — Dead-code audit

Only now remove:

- compatibility wrappers with zero callers;
- superseded root probes;
- retired experiment execution paths;
- unused legacy commands.

Never delete because a module merely "looks old."

---

# 22. Regression gates by subsystem

Use the existing test suite as refactor protection.

Suggested gates:

```text
Screener split
→ screener vertical/discovery/period/quality tests

Exchange split
→ E01–E06 + scheduler/reconciliation tests

Document reconciliation
→ D01–D09 artifact expectation/reconciliation tests

Download worker
→ document download queue/recovery/storage tests

Alternate recovery
→ DA06 recovery adapter/registry + acquisition closure tests

Browser rating
→ browser rating recovery tests

MF structure
→ M01–M09 tests

Research
→ ml_contracts / Focus tests from preserved B1/B2 review work
```

Additionally maintain end-to-end smoke fixtures:

```text
new result event
→ Screener accepted financials
→ document expectations
→ immediate document promotion
→ delayed-document recheck
→ queue
→ download
→ persistence/extraction

failed document
→ diagnosis
→ alternate/replacement recovery
→ same logical artifact
→ generic download path
→ resolved
```

Those two flows are the behavioral contract of the cleanup.

---

# 23. What must NOT change during cleanup

Do not change:

- accepted financial history;
- original raw source observations;
- company/period IDs;
- provenance;
- first-observed timestamps;
- content hashes;
- immutable original document bytes;
- document logical identity;
- queue/retry history unless a specific bug fix is separately approved;
- current financial scope policy;
- existing 67-migration schema merely for aesthetics.

No historical re-scrape is required to make this project clean.

---

# 24. Findings that deserve separate bounded safety tasks

These are not ordinary folder moves:

### Migration 065 lineage
The current canonical migration filename and the migration referred to in the live incident are different `065_*` files. Audit actual production `schema_migrations` before any release.

### Prod/CI dependency drift
CI uses Python 3.12 + `requirements.lock`; Docker currently installs `requirements.txt`. Unify production/CI dependency locking in its own bounded change.

### Browser bridge coupling
Hard-coded container/run identifiers should be replaced, but only after recovery tests freeze current behavior.

### Hidden shell DB seed
Remove implicit `exchange_securities_master.csv` write behavior from UI.

These four issues should not be mixed into a generic module-move commit.

---

# 25. What "done" looks like

The cleanup is complete when:

- there is one canonical application source tree;
- runtime data/state is outside source checkout;
- production does not bind-mount editable source;
- normal worker startup cannot apply migrations;
- each active business process has one obvious owner;
- CLI commands are thin adapters;
- Docker defines process topology, not business workflow loops;
- all recovery strategies live under one document-recovery domain but retain their distinct semantics;
- delayed-document recheck still works;
- failed-link/replacement/alternate-source/browser recovery still ends in the same logical artifact/download path;
- existing financial/document data remains intact;
- tests and end-to-end smoke fixtures prove behavior parity;
- B1/B2 can build against clean feature/document interfaces instead of raw acquisition internals.

---

# 26. The first actual coding assignment

Do **not** start by moving source files.

First assignment after the current download campaign is frozen:

```text
1. Record the exact baseline test/CLI/service state.
2. Preserve all unique A2/B1/B2 diffs.
3. Dump the actual production schema_migrations filenames and reconcile the two 065 histories.
4. Produce one current service inventory and one active-module ownership inventory.
5. Forward-port ONLY the migration safety/no-production-bind-mount behavior onto current main.
6. Make no schema redesign, no scraper behavior change, no document reprocessing and no module deletion.
7. Return test evidence and stop for review.
```

After that gate passes, the first true structural refactor should be the CLI split, followed by the Screener parser split.

---

## Final architectural judgment

The project has outgrown the name "scraper." It is already a **financial acquisition + document recovery + research data platform**.

The right strategy is not to reduce capability until the folder tree looks pretty. The right strategy is to make each existing capability own a clear layer:

```text
SOURCE ADAPTERS
      ↓
WORKFLOWS / USE CASES
      ↓
DOMAIN CONTRACTS
      ↓
PERSISTENCE

DOCUMENT LIFECYCLE
discovery → expectations → promotion → download → extraction
                         ↘ recovery strategies ↗

OPERATIONS
coordinator + worker processes + monitoring

RESEARCH
features + Focus + later ML
```

Once those boundaries exist, new sources and new recovery methods become additions to a domain rather than another root script, another Docker loop, or another 800-line module.
