# Phase 4 exact production canary — STOP

Executed the user's authorized `python3 ops/activation/phase4_documents.py --execute` for Sacheerome company4320 / artifact81417 / download job144466 on11October2026. The admitted attempt started17:13:54UTC and ended17:13:59UTC with **exit1**. Scope was unchanged. No automatic production retry, background worker, historical campaign or broad review was initiated.

## Exact failure

`sqlite3.OperationalError: no such column: rowid` in `phase4_documents_runtime.py:55`, while computing the protected-table fingerprint at line71, before the document downloader is invoked. `accepted_page_versions` is a **WITHOUT ROWID** table. The prepared launcher incorrectly assumed every protected table could be ordered by implicit`rowid`. This is an implementation defect in the canary adapter, not a failed issuer request or extraction.

Host ownership, external-drive identity/capacity/sample hashes, selected source/queue identity, Docker mounts, parser contract, schema/control/empty-WAL checks passed before this error. BSE holds remained visible and unchanged. The initial sandbox invocation could not access Docker and never admitted an attempt; the exact authorized command was then run with the required external permissions. There was only one admitted production canary.

[Runtime stderr/traceback](../../artifacts/phase4/canary/runtime.stderr), [runtime receipt](../../artifacts/phase4/canary/runtime.log), [launcher outcome](../../artifacts/phase4/canary/outcome.json).

## Narrow read-only verification

[Machine-readable verification](../../artifacts/phase4/canary/verification.json), [read-only selected-record snapshot](../../artifacts/phase4/canary/verification/20261011T171446/production-inspection.json), [archive verification](../../artifacts/phase4/canary/archive-after-failure.json).

- Job144466 is still **PENDING**, attempt_count0, no claim token/lease/result/error; its timestamps and retained source URL are unchanged.
- Artifact81417 has **zero** canonical contents, download-attempt links, inbox entries, processing work, extraction outcomes or pages. Its expected SHA256 has no canonical content row. The expected original path is absent.
- Source requests **zero**; downloader/extraction worker never started; original document bytes written **zero**. Therefore the expected SHA256 and21-page extraction were **not achieved in production**. Earlier temporary/offline PDF evidence does not count as this run succeeding.
- The production main DB's device/inode/size/mtime/ctime match the runtime's pre-work snapshot exactly. WAL is empty before and after. Execution failed before document/financial DML; the SQL authorizer denies financial-table writes. No full14.6GB database checksum or financial revalidation campaign was performed; the original intended protected-row fingerprints never completed.
- All four sampled existing original files retain their recorded SHA256 and byte size. The underlying external-drive identity hash is unchanged. No original was overwritten or deleted; no duplicate artifact/content identity was created.
- The authorized host capacity metadata was published once before container startup. Existing DB owner lock files and disposable Docker resources were used. These are the only intended preparation effects; there were no successful document/processing DB transitions.
- The container exited and ownership-after verification passed. The consumed execution-started receipt is retained, so this package cannot silently be replayed.

## Remaining before unattended Phase 4

First correct the canary fingerprint to use stable declared primary-key ordering on WITHOUT ROWID tables, add one focused regression test, and prepare a new explicitly identified same-scope attempt. Do not clear this attempt's receipt or retry automatically. A successful separately authorized canary must establish actual original/provenance/inbox/work/21-page extraction outcomes before unattended activation.

Then finish the already documented controlled-dispatch scheduling, bounded issuer/official-source discovery and long-tail/final checks, unchanged-URL replacement handling, and proof of a newly arriving document through the scheduled pipeline. [Existing Phase4 implementation and remaining work](PHASE4_DOCUMENT_LIFECYCLE.md). Phase3 remains COMPLETE and its schedules were not changed. Phase4 remains **IN PROGRESS — CANARY STOPPED**.
