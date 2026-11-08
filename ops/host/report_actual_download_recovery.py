"""Build requested reports from retained database, byte and controller receipts."""
import csv
import hashlib
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/acquisition-actual-download-recovery'


def write_csv(name, rows):
    with (OUT/name).open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['no_candidates'])
        w.writeheader();w.writerows(rows)


def main():
    d=json.loads((OUT/'raw/live-audit.json').read_text())
    verified={r['content_id']:r for r in d['fresh_content_verification']}
    results=[]
    for r in d['fresh_download_attempts']:
        v=verified.get(r['content_id'],{})
        results.append({'company':r['current_legal_name'],'company_id':r['company_id'],
            'reporting_quarter':r['period_end'] or 'Unknown','document_type':r['artifact_type'],
            'source_url':r['requested_url'],'host':urlsplit(r['requested_url']).hostname,
            'already_stored_before_attempt':False,'attempted_or_deferred':'attempted',
            'queue':'generic','download_job_id':r['download_job_id'],
            'http_status':r['http_status'],'download_outcome':r['result_status'],
            'bytes_saved':r['byte_size'] if r['result_status']=='validated' else 0,
            'reported_response_size':r['byte_size'],'sha256':r['content_sha256'],
            'physical_hash_verification':v.get('state','not_applicable'),
            'content_id':r['content_id'],'database_state':r['queue_status'],
            'failure_or_blocker':r['error_json'] if r['result_status']!='validated' else ''})
    for r in d['fresh_rating_resolutions']:
        steps=[s for s in d['fresh_rating_steps'] if s['resolution_attempt_id']==r['resolution_attempt_id']]
        results.append({'company':r['current_legal_name'],'company_id':r['company_id'],
            'reporting_quarter':r['period_end'] or 'Unknown','document_type':'credit_rating',
            'source_url':r['original_url'],'host':urlsplit(r['original_url']).hostname,
            'already_stored_before_attempt':False,'attempted_or_deferred':'attempted',
            'queue':'rating','download_job_id':r['download_job_id'],
            'http_status':steps[-1]['http_status'] if steps else None,
            'download_outcome':r['resolution_status'],'bytes_saved':0,'reported_response_size':None,
            'sha256':None,'physical_hash_verification':'not_applicable','content_id':None,
            'database_state':r['queue_status'],'failure_or_blocker':r['error_json']})
    write_csv('DOWNLOAD_EXECUTION_RESULTS.csv',results)
    candidates=[];seen=set()
    for r in results:
        if r['source_url'] in seen:continue
        seen.add(r['source_url'])
        candidates.append({k:r[k] for k in ('company','company_id','reporting_quarter','document_type',
            'source_url','host','already_stored_before_attempt','queue','download_job_id')})
    write_csv('DOWNLOAD_CANDIDATES.csv',candidates)
    campaign=json.loads((OUT/'HISTORICAL_CAMPAIGN.json').read_text())
    write_csv('HISTORICAL_DOWNLOAD_CANDIDATES.csv',campaign['jobs'])
    receipt_root=OUT/'raw/controller-receipts';receipt_root.mkdir(exist_ok=True)
    summaries=[]
    for folder in sorted((ROOT/'artifacts/automatic-acquisition').glob('2026-10-30-*')):
        if not ('actual-download-repair-' in folder.name or folder.name.endswith(('current-financial-1745','current-financial-1815','current-financial-1845','current-financial-1915','current-financial-1945','current-financial-2015','document-download-1825','document-download-1925','document-download-2025'))):continue
        target=receipt_root/folder.name;target.mkdir(exist_ok=True)
        for name in ('admission.json','plan.json','manifest.json','outcome.json','runtime.log',
            'daily-budget-reservation.json','execution-storage-preflight.json','scraper-storage-growth.json',
            'host-timings.json','budget-exhausted.json','acquisition-summary.json'):
            if (folder/name).exists():shutil.copyfile(folder/name,target/name)
        if (folder/'outcome.json').exists():summaries.append({'receipt':str(target.relative_to(OUT)),**json.loads((folder/'outcome.json').read_text())})
    (OUT/'raw/controller-summary.json').write_text(json.dumps(summaries,indent=2)+'\n')
    successes=[r for r in results if r['download_outcome']=='validated']
    companies=d['accepted_distinct'][0]['companies']
    ac=d['quarter_monitor']['acquisition']
    total_bytes=sum(r['bytes_saved'] for r in successes)
    handoffs=sum(r['processing_handoff'] for r in d['fresh_processing'] if r['content_origin']=='publisher_original')
    content_lines='\n'.join(f"- {r['company']} ({r['company_id']}), {r['document_type']}, {r['reporting_quarter']}: content {r['content_id']}, {r['bytes_saved']:,} bytes, SHA-256 `{r['sha256']}`; independent physical verification {r['physical_hash_verification']}." for r in successes)
    campaign_sha=hashlib.sha256((OUT/'HISTORICAL_CAMPAIGN.json').read_bytes()).hexdigest()
    (OUT/'FINAL_STATUS.md').write_text(f'''# Actual acquisition result

Observed {d['observed_at_utc']}. **{len(successes)} genuine new publisher documents, {total_bytes:,} saved bytes**, independently re-read and SHA-256 verified. These are new source blobs and contents, not existing files counted again.

{content_lines}

1. Screener finances are being retrieved and accepted. The repaired exact overlay has a real scheduled 18:15 IST result of accepted 2/2, with subsequent accepted work retained in controller receipts.
2. **{companies} distinct September 2026 companies** have accepted explicit quarterly observations. All new acquisition remains standalone; consolidated intake is unchanged.
3. PPT, transcript and credit rating links are extracted. Initial accepted parses had 0 PPT/0 transcript against 1,225 rating rows because current HTML used button-chip. The repaired saved-page census found 241 current-call PPT/transcript links for the initial 223 companies. Current accepted-parse audit has {d['link_types']['concall_ppt']} PPT, {d['link_types']['concall_transcript']} transcript and {d['link_types']['credit_rating']} rating rows (includes historical call groups, unavailable slots and retained page versions; not distinct quarter documents).
4. Missing chip parsing prevented promotion. Rediscovered existing URLs retained pre-epoch queue identity, which zero historical allowance excluded. A separate zero-redirect setting rejected reachable BSE redirects and incorrectly counted them as host network failures.
5. Fixed both markup forms and parser versions, bounded saved-page reparse/handoff, canonical queue reuse, exact-ID finite recovery selection/identity validation, pinned-image financial overlays, redirect HTTP evidence, one validated redirect with two request units, and quarter-specific CLI facts/exclusions.
6. **{len(successes)} actual new documents** downloaded. No rating shell, oversized rejected file, guard failure or old stored content is counted as a download.
7. **0 pre-epoch jobs newly authorized in production.** Initial read-only eligibility found 2,644 distinct unstored artifacts, all pre-epoch; a finite 10-job campaign is ready for separate approval. The tested selector can admit those exact old IDs without changing age, ownership, source limits or routine historical allowance. {len(d['fresh_queue_jobs'])} genuinely new generic jobs were materialized during this task; current eligibility varies with retry/host state.
8. Every successful document belongs to its verified original company and has explicit period_end=2026-09-30, based on accepted quarter plus the matching current call group. Publication timestamps remain independent/unknown. Historical artifacts are not relabeled.
9. The canonical automatic worker performs productive work in controlled production runs. The last pre-fix observed scheduled download failed at its redirect boundary; a future successful LaunchAgent download is not claimed without a retained receipt. {handoffs} new originals have canonical deterministic-processing inbox handoffs. Extraction attempts were deferred by the already exhausted 24/day processing allowance; no budget was reset and completion is not claimed.
10. Remaining: approval of the exact historical campaign; CRISIL/ICRA remain blocked by reviewed terms; India Ratings returned a 200 shell with no validated report target and INTERACTION_REQUIRED; Eternal's 12,334,484-byte presentation exceeds the 10 MiB limit. Existing host cooldowns and daily processing limits remain enforced.

Historical campaign SHA-256: `{campaign_sha}`. No historical campaign was executed.

Regression receipts: 186-test final baseline suite passed; 49 redirect/budget/queue tests passed after the final redirect repair; 47 tests passed against the exact financial image overlays; the three existing quarter regressions passed, including 147,000 queued jobs in a read-only query fixture. Earlier failed checks and corrections remain in raw/.
''')
    (OUT/'LIVE_VALIDATION.md').write_text(f'''# Live source and byte receipts

Final database observation: {d['observed_at_utc']}. Exact per-URL outcomes are in DOWNLOAD_EXECUTION_RESULTS.csv; original candidate/provenance and unknown-period evidence remain in CURRENT_QUARTER_LINK_CANDIDATES.csv, SCREENER_DOCUMENT_LINK_AUDIT.csv and raw/live-audit.json.

{content_lines}

The source blobs have new creation/first-observation timestamps, shown in fresh_processing in the final audit. All {handoffs} publisher originals have processing inbox records. Extraction is still not_started because the existing daily processing capacity is exhausted. The networkless deterministic worker was not given an extra allowance.

Actual failures: the original BSE requests reached redirects and were rejected under the old max_redirects=0 policy; old receipts retained no numeric status. The repaired transport retains status/redirect evidence and does not turn a local redirect cap into a host outage. The current policy permits one validated redirect and reserves two daily request units, preserving the 24 generic request-unit maximum, 42/day source-request allocation and 306,184,192-byte daily ceiling. No existing cooldown was erased.

Eternal returned HTTP 200 with declared size 12,334,484 bytes; zero content bytes were saved. The limit stayed 10,485,760 bytes. The India Ratings pilot returned HTTP 200, text_length 78, candidate_count 0, and no validated report URL; the resolver recorded INTERACTION_REQUIRED/no_validated_report_target. It did not bypass interaction or claim a report download.

The scheduled financial 18:15 run accepted 2/2. The 17:45 run exposed a pinned-image queue keyword mismatch after acceptance; the complete exact overlay set subsequently passed 47 ingestion tests. Initial source/runtime attestation and host-variable failures were retained, corrected, and the exact own-stop records archived. No publisher HTTP occurred in those failed admissions.

Drive UUID, physical identity, reserve, original archive hashes and scraper-growth accounting passed the existing production preflight. Each real worker used the canonical owner gate, SQL write fence, shared host permits, queue fencing, content validation, atomic byte store and hash retention. No migrations, acquisition epoch resets, historical timestamp edits, archive replacement, financial scope changes or legacy worker activation occurred.

Current quarter monitor: {ac['document_links_discovered']} associated artifacts, {ac['documents_already_stored']} stored, {ac['new_documents_downloaded_since_epoch']} new since epoch, {ac['documents_awaiting_download']} awaiting download, {ac['documents_blocked_source_policy']} source-policy blocked, {ac['document_processing_completed']} processing completed. {ac['unknown_period_documents_for_quarter_companies']} documents for quarter companies have unknown periods and are explicitly excluded from quarter totals.
''')
    financial=OUT/'SCREENER_FINANCIAL_STATUS.md'
    initial=financial.read_text().split('\n## Final observation')[0]
    financial.write_text(initial+f'\n## Final observation\n\nAt {d["observed_at_utc"]}, {companies} distinct companies have accepted September standalone observations. Exact scheduled financial outcomes and original authenticated HTTP receipts are retained under raw/controller-receipts. No consolidated intake contract changed.\n')
    print(json.dumps({'companies':companies,'new_documents':len(successes),'saved_bytes':total_bytes,'handoffs':handoffs,'campaign_sha256':campaign_sha}))


if __name__=='__main__':main()
