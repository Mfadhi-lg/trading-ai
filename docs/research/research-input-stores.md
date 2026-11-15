# Research input boundaries (#104)

Current application input and retained evidence have separate contracts. No
research database is migrated, recalculated or rewritten by this change.
Observation time is the time a store was captured/read, not event publication,
decision-time availability or proof of historical universe membership.

## Current application readers

- `experiments/historical_candle_archive_v1/prepare.py::main` reads only a verified
  native application transaction by default. The former automatic
  `latest-acquisition-tables.json` projection and October master fallback are no
  longer selected. Reads have a 100,000-row ceiling per planning query and a
  20-second statement budget; exceeding a bound fails rather than truncating.
  The existing company/security joins, exact identifier resolver, eligibility
  observation dates and disabled full-universe execution gate remain. A saved
  pilot must match newly observed company/key/symbol mappings or preparation
  fails for explicit cohort review. It never silently submits retained keys.
- `scrapper/research/focus/build_p0_governance.py::_main` uses the same native
  reader. SQLite date arithmetic is replaced with the identical Python 365-day
  lower bound. Existing label authority, approval and non-queueable gates remain.
  `created_at_utc` remains the report's existing reproducibility parameter;
  `database_source.observed_at_utc` records the independent input observation.
- `scrapper/research/input_reader.py::observation` uses the central application
  factory, which verifies target identity, schema/lineage and restricted login.
  It opens repeatable-read/read-only MVCC and records nonsecret target ID,
  schema, migrations and server observation time. Native failure is an error;
  it never opens SQLite. The application backend must explicitly be PostgreSQL.
  This code does not authorize production access or activate a deployment.

Explicit cold SQLite reproduction remains available for these two entry points:

```sh
python3 -B -m experiments.historical_candle_archive_v1.prepare \
  --frozen-db /path/to/reviewed-copy.db --frozen-sha256 EXPECTED_SHA256 \
  --observed-at 2026-10-08T00:00:00Z
python3 -B -m scrapper.research.focus.build_p0_governance \
  --db /path/to/reviewed-copy.db --frozen-sha256 EXPECTED_SHA256 \
  --observed-at 2026-10-08T00:00:00Z --output-dir /path/to/isolated-output
```

Use the reviewed capture time, never a guessed historical event date. The reader
requires the explicit path, SHA256 and timezone-aware capture time together;
refuses sidecars; hashes before and after; records schema migration versions;
and uses immutable/query-only access. Changed evidence invalidates the run;
discard its outputs. Use isolated output directories. Old plan/evidence files
are retained, not automatically upgraded to verified current exports.

## Deliberately retained stores

- `earnings_reaction_v1/er03.py`: frozen October acquisition snapshot, fixed pilot
  and bounded filing periods. Exact selected source records and input candles
  have SHA256 provenance in `reproducibility.json`; the historical runner
  explicitly does not certify a full database hash. Its approximate windows,
  descriptive labels and retrospective limits are unchanged. It is not a
  current application reader. A new frozen bundle should preserve its selected
  record hashes, original schema/version and named snapshot capture metadata.
- `earnings_reaction_v1/event_isolation.py`: named frozen SS04 and acquisition
  snapshots. Preparation retains original filing bytes, source hashes,
  document schema, database size/mtime and the dated audit cutoff. `pilot` and
  `build` replay the exported evidence without database access. Source byte
  checksums certify the retained inputs, not complete exchange coverage or
  unknown dissemination times. Existing historical bundles are not relabeled.
- `finance_only_v1/pilot.py`: pinned frozen snapshot, exact SHA256, expected
  migration count/version, explicit cutoff, nonwritable cold-file requirement
  and before/after preservation checks. Keep its original four-company cohort,
  source-cell lineage and labels. No native port is needed for reproduction.
- `technical_integration_v1/report.py::legacy_evidence`: compares frozen SS04
  with the SS01 research store. Its variable `current` means the later research
  revision, not current application data. Original fingerprints, study cutoff
  and chart/availability limitations remain authoritative.
- `scrapper/research/focus/build_p0_governance.py`: the maintained CLI is current
  by default; deliberate frozen replay requires the explicit contract above.
  Existing FG00 artifacts/approvals are retained historical evidence and are not
  regenerated or authorized for training by this patch.
- `scrapper/research/subsector_ss01`: explicit archived master projection into
  the standalone research database. Dry-run/apply binds original table SQL/rows
  through `master_logical_sha256`, source hashes and migration hashes. Q2 guards
  isolated writes and in-memory rollback trials. Preserve labels and unknown
  historical timestamps.
- `subsector_ss02`: enriches that same frozen research store with retained
  official metadata and dated attestations, retaining source observation IDs,
  pre-change snapshot and original fingerprints. Do not refresh it from native
  application data or treat later descriptions as historical knowledge.
- `subsector_ss03`: isolated copies of SS01 plus local research schema/pilot
  tables. Its fixed temporary copy locations are existing research-tooling debt,
  not a native application source or authorization to overwrite retained data.
- `subsector_ss04`: copies SS01 research and applies SS04 taxonomy/decisions,
  fingerprinting original tables. `scrapper/indices/catalog.py::research_catalog`
  reads it explicitly, emits its SHA256 and `RETROSPECTIVE_ONLY` membership.
  A candidate catalog is not a point-in-time trading universe. Native refresh
  must not silently substitute current classifications for these frozen inputs.
- `scrapper/research/document_intelligence/source_review.py::Index`: disposable
  in-memory FTS5 scratch index of retained text, with manifest SHA256 in output.
  It is neither an application database nor persisted research evidence; keep
  SQLite and dispose with the process. No native port is required.

A future transfer of standalone SS01–SS04 research stores requires its own
bounded schema/data/provenance task. No new historical as-of assertion follows
from an application's backend migration.

## Verification contract

`test_research_input_reader` uses synthetic cold files and disposable native
schemas/reader roles. It covers explicit provenance, changed evidence rejection,
no fallback, read bounds, company mapping, stale pilot rejection and Focus date
queries/report provenance. Native tests are required by
`scripts/audit/native_runtime_smoke.py`; ordinary offline skips are not native
passes. Historical source certification still needs the retained originals and
is not claimed by these software tests.

## Verification record (15 November 2026)

- PASS: `sh /private/tmp/issue96-verify.sh -m unittest scrapper.tests.test_research_input_reader scrapper.tests.test_fg00_p0_governance -v` — 35 tests, no skips. Synthetic Focus source ingestion and negative-pool assembly were mocked; the builder, native reader, identity/date queries and output provenance executed.
- PASS: `sh /private/tmp/issue104-verify.sh scripts/audit/native_runtime_smoke.py` — 47 tests, no skips.
- PASS: `sh /private/tmp/issue104-verify.sh -m unittest scrapper.tests.test_sqlite_usage_inventory scrapper.tests.test_ci_database_isolation scrapper.tests.test_experiment_lifecycle scrapper.tests.test_indices_application scrapper.tests.test_phase5_source_review -v` — 23 tests in `/private/tmp/issue104-source`, an exported staged-source view excluding unrelated local edits.
- PASS: `python3 -B scripts/audit/sqlite_usage.py --index` — 174 classified files, 756 source sites.
- PASS: `git diff --cached --check`.

The wrappers used test-only filesystem roots, PostgreSQL 16 at local port 55496
in a newly initialized `/private/tmp/issue104-pg` cluster, and the explicitly
disposable `trading_ai_test` database/UUID fixture schemas. Initial broad native
runs failed for missing/conflicting local dependencies; the final passing smoke
used the existing temporary dependency bundles plus PyArrow and a consistent
pure Python psycopg implementation. No guard or test assertion was weakened.

An additional exploratory command,
`sh /private/tmp/issue96-verify.sh -m unittest experiments.earnings_reaction_v1.tests.test_er03 experiments.earnings_reaction_v1.tests.test_event_isolation scrapper.tests.test_indices_application scrapper.tests.test_phase5_source_review scrapper.tests.test_experiment_lifecycle -v`,
failed with four missing retained-bundle errors and one experiment-register
failure caused by unrelated untracked candle-recovery code. The staged-source
register check above passed. Historical ER03/event-isolation source certification
remains unavailable without the retained bundle; its inputs were not regenerated.

Independent read-only review identified a stale-pilot mapping gap, corrected
before the passing tests, and reported no remaining blockers. The reviewer did
not independently execute tests. No production access, acquisition, deployment,
migration or retained-data modification occurred. Rollback is source reversion;
there is no data migration to reverse.
