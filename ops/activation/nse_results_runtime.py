"""Finite operator adapter; application imports come only from the pinned image."""
import argparse
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
from requests.exceptions import Timeout

sys.path.insert(0, '/app')
sys.path.insert(0, str(Path(__file__).resolve().parent))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def fetch_nse_response(url, selected_period):
    from scrapper.sources.exchange.transport import fetch_exchange_recovery_surface
    try:
        return fetch_exchange_recovery_surface(url, 'NSE', timeout=30, max_redirects=0)
    except Timeout as exc:
        print(json.dumps({'stage': 'source_transport_deferred', 'dataset': 'nse_financial_results',
            'query_period': selected_period, 'error_type': type(exc).__name__,
            'error': str(exc), 'automatic_retry': False,
            'resume': 'next eligible scheduled slot'}), flush=True)
        return None


# Each NSE run reads two quarters; their combined cap must cover both snapshots.
MAX_SNAPSHOT_ROWS = 6000
MAX_TOTAL_ROWS = 2 * MAX_SNAPSHOT_ROWS
NSE_QUEUE_LIMIT = 100
BSE_QUEUE_LIMIT = 2000


def validate_preview(preview, totals=None, *, admission_limit=None, queue_limit=NSE_QUEUE_LIMIT):
    require((preview.get('validation') or {}).get('status') == 'passed', 'Snapshot guard rejected input')
    require(0 < preview['accepted_row_count'] <= MAX_SNAPSHOT_ROWS, 'Snapshot row budget exceeded')
    queued = preview['enqueue'].get('queued', 0)
    if admission_limit is not None:
        require(type(admission_limit) is int and 0 <= admission_limit <= queue_limit, 'Invalid bounded queue admission')
        queued = min(queued, admission_limit)
    require(queued <= queue_limit, f'New queue budget exceeds {queue_limit}; stop before writes')
    totals = totals or {'rows': 0, 'queued': 0}
    require(totals['rows'] + preview['accepted_row_count'] <= MAX_TOTAL_ROWS, 'Total snapshot row budget exceeded')
    require(totals['queued'] + queued <= queue_limit,
            f'Total new queue budget exceeds {queue_limit}; stop before this snapshot writes')


def review_dispositions(reader,snapshot_id,*,retained_bse=False,bounded_nse=False):
    """Keep unknown BSE securities quarantined; contradictions still halt intake."""
    pending=[];fatal=[]
    for row in reader.execute('''SELECT e.filing_id,e.decision_reason FROM exchange_screener_enqueues e
        JOIN exchange_result_filings f USING(filing_id)
        WHERE f.snapshot_id=? AND e.decision='manual_review' ''',(snapshot_id,)):
        reason=row['decision_reason']
        if reason.startswith('possible_same_period_revision:'):continue
        target=pending if (retained_bse or bounded_nse) and reason=='security_not_in_registry' else fatal
        target.append(dict(row))
    return pending,fatal


def run(period=None, source=None, verify_only=False):
    from scrapper.operations.coordinator.exchange import ownership
    from scrapper.sources.exchange.reporting_periods import priority_result_periods

    periods = priority_result_periods(override=period)
    db = Path('/data/scraper.db')
    baseline = json.loads(Path('/activation/production-baseline.json').read_text())
    require(db.is_file() and not db.is_symlink(), 'Existing production DB required')
    mounts = [line.split(' - ', 1)[0].split() for line in
              Path('/proc/self/mountinfo').read_text().splitlines()]
    require(any(row[4] == '/physical' and 'ro' in row[5].split(',') for row in mounts),
            'Underlying production volume must also be mounted read-only')
    with ExitStack() as stack:
        for role in ('scheduler', 'executor'):
            require(stack.enter_context(ownership(db, role)), 'Exchange ' + role + ' already owned')
        return run_owned(periods, db, baseline, source=source, verify_only=verify_only)


def run_owned(periods, db, baseline, source=None, verify_only=False):
    from scrapper.config import assert_configured_writable_targets
    from scrapper.db.database import check_schema_compatibility
    from scrapper.workflows.exchange.universe import discover_exchange_results, resolve_url
    from sqlite_sidecar_guard import assert_safe_empty_wal
    from production_ownership import assert_no_conflicting_leases
    from datetime import datetime, timezone
    from functools import partial
    from scrapper.workflows.exchange import universe
    from scrapper.db.database import connect
    from zoneinfo import ZoneInfo
    # A cooperating WAL reader can be the last connection after a worker exits.
    # Drain monitors, reject unknown connections, and checkpoint committed frames
    # before the unchanged cold-state verification. No sidecar is deleted by hand.
    from sqlite_sidecar_guard import prepare_writer_startup
    assert_configured_writable_targets(db_path=db)
    prepare_writer_startup(db,expected_inode=baseline['db_after']['inode'])
    print(json.dumps({'stage': 'sidecar_guard', 'result': assert_safe_empty_wal(db)}), flush=True)
    require(db.stat().st_ino == baseline['db_after']['inode'] and
            db.stat().st_size >= baseline['db_after']['bytes'], 'Production DB file identity mismatch')
    # Check real, underlying markers before the role-specific read-only configs.
    for name, expected in baseline['physical_markers'].items():
        actual = Path(name.replace('/data/', '/physical/', 1))
        require(hashlib.sha256(actual.read_bytes()).hexdigest() == expected['sha256'],
                'Underlying production marker changed: ' + name)
    assert_configured_writable_targets(db_path=db)
    for target in (db.parent, Path('/data/snapshots')):
        stats = os.statvfs(target)
        require(not stats.f_flag & os.ST_RDONLY and stats.f_bavail * stats.f_frsize >= 64 * 1024 * 1024,
                'Insufficient writable DB/evidence storage and WAL headroom')
    with closing(sqlite3.connect('file:/data/scraper.db?mode=ro', uri=True)) as reader:
        reader.row_factory = sqlite3.Row
        reader.execute('PRAGMA query_only=ON')
        check_schema_compatibility(reader)
        assert_no_conflicting_leases(reader, now=datetime.now(timezone.utc).isoformat())
        require([dict(r) for r in reader.execute('SELECT * FROM schema_migrations ORDER BY version')]
                == baseline['ledger'], 'Production migration identity mismatch')
        require([dict(r) for r in reader.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
                == baseline['schema'], 'Production schema changed')
        receipt = reader.execute('SELECT * FROM document_extraction_claims WHERE resource_key=?',
                                 (baseline['control_row']['resource_key'],)).fetchone()
        require(receipt is not None and dict(receipt) == baseline['control_row'], 'Control receipt changed')
        holds = [dict(r) for r in reader.execute('SELECT * FROM exchange_source_access_events ORDER BY event_id')]
        queue_before = reader.execute('SELECT COUNT(*) FROM screener_company_queue').fetchone()[0]
        if verify_only:
            print(json.dumps({'stage': 'preflight_verified', 'production_ingestion': False,
                'source_requests': 0, 'holds': holds, 'queue_count': queue_before,
                'filing_counts': [dict(r) for r in reader.execute(
                    'SELECT exchange,COUNT(*) AS count FROM exchange_result_filings GROUP BY exchange')],
                'document_worker_started': False, 'backlog_dispatched': False}), flush=True)
    if verify_only:
        if source:
            require(len(source.response.content) <= 10 * 1024 * 1024, 'Retained response byte budget exceeded')
            assert_safe_empty_wal(db)
            preview = discover_exchange_results(dataset='bse_financial_results',
                url=source.response.requested_url, result_period=source.result_period,
                effective_date=datetime.fromisoformat(source.captured_at_utc).astimezone(ZoneInfo('Asia/Kolkata')).date().isoformat(),
                now_utc=lambda: source.captured_at_utc, fetcher=source, dry_run=True)
            validate_preview(preview, queue_limit=BSE_QUEUE_LIMIT)
            print(json.dumps({'stage': 'preflight_preview', 'result': preview,
                'source_requests': 0, 'production_ingestion': False}), flush=True)
        return 0
    dataset = 'bse_financial_results' if source else 'nse_financial_results'
    exchange_name = 'BSE' if source else 'NSE'
    if source:
        periods = (source.result_period,)
    queue_limit = BSE_QUEUE_LIMIT if source else NSE_QUEUE_LIMIT
    totals = {'rows': 0, 'queued': 0, 'bytes': 0}
    # Reuse the successful BSE operation's table boundary. Financial history,
    # documents, ratings and API holds cannot be changed by this writer.
    write_tables = {'scrape_runs','response_blobs','fetch_observations','exchange_source_snapshots',
        'exchange_snapshot_validations','exchange_result_filings','exchange_screener_enqueues',
        'screener_company_queue','screener_company_queue_events','screener_company_collection_state',
        'exchange_company_result_state','exchange_result_watermarks','exchange_source_rejected_rows',
        'exchange_source_excluded_rows','exchange_result_filing_collisions'}
    def protected_connect(path):
        require(Path(path) == db, 'Unexpected canonical DB target')
        conn = connect(path)
        def authorize(action, table, column, database, trigger):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                if table not in write_tables or action == sqlite3.SQLITE_DELETE:
                    return sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_UPDATE and table in ('exchange_result_filings',
                        'exchange_screener_enqueues','response_blobs','fetch_observations',
                        'screener_company_queue','screener_company_queue_events'):
                    return sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH,sqlite3.SQLITE_ALTER_TABLE,
                    sqlite3.SQLITE_DROP_TABLE,sqlite3.SQLITE_CREATE_TABLE):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        conn.set_authorizer(authorize)
        return conn
    for selected_period in periods:
        url = source.response.requested_url if source else resolve_url(dataset, None, result_period=selected_period)
        if not source:
            print(json.dumps({'stage':'source_request_started','dataset':dataset,
                'query_period':selected_period,'requested_url':url}),flush=True)
        response = source.response if source else fetch_nse_response(url, selected_period)
        if response is None:
            # No response exists to ingest; preserve prior period outcomes.
            return 75
        if not source:
            print(json.dumps({'stage':'source_response_received','dataset':dataset,
                'query_period':selected_period,'status_code':response.status_code,
                'fetch_status':response.fetch_status,'bytes':len(response.content),
                'requested_url':response.requested_url,'final_url':response.final_url}),flush=True)
        totals['bytes'] += len(response.content)
        require(totals['bytes'] <= 10 * 1024 * 1024, 'Response exceeds total 10 MiB ingestion budget')
        def retained_response(requested, exchange):
            require(requested == url and exchange == exchange_name, 'Unexpected source request')
            return response
        options = dict(dataset=dataset, result_period=selected_period, fetcher=retained_response)
        if source:
            options.update(url=url, now_utc=lambda: source.captured_at_utc,
                effective_date=datetime.fromisoformat(source.captured_at_utc).astimezone(
                    ZoneInfo('Asia/Kolkata')).date().isoformat())
        assert_safe_empty_wal(db)
        preview = discover_exchange_results(**options, dry_run=True)
        print(json.dumps({'stage': 'preview', 'query_period': selected_period, 'result': preview}), flush=True)
        # Only the NSE host adapter mounts the tested bounded-admission overlay.
        # BSE accepts busy reporting days within its separate queue capacity.
        admission_limit = queue_limit - totals['queued'] if not source else None
        validate_preview(preview, totals, admission_limit=admission_limit, queue_limit=queue_limit)
        if admission_limit is not None:
            print(json.dumps({'stage': 'bounded_queue_admission_plan', 'query_period': selected_period,
                'projected_new_queue_entries': preview['enqueue'].get('queued', 0),
                'maximum_new_queue_entries': admission_limit,
                'remaining_filings': 'Retained undecided for subsequent bounded feed admission'}), flush=True)
            options['max_new_queue_entries'] = admission_limit
        assert_safe_empty_wal(db)
        result = discover_exchange_results(**options,
            _ingest=partial(universe._ingest, connect=protected_connect))
        totals['rows'] += result['accepted_row_count']
        totals['queued'] += result['enqueue'].get('queued', 0)
        with closing(sqlite3.connect('file:/data/scraper.db?mode=ro', uri=True)) as reader:
            reader.row_factory = sqlite3.Row
            require([dict(r) for r in reader.execute('SELECT * FROM exchange_source_access_events ORDER BY event_id')]
                    == holds, 'Unexpected BSE hold mutation')
            delta = reader.execute('SELECT COUNT(*) FROM screener_company_queue').fetchone()[0] - queue_before
            require(0 <= delta <= queue_limit and delta == totals['queued'], 'Unexpected queue mutation')
        require(result['validation']['status'] == 'passed', 'Snapshot changed during persistence')
        print(json.dumps({'stage': 'outcome', 'query_period': selected_period, 'result': result,
                          'queue_delta': delta, 'totals': totals, 'bse_holds_unchanged': True}), flush=True)
        if result['enqueue'].get('manual_review', 0):
            with closing(sqlite3.connect('file:/data/scraper.db?mode=ro',uri=True)) as reader:
                reader.row_factory=sqlite3.Row
                pending,fatal=review_dispositions(reader,result['snapshot_id'],retained_bse=source is not None and exchange_name=='BSE',bounded_nse=admission_limit is not None)
            if pending:
                print(json.dumps({'stage':'registry_pending','snapshot_id':result['snapshot_id'],
                    'filings':pending,'quarantined':True,'unknown_company_jobs_created':0,
                    'coverage':'verified registry identities only; pending filings require evidenced registry reconciliation'}),flush=True)
            if fatal:
                print(json.dumps({'stage':'identity_review_stop','filings':fatal}),flush=True)
                return 1
            if result['enqueue']['manual_review']>len(pending):
                print(json.dumps({'stage':'revision_filings_handed_off','snapshot_id':result['snapshot_id'],
                    'canonical_financial_priority':'new source refresh on the same company/period queue'}),flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--period', help='Optional quarter override; otherwise use IST date priorities')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    # Deadline raises through normal Python unwinding; preserve partial evidence,
    # release OS locks, and never automatically retry a failed write.
    def deadline(*_):
        raise RuntimeError('300-second deadline; stop and inspect retained outcome')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(300)
    try:
        return run(args.period, verify_only=args.verify_only)
    except Exception as exc:
        print(json.dumps({'stage': 'stop', 'error': str(exc), 'automatic_retry': False}), flush=True)
        return 2
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    sys.exit(main())
