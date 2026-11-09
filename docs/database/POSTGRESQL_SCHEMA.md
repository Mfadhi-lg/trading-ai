# PostgreSQL schema lineage

This is the PostgreSQL 16 schema baseline for issue #54, built from the final
schema of a synthetic SQLite database after migrations 001–076. It is separate
from the SQLite migration history. No historical data-repair operation or live
operational seed state is replayed into PostgreSQL. The verified transfer copies
the source's actual rows, including its SQLite migration ledger and policy rows.

`scrapper/db/postgresql_migrations/001_baseline.sql` contains 157 tables, 93
explicit indexes, 27 views and 21 PL/pgSQL row triggers. The baseline retains
primary/composite keys, unique/partial indexes, checks, foreign keys and delete
actions. Foreign keys are added after table creation to resolve cyclic
relationships. They are validated and initially immediate, with explicit
deferral available to the controlled importer.

The schema deliberately preserves these representations:

| SQLite | PostgreSQL | Reason |
|---|---|---|
| TEXT | TEXT | Exact raw strings, decimal digits/scale, missing markers, JSON bytes and timestamp/offset spelling |
| INTEGER | BIGINT | Preserve the SQLite signed 64-bit range and integer flags |
| REAL | DOUBLE PRECISION | Eight operational clock/delay fields; no financial decimal fields |
| Independent INTEGER PRIMARY KEY | BIGINT identity, by default | Preserve explicit imported IDs and support native `RETURNING` |
| INTEGER key that references another table | BIGINT key without identity | Preserve shared identity ownership |

Changing financial text to NUMERIC or timestamps to timestamptz is a separate
data-contract change. No conversion passes through float. Null, empty text,
dash and zero remain distinct. PostgreSQL primary keys require NOT NULL even
where SQLite text/composite primary keys technically permit NULL; transfer
preflight rejects such rows for explicit reconciliation rather than rewriting
or omitting them.

All 21 SQLite triggers are represented. Identity/preference guards raise SQLSTATE
23514, while the saved-content trigger creates its durable processing inbox
entry. Function search paths explicitly place the application schema first and
`pg_temp` last so caller-created temporary relations cannot bypass guards.
The `artifact_retry_coverage` view uses an explicit join on
`expectation.company_id`, preserving SQLite's first-left-column interpretation
where PostgreSQL rejects ambiguous `USING(company_id)`.

## Explicit application and verification

Install `scrapper/requirements.postgresql.txt`, create the isolated service
described in [POSTGRESQL_BACKEND.md](POSTGRESQL_BACKEND.md), and configure the
native environment. An ordinary connection never applies migrations.

```python
from scrapper.db.postgresql import PostgreSQLSettings, connect
from scrapper.db.postgresql_schema import migrate, check_compatibility

settings = PostgreSQLSettings.from_env()
with connect(settings) as conn:
    migrate(conn, "trading_ai_candidate")
with connect(settings, read_only=True) as conn:
    check_compatibility(conn, "trading_ai_candidate")
```

Use a dedicated application schema; `public`, system schemas and unsafe names
are rejected. An advisory transaction lock serializes migration and transfer.
Each file and its ledger insert run transactionally. Unknown identities, altered
SQL checksums, changed applied prefixes and structural drift fail closed. The
ledger records a structural signature after each applied migration. Compatibility
checks verify live columns, types, nullability, constraints, indexes, views,
enabled internal/user triggers, trigger functions and sequence configuration.
They never refresh the signature. A partially applied prefix can receive the
next reviewed migration; a failed upgrade leaves neither its DDL nor ledger row.

Schema fingerprints are local deployment evidence, not portable certification
across PostgreSQL major versions or restore-generated object identities. A
restored schema needs a separately reviewed structural certification; never
rewrite the ledger to silence a drift error.

## Maintenance and rollback

The development generator reads only synthetic SQLite migrations 001–076:

```sh
python -B scripts/audit/generate_postgresql_baseline.py
```

Generated SQL and its source/SQL hashes are committed for review. The runtime
runner executes committed PostgreSQL SQL; it never translates SQLite DDL.
Once released, do not regenerate PG001 to fold in SQLite 077 or later. Add a
new reviewed PostgreSQL migration and manifest entry. Preserve the baseline and
SQLite files. Pending application repository ports and operational acceptance
still prevent PostgreSQL from becoming authoritative.

There are no automatic down migrations. A failed application rolls back its
transaction; an accepted production rollback uses the unchanged verified SQLite
database and the operator procedure in
[POSTGRESQL_TRANSFER.md](POSTGRESQL_TRANSFER.md). Never drop a populated schema as
a normal retry operation. Production schema application and cutover each require
explicit operator authorization.

Tests use complete synthetic schemas. The live suite verifies all object names,
column order/types/nullability, PK columns, FK column coverage/validation,
CHECK counts, view resolution, representative trigger guards, generated IDs,
exact strings, migration idempotency, checksum/drift rejection, sequential
upgrade failure and rollback. It does not certify production records or worker
repository parity.
