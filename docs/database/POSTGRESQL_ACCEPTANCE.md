# PostgreSQL acceptance and remaining evidence (#105)

Source review date: 16 November 2026. **PARTIAL; current production acceptance
is UNKNOWN.** Closed issues #52–#57 and #96–#104 certify scoped implementations,
not whole-system migration. [#106](https://github.com/Mfadhi-lg/trading-ai/issues/106)
tracks aggregate completion, [#107](https://github.com/Mfadhi-lg/trading-ai/issues/107)
retained operational readers, and [#33](https://github.com/Mfadhi-lg/trading-ai/issues/33)
health consolidation. No production access or runtime changes occurred in this task.

## Application acceptance matrix

Each item separates source contracts from missing operational evidence. UNKNOWN
blocks acceptance of that item; it is not a data-loss finding.

- **Backend:** `scrapper/db/database.py`, `postgresql_application.py` and
  `ops/activation/postgresql_runtime.py` implement explicit native routing and
  target checks. The [14 November recovery](../operations/monitor-error-fix-20261114.md)
  documents native acquisition. Current backend/target: UNKNOWN pending authorized
  observation. Supported SQLite compatibility and explicit frozen snapshots are
  distinct from authoritative native operation; application selection migrates no
  side store.
- **Installed release:** the recovery record reports release
  `monitor-approved-20261114T141317Z`, image
  `sha256:254001745bba3543daa6561508b8c00e1e4c9f6bd801d54fc646286f3f841795`
  and 517 verified application files. Current pointer, image and per-file hashes:
  UNKNOWN. Selected source equality does not prove that a worker executed it.
- **Schema/roles/identity:** `postgresql_schema.check_compatibility`,
  `postgresql_permissions` and `postgresql_release` implement lineage, structural
  and target/role checks. Current migration checksums, validated constraints,
  enabled triggers, reader/writer grants and database identity: UNKNOWN. Do not
  refresh fingerprints or credentials to suppress drift.
- **Original transfer:** every reviewed SQLite-profile table, including empty
  tables and `schema_migrations`, needs exact count/digest evidence at the cutover
  watermark. The native migration ledger is separate. Successful original transfer
  reports, snapshot custody and watermark binding: UNKNOWN in this review. The
  [failed November 10 rehearsal](../../artifacts/postgresql-rehearsal-20261110-r3/REPORT.md)
  cannot substitute for successful cutover evidence.
- **Financial/source/document/queue semantics:** `test_postgresql_transfer` covers
  exact decimal strings, NULL/blank/dash/zero, NUL-bearing UTF-8 bytes, document
  reference cycles and generated IDs. Worker/claim tests cover fencing;
  `test_postgresql_acquisition_gate` checks native constraints/orphans. Production
  source-byte manifests, FK/queue identities, current claims/leases and document
  file availability remain UNKNOWN; fixture equality cannot certify them.
- **Backup installation:** `ops/host/postgresql_backup.py` implements local
  encrypted dumps and `postgresql_health.py` observations. Installed scheduler,
  last complete archive/age, retention, offsite copy, key recovery, alert delivery,
  full-size restore duration and original document recovery: UNKNOWN. Code presence
  is not installation proof. The [recovery policy](POSTGRESQL_RECOVERY.md) specifies
  daily backups, protected retention, separate failure domain and weekly restores.

## Lane and reader matrix

Dispatch is `ops/activation/automatic_acquisition.py:run_slot`; schedule builders
are `jobs`, `step3_jobs` and `document_jobs`. Record a separate private receipt per
lane: UTC start/end, scheduled slot, installed release/image, native target,
budget, actual claims/requests, outcome and evidence checksum. IDLE, DEFERRED and
successful acquisition are different results. All current outcomes below are
UNKNOWN; the dated observations are not refreshed by this source review.

- **NSE intake:** no fresh observation; require a normally scheduled bounded receipt.
- **BSE intake:** no fresh observation; require its own receipt and admitted count.
- **Financial current/historical priorities:** recovery reports financial PASS at
  2026-11-14 14:47:49 UTC, five accepted jobs. It does not certify every historical
  priority. **Results-screen admissions** deferred on `Sep-26`; new admissions
  remain unverified, distinct from successful existing queued work.
- **Market cap (CAP):** require its own selection/policy/result receipt.
- **Document discovery:** recovery reports PASS at 2026-11-14 14:49:40 UTC.
- **Document download:** recovery records a paired financial path, not an
  independent current download result. Require original byte/hash/path evidence.
- **Document processing:** recovery reports PASS at 2026-11-14 14:44:13 UTC,
  ten jobs (nine ok, one partial), and 33 period-recovery documents checked.
  Partial extraction remains partial.
- **Rating/document recovery:** require an independent rating receipt/source policy
  result; processing/discovery success does not certify rating downloads.
- **AMC/mutual fund and manual imports:** `test_postgresql_mf_repositories` supplies
  fixture coverage. Authorized live imports remain unobserved; they are not
  implicitly active dispatcher lanes.
- **Read-only monitor/status, company/search, financial diagnostics, document
  audit/freshness and runtime status:** recovery records monitor census success at
  2026-11-14 14:50:06 UTC, not every CLI path. Native tests cover selected adapters
  in `test_native_operational_readers`, `test_postgresql_operations`,
  `test_postgresql_financial_diagnostics` and `test_postgresql_document_audits`.
  Enumerate the installed read-only CLI subcommands and observe each before
  accepting the complete surface. Mutating doctor/recovery commands are excluded.

## Secondary stores and individual dispositions

- **Historical candle inventory (#101/#102):** independent requests, datasets,
  revisions and observations plus raw JSON, Parquet and receipts.
  [Transfer](CANDLE_INVENTORY_POSTGRESQL.md) and
  [runtime selection/rollback](CANDLE_INVENTORY_RUNTIME.md) are implemented.
  Production selection/transfer, source quiescence, unresolved historical lineage,
  archive manifest and external byte restore evidence: UNKNOWN. An application
  database dump cannot recover these files.
- **Live market store (#103):** separate events, REST recovery, bars and raw files.
  The [native contract](../../scrapper/market_live/README.md) and
  `test_postgresql_live_store` cover transfer/replay/rollback. Installed selection,
  owner lease, watermark and raw-byte backup/restore: UNKNOWN. Its environment and
  target marker are independent of application routing.
- **Research inputs/outputs (#104):** the
  [per-consumer register](../research/research-input-stores.md) separates current
  application inputs, retained frozen SQLite and writable research copies.
  `test_research_input_reader` tests native current inputs. Current invocation,
  provenance and output reconciliation: UNKNOWN. Frozen evidence stays preserved;
  closing a source port does not authorize rewriting it.
- **Legacy/manual readers (#107), transfer sources, retained evidence and scratch:**
  the [SQLite inventory](SQLITE_USAGE.md) owns individual dispositions. In-memory
  scratch needs no migration; retained snapshots must not be rewritten. Legacy
  readers/healthchecks cannot certify native health before #107 disposition.
  This task does not absorb or reopen #96–#104 implementations.

## Original watermark reconciliation

The database operator and independent verifier must locate the original frozen
cutover snapshot, successful export/import reports, approved snapshot SHA256,
cutover timestamp and target identity in private retained evidence. Preserve
originals. Missing evidence stays UNKNOWN per table, with a bounded evidence
recovery assigned to the operator; do not infer intactness or loss from newer
mutable counts. Document queue-identity source adaptations and their recovery ledger.

The offline checker reads existing transfer reports only; it opens no database
and imports no driver:

```sh
python3 -B scripts/audit/postgresql_cutover_evidence.py --profile 077 \
  --export-report /private/evidence/export.json \
  --import-report /private/evidence/import.json
```

Select actual source profile 076 or 077. Every reviewed table must appear in both
reports. Exact counts and typed digests must match export, import-source and
import-destination, with successful statuses and matching snapshot hashes.
Missing reports/tables are UNKNOWN; malformed/contradictory or foreign-table
evidence is FAIL. Both exit nonzero. No arguments emits the complete UNKNOWN
table matrix and exits 1. No raw records or supplied paths are printed; keep
aggregate evidence private too.

PASS means **historical report consistency only**. It does not authenticate
reports, rehash snapshot bytes, establish watermark custody or reconcile later
native additions. Independently hash the frozen snapshot and bind reports to
approved cutover/target evidence on an authorized isolated copy. Never use the
importer as a mutable-production probe: it locks tables and may load an empty target.

Build a separately reviewed post-cutover change ledger from audit/queue/source
evidence: keys, timestamps, provenance, updates/deletions as well as inserts.
Compare a consistent native snapshot against frozen baseline plus those changes
on disposable storage. Unexplained differences stay UNKNOWN with table, owner and
next probe. Do not repair, merge or delete records during evidence collection.

## Bounded operator plan and authorization gates

1. Obtain explicit **read-only observation authorization** naming window, targets,
   restricted reader and private evidence destination. Observe pointer/image/hashes,
   target/server identity, schema/roles, schedules, stops/ownership and existing
   receipts with query timeouts and UTC timestamps. Do not run acquisition,
   migrations, backup jobs, doctor or retained-data scans under this authorization.
2. Obtain **reconciliation authorization** for exact retained reports/snapshot
   and a consistent read-only native snapshot. Assign database operator and separate
   verifier. Recover missing all-table custody evidence before integrity claims;
   keep records outside Git. A source/fixture pass is separate from this work.
3. Rehearse **restore on disposable targets**, without production credentials,
   schedulers or mounts. Verify archive hash and separate document/raw manifest;
   restore to a new empty database. Compare portable DDL, all-table digests,
   sequences, exact bytes and financial/FK/queue/document relationships. Detect
   same-count corruption, schema drift and interrupted load; record size, duration
   and failure outcomes. Synthetic tests do not meet full-size RTO or prove live
   backups. Access to retained production backups needs separate authorization.
4. Obtain separate **recovery authorization** after diagnosing each stop's cause
   and current PID/container/lease, passing both ownership gates. Propose exact
   archival with originals retained and rollback on partial failure. Never broadly
   delete stops, replay consumed slots or expand budgets. Observe one future eligible
   normally scheduled receipt per lane. The fixed SQLSTATE 42883/cursor incident is
   historical; no established data-loss finding follows from those errors.
5. Require independent sign-off and **cutover/rollback authorization** before
   changing release/backend/admissions. Before native writes, rollback preserves
   the source unchanged. After native writes, separately authorize admission freeze
   and drain, then reconcile additions before reverting to stale SQLite. Restore
   never wakes workers. Retain previous release and original source/evidence.

## Software verification contract

The required smoke gate now includes application transfer/rollback, application
backup/restore/corruption and candle transfer/restore. Previously those classes
ran only in broader discovery, whose normal skip semantics did not require them
to execute. The smoke gate fails on missing prerequisites, skips or empty coverage.
Ordinary offline tests may still skip native work; that is not passed native coverage.

Use the disposable service and isolated write roots from
[PostgreSQL CI](../../.github/workflows/postgresql.yml), then run:

```sh
python3 -B -m unittest scrapper.tests.test_postgresql_cutover_evidence -v
python -B scripts/audit/native_runtime_smoke.py
python -B -m unittest scrapper.tests.test_postgresql_transfer scrapper.tests.test_postgresql_backup scrapper.tests.test_postgresql_candle_inventory -v
```

No migration or deployment is required. Reverting the checker/tests, mandatory
test-class additions and documentation rolls back this source change. Production
acceptance remains open until UNKNOWN items have evidence or explicit operator
disposition; passing synthetic checks cannot close them.

## Executed verification, 16 November 2026

PASS on Python 3.14, PostgreSQL/client 16.14, psycopg 3.3.8, psycopg-pool 3.3.3
and PyArrow 26.0.1. Dependencies were installed only in
`/private/tmp/issue105-venv` from the repository test lock and native requirements.
The temporary wrapper below clears inherited SCRAPER/TRADING_AI/PG settings and
DATABASE_URL, sets test mode and all application write roots under a new temporary
directory, leaves the document override unset and selects only the newly created
local `trading_ai_test` database on port 55505. The cluster contained synthetic
fixtures only; it had no production credentials, mounts or workers.

```sh
python3 -B -m unittest scrapper.tests.test_postgresql_cutover_evidence scrapper.tests.test_ci_database_isolation -v
# PASS: 10 tests (six evidence checks, four isolation checks).
python3 -B /private/tmp/issue105-verify.py scripts/audit/native_runtime_smoke.py
# PASS: 57 tests, no skips, including native transfer/restore and side-store rollback.
python3 -B /private/tmp/issue105-verify.py -m unittest scrapper.tests.test_postgresql_transfer scrapper.tests.test_postgresql_backup scrapper.tests.test_postgresql_candle_inventory -v
# PASS: 27 tests, no skips.
python3 -B scripts/audit/sqlite_usage.py --index
# PASS: 174 classified files, 756 sites; two added lexical caller references reviewed.
/private/tmp/issue105-venv/bin/python -B -m pytest -q scrapper/tests/test_sqlite_usage_inventory.py -k 'not tracked_inventory_matches_reviewed_evidence'
# PASS: eight tests; one working-tree scan deselected because unrelated local edits
# are preserved. The staged-source inventory command above covers the PR source.
python3 -B scripts/audit/check_migration_manifest.py
git diff --cached --check
# PASS: canonical migration identities unchanged; no whitespace errors.
```

Initial cluster initialization/connection attempts were blocked by sandbox shared
memory/network restrictions; approved reruns used only the disposable cluster.
The first native run then failed on missing requests/certifi in the temporary
environment. Installing locked test dependencies resolved those failures before
the passing runs above. Full repository/retained-data audits and production probes
were NOT RUN. Independent read-only review found no material blocker and
independently passed all six evidence tests; it did not certify live parity.
