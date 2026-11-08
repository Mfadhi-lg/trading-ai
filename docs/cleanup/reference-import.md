# Explicit exchange reference import

C09.1 candidate command (offline preparation; unactivated):

```sh
python -m scrapper.cli exchange-import-reference --db /isolated/database.db --file /path/reference.csv
```

The importer requires an existing compatible schema and an empty
`exchange_securities` table. It validates the complete fixed-header UTF-8 CSV,
IDs, enums, dates, UTC timestamps, duplicate identities and referenced source
snapshots/current versions. It inserts atomically with foreign keys enabled,
never replaces rows, creates no source history, and applies no migrations.
Success prints sorted JSON `{"imported_rows": N, "status": "imported"}` (exit 0).
Validation, schema, lineage, nonempty-table and IO errors print sorted JSON with
`status: error` and `error` (exit 2). A repeated import refuses existing rows.

Without `--file`, the locator prefers
`resources/exchange/exchange_securities_master.csv`. The existing compatibility
locator still falls back to the root CSV when the resource path is absent.
The byte-identical root duplicate was archived and removed during the
[root hygiene pass](chunks/ROOT-HYGIENE.md); the resource file and locator code
are unchanged. This export
contains historical snapshot/version IDs; it is not a self-contained new-DB
seed. Missing provenance is a refusal, never a fabricated snapshot/version.
Use the existing official exchange refresh workflow to populate a new database.

Shell startup, search and autocomplete no longer seed rows. The old
`ensure_exchange_securities_seeded` callable remains as an inert compatibility
name; it reports availability internally and performs no import. Startup opens an
existing DB read-only and checks compatibility; missing/incompatible DB returns
2 without creating directories, tables or files. Empty reference tables report
an explicit-import notice while accepted-company searches continue. Explicit
`/worker`, `/requeue-failed` and `/requeue-inc` acquire guarded writable connections
only on dispatch. Other established explicit monitor dispatch remains unchanged.

Rollback restores prior isolated code; imported rows and history remain. No
live import, schema change, deployment, CSV deletion or data relocation occurs.
