# Phase 4 canary fingerprint correction — ready for separately authorized retry

11 October2026 correction evidence. **12October update:** the separately authorized same-scope attempt02 was executed and stopped at TLS certificate validation, after the repaired fingerprint succeeded. [Actual attempt02 outcome](PHASE4_CANARY_ATTEMPT02_OUTCOME.md). The preparation narrative below is retained evidence and does not authorize replay. Phase3 remains closed; Phase4 remains in progress. The failed original attempt and all its receipts remain at`artifacts/phase4/canary/`.

## Minimal correction

The actual production primary key of `accepted_page_versions` is `(company_id, source, reporting_scope, resolved_source_period)`, in that order. It is a WITHOUT ROWID table. The other protected tables use `financial_observation_id` and `event_id` respectively.

`phase4_documents_runtime.fingerprint` now reads primary-key positions from `PRAGMA table_info`, quotes identifiers and orders by the declared key instead of assuming rowid. Ordinary tables use their declared key too; ordinary tables without a declared key use an unshadowed SQLite row-key alias. Nullable composite keys have deterministic value/type tie-breakers. An unknown table or inaccessible row key fails closed.

The initial straightforward Python serialization passed the regression tests but exceeded the read-only preflight's260-second budget. That timed-out read-only container was stopped; no canary/claims/writes occurred. The helper now streams SQLite-produced typed JSON into SHA256, preserving INTEGER/REAL/TEXT/BLOB/NULL distinctions, binary bytes and exact round-trip real representations without building/sorting a Python dictionary for every production row. This changes only an internal before/after fingerprint encoding, with no persisted history, processing identity, schema or work-contract change.

The existing post-write preservation assertion uses the same repaired helper and still raises `Protected production data changed` on actual changes. SQL write authorizers, ownership/lease guards, empty-WAL checks, physical DB/storage identities, source/media/hash restrictions, capacity reserves, single-document/work limits, parser contract and300-second deadline remain intact.

The launcher gained only an optional `--attempt-dir` to store an independently sealed retry package. Default behavior still refuses the consumed attempt. No failure receipt, queue row or SQLite sidecar was reset or removed.

## Focused validation and actual read-only production preflight

**8 tests passed.** They cover composite WITHOUT ROWID keys and PK declaration order, repeated/reinserted stable fingerprints, ordinary integer keys, no-PK/shadowed-rowid tables, nullable-key tie-breakers, real inserts/updates/deletes triggering the actual preservation assertion, precise floating-point and binary/type changes, quoted identifiers and unknown-table rejection. [Test log](../../artifacts/phase4/fingerprint-correction/focused-tests.log).

**Production read-only preflight PASS**, observed18:18:32UTC. The repaired helper computed and rechecked all three protected tables in **149.44 seconds total**, within the existing read-only budget and unchanged canary deadline. Exact keys and SHA256 results are recorded in the receipt. Job144466 stillPENDING/attempt0; artifact81417/company4320/source URL/eligibility match; no content already exists for the selected artifact/hash. Database/schema/control identities and lease guards passed. DB/sidecar metadata remained unchanged throughout. Source requests0, claims0, production writes0, document/extraction/background workers0. Physical drive identity/reserves and four retained archive sample hashes passed via read-only checks; capacity metadata was not republished.

[Production preflight receipt](../../artifacts/phase4/fingerprint-correction/production-preflight-02/production-inspection.json), [host/read-only outcome](../../artifacts/phase4/fingerprint-correction/production-preflight-02/production-inspection-outcome.json), [storage evidence](../../artifacts/phase4/fingerprint-correction/production-preflight-02/storage-preflight.json). The original timeout remains recorded separately at`fingerprint-correction/production-preflight/outcome.json`.

Existing97-test Phase4 lifecycle evidence and the successful isolated21-page extraction are reused; no unrelated lifecycle rerun, migration, backup, release review/build or infrastructure change was performed. Phase3's installed source/configuration hashes still match.

## Exact retry package — execution disabled

A separate package is prepared at`artifacts/phase4/canary-attempt-02/`. Its **entire plan is identical** to the failed attempt's plan: Sacheerome artifact81417/job144466, one634,502-byte original with expected SHA256`f6101da1213c2893484b94861f85e66375726e12b78a313b97298c63586ca3a3`, one automatic extraction/21pages, maximum10MiB response, zero redirects,300seconds, zero financial writes or other backlog/worker work. The automatic processing selector stays fenced to artifact81417 alone. Application sources/image/parser versions are unchanged; the corrected runtime seal matches the runtime that passed the read-only preflight. Docker configuration validation passed. Authorization is false and no execution-started receipt exists for this package.

[Preparation verification](../../artifacts/phase4/canary-attempt-02/preparation-verification.json), [exact unchanged plan](../../artifacts/phase4/canary-attempt-02/plan.json).

Only after separate authorization, the retry command is:

```sh
python3 ops/activation/phase4_documents.py --attempt-dir artifacts/phase4/canary-attempt-02 --execute
```

Fresh execution-time ownership, source/queue identity, sidecar and physical storage safety checks remain mandatory; a future failed guard still stops execution. No production success is claimed from this read-only preflight. After a successful authorized canary, controlled scheduling and the previously documented source/revision/long-tail connections still gate unattended Phase4 activation.
