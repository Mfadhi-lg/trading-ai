# Candle inventory runtime port (#102)

The historical archive now supports explicitly selected PostgreSQL 16 inventory
sessions. SQLite remains the default. This implementation does not switch an
installed runtime, migrate retained data, start acquisition or enable scheduling.
The application database selection is independent of this separate store.

## Selection and fences

Native configuration requires all three variables:

- `TRADING_AI_CANDLE_BACKEND=postgresql`
- `TRADING_AI_CANDLE_DATABASE_URL`: an explicitly approved native target URL
- `TRADING_AI_CANDLE_SCHEMA`: the schema created and reconciled through [#101's
  transfer contract](CANDLE_INVENTORY_POSTGRESQL.md)

An archive-local `.inventory-target.json` must also select that target. Missing,
malformed or mismatched configuration fails without opening `inventory.sqlite3`.
The marker pins the PostgreSQL cluster system identifier, database and schema OIDs,
version ledger and reviewed catalog digest. Runtime sessions verify these before
access; they never create a schema or silently adopt a replacement. The runtime
role needs schema/table privileges and explicit permission to execute
`pg_catalog.pg_control_system()` to verify the cluster identity. Read-only
observers set server-enforced read-only transactions and do not require CREATE.
Do not grant a runtime role superuser privileges for this check.

`Archive` writers retain the original root `.writer.lock`, excluding legacy
SQLite writers on that root. Native writers additionally hold session advisory
locks for the schema, surviving each commit and excluding writers through another
root. Closing a session releases both fences. The lifecycle drain probes both
fences and never signals a native writer. Legacy process handoff is refused in
native mode until legacy workers have been separately quiesced. Installed code
and environment must be reconciled under the existing operator deployment gates;
old binaries cannot understand the new target marker.

Request-boundary commits, saved raw/transport receipts, uncertain-delivery stops,
quotas, attempt limits, dataset identities, Parquet encoding, source hashes and
receipt timestamps retain their existing contracts. Dataset replacement and
request insertion use explicit conflict clauses. No generated IDs exist in this
inventory. Public rows retain name/index/slice access and value iteration.
Native integrity evidence identifies schema/target verification and a readable
request table; it does not claim a SQLite or full physical integrity scan.

## Disposable cutover and rollback rehearsal

`candle_inventory.select_target` is an operator-only library API. It is never
called by normal readers, acquisition or lifecycle wake. Only use it against a
quiesced disposable copy during verification; production use requires separate
approval. Begin with the snapshot export, schema creation and verified transfer
from #101, with the same unchanged archive root and original inventory present.

```python
from scrapper.db.candle_inventory import select_target

select_target(disposable_archive_root, authorized_snapshot,
              expected_sha256=authorized_snapshot_sha256,
              settings=explicit_test_settings, schema=verified_schema,
              target='postgresql')
# With writers still stopped, rehearse the reverse selection:
select_target(disposable_archive_root, authorized_snapshot,
              expected_sha256=authorized_snapshot_sha256,
              settings=explicit_test_settings, schema=verified_schema,
              target='sqlite')
```

Selection takes the common root fence, checks the current SQLite inventory against
the snapshot, excludes native runtime writers, verifies native metadata and file
references, then atomically publishes the marker. A stale original inventory,
changed target, concurrent writer or failed verification leaves selection
unchanged. The marker is mutable operational state, excluded with the inventory
and lock files from immutable file manifests. Raw, Parquet and earlier receipt
files are not rewritten. Keep readers quiesced during selection as well.

Rollback to the original SQLite inventory is allowed only while its metadata still
matches native metadata exactly. Native writes cause rollback refusal. A reverse
transfer preserving those new writes is a separate task; never erase native data
or resume a stale SQLite inventory. Explicit SQLite snapshot/replay remains
available on a separate declared offline copy with SQLite selection; do not point
it at a native-selected runtime root.

## Verification evidence

`test_postgresql_candle_runtime` uses temporary files, unique schemas and the
dedicated local `trading_ai_test` DSN guard. It exercises real acquisition,
normalization, Parquet/index publication, saved-raw recovery, refresh, revision,
technical-update persistence, public archive reads and lifecycle verification with
providers mocked. Native runtime tests forbid every `sqlite3.connect` call.
They verify read-only enforcement, two-root writer contention, locks across
commits, uncertain delivery, cooperative shutdown, a real aborted native
transaction followed by recovery without another GET, stale-snapshot rejection,
cutover/rollback and complete public read-output parity with SQLite metadata.
These tests are also required by the native runtime smoke gate, so skips fail it.

The bounded query test inserts 3,000 synthetic request records on each backend,
compares a company/status lookup (limit 100) and grouped status/latest-receipt
query, and prints timings. A local PostgreSQL 16 run on 15 November 2026 measured
0.360/0.412 ms native versus 0.130/0.354 ms SQLite respectively. These small local
measurements certify output parity only; they do not establish production
throughput or a performance gain. Full retained-archive benchmarks, installed
runtime verification and production cutover have not been performed.
