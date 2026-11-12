"""One retained-response ingestion, using only the pinned canonical workflow."""
from contextlib import ExitStack, closing
from dataclasses import asdict
from datetime import datetime, timezone
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys

# Read-only operator files take precedence; all scrapper application modules
# continue to come from the unchanged, verified canonical image.
sys.path[:0]=[str(Path(__file__).resolve().parent),'/app']


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def emit(stage, **payload):
    print(json.dumps({'stage':stage, **payload}, sort_keys=True), flush=True)


def run():
    with ExitStack() as owners:
        return _run_owned(owners)


def _run_owned(owners):
    import production_ownership
    import sqlite_sidecar_guard
    from production_ownership import assert_no_conflicting_leases
    from sqlite_sidecar_guard import assert_safe_empty_wal
    from scrapper.config import assert_configured_writable_targets
    from scrapper.db.database import check_schema_compatibility, connect
    from scrapper.operations.coordinator.exchange import ownership
    from scrapper.sources.exchange.browser_results import RetainedBSEBrowserResults
    from scrapper.sources.exchange.result_filings import parse_result_filings, BSE_FINANCIAL_RESULTS
    from scrapper.workflows.exchange import universe

    manifest=json.loads(Path('/checks/manifest.json').read_text())
    require(hashlib.sha256(Path(production_ownership.__file__).read_bytes()).hexdigest()==manifest['production_ownership_sha256'],
            'Production ownership checks differ from the tested source')
    require(hashlib.sha256(Path(sqlite_sidecar_guard.__file__).read_bytes()).hexdigest()==manifest['sqlite_sidecar_guard_sha256'],
            'SQLite sidecar checks differ from the tested source')
    permission=json.loads(Path('/checks/permission.json').read_text())
    require(hashlib.sha256(Path('/checks/permission.json').read_bytes()).hexdigest()==manifest['permission_sha256'],
            'Recorded source permission changed')
    require(permission['collection'] and permission['electronic_storage'] and permission['project_reuse'],
            'Source permission does not cover the selected use')
    require(permission['selected_operation']=='eight retained October 9 BSE filings only',
            'Permission scope mismatch')
    for name, digest in manifest['application_hashes'].items():
        require(hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()==digest,
                'Packaged application source mismatch: '+name)
    for name,digest in manifest['input_hashes'].items():
        require(hashlib.sha256((Path('/input')/name).read_bytes()).hexdigest()==digest,
                'Retained input changed: '+name)
    require(hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==manifest['runtime_sha256'],
            'Bounded runtime adapter changed')
    emit('guard_execution_path',runtime=str(Path(__file__).resolve()),
         ownership=str(Path(production_ownership.__file__).resolve()),
         sqlite_sidecars=str(Path(sqlite_sidecar_guard.__file__).resolve()),
         verified_hashes={'runtime':manifest['runtime_sha256'],
             'ownership':manifest['production_ownership_sha256'],
             'sqlite_sidecars':manifest['sqlite_sidecar_guard_sha256']},
         application_source='unchanged pinned image /app/scrapper')
    source=RetainedBSEBrowserResults.load(
        receipt_path=Path('/input/receipts/2026-10-09-morning.json'),
        raw_path=Path('/input/receipts/2026-10-09-morning.raw'),
        verification_path=Path('/input/verification.json'))
    require(source.content_sha256=='4888eaaab82c2e59bda3b778947cd411756aa2fd4aefc0713d1abac85ba987d2'
            and source.captured_at_utc=='2026-10-09T04:29:57.897538+00:00',
            'Source is not the authorized October 9 capture')
    parsed=parse_result_filings(source.response.content,BSE_FINANCIAL_RESULTS,result_period=source.result_period)
    expected_keys=json.loads(Path('/checks/selected-filings.json').read_text())
    expected_keys=sorted(item['filing_key'] for item in expected_keys)
    require(len(parsed.filings)==8 and not parsed.rejected and
            sorted(item.filing_key for item in parsed.filings)==expected_keys,
            'Candidate is not exactly the eight authorized filings')
    require(source.result_period=='2026-09' and len(source.response.content)==26951,
            'Retained period/byte budget mismatch')

    baseline=json.loads(Path('/checks/production-baseline.json').read_text())
    db=Path('/data/scraper.db')
    require(db.is_file() and not db.is_symlink(), 'Production DB missing or symlinked')
    before_stat=db.stat()
    require(before_stat.st_ino==baseline['db_after']['inode'] and
            before_stat.st_size>=baseline['db_after']['bytes'], 'Production DB file identity mismatch')
    mount_rows=[line.split(' - ',1)[0].split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    require(any(r[4]=='/physical' and 'ro' in r[5].split(',') for r in mount_rows),
            'Underlying production identity mount is not read-only')
    for name,expected in baseline['physical_markers'].items():
        actual=Path(name.replace('/data/','/physical/',1))
        require(hashlib.sha256(actual.read_bytes()).hexdigest()==expected['sha256'],
                'Underlying production marker changed: '+name)
    assert_configured_writable_targets(db_path=db)
    storage=[]
    # This operation writes DB metadata and one 27 KB exchange evidence blob,
    # not documents/backlogs. Check both actual destinations and WAL headroom.
    for target in (Path('/data'),Path('/data/snapshots')):
        stats=os.statvfs(target)
        free=stats.f_bavail*stats.f_frsize
        require(target.stat().st_dev==before_stat.st_dev,
                'DB/evidence storage device mismatch: '+str(target))
        require(not (stats.f_flag & os.ST_RDONLY) and free>=64*1024*1024,
                'Insufficient writable storage/WAL headroom: '+str(target))
        storage.append({'path':str(target),'device':target.stat().st_dev,
            'available_bytes':free,'minimum_operation_headroom_bytes':64*1024*1024})
    emit('storage_checks_passed',destinations=storage)
    for role in ('scheduler','executor'):
        require(owners.enter_context(ownership(db,role)),'Existing exchange '+role+' owner')
    sidecar_state=assert_safe_empty_wal(db)

    def reader():
        # Ordinary SQLite locking/WAL handling; never ignore WAL with immutable=1.
        c=sqlite3.connect('file:/data/scraper.db?mode=ro',uri=True)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        return c
    tables=tuple(json.loads(Path('/checks/expected-changes.json').read_text())['row_count_deltas'])
    def counts(c):
        return {t:c.execute('SELECT COUNT(*) FROM "'+t+'"').fetchone()[0] for t in tables}
    paths=('/company/TCS/','/company/GMBREW/','/company/LOTUSCHO/','/company/ONIXSOLAR/','/company/523465/')
    def states(c):
        return [dict(r) for r in c.execute('SELECT * FROM screener_company_collection_state WHERE company_path_normalized IN (?,?,?,?,?) AND reporting_scope=\'standalone\' ORDER BY company_path_normalized',paths)]
    with closing(reader()) as c:
        check_schema_compatibility(c)
        lease_checks=assert_no_conflicting_leases(c,now=datetime.now(timezone.utc).isoformat())
        require([dict(r) for r in c.execute('SELECT * FROM schema_migrations ORDER BY version')]==baseline['ledger'],
                'Production migration ledger drift')
        require([dict(r) for r in c.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]==baseline['schema'],
                'Production schema drift')
        control=c.execute('SELECT * FROM document_extraction_claims WHERE resource_key=?',
                          (baseline['control_row']['resource_key'],)).fetchone()
        require(control is not None and dict(control)==baseline['control_row'], 'Production control receipt changed')
        holds=[dict(r) for r in c.execute('SELECT * FROM exchange_source_access_events ORDER BY event_id')]
        require(holds==manifest['expected_holds'], 'BSE API holds changed')
        require(not c.execute('SELECT 1 FROM response_blobs WHERE content_sha256=?',(source.content_sha256,)).fetchone(),
                'Candidate blob is already persisted; expected mutation count changed')
        for f in parsed.filings:
            require(not c.execute('SELECT 1 FROM exchange_result_filings WHERE exchange=? AND filing_key=?',
                                  ('BSE',f.filing_key)).fetchone(), 'An authorized filing is already stored')
        before_counts=counts(c)
        before_states=states(c)
        protected_queue=[dict(r) for r in c.execute('SELECT * FROM screener_company_queue WHERE queue_item_id IN (18126,18127) ORDER BY queue_item_id')]
        require(len(protected_queue)==2, 'Expected reused queue rows missing')
        watermark=dict(c.execute('SELECT * FROM exchange_result_watermarks WHERE exchange=\'BSE\' AND dataset=?',
                                (BSE_FINANCIAL_RESULTS,)).fetchone())
        require(watermark['last_broadcast_at_utc']=='2026-09-15T10:32:20+00:00',
                'BSE watermark changed; refresh forecast before any writes')
    emit('safety_checks_passed', source_permission=permission, production_identity={
        'inode':before_stat.st_ino,'bytes':before_stat.st_size,'schema':'matched','ledger':'matched',
        'sidecars':sidecar_state,'markers':'matched','runtime_identity':'passed'},before_counts=before_counts,
        lease_checks=lease_checks)

    write_tables={'scrape_runs','response_blobs','fetch_observations','exchange_source_snapshots',
        'exchange_snapshot_validations','exchange_result_filings','exchange_screener_enqueues',
        'screener_company_queue','screener_company_queue_events','screener_company_collection_state',
        'exchange_company_result_state','exchange_result_watermarks','exchange_source_rejected_rows',
        'exchange_source_excluded_rows','exchange_result_filing_collisions'}
    writes=[]
    def protected_connect(path):
        require(Path(path)==db,'Canonical writer selected an unexpected DB')
        c=connect(path)  # Existing production identity, FK and directory guards.
        def authorize(action,table,column,database,trigger):
            if table == 'screener_queue_identities' and action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                return sqlite3.SQLITE_OK if action == sqlite3.SQLITE_INSERT and trigger == 'trg_queue_identity_register' else sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
                if table not in write_tables or action==sqlite3.SQLITE_DELETE:
                    return sqlite3.SQLITE_DENY
                if action==sqlite3.SQLITE_UPDATE and table in ('exchange_result_filings','exchange_screener_enqueues','response_blobs','fetch_observations','screener_company_queue','screener_company_queue_events'):
                    return sqlite3.SQLITE_DENY
                writes.append({'action':action,'table':table,'column':column})
            if action in (sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH,sqlite3.SQLITE_ALTER_TABLE,
                          sqlite3.SQLITE_DROP_TABLE,sqlite3.SQLITE_CREATE_TABLE):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        c.set_authorizer(authorize)
        return c

    with ExitStack() as stack:
        require(snapshot_main_unchanged(db,sidecar_state),'Main DB changed after read-only baseline checks')
        assert_safe_empty_wal(db)
        options=dict(dataset=BSE_FINANCIAL_RESULTS,url=source.response.requested_url,
            result_period=source.result_period,effective_date='2026-10-09',
            now_utc=lambda:source.captured_at_utc,fetcher=source)
        preview=universe.discover_exchange_results(**options,dry_run=True)
        emit('preview', result=preview)
        require(preview['validation']['status']=='passed' and preview['accepted_row_count']==8 and
                preview['rejected_row_count']==0 and preview['enqueue']=={
                    'queued':3,'reused':5,'stale':0,'already_accepted':0,'manual_review':0},
                'Fresh canonical preview differs from the authorized rehearsal')
        require(snapshot_main_unchanged(db,sidecar_state),'Main DB changed during canonical preview')
        assert_safe_empty_wal(db)
        emit('write_start', authorized_filing_keys=expected_keys, automatic_retry=False)
        result=universe.discover_exchange_results(**options,
            _ingest=partial(universe._ingest,connect=protected_connect))
        emit('canonical_result', result=result)
        require(result['validation']['status']=='passed', 'Persistence guard rejected candidate')
        require(result['filings']['stored']==8 and result['filings']['identity_collisions']==0,
                'Actual filing mutations differ from expected result')
        require(result['enqueue']['queued']==3 and result['enqueue']['reused']==5 and
                not result['enqueue']['manual_review'], 'Actual queue decisions differ from expected result')
        with closing(reader()) as c:
            actual_counts=counts(c)
            deltas={t:actual_counts[t]-before_counts[t] for t in tables}
            expected=json.loads(Path('/checks/expected-changes.json').read_text())['row_count_deltas']
            require(deltas==expected,'Actual DB row-count deltas differ: '+json.dumps(deltas))
            require([dict(r) for r in c.execute('SELECT * FROM exchange_source_access_events ORDER BY event_id')]==holds,
                    'BSE API holds changed')
            require([dict(r) for r in c.execute('SELECT * FROM screener_company_queue WHERE queue_item_id IN (18126,18127) ORDER BY queue_item_id')]==protected_queue,
                    'Existing reused queue rows were modified')
            after_states=states(c)
            before_map={s['company_path_normalized']:s for s in before_states}
            for state in after_states:
                prior=before_map.get(state['company_path_normalized'])
                if prior:
                    require(all(state[k]==prior[k] for k in ('company_id','latest_accepted_result_period','latest_accepted_parse_id')),
                            'Existing accepted collection history changed')
            actual_filings=[dict(r) for r in c.execute('SELECT * FROM exchange_result_filings WHERE snapshot_id=? ORDER BY filing_key',(result['snapshot_id'],))]
            require(sorted(f['filing_key'] for f in actual_filings)==expected_keys,'Stored filing identities differ')
            by_key={f.filing_key:f for f in parsed.filings}
            require(all(f['raw_row_json']==by_key[f['filing_key']].raw_row_json for f in actual_filings),
                    'Stored source rows were changed')
            fetch=c.execute('SELECT * FROM fetch_observations WHERE fetch_id=?',(result['fetch_id'],)).fetchone()
            require(fetch['requested_url']==source.response.requested_url and fetch['final_url']==source.response.final_url
                    and fetch['auth_mode']=='public_browser' and fetch['fetched_at_utc']==source.captured_at_utc,
                    'Source URL/auth/capture provenance mismatch')
            blob=c.execute('SELECT * FROM response_blobs WHERE blob_id=?',(result['blob_id'],)).fetchone()
            require(hashlib.sha256(Path(blob['local_path']).read_bytes()).hexdigest()==source.content_sha256,
                    'Persisted response bytes differ from authorized input')
            new_watermark=dict(c.execute('SELECT * FROM exchange_result_watermarks WHERE exchange=\'BSE\' AND dataset=?',
                                        (BSE_FINANCIAL_RESULTS,)).fetchone())
            require(new_watermark['last_broadcast_at_utc']==parsed.max_broadcast_at_utc and
                    new_watermark['last_snapshot_id']==result['snapshot_id'], 'Wrong watermark advancement')
            decisions=[dict(r) for r in c.execute('SELECT e.* FROM exchange_screener_enqueues e JOIN exchange_result_filings f USING(filing_id) WHERE f.snapshot_id=? ORDER BY e.filing_id',(result['snapshot_id'],))]
            queue_ids=sorted({d['queue_item_id'] for d in decisions if d['decision']=='queued'})
            queues=[dict(c.execute('SELECT * FROM screener_company_queue WHERE queue_item_id=?',(qid,)).fetchone()) for qid in queue_ids]
            require({q['company_path_normalized'] for q in queues}=={'/company/LOTUSCHO/','/company/ONIXSOLAR/','/company/523465/'}
                    and all(q['reporting_scope']=='standalone' and q['result_period']=='2026-09' and q['status']=='pending' for q in queues),
                    'New queue scope or company identities differ')
            emit('verified_outcome', status='PASS',actual_deltas=deltas,new_queue_rows=queues,
                enqueue_decisions=decisions,watermark_before=watermark,watermark_after=new_watermark,
                bse_holds_unchanged=True,accepted_history_unchanged=True,
                protected_write_tables=sorted({w['table'] for w in writes}),
                operation_finished_at_utc=datetime.now(timezone.utc).isoformat(),source_sha256=source.content_sha256)
    return 0


def snapshot_main_unchanged(db,sidecar_state):
    from sqlite_sidecar_guard import snapshot
    return snapshot(db)['main']==sidecar_state['files']['main']


if __name__=='__main__':
    def deadline(*_):
        raise RuntimeError('300-second deadline; stop and retain partial evidence')
    signal.signal(signal.SIGALRM,deadline)
    signal.alarm(300)
    try:
        sys.exit(run())
    except Exception as exc:
        emit('stop',status='STOP',error=str(exc),automatic_retry=False)
        sys.exit(2)
    finally:
        signal.alarm(0)
