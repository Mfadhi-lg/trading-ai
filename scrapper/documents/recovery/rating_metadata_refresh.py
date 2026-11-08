"""Reusable rating metadata refresh with verified identity and frozen URL evidence.

Refresh promotes metadata only through existing persistence; it never downloads
or validates a replacement, and never accepts financial observations.
"""
import json
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from ...core.http import ScreenerHostClient
from ...db.database import connect
from ...db.repo import (
    finish_run, get_or_create_parse_artifact, record_fetch, start_run,
    store_blob, utc_now,
)
from ..discovery.promotion import promote_document_links
from ...sources.screener.company import _parse_documents, _parse_identity_and_quote

from ...db.documents.recovery.rating_metadata_refresh import (
    selected_targets as list_selected_targets, old_jobs, attach_fetch_identity, persist_pending_links,
)

def normalized(url):
    return unquote(url or '').strip()


def run_refresh(*, recovery_run, failed_hosts, current_hosts, db_path, snapshots, report, save_report, _hooks=None):
    RECOVERY_RUN, FAILED_HOSTS, CURRENT_HOSTS = recovery_run, failed_hosts, current_hosts
    DB_PATH, SNAPSHOTS, REPORT = db_path, snapshots, report
    connect = getattr(_hooks, "connect", globals()["connect"])
    utc_now = getattr(_hooks, "utc_now", globals()["utc_now"])
    ScreenerHostClient = getattr(_hooks, "ScreenerHostClient", globals()["ScreenerHostClient"])
    start_run = getattr(_hooks, "start_run", globals()["start_run"])
    store_blob = getattr(_hooks, "store_blob", globals()["store_blob"])
    record_fetch = getattr(_hooks, "record_fetch", globals()["record_fetch"])
    BeautifulSoup = getattr(_hooks, "BeautifulSoup", globals()["BeautifulSoup"])
    _parse_identity_and_quote = getattr(_hooks, "_parse_identity_and_quote", globals()["_parse_identity_and_quote"])
    _parse_documents = getattr(_hooks, "_parse_documents", globals()["_parse_documents"])
    get_or_create_parse_artifact = getattr(_hooks, "get_or_create_parse_artifact", globals()["get_or_create_parse_artifact"])
    promote_document_links = getattr(_hooks, "promote_document_links", globals()["promote_document_links"])
    finish_run = getattr(_hooks, "finish_run", globals()["finish_run"])
    normalized = getattr(_hooks, "normalized", globals()["normalized"])
    selected_targets = getattr(_hooks, "selected_targets", lambda conn: list_selected_targets(conn, recovery_run=RECOVERY_RUN, failed_hosts=FAILED_HOSTS))
    conn = connect(DB_PATH)
    targets = selected_targets(conn)
    # Freeze selection and each company's old URLs before the first fetch.
    for target in targets:
        target['old_jobs'] = old_jobs(conn, recovery_run=RECOVERY_RUN, company_id=target['company_id'], failed_hosts=FAILED_HOSTS)
    payload = {'started_at_utc': utc_now(), 'recovery_run': RECOVERY_RUN,
               'target_count': len(targets), 'targets': targets, 'results': []}
    save_report(payload)
    print(json.dumps({'selected_companies': len(targets),
                      'failed_links': sum(len(t['old_jobs']) for t in targets)}), flush=True)
    client = ScreenerHostClient()
    try:
        for position, target in enumerate(targets, 1):
            result = {'company_id': target['company_id'],
                      'company_name': target['company_name']}
            run_id = start_run(conn, 'rating-link-refresh-1.0.0', {
                'source': 'screener', 'purpose': 'failed_agency_link_refresh',
                'company_id': target['company_id'], 'recovery_run': RECOVERY_RUN,
            })
            try:
                url = target['canonical_url']
                if urlsplit(url).hostname != 'www.screener.in':
                    raise ValueError('Unexpected mapped source host')
                response = client.fetch_company(url)
                now = utc_now()
                symbol = target['source_symbol'] or str(target['company_id'])
                scope_hint = 'consolidated' if '/consolidated/' in url else 'standalone'
                blob_id = store_blob(conn, response, symbol, scope_hint, now, SNAPSHOTS)
                record_fetch(conn, run_id, None, response, scope_hint, now, blob_id)
                if response.fetch_status != 'ok':
                    raise ValueError(f'Source rejected: HTTP {response.status_code}, {response.fetch_status}')
                soup = BeautifulSoup(response.content, 'html.parser')
                identity = _parse_identity_and_quote(soup, now)
                if str(identity[4]) != str(target['source_company_id']):
                    raise ValueError('Source company ID differs from verified mapping')
                scope = identity[3]
                attach_fetch_identity(conn, company_id=target['company_id'], scope=scope, run_id=run_id, blob_id=blob_id)
                _, _, links = _parse_documents(soup)
                links = [l for l in links if l.document_type == 'credit_rating'
                         and urlsplit(l.source_url or '').hostname in CURRENT_HOSTS]
                parse_id, status = get_or_create_parse_artifact(
                    conn, blob_id, 'screener_failed_agency_rating_links', '1.0.0', '1.0.0')
                if status == 'pending':
                    persist_pending_links(conn, blob_id=blob_id, parse_id=parse_id, scope=scope, links=links)
                elif status != 'ok':
                    raise ValueError(f'Existing metadata parse has status {status}')
                promotion = promote_document_links(conn, document_type='credit_rating',
                    parse_id=parse_id, verified_company_id=target['company_id'])
                current_urls = {normalized(l.source_url) for l in links}
                result.update(status='refreshed', parse_id=parse_id, blob_id=blob_id,
                    source_url=response.final_url,
                    current_links=[{'date': l.period_label_raw, 'publisher': l.publisher,
                                    'url': l.source_url} for l in links],
                    old_links_still_present=sum(normalized(j['requested_url']) in current_urls
                                                for j in target['old_jobs']),
                    old_links_no_longer_listed=sum(normalized(j['requested_url']) not in current_urls
                                                   for j in target['old_jobs']),
                    promotion=promotion)
                finish_run(conn, run_id, 'ok')
            except Exception as error:
                conn.rollback()
                result.update(status='failed', error=f'{type(error).__name__}: {error}')
                finish_run(conn, run_id, 'error')
            payload['results'].append(result)
            save_report(payload)
            print(json.dumps({'progress': f'{position}/{len(targets)}',
                'company': target['company_name'], 'status': result['status'],
                'links': len(result.get('current_links', [])), 'error': result.get('error')}), flush=True)
            # Stop a rejected authenticated session rather than repeating it.
            if result.get('error', '').startswith('ValueError: Source rejected: HTTP') and response.status_code in (401,403,429):
                break
        payload['finished_at_utc'] = utc_now()
        payload['summary'] = dict(Counter(r['status'] for r in payload['results']))
        payload['summary'].update(
            selected_companies=len(targets), processed_companies=len(payload['results']),
            current_rating_links=sum(len(r.get('current_links', [])) for r in payload['results']),
            old_links_still_present=sum(r.get('old_links_still_present', 0) for r in payload['results']),
            old_links_no_longer_listed=sum(r.get('old_links_no_longer_listed', 0) for r in payload['results']))
        save_report(payload)
        print(json.dumps({'summary': payload['summary'], 'report': str(REPORT)}), flush=True)
    finally:
        client.session.close()
        conn.close()
