"""Read-only financial selection diagnosis under the existing host owner locks."""
import json
import fcntl
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path[:0] = ['/activation', '/app']
from sqlite_sidecar_guard import _assert_safe_empty_wal, snapshot
from scrapper.sources.exchange.reporting_periods import priority_result_periods


def inspect(conn, now):
    period = priority_result_periods(now=datetime.fromisoformat(now))[0]
    discovered = dict(conn.execute('''SELECT e.queue_item_id,max(f.discovered_at_utc)
        FROM exchange_screener_enqueues e JOIN exchange_result_filings f USING(filing_id)
        WHERE e.queue_item_id IS NOT NULL GROUP BY e.queue_item_id'''))
    rows = [dict(r) for r in conn.execute('''SELECT q.*,
      coalesce((SELECT a.scope_path FROM screener_scope_aliases a
                WHERE a.company_path=q.company_path_normalized),q.company_path_normalized) canonical_path,
      (SELECT s.eligibility FROM screener_scope_aliases a
       JOIN screener_collection_scope s ON s.company_path=a.scope_path
       WHERE a.company_path=q.company_path_normalized LIMIT 1) eligibility
      FROM screener_company_queue q ORDER BY q.queue_item_id''')]
    for r in rows:
        r['discovered'] = discovered.get(r['queue_item_id'])
    def reason(r):
        if r['status'] not in ('pending', 'failed_retryable', 'claimed', 'running'):
            return r['status']
        if r['collection_disposition']:
            return 'disposition:' + r['collection_disposition']
        if r['eligibility'] == 'below_floor':
            return 'below_market_cap'
        if r['status'] in ('claimed', 'running'):
            if not r['claim_expires_at_utc'] or r['claim_expires_at_utc'] > now:
                return 'active_or_unknown_lease'
        elif r['next_attempt_at_utc'] and r['next_attempt_at_utc'] > now:
            return 'deferred_until_due'
        if r['reporting_scope'] != 'standalone':
            return 'reporting_scope'
        if r['result_period'] > period:
            return 'future_period'
        if r['result_period'] == period:
            return 'current_due'
        if r['discovered'] and r['discovered'] >= '2026-10-09T00:00:00+00:00':
            return 'recent_filing_due'
        return 'historical_lane'
    return {'observed_at_utc': now, 'period': period,
        'known_companies': conn.execute('SELECT count(*) FROM companies').fetchone()[0],
        'all_jobs': dict(Counter(reason(r) for r in rows)),
        'current_quarter_jobs': dict(Counter(reason(r) for r in rows if r['result_period'] == period)),
        'current_quarter_companies': dict(Counter({k:len({r['canonical_path'] for r in rows
            if r['result_period']==period and reason(r)==k}) for k in {reason(r) for r in rows if r['result_period']==period}})),
        'pending_by_period_and_reason': [dict(period=p, reason=k, jobs=n) for (p,k),n in
            Counter((r['result_period'], reason(r)) for r in rows if r['status'] in ('pending','failed_retryable','claimed','running')).items()],
        'pending_examples': [{k:r[k] for k in ('queue_item_id','company_path_normalized','result_period',
            'reporting_scope','status','eligibility','collection_disposition','next_attempt_at_utc','created_at_utc','discovered','last_error_json')}
            | {'selection_reason': reason(r)} for r in rows if r['status'] in ('pending','failed_retryable')][:1300],
        'recent_screen_runs': [dict(r) for r in conn.execute('''SELECT screen_run_id,query_role,query_raw,
            observed_at_utc,result_count,captured_row_count,page_number,page_count,coverage_status,
            validation_status FROM screener_screen_runs ORDER BY screen_run_id DESC LIMIT 5''')],
        'filing_decisions': [dict(r) for r in conn.execute('''SELECT e.decision,e.decision_reason,
            count(*) jobs,count(distinct e.company_path_normalized) companies
            FROM exchange_screener_enqueues e WHERE e.result_period=? GROUP BY 1,2''',(period,))],
        'unqueued_screen_candidates': [dict(r) for r in conn.execute('''SELECT count(distinct c.company_path_normalized) companies
            FROM screener_result_candidates c WHERE c.result_period=? AND NOT EXISTS
            (SELECT 1 FROM screener_company_queue q WHERE q.result_period=c.result_period
            AND q.company_path_normalized=replace(c.company_path_normalized,'/consolidated/','/'))''',(period,))]}


def screen_evidence(conn):
    from bs4 import BeautifulSoup
    row=conn.execute('''SELECT b.local_path,f.fetch_id FROM response_blobs b
        JOIN fetch_observations f USING(blob_id) JOIN scrape_runs r USING(run_id)
        WHERE r.code_version='financial-screen-refresh-v1' ORDER BY f.fetch_id DESC LIMIT 1''').fetchone()
    path=Path(row['local_path'])
    soup=BeautifulSoup(path.read_bytes(),'html.parser')
    node=soup.select_one('[data-page-info]')
    return {'fetch_id':row['fetch_id'],'path':str(path),
        'page_info':str(node), 'query':str(soup.select_one('textarea[name=query]')),
        'rows':len(soup.select('[data-row-company-id]')),
        'pagination_links':[{'href':a.get('href'),'text':a.get_text(' ',strip=True)}
            for a in soup.select('.pagination a')][:15]}


if __name__ == '__main__':
    db = Path('/data/scraper.db')
    gate = Path('/activation/.monitor-readers.lock').open('rb')
    fcntl.flock(gate, fcntl.LOCK_EX)
    before = _assert_safe_empty_wal(db)
    conn = sqlite3.connect('file:' + str(db) + '?mode=ro&immutable=1', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    try:
        if '--accepted-versions' in sys.argv:
            result={'rows':[dict(r) for r in conn.execute('SELECT * FROM accepted_page_versions')]}
        else:
            result = screen_evidence(conn) if '--screen-evidence' in sys.argv else inspect(conn, datetime.now(timezone.utc).isoformat())
    finally:
        conn.close()
    if snapshot(db) != before['files']:
        raise RuntimeError('Database changed during diagnosis; discard results: ' +
            json.dumps({'before':before['files'], 'after':snapshot(db)}))
    gate.close()
    print(json.dumps(result))
