"""Write the AR-01.3B record only after successful verification and 075 canary."""
import datetime
import hashlib
import json
import shlex
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[3]


def read(name):
    return json.loads((OUT/name).read_text())


verification_dir='resumed-verification' if (OUT/'resumed-verification/final.json').exists() else 'post-verification'
final=read('resumed-verification/final.json') if verification_dir=='resumed-verification' else read('final.json')
if final['status']!='PASS':raise RuntimeError('Do not publish PASS for incomplete/unexpected migration state')
pre=read('pre-write.json');post=read('post-metadata.json');canary=read('schema075-canary.json')
verification=read(verification_dir+'/verification.json');timing=read('migration-timing.json');size=read('size-delta.json')
assert verification['status']=='PASS' and verification['original_tables']==149
assert verification['new_fk_identities']==verification['missing_fk_identities']==0
assert canary['status']=='PASS' and canary['schema_compatibility']=='PASS' and not canary['pending'] and not canary['unknown']
assert canary['db_before']==canary['db_after']==post['db_before'] and canary['source_sha256']==post['source_sha256']
assert pre['original_markers']==post['original_markers']==canary['original_markers']
original=json.loads((ROOT/'docs/acquisition-readiness/baselines/AR-01.3/readonly-correction/application-source-before.json').read_text())
assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in original.items())
assert json.loads((ROOT/'ops/docker/release/c28.1/runtime-identity-core.json').read_text())['authorized'] is False
projection=read(verification_dir+'/original-row-fingerprints.json');assert projection==read('rows-before.json')
schema_diff=read(verification_dir+'/schema-diff.json')
assert len(schema_diff['added'])==19 and not schema_diff['removed'] and len(schema_diff['modified'])==3
new_versions=[r for r in post['ledger'] if r['version'] not in {x['version'] for x in pre['ledger']}]
summary={'status':'PASS','original_149_table_fingerprints_equal':True,'baseline_ledger_rows_preserved':69,
    'only_seven_new_ledger_rows':True,'runtime_marker_bytes_unchanged':True,'accepted_identity_unauthorized_unchanged':True,
    'application_py_files_preserved':len(original),'canary_db_identity_hash_unchanged':True,
    'operational_workers_started':False,'network_requests':0,'production_business_writes':0,
    'recovery_authority':'USER','execution_agent':'CODEX','automatic_restore':False}
(OUT/'final-comparison.json').write_text(json.dumps(summary,indent=2)+'\n')
start=datetime.datetime.fromtimestamp(timing['start_epoch'],datetime.timezone.utc).isoformat()
end=datetime.datetime.fromtimestamp(timing['end_epoch'],datetime.timezone.utc).isoformat()
command=shlex.join(read('migration-command.json'))
new_names='\n'.join(r['version'] for r in new_versions)
report=ROOT/'docs/acquisition-readiness/chunks/AR-01.3B-migration-only-production-activation.md'
body=f'''# AR-01.3B — migration-only production activation

**Status: COMPLETE / PASS for migration-only AR-01.3B.** Executed 8 October 2026. Production SQLite is now schema **075**. No operational Trading AI service was started. This is not acquisition readiness, a scheduler/worker rollout or permission for the first write-capable canary.

## Exact authorization and execution boundary

User explicitly authorized only migrations 069–075 under AR-01.3A's migration-only recovery model, repeated gates, post-verification and a zero-write 075 canary. **Recovery authority: USER; execution agent: CODEX.** No exchange/Screener/agency/issuer/document request, queue admission/claim/requeue, downloader/reconciler, scope monitor, browser/rating/processing worker, backlog work, FK repair or automatic restore was authorized or executed. On unexpected migration state the driver stops, preserves DB/sidecars/state, does not retry, repair ledger or restore; user approval is still required even if migration-only snapshot restore conditions appear satisfied.

The selected source/image is the reviewed C28.1 release: source commit `c3ddbbb9c298d07b8b46a362996251417282476a`, tree `eeb21f032e5bb4119279bc891c7342fa99eae7a1`, image `trading-ai-c281-release@sha256:4d289faa67334bc870d4f2a6a181feecee777fd427eac209b9adef68ecab0238`. Platform manifest `sha256:ba03c307890e839200f0b9e256ed0e9701c56e0ad2e541fdc57686c3be5b227c`; config `sha256:2313df692fbf96b8371d96221548bba6f5e5f9f8f616cf70fe8273504627499a`. No application-source overlay, rebuild or mutable tag. {len(original):,} preexisting Python file hashes under `scrapper/` remain unchanged, including the preexisting offline AR-02.1 patch.

An interruption occurred during preparation, before the migration driver was invoked. Inspection confirmed no attempt marker, result, driver log or running container before continuing. The production migration was invoked **once**; no migration retry occurred. [Execution log](../baselines/AR-01.3B/driver.log).

## Ten pre-write gates — all PASS

Repeated host inventories confirm all 52 Docker containers stopped, including all 15 Trading AI project containers; no known external DB writer, user cron or unknown DB-writing launch task. The document-only capacity publisher was not used as a migration prerequisite and was not started/repaired. [Immediate pre-migration gate](../baselines/AR-01.3B/immediate-pre-migration-writer-gate.json), [complete gate result](../baselines/AR-01.3B/pre-write-gate-result.json), [read-only pre-write stdout](../baselines/AR-01.3B/pre-write-stdout.log), [empty pre-write stderr](../baselines/AR-01.3B/pre-write-stderr.log).

The existing `trading-ai_scraper-data` volume has the expected project identity. Production file `/data/scraper.db` exactly matches AR-01.3A: **{pre['db_before']['bytes']:,} bytes**, device {pre['db_before']['device']}, inode {pre['db_before']['inode']}, mtime/ctime **{pre['db_before']['mtime_ns']} ns**, SHA256 **`{pre['source_sha256']}`**. WAL, SHM and rollback journal absent before mutation. Full DDL equals the complete 068 baseline; latest migration 068, 69 identities/timestamps unchanged, only the seven reviewed filenames pending, no unknown applied identity and no partial pending object/column.

AR-01.3A snapshot/restore evidence and AR-01.1 checksum indices were reproduced. The durable raw snapshot was rehashed and the separately retained disposable restore-proof volume was mounted read-only and rehashed; both still match the authorized 068 hash. [Recovery copy availability](../baselines/AR-01.3B/recovery-copy-availability.json). The pinned image/source label and existing volume were inspected. Exact migration SHA256s inside the image and current source match AR-01.1/AR-01.2 ([manifest](../baselines/AR-01.3B/migration-manifest.json), [pre-write inspection](../baselines/AR-01.3B/pre-write.json)). Host capacity was checked for post-state/failure preservation, without deleting prior evidence.

All directories that reviewed `init-db` initialization would mkdir already existed, and both sentinel bind targets already existed. Therefore no incidental directory/placeholder creation was needed. Original physical sentinel bytes/hashes were captured and match after migration and canary; no document-store byte/config was replaced. [Preparation checks](../baselines/AR-01.3B/preparation-verification.json).

## Reviewed migration-only runtime identity and exact command

The accepted release's `authorized:false` identity stays unchanged. A separately retained activation-only copy changes **only** `authorized` to true for this single container, with the exact reviewed commit/platform image/target paths and no additional writable root. [Exact identity diff/hashes](../baselines/AR-01.3B/runtime-identity-diff.json), [activation-only identity](../baselines/AR-01.3B/migration-only-runtime-identity.json). Runtime/store/core-snapshot sentinels are read-only bind overlays at the already-existing targets; underlying original files remain unchanged after container removal. No external document-store or secret mount.

Exact writable migration Docker command ([argument array](../baselines/AR-01.3B/migration-command.json)):

```sh
{command}
```

This invokes the prescribed **`python -m scrapper.cli init-db --db /data/scraper.db`**, not a worker/default application entrypoint. The existing DB volume is writable only for this migration container. Root filesystem is read-only, healthcheck disabled, network `none`, all capabilities dropped, no-new-privileges, 0.5 CPU/1536 MiB and ephemeral `/tmp`. No Compose up, startup profile, automatic restart, secret or document acquisition mount. Config: production environment, exact core identity at `/run/trading-ai/runtime-identity.json`, DB `/data/scraper.db`, store `/data`, core document identity `/data/snapshots`; no external `/documents` store.

## Migration timing, output and ledger

Start **{start}**; end **{end}**; Docker command wall duration **{timing['duration_seconds']:.6f} seconds**; exit **{timing['exit_code']}**. [Timing](../baselines/AR-01.3B/migration-timing.json), [one-attempt marker](../baselines/AR-01.3B/migration-attempt.json). The marker is written before invocation and prevents replay after interruption. No SQL file was skipped, renamed, replayed or manually repaired; no fabricated ledger row.

Full stdout:

```text
{(OUT/'migration-stdout.log').read_text().rstrip()}
```

Full stderr (expected runtime write-target identity diagnostic, **not** an error):

```text
{(OUT/'migration-stderr.log').read_text().rstrip()}
```

Before: 69 full identities, latest 068. After: **76**, latest `075_periodic_dispatch.sql`; the original 69 filename/timestamp records are unchanged. Exactly seven new identities:

```text
{new_names}
```

No pending/unknown identity. [Before ledger](../baselines/AR-01.3B/ledger-before.json), [after ledger](../baselines/AR-01.3B/post-verification/ledger.json). The distinct historical `065_exchange_source_access.sql` remains accepted by the image allowlist and was not replayed.

## Exact schema and initial-state verification

Full resulting schema equals the AR-01.1 reviewed after-schema, including all SQL constraints, names and uniqueness definitions. Exactly **three modified existing queue DDLs**, **13 named new objects** and **six implicit uniqueness indexes**; no removed object or unexpected DDL. [Complete schema](../baselines/AR-01.3B/post-verification/schema.json), [exact diff](../baselines/AR-01.3B/post-verification/schema-diff.json).

`claim_token TEXT` exists on `artifact_download_queue`, `credit_rating_download_queue`, `browser_rating_recovery_jobs`; non-NULL count on each is **0**. The eight new tables—`document_extraction_claims`, `acquisition_host_state`, `acquisition_host_permits`, `document_processing_inbox`, `document_processing_work`, `exchange_coordinator_tasks`, `collection_scope_tasks`, `periodic_dispatch_tasks`—all exist and have **0 rows**. `document_processing_saved` AFTER INSERT trigger exists. Reviewed indexes `idx_acquisition_host_active`, `exchange_coordinator_ready`, `collection_scope_single_pending`, `periodic_single_pending` and six implicit uniqueness indexes match exactly. Historical content was not backfilled; no queue work was admitted or claimed.

## Original-data, FK and SQLite verification

A full byte-identical post-migration capture is retained at `{final['post_capture']}`. Source was mounted read-only during capture; all source/copy hashes and file identity stayed stable. No WAL/SHM/journal remained after normal CLI connection closure. No manual checkpoint/replay/recovery was used. The capture was seeded into new isolated native disposable volume `{final['post_verification_volume']}` for the full scans, avoiding the earlier macOS bind-mount I/O bottleneck. Validators do not mount production DB or document storage. [Capture](../baselines/AR-01.3B/post-capture.json), [full verification](../baselines/AR-01.3B/post-verification/verification.json), [raw stage stdout](../baselines/AR-01.3B/post-verification/stdout.log), [empty stderr](../baselines/AR-01.3B/post-verification/stderr.log).

All **149 original tables / {verification['original_rows']:,} original rows** match the AR-01.3A baseline-column count/fingerprint manifest exactly. Method: every original column, stable rowid/declared-PK order, length-prefixed pickle protocol 4 tuples and streamed SHA256. Added nullable claim columns are checked separately. For `schema_migrations`, the original 69 records are independently fingerprinted and compared; the seven authorized new records are separately verified rather than misclassified as unexplained data drift. No table/family is skipped. [Baseline](../baselines/AR-01.3B/rows-before.json), [all original projections](../baselines/AR-01.3B/post-verification/original-row-fingerprints.json), [final comparison](../baselines/AR-01.3B/final-comparison.json).

Full FK check: before **10,771**, after **10,771**. New identity multiset difference **0**; missing difference **0**, hence no replaced identity. The historical 5,395 `exchange_screener_enqueues` and 5,376 `screener_company_queue_events` missing-parent cases remain untouched. No FK repair or parent/attempt fabrication. [Before tuples](../baselines/AR-01.3B/foreign-keys-before.json), [after tuples](../baselines/AR-01.3B/post-verification/foreign-keys.json), [exact multiset comparison](../baselines/AR-01.3B/post-verification/fk-comparison.json).

SQLite `quick_check`: **ok**; `integrity_check`: **ok**. [Raw results](../baselines/AR-01.3B/post-verification/sqlite-checks.json). Both source/capture and native-copy hashes match; validation rehash confirms the copy did not change. Historical FK violations remain distinct from structural integrity success.

DB main size: **{size['before']:,} → {size['after']:,} bytes**, delta **+{size['delta_bytes']:,} bytes (80 KiB)**, exactly the rehearsal growth. This is new empty schema/index pages plus schema/ledger metadata, not a corpus rewrite; no large-table scan was needed during migration. Full validation scans occur afterward on the read-only disposable copy. Growth is below the reviewed 1 MiB alert, with no unexplained DDL. [Size record](../baselines/AR-01.3B/size-delta.json). Post-migration full SHA256: **`{post['source_sha256']}`**.

## Zero-write production schema-075 canary

After full verification passed, the isolated inspector ran against real production through the reviewed safe method: fresh host ownership gate, existing regular DB, absent WAL/SHM/journal, read-only data/root/proof mounts, guarded `mode=ro&immutable=1`, query-only, network/healthcheck disabled, no secrets/docs/workers, 0.5 CPU/1536 MiB and 30-second deadline. It imports only pinned image `/app` code and calls `check_schema_compatibility` without connect/init-db/apply_migrations. Application compatibility **PASS**; 76 ledger identities, latest075, no pending/unknown migration; complete reviewed DDL, empty new tables and NULL historical claim tokens confirmed.

Canary output also confirms identical before/after source file identity, size/mtime/ctime and full SHA256, matching the post-migration captured source. No SQL or filesystem business write, queue work, role heartbeat owner or source request. [Exact canary command](../baselines/AR-01.3B/schema075-canary-command.json), [complete stdout](../baselines/AR-01.3B/schema075-canary-stdout.log), [empty stderr](../baselines/AR-01.3B/schema075-canary-stderr.log), [structured output](../baselines/AR-01.3B/schema075-canary.json), [exit](../baselines/AR-01.3B/schema075-canary-exit.json), [final writer inventory](../baselines/AR-01.3B/schema075-canary-writer-after.json).

## Preservation, recovery boundary and stop

Only the seven authorized schema/ledger changes occurred in production. Full original-row equality, empty newly introduced operational tables, NULL historical claims and preserved old ledgers/physical sentinels prove no admitted business/operational work. All operational containers/roles remain stopped; diagnostic/migration containers exit and are removed. Every container used `--network none`; no request-producing code/worker was invoked. The original 068 raw snapshot and restore-proof copies remain retained and unchanged; no production restore occurred.

The accepted release files/false identity and application source are preserved. Activation-only config remains evidence outside that package and was bound only into the single migration process; it did not enable a service. Post-state capture, disposable verification volume, commands, script hashes and raw outputs remain available. [Final status](../baselines/AR-01.3B/final.json), [SHA256SUMS](../baselines/AR-01.3B/SHA256SUMS).

This PASS permits **planning** the first separately selected bounded write-capable C28 canary; it is not authorization to start one. Generalized post-business-write recovery, lane-specific source/guard/config readiness and that canary's exact write/request/cohort bounds must be reviewed before such activation. Acquisition is not declared GREEN by schema migration. No Trading AI service was started after migration; stop here.

PRE_WRITE_GATE:
PASS

MIGRATIONS_069_075:
PASS

PRODUCTION_SCHEMA:
075

ORIGINAL_DATA_PRESERVATION:
PASS

NEW_FK_VIOLATIONS:
0

SQLITE_INTEGRITY:
PASS

C28_SCHEMA_COMPATIBILITY:
PASS

OPERATIONAL_WORKERS_STARTED:
NO

NETWORK_REQUESTS:
0

PRODUCTION_BUSINESS_WRITES:
0

AR_01_3B_MIGRATION_ONLY:
PASS

SAFE_TO_PLAN_FIRST_WRITE_CAPABLE_C28_CANARY:
YES
'''
if verification_dir=='resumed-verification':
    body=body.replace('../baselines/AR-01.3B/post-verification/','../baselines/AR-01.3B/resumed-verification/')
    body=body.replace('[Final status](../baselines/AR-01.3B/final.json)',
                      '[Completed final status](../baselines/AR-01.3B/resumed-verification/final.json)')
    note='''## External Docker interruption and read-only continuation

Docker disappeared during the first post-verification run at 04:18:12Z, after both structural checks returned `ok` and before the FK result. The CLI reported `error waiting for container: unexpected EOF`; the driver stopped and did not retry migration, repair or restore. Its subsequent additional failure-capture attempt could not reach the missing daemon socket. The complete post-migration DB capture already existed, and its SHA256 was independently reproduced from the retained host file. [Original interrupted status](../baselines/AR-01.3B/final.json), [original stderr](../baselines/AR-01.3B/post-verification/stderr.log), [preservation record](../baselines/AR-01.3B/interruption-preservation.json).

The user then confirmed Docker was available. No agent-driven Docker restart occurred. A fresh read-only gate reconfirmed the exact preserved 075 source hash/identity/schema/ledger and stopped writers. Only the remaining read-only FK/fingerprint verification and the zero-write canary resumed, on the same rehashed immutable copy and pinned image. The earlier completed `quick_check`/`integrity_check` were reused only after verifying their exact covered input hash and successful recorded results. This was not retrying partial DDL or an init-db attempt. [Resume gate](../baselines/AR-01.3B/resumed-production-gate.json), [exact resume command](../baselines/AR-01.3B/resumed-verification-command.json), [resume output](../baselines/AR-01.3B/resumed-verification/stdout.log), [completed status](../baselines/AR-01.3B/resumed-verification/final.json). Original interruption logs/status remain unchanged.

'''
    body=body.replace('## Preservation, recovery boundary and stop\n',note+'## Preservation, recovery boundary and stop\n')
report.write_text(body)
files=sorted(p for p in OUT.rglob('*') if p.is_file() and p.name!='SHA256SUMS' and '__pycache__' not in p.parts)+[report]
(OUT/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT)}\n' for p in files))
print(json.dumps(summary,indent=2))
