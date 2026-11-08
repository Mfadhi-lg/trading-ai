# Trading AI

Trading AI preserves source-faithful financial history and supporting documents for research. The existing acquisition system collects Screener and exchange data, imports official AMC workbooks, and discovers, downloads, extracts and recovers documents while retaining original bytes, versions and evidence. The Python package keeps its existing name, `scrapper`.

Start with the [current-status and supersession index](docs/STATUS.md). The [cleanup execution ledger](TRADING_AI_CLEANUP_EXECUTION.md) governs structural cleanup; the [Master Roadmap V4](TRADING_AI_MASTER_ROADMAP_V4_2026-09-29.md) describes product direction subject to later recorded supersession. Execute only an explicitly selected chunk.

## Runtime and operation

The latest recorded production observation is [C06.1, 3 October 2026](docs/cleanup/chunks/C06.md): 11 continuous roles ran a verified immutable image with no source-code mounts. Manual discovery and browser recovery stayed stopped; BSE access holds remained active. This is a dated observation, not a live health check.

The active release uses retained Compose configuration in an isolated `/private/tmp` release worktree. This checkout's [compose.yml](compose.yml) and [Dockerfile](Dockerfile) are historical operator/build entries and were not replaced during activation. Use the C06 record and [C05.3 release evidence](docs/cleanup/chunks/C05.3.md) to identify the approved release before creating or recreating containers. Temporary release files, backup and OCI exports still need durable retention.

From this repository root, inspect existing project containers with:

```sh
./scraper status
```

The [scraper launcher](scraper) retains shutdown/wake commands: `./scraper shutdown` records and gracefully stops the running set; `./scraper wake` restarts that set after checking the external document store. These change runtime state and are operator actions, not setup instructions. C06 records the successful rehearsal. Keep the external disk mounted at its configured path with its required sentinel before wake. Do not substitute a fresh root Compose startup for restoring the approved set.

Detailed command and capability reference: [scrapper/README.md](scrapper/README.md). Its older startup/build examples must be read with the active-release distinction above. Preserve command names, aliases and flags during cleanup. Runtime changes and migration application require explicit production authorization; develop outside production mounts with isolated writable targets.

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
| `a2-review/`, `b1-review/`, `b2-review/` | Preserved copied review trees, not active releases or registered worktrees |

Original financial history, raw cells/responses, company/period identity, document content and attempt history remain authoritative evidence. Missing, unavailable, blocked and unknown documents remain distinct; optional documents do not invalidate accepted financials. Do not rerun historical campaign mutation scripts as setup.

ML training, document AI, news/market agents and trading execution remain deferred product work. Cleanup reserves their ownership without implementing or activating them.
