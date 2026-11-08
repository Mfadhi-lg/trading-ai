"""Read-only financial acquisition inspection; no requests, claims or migrations."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import sqlite3
import sys

sys.path[:0] = ['/activation', '/app']
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot
from scrapper.db.database import check_schema_compatibility
from scrapper.db.screener_discovery_repo import preview_due_companies
from scrapper.sources.exchange.reporting_periods import priority_result_periods


def main():
    db = Path('/data/scraper.db')
    sidecars = assert_safe_empty_wal(db)
    now = datetime.now(timezone.utc).isoformat()
    period = priority_result_periods()[0]
    with closing(sqlite3.connect('file:/data/scraper.db?mode=ro', uri=True)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        check_schema_compatibility(c)
        def query(sql, params=()):
            return [dict(r) for r in c.execute(sql, params)]
        tables = ('companies','source_identifiers','exchange_company_registry','exchange_result_filings',
            'exchange_screener_enqueues','screener_company_queue','screener_company_collection_state',
            'screener_collection_scope','screener_scope_aliases','accepted_page_versions',
            'company_metric_observations','acquisition_host_state','collection_scope_tasks')
        data = {'observed_at_utc':now, 'current_period':period, 'mode':'read_only', 'source_requests':0,
            'sidecar_guard':sidecars,
            'columns':{t:query('PRAGMA table_info("'+t+'")') for t in tables},
            'queue_counts':query('SELECT result_period,reporting_scope,status,collection_disposition,count(*) AS count FROM screener_company_queue GROUP BY 1,2,3,4 ORDER BY result_period DESC'),
            'scope_counts':query('SELECT eligibility,count(*) AS count,min(observed_at_utc) AS oldest_observation,max(observed_at_utc) AS newest_observation,min(next_check_at_utc) AS earliest_check FROM screener_collection_scope GROUP BY eligibility'),
            'scope_due':query('SELECT eligibility,count(*) AS count FROM screener_collection_scope WHERE next_check_at_utc<=? GROUP BY eligibility',(now,)),
            'scope_alias_counts':query('SELECT identity_basis,count(*) AS count FROM screener_scope_aliases GROUP BY identity_basis'),
            'scope_reentry_observations':query("SELECT eligibility,count(*) AS count,max(observed_at_utc) AS latest FROM screener_scope_observations GROUP BY eligibility"),
            'scope_missing_for_accepted':query("SELECT count(DISTINCT a.company_id) AS count FROM accepted_page_versions a WHERE source='screener' AND reporting_scope='standalone' AND NOT EXISTS (SELECT 1 FROM screener_collection_scope s WHERE s.company_id=a.company_id)"),
            'queue_error_groups':query("SELECT status,json_extract(last_error_json,'$.error.type') AS error_type,json_extract(last_error_json,'$.error.message') AS error_message,count(*) AS count FROM screener_company_queue WHERE status LIKE 'failed_%' GROUP BY 1,2,3 ORDER BY count DESC LIMIT 20"),
            'due_preview':[r.to_dict() for r in preview_due_companies(c, now_utc=now, limit=10)],
            'current_queues':query('SELECT q.*,s.company_id,s.latest_discovered_result_period,s.latest_accepted_result_period,s.latest_accepted_parse_id FROM screener_company_queue q LEFT JOIN screener_company_collection_state s USING(company_path_normalized,reporting_scope) WHERE q.result_period=? ORDER BY queue_item_id DESC LIMIT 40',(period,)),
            'filing_counts':query('SELECT exchange,result_period,reporting_scope,count(*) AS count,max(broadcast_at_utc) AS latest_broadcast FROM exchange_result_filings GROUP BY 1,2,3 ORDER BY result_period DESC'),
            'current_filing_decisions':query('SELECT f.exchange,f.reporting_scope,e.decision,e.decision_reason,count(*) AS count FROM exchange_result_filings f LEFT JOIN exchange_screener_enqueues e USING(filing_id) WHERE f.result_period=? GROUP BY 1,2,3,4',(period,)),
            'current_filing_examples':query('SELECT f.filing_id,f.exchange,f.exchange_key,f.company_name_raw,f.result_period,f.reporting_scope,f.broadcast_at_utc,s.fetch_id,s.blob_id,e.* FROM exchange_result_filings f JOIN exchange_source_snapshots s USING(snapshot_id) LEFT JOIN exchange_screener_enqueues e USING(filing_id) WHERE f.result_period=? ORDER BY f.filing_id DESC LIMIT 30',(period,)),
            'state_periods':query('SELECT reporting_scope,latest_discovered_result_period,latest_accepted_result_period,count(*) AS count FROM screener_company_collection_state GROUP BY 1,2,3 ORDER BY 2 DESC'),
            'acceptance_counts':query('SELECT source,reporting_scope,count(*) AS count,max(accepted_at_utc) AS latest FROM accepted_page_versions GROUP BY 1,2'),
            'holds':query('SELECT * FROM exchange_source_access_events ORDER BY event_id'),
            'host_state':query('SELECT * FROM acquisition_host_state'),
            'collection_tasks':query('SELECT state,count(*) AS count FROM collection_scope_tasks GROUP BY state'),
            'identity_duplicates':query('SELECT coalesce(a.scope_path,q.company_path_normalized) AS canonical_path,q.reporting_scope,q.result_period,count(*) AS count FROM screener_company_queue q LEFT JOIN screener_scope_aliases a ON a.company_path=q.company_path_normalized WHERE q.collection_disposition IS NULL GROUP BY 1,2,3 HAVING count(*)>1 ORDER BY count DESC LIMIT 20')}
        print(json.dumps(data, default=str), flush=True)
    if snapshot(db)['main'] != sidecars['files']['main']:
        raise RuntimeError('Production main DB changed during the inspection')


if __name__ == '__main__':
    def deadline(*_):
        raise RuntimeError('Bounded read-only inspection deadline')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(240)
    main()
