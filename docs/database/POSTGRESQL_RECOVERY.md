# PostgreSQL backup, restore and operational recovery

Issue #57 adds a CI recovery gate, not a production backup installation or a
PostgreSQL cutover. SQLite remains governed by the existing
[recovery procedure](../operations/security-backup.md). No backup scheduler,
offsite bucket, credentials, runtime role or production database is changed.

## Backup policy and ownership

Before approving PostgreSQL as primary, the repository owner (service owner)
must assign an on-call database operator and a separate restore verifier, record
their contacts in the private operations inventory, and demonstrate this policy:

- Schedule a consistent PostgreSQL custom-format logical dump every 24 hours.
  Target recovery point: 24 hours; target recovery time: four hours, subject to a
  measured full-size restore. These are acceptance targets, not achieved SLAs.
- Keep 14 daily, eight weekly and 12 monthly encrypted recovery sets, including
  an offsite copy in a separate failure domain. Object storage versioning and
  retention protection must prevent the backup principal from deleting retained
  copies. Never expire the last successfully restore-verified set.
- Encrypt transfers with verified TLS and backups with an approved age recipient
  or managed KMS key before upload. Separate decryption keys from backup storage
  and test operator recovery access. Use a restricted backup login and private
  files/directories (0600/0700); credentials belong in a managed secret or 0600
  passfile, never command arguments, logs or Git.
- Schedule an isolated restore every week and after schema changes, major
  upgrades, backup/client changes or key rotation. The database operator owns
  execution; the restore verifier signs schema/count/financial-record evidence;
  the service owner owns failure escalation and cutover approval.
- Back up original document bytes separately, with database snapshot time,
  content hashes, sizes and provenance. A database dump alone cannot recover
  documents. Preserve role/grant definitions and runtime identity configuration
  as a separate encrypted recovery set; never restore production credentials
  or acquisition schedules into a verification environment.

Production acceptance requires a configured scheduler, offsite storage, alert
routing, owner contacts and measured restore evidence. This repository does not
claim those are installed. Logical dumps do not provide point-in-time recovery;
if a smaller recovery point is required, approve a WAL/base-backup policy and
validate it separately.

## Repeatable non-production drill

Use a dedicated, empty PostgreSQL 16 service, with no persistent mounts, runtime
credentials or acquisition workers. Install the locked offline dependencies,
`scrapper/requirements.postgresql.txt`, and PostgreSQL 16 `pg_dump`/`pg_restore`
on PATH. Set all application write roots under a fresh temporary directory as
in [.github/workflows/postgresql.yml](../../.github/workflows/postgresql.yml).
Then run:

```sh
TRADING_AI_ENV=test \
TRADING_AI_POSTGRESQL_TEST_DSN=postgresql://postgres:postgres@127.0.0.1:5432/trading_ai_test \
python -B -m unittest scrapper.tests.test_postgresql_backup -v
```

The login needs CREATE DATABASE only in this disposable service. The test refuses
remote hosts, other database names, URL query overrides and non-test identities.
It creates a unique schema and a separate clean database from `template0`,
applies reviewed migrations and transfers only synthetic source fixtures. It
creates a custom archive, restores with `--exit-on-error` and compares:

- complete schema-only dumps, including indexes, constraints, views, functions
  and triggers (only randomized psql restrict keys are ignored);
- row counts and exact sorted row digests for every table, including both
  migration ledgers, financial values, null/blank/dash/zero markers, timestamps,
  raw evidence and document/content relationships;
- sequence last values and `is_called`, preserving the next identity allocation.

The fixture includes a long exact decimal string and document provenance. The
drill deliberately corrupts a financial value without changing the row count,
then drops a view, and verifies that both corruptions are detected. It removes
only its own UUID schema/database and temporary files. It does not retain or
upload database artifacts. Without the explicit test DSN the live test is
reported skipped; CI supplies the DSN and client tools so failures cannot silently
become skips. The existing PostgreSQL suite still runs fresh migrations,
sequential upgrades, repository/worker integration and integrity tests. SQLite
coverage remains in the separate supported offline and lineage jobs.

## Operator backup and restore procedure

These steps describe separately authorized operations. They do not authorize
production access. Rehearse them on synthetic data first.

1. Record the source version, migration checksums, schema, snapshot time and
   document manifest. Use a matching PostgreSQL 16 client. Provision a dedicated
   backup login with read access to all application tables/sequences and no
   acquisition or application write privileges. Coordinate DDL freezes: a logical
   dump takes a consistent snapshot but does not coordinate concurrent migrations.
2. In a private staging directory with `umask 077`, use an approved libpq service
   file/passfile and run `pg_dump --format=custom --no-owner --no-privileges
   --file=backup.dump`. Supply connection settings via the approved environment,
   not a credential-bearing command line. A nonzero exit is failure. Hash the
   archive, encrypt it (for example `age -R recipients.txt -o backup.dump.age
   backup.dump`), and upload only encrypted bytes plus an encrypted manifest.
   Verify remote checksums and availability before applying retention policy.
3. Save table counts, canonical row digests, critical financial identifiers/raw
   values and source/document hashes from the *same snapshot*. For a live database,
   export a REPEATABLE READ snapshot, keep that transaction open and pass
   `--snapshot` to pg_dump while generating verification evidence in that
   transaction. Counts taken at a different time are not a valid comparison.
4. Provision a fresh disposable PostgreSQL 16 instance with application network
   egress disabled, no production storage and no schedulers. Verify archive hash
   after decryption. Restore into a new database created from `template0` using
   `pg_restore --exit-on-error --no-owner --no-privileges --dbname=restore_test
   backup.dump`. Never use `--clean` against an existing target. A failed restore
   leaves an unusable candidate: retain diagnostics privately and start a new
   empty target after investigating.
5. Compare the full schema and all table counts/digests with the snapshot
   evidence, including exact financial text, IDs, reporting scopes, missing
   markers, timestamps, source bytes/hashes, FK links and sequence positions.
   Check constraints are validated and triggers enabled. Run the repository,
   integration and data-integrity suites on a synthetic restored environment;
   never run mutating test fixtures against a restored production dataset.
6. Schema fingerprints in `_postgresql_migrations` include internal PostgreSQL
   object identities and may differ after restore. Preserve this ledger. Compare
   portable DDL with a clean database migrated from the same reviewed files,
   investigate every difference, and obtain explicit structural recertification
   before runtime startup. Do not update ledger signatures to suppress drift.
   The CI drill proves portable DDL/data recovery; it does not certify automatic
   application startup on a restored production database. Roles, grants and
   runtime target identity also require separate reviewed provisioning.
7. Record archive checksum, PostgreSQL/client versions, migration identities,
   schema comparison, all counts/digests, critical-record results, elapsed time,
   document recovery results and verifier sign-off. Keep private evidence with
   the backup set. Only a separately authorized cutover can redirect workers;
   preserve the former source for rollback. Restore completion alone never wakes
   acquisition. Failed verification means the backup is unverified, not healthy.

## Monitoring and incident runbooks

The database operator owns these alerts and the service owner receives missed
alerts. Establish baselines before cutover and tune thresholds from observed
workload; initial thresholds are:

- **Connections:** alert at 80% of `max_connections` for five minutes using
  `pg_stat_activity` and reserved-slot settings. Inspect application name, state,
  wait events and transaction age. On exhaustion, pause new job admissions,
  inspect idle-in-transaction sessions, reduce pool/concurrency demand and check
  leaked sessions. Keep an operator reserve. Terminating sessions requires
  incident authorization; fix the leak before resuming bounded work. Do not
  raise connection limits without memory/load evidence.
- **Storage:** monitor host/volume free space and `pg_database_size`, table/index
  growth and WAL/archive backlog; alert below 20% free or a seven-day exhaustion
  forecast. Investigate growth, failed WAL archival and replication slots before
  adding space. Never remove database/WAL files or retained backups manually to
  clear an alert.
- **Slow queries:** collect duration histograms and `pg_stat_statements` when
  explicitly enabled. Start with a two-second p95 or twice normal baseline for
  15 minutes. Keep logged SQL/parameters access-restricted because they may
  contain private records. Reproduce with synthetic data; inspect plans and
  indexes before changing limits.
- **Locks:** alert on blocked sessions over 60 seconds, deadlocks and transactions
  idle for over five minutes. Use `pg_blocking_pids(pid)`, `pg_locks` and
  `pg_stat_activity` to find the blocker; pause dependent admissions and coordinate
  cancellation with its owner. Never disable constraints or triggers to proceed.
- **Failed backups:** alert immediately on dump/encryption/upload/hash/restore
  failure and if the last complete offsite set is older than 26 hours. Preserve
  the last verified set, inspect capacity, auth, key access, connectivity and
  client/server compatibility, then retry into a new artifact. Escalate missed
  recovery points to the service owner; record gaps, rather than marking a retry
  healthy before upload and restore verification succeed.
- **Upgrades:** pin the supported major (16), rehearse extensions/client and schema
  compatibility on a restored copy, measure migration and recovery time, and
  verify financial invariants. Take a verified pre-upgrade backup, freeze writes
  in an approved window and retain the old cluster. Major upgrades require a
  reviewed pg_upgrade or dump/restore plan. Roll back to the retained old cluster
  only while new writes remain frozen; otherwise reconcile new writes explicitly.
  Minor updates also require recovery evidence and a separately approved restart.

Observe backup age, restore age/duration and missing document recovery alongside
database metrics. No successful CI run proves live alert delivery or production
backup health.
