"""Refresh the existing permitted results screen into the canonical queue.

Runs inside the existing bounded financial owner, using shared host permits and
the existing screen capture/completeness checks. No independent scheduler/queue.
"""
import json
from datetime import datetime, timezone
from scrapper.db.repository_backend import statement

SCREEN_URL = 'https://www.screener.in/screens/3844820/new-results/'
QUERY_ROLE = 'september_3844820'  # Preserve the existing immutable capture identity.
RUN_VERSION = 'financial-screen-refresh-v2'
MAX_PAGES = 39  # Observed pagination ceiling; every capture still needs an explicit grant.
REFRESH_SECONDS = 6 * 3600
RETRY_SECONDS = 3600


class ScreenChanged(ValueError):
    """A changed screen or pagination cannot admit a partial result set."""


def _validate_budget(budget, *, query, day):
    if not isinstance(budget, dict):
        raise ScreenChanged('Explicit financial-screen request budget required')
    if (budget.get('url') != SCREEN_URL or budget.get('query') != query or
            budget.get('day_utc') != day or not isinstance(budget.get('authorization'), str) or
            not budget['authorization'].strip()):
        raise ScreenChanged('Financial-screen budget identity/day/authorization mismatch')
    for key, ceiling in (('max_pages', MAX_PAGES), ('requests_per_capture', MAX_PAGES),
                         ('source_requests_per_day', 148),
                         ('requests_per_day', 148)):
        if type(budget.get(key)) is not int or not 1 <= budget[key] <= ceiling:
            raise ScreenChanged('Invalid bounded financial-screen budget: ' + key)


def _reserve(conn, run, budget, units):
    """Durable conservative reservations; failures and interruptions never refund.

    Serialize the read/check/write on both backends. No transaction spans HTTP.
    The daily ledger includes ALL refresh runs, irrespective of grant/quarter.
    """
    from scrapper.db.repository_backend import transaction, begin_immediate
    with transaction(conn):
        begin_immediate(conn)
        configs = [(row['run_id'], row['started_at_utc'], json.loads(row['config_json'])) for row in
            conn.execute('SELECT run_id,started_at_utc,config_json FROM scrape_runs WHERE code_version=?',
                         (RUN_VERSION,)).fetchall()]
        current = next(config for rid, _, config in configs if rid == run)
        # Prior code did not meter redirect hops. Unknown legacy usage is not
        # free capacity: defer until a fresh UTC day rather than guess units.
        today = [(started, config) for _, started, config in configs
                 if config.get('budget_day_utc', datetime.fromisoformat(started)
                     .astimezone(timezone.utc).date().isoformat()) == budget['day_utc']]
        if any('reserved_requests' not in config for _, config in today):
            raise ScreenChanged('Unmetered legacy screen requests on this UTC day; defer safely')
        used = sum(config['reserved_requests'] for _, config in today)
        if (current.get('reserved_requests', 0) + units > budget['requests_per_capture'] or
                used + units > min(budget['source_requests_per_day'], budget['requests_per_day'])):
            raise ScreenChanged('Financial-screen source/day request budget exhausted; no admission')
        current['reserved_requests'] = current.get('reserved_requests', 0) + units
        conn.execute('UPDATE scrape_runs SET config_json=? WHERE run_id=?',
                     (json.dumps(current), run))


def refresh_if_due(conn, *, now_utc, client_factory=None, snapshot_root=None, budget=None, clock=None):
    from bs4 import BeautifulSoup
    from scrapper.config import (SCREENER_MIN_MARKET_CAP_CRORE, SNAPSHOTS,
        RESULT_SCREEN_PARSER_NAME, RESULT_SCREEN_PARSER_VERSION, REQUEST_TIMEOUT)
    from scrapper.core.http import FetchResponse, _classify_result_screen_response
    from scrapper.db.repo import start_run, finish_run, store_blob, record_fetch
    from scrapper.db.screener_discovery_repo import (persist_screen,
        persist_rejected_screen, enqueue_complete_screen_pages)
    from scrapper.sources.screener.result_screen import canonical_query, parse_result_screen
    from scrapper.sources.exchange.reporting_periods import priority_result_periods
    from scrapper.operations.workers.screener_requests import SharedScreenerClient

    clock = clock or (lambda: datetime.now(timezone.utc))
    instant = datetime.fromisoformat(now_utc)
    period = priority_result_periods(now=instant)[0]
    year, month = map(int, period.split('-'))
    query = f'Last result date >{year:04d}{month-1:02d} AND Market Capitalization >{SCREENER_MIN_MARKET_CAP_CRORE:g}'
    previous = conn.execute(statement(conn, '''SELECT started_at_utc,status,config_json FROM scrape_runs
        WHERE code_version=? AND json_extract(config_json,'$.period')=?
        ORDER BY run_id DESC LIMIT 1''', '''SELECT started_at_utc,status,config_json FROM scrape_runs
        WHERE code_version=? AND config_json::jsonb ->> 'period'=?
        ORDER BY run_id DESC LIMIT 1'''), (RUN_VERSION, period)).fetchone()
    if previous:
        delay = REFRESH_SECONDS if previous['status']=='ok' and json.loads(previous['config_json']).get('capture_complete',True) else RETRY_SECONDS
        bounds_corrected=(previous['status']=='error' and
            json.loads(previous['config_json']).get('max_pages',MAX_PAGES)<MAX_PAGES)
        if not bounds_corrected and (instant-datetime.fromisoformat(previous['started_at_utc'])).total_seconds() < delay:
            return {'status':'not_due', 'source_requests':0, 'period':period}

    day = instant.astimezone(timezone.utc).date().isoformat()
    try:
        _validate_budget(budget, query=query, day=day)
    except ScreenChanged as exc:
        return {'status':'deferred', 'period':period, 'source_requests':0,
                'reason':str(exc), 'queue_admission':False}

    run = start_run(conn, RUN_VERSION, {'mode':'current_result_screen_refresh',
        'period':period, 'query':query, 'url':SCREEN_URL, 'max_pages':MAX_PAGES,
        'refresh_seconds':REFRESH_SECONDS, 'retry_seconds':RETRY_SECONDS,
        'budget_day_utc':day, 'budget':budget, 'reserved_requests':0, 'capture_complete':False})
    def check_day():
        if clock().astimezone(timezone.utc).date().isoformat() != day:
            raise ScreenChanged('Financial-screen budget UTC day expired; no further requests')

    client = None
    ids = []
    requests = 0
    snapshot_root = snapshot_root or SNAPSHOTS
    try:
        _reserve(conn, run, budget, 1)
        from scrapper.documents.download.contracts import DownloadPolicy
        # Exact source identity also bounds each page to one outbound request.
        client = (client_factory or SharedScreenerClient)(conn,
            owner='financial-screen-refresh-'+str(run), wait_seconds=10,
            policy=DownloadPolicy(max_redirects=0, max_bytes=8*1024*1024,
                total_timeout_seconds=REQUEST_TIMEOUT), check=check_day)
        page_count = 1
        for page in range(1, MAX_PAGES+1):
            check_day()
            url = SCREEN_URL if page==1 else SCREEN_URL+'?page='+str(page)
            response = client._get(url)
            requests += 1
            observed = datetime.now(timezone.utc).isoformat()
            value = FetchResponse(url, response.url, response.status_code, response.content,
                response.headers.get('Content-Type'), response.encoding, None, None,
                'authenticated_screen_capture', _classify_result_screen_response(response))
            blob = store_blob(conn, value, '_RESULT_DISCOVERY', QUERY_ROLE, observed,
                snapshot_root, resource_kind='result_screen')
            fetch = record_fetch(conn, run, None, value, 'not_applicable', observed,
                blob, resource_kind='result_screen')
            if hasattr(client, 'completed'):
                client.completed = []  # These bytes now have immutable fetch evidence.
            try:
                if value.fetch_status!='ok' or response.status_code!=200:
                    raise RuntimeError('Screener screen access unavailable: status='+str(response.status_code))
                if response.url != url:
                    raise RuntimeError('Screener screen source identity changed')
                field = BeautifulSoup(response.content,'html.parser').select_one('textarea[name=query]')
                if field is None or canonical_query(field.get_text())!=query:
                    raise ScreenChanged('Saved screen filter differs from the active quarter/floor; no admission')
                parsed = parse_result_screen(response.content, query, url)
                if page==1:
                    page_count = parsed.page_count
                    result_count = parsed.result_count
                if any(r.result_period>period for r in parsed.rows):
                    raise ScreenChanged('Screen contains a future reporting period; no admission')
            except Exception:
                persist_rejected_screen(conn, query_role=QUERY_ROLE, query_raw=query,
                    requested_url=url, fetch_id=fetch, blob_id=blob, observed_at_utc=observed,
                    parser_name=RESULT_SCREEN_PARSER_NAME, parser_version=RESULT_SCREEN_PARSER_VERSION)
                raise
            sid, _ = persist_screen(conn, parsed, query_role=QUERY_ROLE, fetch_id=fetch,
                blob_id=blob, observed_at_utc=observed, parser_name=RESULT_SCREEN_PARSER_NAME,
                parser_version=RESULT_SCREEN_PARSER_VERSION)
            ids.append(sid)
            if (parsed.page_number!=page or parsed.page_count!=page_count or
                    parsed.result_count!=result_count or page_count>budget['max_pages']):
                raise ScreenChanged(f'Screen page {parsed.page_number}/{parsed.page_count} exceeds bound {MAX_PAGES} or changed; no admission')
            if page == 1:
                _reserve(conn, run, budget, page_count - 1)
            print(json.dumps({'stage':'financial_screen_page','page':page,
                'page_count':page_count,'result_count':parsed.result_count,'rows':len(parsed.rows)}),flush=True)
            if page==page_count:
                break
        # Only a complete, stable capture can admit canonical jobs.
        result = enqueue_complete_screen_pages(conn, ids, now_utc=now_utc)
        with conn:
            config = json.loads(conn.execute('SELECT config_json FROM scrape_runs WHERE run_id=?', (run,)).fetchone()[0])
            config['capture_complete'] = result['complete']
            conn.execute('UPDATE scrape_runs SET config_json=? WHERE run_id=?',
                (json.dumps(config), run))
        finish_run(conn, run, 'ok')
        return {'status':'refreshed','period':period,'source_requests':requests,**result}
    except (ScreenChanged, ValueError) as exc:
        conn.rollback()
        finish_run(conn, run, 'error')
        return {'status':'deferred','period':period,'source_requests':requests,
            'reason':str(exc),'queue_admission':False}
    except BaseException:
        conn.rollback()
        try:
            # Redirect/error responses can precede _get returning. Keep original
            # bytes without parsing or admitting any partial response set.
            if client is not None and hasattr(client, 'preserve_partial'):
                client.preserve_partial(snapshot_root=snapshot_root, now_utc=now_utc)
        finally:
            finish_run(conn, run, 'error')
        raise
    finally:
        if client is not None:
            client.close()
