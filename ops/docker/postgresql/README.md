# Local PostgreSQL acquisition release

The optional native release keeps the existing bounded acquisition schedules,
source budgets, ownership locks and document store. It is selected only by a
prepared release containing a manifest-bound `ops/activation/postgresql-target.json`.
Release preparation retains the existing workspace experiment dependency through
a symlink, preserving the candle lifecycle’s original state root and process
identity. This workspace dependency remains outside the sealed acquisition inventory.
Changing repository source never activates that release. The target can pin
`host_python` and `host_site_packages` to a persistent, hash-locked host
environment. The lifecycle launcher follows the active release’s prepared
LaunchAgent, so deleting an unrelated package-cache environment cannot break
its selected interpreter.

Build the replacement application image from the inspected local base tag using
`Dockerfile` in this directory. Verify that `trading-ai-postgresql-base:20261110`
resolves to the retained base image `sha256:1a310808f878911b4bd0a324beaf2948f215e3743238c981a531656cf0eecbf9`
before building. Record the resulting immutable image ID, source revision and
application/operator source hashes. Image readiness uses the configured backend.
The native image includes the locked application dependencies and optional
PostgreSQL driver; building it does not start acquisition.

`postgresql_release.py` validates the complete source inventory and target/config
checksums. The target identifies the database/schema UUID, pinned application and
PostgreSQL images, private socket volume and five dedicated logins. Credentials
remain outside the release and Git, in separate Docker volume subdirectories.
Only the selected login's subdirectory is mounted in each worker. Financial
operations with a paired document companion additionally mount the document
login's credentials and select its separate runtime identity in the child.
This selects the correct server policy; it is not isolation from a hostile
process sharing that container.

The PostgreSQL service uses SCRAM authentication and a Unix socket, with no TCP
listener. Network-isolated ingestion and processing stay network-isolated.
Exchange, financial, document, reader and backup logins retain server-enforced
policies; normal startup never repairs grants or applies migrations. Provision
policies explicitly through `postgresql_permissions.provision_policy()` using
`nse_results_runtime.exchange_authorizer`, the existing application policy,
`phase4_routine_runtime.routine_authorizer`, and the reader policy. The backup
login additionally needs SELECT on sequences and does not need sequence USAGE.
Set authentication separately and privately; never put passwords in commands,
release JSON, logs or a pull request.

The original `/data` mount becomes read-only. Existing files, snapshots, exports,
campaigns, logs, browser state and backups subdirectories receive their existing
writable submounts. All subpaths must exist before launch. Existing `mf_import`
directories must also exist because common startup checks them. Keep the
underlying `/physical` identity mount read-only: native preflight still verifies
its original marker hashes. The SQLite database is retained unchanged for the
pre-write rollback window. The external document archive stays at its existing
location with its existing sentinel and capacity checks.

Host status/search/quarter observations use the restricted native reader and a
repeatable-read, read-only transaction. Catalog identifiers use binary UTF-8
ordering; company sections use stable keys and undated financial periods sort
after dated periods, matching SQLite observations. The manual CLI follows the selected
native release instead of reopening SQLite. Missing native configuration keeps
the existing backend; malformed native configuration fails closed.

## Local encrypted backups

The owner selected local-only backups for this migration. This is an explicit
local operating choice, not an offsite recovery certification. Keep the private
age key separately on the Mac; do not place it beside encrypted archives.
Document its private recovery location in the operator inventory.

`ops/host/postgresql_backup.py --execute` runs a read-only, socket-only `pg_dump`
client using the dedicated backup login and streams its custom archive into age.
The backup JSON identifies the local directory sentinel, public recipient,
reviewed age executable hash and target UUID, and is bound to the release
manifest. A 100 GiB free-space check runs before backup; this is not continuous
reserve enforcement. Existing archives are never expired automatically.

A uniquely named dump client, exclusive job lock, private diagnostics and atomic
receipts support interruption/failure diagnosis. Interruptions stop that owned
client and mark failure; partial archives are preserved. Install the daily local
LaunchAgent only after an initial encrypted backup, decryption and isolated
restore have passed. Install an hourly `ops/host/postgresql_health.py` observation after restore
verification. It verifies the selected native reader, backup target identity,
26-hour freshness, archive location/existence/size and storage configuration,
then writes a private `artifacts/postgresql-backups/health.json` receipt. It does
not rehash the entire encrypted archive each hour or repeat restore verification.
Monitor the health receipt and LaunchAgent exit status; a successful
dump alone is not restore proof. Keep source, schema, all-table hash, sequence,
backup and restore receipts privately.

Follow `docs/database/POSTGRESQL_TRANSFER.md` for the frozen writer window,
fresh source snapshot, exact import/rerun and rollback requirements. A release
switch is a separate explicit operation under the existing ownership locks;
reconcile the retained pause configuration and run read-only/container smoke
checks before permitting acquisition writes.
