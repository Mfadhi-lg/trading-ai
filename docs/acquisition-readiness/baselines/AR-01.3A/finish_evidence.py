"""Publish AR-01.3A evidence only after every snapshot/restore gate passes."""
import hashlib
import json
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
NATIVE = OUT / 'native-proof'


def read(path):
    return json.loads(path.read_text())


def main():
    final = read(NATIVE / 'final.json')
    if final['status'] != 'PASS':
        raise RuntimeError('Cannot publish PASS report before full proof passes')
    baseline, restored = NATIVE / 'snapshot-validation', NATIVE / 'restore-validation'
    sv, rv = read(baseline / 'validation.json'), read(restored / 'validation.json')
    for v in (sv, rv):
        assert v['status'] == 'PASS' and v['quick_check'] == ['ok'] and v['integrity_check'] == ['ok']
    for name in ('schema.json', 'ledger.json', 'rows.json'):
        assert read(baseline / name) == read(restored / name)
    fk = read(baseline / 'foreign-keys.json')
    assert Counter(map(tuple, fk)) == Counter(map(tuple, read(restored / 'foreign-keys.json')))
    old = ROOT / 'docs/acquisition-readiness/baselines/AR-01.1'
    assert Counter(map(tuple, fk)) == Counter(map(tuple, read(old / 'foreign-keys-before.json')))
    assert read(baseline / 'rows.json') == read(old / 'rows-before.json')
    original = read(ROOT / 'docs/acquisition-readiness/baselines/AR-01.3/readonly-correction/application-source-before.json')
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in original.items())
    copy = read(OUT / 'source-snapshot.json')
    end = read(OUT / 'source-final-verification.json')
    assert end['source_after'] == copy['source_before'] and end['source_sha256_after'] == copy['snapshot_sha256']
    before = read(OUT / 'source-snapshot-writer-before.json')
    after = read(OUT / 'source-final-verification-writer-after.json')
    assert before['writer_gate_passed'] and after['writer_gate_passed']
    assert not before['known_external_db_writers'] and not after['known_external_db_writers']
    ledger = read(baseline / 'ledger.json')
    assert len(ledger) == 69 and ledger[-1]['version'] == '068_exchange_source_access_compatibility.sql'
    comparison = {'status': 'PASS', 'schema_equal': True, 'ledger_equal': True, 'all_row_fingerprints_equal': True,
        'fk_identity_multisets_equal': True, 'fresh_matches_historical_full_manifest': True,
        'source_identity_and_full_hash_unchanged': True, 'application_py_files_preserved': len(original),
        'recovery_authority': 'USER', 'execution_agent': 'CODEX', 'production_migrations': 0,
        'production_restore': False, 'production_business_or_operational_writes': 0}
    (OUT / 'comparison.json').write_text(json.dumps(comparison, indent=2) + '\n')
    groups = Counter(r[0] for r in fk)
    report = ROOT / 'docs/acquisition-readiness/chunks/AR-01.3A-pre-migration-snapshot-restore-proof.md'
    body = f'''# AR-01.3A — fresh 068 snapshot and disposable restore proof

**Status: COMPLETE / PASS for AR-01.3A only.** Executed 7 October 2026 UTC / 8 October 2026 IST. Production remains schema 068; migrations 069–075 were not applied. No scheduler, daemon, downloader, scope monitor, reconciler, rating/processing/browser worker, network acquisition, queue processing, document acquisition or historical backlog work was started.

## Authorization and recovery boundary

User explicitly selected AR-01.3A: writer/schema gate, fresh snapshot, complete validation and restoration proof on disposable storage. **Recovery/authorization authority: USER. Execution agent: CODEX.** No production restore, overwrite, repair, requeue or claim clearing is authorized by this proof. On unexpected migration state Codex must stop, preserve failed DB/sidecars, report exact state and wait. A future restore requires proof that every post-snapshot change was migration-only **and** explicit user authorization. Generalized post-business-write recovery is outside this bounded phase and remains required before enabling write-capable workers/schedulers.

The previous server interruption occurred before execution: no snapshot destination, driver log/final result or active run existed when checked. The single production source copy began at 2026-10-07T18:23:25Z and passed at 18:24:48Z. It was not repeated. [Execution log](../baselines/AR-01.3A/driver.log).

## Repeated quiescence and exact schema gate

[Fresh canary result](../baselines/AR-01.3A/fresh-canary/result.json), [source-copy before gate](../baselines/AR-01.3A/source-snapshot-writer-before.json), [source-copy after gate](../baselines/AR-01.3A/source-snapshot-writer-after.json), [final before gate](../baselines/AR-01.3A/source-final-verification-writer-before.json), [final after gate](../baselines/AR-01.3A/source-final-verification-writer-after.json) prove all 52 observed Docker containers were stopped at the boundary inventories, including all 15 Trading AI project containers. No known external DB writer or user cron was observed. Host process/launch inventories distinguish the existing document-capacity publisher definition from a DB owner; it was not started, repaired or stopped.

Production had exactly the reviewed 068 DDL, 69 full ledger identities/timestamps, latest `068_exchange_source_access_compatibility.sql`; only 069_worker_claim_fencing.sql, 070_document_extraction_claims.sql, 071_shared_host_permits.sql, 072_document_processing_work.sql, 073_exchange_coordinator_tasks.sql, 074_collection_scope_tasks.sql and 075_periodic_dispatch.sql pending. Unknown applied migrations: none. The pinned application's pending-schema block is expected. No partial pending DDL or new processing table was present.

These are bounded inventories, not a permanent lock against future operator activity. Repeat the writer/identity/sidecar gate immediately before any later authorized migration; source drift invalidates use of this snapshot as its baseline.

## Fresh source snapshot, hashes and identity

Source: existing `trading-ai_scraper-data` volume, main file `/source/scraper.db` in the byte-copy container, mounted **read-only**. The snapshot copier never opens production SQLite, checkpoints, replays or changes journal mode. The preceding schema canary uses the separately reviewed guarded immutable read only. The copy-stage writer gate was {copy['writer_gate_age_seconds_at_start']:.6f} seconds old when checked.

Source before/after and again after the full restore proof: **{copy['snapshot_bytes']:,} bytes**, device {copy['source_before']['device']}, inode {copy['source_before']['inode']}, mtime/ctime {copy['source_before']['mtime_ns']} ns. WAL, SHM and rollback journal absent at every source copy/hash boundary; therefore no required sidecar bytes were omitted. This executes the reviewed WAL-free branch. An unexpected sidecar would stop the operation, not authorize immutable reads, checkpointing or replay on production.

Source-before, source-after-copy, raw snapshot, disposable validation copy, disposable restored copy and final production source all reproduce **SHA256 `{final['source_sha256']}`**. Source identity/size/mtime/ctime and sidecar state match exactly across the proof window. The matching historical audit hash is corroboration; this snapshot was freshly copied from the real production volume, not reused from the old audit clone. [Source-copy evidence](../baselines/AR-01.3A/source-snapshot.json), [final source evidence](../baselines/AR-01.3A/source-final-verification.json).

Durable raw snapshot: `{final['snapshot_path']}`, mode **0444**. Host validation copy retained under `artifacts/acquisition-readiness/AR-01.3/pre-migration-068/validation/scraper.db`. Verified native disposable validation volume: `{final['snapshot_validation_volume']}`. Independent disposable restore-proof volume: `{final['restore_volume']}`. Both disposable volumes have `org.trading-ai.disposable-proof=AR-01.3A` labels and contain `/scraper.db`; neither is the production volume. They remain retained, not deleted or reused. [Volume identities](../baselines/AR-01.3A/native-proof/volumes.json), [snapshot copy identity/hash](../baselines/AR-01.3A/native-proof/seed-snapshot.json), [restored copy identity/hash](../baselines/AR-01.3A/native-proof/seed-restored.json).

Copied files have separate paths/inodes/creation metadata; byte hash, size and complete logical manifests are the equality criteria. No production inode/file was replaced. Raw snapshot and disposable copies are local retention; keep them and their evidence until the migration-only window is resolved.

## Complete snapshot validation and restore comparison

Both independently validated disposable copies use the same pinned C28.1 image `trading-ai-c281-release@sha256:4d289faa67334bc870d4f2a6a181feecee777fd427eac209b9adef68ecab0238`, source `c3ddbbb9c298d07b8b46a362996251417282476a`, Python **3.12.14**, SQLite **{sv['sqlite']}**. Their input volume is read-only; immutable mode is used only on quiescent standalone copies without sidecars, with `PRAGMA query_only=ON`. Neither validator mounts production DB or document storage or invokes application entrypoints/migrations.

- Snapshot `quick_check`: **ok**; `integrity_check`: **ok**.
- Restored copy `quick_check`: **ok**; `integrity_check`: **ok**.
- Exact `sqlite_master` schema and ledger filename/timestamp records: **equal**, latest 068, 69 identities.
- All **{sv['table_count']} original tables / {sv['row_count']:,} rows**, including the ledger: counts, baseline columns and SHA256 fingerprints **equal** between fresh snapshot and restored copy; they also match the retained AR-01.1 full original manifest. Non-ledger original rows: **{sv['row_count'] - len(ledger):,}**.
- Source, retained snapshot and recreated file SHA256 match; both validators rehash their input after scanning and find no change.

Fingerprint method exactly follows AR-01.1: every original column, stable rowid order (declared primary-key order for WITHOUT ROWID tables), length-prefixed pickle protocol 4 tuples, streamed SHA256. No accepted version, timestamp, immutable-content reference/hash, queue attempt, claim or lineage field is exempt; raw row payloads are not exported.

[Snapshot checks](../baselines/AR-01.3A/native-proof/snapshot-validation/validation.json), [restored checks](../baselines/AR-01.3A/native-proof/restore-validation/validation.json), [snapshot schema](../baselines/AR-01.3A/native-proof/snapshot-validation/schema.json), [snapshot ledger](../baselines/AR-01.3A/native-proof/snapshot-validation/ledger.json), [snapshot fingerprints](../baselines/AR-01.3A/native-proof/snapshot-validation/rows.json), [restored fingerprints](../baselines/AR-01.3A/native-proof/restore-validation/rows.json), [independent comparison](../baselines/AR-01.3A/comparison.json).

## Full FK identity baseline

Fresh full `PRAGMA foreign_key_check` returns **{len(fk):,} preexisting violations**: {groups['exchange_screener_enqueues']:,} `exchange_screener_enqueues` and {groups['screener_company_queue_events']:,} `screener_company_queue_events`, referencing missing `screener_company_queue` parents. Exact `(table,rowid,parent,fkid)` identity **multisets** equal the earlier AR-01.1 baseline and the recreated copy. No replacement/new/missing identity is hidden by aggregate counts. No historical violation was repaired or ignored.

[Fresh FK identities](../baselines/AR-01.3A/native-proof/snapshot-validation/foreign-keys.json), [restored FK identities](../baselines/AR-01.3A/native-proof/restore-validation/foreign-keys.json). Future migration acceptance remains **zero NEW FK identities**, with both multiset differences and original-row projections checked. Integrity success does not imply zero historical FK violations.

## Exact commands, isolation and retained execution outcomes

The source-copy orchestrator was `python3 docs/acquisition-readiness/baselines/AR-01.3A/snapshot_driver.py`. Native disposable validation continuation was `python3 docs/acquisition-readiness/baselines/AR-01.3A/native_restore_proof.py`. All exact Docker arguments are retained as structured command arrays: [source copy](../baselines/AR-01.3A/source-snapshot-command.json), [snapshot seed](../baselines/AR-01.3A/native-proof/seed-snapshot-command.json), [snapshot validation](../baselines/AR-01.3A/native-proof/snapshot-validation-command.json), [restored seed](../baselines/AR-01.3A/native-proof/seed-restored-command.json), [restored validation](../baselines/AR-01.3A/native-proof/restore-validation-command.json), [final source verification](../baselines/AR-01.3A/source-final-verification-command.json).

Every helper uses the pinned local image, `--rm --pull never --no-healthcheck --network none --read-only --cap-drop ALL --security-opt no-new-privileges`, 1 CPU/1536 MiB and ephemeral `/tmp`; Python entrypoint overrides normal runtime. Copy operations write only newly authorized snapshot/disposable targets; source production mount remains read-only. Validators write only evidence and read only disposable DB copies. The image's automatic anonymous `/data` volume is separate from production and unused by these scripts. No secrets/document mount. Network traffic observed was zero.

The initial macOS bind-mount validator made no check result and was deliberately stopped after prolonged I/O-bound `quick_check`; its stdout, empty stderr, runtime mounts and original driver FAIL record are preserved. This was an **incomplete performance-limited validation**, not evidence of corrupt data or unexpected migration state. Validation then moved to isolated native disposable storage using the same pinned interpreter/SQLite and existing verified raw snapshot. The production source copy was never repeated, no migration was attempted/retried, and no production recovery occurred. Final authority for the completed proof is [native-proof/final.json](../baselines/AR-01.3A/native-proof/final.json), not the retained incomplete bind-validation record.

Both completed native validators and copy seeders exit successfully with empty stderr. Full stage stdout/stderr files are retained in their respective evidence directories. The final source-hash verification and repeated quiescence gate also pass. [Final proof](../baselines/AR-01.3A/native-proof/final.json) records zero production migrations/writes/restores. Application source remains unchanged: {len(original):,} preexisting Python file hashes preserved.

## Migration-only readiness and stop

This proof clears snapshot/restore prerequisites **only for a separately authorized migration-only 069–075 window with no operational/business writes**. It does not authorize those migrations now, any worker/scheduler/service, source request, historical recovery or generalized post-write restore. Before a future migration, repeat writer/identity/hash/sidecar checks against this exact baseline; preserve the snapshot. On unexpected migration state, stop, preserve failed DB/sidecars, report, do not retry, and seek user authorization. Never restore this pre-change snapshot over legitimate new business/operational writes or claims. Do not independently restore even when the migration-only condition appears satisfied.

Evidence hashes, scripts, command arrays, full manifests and raw outputs are retained in [SHA256SUMS](../baselines/AR-01.3A/SHA256SUMS). No migration activation was performed after this proof; stop here.

RECOVERY_AUTHORITY:
USER

EXECUTION_AGENT:
CODEX

PRODUCTION_WRITERS_QUIESCENT:
YES

FRESH_068_SNAPSHOT:
PASS

SNAPSHOT_INTEGRITY:
PASS

RESTORE_PROOF_ON_DISPOSABLE_COPY:
PASS

FK_BASELINE_CAPTURED:
YES

ORIGINAL_DATA_FINGERPRINTS_CAPTURED:
YES

SAFE_TO_AUTHORIZE_MIGRATION_ONLY_069_075:
YES
'''
    report.write_text(body)
    files = sorted(p for p in OUT.rglob('*') if p.is_file() and p.name != 'SHA256SUMS' and '__pycache__' not in p.parts) + [report]
    (OUT / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT)}\n' for p in files))
    print(json.dumps(comparison, indent=2))


if __name__ == '__main__':
    main()
