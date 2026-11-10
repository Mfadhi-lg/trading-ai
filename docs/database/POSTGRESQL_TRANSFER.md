# Verified SQLite to PostgreSQL transfer

Issue #55 adds an explicit transfer utility. It does not select an application
backend, start workers, switch traffic or authorize production access. The
native backend and reviewed schema are described in
[POSTGRESQL_BACKEND.md](POSTGRESQL_BACKEND.md) and
[POSTGRESQL_SCHEMA.md](POSTGRESQL_SCHEMA.md).

The supported source is the reviewed SQLite 001–076 schema and the separately pinned 077 profile. The tool verifies
its complete catalog against the baseline, SQLite integrity, foreign keys and
non-null primary keys. Unsupported schema drift, dynamically mistyped values,
NUL characters outside the explicit document-source mapping, invalid constraints or oversized integer
values fail explicitly; records are never repaired, skipped or truncated.

The catalog check first accepts the original exact SQL fingerprint. For normal
SQLite initialization/rebuild formatting variants, it reconstructs the frozen
001–076 lineage in memory from checksum-verified files and requires its catalog
to match the existing manifest. It then compares SQL tokens, ignoring only
whitespace between tokens and optional double quotes around the declared
`CREATE TABLE` name. Literals, quoted expressions, comments, token boundaries,
object inventories and all schema definitions remain significant. The source
schema and frozen manifests are never rewritten. This deliberately conservative
comparison does not accept arbitrary semantically equivalent SQL.

The source is opened with `mode=ro` and `query_only`. SQLite's backup API
captures committed WAL data into a newly created private snapshot. Only the new
snapshot is switched to rollback-journal mode. The source file, source journal
mode, original bytes and source records are unchanged. File and per-table hashes,
counts and the integrity results are produced before an import is authorized.
Backups contain private financial/document metadata: retain them securely and
do not commit or attach them to a public pull request.

## Lossless document source text

PostgreSQL migration 004 stores `credit_rating_passages.text_raw`,
`document_page_elements.text_raw`, and `document_pages.title_raw`, `native_text`,
`ocr_text`, `selected_text` as BYTEA containing strict UTF-8. SQLite TEXT can
contain NUL characters; PostgreSQL TEXT cannot. These explicit raw-text fields
preserve every character, including NUL, without stripping, replacement or
normalization. Other text columns retain PostgreSQL TEXT and fail closed on
unsupported values.

The page selection CHECK retains exact native/OCR equality and empty-text
semantics. Defaults and NULLability are preserved. Repository writers bind
UTF-8 bytes and public payload readers decode to their original Python strings.
Transfer verification decodes these columns before the original typed-TEXT
hash calculation. Thus counts and hashes compare original source values, not a
sanitized representation. Direct native SQL consumers receive BYTEA for these
columns; use strict UTF-8 decoding in Python. PostgreSQL `length(bytea)` counts
bytes, and SQL `convert_from` cannot produce text containing NUL. These fields
are not text search keys or filtered/indexed text expressions in the active
application. Backups retain their bytes normally.

## Disposable rehearsal

Use synthetic inputs or an explicitly authorized disposable copy. Install the
optional requirements and use the standalone PostgreSQL development service.
Supply `TRADING_AI_DB_BACKEND=postgresql` and `DATABASE_URL` through the
environment; the CLI never accepts or prints a DSN argument.

```sh
python -B -m scrapper.db.postgresql_migration export \
  --source /path/to/disposable-source.db \
  --snapshot /path/to/new-verified-snapshot.db \
  --report /path/to/new-export-report.json

python -B -m scrapper.db.postgresql_migration schema \
  --schema trading_ai_candidate --allow-destination-write \
  --report /path/to/new-schema-report.json

# Copy the reviewed snapshot_sha256 from the export report into this variable.
export SNAPSHOT_SHA256='<reviewed sha256>'
python -B -m scrapper.db.postgresql_migration import \
  --schema trading_ai_candidate --allow-destination-write \
  --snapshot /path/to/new-verified-snapshot.db \
  --snapshot-sha256 "$SNAPSHOT_SHA256" \
  --batch-size 1000 --report /path/to/new-import-report.json
```

Snapshot and report paths must be new; no retained file is overwritten. Reports
and snapshots use private file permissions. Reports contain counts, digests,
phase/table progress and sanitized error types, never raw records, SQL
parameters or driver error text. Progress goes to stderr as JSON. A failed
snapshot remains available for operator diagnosis; a successful report is
required before using it. Filesystem errors reserving a report occur before
database access. A failure report's last progress entry identifies the active
table/phase; investigate with controlled private diagnostics rather than
publishing server errors or source data.

Imports require a standalone snapshot in DELETE journal mode with no journal,
WAL or SHM sidecars. File authorization is checked again inside the pinned
SQLite read transaction, preventing committed changes during comparison. The
tool takes the schema migration advisory lock and exclusive table locks, then
checks the applied PostgreSQL lineage and live structural fingerprint.

An empty destination receives all tables in the matched source profile (158 with SQLite 077) in bounded row batches inside
one PostgreSQL transaction. User triggers are transactionally suspended during
this historical copy: this preserves cyclic preferred-content links and avoids
regenerating processing-inbox rows that are already in the source. PK, UNIQUE,
NOT NULL and CHECK enforcement remain active. Foreign keys are deferred and
forced immediate before verification; all user triggers are restored before
commit. Historical rows predating insert/update triggers remain exact copies;
future writes receive the normal trigger protections.

Every source/destination row is compared through typed canonical SHA-256
digests in primary-key order. Text ordering explicitly uses binary/C collation;
server-side destination cursors keep reads bounded. Counts are compared for
every table, including empty tables and the retained SQLite migration ledger.
Decimal values remain exact strings: equality preserves every digit, sign,
scale and missing marker, which is stronger than numeric-only equality and
never involves float. Unit/scale fields, timestamps, JSON and provenance are
included in the same comparison. Only operational REAL fields retain their
IEEE double representation. A final structural check confirms constraints and
trigger state. Identity sequences restart transactionally beyond imported IDs.

Any populated destination is verify-only. An exact rerun returns
`verified_existing`; different, partial or unrelated rows produce failure with
no overwrite/upsert/merge. Existing identity sequences must remain ahead of
the stored keys. Structural sequence changes are also rejected. Do not run
workers on the candidate during rehearsal or verification.

## Failure, restart and restore rehearsal

The importer commits only after complete validation. A disconnect, constraint
failure, mismatch or interruption rolls back the whole destination load,
including trigger changes and sequence restarts. There is no per-batch partial
commit or persisted checkpoint. Restart from the same verified snapshot with a
new report. Work is bounded in memory, but a large database still needs enough
PostgreSQL transaction/WAL capacity and a planned offline window; this release
does not claim a production-scale duration benchmark. Tune statement/idle
timeouts through `PostgreSQLSettings` only after measurement. If a connection
fails after commit acknowledgement is lost, rerun verification safely.

Rehearse SQLite restore by creating a **new** disposable database from the
verified backup, without replacing the original:

```sh
python -B -m scrapper.db.postgresql_migration export \
  --source /path/to/new-verified-snapshot.db \
  --snapshot /path/to/new-restored-disposable.db \
  --report /path/to/new-restore-report.json
```

Compare all table counts/digests with the original export report and run the
application's SQLite read-only compatibility check against the restored copy.
Automated tests change a disposable working database, restore to a new path and
verify the original evidence; interruption tests prove destination rollback and
restart. PostgreSQL backup/restore certification is a separate #57 deliverable.
Never drop or truncate an existing candidate to make a retry pass. Investigate
unexpected rows/drift, or create a separately authorized fresh candidate.

## Production cutover and rollback gate

These are operator instructions, not an automated activation workflow:

1. Obtain explicit authorization for production database access/backup and
   separately for cutover. Complete repository parity (#53), schema acceptance
   (#54), representative performance tests (#56), and PostgreSQL operational
   backup/restore/CI readiness (#57). Confirm roles, storage, backup retention,
   disk/WAL capacity, timeouts and the deployment/recovery release are reviewed.
2. With explicit operational approval, pause admissions and drain all SQLite
   writers. Record the freeze time and release identity. Keep workers stopped
   so the verified backup represents the final writable SQLite state.
3. Create the new verified snapshot. Retain the unchanged original SQLite
   database, its required state/document stores, and a securely retained backup.
   Rehearse restore to a fresh disposable path and compare every table digest.
4. Apply the reviewed PostgreSQL schema to a dedicated empty candidate and run
   the hash-authorized import. Require `committed`, matching counts/digests for
   every table, structural verification and read-only business-query parity.
   Run the importer again and require `verified_existing`.
5. Require explicit operator sign-off on these concrete reports before changing
   the application backend/release. The transfer utility cannot perform that
   change. Run approved smoke checks before permitting new writes.
6. During the pre-write rollback window, restore the previous SQLite application
   release/configuration and point it at the retained unchanged SQLite database
   (or a verified restored copy) under separate restart authorization. Preserve
   the PostgreSQL candidate and reports for diagnosis.
7. After PostgreSQL has accepted new writes, SQLite is stale. Stop/drain the
   new writers under explicit approval, retain a verified PostgreSQL backup and
   reconcile/replay the delta through a separately reviewed procedure. **Do not
   switch blindly to SQLite or claim automatic lossless reverse migration.**

No production dataset was used to develop or certify this utility. Passing
synthetic transfer tests does not authorize cutover or establish live data
quality, production workload performance or application repository parity.
