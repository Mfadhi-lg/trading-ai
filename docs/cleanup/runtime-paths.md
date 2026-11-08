# Runtime path contract — C08.1

This contract is implemented in the isolated C08.1 candidate, based on the C06
release. It has not been activated. Existing DBs, original bytes and persisted
`local_path` values remain at their current locations. The canonical checkout
still contains its preserved older runtime source; the incremental patch is
in `docs/cleanup/baselines/C08.1/candidate.patch`.

| Configuration name | Environment setting | Default and ownership |
|---|---|---|
| `STORE`, `RUNTIME_ROOT` | `SCRAPER_STORE_PATH` | Package `store/`; shared runtime root, unchanged |
| `DB_PATH`, `DATABASE_DIR` | `SCRAPER_DB_PATH` | `STORE/scraper.db`; directory alias is the DB parent; SQLite sidecars remain beside the DB |
| `FILES` | `SCRAPER_FILES_PATH` | `STORE/files`; existing file inputs |
| `SNAPSHOTS` | `SCRAPER_SNAPSHOTS_PATH` | `STORE/snapshots`; original source responses and source-specific snapshot children |
| `DOCUMENT_STORE`, `DOCUMENTS` | `SCRAPER_DOCUMENT_STORE_PATH` | `SNAPSHOTS`; immutable document root; existing content child remains `artifact_content` |
| `EXPORTS` | `SCRAPER_EXPORTS_PATH` | `STORE/exports`; existing report/feature children |
| `CAMPAIGNS` | `SCRAPER_CAMPAIGNS_PATH` | `STORE/campaigns`; selected run evidence, for later consumer integration |
| `LOGS` | `SCRAPER_LOGS_PATH` | `STORE/logs`; runtime logs, for later consumer integration |
| `BROWSER_STATE` | `SCRAPER_BROWSER_STATE_PATH` | `STORE/browser-state`; PID/lock/profile/temp state, integration belongs to C08.2 |
| `BACKUPS` | `SCRAPER_BACKUPS_PATH` | `STORE/backups`; consistent backup evidence, for later consumer integration |

All existing imports and environment settings remain valid. New aliases reference
the same `Path` objects. Defaults deliberately preserve the current layout;
the diagram's `database/` directory is not a migration instruction. MF import
inbox/accepted/rejected/quarantine remain below `STORE/mf_import`. Migrations
remain versioned package inputs at `ROOT/db/migrations`, never runtime data.
No future intelligence/dataset/model/trading directories are created.

Paths retain existing `Path` semantics, including relative values resolved
against the process working directory by the identity guard. Imports/help do
not resolve/create writable directories or start workers. Configure absolute
paths for operator runs so working-directory changes cannot redirect writes.
Setting a path does not copy data or rewrite database references; changing an
existing store requires separately selected path/data compatibility work.

`ensure_dirs()` creates the existing directory set at configured files,
snapshots and exports roots only after H03 authorization. New campaigns/logs/
browser/backup roots are not eagerly created. Database connections continue to
use `DB_PATH`; no new DB parent is created by `ensure_dirs()`.
The existing external document-store sentinel, reserve/capacity publication
and freshness checks remain unchanged; setting `SCRAPER_DOCUMENT_STORE_PATH`
still defaults to requiring that sentinel before content directory creation.

Every configured root is included in `assert_configured_writable_targets()`.
Development/test refuse known production paths, mount identities, symlink aliases
and unverified copied markers before the first directory is created. Production
retains exact DB/store/document identities and sentinels; a configured root
outside the authorized store/documents roots requires an exact entry in the
identity's `additional_writable_roots`. No bypass flag is added. Existing C06
`/data` and `/documents` settings require no changes for the defaults.

C08.1 does not wire host PID/log/lock/profile/environment consumers or modify
`./scraper`, shutdown state, bridge/container references or shutdown/wake order.
C08.2 and C08.3 own those changes. Campaign and backup scripts keep their
current explicit paths until their selected integration work.

For an isolated test candidate, point `SCRAPER_STORE_PATH`, `SCRAPER_DB_PATH`
and `SCRAPER_DOCUMENT_STORE_PATH` at disposable private roots and set
`TRADING_AI_ENV=test`. Configure split roots there as needed. Production identity
examples from C05/C06 are release-specific; editing them or deploying this patch
requires a separately selected release action.

Rollback restores the C06 `config.py` and removes only the new test/contract
from the unactivated candidate. No database restore or corpus move is needed.
