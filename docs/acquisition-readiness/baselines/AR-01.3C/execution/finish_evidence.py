"""Publish only a completed, independently verified two-write control canary."""
import hashlib,json,shlex
from datetime import datetime,timedelta,timezone
from pathlib import Path

OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[4]


def read(name):return json.loads((OUT/name).read_text())


final=read('final.json')
if final['status']!='PASS':raise RuntimeError('Incomplete/failed canary; no PASS report')
comparison=read('comparison.json');receipt=read('receipt.json');timing=read('canary-timing.json');window=read('observation-window.json')
pre=read('pre-write-gate.json');immediate=read('immediate-observation.json');end=read('two-minute-observation.json');last=read('final-readonly-gate.json')
claim=receipt['claimed']['claim'];row=receipt['released']['record'];prepared=receipt['prepared']
assert row==immediate['control_row']==end['control_row']==last['control_row']
assert row=={**claim,'status':'RELEASED'} and row['owner']==row['claim_token']
assert row['lease_expires_at_utc']==(datetime.fromisoformat(prepared['now'])+timedelta(seconds=60)).isoformat()
assert receipt['released']['affected_rows']==2 and final['normal_row_mutations']==2
assert comparison['changed_tables']==['document_extraction_claims'] and comparison['new_fk_identities']==comparison['missing_fk_identities']==0
assert len(pre['ledger'])==76 and all(x['work_references']==x['other_claim_keys']==0 for x in (immediate,end,last))
assert window['observed_end_epoch']>=window['exit_epoch']+120
baseline=json.loads((ROOT/'docs/acquisition-readiness/baselines/AR-01.3/readonly-correction/application-source-before.json').read_text())
assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in baseline.items())
assert json.loads((ROOT/'ops/docker/release/c28.1/runtime-identity-core.json').read_text())['authorized'] is False
command=shlex.join(read('reviewed-command.json'));start=datetime.fromtimestamp(timing['start_epoch'],timezone.utc).isoformat();exit_time=datetime.fromtimestamp(timing['exit_epoch'],timezone.utc).isoformat()
report=ROOT/'docs/acquisition-readiness/chunks/AR-01.3C-first-write-canary-execution.md'
body=f'''# AR-01.3C — first write-capable C28.1 control canary execution

**Status: COMPLETE / PASS.** Executed 8 October 2026. The exact reviewed isolated ownership-control canary was invoked once. One INSERT and one fenced ACTIVE→RELEASED UPDATE affected the same reserved key; no other DB row changed. No operational service, acquisition, processing campaign or AR-02 work was started.

## Authorization and exact existing application path

User explicitly authorized only [the reviewed plan](AR-01.3C-first-write-canary-plan.md), key `AR-01.3C/control-ownership/2026-10-08/v1`, two normal mutations, fresh gates, independent proof and two-minute read-only observation. **Recovery authority USER; execution agent CODEX.** No automatic retry, forward release/recovery, receipt deletion or DB restore was permitted or performed. The retained receipt is control ownership metadata, not an extraction job or invented document-success state.

The executed inline Python was extracted byte-for-byte from the reviewed plan, parsed without calling it during preparation, and used unchanged: `connect` → `check_schema_compatibility` → `claim_extraction` → `assert_extraction_owned` → `release_extraction`. Existing functions are in pinned source `c3ddbbb9c298d07b8b46a362996251417282476a`; image `trading-ai-c281-release@sha256:4d289faa67334bc870d4f2a6a181feecee777fd427eac209b9adef68ecab0238`, Linux ARM64. Source/platform identity was verified before admission. There was no worker entrypoint, function substitution, renewal or extra business operation.

## Fresh pre-write gates and attempt fence

[Fresh initial gate](../baselines/AR-01.3C/execution/initial-gate.json), [last pre-write gate](../baselines/AR-01.3C/execution/pre-write-gate.json), [immediate owner inventory](../baselines/AR-01.3C/execution/immediate-pre-write-owner-gate.json) prove schema075, exact unchanged reviewed DDL, 76 ledger identities/latest075, C28 schema compatibility PASS, reserved key absent, zero other claims and zero processing-work reference. All operational owners/containers were stopped; no known unknown DB writer or user cron. No host/document capacity publisher or source/network role was enabled.

Fresh source identity matched AR-01.3B exactly: **{pre['db_before']['bytes']:,} bytes**, device {pre['db_before']['device']}, inode {pre['db_before']['inode']}, mtime/ctime **{pre['db_before']['mtime_ns']} ns**, SHA256 **`{pre['db_sha256']}`**. WAL/SHM/journal absent. Required reviewed core directories and physical sentinel bind targets already existed, so the exact `connect` initialization needed no new production directory/placeholder.

The retained075 baseline copy was rehashed and seeded into a separately labelled native disposable volume. Complete **{comparison['all_tables']} table** fingerprints and full FK identities were captured before the write. This covers the full075 schema, including all eight new tables and all 76 ledger records, rather than reusing a 068-only projection. Baseline claim table was empty. [Before schema](../baselines/AR-01.3C/execution/before-fingerprints/schema.json), [ledger](../baselines/AR-01.3C/execution/before-fingerprints/ledger.json), [all fingerprints](../baselines/AR-01.3C/execution/before-fingerprints/rows.json), [FK identities](../baselines/AR-01.3C/execution/before-fingerprints/foreign-keys.json).

A host-only `flock` on the evidence-directory fence remained held through the attempt/proof, and a persistent [attempt marker](../baselines/AR-01.3C/execution/attempt.json) was written before the only writable invocation. The driver refuses any subsequent write invocation after that marker; it never increments run IDs or upserts an existing control receipt.

The first read-only gate preparation exposed a comparison bug: its identity dictionary omitted the baseline's absent-sidecar field. It stopped **before any attempt marker or write command**. Independent read-only diagnosis proved exact matching file values/hash, absent key, zero other claims/references. The read-only dictionary was corrected; initial failure/diagnosis are retained under `initial-identity-comparison-failure/` and `diagnostic.json`. This was no production-state mismatch and no write-canary retry; the approved write code remained unchanged.

## Canary-only identity/config and exact command

Fresh canary-only config was staged under `docs/acquisition-readiness/baselines/AR-01.3C/approved-config/`, derived from the accepted core identity with only `authorized:true` changed. Exact source/platform/target paths retained, no extra writable root. The accepted release identity remains false/unchanged. Store/core-snapshot sentinels were read-only bind overlays at preexisting targets; original underlying bytes/hashes match before/after. No external document-store or secret mount.

[Authorization/config/code hashes](../baselines/AR-01.3C/execution/authorization.json), [exact argument array](../baselines/AR-01.3C/execution/reviewed-command.json), [reviewed inline code](../baselines/AR-01.3C/execution/reviewed-inline-code.py). Exact command, executed only once:

```sh
{command}
```

`--network none`, healthcheck disabled, root read-only, all capabilities dropped, no-new-privileges, 0.5 CPU/1536 MiB, ephemeral `/tmp`; only the DB volume writable for the two control DML effects and normal SQLite journal/lock lifecycle. No source/document/download/filesystem business operation. Execution deadline20 seconds; ownership lease60 seconds; no loop or next batch.

## Actual transitions, timing and receipts

Start **{start}**; exit **{exit_time}**; duration **{timing['duration_seconds']:.6f} seconds**; exit code **{timing['exit_code']}**. [Timing](../baselines/AR-01.3C/execution/canary-timing.json). The application emitted three durable stdout phases: prepared timestamp, committed claim, committed release. Full [stdout](../baselines/AR-01.3C/execution/canary-stdout.log), [stderr](../baselines/AR-01.3C/execution/canary-stderr.log), [parsed receipt](../baselines/AR-01.3C/execution/receipt.json). Stderr is the expected production writable-target guard diagnostic, with no acquisition/worker output.

Before row: absent. First committed INSERT: exact reserved resource key, owner/token **`{claim['claim_token']}`**, status **ACTIVE**, expiry **`{claim['lease_expires_at_utc']}`**. Prepared UTC timestamp **`{prepared['now']}`**; expiry equals that timestamp plus60 seconds. Existing function generated the 32-hex fencing nonce; owner equals token. `assert_extraction_owned` verified the current unexpired generation using reads only.

Second committed UPDATE: same PK/owner/token/expiry, status **RELEASED**. Fenced UPDATE matched exactly one row. Connection `total_changes` delta is exactly **2**, returned in the released receipt. No deletion or reclaim. Final row independently observed:

```json
{json.dumps(row,indent=2,sort_keys=True)}
```

Both commits use the existing application transactions. No renewal, manual SQL INSERT/UPDATE, other key, processing/extraction persistence or global queue action occurred. Maximum control resources1; real acquisition/processing work items0.

## Independent immediate and two-minute observation

Immediately after exit, a separate pinned Python inspector mounted production **read-only**, with root/proof also read-only, network/healthcheck disabled and no secrets/docs/workers. It required fresh stopped-owner inventory, absent WAL/SHM/journal and a stable regular DB before guarded immutable query-only reads. Exact key exists once, RELEASED; owner/token and expiry match the application receipt; zero work references and zero other claim keys; schema/ledger unchanged. [Immediate observation](../baselines/AR-01.3C/execution/immediate-observation.json), [exact command](../baselines/AR-01.3C/execution/immediate-observation-command.json).

The second independent read was taken at or after **exit +120 seconds**, with no intervening write invocation or automatic renewal. Row, source size/mtime/ctime/inode/hash, sentinels/schema/ledger and no-reference conditions remain identical. [Window timing](../baselines/AR-01.3C/execution/observation-window.json), [two-minute observation](../baselines/AR-01.3C/execution/two-minute-observation.json), [exact command](../baselines/AR-01.3C/execution/two-minute-observation-command.json). The host waiting loop only waits; it admits no application work or batch.

## Full other-table and FK preservation

The post-canary main file was captured through a read-only source mount with SHA256/source-identity equality before/after, preserving no-sidecar state. Full-copy verification ran on a new labelled native disposable volume, without production DB/document mounts. [Post capture](../baselines/AR-01.3C/execution/post-capture.json), [full after manifest](../baselines/AR-01.3C/execution/after-fingerprints/rows.json), [complete comparison](../baselines/AR-01.3C/execution/comparison.json).

All **{comparison['unchanged_other_tables']} other table** row counts, all-column fingerprints and schema/ledger records match exactly. The sole changed table is `document_extraction_claims`, with exactly the one expected inserted final RELEASED row. Before claims list empty; after list exactly the verified row—no other key changed. All original149 tables, accepted versions/timestamps, queues/attempts/leases, artifacts/content/hash references and other new075 task/work/permit tables are included; none excluded. No inbox/work reference was created, no backlog moved, and no original data was processed.

Fingerprint method: stable rowid/declared-PK order, all current075 columns, length-prefixed pickle protocol4 tuples, SHA256, same pinned Python/SQLite before and after. The schema and all76 migration records are unchanged. All repeated source observations reproduce post-canary SHA256 **`{final['source_sha256']}`** and the same file metadata; final read-only gate also passes.

FK before/after **{comparison['before_fk']:,} / {comparison['after_fk']:,}**; exact `(table,rowid,parent,fkid)` multiset new difference0 and missing difference0. Historical violations remain untouched; no repair or fabricated parents. [Before tuples](../baselines/AR-01.3C/execution/before-fingerprints/foreign-keys.json), [after tuples](../baselines/AR-01.3C/execution/after-fingerprints/foreign-keys.json). Structural integrity/075 migration proof from AR-01.3B remains distinct from these actual two ownership writes.

## Preserved scope and stop

No operational service/scheduler/daemon/downloader/scope/reconciler/rating/browser/processing worker started. Every helper was a finite diagnostic/copy invocation with no network. Document store and secrets were never mounted; no source response, document byte or other production file was written. Only expected SQLite DB/journal/lock lifecycle and the two row effects occurred; physical sentinel bytes and preexisting paths are preserved. Evidence/disposable snapshots are authorized retention, not production side effects.

Accepted C28 release/config remains locked; application source unchanged. The released control receipt is deliberately retained. No automatic retry, forward recovery, claim clearing, receipt deletion or DB restore occurred. User remains recovery authority. [Final result](../baselines/AR-01.3C/execution/final.json), [final readonly/owner gate](../baselines/AR-01.3C/execution/final-readonly-gate.json), [SHA256SUMS](../baselines/AR-01.3C/execution/SHA256SUMS).

AR-01.3's cleaned image/schema/write-path safety gate can be closed with this bounded proof, after the separately evidenced A/B prerequisites. This does not establish live acquisition, a functioning publisher lane, scheduler health or Document Intelligence readiness. **No AR-02 action is selected or executed. Stop here.**

PRE_WRITE_GATE:
PASS

C28_WRITE_PATH:
PASS

CONTROL_KEY:
AR-01.3C/control-ownership/2026-10-08/v1

CLAIM_INSERT:
PASS

FENCED_RELEASE:
PASS

NORMAL_ROW_MUTATIONS:
2

FINAL_CONTROL_STATUS:
RELEASED

UNEXPECTED_OTHER_DB_CHANGES:
0

NEW_FK_VIOLATIONS:
0

NETWORK_REQUESTS:
0

OPERATIONAL_WORKERS_STARTED:
NO

HISTORICAL_BACKLOG_CHANGED:
NO

AR_01_3C_FIRST_WRITE_CANARY:
PASS

SAFE_TO_CLOSE_AR_01_3:
YES
'''
report.write_text(body)
files=sorted(p for p in OUT.rglob('*') if p.is_file() and p.name!='SHA256SUMS' and '__pycache__' not in p.parts)
config=ROOT/'docs/acquisition-readiness/baselines/AR-01.3C/approved-config';files+=sorted(config.glob('*.json'))+[report]
(OUT/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT)}\n' for p in files))
print(json.dumps({'status':'PASS','normal_row_mutations':2,'other_tables_unchanged':comparison['unchanged_other_tables'],'new_fk_violations':0,'control_status':'RELEASED','no_ar02_start':True},indent=2))
