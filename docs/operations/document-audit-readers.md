# Document audit and freshness readers

The document evidence tools select the configured application backend. In a
PostgreSQL release they use the existing restricted reader login, verified
schema/target identity and a repeatable-read, read-only transaction. They do not
open the retained SQLite database or its sidecars. Failed native access fails the
audit; freshness returns `status: unknown` with a nonzero exit code. Neither path
falls back to SQLite.

`actual_download_audit.py`, `document_backlog_audit.py`,
`download_batch_evidence.py` and `download_failure_evidence.py` share this reader
boundary. Exact job-to-attempt joins, selection limits and physical file/hash
checks are retained. Signed access values are redacted in exported evidence;
source blobs are unchanged. Evidence records the backend, observation time and
hashed native target identifier. Native census omits the automatic historical
SQLite comparison and labels it `not_opened_in_native_mode`; report generation
must not substitute old counts for missing comparisons.

The host audit wrappers keep admission/owner locks, ownership checks, scheduler
reservations and storage preflight. Native transport verifies the sealed release
and uses its pinned image, private PostgreSQL socket, reader-only credential
subdirectory and runtime identity. Data/document mounts are read-only; only the
selected evidence output is writable. This transport is not a new general
ownership exemption. The existing reader policy already grants SELECT across
application tables and catalogs and denies application DML; no new grants,
credentials, migration or deployment are required by this code change.

## Freshness command

Explicit native observation (requires the configured reader environment and
separate authorization for any production observation):

```sh
python3 -B scripts/document-freshness --native --since 2026-11-12T00:00:00+00:00 --limit 1000
```

Explicit historical comparison input remains available, even when the configured
application backend is PostgreSQL:

```sh
python3 -B scripts/document-freshness --snapshot /path/to/quiescent-snapshot.db --since 2026-11-12T00:00:00+00:00
```

Snapshot mode requires an existing sidecar-free file, opens it immutable/read-only
and reports its path, modification timestamp and observation time as historical
provenance. These timings do not certify current native health or an SLA.

## Validation and operational boundary

`test_postgresql_document_audits` uses disposable UUID schemas/databases and
restricted reader logins, with SQLite opening forbidden during native reads.
Synthetic parity checks cover exact attempts/failures, populated backlog and
candidate selection, freshness/truncation and Unicode/NUL page character counts.
Existing offline tests cover missing/corrupt/matching physical hashes and path
escapes. Native tests are required by `scripts/audit/native_runtime_smoke.py`;
missing prerequisites or skipped native coverage fail that command.

This change does not authorize a production census, hash scan, acquisition,
requeue, document rewrite, credential change or release activation. No database
migration is added. Rollback is the prior source release; original evidence and
source bytes remain in place.
