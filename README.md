# Trading AI

Last documented acquisition snapshot (not live runtime verification): [22 October restart and verified outcomes](artifacts/acquisition-restart-20261022/REPORT.md). Use the installed bounded acquisition dispatcher; the old continuous workers remain stopped. Financial/cap scheduling is active, NSE intake admits at most 100 new jobs per operation, and the document execution window ends 23 October at 14:42 IST. A new CARE-hosted rating PDF was downloaded and its saved size/hash verified; new presentation/transcript downloads remain unproven. [Activation reference](ops/activation/README.md), [practical execution plan](docs/current/TRADING_AI_ACQUISITION_READINESS_EXECUTION.md).

Trading AI preserves source-faithful financial history and supporting documents for research. The existing acquisition system collects Screener and exchange data, imports official AMC workbooks, and discovers, downloads, extracts and recovers documents while retaining original bytes, versions and evidence. The Python package keeps its existing name, `scrapper`.

Changes to `main` follow the [merge policy](docs/operations/merge-policy.md), with
pull requests and required migration-lineage and offline-contract CI checks.

For current evidence, run `python3 -B scripts/runtime-status`; see [read-only verification and report freshness](docs/operations/runtime-verification.md). Local operator reports at `artifacts/runtime-verification/latest.json` record only the selected observations at their timestamps. Without a refreshed report, production runtime status is unknown.

Start with the [current-status and supersession index](docs/STATUS.md). Canonical application source is `scrapper/`; the structural cleanup was integrated into `main` by [C28.1](docs/cleanup/chunks/C28.1.md). The [research and document intelligence master plan](docs/current/TRADING_AI_MASTER_RESEARCH_AND_DOCUMENT_INTELLIGENCE_PLAN.md) describes the proposed next product work. The [cleanup execution ledger](TRADING_AI_CLEANUP_EXECUTION.md) and [Master Roadmap V4](TRADING_AI_MASTER_ROADMAP_V4_2026-09-29.md) retain their root paths for historical evidence readers. Execute only an explicitly selected task. Coding agents should start with [repository-wide AI instructions](AGENTS.md) and the [evidence-based AI engineering workflow](docs/ai-engineering/README.md).

## Terminal workflow

```sh
./scraper                         # Interactive slash-command CLI
./scraper-monitor                 # Live terminal monitor
./scraper monitor --once           # Fast snapshot; no Docker/database wait
./scraper status                   # Full readable receipt/schedule snapshot
./scraper doctor                   # Guarded live queue/drive/dependency inspection
./scraper search CARERATING        # Name, ticker, BSE code, ISIN or ID
./scraper company CARERATING       # Financial matrices, queue and documents
./scraper logs                     # Recent dispatcher log
./scraper shutdown                 # Drain before Finder drive ejection
./scraper wake                     # Resume the existing schedule
```

The web control panel has been removed. The CLI uses the current bounded
acquisition workflow. See [terminal commands and monitoring](docs/operations/terminal-monitor.md).

## Runtime and operation

PostgreSQL 16 is the primary backend target for new configured deployments. The
[application database setup](docs/database/POSTGRESQL_APPLICATION.md) covers
explicit backend selection, restricted logins, target identity and verification.
Existing installations retain SQLite unless deliberately configured otherwise;
this does not change the pinned acquisition deployment below.

The current workflow is the bounded host dispatcher documented in
[operational recovery](docs/operations/operational-recovery-20261022.md). It admits
finite exchange, financial, market-cap and document batches on its existing
schedule. The former continuous Docker workers remain legacy/manual roles.

`./scraper shutdown` pauses admissions and drains existing work; `./scraper wake`
validates and resumes that same schedule. Shutdown/wake do not restore the old
continuous worker set. Eject My Passport through Finder after a successful guarded
shutdown, and reconnect the correct drive before wake.

Do not substitute a root Compose startup or rebuild for the pinned acquisition
images. The historical [C06 record](docs/cleanup/chunks/C06.md) and
[C05.3 release evidence](docs/cleanup/chunks/C05.3.md) remain dated evidence of the
previous workflow.

Detailed command and capability reference: [scrapper/README.md](scrapper/README.md). Its older startup/build examples must be read with the active-release distinction above. Preserve command names, aliases and flags during cleanup. Runtime changes and migration application require explicit production authorization; develop outside production mounts with isolated writable targets.

The [PostgreSQL transition](docs/database/POSTGRESQL_BACKEND.md) provides an opt-in
native connection API and isolated development service. SQLite remains the
application runtime until repository parity, verified data transfer and operator
acceptance pass; this transition does not activate a production backend.

## Repository and evidence

| Location | Purpose |
|---|---|
| [scrapper](scrapper/) | Existing acquisition, persistence, document processing, commands and tests |
| [docs/cleanup/chunks](docs/cleanup/chunks/) | Per-chunk scope, verification, limitations and rollback |
| [docs/cleanup/baselines](docs/cleanup/baselines/) | Retained machine evidence, patches and review archives |
| [architecture review](docs/architecture/2026-10-01/REPOSITORY_REVIEW.md) | Dated repository snapshot and ownership findings |
| [cleanup references](docs/architecture/cleanup-references/) | Preserved blueprint and module map; destination design rather than current layout |
| [artifacts](artifacts/) | Dated campaign, research and audit evidence; status is indexed separately |
| [experiments](experiments/) | Isolated exploratory work |
| [Root hygiene and archive index](docs/cleanup/chunks/ROOT-HYGIENE.md) | Verified external retention of the historical candidate and share snapshot; one canonical application tree in this checkout |

The historical C04–C17.4 candidate now lives in the sibling `../trading-ai-retained-cleanup-archive-2026-10-07/` archive, with original paths and SHA256 manifests. Original per-chunk archives, patches and evidence remain under `docs/cleanup/`. `OLD/` retains inputs still read by the Focus governance command, `artifacts/` retains campaign/research evidence, and `exchange-inbox/` retains its existing Compose input path. Local session and lifecycle state stay at their configured paths. See the hygiene record for exact dispositions and recovery instructions.

Original financial history, raw cells/responses, company/period identity, document content and attempt history remain authoritative evidence. Missing, unavailable, blocked and unknown documents remain distinct; optional documents do not invalidate accepted financials. Do not rerun historical campaign mutation scripts as setup.

ML training, document AI, news/market agents and trading execution remain deferred product work. Cleanup reserves their ownership without implementing or activating them.
