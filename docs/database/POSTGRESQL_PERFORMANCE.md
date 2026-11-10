# PostgreSQL query and ingestion benchmarks — issue #56

This is synthetic PostgreSQL 16 evidence, not a production health report or a
cutover approval. The application remains unchanged until an operator explicitly
applies the new migration. No production database, mount, credential or runtime
was accessed. The benchmark creates and drops only a fresh UUID schema in an
explicitly disposable test database.

## Reproduce

Install `scrapper/requirements.postgresql.txt`. Use a new PostgreSQL 16 cluster
or the isolated development service in [POSTGRESQL_BACKEND.md](POSTGRESQL_BACKEND.md),
with an explicitly disposable database. Do not use a production DSN.

```sh
export TRADING_AI_ENV=test
export TRADING_AI_POSTGRESQL_TEST_DSN='postgresql://<test-login>@127.0.0.1:<test-port>/<disposable-database>'
python -B -m scrapper.research.postgresql_benchmark \
  --companies 5000 --years 10 --repeats 7 --batches 12 \
  --disposable-database --output /tmp/issue56.json
```

The CLI does not read `DATABASE_URL`. It requires all three test guards. It
migrates its new schema explicitly, removes the candidate index there for the
baseline, and restores it for the comparison. It never modifies an existing
application schema or its ledger. Cleanup runs after success or failure; an
existing UUID-name collision is never dropped. A killed process can leave its
own disposable schema; inspect ownership before any manual cleanup.

For a fast software contract check, use `--companies 10 --years 1 --repeats 2
--batches 1`. The native tests run that scale in the existing isolated PostgreSQL
CI job. They check plans, exact result equivalence, worker completion, ingestion
row/value invariants and schema cleanup. Offline tests check input guards and
exact-value/multiset comparison. They do not enforce machine-dependent timings.

## Dataset and workload contract

The full run uses 5,000 synthetic companies, 2016–2025:

- 4,000,000 financial observations: 40 quarterly periods × 20 line items per
  company. Decimal strings remain `000123.4500`; no float conversion or semantic
  normalization occurs. Each company has one synthetic parse/blob and current
  accepted pointer. This represents wide statement histories, not realistic
  page revision distributions or a point-in-time research certification.
- 600,000 acceptance events: 40 periods × three history events per company.
  The baseline/indexed mixed load each adds 2,400 events; the baseline additions
  are removed before the index comparison. Initial and final counts are separate.
- 200,000 document pages: one 40-page extraction per company, with deterministic
  repeated text. This covers payload lookup, not OCR cost, PDF byte storage,
  million-document corpus capacity or extraction-worker throughput.
- 12,600,000 prospective daily candles: 252 weekdays per year × ten years ×
  5,000 companies. Calendar days are synthetic and do not model Indian exchange
  holidays. Exact NUMERIC prices and BIGINT volume are benchmark-only types.
  There is no candle table or candle ingestion repository in the current app.

The benchmark records every query, synthetic parameter, full JSON `EXPLAIN
(ANALYZE, BUFFERS)` plan, summaries of seven warmed wall-clock latency samples, result count
and SHA256 of the complete result multiset. Each wall sample includes fetching
all results but excludes hashing; plans run separately. Unordered SQL is
compared as a multiset without losing duplicate multiplicity or exact strings.
The company-period query keeps the existing text-date ordering semantics.

Critical paths traced to application code:

- `scrapper/research/features/financial.py:company_quarter_features`: complete
  acceptance history filtered by company and reporting scope. PG001 has only
  the event primary key. The benchmark tests `(company_id, reporting_scope)`;
  no timestamp key is justified by this query, which has no SQL date predicate.
- `scrapper/db/screener/acceptance.py:sync_screener_quarter_state`: accepted
  company/scope → financial periods → parse/blob provenance, latest quarter.
- `scrapper/view/render.py:_section_matrix` and `scrapper/exports/json_export.py`:
  financial cells for parse/section, ordered by source row/column. The existing
  unique key `(parse_id, section_code, row_position, column_position)` matches.
- `scrapper/db/document_artifact_repo.py:_ensure_company`: exact current NSE
  symbol lookup, covered by the existing partial unique identifier index.
- `scrapper/db/document_extraction_repo.py:extraction_payload`: document pages
  by extraction, ordered by page number, covered by the existing page primary key.
  The element lookup is already covered by its extraction/page/position primary
  key; element and OCR execution costs are outside this benchmark.

## Concurrency and ingestion

Each application variant runs four readers and two writers with a synchronized
start. Each reader performs twelve transactions containing history and statement
reads. Each writer commits twelve 100-event batches. Separate sessions preserve
transaction isolation; reported reader and writer latencies include transaction
completion. Aggregate write rows/second uses the entire mixed-load elapsed time,
including session/thread startup and waiting for readers. It is not an isolated
maximum insert throughput measurement.

Prospective candle trials compare individual parameterized INSERTs,
`executemany`, and COPY to a session-local temporary stage followed by one
ordered `INSERT ... ON CONFLICT ... DO UPDATE`. All methods use the same explicit
column assignments, exact Decimal values and composite identity. COPY alone
cannot upsert; the stage/merge transaction is essential. One and four writers
use disjoint sorted company keys, twelve daily batches each. A `new` trial inserts
new synthetic symbol IDs on twelve days; `replay` trials update existing keys.
Post-run checks verify persisted counts and every inserted/replayed price and
volume outside the timed work. Rate numerators represent verified logical rows,
including replayed rows; they are not counts of newly created records.

Each method deletes only its own fresh-symbol rows and performs VACUUM ANALYZE
outside timing before the next trial. Cache/order, filesystem, autovacuum and
heap-layout effects remain; fixed-order warm trials on one host are not an
independent randomized experiment. Small differences are not actionable.
The worker ownership pattern intentionally avoids overlapping-key deadlocks;
it does not establish arbitrary overlapping-worker throughput. Existing native
worker contract tests remain the authority for claim fencing and contention.

## Recorded result and decisions

See [issue56.json](performance/issue56.json) for the full measured evidence,
server/platform/settings and the SHA256 of the exact benchmark implementation.
Recorded on 10 November 2026 on macOS ARM64 with PostgreSQL 16.14,
128 MiB shared_buffers, 4 MiB work_mem, fsync and synchronous_commit enabled.
These are warm local-host samples, not latency objectives.

- Acceptance-history wall median: **12.385 ms → 0.170 ms** (about 73× faster).
  The first recorded plan changes from parallel sequential access (`Gather`)
  touching 10,345 shared blocks to an index scan touching six. Both return the
  same 120 rows. The index uses **4,587,520 bytes (4.38 MiB)** and builds in
  101.7 ms on this synthetic table.
- Company-period 0.396 → 0.395 ms, statement 1.057 → 1.061 ms, document pages
  0.145 → 0.145 ms and symbol 0.092 → 0.088 ms. These paths already use existing
  keys; small timing differences do not justify extra indexes.
- Four-reader/two-writer history+statement transaction median: **28.740 →
  3.870 ms**, p95 43.305 → 5.002 ms. Writer commit median: **1.589 → 2.221 ms**,
  p95 6.429 → 3.221 ms. This shows the read benefit with a measurable median
  write cost in this mixed run, not an assertion that indexing speeds writes.
  Aggregate mixed-workload write rate is 6,441 → 45,919 rows/s, with the reader
  completion-time denominator described above.
- Candle whole-market daily median: **168.772 ms** with company/date PK only,
  **2.915 ms** with an additional day/company index, **2.747 ms** with annually
  partitioned tables and the same indexes. Company-year reads are 0.316, 0.328
  and 0.337 ms respectively. The day index roughly doubles total index storage
  (397,918,208 → 800,006,144 bytes); the tiny partition read difference is not a
  basis for adopting partitioning.
- One-writer fresh candle ingestion: row INSERT **22,635**, executemany
  **69,166**, COPY/stage/upsert **325,737 logical rows/s**. Four-writer values:
  **49,658**, **57,194**, **669,356 rows/s** respectively. Four-writer replay:
  **49,677**, **55,450**, **638,347 rows/s**. Each new trial verifies 60,000
  inserted records; replay verifies all 5,000 existing symbols and exact values.
- With the day index, four-writer COPY/upsert measures **531,737 new** and
  **516,644 replay rows/s**; annual partitioning measures **557,776 new** and
  **290,678 replay rows/s**. These short fixed-order measurements justify
  keeping stage/upsert as the prospective bulk strategy and deferring partitioning,
  rather than projecting any of these rates onto production.

Migration `002_acceptance_history_index.sql` adds only
`idx_acceptance_events_company_scope(company_id, reporting_scope)`. It does not
filter history, change queries, alter financial precision, rewrite provenance,
or change SQLite. PG001's SQL/hash and baseline object catalog remain frozen;
the manifest appends PG002. Compatibility fingerprints include the added index.

No duplicate statement, symbol or document indexes are added. Prospective candle
indexing/partitioning remains confined to the benchmark. Annual partitions use
the same `(company_id, day)` primary key and `(day, company_id)` secondary key
as the indexed unpartitioned comparison. The benchmark compares company-year
reads, whole-market one-day reads and COPY/upsert writes. Partition pruning is
visible in plans; that alone does not justify the maintenance cost.

Partitioning requires future boundary creation, routing-error handling,
per-partition vacuum/index/statistics maintenance, unique keys including the
partition key, backup/restore and retention procedures. It is deferred pending
a measured benefit under the actual candle API, broader date ranges and a
representative retention workload. No partitioned application table is created.

## Verification, rollout and limitations

Native tests cover fresh migration, frozen-PG001→PG002 upgrade, fingerprint
compatibility, checksum rejection, failed-upgrade rollback and exact text
preservation. The baseline generator now refuses to overwrite the manifest once
later migrations exist. Its pure `generate()` still audits frozen PG001 bytes.

Applying PG002 to a persistent database remains an explicit operator action.
The transactional CREATE INDEX can block writes and consume disk; the small
synthetic build time is not a production lock-duration guarantee. Plan a bounded
maintenance window after verifying backups, storage and live row volume.
No automatic down migration is supplied. Failed DDL rolls back; an accepted
post-application rollback needs a new reviewed migration and fingerprint update,
not an untracked DROP INDEX that would trigger schema-drift refusal. Reverting
the source before application leaves the stored schema unchanged.

These results do not certify live data distributions, production capacity,
network latency, cold-start behavior, financial correctness of research outputs,
or a PostgreSQL cutover. They justify only the scoped index and the documented
benchmark decisions. No application runtime was activated.

### Executed checks (10 November 2026)

All native tests used the newly initialized disposable PostgreSQL 16.14 database
and application write roots under `/tmp/issue56-test-root`, following the CI
isolation contract. Benchmark Python: 3.14; final native tests: 3.12.13;
psycopg: 3.3.8. The initial native attempt used an incomplete environment and
failed on missing certifi/requests/xlrd (89 tests, nine errors); rerunning with
the complete test environment passed without code changes for those errors.

- PASS: full benchmark command above, including all result/ingestion invariants.
- PASS: `python -B -m unittest discover -s scrapper/tests -t . -p 'test_postgresql*.py' -v`
  — 147 tests, no skips, including fresh/upgrade migration and native benchmark checks.
- PASS: `python -B -m unittest scrapper.tests.test_database_migration_controls scrapper.tests.test_repository_backend scrapper.tests.test_company_quarter_features scrapper.tests.test_financial_feature_ownership -v`
  — 30 tests.
- PASS: `python -B scripts/audit/check_migration_manifest.py`.
- PASS: `python -B -m unittest scrapper.tests.test_migration_manifest scrapper.tests.test_ci_database_isolation -v`
  — six tests.
- PASS: `python -B -m unittest discover -s scripts/audit -p test_migration_lineage.py -v`
  — ten tests.
- PASS: `git diff --check`; evidence SHA256 matches the benchmark source.
- Independent review: separate read-only reviewer inspected the code, safety,
  plans, reported numbers, result equality and source hash; no material findings
  remain. The reviewer did not execute tests or access databases.
- NOT RUN locally: the entire offline/browser suite; GitHub's PR workflows run
  those broader checks. No production migration or deployment was performed.
