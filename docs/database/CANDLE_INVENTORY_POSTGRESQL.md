# Historical candle inventory transfer contract (#101)

This store is **separate** from application `scraper.db`. No archive runtime
reader, writer, lifecycle command, market refresh or technical-update consumer is
switched. Production transfer and runtime port/cutover require separate approval.

The reviewed source is `Archive.__init__` in
`experiments/historical_candle_archive_v1/storage.py`. Its DDL is pinned in
`scrapper/db/candle_inventory_v1.sql`; a regression test compares that baseline to
the actual bootstrap. Unknown tables, indexes, constraints or columns fail closed.

- `requests`: text `request_id` primary key; company/instrument, unit/interval and
  date identity, verbatim status, attempts, raw hash/path, receipt timestamp, HTTP
  result, row count, error and elapsed time. `request_gaps` supports resume scans.
- `datasets`: text `dataset_id` primary key; request/company/instrument references,
  timeframe/definition/status, first/last starts, count, Parquet path/hash/bytes,
  raw hash, observation timestamp, completeness (including superseded evidence),
  corporate-action status and write duration. Two lookup indexes remain.
- `revisions`: `(request_id,new_sha256)` primary key, previous hash and verbatim
  observation timestamp. No history is flattened.
- `candle_observations`: primary key `(instrument_key,timeframe,definition,start,
  observed_at)`; company, end bound, status, quality, row hash and dataset lineage;
  `observation_asof` index preserved.

There are no source foreign keys, generated identities, sequences or status
checks. Do not invent them during transfer. SQLite's nullable primary keys cannot
be faithfully represented by native PostgreSQL primary keys: such source rows
are rejected for explicit remediation, never dropped. TEXT remains TEXT (including
timestamps and source representations); INTEGER becomes BIGINT and REAL becomes
DOUBLE PRECISION with exact hexadecimal float digest verification. NULL and zero
remain distinct. Malformed stored types fail before driver coercion. Out-of-range
integers, unsupported text or content mismatches roll back the complete load.

`IN_FLIGHT` and `STOPPED` retain uncertain-delivery meaning; transfer never
classifies them as retryable. `RAW_SAVED`, `SUCCESS`, isolated failures and
quarantine remain verbatim. Receipts and transport receipts are preserved as
files. Raw provider JSON is not parsed or normalized by this tool.

## Explicit library workflow

Use only a quiesced **disposable copy** of inventory and an approved unchanged
archive root. Do not call the production `Archive()` constructor. The caller owns
operator authorization, source quiescence, the target DSN and schema choice.

```python
from scrapper.db.postgresql import PostgreSQLSettings, connect
from scrapper.db.candle_inventory_transfer import export_snapshot, migrate, transfer

report = export_snapshot(copy_path, new_snapshot_path, archive_root, batch_size=1000)
# Review report and authorize this exact snapshot SHA256 before transferring.
# Choose timeouts exceeding the measured disposable rehearsal scan/batch times.
settings = PostgreSQLSettings(explicit_target_dsn, idle_transaction_timeout_ms=scan_timeout_ms,
                              statement_timeout_ms=batch_timeout_ms)
with connect(settings) as conn:
    migrate(conn, new_schema_name)  # requires a new namespace; transactionally versioned
result = transfer(settings, new_snapshot_path, new_schema_name, archive_root,
                  expected_sha256=authorized_snapshot_sha256, batch_size=1000)
```

Snapshot export uses SQLite's backup API (including committed WAL), exclusive
creation and read-only source access. Transfer requires standalone DELETE-journal
snapshots without sidecars, checks the hash before and after pinning a read lock,
and reconciles every row ordered by primary key using bounded batches. Reports
include counts/digests, status distributions and a path/byte/hash manifest of
retained files (except inventory/lock state). Raw references verify decompressed
SHA256; Parquet references verify bytes, SHA256 and size. Every other retained file,
including earlier receipts, participates in the file manifest.
Dataset/revision hashes absent from current requests and dangling logical links
are counted in `unresolved_lineage`; these historical gaps are preserved, not
certified as valid source references. Raw hashes in older revisions may no longer
be present in current requests. The report does not infer missing lineage. Symlinks and
special files are rejected. Archive writes must remain stopped throughout the
operation; pre/post checks detect changes but do not lock external file writers.
Reports include archive metadata and should be retained privately.

A native target must match actual tables, columns, defaults, indexes, constraints,
triggers, row security flags/policies and version ledger. Transfer requires permission to create a temporary
reference schema inside a rolled-back savepoint for that verification. All target
tables are locked exclusively. Empty targets load atomically; retries verify exact
content. Partial or foreign contents are rejected without merging or overwriting.
No transfer commits partial progress: resume means retrying the same snapshot.
The file hash pass and decompressed raw validation are streamed. Metadata row batches are bounded; file manifests scale with file count.

## Backup, restore and rollback rehearsal

`test_postgresql_candle_inventory` uses synthetic data only. With the dedicated
local `trading_ai_test` service configured, it injects an interruption, proves zero
committed target rows, retries twice, runs native `pg_dump --format=custom` and
`pg_restore` into a newly allocated database, compares native schema dumps, then
reconciles restored metadata and the same referenced-file manifest. Same-count
content corruption and schema drift must fail. The test verifies original
inventory/file identities after failure and success. PostgreSQL CI discovers this
module through its existing `test_postgresql*.py` command and isolated service.

Rollback before cutover is leaving runtime on its original inventory; the test
proves it is unchanged. Native dumps do not contain raw/Parquet/receipt bytes:
retain a separate byte-identical archive backup with its manifest and verify that
root during restore. A real operator backup/restore of retained data has not been
performed by this implementation. Never delete source evidence after transfer.

For a large archive, measure the complete checksum pass on the disposable copy
and configure `idle_transaction_timeout_ms` above that duration: the final file
scan runs inside the target transaction before commit. The default 60 seconds may
be insufficient. Size statement/lock timeouts explicitly for the rehearsal. A
timeout aborts the transaction; retry with the same snapshot after adjusting the
settings. Full retained-archive throughput has not been measured by these tests.
