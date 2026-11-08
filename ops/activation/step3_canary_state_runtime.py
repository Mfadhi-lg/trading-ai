"""Read-only before/after business-state evidence for the selected canary."""
from contextlib import closing, ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot


def main():
    from scrapper.operations.coordinator.exchange import ownership
    with ExitStack() as owners:
        for role in ('scheduler','executor'):
            if not owners.enter_context(ownership(Path('/data/scraper.db'),role)):
                raise RuntimeError('Existing exchange '+role+' owner')
        return read_state()


def read_state():
    db=Path('/data/scraper.db')
    sidecars=assert_safe_empty_wal(db)
    before_path=Path('/verification/before.json')
    before=json.loads(before_path.read_text()) if before_path.exists() else None
    with closing(sqlite3.connect('file:/data/scraper.db?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
        def rows(sql,args=()):return [dict(r) for r in c.execute(sql,args)]
        def fingerprint(sql,args=()):
            h=hashlib.sha256();count=0
            for row in c.execute(sql,args):
                h.update(json.dumps(dict(row),sort_keys=True,separators=(',',':')).encode());h.update(b'\n');count+=1
            return {'rows':count,'sha256':h.hexdigest()}
        parse_ids=before['prior_parse_ids'] if before else [r[0] for r in c.execute(
            "SELECT DISTINCT parse_id FROM acceptance_events WHERE company_id IN (1,3) AND source='screener' ORDER BY parse_id")]
        placeholders=','.join('?' for _ in parse_ids) or 'NULL'
        protected={}
        for table in ('financial_observations','company_metric_observations','quote_observations','source_cells','parse_artifacts'):
            cols=[r['name'] for r in c.execute('PRAGMA table_info("'+table+'")')]
            if 'parse_id' in cols:
                protected[table]=fingerprint('SELECT * FROM "'+table+'" WHERE parse_id IN ('+placeholders+') ORDER BY '+','.join('"'+col+'"' for col in cols),parse_ids)
        cutoff=before['acceptance_event_cutoff'] if before else c.execute('SELECT coalesce(max(acceptance_id),0) FROM acceptance_events').fetchone()[0]
        protected['prior_acceptance_events']=fingerprint('SELECT * FROM acceptance_events WHERE acceptance_id<=? ORDER BY acceptance_id',(cutoff,))
        protected['other_queues']=fingerprint('SELECT * FROM screener_company_queue WHERE queue_item_id NOT IN (18126,18127) ORDER BY queue_item_id')
        protected['other_queue_events']=fingerprint('SELECT * FROM screener_company_queue_events WHERE queue_item_id NOT IN (18126,18127) ORDER BY queue_event_id')
        protected['other_accepted_versions']=fingerprint('SELECT * FROM accepted_page_versions WHERE company_id NOT IN (1,3) ORDER BY company_id,source,reporting_scope,resolved_source_period')
        protected['exchange_filings']=fingerprint('SELECT * FROM exchange_result_filings ORDER BY filing_id')
        fetch_cutoff=before['fetch_cutoff'] if before else c.execute('SELECT coalesce(max(fetch_id),0) FROM fetch_observations').fetchone()[0]
        state={'observed_at_utc':datetime.now(timezone.utc).isoformat(),'sidecar_guard':sidecars,
            'prior_parse_ids':parse_ids,'acceptance_event_cutoff':cutoff,'fetch_cutoff':fetch_cutoff,'protected':protected,
            'queues':rows('SELECT * FROM screener_company_queue WHERE queue_item_id IN (18126,18127) ORDER BY queue_item_id'),
            'queue_events':rows('SELECT * FROM screener_company_queue_events WHERE queue_item_id IN (18126,18127) ORDER BY queue_event_id'),
            'accepted_versions':rows('SELECT * FROM accepted_page_versions WHERE company_id IN (1,3) ORDER BY company_id,source,reporting_scope,resolved_source_period'),
            'new_acceptance_events':rows('SELECT * FROM acceptance_events WHERE acceptance_id>? ORDER BY acceptance_id',(cutoff,)),
            'new_fetches':rows('SELECT f.*,b.content_sha256,b.byte_size,b.local_path FROM fetch_observations f LEFT JOIN response_blobs b USING(blob_id) WHERE fetch_id>? ORDER BY fetch_id',(fetch_cutoff,)),
            'collection_state':rows("SELECT * FROM screener_company_collection_state WHERE company_path_normalized IN ('/company/GMBREW/','/company/TCS/') ORDER BY company_path_normalized,reporting_scope"),
            'companies':rows('SELECT * FROM companies WHERE company_id IN (1,3) ORDER BY company_id'),
            'source_identifiers':rows("SELECT * FROM source_identifiers WHERE company_id IN (1,3) AND source='screener' ORDER BY company_id"),
            'holds':rows('SELECT * FROM exchange_source_access_events ORDER BY event_id'),
            'host_state':rows('SELECT * FROM acquisition_host_state'),
            'permits':rows('SELECT host,acquisition_mode,status,acquired_at,released_at,owner FROM acquisition_host_permits ORDER BY acquired_at'),
            'new_parse_details':rows('''SELECT p.*,q.company_path_normalized,q.result_period,q.status,
                (SELECT count(*) FROM financial_observations f WHERE f.parse_id=p.parse_id) AS financial_rows
                FROM screener_company_queue q JOIN parse_artifacts p ON p.parse_id=q.accepted_parse_id
                WHERE q.queue_item_id IN (18126,18127)''')}
        if before:
            state['protected_unchanged']={k:v==before['protected'][k] for k,v in protected.items()}
            state['holds_unchanged']=state['holds']==before['holds']
        print(json.dumps(state),flush=True)
    if snapshot(db)['main']!=sidecars['files']['main']:
        raise RuntimeError('Main DB changed during query-only verification')


if __name__=='__main__':main()
