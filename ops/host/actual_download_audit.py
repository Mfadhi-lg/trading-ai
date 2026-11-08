"""Read-only, networkless evidence for the actual acquisition repair.

Run under the installed admission and owner locks, with /data read-only.
"""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

sys.path[:0] = ['/activation', '/app', '/host']


def main():
    from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot
    from production_ownership import assert_no_conflicting_leases
    db = Path('/data/scraper.db')
    guard = assert_safe_empty_wal(db)
    out = Path('/evidence')
    c = sqlite3.connect(f'file:{db}?mode=ro&immutable=1', uri=True)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    c.execute('PRAGMA temp_store=MEMORY')
    now = datetime.now(timezone.utc).isoformat()
    assert_no_conflicting_leases(c, now=now)
    def rows(sql, args=()):
        return [dict(r) for r in c.execute(sql, args)]
    accepted = """SELECT DISTINCT p.company_id,p.parse_id,p.reporting_scope,p.accepted_at_utc
        FROM accepted_page_versions p WHERE p.source='screener' AND EXISTS
        (SELECT 1 FROM financial_observations f WHERE f.parse_id=p.parse_id
         AND f.section_code='quarters' AND f.period_type='quarter' AND f.period_end='2026-09-30')"""
    evidence = {'observed_at_utc': now, 'guard': guard}
    queries = {
        'accepted_september': 'SELECT reporting_scope,count(DISTINCT company_id) companies,max(accepted_at_utc) latest FROM ('+accepted+') GROUP BY reporting_scope',
        'accepted_distinct': 'SELECT count(DISTINCT company_id) companies FROM ('+accepted+')',
        'generic_states': 'SELECT status,count(*) n FROM artifact_download_queue GROUP BY status',
        'rating_states': 'SELECT status,count(*) n FROM credit_rating_download_queue GROUP BY status',
        'rating_candidates': """SELECT q.*,a.company_id,a.period_end,a.artifact_type,co.current_legal_name
            FROM credit_rating_download_queue q JOIN document_artifacts a USING(artifact_id)
            JOIN companies co USING(company_id) WHERE q.created_at_utc>='2026-10-09T00:00:00+00:00'
            AND q.status IN ('PENDING','RETRYABLE_FAILURE')
            AND (q.next_attempt_at_utc IS NULL OR q.next_attempt_at_utc<=strftime('%Y-%m-%dT%H:%M:%S+00:00','now'))
            AND NOT EXISTS(SELECT 1 FROM artifact_contents c WHERE c.artifact_id=a.artifact_id)
            AND EXISTS(SELECT 1 FROM screener_collection_scope s WHERE s.company_id=a.company_id AND s.eligibility='eligible')
            AND NOT EXISTS(SELECT 1 FROM credit_rating_host_policy p WHERE p.automation_status='BLOCKED_TERMS'
              AND (q.target_host=p.host OR q.target_host LIKE '%.'||p.host))
            ORDER BY CASE WHEN a.period_end='2026-09-30' THEN 0 ELSE 1 END,q.priority,q.download_job_id LIMIT 10""",
        'host_policies': 'SELECT * FROM credit_rating_host_policy',
        'historic_success_hosts': "SELECT q.target_host,count(*) n FROM artifact_download_queue q WHERE q.status='SUCCEEDED' GROUP BY 1 ORDER BY 2 DESC LIMIT 30",
        'rating_success_hosts': "SELECT q.target_host,count(*) n FROM credit_rating_download_queue q WHERE q.status='SUCCEEDED' GROUP BY 1 ORDER BY 2 DESC",
        'recent_fetches': "SELECT f.* FROM fetch_observations f WHERE source='screener' AND resource_kind='company_page' ORDER BY fetch_id DESC LIMIT 15",
        'recent_acceptances': 'SELECT * FROM ('+accepted+') ORDER BY accepted_at_utc DESC LIMIT 15',
        'response_blob_schema': "SELECT sql FROM sqlite_master WHERE name='response_blobs'",
        'screen_candidates': 'SELECT result_period,reporting_scope_hint,count(*) n FROM screener_result_candidates GROUP BY 1,2',
        'company_origins': 'SELECT result_period,origin,status,count(*) n FROM screener_company_queue GROUP BY 1,2,3',
        'fresh_download_attempts': """SELECT t.*,q.download_job_id,q.status queue_status,a.company_id,
            co.current_legal_name,a.artifact_type,a.period_end FROM artifact_download_attempts t
            JOIN document_artifacts a USING(artifact_id) JOIN companies co USING(company_id)
            LEFT JOIN artifact_download_queue_attempts qa USING(download_attempt_id)
            LEFT JOIN artifact_download_queue q USING(download_job_id)
            WHERE t.started_at_utc>='2026-10-30T11:25:35+00:00' ORDER BY t.download_attempt_id""",
        'fresh_rating_resolutions': """SELECT r.*,a.company_id,a.period_end,co.current_legal_name,q.status queue_status
            FROM credit_rating_resolution_attempts r JOIN document_artifacts a USING(artifact_id)
            JOIN companies co USING(company_id) JOIN credit_rating_download_queue q USING(download_job_id)
            WHERE r.started_at_utc>='2026-10-30T11:25:35+00:00' ORDER BY r.resolution_attempt_id""",
        'fresh_rating_steps': """SELECT s.* FROM credit_rating_resolution_steps s JOIN credit_rating_resolution_attempts r USING(resolution_attempt_id)
            WHERE r.started_at_utc>='2026-10-30T11:25:35+00:00' ORDER BY s.resolution_attempt_id,s.step_position""",
        'generic_host_health': 'SELECT * FROM artifact_download_host_health WHERE blocked_until_utc IS NOT NULL',
        'shared_host_health': 'SELECT * FROM acquisition_host_state',
        'fresh_queue_jobs': """SELECT q.download_job_id,q.artifact_id,q.requested_url,q.created_at_utc,q.status,q.attempt_count,q.next_attempt_at_utc,
            q.last_error,a.company_id,a.period_end,a.artifact_type FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id)
            WHERE q.created_at_utc>='2026-10-30T11:25:35+00:00' ORDER BY q.download_job_id""",
        'fresh_processing': """SELECT c.content_id,c.artifact_id,c.extraction_status,c.content_origin,b.created_at_utc blob_created_at_utc,
            b.first_observed_at_utc blob_first_observed_at_utc,
            EXISTS(SELECT 1 FROM document_processing_inbox i WHERE i.content_id=c.content_id) processing_handoff
            FROM artifact_contents c JOIN response_blobs b USING(blob_id)
            WHERE c.created_at_utc>='2026-10-30T11:25:35+00:00' ORDER BY c.content_id""",
    }
    for key, sql in queries.items():
        evidence[key] = rows(sql)
    from document_backlog_audit import verify
    evidence['fresh_content_verification']=[]
    for r in rows('''SELECT c.*,b.local_path FROM artifact_contents c JOIN response_blobs b USING(blob_id)
        WHERE c.created_at_utc>='2026-10-30T11:25:35+00:00' ORDER BY c.content_id LIMIT 10'''):
        evidence['fresh_content_verification'].append(verify(r))
    from bs4 import BeautifulSoup
    from scrapper.sources.screener.document_links import _parse_documents
    saved=[]; current_links=[]
    pages=rows('SELECT * FROM ('+accepted+') ORDER BY accepted_at_utc DESC')
    seen_companies=set()
    for p in pages:
        if p['company_id'] in seen_companies:continue
        seen_companies.add(p['company_id'])
        b=c.execute('SELECT b.* FROM response_blobs b JOIN parse_artifacts p USING(blob_id) WHERE p.parse_id=?',(p['parse_id'],)).fetchone()
        path=Path(b['local_path']); body=path.read_bytes()
        import hashlib
        if hashlib.sha256(body).hexdigest()!=b['content_sha256']:raise RuntimeError('Saved page checksum mismatch')
        soup=BeautifulSoup(body,'html.parser')
        heading, fields, links=_parse_documents(soup)
        section=soup.select_one('section#documents')
        if len(saved)<8:(out/f'raw/documents-{p["company_id"]}-{p["parse_id"]}.html').write_text(str(section))
        from scrapper.documents.discovery.promotion import call_matches_result_window
        for l in links:
            if l.document_type not in ('concall_ppt','concall_transcript') or not l.source_url or not call_matches_result_window(l.period_label_raw,'2026-09-30'):continue
            found=c.execute('''SELECT a.artifact_id,a.period_end,
                EXISTS(SELECT 1 FROM artifact_contents ac WHERE ac.artifact_id=a.artifact_id) already_stored,
                (SELECT min(download_job_id) FROM artifact_download_queue q WHERE q.artifact_id=a.artifact_id AND q.requested_url=s.document_url) download_job_id
                FROM document_artifacts a JOIN artifact_sources s USING(artifact_id)
                WHERE a.company_id=? AND s.document_url=? ORDER BY a.artifact_id LIMIT 1''',(p['company_id'],l.source_url)).fetchone()
            current_links.append({'company_id':p['company_id'],'accepted_parse_id':p['parse_id'],
                'document_type':l.document_type,'source_url':l.source_url,'call_month':l.period_label_raw,
                'published_at_utc':None,'financial_quarter':'2026-09',
                'artifact_id':found['artifact_id'] if found else None,'period_end':found['period_end'] if found else None,
                'already_stored':found['already_stored'] if found else 0,
                'download_job_id':found['download_job_id'] if found else None})
        saved.append({**p,'blob_id':b['blob_id'],'sha256':b['content_sha256'],'byte_size':len(body),
            'parsed_types':{t:sum(x.document_type==t for x in links) for t in sorted({x.document_type for x in links})},
            'concalls_html_present':soup.select_one('div.concalls') is not None,
            'concall_link_elements':len(soup.select('.concall-link'))})
    evidence['saved_page_reparse']=saved
    audit = rows('''WITH accepted AS ('''+accepted+''')
        SELECT DISTINCT p.company_id,co.current_legal_name,p.parse_id,p.reporting_scope,p.accepted_at_utc,
        l.document_link_id,l.document_type,l.source_url,l.period_label_raw,l.source_published_at_utc,
        l.availability_status,pr.promotion_status,pr.promotion_reason,pr.artifact_id,
        a.period_end,a.first_observed_at_utc,q.download_job_id,q.created_at_utc job_created_at_utc,
        q.status job_status,q.target_host,q.next_attempt_at_utc,q.last_result_status,q.last_error,
        EXISTS(SELECT 1 FROM artifact_contents ac WHERE ac.artifact_id=a.artifact_id) already_stored,
        (SELECT max(http_status) FROM fetch_observations f WHERE f.company_id=p.company_id
         AND f.blob_id=(SELECT blob_id FROM parse_artifacts WHERE parse_id=p.parse_id)) fetch_http_status
        FROM accepted p JOIN companies co USING(company_id)
        JOIN document_links l USING(parse_id) LEFT JOIN document_link_promotions pr USING(document_link_id)
        LEFT JOIN document_artifacts a ON a.artifact_id=pr.artifact_id
        LEFT JOIN artifact_download_queue q ON q.artifact_id=a.artifact_id AND q.requested_url=pr.normalized_url
        WHERE l.document_type IN ('concall_ppt','concall_transcript','credit_rating')
        ORDER BY p.company_id,l.document_link_id,q.download_job_id''')
    from scrapper.core.host_policy import policy_for
    for r in audit:
        r['financial_quarter'] = '2026-09'
        r['exclusion_reason'] = ('unavailable_link' if not r['source_url'] else
            'publisher_terms_block' if policy_for(r['source_url']).blocked else
            'already_stored' if r['already_stored'] else 'not_promoted' if not r['artifact_id'] else
            'no_generic_job' if not r['download_job_id'] else
            'pre_epoch_zero_historical_allowance' if r['job_created_at_utc']<'2026-10-09T00:00:00+00:00' else r['job_status'])
    def csvfile(name, records):
        with (out/name).open('w') as f:
            w=csv.DictWriter(f, fieldnames=list(records[0]) if records else ['no_candidates'])
            w.writeheader();w.writerows(records)
    csvfile('SCREENER_DOCUMENT_LINK_AUDIT.csv', audit)
    csvfile('RATING_DOWNLOAD_CANDIDATES.csv',evidence['rating_candidates'])
    csvfile('CURRENT_QUARTER_LINK_CANDIDATES.csv',current_links)
    common = '''SELECT q.*,a.company_id,co.current_legal_name,a.artifact_type,a.period_end,a.published_at_utc,
       EXISTS(SELECT 1 FROM ('''+accepted+''') p WHERE p.company_id=a.company_id) september_company
       FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id)
       JOIN companies co USING(company_id)
       WHERE a.artifact_type IN ('presentation','transcript','credit_rating')
       AND q.status IN ('PENDING','RETRYABLE_FAILURE')
       AND (q.next_attempt_at_utc IS NULL OR q.next_attempt_at_utc<=?)
       AND NOT EXISTS(SELECT 1 FROM artifact_contents ac WHERE ac.artifact_id=a.artifact_id)
       AND EXISTS(SELECT 1 FROM screener_collection_scope sc WHERE sc.company_id=a.company_id AND sc.eligibility='eligible')
       AND NOT EXISTS(SELECT 1 FROM credit_rating_host_policy p WHERE p.automation_status='BLOCKED_TERMS'
          AND (q.target_host=p.host OR q.target_host LIKE '%.'||p.host))
       AND NOT EXISTS(SELECT 1 FROM artifact_download_host_health h WHERE h.host=q.target_host AND h.blocked_until_utc>?)
       AND q.target_host<>'api.bseindia.com' '''
    eligible=rows(common+' ORDER BY (a.period_end=\'2026-09-30\') DESC,september_company DESC,(q.status=\'PENDING\') DESC,q.created_at_utc DESC,q.download_job_id', (now,now))
    csvfile('ELIGIBLE_EXISTING_JOBS.csv',eligible)
    evidence['eligible_unique_artifacts']=len({r['artifact_id'] for r in eligible})
    evidence['eligible_post_epoch_jobs']=sum(r['created_at_utc']>='2026-10-09T00:00:00+00:00' for r in eligible)
    evidence['link_audit_rows']=len(audit)
    evidence['link_types']={t:sum(r['document_type']==t for r in audit) for t in ('concall_ppt','concall_transcript','credit_rating')}
    chosen=[];seen=set();hosts=set()
    # Diversity first; no duplicate artifact or URL, then fill remaining capacity.
    for diverse in (True,False):
        for r in eligible:
            if len(chosen)>=10:break
            if r['artifact_id'] in seen or r['requested_url'] in seen or (diverse and r['target_host'] in hosts):continue
            chosen.append(r);seen.update((r['artifact_id'],r['requested_url']));hosts.add(r['target_host'])
    csvfile('DOWNLOAD_CANDIDATES.csv',chosen)
    evidence['candidates']=chosen
    from acquisition_quarter_queries import monitor
    evidence['quarter_monitor']=monitor(c,'2026-09')
    c.close()
    if snapshot(db)!=guard['files']:raise RuntimeError('Database changed during audit')
    (out/'raw/live-audit.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({k:evidence[k] for k in ('observed_at_utc','accepted_september','accepted_distinct','eligible_unique_artifacts','eligible_post_epoch_jobs','link_types')}))


if __name__=='__main__':main()
