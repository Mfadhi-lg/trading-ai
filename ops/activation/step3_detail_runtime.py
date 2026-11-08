"""Read-only follow-up on current-quarter identity, eligibility and stale records."""
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
import sys
from pathlib import Path

sys.path[:0] = ['/activation', '/app']
from sqlite_sidecar_guard import assert_safe_empty_wal
from scrapper.sources.exchange.reporting_periods import priority_result_periods


def main():
    assert_safe_empty_wal(Path('/data/scraper.db'))
    period = priority_result_periods()[0]
    with closing(sqlite3.connect('file:/data/scraper.db?mode=ro', uri=True)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        def query(sql, args=()):
            return [dict(r) for r in c.execute(sql, args)]
        data = {'observed_at_utc':datetime.now(timezone.utc).isoformat(), 'current_period':period,
            'current_identity_scope':query('''SELECT q.queue_item_id,q.company_path_normalized,
                q.status,q.result_period,q.collection_disposition,a.scope_path,s.company_id,
                s.eligibility,s.market_cap_crore,s.observed_at_utc,s.next_check_at_utc,
                e.registry_company_id,r.company_id AS registry_company_id_bridge,
                r.canonical_name_raw,t.latest_accepted_result_period,t.latest_accepted_parse_id
                FROM screener_company_queue q LEFT JOIN screener_scope_aliases a
                ON a.company_path=q.company_path_normalized LEFT JOIN screener_collection_scope s
                ON s.company_path=a.scope_path LEFT JOIN exchange_screener_enqueues e
                ON e.filing_id=q.exchange_filing_id LEFT JOIN exchange_company_registry r
                ON r.registry_company_id=e.registry_company_id
                LEFT JOIN screener_company_collection_state t USING(company_path_normalized,reporting_scope)
                WHERE q.result_period=? ORDER BY q.queue_item_id''',(period,)),
            'accepted_current_quarter':query('''SELECT f.reporting_scope,count(DISTINCT a.company_id) AS companies
                FROM accepted_page_versions a JOIN financial_observations f USING(parse_id)
                WHERE a.source='screener' AND f.section_code='quarters' AND f.reporting_scope=a.reporting_scope
                AND substr(f.period_end,1,7)=? GROUP BY f.reporting_scope''',(period,)),
            'registry_bridge':query('SELECT company_id IS NULL AS missing_bridge,count(*) AS count FROM exchange_company_registry GROUP BY 1'),
            'unlinked_verified_bridge':query('''SELECT count(*) AS count FROM exchange_company_registry r
                WHERE r.company_id IS NULL AND EXISTS (SELECT 1 FROM exchange_securities s
                JOIN security_identifiers i ON i.identifier_type=s.exchange AND i.identifier_value=s.exchange_key
                WHERE s.current_isin=r.isin AND s.is_present=1 AND s.verification_status='verified' AND i.is_current=1)'''),
            'duplicate_detail':query('''SELECT q.queue_item_id,q.company_path_normalized,q.result_period,
                q.status,q.accepted_parse_id,q.collection_disposition FROM screener_company_queue q
                JOIN screener_scope_aliases a ON a.company_path=q.company_path_normalized
                WHERE a.scope_path='/company/INDOKEM/' ORDER BY q.result_period DESC,q.queue_item_id'''),
            'reentry_events':query("SELECT count(*) AS count,max(event_at_utc) AS latest FROM screener_company_queue_events WHERE json_extract(evidence_json,'$.reason')='market_cap_reentry'"),
            'untracked_below_floor_current':query('''SELECT count(*) AS count FROM screener_company_queue q
                JOIN screener_scope_aliases a ON a.company_path=q.company_path_normalized
                JOIN screener_collection_scope s ON s.company_path=a.scope_path
                WHERE q.result_period=? AND s.eligibility='below_floor' AND q.collection_disposition IS NULL''',(period,)),
            'recent_source_fetches':query('''SELECT company_id,resource_kind,max(fetched_at_utc) AS latest
                FROM fetch_observations WHERE company_id IN (1,3) AND resource_kind='company_page'
                GROUP BY company_id,resource_kind''')}
        print(json.dumps(data), flush=True)


if __name__ == '__main__':
    main()
