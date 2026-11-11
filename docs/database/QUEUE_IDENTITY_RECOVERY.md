# Historical Screener queue reference recovery

The retained 2026-11-10 rehearsal snapshot contains 10,771 historical child
references to 5,376 missing queue IDs. These exact violations were retained in
October evidence. Original parent contents cannot be recovered completely from
the inspected backups; recreating pending jobs would invent operational state
and conflict with newer natural-key identities.

SQLite migration 077 and PostgreSQL migration 003 separate immutable queue
identity from operational jobs. `screener_queue_identities` records real queue
identities and explicitly evidenced `historical_reference` identities. No queue
job, event or enqueue row is synthesized, deleted or repointed. New audit
references require a real operational parent. Queue IDs cannot be reused;
queue rows cannot be deleted. SQLite replacement guards cover IDs, natural
keys and claim tokens. Runtime ledger writes are insert-only, require a real
queue row and cannot create historical references.

Normal initialization refuses unapproved orphan references. The explicit
`python -B -m scrapper.db.queue_identity_recovery` command accepts a standalone
snapshot, its reviewed SHA256, a NEW target and NEW report, and
`--allow-rehearsal-write`. It pins the source read-only and requires the exact
retained historical violation-set fingerprint. It seeds identities only on the
new copy, applies checksum-pinned migration 077, compares all original columns
of queue/events/enqueues and migration ledger, checks foreign keys and catalog,
and appends the new migration identity. Writes to unrelated application records
are denied during migration; SQLite schema/statistics housekeeping is allowed.

Preparation is not a verified export. Failure can retain a partially prepared
copy; never reuse or activate it. A fresh successful candidate still requires
normal full integrity, every-table digests, import/rerun, schema/sequence checks
and SQLite/PostgreSQL restore verification. Retain source/candidate checksums,
identity provenance and preparation/export/import/restore reports privately.
Source manifests and PG001 remain frozen; the SQLite077 profile is separate.

This change does not authorize production access, repair, shutdown, deployment,
backend cutover or acquisition activation. Prove recovery on isolated copies
first. Production migration requires a fresh consistent authorized snapshot,
reviewed preservation results, a drained writer window and explicit approval.
