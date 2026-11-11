"""Selected current-quarter run through the existing canonical company worker."""
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import signal
import sqlite3
from postgresql_runtime import read_connection
import sys

sys.path[:0] = ['/activation', '/app']


class DeadlineExceeded(BaseException):
    """Unwind canonical worker cleanup without its per-company retry handler."""


def worker_now():
    # Revision refresh writes fractional seconds. The worker's default clock
    # truncates them, making a just-refreshed row appear not due in that second.
    return datetime.now(timezone.utc).isoformat()


def current_prefix(rows,period):
    """Only the current canonical prefix; never fill a batch with old work."""
    selected=[]
    for row in rows:
        if row.result_period!=period or row.reporting_scope!='standalone' or row.origin!='exchange_filing':
            break
        selected.append(row)
    return selected


def selected_claim(conn, *, plan, now_utc, limit, worker_id):
    """Fence the existing claimant before it can take older or unselected work."""
    from scrapper.db.screener_discovery_repo import preview_due_companies, claim_due_companies
    from scrapper.sources.exchange.reporting_periods import priority_result_periods
    expected = plan['queue_item_ids']
    if limit != len(expected) or priority_result_periods()[0] != plan['result_period']:
        raise RuntimeError('Selected batch size or current quarter changed; refresh the plan')
    if plan.get('priority_policy')=='three-priority-v1':
        return claim_due_companies(conn,now_utc=now_utc,limit=limit,worker_id=worker_id,
            queue_item_ids=tuple(expected))
    preview = preview_due_companies(conn, now_utc=now_utc, limit=limit)
    if [r.queue_item_id for r in preview] != expected or any(
        r.result_period != plan['result_period'] or r.reporting_scope != 'standalone' or
        r.origin != 'exchange_filing' for r in preview):
        raise RuntimeError('Canonical due order differs from the selected current-quarter batch; no claims made')
    return claim_due_companies(conn, now_utc=now_utc, limit=limit, worker_id=worker_id)


def main():
    from nse_results_runtime import run_owned, require
    from scrapper.operations.coordinator.exchange import ownership
    from scrapper.operations.workers.screener_requests import SharedScreenerClient
    from scrapper.workflows.screener.company_worker import run_screener_company_worker
    from scrapper.sources.exchange.reporting_periods import priority_result_periods
    plan = json.loads(Path('/selected/plan.json').read_text())
    is_cap=plan['mode'] in ('scope_monitor_once','scope_monitor_scheduled')
    manifest = json.loads(Path('/selected/manifest.json').read_text())
    for name, digest in (manifest['application_hashes'] | manifest.get('application_overlays',{})).items():
        require(hashlib.sha256((Path('/app') / name).read_bytes()).hexdigest() == digest,
                'Canonical financial application source changed: ' + name)
    for name,digest in manifest.get('migration_hashes',{}).items():
        require(hashlib.sha256((Path('/app/scrapper/db/migrations')/name).read_bytes()).hexdigest()==digest,'Prepared migration definition changed: '+name)
    for name, digest in manifest['operator_hashes'].items():
        require(hashlib.sha256((Path('/activation') / name).read_bytes()).hexdigest() == digest,
                'Prepared operator adapter changed: ' + name)
    require(hashlib.sha256(Path('/selected/plan.json').read_bytes()).hexdigest() == manifest['plan_sha256'],
            'Selected current-quarter plan changed')
    proof_path=Path('/selected/session-check.json')
    if proof_path.exists():
        from scrapper.config import SCREENER_COOKIE_FILE
        from scrapper.core.http import load_screener_cookies
        proof=json.loads(proof_path.read_text())
        require(hmac.new(bytes.fromhex(proof['nonce']),SCREENER_COOKIE_FILE.read_bytes(),hashlib.sha256).hexdigest()==proof['file_hmac'],
                'Scheduled session mount differs from the current authenticated host session')
        require(bool(load_screener_cookies(SCREENER_COOKIE_FILE).get('sessionid')),'Current session is unavailable')
        print(json.dumps({'stage':'session_file_verified','current_host_session_matches':True,
            'token_values_logged':False}),flush=True)
    db = Path('/data/scraper.db')
    require(db.is_file() and not db.is_symlink(), 'Existing non-symlink production DB required')
    baseline = json.loads(Path('/activation/production-baseline.json').read_text())
    require(any(r.split(' - ',1)[0].split()[4:6] == ['/physical','ro'] or
        (r.split(' - ',1)[0].split()[4] == '/physical' and 'ro' in r.split(' - ',1)[0].split()[5].split(','))
        for r in Path('/proc/self/mountinfo').read_text().splitlines()), 'Read-only physical identity mount required')
    with ExitStack() as stack:
        for role in ('scheduler','executor'):
            require(stack.enter_context(ownership(db,role)), 'Production exchange '+role+' is already owned')
        run_owned(priority_result_periods(), db, baseline, verify_only=True)
        require(priority_result_periods()[0] == plan['result_period'], 'Current quarter changed')
        for path in (Path('/data'),Path('/data/snapshots'),Path('/data/exports')):
            target = path if path.exists() else path.parent
            stats = os.statvfs(target)
            require(stats.f_bavail * stats.f_frsize >= 256 * 1024 * 1024, 'Financial batch storage/WAL reserve unavailable')
        if plan.get('priority_policy')=='three-priority-v1' and not is_cap and '--verify-only' not in sys.argv:
            from financial_priority import refresh_revisions
            from financial_screen_refresh import refresh_if_due
            from scrapper.db.database import connect
            with closing(connect(db)) as writable:
                discovery=refresh_if_due(writable,now_utc=datetime.now(timezone.utc).isoformat())
                print(json.dumps({'stage':'financial_screen_refresh',**discovery}),flush=True)
                refreshed=refresh_revisions(writable,now_utc=datetime.now(timezone.utc).isoformat(),activated_at=plan['priority_activated_at'])
                print(json.dumps({'stage':'revision_refresh','queue_ids':refreshed}),flush=True)
        if plan.get('paired_downloads') and '--verify-only' not in sys.argv:
            from paired_downloads import begin
            stack.enter_context(begin(plan))
        with closing(read_connection(db)) as c:
            c.row_factory = sqlite3.Row
            holds = [dict(r) for r in c.execute('SELECT * FROM exchange_source_access_events ORDER BY event_id')]
            company_count = c.execute('SELECT count(*) FROM companies').fetchone()[0]
            acceptance_count=c.execute('SELECT count(*) FROM accepted_page_versions').fetchone()[0]
            if plan['mode']=='priority_financial_scheduled':
                from financial_priority import select
                selected,lanes,period=select(c,now_utc=datetime.now(timezone.utc).isoformat(),
                    activated_at=plan['priority_activated_at'],historical_slot=plan['historical_slot'],limit=plan['max_companies'])
                plan['queue_item_ids']=[row.queue_item_id for row in selected]
                require(period==plan['result_period'],'Reporting quarter changed during selection')
                print(json.dumps({'stage':'priority_financial_selection','current_period':period,
                    'selected_queue_ids':plan['queue_item_ids'],'priorities':lanes,
                    'historical_budget':int(plan['historical_slot'])}),flush=True)
                if not selected:
                    print(json.dumps({'stage':'financial_scheduled_idle','current_period':period,
                        'queue_claims':0,'source_requests':0}),flush=True)
                    return 0
            if '--verify-only' in sys.argv:
                if is_cap:
                    row=c.execute('SELECT company_path,eligibility,market_cap_crore,next_check_at_utc FROM screener_collection_scope WHERE next_check_at_utc<=? ORDER BY next_check_at_utc,company_path LIMIT 1',
                        (datetime.now(timezone.utc).isoformat(),)).fetchone()
                    print(json.dumps({'stage':'scope_monitor_plan_verified','source_requests':0,
                        'scope_claims':0,'maximum_checks':1,'next_due':dict(row) if row else None}),flush=True)
                    return 0
                if plan.get('priority_policy')=='three-priority-v1':
                    print(json.dumps({'stage':'priority_preflight_verified','source_requests':0,'queue_claims':0,'selected':[r.to_dict() for r in selected]}),flush=True)
                    return 0
                from scrapper.db.screener_discovery_repo import preview_due_companies
                rows = preview_due_companies(c, now_utc=datetime.now(timezone.utc).isoformat(), limit=2)
                require([r.queue_item_id for r in rows] == plan['queue_item_ids'], 'Selected queue order changed')
                require(all(r.result_period == plan['result_period'] and r.reporting_scope == 'standalone'
                    and r.origin == 'exchange_filing' for r in rows), 'Selected current-quarter scope changed')
                print(json.dumps({'stage':'selected_batch_verified','source_requests':0,
                    'queue_claims':0,'selected':[r.to_dict() for r in rows]}), flush=True)
                return 0
        if is_cap:
            from scrapper.commands.collection_scope import monitor
            from scrapper.db.database import connect
            with closing(connect(db)) as conn:
                def financial_state():
                    digest=hashlib.sha256()
                    for row in conn.execute('SELECT * FROM accepted_page_versions ORDER BY company_id,source,reporting_scope,resolved_source_period'):
                        digest.update(json.dumps(dict(row),sort_keys=True).encode())
                    return digest.hexdigest()
                protected_before=financial_state()
                now=datetime.now(timezone.utc).isoformat()
                due_before=conn.execute('SELECT count(*) FROM screener_collection_scope WHERE next_check_at_utc<=?',(now,)).fetchone()[0]
                selected=conn.execute('''SELECT * FROM screener_collection_scope WHERE next_check_at_utc<=?
                    AND (claim_token IS NULL OR claim_expires_at_utc<=?) ORDER BY next_check_at_utc,company_path LIMIT 1''',(now,now)).fetchone()
                print(json.dumps({'stage':'cap_monitor_before','selected_scope':dict(selected) if selected else None,
                    'accepted_versions_sha256':protected_before,'due_count':due_before,
                    'maximum_checks':plan.get('max_scope_checks',1)}),flush=True)
                result=monitor(conn,limit=plan.get('max_scope_checks',1),shared_permits=True,**({'company_path':plan['scope_company_path']} if plan.get('scope_company_path') else {}))
                require(financial_state()==protected_before,'Cap check modified prior financial acceptance')
                for row in result['outcomes']:
                    if row.get('path'):
                        updated=conn.execute('SELECT * FROM screener_collection_scope WHERE company_path=?',(row['path'],)).fetchone()
                        print(json.dumps({'stage':'cap_monitor_after','scope':dict(updated) if updated else None,
                            'accepted_versions_unchanged':True}),flush=True)
            print(json.dumps({'stage':'scope_monitor_outcome','result':result,
                'financial_queue_claims':0,'financial_worker_started':False}),flush=True)
        else:
            def audited_client(conn, **options):
                client=SharedScreenerClient(conn,**options)
                proof_path=Path('/selected/session-check.json')
                if proof_path.exists():
                    from scrapper.config import SCREENER_COOKIE_FILE
                    from scrapper.core.http import load_screener_cookies
                    proof=json.loads(proof_path.read_text())
                    require(hmac.new(bytes.fromhex(proof['nonce']),SCREENER_COOKIE_FILE.read_bytes(),hashlib.sha256).hexdigest()==proof['file_hmac'],
                            'Worker session mount differs from the updated host session')
                    cookie=load_screener_cookies(SCREENER_COOKIE_FILE).get('sessionid')
                    require(cookie and client.session.cookies.get('sessionid')==cookie and client.transport.cookies.get('sessionid')==cookie,
                            'Worker transport does not carry the updated authenticated session')
                    print(json.dumps({'stage':'worker_session_verified','configured_session_file':str(SCREENER_COOKIE_FILE),
                        'updated_host_file_matches':True,'canonical_client_cookie_matches':True,
                        'shared_transport_cookie_matches':True,'token_values_logged':False}),flush=True)
                original=client.transport.get
                def observed_get(url, **kwargs):
                    print(json.dumps({'stage':'screener_request_started','url':url,
                        'at_utc':datetime.now(timezone.utc).isoformat()}),flush=True)
                    try:
                        response=original(url,**kwargs)
                    except Exception as exc:
                        from scrapper.workflows.screener.company_worker import _safe_error
                        print(json.dumps({'stage':'screener_request_failed','url':url,
                            'error':_safe_error(exc)}),flush=True)
                        raise
                    print(json.dumps({'stage':'screener_response_received','requested_url':url,
                        'final_url':response.url,'status_code':response.status_code,
                        'content_type':response.headers.get('Content-Type')}),flush=True)
                    return response
                client.transport.get=observed_get
                original_quick=client.fetch_quick_ratios
                def checked_quick(url,referer):
                    response=original_quick(url,referer)
                    if response.status_code in (401,403,429,302) or response.fetch_status=='rejected':
                        from scrapper.config import SNAPSHOTS
                        client.preserve_partial(snapshot_root=SNAPSHOTS,now_utc=datetime.now(timezone.utc).isoformat())
                        raise RuntimeError('Screener login/session or access rejection for required quick ratios: status='+str(response.status_code))
                    if response.fetch_status=='ok':
                        from scrapper.sources.screener.metrics import parse_quick_ratios_fragment
                        fields,metrics=parse_quick_ratios_fragment(response.content)
                        if not fields or not metrics or not any(m.value_decimal is not None for m in metrics):
                            from scrapper.config import SNAPSHOTS
                            client.preserve_partial(snapshot_root=SNAPSHOTS,now_utc=datetime.now(timezone.utc).isoformat())
                            raise RuntimeError('Required quick-ratio content is absent or unparseable; stop for authenticated source review')
                    return response
                client.fetch_quick_ratios=checked_quick
                original_company=client.fetch_company
                def mapped_company(url):
                    from urllib.parse import urlsplit
                    from financial_priority import verified_source_target
                    from scrapper.sources.screener.urls import company_path_url
                    target=verified_source_target(conn,urlsplit(url).path)
                    if target is None:return original_company(url)
                    mapped_url=company_path_url(target[0])[0]
                    if mapped_url!=url:
                        print(json.dumps({'stage':'verified_source_alias','logical_url':url,
                            'source_request_url':mapped_url,'source_company_id':target[1]}),flush=True)
                    response=original_company(mapped_url)
                    if response.fetch_status=='ok':
                        from scrapper.core.collection_scope import scope_snapshot
                        require(str(scope_snapshot(response.content)['source_company_id'])==target[1],
                            'Screener source identity mismatch for verified queue alias')
                    return response
                client.fetch_company=mapped_company
                return client
            from financial_priority import skip_accepted
            result = run_screener_company_worker(due=True, limit=len(plan['queue_item_ids']), db_path=db,
                now_utc=worker_now,
                shared_permits=True, shared_client_factory=audited_client,
                skip_if_already_accepted=skip_accepted,
                claim_due_companies=lambda conn, **options: selected_claim(conn, plan=plan, **options))
            print(json.dumps({'stage':'financial_batch_outcome','result':result}, default=str), flush=True)
        with closing(read_connection(db)) as c:
            c.row_factory=sqlite3.Row
            require([dict(r) for r in c.execute('SELECT * FROM exchange_source_access_events ORDER BY event_id')] == holds,
                    'Exchange API holds changed during financial processing')
            delta=c.execute('SELECT count(*) FROM companies').fetchone()[0]-company_count
            require(0<=delta<=len(plan['queue_item_ids']) if plan.get('allow_new_companies') else delta==0,
                    'Unexpected company registry growth')
            if is_cap:
                require(c.execute('SELECT count(*) FROM accepted_page_versions').fetchone()[0] == acceptance_count,
                        'Market-cap check changed accepted financial versions')
        if is_cap:
            fatal=('HTTP 401','HTTP 403','HTTP 429','HTTP 200, rejected','HTTP 302',
                'lease was lost','identity mismatch')
            return 1 if any(any(marker in row.get('error','') for marker in fatal) for row in result['outcomes']) else 0
        if plan.get('scheduled'):
            failures=[row for row in result['outcomes'] if row['status'].startswith('failed_')]
            def fatal_failure(row):
                message=row.get('error',{}).get('message','').lower()
                return row.get('stop_host',False) or any(marker in message for marker in
                    ('identity','lease','manifest','classification=rejected','status=302',
                     'required quick-ratio','blocking_rules='))
            # Ordinary company failures remain fenced in the canonical queue's
            # terminal/backoff policy; access/validation/ownership failures halt.
            return 1 if any(fatal_failure(row) for row in failures) else 0
        failed = result['summary']['outcome_counts']
        return 1 if any(name.startswith('failed_') and count for name,count in failed.items()) else 0


def entrypoint():
    try:
        return main()
    except (Exception, DeadlineExceeded) as exc:
        from scrapper.db.document_work_claims import OwnershipLost
        from acquisition_failures import classify_exception
        deferred = isinstance(exc, (OwnershipLost, DeadlineExceeded)) or classify_exception(exc)[1] == 75
        # The worker has already unwound, fenced persistence and released only
        # its own remaining claims. Consume this slot; let a later bounded slot
        # acquire fresh claims instead of disabling the entire lane forever.
        print(json.dumps({'stage':'claim_deferred' if isinstance(exc, OwnershipLost) else 'execution_failure' if deferred else 'stop',
            'error':str(exc),'automatic_retry':False}),flush=True)
        return 75 if deferred else 2


if __name__ == '__main__':
    def deadline(*_):
        raise DeadlineExceeded('600-second selected financial deadline; retain partial evidence and stop')
    signal.signal(signal.SIGALRM,deadline)
    signal.alarm(600)
    sys.exit(entrypoint())
