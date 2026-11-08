"""Read-only document census and checkpointed, bounded physical verification.

Run in the sealed production image under the host admission/owner locks. No
database writes, network, migrations, queue reconciliation or file repair.
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

CONTENT_SQL = """SELECT c.content_id,c.artifact_id,c.content_sha256,c.byte_size,c.content_origin,c.is_official,c.blob_id,
 c.media_type,c.extraction_status,c.created_at_utc,b.local_path,a.company_id,
 a.artifact_type,a.publisher,a.period_end,a.event_date,
 EXISTS(SELECT 1 FROM screener_collection_scope s WHERE s.company_id=a.company_id
 AND s.eligibility='eligible') eligible
 FROM artifact_contents c JOIN response_blobs b USING(blob_id)
 JOIN document_artifacts a USING(artifact_id) ORDER BY c.content_id"""

SENSITIVE_QUERY = re.compile(r'([?&](?:access_token|auth_token|api_key|password|sessionid|x-amz-signature|signature|token)=)(?!<redacted>)([^&#\s"\\]+)',re.I)


def redact_query_values(record):
    """Keep publisher/path provenance without exporting signed access values."""
    result=dict(record)
    for key,value in record.items():
        if isinstance(value,str) and SENSITIVE_QUERY.search(value):
            result.setdefault(key+'_sha256',hashlib.sha256(value.encode()).hexdigest())
            result[key]=SENSITIVE_QUERY.sub(lambda match:match[1]+'<redacted>',value)
    return result


def supported(row):
    return row['media_type'] in ('application/pdf','application/vnd.ms-powerpoint',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation') or (
        row['media_type']=='text/html' and row['artifact_type']=='credit_rating')


def verify(row, roots=(Path('/documents'), Path('/data/snapshots'))):
    start=time.monotonic()
    path=Path(row['local_path'])
    # Resolve symlinks before opening; never follow an archive escape.
    resolved=path.resolve()
    if not any(resolved.is_relative_to(root.resolve()) for root in roots):
        raise RuntimeError('Archive path outside approved storage: '+str(path))
    result={'content_id':row['content_id'],'local_path':str(path),
        'verified_at_utc':datetime.now(timezone.utc).isoformat()}
    try:
        with path.open('rb') as stream:
            before=os.fstat(stream.fileno())
            digest=hashlib.sha256()
            size=0
            while chunk:=stream.read(1024*1024):
                digest.update(chunk);size+=len(chunk)
            after=os.fstat(stream.fileno())
        if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
            raise RuntimeError('Archive changed during verification')
        result.update(actual_sha256=digest.hexdigest(),actual_bytes=size,
            inode=after.st_ino,mtime_ns=after.st_mtime_ns,
            state='verified' if size==row['byte_size'] and digest.hexdigest()==row['content_sha256'] else 'corrupt')
    except FileNotFoundError:
        result['state']='missing'
    result['seconds']=time.monotonic()-start
    return result


def write(path, data):
    temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w') as stream:
        json.dump(data,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)


def census(db, out):
    if (out/'inventory.json').exists():raise ValueError('Preserve existing census; use a new refresh directory')
    sys.path[:0]=['/activation','/app']
    from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot
    from production_ownership import assert_no_conflicting_leases
    from scrapper.db.database import check_schema_compatibility
    from scrapper.pipeline.extract_document import processing_version
    from scrapper.documents.ocr import TesseractOcr
    guard=assert_safe_empty_wal(db)
    baseline=json.loads(Path('/activation/production-baseline.json').read_text())
    if db.stat().st_ino!=baseline['db_after']['inode']:raise RuntimeError('Database identity differs')
    version=processing_version(TesseractOcr())
    with closing(sqlite3.connect('file:'+str(db)+'?mode=ro&immutable=1',uri=True)) as c:
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON');c.execute('PRAGMA temp_store=MEMORY')
        c.execute('BEGIN')
        check_schema_compatibility(c)
        if [dict(r) for r in c.execute('SELECT * FROM schema_migrations ORDER BY version')]!=baseline['ledger']:
            raise RuntimeError('Migration ledger differs')
        now=datetime.now(timezone.utc).isoformat()
        assert_no_conflicting_leases(c,now=now)
        queries={
            'totals':"""SELECT (SELECT count(*) FROM artifact_download_queue) generic_jobs,
             (SELECT count(*) FROM credit_rating_download_queue) rating_jobs,
             (SELECT count(*) FROM document_artifacts) artifacts,
             (SELECT count(DISTINCT artifact_id) FROM artifact_contents) downloaded_artifacts,
             (SELECT count(*) FROM artifact_contents) contents,
             (SELECT count(DISTINCT content_sha256) FROM artifact_contents) recorded_hashes,
             (SELECT count(*) FROM document_processing_work) processing_work,
             (SELECT count(*) FROM document_extractions) extractions,
             (SELECT count(DISTINCT company_id) FROM document_artifacts JOIN artifact_contents USING(artifact_id)) covered_companies""",
            'generic_states':'SELECT status,count(*) jobs FROM artifact_download_queue GROUP BY status',
            'rating_states':'SELECT status,count(*) jobs FROM credit_rating_download_queue GROUP BY status',
            'duplicate_scopes':"""SELECT count(*) groups,sum(n-1) extra_rows FROM
             (SELECT artifact_id,requested_url,count(*) n FROM artifact_download_queue
             GROUP BY artifact_id,requested_url HAVING count(*)>1)""",
            'pending_associations':"""SELECT q.status,
             EXISTS(SELECT 1 FROM screener_collection_scope s WHERE s.company_id=a.company_id AND s.eligibility='eligible') eligible,
             EXISTS(SELECT 1 FROM artifact_contents c WHERE c.artifact_id=a.artifact_id) attached,
             count(*) jobs FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id)
             WHERE q.status IN ('PENDING','RETRYABLE_FAILURE','LEASED') GROUP BY q.status,eligible,attached""",
            'parser_versions':"""SELECT parser_name,parser_version,ocr_engine,ocr_engine_version,options_json,
             extraction_status,count(*) extractions FROM document_extractions GROUP BY 1,2,3,4,5,6""",
            'processing_failure_records':"""SELECT e.extraction_id,e.content_id,e.parser_version,
                e.extraction_status,c.extraction_status content_status,c.artifact_id,c.content_sha256,
                e.error_json FROM document_extractions e LEFT JOIN artifact_contents c USING(content_id)
                WHERE e.extraction_status='failed' ORDER BY e.extraction_id""",
            'multiple_versions':"""SELECT count(*) artifacts,sum(n-1) extra_contents FROM
             (SELECT artifact_id,count(*) n FROM artifact_contents GROUP BY artifact_id HAVING n>1)""",
            'hash_aliases':"""SELECT count(*) hashes,sum(n-1) extra_content_ids FROM
             (SELECT content_sha256,count(*) n FROM artifact_contents GROUP BY content_sha256 HAVING n>1)""",
            'same_hash_multiple_urls':"""SELECT count(*) hashes,coalesce(sum(n-1),0) extra_urls FROM
                (SELECT content_sha256,count(DISTINCT requested_url) n FROM artifact_download_attempts
                WHERE result_status='validated' GROUP BY content_sha256 HAVING count(DISTINCT requested_url)>1)""",
            'queue_scopes':"SELECT scope_id,status,count(*) jobs FROM artifact_download_queue GROUP BY scope_id,status",
            'financial_scope_source_aliases':"""SELECT count(*) artifact_url_groups,
                sum(standalone=1 AND consolidated=1) both_financial_scopes,
                sum(standalone=1) standalone_groups,sum(consolidated=1) consolidated_groups FROM (
                SELECT artifact_id,document_url,
                max(discovery_page_url LIKE '%/company/%' AND discovery_page_url NOT LIKE '%/consolidated/%') standalone,
                max(discovery_page_url LIKE '%/company/%/consolidated/%') consolidated
                FROM artifact_sources WHERE discovered_from_source='screener'
                GROUP BY artifact_id,document_url)""",
            'policy':'SELECT * FROM credit_rating_host_policy',
            'cooldowns':'SELECT * FROM artifact_download_host_health WHERE blocked_until_utc > ?',
            'access_holds':'SELECT * FROM exchange_source_access_events ORDER BY rowid DESC LIMIT 5',
            'recent_rating_jobs':"""SELECT q.download_job_id,q.artifact_id,q.target_host,q.requested_url,q.status,
                q.last_result_status,q.last_error,q.updated_at_utc,a.company_id,a.publisher,a.event_date,
                a.first_observed_at_utc,a.title_raw FROM credit_rating_download_queue q
                JOIN document_artifacts a USING(artifact_id) ORDER BY q.updated_at_utc DESC LIMIT 8""",
        }
        result={'observed_at_utc':now,'guard':guard,'processing_version':dict(zip(
            ('parser_name','parser_version','ocr_engine','ocr_engine_version','options_json'),version.values())),
            'queries':queries,'network_requests':0,'production_writes':0}
        for name,sql in queries.items():
            # Optional source-access schema varies by baseline; report a failed
            # optional query explicitly, never invent a policy state.
            try:result[name]=[dict(r) for r in c.execute(sql,(now,) if name=='cooldowns' else ())]
            except sqlite3.OperationalError as exc:
                if name!='access_holds':raise
                result[name]={'query_error':str(exc)}
        cache={r['content_id']:dict(r) for r in c.execute('''SELECT content_id,extraction_id,extraction_status,
            page_or_slide_count FROM document_extractions WHERE parser_name=? AND parser_version=?
            AND ocr_engine=? AND ocr_engine_version=? AND options_json=?''',version.values())}
        with (out/'contents.jsonl').open('w') as stream:
            for row in c.execute(CONTENT_SQL):
                item=dict(row);item['supported']=supported(item);item['exact_cache']=cache.get(item['content_id'])
                stream.write(json.dumps(redact_query_values(item))+'\n')
            stream.flush();os.fsync(stream.fileno())
        result['content_query']=CONTENT_SQL
        result['backups']=[{'name':p.name,'bytes':p.stat().st_size} for p in Path('/data/backups').glob('*.db')]
        prior=Path('/data/backups/acquisition-recovery-20261026-20261026T094742Z.db')
        if prior.is_file():
            with closing(sqlite3.connect(prior.as_uri()+'?mode=ro&immutable=1',uri=True)) as historical:
                historical.row_factory=sqlite3.Row;historical.execute('PRAGMA query_only=ON')
                old_ids={r[0] for r in historical.execute('SELECT download_job_id FROM artifact_download_queue')}
                result['attachment_discrepancy']={'backup':str(prior),'backup_jobs':len(old_ids),
                    'backup_associations':[dict(r) for r in historical.execute(queries['pending_associations'])],
                    'new_job_rows':[]}
                added=[r[0] for r in c.execute('SELECT download_job_id FROM artifact_download_queue') if r[0] not in old_ids]
                for job_id in added:
                    row=c.execute('''SELECT q.*,a.artifact_type,a.company_id,a.first_observed_at_utc,
                        (SELECT count(*) FROM artifact_contents x WHERE x.artifact_id=q.artifact_id) attached_contents
                        FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id)
                        WHERE q.download_job_id=?''',(job_id,)).fetchone()
                    result['attachment_discrepancy']['new_job_rows'].append(dict(row))
                attached_sql="""SELECT q.download_job_id FROM artifact_download_queue q
                    JOIN document_artifacts a USING(artifact_id) WHERE q.status='PENDING'
                    AND EXISTS(SELECT 1 FROM screener_collection_scope s WHERE s.company_id=a.company_id AND s.eligibility='eligible')
                    AND EXISTS(SELECT 1 FROM artifact_contents x WHERE x.artifact_id=a.artifact_id)"""
                old_attached={r[0] for r in historical.execute(attached_sql)}
                new_attached={r[0] for r in c.execute(attached_sql)}
                comparison=result['attachment_discrepancy']
                comparison.update(backup_eligible_pending_attached=len(old_attached),
                    current_eligible_pending_attached=len(new_attached),
                    added_attached_job_ids=sorted(new_attached-old_attached),
                    removed_attached_job_ids=sorted(old_attached-new_attached))
                comparison['changed_rows']=[]
                for job_id in sorted(old_attached^new_attached):
                    fields='download_job_id,artifact_id,scope_id,status,created_at_utc,updated_at_utc,last_result_status,last_error'
                    old=historical.execute('SELECT '+fields+' FROM artifact_download_queue WHERE download_job_id=?',(job_id,)).fetchone()
                    new=c.execute('SELECT '+fields+' FROM artifact_download_queue WHERE download_job_id=?',(job_id,)).fetchone()
                    change={'before':dict(old) if old else None,'after':dict(new) if new else None}
                    if new is not None:
                        company_id=c.execute('SELECT company_id FROM document_artifacts WHERE artifact_id=?',(new['artifact_id'],)).fetchone()[0]
                        scope_sql='SELECT company_path,eligibility,floor_crore,observed_at_utc,evidence_json FROM screener_collection_scope WHERE company_id=?'
                        change.update(company_id=company_id,
                            before_scope=[dict(r) for r in historical.execute(scope_sql,(company_id,))],
                            after_scope=[dict(r) for r in c.execute(scope_sql,(company_id,))])
                    comparison['changed_rows'].append(change)
        from scrapper.operations.workers.automatic_documents import select_downloads
        result['permitted_fresh_download_selection']=select_downloads(c,now=now,
            epoch='2026-10-09T00:00:00+00:00',limit=1,historical_limit=0)
        result['latest_downloads']=[dict(r) for r in c.execute("SELECT download_job_id,artifact_id,requested_url,status,updated_at_utc FROM artifact_download_queue WHERE status='SUCCEEDED' ORDER BY updated_at_utc DESC LIMIT 5")]
        pilot=Path('/campaign/PILOT_AUTHORIZATION_AND_PLAN.json')
        if pilot.exists():
            manifest=json.loads(pilot.read_text());pilot_results=[]
            for selected in manifest['contents']:
                row=c.execute('''SELECT c.content_id,c.artifact_id,c.content_sha256,c.byte_size,c.extraction_status,
                    a.company_id,co.current_legal_name,a.title_raw,a.publisher,a.event_date,a.period_end,
                    e.extraction_id,e.parser_name,e.parser_version,e.ocr_engine,e.ocr_engine_version,e.options_json,
                    e.extraction_status parser_status,e.page_or_slide_count,e.native_page_count,e.ocr_page_count,
                    e.unusable_page_count,e.error_json,w.work_id
                    FROM artifact_contents c JOIN document_artifacts a USING(artifact_id)
                    JOIN companies co ON co.company_id=a.company_id
                    LEFT JOIN document_extractions e ON e.content_id=c.content_id
                    AND e.parser_name=? AND e.parser_version=? AND e.ocr_engine=? AND e.ocr_engine_version=? AND e.options_json=?
                    LEFT JOIN document_processing_work w ON w.content_id=c.content_id AND w.resource_key=?
                    WHERE c.content_id=?''',(*version.values(),version.key(selected['content_id']),selected['content_id'])).fetchone()
                item=dict(row)
                if any(item[k]!=selected[k] for k in ('content_id','artifact_id','company_id','content_sha256','byte_size')):
                    raise RuntimeError('Pilot immutable identity differs')
                if item['extraction_id'] is not None:
                    item['pages']=dict(c.execute('''SELECT count(*) count,sum(length(selected_text)) text_characters,
                        min(page_or_slide_number) first_page,max(page_or_slide_number) last_page,
                        sum(content_id<>?) cross_content_pages FROM document_pages WHERE extraction_id=?''',
                        (selected['content_id'],item['extraction_id'])).fetchone())
                    item['exact_extraction_count']=c.execute('''SELECT count(*) FROM document_extractions
                        WHERE content_id=? AND parser_name=? AND parser_version=? AND ocr_engine=?
                        AND ocr_engine_version=? AND options_json=?''',(selected['content_id'],*version.values())).fetchone()[0]
                    if item['pages']['cross_content_pages'] or item['exact_extraction_count']!=1:
                        raise RuntimeError('Pilot persistence lineage differs')
                pilot_results.append(item)
            result['pilot_results']=pilot_results
        with (out/'jobs.jsonl').open('w') as stream:
            committed={r['download_job_id']:r['content_id'] for r in c.execute('''SELECT qa.download_job_id,d.content_id
                FROM artifact_download_queue_attempts qa JOIN artifact_download_attempts d USING(download_attempt_id)
                WHERE d.result_status='validated' ORDER BY d.download_attempt_id''')}
            for row in c.execute('''SELECT q.download_job_id,q.artifact_id,q.scope_id,q.requested_url,
                q.status,q.last_result_status,q.last_error,q.created_at_utc,a.company_id,a.artifact_type,
                EXISTS(SELECT 1 FROM screener_collection_scope s WHERE s.company_id=a.company_id AND s.eligibility='eligible') eligible
                FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id) ORDER BY q.download_job_id'''):
                item=dict(row);item.update(queue_kind='generic',committed_content_id=committed.get(row['download_job_id']))
                stream.write(json.dumps(redact_query_values(item))+'\n')
            for row in c.execute('''SELECT q.download_job_id,q.artifact_id,q.requested_url,q.status,
                q.last_result_status,q.last_error,q.created_at_utc,a.company_id,a.artifact_type,
                EXISTS(SELECT 1 FROM screener_collection_scope s WHERE s.company_id=a.company_id AND s.eligibility='eligible') eligible
                FROM credit_rating_download_queue q JOIN document_artifacts a USING(artifact_id) ORDER BY q.download_job_id'''):
                stream.write(json.dumps(redact_query_values({**dict(row),'queue_kind':'rating','committed_content_id':None}))+'\n')
    if snapshot(db)!=guard['files']:raise RuntimeError('Production changed during snapshot')
    result['contents_sha256']=hashlib.sha256((out/'contents.jsonl').read_bytes()).hexdigest()
    write(out/'inventory.json',result)
    return result['totals']


def verify_batch(out, limit, seconds, sample=False, archive_root=None, candidates=False):
    body=(out/'contents.jsonl').read_bytes()
    if hashlib.sha256(body).hexdigest()!=json.loads((out/'inventory.json').read_text())['contents_sha256']:
        raise RuntimeError('Logical snapshot checksum differs')
    rows=[json.loads(line) for line in body.splitlines()]
    checkpoint=out/'verification.jsonl'
    done={json.loads(line)['content_id'] for line in checkpoint.read_text().splitlines()} if checkpoint.exists() else set()
    if candidates:
        buckets={kind:[] for kind in ('credit_rating','presentation','transcript')}
        for row in rows:
            if row['eligible'] and row['supported'] and row['extraction_status']=='not_started' and row['artifact_type'] in buckets:
                buckets[row['artifact_type']].append(row)
        rows=[]
        while buckets:
            for kind in list(buckets):
                rows.append(buckets[kind].pop())
                if not buckets[kind]:del buckets[kind]
    if sample:
        # Round-robin across type, media, eligibility, size, era and outcome.
        groups={}
        for row in rows:
            key=(row['artifact_type'],row['media_type'],row['eligible'],
                row['byte_size']//1048576,row['created_at_utc'][:7],row['extraction_status'])
            groups.setdefault(key,[]).append(row)
        rows=[]
        while groups and len(rows)<limit:
            for key in list(groups):
                if len(rows)>=limit:break
                rows.append(groups[key].pop(len(groups[key])//2))
                if not groups[key]:del groups[key]
    start=time.monotonic();checked=[]
    pending=[r for r in rows if r['content_id'] not in done][:limit]
    def physical(row):
        logical_path=row['local_path']
        if archive_root is not None:
            if not logical_path.startswith('/documents/'):raise RuntimeError('Cannot materialize non-archive reference')
            row={**row,'local_path':str(archive_root/logical_path.removeprefix('/documents/'))}
        item=verify(row,roots=(archive_root,)) if archive_root is not None else verify(row)
        if archive_root is not None and item['state']=='missing' and not (archive_root/'.trading_ai_runtime_identity.json').is_file():
            raise OSError('Archive disconnected; missing-file classification suspended')
        item['local_path']=logical_path
        return item
    with checkpoint.open('a') as stream:
        # Four bounded readers overlap disk I/O. Only the coordinator appends the
        # checkpoint. Never schedule an unbounded future per corpus document.
        with ThreadPoolExecutor(max_workers=4) as pool:
            for index in range(0,len(pending),4):
                if time.monotonic()-start>=seconds:break
                futures=[pool.submit(physical,r) for r in pending[index:index+4]]
                for future in futures:
                    item=future.result();stream.write(json.dumps(item)+'\n')
                    if len(checked)%25==24:stream.flush();os.fsync(stream.fileno())
                    checked.append(item)
        stream.flush();os.fsync(stream.fileno())
    result={'checked':len(checked),'states':dict(Counter(r['state'] for r in checked)),
        'seconds':time.monotonic()-start,'previously_checked':len(done),'sample':sample}
    print(json.dumps(result));return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('census','verify','sample'))
    p.add_argument('--db',type=Path,default=Path('/data/scraper.db'))
    p.add_argument('--output',type=Path,default=Path('/evidence'))
    p.add_argument('--limit',type=int,default=100)
    p.add_argument('--seconds',type=int,default=40)
    a=p.parse_args()
    if not 1<=a.limit<=2000 or not 1<=a.seconds<=50:raise ValueError('Bounded verification required')
    if a.action=='census':print(json.dumps(census(a.db,a.output)))
    else:verify_batch(a.output,a.limit,a.seconds,a.action=='sample')


if __name__=='__main__':main()
