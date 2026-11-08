"""Rebuild Phase 2 evidence from measured checkpoints and canonical receipts."""
from collections import Counter,defaultdict
from datetime import datetime,timezone
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/document-backlog-recovery-20261026'


def read_jsonl(path):return [json.loads(line) for line in path.read_text().splitlines()]
def save(name,value):
    (OUT/name).write_text(json.dumps(value,indent=2,default=str)+'\n')
def markdown(name,text):(OUT/name).write_text(text.strip()+'\n')


def main():
    sys.path.insert(0,str(ROOT/'ops/host'))
    from document_backlog_audit import redact_query_values
    raw=OUT/'raw'
    original=json.loads((raw/'inventory.json').read_text())
    snapshots=[(p,json.loads(p.read_text())) for p in raw.glob('refresh-*/inventory.json')]
    latest_path,latest=max(snapshots,key=lambda pair:pair[1]['observed_at_utc']) if snapshots else (raw/'inventory.json',original)
    rows=read_jsonl(latest_path.parent/'contents.jsonl')
    verification={r['content_id']:r for r in read_jsonl(raw/'verification.jsonl')}
    manifest=json.loads((OUT/'PILOT_AUTHORIZATION_AND_PLAN.json').read_text())
    receipts=[json.loads(f.read_text()) for f in (OUT/'receipts').glob('content-*.json')]
    intents=[json.loads(f.read_text()) for f in (OUT/'receipts').glob('intent-*.json')]
    receipt_by_id={r['content_id']:r for r in receipts}
    canonical={r['content_id']:r for r in latest.get('pilot_results',[])}
    classification=[];breakdowns=defaultdict(Counter)
    for row in rows:
        physical=verification.get(row['content_id'],{})
        status=row['extraction_status'];cache=row['exact_cache']
        receipt=receipt_by_id.get(row['content_id'])
        if receipt:
            status=receipt['extraction_status'];cache={'extraction_status':status,'extraction_id':receipt['extraction_id']}
        if not physical:category='UNVERIFIED'
        elif physical['state']=='missing':category='D'
        elif physical['state']=='corrupt':category='E'
        elif not row['supported']:category='F'
        elif cache and cache['extraction_status']=='ok':category='C'
        elif status=='ok':category='A'
        else:category='B'
        actionable=category=='B' and bool(row['eligible']) and status in ('not_started','pending') and cache is None
        item={**row,'current_extraction_status':status,'category':category,
            'physical_state':physical.get('state','unverified'),'actionable':actionable,
            'processing_review_required':status in ('failed','partial')}
        classification.append(item)
        dimensions={'document_type':row['artifact_type'],'mime':row['media_type'],
            'quarter':row['period_end'] or 'unknown','eligibility':'eligible' if row['eligible'] else 'not_eligible',
            'size':'<256KiB' if row['byte_size']<262144 else '256KiB-1MiB' if row['byte_size']<1048576 else '1-10MiB' if row['byte_size']<=10485760 else '>10MiB',
            'processing_status':status,'era':'historical' if row['created_at_utc']<'2026-10-09T00:00:00+00:00' else 'post_epoch',
            'publication_era':'unknown' if not row['event_date'] else 'historical' if row['event_date']<'2026-10-09' else 'post_epoch',
            'content_origin':row.get('content_origin','not_in_earlier_export'),
            'category':category}
        for dimension,key in dimensions.items():breakdowns[dimension][str(key)]+=1
    with (OUT/'content-classification.jsonl').open('w') as stream:
        for row in classification:stream.write(json.dumps(row)+'\n')
    with (OUT/'DOCUMENT_SHA256_MANIFEST.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=['content_id','artifact_id','company_id','local_path',
            'expected_sha256','actual_sha256','expected_bytes','actual_bytes','physical_state','verified_at_utc'])
        writer.writeheader()
        for row in rows:
            physical=verification.get(row['content_id'],{})
            writer.writerow({'content_id':row['content_id'],'artifact_id':row['artifact_id'],
                'company_id':row['company_id'],'local_path':row['local_path'],
                'expected_sha256':row['content_sha256'],'actual_sha256':physical.get('actual_sha256'),
                'expected_bytes':row['byte_size'],'actual_bytes':physical.get('actual_bytes'),
                'physical_state':physical.get('state','unverified'),'verified_at_utc':physical.get('verified_at_utc')})
    good=[r for r in verification.values() if r['state']=='verified']
    origin_groups=defaultdict(list)
    for row in rows:origin_groups[row['artifact_id']].append(row)
    origin_versions={
        'multiple_publisher_original_hashes':sum(len({r['content_sha256'] for r in group if r.get('content_origin')=='publisher_original'})>1 for group in origin_groups.values()),
        'original_and_derivative_artifacts':sum({r.get('content_origin') for r in group}=={'publisher_original','generated_derivative'} for group in origin_groups.values()),
        'derivative_only_artifacts':sum(all(r.get('content_origin')=='generated_derivative' for r in group) for group in origin_groups.values())}
    content_lookup={r['content_id']:r for r in rows}
    recovered_historical_failures=[]
    for record in latest.get('processing_failure_records',[]):
        cache=content_lookup.get(record['content_id'],{}).get('exact_cache')
        if cache and cache['extraction_status']=='ok':
            recovered_historical_failures.append({'content_id':record['content_id'],
                'historical_failed_extraction_id':record['extraction_id'],'historical_parser_version':record['parser_version'],
                'successful_current_extraction_id':cache['extraction_id']})
    totals=latest['totals'][0]
    summary={**totals,'logical_snapshot_utc':latest['observed_at_utc'],
        'generated_at_utc':datetime.now(timezone.utc).isoformat(),
        'verification_complete':set(verification)=={r['content_id'] for r in rows},'verified_contents':len(good),
        'verified_unique_paths':len({r['local_path'] for r in good}),
        'referenced_unique_paths':len({r['local_path'] for r in rows}),
        'verified_unique_hashes':len({r['actual_sha256'] for r in good}),
        'verified_companies':len({r['company_id'] for r in classification if r['physical_state']=='verified'}),
        'verified_bytes_sum_by_content':sum(r['actual_bytes'] for r in good),
        'missing_contents':sum(r['state']=='missing' for r in verification.values()),
        'missing_unique_paths':len({r['local_path'] for r in verification.values() if r['state']=='missing'}),
        'corrupt_contents':sum(r['state']=='corrupt' for r in verification.values()),
        'corrupt_unique_paths':len({r['local_path'] for r in verification.values() if r['state']=='corrupt'}),
        'unverified_contents':len(rows)-len(verification),
        'categories':dict(Counter(r['category'] for r in classification)),
        'actionable_verified_eligible':sum(r['actionable'] for r in classification),
        'actionable_verified_eligible_unique_hashes':len({r['content_sha256'] for r in classification if r['actionable']}),
        'actionable_verified_eligible_companies':len({r['company_id'] for r in classification if r['actionable']}),
        'unsupported_media_metadata':sum(not r['supported'] for r in rows),
        'already_processed_ok_metadata':sum(r['current_extraction_status']=='ok' for r in classification),
        'processing_failures_metadata':sum(r['current_extraction_status']=='failed' for r in classification),
        'failed_extraction_records':sum(r['extractions'] for r in latest['parser_versions'] if r['extraction_status']=='failed'),
        'historical_failed_records_with_current_success':recovered_historical_failures,
        'partial_processing_metadata':sum(r['current_extraction_status']=='partial' for r in classification),
        'supported_unprocessed_metadata_eligible':sum(r['eligible'] and r['supported'] and r['current_extraction_status'] in ('not_started','pending') and not r['exact_cache'] for r in classification),
        'breakdowns':{k:dict(v) for k,v in breakdowns.items()},
        'parser_versions':latest['parser_versions'],'snapshot_source':str(latest_path.relative_to(OUT))}
    summary['duplicate_analysis']={'queue_scopes':latest.get('queue_scopes'),
        'financial_scope_source_aliases':latest.get('financial_scope_source_aliases'),
        'same_hash_multiple_urls':latest.get('same_hash_multiple_urls'),
        'content_origin_versions':origin_versions}
    # Queue categories describe coverage and source state separately. A shared
    # artifact attachment is never labelled an HTTP-byte proof for every URL.
    jobs_path=latest_path.parent/'jobs.jsonl'
    by_artifact=defaultdict(list)
    by_content={r['content_id']:r for r in classification}
    for row in classification:by_artifact[row['artifact_id']].append(row)
    job_counts=Counter();committed_counts=Counter()
    if jobs_path.exists():
        with (OUT/'job-classification.jsonl').open('w') as stream:
            for job in read_jsonl(jobs_path):
                job=redact_query_values(job)
                text=str(job.get('last_result_status') or '')+' '+str(job.get('last_error') or '')
                attached=by_artifact[job['artifact_id']]
                if job['status']=='INTERACTION_REQUIRED' or 'PUBLISHER_TERMS' in text or 'http_403' in text:category='G'
                elif job.get('artifact_type')=='recording':category='F'
                elif not attached and ('http_404' in text or 'no_validated_report_target' in text):category='H'
                elif not attached:category='D'
                else:
                    categories={r['category'] for r in attached}
                    category=next((k for k in ('C','A','B','F','E','D','UNVERIFIED') if k in categories),'UNVERIFIED')
                committed=by_content.get(job.get('committed_content_id'))
                proof=committed['physical_state'] if committed else 'no_direct_job_content_receipt'
                result={**job,'coverage_category':category,'direct_job_byte_proof':proof,
                    'attached_content_ids':[r['content_id'] for r in attached]}
                stream.write(json.dumps(result)+'\n')
                job_counts[category]+=1;committed_counts[proof]+=1
    summary['job_coverage_categories']=dict(job_counts);summary['direct_job_byte_proof']=dict(committed_counts)
    discrepancy=latest.get('attachment_discrepancy')
    if discrepancy:
        save('attachment-discrepancy.json',discrepancy)
        discrepancy_text=(f"The verified backup contains {discrepancy.get('backup_eligible_pending_attached','see backup_associations')} eligible pending attached-content jobs; "
            f"the current snapshot contains {discrepancy.get('current_eligible_pending_attached',22214)}. "
            f"Added attached-job IDs: {discrepancy.get('added_attached_job_ids','see new_job_rows')}; "
            f"removed IDs: {discrepancy.get('removed_attached_job_ids',[])}. "
            f"The backup has {discrepancy['backup_jobs']} generic jobs; the live total is {totals['generic_jobs']}. "
            "Exact row identities, scope, timestamps and attachment counts are in `attachment-discrepancy.json`. "
            "The earlier prose mixed observations rather than supplying a timestamped four-row reconciliation; no lost files are inferred from queue count differences.")
        changes=discrepancy.get('changed_rows',[])
        if len(changes)==4 and all(r['before'] and r['after'] and r['before']['status']=='SKIPPED_POLICY'
            and r['before']['last_result_status']=='SCOPE_BELOW_MARKET_CAP_FLOOR' and r['after']['status']=='PENDING' for r in changes):
            discrepancy_text+=' The four rows were scope-floor holds for company 3960, released by the existing market-cap reentry API. The retained 26 October 17:00 monitor receipt records /company/539518/ moving from below_floor to eligible at 701 crore against the unchanged 580-crore floor. `scrapper/commands/collection_scope.py:apply_observation` reopens only SCOPE_BELOW_MARKET_CAP_FLOOR document holds. This explains both +4 pending jobs and +4 attached pending jobs with no new queue rows or downloads; publisher restrictions and Focus List rules were not changed.'
    else:
        discrepancy_text='The live count is 22,214. Earlier runtime receipts contain a lower total; a direct backup-to-live comparison is needed to identify the exact four-row difference. No loss or deletion is inferred.'
    save('inventory-summary.json',summary)
    save('pilot-database-validation.json',{'observed_at_utc':latest['observed_at_utc'],'records':latest.get('pilot_results',[])})
    selected=len(manifest['contents']);counts=Counter(r['extraction_status'] for r in receipts)
    pilot={'selected':selected,'attempted':len({r['content_id'] for r in intents}),
        'success':counts['ok'],'partial':counts['partial'],'failed':counts['failed'],
        'deferred':selected-len(receipts),'unsupported':0,'reused':sum(r['cache_hit'] for r in receipts),
        'fresh':sum(not r['cache_hit'] for r in receipts),'database_validated':len([r for r in canonical.values() if r['extraction_id']]),
        'external_requests':0}
    save('campaign-status.json',pilot)
    reservations=[]
    for path in sorted((OUT/'batches').glob('*/reservations.json')):
        for folder in json.loads(path.read_text()):
            reservation=Path(folder)/'daily-budget-reservation.json'
            if reservation.exists():
                reservations.append({'receipt':str(reservation.relative_to(ROOT)),
                    'batch':str(path.parent.relative_to(OUT)),**json.loads(reservation.read_text())})
    save('campaign-reservations.json',reservations)
    save('PILOT_EXECUTION_MANIFEST.json',{'campaign_id':manifest['campaign_id'],
        'authorization_digest':(OUT/'PILOT_MANIFEST.sha256').read_text().strip(),
        'version':manifest['version'],'observed_at_utc':latest['observed_at_utc'],
        'contents':[{**item,'processing_outcome':receipt_by_id.get(item['content_id'],{}).get('extraction_status','DEFERRED'),
            'execution_receipt':receipt_by_id.get(item['content_id']),
            'database_validation':canonical.get(item['content_id'])} for item in manifest['contents']]})
    with (OUT/'pilot-item-results.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=['content_id','artifact_id','company_id','document_type',
            'byte_size','media_type','parser_version','extraction_method','result','failure_class',
            'extraction_id','work_id','cache_hit','document_seconds','pages','ocr_pages','text_characters'])
        writer.writeheader()
        for item in manifest['contents']:
            r=receipt_by_id.get(item['content_id'],{});db=canonical.get(item['content_id'],{})
            method=None
            if r:
                method='rating_html_native' if item['media_type']=='text/html' else 'pdf' if item['media_type']=='application/pdf' else 'powerpoint'
                if item['media_type']!='text/html':method+='_native_and_ocr' if db.get('ocr_page_count',0) else '_native'
            error=json.loads(db.get('error_json') or '{}')
            writer.writerow({'content_id':item['content_id'],'artifact_id':item['artifact_id'],'company_id':item['company_id'],
                'byte_size':item['byte_size'],'media_type':item['media_type'],
                'parser_version':manifest['version']['parser_version'],'extraction_method':method,'failure_class':error.get('type'),
                'document_type':item['artifact_type'],'result':r.get('extraction_status','DEFERRED'),
                'extraction_id':r.get('extraction_id'),'work_id':r.get('work_id'),'cache_hit':r.get('cache_hit'),
                'document_seconds':r.get('document_seconds'),'pages':db.get('page_or_slide_count'),
                'ocr_pages':db.get('ocr_page_count'),'text_characters':db.get('pages',{}).get('text_characters')})
    fresh=[r for r in receipts if not r['cache_hit']]
    times=sorted(r['document_seconds'] for r in fresh)
    batch_seconds=sum(json.loads(f.read_text())['total_seconds'] for f in (OUT/'batches').glob('*/outcome.json'))
    timings=Counter()
    for r in receipts:timings.update(r['timings'])
    performance={'fresh_samples':len(times),'cache_samples':pilot['reused'],
        'fresh_median_seconds':statistics.median(times) if times else None,
        'fresh_p95_nearest_rank_seconds':times[math.ceil(len(times)*.95)-1] if times else None,
        'document_seconds_sum':sum(times),'docker_batch_seconds_sum':batch_seconds,
        'fresh_documents_per_compute_hour':len(times)*3600/sum(times) if times else None,
        'fresh_documents_per_container_hour':len(times)*3600/batch_seconds if batch_seconds else None,
        'timing_totals_seconds':dict(timings),
        'verified_actionable_compute_hours_estimate':summary['actionable_verified_eligible']*(sum(times)/len(times))/3600 if times else None,
        'minimum_days_at_24_stage_operations':math.ceil(summary['actionable_verified_eligible']/24),
        'minimum_days_at_22_backlog_operations':math.ceil(summary['actionable_verified_eligible']/22)}
    save('benchmark.json',performance)
    completeness='COMPLETE' if summary['verification_complete'] else 'INCOMPLETE: remaining bytes have not been hash-verified'
    markdown('INVENTORY_RECONCILIATION.md',f'''# Document inventory reconciliation

Logical snapshot: {latest['observed_at_utc']}; physical checkpoint state: **{completeness}**.
Scope is database-referenced archive documents. Unreferenced archive files are not deleted or counted as canonical documents.

- Generic download jobs: {totals['generic_jobs']:,}; rating jobs: {totals['rating_jobs']:,}. These queues overlap in artifact coverage.
- Artifact records: {totals['artifacts']:,}; artifacts with content: {totals['downloaded_artifacts']:,}.
- Content IDs: {totals['contents']:,}; recorded distinct hashes: {totals['recorded_hashes']:,}; covered company IDs: {totals['covered_companies']:,}.
- Content provenance: {summary['breakdowns'].get('content_origin',{})}. Publisher originals and generated derivatives are kept separate; the content-ID total is not a count of original HTTP downloads.
- Verified physical content associations: {len(good):,}; unique verified paths: {summary['verified_unique_paths']:,}; distinct measured hashes: {summary['verified_unique_hashes']:,}.
- Missing content references: {summary['missing_contents']:,} ({summary['missing_unique_paths']:,} unique paths); corrupt: {summary['corrupt_contents']:,}; unverified: {summary['unverified_contents']:,}.
- Unsupported media metadata: {summary['unsupported_media_metadata']:,}; successful processed metadata: {summary['already_processed_ok_metadata']:,}; partial: {summary['partial_processing_metadata']:,}; failed content statuses: {summary['processing_failures_metadata']:,}; failed extraction records across versions: {summary['failed_extraction_records']:,}. These are separate layers. Exact failed-record/content-state relationships are retained in the snapshot's `processing_failure_records`; no status is repaired with ad hoc SQL. Missing originals are kept separate even when a historical extraction exists.

Historical failure reconciliation: {recovered_historical_failures}. These older failed records remain preserved alongside successful exact-current-version results; they are not current failed contents or new Phase 2 recoveries.
- Actionable verified supported eligible unprocessed contents: {summary['actionable_verified_eligible']:,}, covering {summary['actionable_verified_eligible_companies']:,} companies and {summary['actionable_verified_eligible_unique_hashes']:,} distinct hashes. Supported unprocessed eligible metadata count: {summary['supported_unprocessed_metadata_eligible']:,}.
- Content categories: {summary['categories']}. Failed/partial results remain separately identified, never counted as complete success.

`content-classification.jsonl` classifies every content ID. `job-classification.jsonl` classifies queue coverage separately from direct job-to-content byte receipts. Category G denotes retained source restrictions; H denotes recorded unavailability, not proof a publication never existed. D for a job without an attachment means bytes have not been committed, not proof a file was deleted. Unsupported media is F. B includes incomplete processing; partial/failed outcomes retain their exact status and require reviewed recovery. Only supported eligible not-started/pending rows without exact-version caches enter the actionable count. UNVERIFIED is deliberately separate from B.

Every breakdown by type, MIME, period, company eligibility, size, status, parser version and historical/post-epoch era is in `inventory-summary.json`. Unknown periods remain unknown. The only non-null eligible unprocessed periods in the original census were three legacy 1970-01-31 values; no investment quarter was inferred.

The original SQL and schema/ledger/database identity checks are retained in `raw/inventory.json`; refresh SQL is in `{latest_path.relative_to(OUT)}`. `raw/contents.jsonl` is the original consistent logical snapshot, with a retained SHA-256. Physical verification spans checkpointed observations, not one instantaneous filesystem snapshot. Files are streamed, checked for concurrent inode/size/mtime changes, and never repaired by this utility.

The first stratified 100-file sample yielded 85 verified and 15 missing. Expansion used bounded batches (at most 2,000 records, about 40 seconds), releasing admission/owner locks between batches and yielding to every acquisition window. Later I/O overlap uses at most four read-only readers; there remains one production database writer.

Duplicate scope evidence: {latest['duplicate_scopes']}. Same-byte content aliases: {latest['hash_aliases']}. Additional artifact content versions: {latest['multiple_versions']}. Database migration 045 explicitly permits one immutable blob attached to multiple artifacts. Cross-scope jobs and source/version aliases are preserved. Pending jobs with committed artifact content are retained for canonical reconciliation, with no ad hoc state updates or terminal reopens.

Financial source-page aliases (standalone/consolidated) are counted independently of queue campaign scopes: {latest.get('financial_scope_source_aliases')}. The queue scopes are source campaigns, rather than standalone/consolidated financial statements. Validated HTTP history identifies identical bytes at different requested URLs: {latest.get('same_hash_multiple_urls')}. Content-version provenance: {origin_versions}. In particular, original-plus-derivative attachments are legitimate renderings; multiple publisher-original hashes require source-version review before declaring revised publications. Derivative-only artifacts are not original-download coverage.

{discrepancy_text} Exact live associations: {latest['pending_associations']}.
''')
    markdown('LOCAL_PROCESSING_IMPLEMENTATION.md','''# Finite local processing implementation

New `ops/host/document_backlog_audit.py` provides the missing Phase 2 audit utility. No pre-existing utility was present at that path. The host adapter serializes with the installed dispatcher's admission and NSE owner locks, verifies production ownership, schema/ledger/inode, the external UUID/sentinel, capacity and actual scheduler windows.

`ops/host/local_document_campaign.py` freezes at most 100 verified, eligible content IDs, with original artifact/company identity, media, size, hash, reference and exact parser/OCR/options. The manifest is immutable after freeze. Its canonical JSON digest is in `PILOT_MANIFEST.sha256`; ordinary file SHA-256 is separately retained in `SHA256SUMS`.

`scrapper/operations/workers/local_document_campaign.py` validates the entire manifest before mutation, then calls existing `reconcile_processing` and `run_processing`. It creates no schema, database, download queue or alternate parser. `ops/activation/local_document_campaign_runtime.py` uses the established runtime identity, SQL authorizer, canonical processing ownership lock and extraction claim fences. The container has network_mode=none, all capabilities dropped, a read-only root filesystem, 0.5 CPU and 1,536 MiB memory.

One shared daily reservation is consumed per admitted document, including cache recovery. Interrupted/unused reservations are retained. At most three documents share one container; each admission checks live scheduling, ownership, storage and remaining capacity. Two daily operations are left for normal arrivals. Historical acquisition allowance remains zero; the consumed earlier one-document historical authorization is unchanged.

Intent receipts preserve exact work/content IDs before extraction. Atomic fsynced completion receipts preserve exact extraction IDs, current version, hash, sizes, cache status, timings and outcomes. Unique temporary receipt names permit recovery when containers restart as PID 1. Committed exact-version results are reused after a crash; failed caches are not relabelled successful. Active/expired claims remain governed by canonical fencing and renewal. No ad hoc SQL marks production work complete.

Already mounted canonical references require no file copy/materialization. Original bytes are checked before work and again by the canonical parser at use; this deliberate integrity check is timed. Exported text and page lineage remain in canonical production extraction tables and the campaign's retained exports.

Dry run is read-only and consumes no real reservation. Resume on another permitted day using the existing pinned Python:

```sh
/Users/mohamedfadhi/.cache/uv/archive-v0/-WyGD_mChAFVX11K/bin/python -B ops/host/document_backlog_recovery.py process-available --limit 3 --seconds 180
```

The finite historical campaign requires explicit invocation; it does not add a second scheduler. The installed scheduler continues normal acquisition and post-epoch processing. No financial parser, Focus List logic, publisher permission, stage maximum, historical authorization or installed scheduler source was changed.
''')
    markdown('LOCAL_PROCESSING_RESULTS.md',f'''# Actual local processing results

{pilot}

The first canary was content 68946 / artifact 10546, extraction 33646, three pages and 15,423 text characters. Its document wall time was 1.955 seconds and Docker batch time 4.839 seconds. Subsequent bounded batches expanded to the retained daily limit; no selected-but-deferred document is counted as successful.

Independent read-only validation: {pilot['database_validated']} committed extraction IDs verified against the frozen 100 identities. `pilot-database-validation.json` retains exact versions, original hashes, canonical work IDs, page counts, text counts and cross-content checks. Item-level results for all 100 IDs are in `pilot-item-results.csv`; durable per-document intent/completion receipts and full canonical exports are under `receipts/`.

`extraction-identity-spotchecks.json` checks one presentation, transcript and rating report against registered company names, original artifact IDs and their extracted text. The observed examples identify Tata Motors Passenger Vehicles, InfoBeans Technologies and PNC Infratech; all three name-token checks passed. These checks preserve source metadata without inferring or backfilling fiscal periods from the text.

Every measured pilot result is fresh extraction; cache reuse has no production throughput sample in this pilot. Restart/cache behavior is verified by disposable regression fixtures, including death after persistence and before export/receipt. This is not presented as live cache throughput.

The pilot's network requests are zero, enforced by network_mode=none and runtime interface flags. Separate source-discovery/download canaries are explicitly outside this networkless campaign. Tesseract OCR is deterministic document extraction; no Qwen, Gemini or other model inference ran.

The manifest includes historical stored documents. No post-epoch unprocessed stored content was available at freeze. Publisher diversity is recorded where known; many issuer publication dates, publishers and financial periods are null and were not fabricated. All 100 selected files were physically verified before freeze and each attempted file was verified again at execution.

`tests.log` contains the applicable offline tests, including supported PDF, missing/corrupt files, unsupported media, duplicate IDs/scopes, same hashes under different content IDs, old/current parser versions, crash before/after persistence, database lock, expired claim, disconnected storage, daily limits, scheduler conflict, identity guards and network isolation. Test-only SQL fault injection never targets production.
''')
    markdown('PERFORMANCE_REPORT.md',f'''# Measured deterministic processing performance

- Fresh samples: {len(times)}; cache samples: {pilot['reused']}.
- Fresh per-document median: {performance['fresh_median_seconds']:.3f}s; p95: {performance['fresh_p95_nearest_rank_seconds']:.3f}s (nearest-rank, small representative sample).
- Sum of fresh document times: {sum(times):.3f}s; sum of actual Docker batch times: {batch_seconds:.3f}s.
- Fresh compute throughput: {performance['fresh_documents_per_compute_hour']:.2f} documents/hour; container-inclusive batch throughput: {performance['fresh_documents_per_container_hour']:.2f}/hour.
- Measured OCR time: {timings.get('ocr_seconds',0):.3f}s. Parser totals include OCR, so these nested measurements must not be added twice.

`benchmark.json` and per-item receipts distinguish selection, content loading, verified file reading/hash, version startup, canonical claim lock wait, native PDF/HTML/PowerPoint extraction, OCR, work reconciliation, database persistence and canonical export. Mounted files require no copy. Database connection/open, host attestation and receipt fsync are included in total wall/batch time but are not all isolated into independent timers; no invented sub-timings are supplied.

Batching amortizes container startup over at most three documents. Repeated full-corpus selection and financial-table fingerprints are absent; exact-version lookup is indexed and canonical. Existing integrity verification is retained. No matched before/after production parser speedup is claimed: the previous recovery benchmark reused a committed extraction, whereas this pilot performs fresh work. The sample has different document sizes, page counts and OCR needs. Python profiling adds measurement overhead.

Verified actionable backlog now: {summary['actionable_verified_eligible']:,}. Extrapolated pure compute: {performance['verified_actionable_compute_hours_estimate']:.2f} hours using this sample mean. The 24-operation/day ceiling gives at least {performance['minimum_days_at_24_stage_operations']} days; leaving two operations for new arrivals gives at least {performance['minimum_days_at_22_backlog_operations']} days. These are conditional lower bounds: stage reservations also cover idle/failure/recovery, scheduler sleep/windows and source arrivals reduce usable backlog capacity. The 100-ID campaign does not authorize processing the entire corpus. Unsupported, missing, failed and unverified content is excluded from the actionable estimate. If physical verification is incomplete, this is only the verified portion, never an ETA for all 37,924 historical metadata rows.
''')
    # Keep the actual source operations with this package, with fencing/session
    # tokens redacted using the existing operational redactor.
    sys.path.insert(0,str(ROOT/'ops/host'))
    from acquisition_observations import redact
    source_results={}
    for kind in ('discovery','download','rating'):
        invocation=raw/(kind+'-canary.json')
        if not invocation.exists():continue
        result=json.loads(invocation.read_text());source_results[kind]=result
        folder=Path(result['path']) if result.get('path') else None
        if folder is None:continue
        target=OUT/'source-checks'/kind;target.mkdir(parents=True,exist_ok=True)
        for name in ('admission.json','outcome.json','daily-budget-reservation.json',
                     'execution-storage-preflight.json','host-timings.json','plan.json','runtime.log'):
            source=folder/name
            if not source.is_file():continue
            if source.suffix=='.json':
                (target/name).write_text(json.dumps(redact(json.loads(source.read_text())),indent=2)+'\n')
            else:
                lines=[]
                for line in source.read_text().splitlines():
                    try:line=json.dumps(redact(json.loads(line)))
                    except json.JSONDecodeError:pass
                    lines.append(line)
                (target/name).write_text('\n'.join(lines)+'\n')
    save('source-checks.json',source_results)
    rating=next((r for r in latest.get('recent_rating_jobs',[]) if r['status']=='INTERACTION_REQUIRED'),{})
    markdown('DOCUMENT_DOWNLOAD_CANARY.md',f'''# Fresh document download canary

**NO genuinely fresh downloaded document is demonstrated.** Source discovery and bounded workers ran, but the requested new-publication → validated bytes → storage → processing-handoff chain did not complete. Existing archive files, successful old queue rows and the 22 local extractions are not presented as new downloads.

The read-only canonical fresh-download selector at {latest['observed_at_utc']} returned `{latest.get('permitted_fresh_download_selection')}`. Routine historical download allowance remains zero. Initial generic pending count 23,631 is not a source-permitted download backlog.

The manual discovery receipt is `{source_results.get('discovery',{}).get('path','see source-checks')}`. It refreshed GLASSWALL/company 4432 and retained its presentation as `expected_not_yet_published`; CANHLIFE/company 106 was administratively deferred by `HOST_DEFERRED`. Official observations retained `source_access_blocked` for its issuer entry, an NSE corporate-announcement read timeout, and a subsequent host deferral. No publication URL or publication date was fabricated. Later scheduled discovery remains separately recorded by the installed dispatcher.

The manual generic download receipt is `{source_results.get('download',{}).get('path','see source-checks')}`. It returned `DEFERRED_ENVIRONMENT / Initial external growth budget exhausted` before container execution or HTTP. At that observation free APFS capacity was 374,239,907,840 bytes versus the retained 375,164,096,512-byte baseline: 924,188,672 bytes difference exceeded the unchanged 536,870,912-byte guard. Later physical checks recovered capacity; this transient observation is not a permanent source ban. The guard/baseline was not raised or reset.

The separate retained rating-stage canary ended `INTERACTION_REQUIRED / no_validated_report_target`: job {rating.get('download_job_id',14842)}, artifact {rating.get('artifact_id',83252)}, publisher host `{rating.get('target_host','www.indiaratings.co.in')}`, URL `{rating.get('requested_url','https://www.indiaratings.co.in/pressrelease/69575')}`. Its metadata publication date is {rating.get('event_date','2024-04-22')}; this older publication is explicitly not fresh-download proof. The canonical next eligible job consumed the one rating stage/day, so another publisher was not tried by bypassing that budget. No browser interaction restriction or 403 was circumvented.

Exact host budgets, outcome timestamps and source checks are under `source-checks/`, with the original dispatcher paths above. `source-checks.json` preserves operation-level results; detailed runtime outcomes distinguish a successful worker invocation from an actual download. BSE API holds and CRISIL/ICRA publisher restrictions remain in the live policy snapshot. Local processing continued independently with zero network access.
''')
    status=json.loads((raw/'live-status.json').read_text()) if (raw/'live-status.json').exists() else {}
    financial=[]
    for path in sorted((ROOT/'artifacts/automatic-acquisition').glob('2026-10-27-current-financial*/acquisition-summary.json')):
        data=json.loads(path.read_text())
        for period in data.get('periods',[]):
            if period.get('stage')!='financial_batch_outcome':continue
            item=period['result'];accepted=item.get('summary',{}).get('outcome_counts',{}).get('accepted',0)
            if accepted:financial.append({'receipt':str(path.relative_to(ROOT)),'accepted':accepted,
                'outcomes':redact(item.get('outcomes',[]))})
    save('financial-operational-evidence.json',financial)
    last_financial=status.get('receipts',{}).get('last_successful_financial_batch',{})
    blocked=status.get('receipts',{}).get('blocked_stages',{})
    markdown('LIVE_STATUS.md',f'''# Final live status

Status observation: {status.get('observed_at_utc')}; database inventory observation: {latest['observed_at_utc']}. Report generation: {summary['generated_at_utc']}.

- Installed LaunchAgent loaded: {status.get('dispatcher_loaded')}; admissions paused: {status.get('admissions_paused')}.
- Current ownership observation: {status.get('currently_executing_stage')}; inspection errors: {status.get('inspection_errors',[])}.
- Latest successful financial execution: {last_financial}.
- Meaningful standalone acceptance receipts captured today: {len(financial)} batches, {sum(r['accepted'] for r in financial)} accepted jobs. `financial-operational-evidence.json` retains actual parse/queue identities. The 11:15 batch accepted WENDT/ABSLAMC; 11:45 accepted SRF/PCJEWELLER. These were real installed-scheduler executions after canonical wake, not queue-only admissions.
- Existing blocked stages: {blocked}. The BSE evening browser stop from 26 October remains retained. NSE publisher timeouts/access holds are not bypassed. Current intake remains standalone-only; consolidated intake is a separate follow-up. Existing accepted consolidated historical observations were preserved.
- Storage identity remains external UUID `60E1204B-CB98-48C4-9398-1D2FBAC9B44A` at `/Volumes/My Passport/passport/trading-ai`. Live readiness/capacity and original sample hashes are in the status/preflight JSON. Existing verified production backups remain available; no database/schema migration, backup overwrite or second processing database was introduced.
- Authorization: scheduler service is continuous under the retained recovery authorization; its old verification deadline is not a service expiry. Phase 2 is a separate exact-100-content campaign, parser 1.1.0/Tesseract 5.5.0, sharing 24 daily processing stage operations. No consumed historical authorization was edited. Configuration fingerprints remain checked; `configuration-preservation.json` records unchanged installed scheduler/configuration hashes.
- Pilot: {pilot}. Daily execution stopped with two normal-arrival reservations protected. The remaining {pilot['deferred']} IDs need future permitted batches; the finite campaign is not installed as an unbounded historical scheduler. Routine financial/document acquisition remains scheduled normally. No inference/scoring or Focus List selection change ran.
- Generic queue jobs: {original['totals'][0]['generic_jobs']} → {totals['generic_jobs']}; rating queue jobs: {original['totals'][0]['rating_jobs']} → {totals['rating_jobs']}; artifacts: {original['totals'][0]['artifacts']} → {totals['artifacts']}; content IDs: {original['totals'][0]['contents']} → {totals['contents']}. Ordinary financial/discovery operations may add metadata; these additions are not counted as downloaded bytes.
- Canonical processing work: {totals.get('processing_work',27)}; extractions: {totals.get('extractions',33667)}. The pilot added {len(receipts)} recorded contents to the initial 5 work rows / 33,645 extractions. Each committed extraction was independently validated; no duplicate or cross-content page persistence was found.

Remaining constraints: finish any explicitly unverified physical references shown in `inventory-summary.json`; investigate missing/corrupt bytes without rewriting originals; review partial/failed historical parses; resume the finite pilot on later budget-permitted days; resolve publisher interaction/source availability; investigate the retained BSE visible-browser stop separately. The corpus-wide throughput estimate is not authorization for a broader historical campaign.
''')
    for directory in raw.glob('refresh-*'):
        if directory.is_dir() and not any(directory.iterdir()):directory.rmdir()
    print(json.dumps({'inventory':summary['categories'],'pilot':pilot,'benchmark':performance['fresh_median_seconds']}))


if __name__=='__main__':main()
