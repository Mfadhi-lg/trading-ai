"""Bounded adapters over existing queues; no new crawler or historical campaign."""
from ...db.database import connect, check_schema_compatibility
from ...db.repo import utc_now
from ...documents.priority import bounded_lanes, current_period, download_lanes
from ...documents.availability import parse_utc
from ...pipeline.run_document_download_worker import run_document_download_worker
from ..coordinator.exchange import ownership
from ...documents.rating_browser import browser_host_sql


def select_downloads(conn, *, now, epoch, limit=2, historical_limit=0, recovery_job_ids=(), historical_fallback=False):
    # SQL bounds each lane before Python fairness selection. Existing claimant
    # still decides policy, retries, health, content dedup and fenced ownership.
    parse_utc(epoch); parse_utc(now)
    bounded_lanes([],limit=limit,historical_limit=historical_limit,epoch=epoch,now=now)
    common = f'''SELECT q.download_job_id,q.created_at_utc first_observed_at_utc,a.period_end,
        row_number() OVER (PARTITION BY q.target_host
            ORDER BY q.created_at_utc,q.download_job_id) AS host_sequence
        FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id)
        WHERE a.artifact_type IN ('presentation','transcript','credit_rating')
          AND q.target_host<>'api.bseindia.com'
          AND NOT (a.artifact_type='credit_rating' AND {browser_host_sql('q.target_host')})
          AND (q.status IN ('PENDING','RETRYABLE_FAILURE') OR
               (q.status='LEASED' AND q.lease_expires_at_utc<=?))
          AND (q.next_attempt_at_utc IS NULL OR q.next_attempt_at_utc<=?)
          AND (NOT EXISTS (SELECT 1 FROM artifact_contents c WHERE c.artifact_id=a.artifact_id)
            OR EXISTS (SELECT 1 FROM artifact_download_scopes rs WHERE rs.scope_id=q.scope_id
                AND json_extract(rs.policy_json,'$.revision_artifact_id')=q.artifact_id
                AND json_extract(rs.policy_json,'$.revision_url')=q.requested_url
                AND json_extract(rs.policy_json,'$.revision_indicator') IS NOT NULL))
          AND NOT EXISTS (SELECT 1 FROM artifact_download_host_health h
              WHERE h.host=q.target_host AND h.blocked_until_utc>?)
          AND NOT EXISTS (SELECT 1 FROM credit_rating_host_policy p
              WHERE p.automation_status='BLOCKED_TERMS' AND
                (q.target_host=p.host OR q.target_host LIKE '%.'||p.host))
          AND EXISTS (SELECT 1 FROM screener_collection_scope s
              WHERE s.company_id=a.company_id AND s.eligibility='eligible')'''
    if recovery_job_ids:
        # Only an externally authorized finite campaign supplies these exact
        # canonical identities. Never rewrite timestamps or widen routine lanes.
        ids=tuple(recovery_job_ids)
        if len(ids)>10 or len(set(ids))!=len(ids) or any(type(i) is not int or i<1 for i in ids):
            raise ValueError('Recovery requires 1–10 distinct canonical job IDs')
        marks=','.join('?' for _ in ids)
        rows=[dict(r) for r in conn.execute(common+f' AND q.download_job_id IN ({marks})'+
            ' ORDER BY CASE WHEN a.period_end=? THEN 0 ELSE 1 END,q.priority,q.download_job_id LIMIT ?',
            (now,now,now,*ids,current_period(now),limit))]
        return [(r,1 if r['period_end']==current_period(now) else 3) for r in rows]
    rows = []
    lanes = ((
        ('a.period_end=?',(current_period(now),),limit),
        ('q.created_at_utc>=? AND (a.period_end IS NULL OR a.period_end<>?)',(epoch,current_period(now)),limit),
        ('q.created_at_utc<? AND (a.period_end IS NULL OR a.period_end<>?)',(epoch,current_period(now)),limit),
    ) if historical_fallback else (
        ('q.created_at_utc>=? AND a.period_end=?',(epoch,current_period(now)),limit),
        ('q.created_at_utc>=? AND (a.period_end IS NULL OR a.period_end<>?)',(epoch,current_period(now)),limit),
        ('q.created_at_utc<?',(epoch,),historical_limit),
    ))
    for predicate,args,budget in lanes:
        if budget:
            rows.extend(dict(r) for r in conn.execute(common+' AND '+predicate+
                ' ORDER BY host_sequence,q.created_at_utc,q.download_job_id LIMIT ?',
                (now,now,now,*args,budget)))
    if historical_fallback:
        return download_lanes(rows,limit=limit,epoch=epoch,now=now)
    return bounded_lanes(rows,limit=limit,historical_limit=historical_limit,epoch=epoch,now=now)


def run_automatic_downloads(*, db_path, epoch, limit=2, historical_limit=0, recovery_job_ids=(), saved_parse_ids=(),
                            now_utc=utc_now, connection_factory=connect, historical_fallback=False, **worker_options):
    conn=connection_factory(db_path)
    try:
        check_schema_compatibility(conn)
        with ownership(db_path,'automatic-document-download') as acquired:
            if not acquired: return {'status':'busy','selected':[]}
            repaired=[]
            if not recovery_job_ids:
                # Up to eight saved accepted current-quarter pages per slot. This is a
                # parser repair, with original blob/fetch times and no HTTP.
                from ...workflows.documents.saved_link_refresh import refresh_saved_links
                period=current_period(now_utc())
                if len(saved_parse_ids)>8 or any(type(i) is not int or i<1 for i in saved_parse_ids):
                    raise ValueError('Saved link repair requires at most eight exact parse IDs')
                exact=(' AND p.parse_id IN ('+','.join('?' for _ in saved_parse_ids)+')') if saved_parse_ids else ''
                repair_rows=conn.execute('''SELECT p.company_id,p.parse_id FROM accepted_page_versions p
                    JOIN parse_artifacts pa USING(parse_id)
                    WHERE p.source='screener' AND p.reporting_scope='standalone'
                    AND EXISTS(SELECT 1 FROM screener_collection_scope sc WHERE sc.company_id=p.company_id AND sc.eligibility='eligible')
                    AND EXISTS(SELECT 1 FROM financial_observations f WHERE f.parse_id=p.parse_id AND f.section_code='quarters' AND f.period_end=?)
                    AND NOT EXISTS(SELECT 1 FROM parse_artifacts x WHERE x.blob_id=pa.blob_id
                      AND x.parser_name=? AND x.parser_version='1.0.1' AND x.parse_status='ok')
                    '''+exact+''' ORDER BY p.accepted_at_utc DESC LIMIT 8''',
                    (period,'saved-document-links-'+period,*saved_parse_ids)).fetchall()
                for row in repair_rows:
                    repaired.append(refresh_saved_links(conn,company_id=row['company_id'],accepted_parse_id=row['parse_id'],period_end=period))
            from ...pipeline.accepted_document_downloads import enqueue_discovered_documents
            enqueued=[] if recovery_job_ids else enqueue_discovered_documents(conn,epoch=epoch,now=now_utc(),
                limit=limit,historical_limit=historical_limit,historical_fallback=historical_fallback)
            selected=select_downloads(conn,now=now_utc(),epoch=epoch,
                limit=limit,historical_limit=historical_limit,recovery_job_ids=recovery_job_ids,historical_fallback=historical_fallback)
            if not selected: return {'status':'idle','selected':[],'enqueued':enqueued,'saved_link_repairs':repaired}
            result=run_document_download_worker(conn,limit=len(selected),
                artifact_types=('presentation','transcript','credit_rating'),
                allowed_job_ids=tuple(r['download_job_id'] for r,_ in selected),
                shared_permits=True,now=now_utc,**worker_options)
            return {'enqueued':enqueued,'saved_link_repairs':repaired,'selected':[{'download_job_id':r['download_job_id'],'priority':lane}
                for r,lane in selected],'download':result}
    finally: conn.close()


def run_automatic_discovery(*, db_path, epoch, limit=2, historical_limit=0,
                            output=None, now_utc=utc_now, official_request_limit=2, screener_request_limit=2,
                            snapshot_root=None, connection_factory=connect):
    """Use the canonical bounded follow-up refresher, same policies and leases."""
    from ...db.documents.due import _DUE_SELECT, preview_due_expectations, claim_due_expectations
    from ...workflows.documents.reconciliation import reconcile_artifacts
    if type(screener_request_limit) is not int or not 1 <= screener_request_limit <= 100:
        raise ValueError('Screener request budget must be 1–100')
    if type(official_request_limit) is not int or not 0 <= official_request_limit <= 2:
        raise ValueError('Official page budget must be 0–2')
    conn=connection_factory(db_path)
    try:
        check_schema_compatibility(conn)
        with ownership(db_path,'periodic-executor') as acquired:
            if not acquired: return {'status':'busy','selected':[]}
            now=now_utc()
            from ...documents.late_publications import extend_exhausted_waits
            extended=extend_exhausted_waits(conn,now=now,limit=1)
            bounded_lanes([],limit=limit,historical_limit=historical_limit,epoch=epoch,now=now)
            common='''SELECT expectation.expectation_id,expectation.company_id,expectation.event_id,CASE WHEN json_extract(expectation.schedule_json,'$.long_tail_v1')=1 THEN expectation.updated_at_utc ELSE expectation.created_at_utc END first_observed_at_utc,event.period_end
                FROM artifact_expectations expectation JOIN artifact_events event USING(event_id)
                WHERE expectation.artifact_type IN ('presentation','transcript')
                  AND expectation.next_check_at_utc<=?
                  AND (expectation.claim_token IS NULL OR expectation.claim_expires_at_utc<=?)
                  AND EXISTS (SELECT 1 FROM screener_collection_scope s
                      WHERE s.company_id=expectation.company_id AND s.eligibility='eligible')'''
            rows=[]
            for predicate,args,budget in (
                ("(expectation.created_at_utc>=? OR json_extract(expectation.schedule_json,'$.long_tail_v1')=1) AND event.period_end=?",(epoch,current_period(now)),limit),
                ("(expectation.created_at_utc>=? OR json_extract(expectation.schedule_json,'$.long_tail_v1')=1) AND (event.period_end IS NULL OR event.period_end<>?)",(epoch,current_period(now)),limit),
                ('expectation.created_at_utc<?',(epoch,),historical_limit),
            ):
                if budget:
                    rows.extend(dict(r) for r in conn.execute(common+' AND '+predicate+
                        ' GROUP BY expectation.company_id,expectation.event_id ORDER BY expectation.expected_after_utc DESC,expectation.next_check_at_utc,expectation.expectation_id LIMIT ?',
                        (now,now,*args,budget)))
            selected=bounded_lanes(rows,limit=limit,historical_limit=historical_limit,epoch=epoch,now=now)
            # A slot is a company/event page retrieval, with both due document
            # expectations carried together. Never claim a future or leased peer.
            groups = {}
            for row, lane in selected:
                groups.setdefault((row['company_id'], row['event_id']), (row, lane))
            groups = list(groups.values())[:screener_request_limit]
            selected = []
            for row, lane in groups:
                fresh = " AND (expectation.created_at_utc>=? OR json_extract(expectation.schedule_json,'$.long_tail_v1')=1)" if lane != 3 else ''
                peers = conn.execute(common + ' AND expectation.company_id=? AND expectation.event_id=?'
                    + fresh + ' ORDER BY expectation.expectation_id',
                    (now, now, row['company_id'], row['event_id'], *((epoch,) if fresh else ()))).fetchall()
                selected.extend((dict(peer), lane) for peer in peers)
            due_count = conn.execute('SELECT count(*) FROM (' + common + ')', (now, now)).fetchone()[0]
            due_groups = conn.execute('SELECT count(*) FROM (' + common +
                ' GROUP BY expectation.company_id,expectation.event_id)', (now, now)).fetchone()[0]
            capacity = {'due_expectations': due_count, 'due_company_events': due_groups, 'selected_expectations': len(selected),
                'selected_company_events': len(groups), 'screener_request_limit': screener_request_limit,
                'remaining_due_expectations': due_count - len(selected)}
            if not selected: return {'status':'idle','selected':[],'long_tail_extended':extended,'capacity':capacity}
            ids=tuple(r['expectation_id'] for r,_ in selected)
            from ...sources.documents.routine import refresh_official_pages
            from ...operations.workers.host_requests import HostSession
            from ...documents.downloader import DownloadPolicy
            from ...config import SNAPSHOTS
            companies=[r[0] for r in conn.execute('SELECT DISTINCT company_id FROM artifact_expectations WHERE expectation_id IN ('+','.join('?' for _ in ids)+')',ids)]
            with HostSession(conn,mode='generic',owner='automatic-official-discovery',
                    policy=DownloadPolicy(max_bytes=2*1024*1024,total_timeout_seconds=45,max_redirects=0),
                    check=lambda:None,wait_seconds=0) as session:
                official=refresh_official_pages(conn,company_ids=companies,now=now,
                    snapshot_root=snapshot_root or SNAPSHOTS,session=session,request_limit=official_request_limit)
            # IDs come only from SQLite integers; values cannot introduce SQL.
            selection=_DUE_SELECT.replace('WHERE expectation.next_check_at_utc',
                'WHERE expectation.expectation_id IN ('+','.join(str(int(x)) for x in ids)+') AND expectation.next_check_at_utc')
            result=reconcile_artifacts(due=True,limit=len(ids),db_path=db_path,
                now_utc=now_utc,output=output,shared_permits=True,
                shared_client_factory=lambda *a, **kw: BoundedDocumentScreenerClient(
                    *a, request_limit=screener_request_limit, **kw),init_db=check_schema_compatibility,
                connect=connection_factory,document_connection_factory=connection_factory,
                preview_due_expectations=lambda c,**kw:preview_due_expectations(c,_DUE_SELECT=selection,**kw),
                claim_due_expectations=lambda c,**kw:claim_due_expectations(c,_DUE_SELECT=selection,**kw))
            return {'selected':[{'expectation_id':r['expectation_id'],'priority':lane} for r,lane in selected],
                    'discovery':result,'official':official,'long_tail_extended':extended,'capacity':capacity}
    finally: conn.close()


def handoff_browser_ratings(conn, *, epoch, now, limit=2):
    """Move bounded generic rating discoveries to the recurring rating owner."""
    from ...pipeline.credit_rating_downloads import queue_credit_rating_downloads
    rows = conn.execute(f'''SELECT q.artifact_id,min(q.created_at_utc) created_at_utc
        FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id)
        WHERE a.artifact_type='credit_rating' AND {browser_host_sql('q.target_host')}
          AND q.created_at_utc>=? AND q.status IN ('PENDING','RETRYABLE_FAILURE')
          AND NOT EXISTS(SELECT 1 FROM artifact_contents c WHERE c.artifact_id=q.artifact_id)
          AND NOT EXISTS(SELECT 1 FROM artifact_download_queue active
            WHERE active.artifact_id=q.artifact_id AND active.status='LEASED'
              AND active.lease_expires_at_utc>?)
          AND NOT EXISTS(SELECT 1 FROM credit_rating_download_queue r
            WHERE r.artifact_id=q.artifact_id AND r.requested_url=q.requested_url)
          AND EXISTS(SELECT 1 FROM screener_collection_scope sc
            WHERE sc.company_id=a.company_id AND sc.eligibility='eligible')
        GROUP BY q.artifact_id ORDER BY min(q.created_at_utc),q.artifact_id LIMIT ?''',
        (epoch,now,limit)).fetchall()
    for row in rows:
        queue_credit_rating_downloads(conn,artifact_ids=(row['artifact_id'],),
            created_at_utc=row['created_at_utc'],include_queue_counts=False)
    return [r['artifact_id'] for r in rows]


def run_automatic_ratings(*, db_path, epoch, now_utc=utc_now, connection_factory=connect, **worker_options):
    """One eligible rating per recurring slot, with CRISIL/ICRA browser routing."""
    from ...pipeline.credit_rating_downloads import run_credit_rating_download_worker
    conn=connection_factory(db_path)
    try:
        check_schema_compatibility(conn)
        with ownership(db_path,'rating-worker') as acquired:
            if not acquired:return {'status':'busy'}
            now=now_utc();parse_utc(epoch)
            handoff_browser_ratings(conn,epoch=epoch,now=now)
            row=conn.execute('''SELECT q.download_job_id FROM credit_rating_download_queue q
                JOIN document_artifacts a USING(artifact_id)
                WHERE q.created_at_utc>=? AND q.status IN ('PENDING','RETRYABLE_FAILURE','LEASED')
                  AND (q.status<>'LEASED' OR q.lease_expires_at_utc<=?)
                  AND (q.next_attempt_at_utc IS NULL OR q.next_attempt_at_utc<=?)
                  AND NOT EXISTS(SELECT 1 FROM artifact_contents c WHERE c.artifact_id=a.artifact_id)
                  AND NOT EXISTS(SELECT 1 FROM credit_rating_host_health h
                    WHERE h.host=q.target_host AND h.blocked_until_utc>?)
                  AND EXISTS(SELECT 1 FROM screener_collection_scope s WHERE s.company_id=a.company_id AND s.eligibility='eligible')
                  AND q.target_host<>'api.bseindia.com'
                  AND NOT EXISTS(SELECT 1 FROM credit_rating_host_policy p WHERE p.automation_status='BLOCKED_TERMS'
                    AND (q.target_host=p.host OR q.target_host LIKE '%.'||p.host))
                ORDER BY CASE WHEN a.period_end=? THEN 0 ELSE 1 END,q.priority,q.download_job_id LIMIT 1''',
                (epoch,now,now,now,current_period(now))).fetchone()
            if not row:return {'status':'idle'}
            return run_credit_rating_download_worker(conn,limit=1,allowed_job_ids=(row[0],),shared_permits=True,**worker_options)
    finally:conn.close()


from .screener_requests import SharedScreenerClient
class BoundedDocumentScreenerClient(SharedScreenerClient):
    """Explicit company-page request budget; redirects cannot expand it."""
    def __init__(self,*args,request_limit=2,**kwargs):
        if type(request_limit) is not int or not 1 <= request_limit <= 100:
            raise ValueError('Screener request budget must be 1–100')
        self.request_limit = request_limit
        from ...documents.downloader import DownloadPolicy
        kwargs['policy']=DownloadPolicy(max_bytes=2*1024*1024,total_timeout_seconds=45,max_redirects=0)
        kwargs['wait_seconds']=10
        super().__init__(*args,**kwargs)
        self.document_requests=0
    def _get(self,url,**kwargs):
        if self.document_requests>=self.request_limit:raise RuntimeError('Document discovery request budget exhausted')
        self.document_requests+=1
        return super()._get(url,**kwargs)
