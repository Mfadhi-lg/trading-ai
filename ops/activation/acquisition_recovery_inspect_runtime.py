"""Cold read-only recovery evidence. Run only under the existing host locks."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot
from production_ownership import assert_no_conflicting_leases
from scrapper.db.database import check_schema_compatibility


def inspect():
    db = Path('/data/scraper.db')
    before = assert_safe_empty_wal(db)
    baseline = json.loads(Path('/activation/production-baseline.json').read_text())
    if db.stat().st_ino != baseline['db_after']['inode']:
        raise RuntimeError('Production database identity differs')
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1', uri=True)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA temp_store=MEMORY')
        check_schema_compatibility(c)
        assert_no_conflicting_leases(c, now=now)
        if [dict(r) for r in c.execute('SELECT * FROM schema_migrations ORDER BY version')] != baseline['ledger']:
            raise RuntimeError('Migration ledger differs')
        def rows(sql, args=()):
            return [dict(r) for r in c.execute(sql, args)]
        result = {'observed_at_utc': now, 'guard': before, 'production_writes': 0,
                  'network_requests': 0, 'schema_verified': True}
        result['financial_failures'] = rows("""SELECT q.queue_item_id,q.company_path_normalized,COALESCE(cs.company_id,sc.company_id) company_id,
            q.reporting_scope,q.result_period,q.status,q.last_error_json,q.created_at_utc,q.updated_at_utc,
            (SELECT min(event_at_utc) FROM screener_company_queue_events e WHERE e.queue_item_id=q.queue_item_id AND e.to_status IN ('failed_terminal','failed_retryable')) first_error_at_utc,
            (SELECT max(event_at_utc) FROM screener_company_queue_events e WHERE e.queue_item_id=q.queue_item_id AND e.to_status IN ('failed_terminal','failed_retryable')) last_error_at_utc
            FROM screener_company_queue q
            LEFT JOIN screener_company_collection_state cs ON cs.company_path_normalized=q.company_path_normalized AND cs.reporting_scope=q.reporting_scope
            LEFT JOIN screener_scope_aliases alias ON alias.company_path=q.company_path_normalized
            LEFT JOIN screener_collection_scope sc ON sc.company_path=COALESCE(alias.scope_path,q.company_path_normalized)
            WHERE q.status IN ('failed_retryable','failed_terminal','manual_review')""")
        result['document_failures'] = rows("""SELECT q.download_job_id,a.company_id,a.artifact_type,
            a.period_end,q.target_host,q.status,q.attempt_count,q.last_result_status,q.last_error,
            q.created_at_utc,q.updated_at_utc,q.next_attempt_at_utc,q.lease_expires_at_utc,
            (SELECT min(d.fetched_at_utc) FROM artifact_download_queue_attempts qa JOIN artifact_download_attempts d USING(download_attempt_id) WHERE qa.download_job_id=q.download_job_id AND d.result_status<>'validated') first_error_at_utc,
            (SELECT max(d.fetched_at_utc) FROM artifact_download_queue_attempts qa JOIN artifact_download_attempts d USING(download_attempt_id) WHERE qa.download_job_id=q.download_job_id AND d.result_status<>'validated') last_error_at_utc
            FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id)
            WHERE q.status IN ('RETRYABLE_FAILURE','TERMINAL_FAILURE','LEASED','SKIPPED_POLICY')""")
        result['rating_failures'] = rows("""SELECT q.*,a.company_id,a.period_end FROM credit_rating_download_queue q
            JOIN document_artifacts a USING(artifact_id) WHERE q.status NOT IN ('SUCCEEDED','PENDING')""")
        result['cooldowns'] = rows('SELECT * FROM artifact_download_host_health WHERE blocked_until_utc>?', (now,))
        result['host_policy'] = rows('SELECT * FROM credit_rating_host_policy')
        result['scope'] = rows('SELECT eligibility,count(*) n,count(DISTINCT company_id) companies FROM screener_collection_scope GROUP BY eligibility')
        result['financial_coverage'] = rows("""SELECT reporting_scope,period_end,count(DISTINCT accepted_page_versions.company_id) companies
            FROM financial_observations JOIN accepted_page_versions USING(parse_id)
            WHERE period_type='quarter' AND period_end IN ('2026-06-30','2026-09-30')
            GROUP BY financial_observations.reporting_scope,period_end""".replace('SELECT reporting_scope','SELECT financial_observations.reporting_scope'))
        result['stored_sample'] = rows("""SELECT c.content_id,c.artifact_id,a.company_id,a.artifact_type,
            c.content_sha256,c.byte_size,c.extraction_status,b.local_path,c.created_at_utc
            FROM artifact_contents c JOIN response_blobs b USING(blob_id)
            JOIN document_artifacts a USING(artifact_id)
            ORDER BY c.created_at_utc DESC,c.content_id DESC LIMIT 12""")
        result['counts']={t:rows('SELECT status,count(*) jobs FROM '+t+' GROUP BY status') for t in ('screener_company_queue','artifact_download_queue','credit_rating_download_queue')}
        result['period_types']=rows('SELECT DISTINCT period_type FROM financial_observations LIMIT 10')
        result['latest_processing']=rows('SELECT * FROM document_extractions ORDER BY extraction_id DESC LIMIT 2')
        result['recent_rating_jobs']=rows('SELECT * FROM credit_rating_download_queue ORDER BY updated_at_utc DESC LIMIT 3')
        result['processing_work'] = rows('SELECT * FROM document_processing_work')
        result['processing_inbox'] = rows('SELECT * FROM document_processing_inbox')
        result['latest_downloads'] = rows("SELECT * FROM artifact_download_queue WHERE status='SUCCEEDED' ORDER BY updated_at_utc DESC LIMIT 5")
        result['indexes'] = rows("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index' AND tbl_name IN ('screener_company_queue','exchange_screener_enqueues','screener_company_queue_events')")
        result['backups'] = [{'path':str(p),'bytes':p.stat().st_size,'mtime':p.stat().st_mtime}
            for directory in ('/data/backups','/data/snapshots') for p in Path(directory).glob('*.db*')]
        from scrapper.operations.workers.automatic_documents import select_downloads
        from scrapper.db.documents.processing import automatic_processing_contents
        from scrapper.pipeline.extract_document import processing_version
        from scrapper.documents.ocr import TesseractOcr
        result['eligible_download_selection']=select_downloads(c,now=now,epoch='2026-10-09T00:00:00+00:00',limit=1,historical_limit=0)
        result['eligible_processing_selection']=automatic_processing_contents(c,version=processing_version(TesseractOcr()),now=now,epoch='2026-10-09T00:00:00+00:00',limit=1,historical_limit=0)
        from scrapper.db.screener_discovery_repo import preview_due_companies
        result['financial_due_canonical_count']=len(preview_due_companies(c,now_utc=now,limit=100000))
        result['eligible_financial_coverage']=rows("""SELECT count(DISTINCT s.company_id) companies
            FROM screener_collection_scope s WHERE s.eligibility='eligible'
            AND EXISTS(SELECT 1 FROM accepted_page_versions a WHERE a.company_id=s.company_id AND a.source='screener')""")
        result['accepted_scope_period_combinations']=rows('SELECT source,reporting_scope,count(*) combinations,count(DISTINCT company_id) companies FROM accepted_page_versions GROUP BY source,reporting_scope')
        result['financial_due_preview_count']=c.execute("SELECT count(*) FROM screener_company_queue q WHERE q.collection_disposition IS NULL AND q.status IN ('pending','failed_retryable') AND (q.next_attempt_at_utc IS NULL OR q.next_attempt_at_utc<=?)",(now,)).fetchone()[0]
        result['duplicate_download_groups']=rows("SELECT count(*) groups,coalesce(sum(n-1),0) extra_queue_rows FROM (SELECT artifact_id,requested_url,count(*) n FROM artifact_download_queue GROUP BY artifact_id,requested_url HAVING count(*)>1)")
        result['backlog_dispositions']=rows("""SELECT q.status,
            CASE WHEN q.created_at_utc<'2026-10-09T00:00:00+00:00' THEN 'historical' ELSE 'post_epoch' END lane,
            EXISTS(SELECT 1 FROM screener_collection_scope s WHERE s.company_id=a.company_id AND s.eligibility='eligible') company_eligible,
            EXISTS(SELECT 1 FROM artifact_contents ac WHERE ac.artifact_id=a.artifact_id) already_stored,
            count(*) jobs,count(DISTINCT a.company_id) companies,min(q.created_at_utc) oldest_job_created_at_utc
            FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id)
            WHERE q.status IN ('PENDING','RETRYABLE_FAILURE','LEASED')
            GROUP BY q.status,lane,company_eligible,already_stored""")
        result['stored_processing_backlog']=rows("""SELECT c.extraction_status,count(*) contents,count(DISTINCT a.company_id) companies
            FROM artifact_contents c JOIN document_artifacts a USING(artifact_id)
            WHERE EXISTS(SELECT 1 FROM screener_collection_scope s WHERE s.company_id=a.company_id AND s.eligibility='eligible')
            GROUP BY c.extraction_status""")
        result['download_lanes']=rows("""SELECT a.artifact_type,q.status,
            CASE WHEN q.created_at_utc<'2026-10-09T00:00:00+00:00' THEN 'historical' ELSE 'post_epoch' END lane,
            count(*) jobs,count(DISTINCT a.company_id) companies
            FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id)
            GROUP BY a.artifact_type,q.status,lane""")
        from financial_priority import select
        import types
        old=types.ModuleType('selection_before')
        exec(BEFORE_SOURCE,old.__dict__)
        result['selection_comparison']=[]
        for name,fn in (('before',old.select),('after',select),('before_warm',old.select),('after_warm',select)):
            trace=[];c.set_trace_callback(trace.append);begin=time.monotonic()
            chosen,priority,quarter=fn(c,now_utc=now,activated_at='2026-10-09T00:00:00+00:00')
            result['selection_comparison'].append({'name':name,'seconds':time.monotonic()-begin,
                'queries':len(trace),'ids':[r.queue_item_id for r in chosen],'lanes':priority})
            c.set_trace_callback(None)

        queries = []
        c.set_trace_callback(queries.append)
        start = time.monotonic()
        selected, lanes, period = select(c, now_utc=now, activated_at='2026-10-09T00:00:00+00:00')
        result['selection_benchmark'] = {'seconds':time.monotonic()-start,'queries':len(queries),
            'selected':[r.queue_item_id for r in selected],'lanes':lanes,'period':period}
        c.set_trace_callback(None)
    if snapshot(db) != before['files']:
        raise RuntimeError('Database changed; discard recovery census')
    return result


BEFORE_SOURCE='"""Three lanes over canonical queue identities; no second financial pipeline."""\nfrom datetime import datetime\nimport json\nimport re\nfrom scrapper.sources.exchange.reporting_periods import IST, priority_result_periods\n\n\ndef select(conn, *, now_utc, activated_at, historical_slot=False):\n    from scrapper.db.screener_discovery_repo import preview_due_companies\n    period=priority_result_periods(now=datetime.fromisoformat(now_utc))[0]\n    rows=preview_due_companies(conn,now_utc=now_utc,limit=100000)\n    lanes={1:[],2:[],3:[]}; identities=set()\n    for row in rows:\n        if row.reporting_scope!=\'standalone\':continue\n        if row.result_period!=\'1970-01\' and (not re.fullmatch(r\'\\d{4}-(0[1-9]|1[0-2])\',row.result_period) or row.result_period>period):continue\n        meta=conn.execute(\'SELECT created_at_utc,last_error_json FROM screener_company_queue WHERE queue_item_id=?\',(row.queue_item_id,)).fetchone()\n        discovered=conn.execute(\'\'\'SELECT max(f.discovered_at_utc) FROM exchange_screener_enqueues e\n            JOIN exchange_result_filings f USING(filing_id) WHERE e.queue_item_id=?\'\'\',(row.queue_item_id,)).fetchone()[0]\n        latest=discovered or meta[\'created_at_utc\']\n        reason=json.loads(meta[\'last_error_json\'] or \'{}\').get(\'reason\')\n        revision=unaccepted_refresh(conn,row.queue_item_id,row.exchange_filing_id)\n        lane=1 if row.result_period==period else 2 if revision else 2 if discovered is not None and latest>=activated_at else 3\n        alias=conn.execute(\'SELECT scope_path FROM screener_scope_aliases WHERE company_path=?\',(row.company_path_normalized,)).fetchone()\n        key=(alias[0] if alias else row.company_path_normalized,row.reporting_scope,row.result_period)\n        if key in identities:continue\n        identities.add(key)\n        lanes[lane].append((latest,row))\n    for lane in lanes:lanes[lane].sort(key=lambda pair:(pair[0],pair[1].queue_item_id))\n    selected=[];selected_lanes=[]\n    def take(lane):\n        if lanes[lane]:\n            selected.append(lanes[lane].pop(0)[1]);selected_lanes.append(lane);return True\n        return False\n    take(1) or take(2)\n    if historical_slot and lanes[3]:take(3)\n    else:take(2) or take(1)\n    # History cannot borrow live capacity, even when both live lanes are empty.\n    return tuple(selected),selected_lanes,period\n\n\ndef historical_slot(at):\n    local=at.astimezone(IST)\n    return local.minute==45 and local.hour in (0,6,12,18)\n\n\ndef refresh_revisions(conn, *, now_utc, activated_at, limit=2):\n    """Reconsider source-grounded revision holds using the SAME queue identity.\n\n    A filing broadcast newer than its accepted snapshot warrants retrieval, not\n    invented corrected numbers. Cross-exchange duplicates share one queue; old\n    broadcasts already captured by the accepted source do not trigger retrieval.\n    """\n    candidates=conn.execute(\'\'\'SELECT e.*,f.broadcast_at_utc,f.discovered_at_utc\n      FROM exchange_screener_enqueues e JOIN exchange_result_filings f USING(filing_id)\n      WHERE (e.decision_reason LIKE \'possible_same_period_revision:%\' OR e.decision IN (\'already_accepted\',\'stale\'))\n      AND f.discovered_at_utc>=? ORDER BY f.discovered_at_utc,f.filing_id\'\'\',(activated_at,)).fetchall()\n    refreshed=[]\n    with conn:\n        for filing in candidates:\n            if len(refreshed)>=limit:break\n            row=conn.execute(\'\'\'SELECT * FROM screener_company_queue WHERE company_path_normalized=?\n                AND reporting_scope=\'standalone\' AND result_period=?\'\'\',(filing[\'company_path_normalized\'],filing[\'result_period\'])).fetchone()\n            if row and (row[\'status\'] in (\'claimed\',\'running\') or row[\'collection_disposition\']):continue\n            state=conn.execute(\'\'\'SELECT max(a.accepted_at_utc) FROM accepted_page_versions a\n              JOIN screener_company_collection_state s ON s.company_id=a.company_id\n              WHERE s.company_path_normalized=? AND a.source=\'screener\' AND a.reporting_scope=\'standalone\' \'\'\',(filing[\'company_path_normalized\'],)).fetchone()[0]\n            if not state or filing[\'broadcast_at_utc\']<=state:continue\n            if row is None:\n                # The old quarter may exist only in a newer accepted source snapshot.\n                # Use the existing UNIQUE(path,scope,period) queue contract, not a\n                # separate revision inbox. Source enqueue decision stays immutable.\n                conn.execute(\'\'\'INSERT OR IGNORE INTO screener_company_queue\n                  (origin,exchange_filing_id,company_path_normalized,reporting_scope,result_period,\n                   queue_reason,status,attempt_count,next_attempt_at_utc,created_at_utc,updated_at_utc)\n                  VALUES(\'exchange_filing\',?,?,\'standalone\',?,\'result_period_advanced\',\'skipped_already_accepted\',0,?,?,?)\'\'\',\n                  (filing[\'filing_id\'],filing[\'company_path_normalized\'],filing[\'result_period\'],now_utc,now_utc,now_utc))\n                row=conn.execute("SELECT * FROM screener_company_queue WHERE company_path_normalized=? AND reporting_scope=\'standalone\' AND result_period=?",(filing[\'company_path_normalized\'],filing[\'result_period\'])).fetchone()\n            event=conn.execute("SELECT 1 FROM screener_company_queue_events WHERE queue_item_id=? AND json_extract(evidence_json,\'$.filing_id\')=? AND json_extract(evidence_json,\'$.reason\')=\'source_filing_refresh\'",(row[\'queue_item_id\'],filing[\'filing_id\'])).fetchone()\n            if event:continue\n            # Retry/backoff or pending refresh is already owned by the canonical queue.\n            if row[\'status\'] in (\'pending\',\'failed_retryable\') and unaccepted_refresh(conn,row[\'queue_item_id\'],row[\'exchange_filing_id\']):continue\n            next_status=row[\'status\'] if row[\'status\'] in (\'pending\',\'failed_retryable\') else \'pending\'\n            next_attempt=row[\'next_attempt_at_utc\'] if row[\'status\'] in (\'pending\',\'failed_retryable\') else now_utc\n            evidence=json.dumps({\'reason\':\'source_filing_refresh\',\'filing_id\':filing[\'filing_id\'],\n                \'previous_filing_id\':row[\'exchange_filing_id\'],\'previous_candidate_id\':row[\'candidate_id\'],\'previous_parse_id\':row[\'accepted_parse_id\'] or conn.execute("SELECT latest_accepted_parse_id FROM screener_company_collection_state WHERE company_path_normalized=? AND reporting_scope=\'standalone\'",(filing[\'company_path_normalized\'],)).fetchone()[0],\n                \'source_discovered_at_utc\':filing[\'discovered_at_utc\']},sort_keys=True)\n            conn.execute("UPDATE screener_company_queue SET status=?,origin=\'exchange_filing\',candidate_id=NULL,exchange_filing_id=?,next_attempt_at_utc=?,last_error_json=?,updated_at_utc=? WHERE queue_item_id=?",(next_status,filing[\'filing_id\'],next_attempt,evidence,now_utc,row[\'queue_item_id\']))\n            conn.execute("INSERT INTO screener_company_queue_events(queue_item_id,event_at_utc,from_status,to_status,evidence_json) VALUES(?,?,?,?,?)",(row[\'queue_item_id\'],now_utc,row[\'status\'],next_status,evidence))\n            # Keep the original enqueue decision immutable; event establishes lineage.\n            refreshed.append(row[\'queue_item_id\'])\n    return refreshed\n\n\ndef skip_accepted(conn,item,**options):\n    from scrapper.db.screener_discovery_repo import skip_if_already_accepted\n    row=conn.execute(\'SELECT last_error_json FROM screener_company_queue WHERE queue_item_id=?\',(item.queue_item_id,)).fetchone()\n    if unaccepted_refresh(conn,item.queue_item_id,item.exchange_filing_id):return False\n    return skip_if_already_accepted(conn,item,**options)\n\n\ndef unaccepted_refresh(conn,queue_id,filing_id):\n    if filing_id is None:return False\n    return conn.execute("""SELECT 1 FROM screener_company_queue_events refresh\n      WHERE refresh.queue_item_id=? AND json_extract(refresh.evidence_json,\'$.reason\')=\'source_filing_refresh\'\n      AND json_extract(refresh.evidence_json,\'$.filing_id\')=?\n      AND NOT EXISTS(SELECT 1 FROM screener_company_queue_events settled WHERE settled.queue_item_id=refresh.queue_item_id\n        AND settled.to_status IN (\'accepted\',\'skipped_already_accepted\') AND settled.queue_event_id>refresh.queue_event_id)\n      LIMIT 1""",(queue_id,filing_id)).fetchone() is not None\n\n\ndef verified_source_target(conn,logical_path):\n    """Route a retained queue alias through its existing verified source identity."""\n    from scrapper.db.collection_scope_repo import path_of\n    ids={r[0] for r in conn.execute(\'\'\'SELECT company_id FROM screener_company_collection_state\n      WHERE company_path_normalized=? AND reporting_scope=\'standalone\' AND company_id IS NOT NULL\n      UNION SELECT s.company_id FROM screener_scope_aliases a JOIN screener_collection_scope s\n        ON s.company_path=a.scope_path WHERE a.company_path=? AND s.company_id IS NOT NULL\'\'\',(logical_path,logical_path))}\n    if len(ids)>1:raise RuntimeError(\'Conflicting source identity for canonical queue alias\')\n    if not ids:return None\n    row=conn.execute("SELECT source_company_id,canonical_url FROM source_identifiers WHERE company_id=? AND source=\'screener\' AND mapping_status=\'verified\'",(next(iter(ids)),)).fetchone()\n    if row is None:return None\n    path=path_of(row[\'canonical_url\'])\n    if path is None:raise RuntimeError(\'Verified source identity has an invalid Screener path\')\n    return path,str(row[\'source_company_id\'])\n'

if __name__ == '__main__':
    print(json.dumps(inspect(),default=str))
