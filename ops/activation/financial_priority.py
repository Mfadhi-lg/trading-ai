"""Three lanes over canonical queue identities; no second financial pipeline."""
from datetime import datetime
import json
import re
from scrapper.db.repository_backend import is_postgresql
from scrapper.sources.exchange.reporting_periods import IST, priority_result_periods


def select(conn, *, now_utc, activated_at, historical_slot=False, limit=2):
    from scrapper.db.screener_discovery_repo import preview_due_companies
    if type(limit) is not int or not 1<=limit<=10:raise ValueError('Financial batch must contain 1–10 companies')
    period=priority_result_periods(now=datetime.fromisoformat(now_utc))[0]
    rows=preview_due_companies(conn,now_utc=now_utc,limit=100000)
    # Batch canonical metadata once; preserve preview ordering and alias fairness.
    metadata={}
    for offset in range(0,len(rows),400):
        ids=tuple(r.queue_item_id for r in rows[offset:offset+400])
        marks=','.join('?' for _ in ids)
        query=f"""WITH discovered AS (
            SELECT e.queue_item_id,max(f.discovered_at_utc) latest
            FROM exchange_screener_enqueues e JOIN exchange_result_filings f USING(filing_id)
            WHERE e.queue_item_id IN ({marks}) GROUP BY e.queue_item_id
        ), events AS (
            SELECT e.queue_item_id,
              max(CASE WHEN json_extract(e.evidence_json,'$.reason')='source_filing_refresh'
                AND json_extract(e.evidence_json,'$.filing_id')=q.exchange_filing_id
                THEN e.queue_event_id END) refreshed,
              max(CASE WHEN e.to_status IN ('accepted','skipped_already_accepted')
                THEN e.queue_event_id END) settled,
              max(CASE WHEN json_extract(e.evidence_json,'$.reason')='market_cap_reentry'
                THEN e.queue_event_id END) reentered
            FROM screener_company_queue_events e JOIN screener_company_queue q USING(queue_item_id)
            WHERE e.queue_item_id IN ({marks}) GROUP BY e.queue_item_id
        ) SELECT q.queue_item_id,q.created_at_utc,q.last_error_json,d.latest,a.scope_path,
            s.eligibility,v.reentered,v.settled,
            (q.exchange_filing_id IS NOT NULL AND v.refreshed IS NOT NULL
             AND (v.settled IS NULL OR v.settled<v.refreshed)) revision
          FROM screener_company_queue q LEFT JOIN discovered d USING(queue_item_id)
          LEFT JOIN events v USING(queue_item_id)
          LEFT JOIN screener_scope_aliases a ON a.company_path=q.company_path_normalized
          LEFT JOIN screener_collection_scope s ON s.company_path=a.scope_path
          WHERE q.queue_item_id IN ({marks})"""
        if is_postgresql(conn):
            query=query.replace("json_extract(e.evidence_json,'$.reason')","(e.evidence_json::jsonb ->> 'reason')")
            query=query.replace("json_extract(e.evidence_json,'$.filing_id')=q.exchange_filing_id",
                                "(e.evidence_json::jsonb ->> 'filing_id')=CAST(q.exchange_filing_id AS text)")
        metadata.update((r['queue_item_id'],r) for r in conn.execute(query,ids+ids+ids))
    lanes={1:[],2:[],3:[]}; identities=set()
    for row in rows:
        if row.reporting_scope!='standalone':continue
        if row.result_period!='1970-01' and (not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',row.result_period) or row.result_period>period):continue
        meta=metadata[row.queue_item_id]
        discovered=meta['latest']
        latest=discovered or meta['created_at_utc']
        revision=bool(meta['revision'])
        # Re-entry is new actionable work even when the queue identity predates
        # rollout. Require the canonical scope check to confirm eligibility;
        # preserve the existing disposition, due-time and claim fences above.
        reentry=(meta['eligibility']=='eligible' and (
            json.loads(meta['last_error_json'] or '{}').get('reason')=='market_cap_reentry' or
            meta['reentered'] is not None and (meta['settled'] is None or meta['settled']<meta['reentered'])))
        lane=1 if row.result_period==period else 2 if revision or reentry else 2 if discovered is not None and latest>=activated_at else 3
        key=(meta['scope_path'] or row.company_path_normalized,row.reporting_scope,row.result_period)
        if key in identities:continue
        identities.add(key)
        lanes[lane].append((latest,row))
    for lane in lanes:lanes[lane].sort(key=lambda pair:(pair[0],pair[1].queue_item_id))
    selected=[];selected_lanes=[]
    def take(lane):
        if lanes[lane]:
            selected.append(lanes[lane].pop(0)[1]);selected_lanes.append(lane);return True
        return False
    take(1) or take(2)
    if len(selected)<limit:
        if historical_slot and lanes[3]:take(3)
        else:take(2) or take(1)
    while len(selected)<limit and (lanes[1] or lanes[2]):
        take(1) or take(2)
    # History cannot borrow live capacity, even when both live lanes are empty.
    return tuple(selected),selected_lanes,period


def historical_slot(at):
    local=at.astimezone(IST)
    return local.minute==45 and local.hour in (0,6,12,18)


def refresh_revisions(conn, *, now_utc, activated_at, limit=2):
    """Reconsider source-grounded revision holds using the SAME queue identity.

    A filing broadcast newer than its accepted snapshot warrants retrieval, not
    invented corrected numbers. Cross-exchange duplicates share one queue; old
    broadcasts already captured by the accepted source do not trigger retrieval.
    """
    candidates=conn.execute('''SELECT e.*,f.broadcast_at_utc,f.discovered_at_utc
      FROM exchange_screener_enqueues e JOIN exchange_result_filings f USING(filing_id)
      WHERE (e.decision_reason LIKE 'possible_same_period_revision:%' OR e.decision IN ('already_accepted','stale'))
      AND f.discovered_at_utc>=? ORDER BY f.discovered_at_utc,f.filing_id''',(activated_at,)).fetchall()
    refreshed=[]
    with conn:
        for filing in candidates:
            if len(refreshed)>=limit:break
            row=conn.execute('''SELECT * FROM screener_company_queue WHERE company_path_normalized=?
                AND reporting_scope='standalone' AND result_period=?''',(filing['company_path_normalized'],filing['result_period'])).fetchone()
            if row and (row['status'] in ('claimed','running') or row['collection_disposition']):continue
            state=conn.execute('''SELECT max(a.accepted_at_utc) FROM accepted_page_versions a
              JOIN screener_company_collection_state s ON s.company_id=a.company_id
              WHERE s.company_path_normalized=? AND a.source='screener' AND a.reporting_scope='standalone' ''',(filing['company_path_normalized'],)).fetchone()[0]
            if not state or filing['broadcast_at_utc']<=state:continue
            if row is None:
                # The old quarter may exist only in a newer accepted source snapshot.
                # Use the existing UNIQUE(path,scope,period) queue contract, not a
                # separate revision inbox. Source enqueue decision stays immutable.
                insert='''INSERT OR IGNORE INTO screener_company_queue
                  (origin,exchange_filing_id,company_path_normalized,reporting_scope,result_period,
                   queue_reason,status,attempt_count,next_attempt_at_utc,created_at_utc,updated_at_utc)
                  VALUES('exchange_filing',?,?,'standalone',?,'result_period_advanced','skipped_already_accepted',0,?,?,?)'''
                if is_postgresql(conn):
                    insert=insert.replace('INSERT OR IGNORE INTO','INSERT INTO')+' ON CONFLICT DO NOTHING'
                conn.execute(insert,(filing['filing_id'],filing['company_path_normalized'],filing['result_period'],now_utc,now_utc,now_utc))
                row=conn.execute("SELECT * FROM screener_company_queue WHERE company_path_normalized=? AND reporting_scope='standalone' AND result_period=?",(filing['company_path_normalized'],filing['result_period'])).fetchone()
            if is_postgresql(conn):
                event_sql="SELECT 1 FROM screener_company_queue_events WHERE queue_item_id=? AND (evidence_json::jsonb ->> 'filing_id')=CAST(? AS text) AND (evidence_json::jsonb ->> 'reason')='source_filing_refresh'"
            else:
                event_sql="SELECT 1 FROM screener_company_queue_events WHERE queue_item_id=? AND json_extract(evidence_json,'$.filing_id')=? AND json_extract(evidence_json,'$.reason')='source_filing_refresh'"
            event=conn.execute(event_sql,(row['queue_item_id'],filing['filing_id'])).fetchone()
            if event:continue
            # Retry/backoff or pending refresh is already owned by the canonical queue.
            if row['status'] in ('pending','failed_retryable') and unaccepted_refresh(conn,row['queue_item_id'],row['exchange_filing_id']):continue
            next_status=row['status'] if row['status'] in ('pending','failed_retryable') else 'pending'
            next_attempt=row['next_attempt_at_utc'] if row['status'] in ('pending','failed_retryable') else now_utc
            evidence=json.dumps({'reason':'source_filing_refresh','filing_id':filing['filing_id'],
                'previous_filing_id':row['exchange_filing_id'],'previous_candidate_id':row['candidate_id'],'previous_parse_id':row['accepted_parse_id'] or conn.execute("SELECT latest_accepted_parse_id FROM screener_company_collection_state WHERE company_path_normalized=? AND reporting_scope='standalone'",(filing['company_path_normalized'],)).fetchone()[0],
                'source_discovered_at_utc':filing['discovered_at_utc']},sort_keys=True)
            conn.execute("UPDATE screener_company_queue SET status=?,origin='exchange_filing',candidate_id=NULL,exchange_filing_id=?,next_attempt_at_utc=?,last_error_json=?,updated_at_utc=? WHERE queue_item_id=?",(next_status,filing['filing_id'],next_attempt,evidence,now_utc,row['queue_item_id']))
            conn.execute("INSERT INTO screener_company_queue_events(queue_item_id,event_at_utc,from_status,to_status,evidence_json) VALUES(?,?,?,?,?)",(row['queue_item_id'],now_utc,row['status'],next_status,evidence))
            # Keep the original enqueue decision immutable; event establishes lineage.
            refreshed.append(row['queue_item_id'])
    return refreshed


def skip_accepted(conn,item,**options):
    from scrapper.db.screener_discovery_repo import skip_if_already_accepted
    row=conn.execute('SELECT last_error_json FROM screener_company_queue WHERE queue_item_id=?',(item.queue_item_id,)).fetchone()
    if unaccepted_refresh(conn,item.queue_item_id,item.exchange_filing_id):return False
    return skip_if_already_accepted(conn,item,**options)


def unaccepted_refresh(conn,queue_id,filing_id):
    if filing_id is None:return False
    query="""SELECT 1 FROM screener_company_queue_events refresh
      WHERE refresh.queue_item_id=? AND json_extract(refresh.evidence_json,'$.reason')='source_filing_refresh'
      AND json_extract(refresh.evidence_json,'$.filing_id')=?
      AND NOT EXISTS(SELECT 1 FROM screener_company_queue_events settled WHERE settled.queue_item_id=refresh.queue_item_id
        AND settled.to_status IN ('accepted','skipped_already_accepted') AND settled.queue_event_id>refresh.queue_event_id)
      LIMIT 1"""
    if is_postgresql(conn):
        query=query.replace("json_extract(refresh.evidence_json,'$.reason')","(refresh.evidence_json::jsonb ->> 'reason')")
        query=query.replace("json_extract(refresh.evidence_json,'$.filing_id')=?","(refresh.evidence_json::jsonb ->> 'filing_id')=CAST(? AS text)")
    return conn.execute(query,(queue_id,filing_id)).fetchone() is not None


def verified_source_target(conn,logical_path):
    """Route a retained queue alias through its existing verified source identity."""
    from scrapper.db.collection_scope_repo import path_of
    ids={r[0] for r in conn.execute('''SELECT company_id FROM screener_company_collection_state
      WHERE company_path_normalized=? AND reporting_scope='standalone' AND company_id IS NOT NULL
      UNION SELECT s.company_id FROM screener_scope_aliases a JOIN screener_collection_scope s
        ON s.company_path=a.scope_path WHERE a.company_path=? AND s.company_id IS NOT NULL''',(logical_path,logical_path))}
    if len(ids)>1:raise RuntimeError('Conflicting source identity for canonical queue alias')
    if not ids:return None
    row=conn.execute("SELECT source_company_id,canonical_url FROM source_identifiers WHERE company_id=? AND source='screener' AND mapping_status='verified'",(next(iter(ids)),)).fetchone()
    if row is None:return None
    path=path_of(row['canonical_url'])
    if path is None:raise RuntimeError('Verified source identity has an invalid Screener path')
    return path,str(row['source_company_id'])
