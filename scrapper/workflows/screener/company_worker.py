"""Bounded company-quarter workflow with unchanged queue policy."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from ... import __version__
from ...config import (
    DB_PATH,
    EXPORTS,
    RESULT_SCREEN_PARSER_NAME,
    RESULT_SCREEN_PARSER_VERSION,
    SCREENER_BASE,
    SNAPSHOTS,
)
from ...core.log import get_logger, log_event
from ...core.http import FetchResponse, ScreenerHostClient, fetch_screener_result_screen
from ...db.database import connect, init_db
from ...db.repo import finish_run, record_fetch, start_run, store_blob, utc_now
from ...db.screener_discovery_repo import (
    ClaimedCompany,
    claim_due_companies,
    complete_company,
    enqueue_screen_candidates,
    fail_company,
    mark_running,
    persist_screen,
    persist_rejected_screen,
    preview_due_companies,
    preview_incomplete_metric_candidates,
    preview_requeue_candidates,
    release_claims,
    requeue_failed_companies,
    requeue_incomplete_metric_companies,
    skip_if_already_accepted,
)
from ...sources.screener.result_screen import canonical_query, parse_result_screen
from .ingest import VerticalSliceResult, scrape_screener_company_path


DISCOVERY_SCHEMA_VERSION = "1.0.0"

def _safe_error(exc: Exception, *, re=re) -> dict[str, Any]:
    message = str(exc)
    message = re.sub('(?i)(sessionid|csrftoken|cookie)\\s*[=:]\\s*[^\\s;,]+', '\\1=<redacted>', message)
    return {'type': type(exc).__name__, 'message': message[:2000], **{name: getattr(exc, name) for name in ('market_cap_crore', 'floor_crore', 'blob_id') if hasattr(exc, name)}}

def _failure_policy(exc: Exception, origin: str='screener_screen') -> tuple[bool, bool]:
    message = str(exc).lower()
    stop_host = any((marker in message for marker in ('status=401', 'status=403', 'status=429', 'captcha', 'login', 'access-denied')))
    terminal_markers = ['status=404', 'description is missing', 'profile carries no bse or nse identifier', 'headers are missing', 'table has no observations', 'not above screener market-cap scope floor', 'below screener market-cap scope floor', 'screen result period is absent', 'reporting scope differs', 'identity', 'manifest', 'failed artifact']
    if origin == 'exchange_filing':
        terminal_markers.remove('screen result period is absent')
    terminal = any((marker in message for marker in terminal_markers))
    return (not terminal, stop_host)

def run_screener_company_worker(*, due: bool, limit: int, dry_run: bool=False, db_path: Path=DB_PATH, snapshot_root: Path=SNAPSHOTS, export_root: Path=EXPORTS, worker_id: str | None=None, now_utc: Callable[[], str]=utc_now, scrape: Callable[..., VerticalSliceResult]=scrape_screener_company_path, host_client: ScreenerHostClient | None=None, shared_permits: bool=False, shared_client_factory=None, Any=Any, ClaimedCompany=ClaimedCompany, Counter=Counter, DISCOVERY_SCHEMA_VERSION=DISCOVERY_SCHEMA_VERSION, ScreenerHostClient=ScreenerHostClient, __name__='scrapper.pipeline.screener_discovery', _failure_policy=_failure_policy, _safe_error=_safe_error, claim_due_companies=claim_due_companies, complete_company=complete_company, connect=connect, fail_company=fail_company, get_logger=get_logger, init_db=init_db, log_event=log_event, mark_running=mark_running, preview_due_companies=preview_due_companies, release_claims=release_claims, scrape_screener_company_path=scrape_screener_company_path, skip_if_already_accepted=skip_if_already_accepted, sqlite3=sqlite3, uuid=uuid) -> dict[str, Any]:
    if not due:
        raise ValueError('Screener company worker currently requires --due')
    if limit < 1:
        raise ValueError('limit must be positive')
    checked_at = now_utc()
    worker_id = worker_id or f'screener-worker-{uuid.uuid4().hex}'
    logger = get_logger(__name__)
    if dry_run:
        due_rows: tuple[ClaimedCompany, ...] = ()
        if db_path.exists():
            readonly = sqlite3.connect(f'file:{db_path.resolve()}?mode=ro', uri=True)
            readonly.row_factory = sqlite3.Row
            try:
                due_rows = preview_due_companies(readonly, now_utc=checked_at, limit=limit)
            finally:
                readonly.close()
        return {'schema_version': DISCOVERY_SCHEMA_VERSION, 'mode': 'dry_run', 'worker_id': None, 'checked_at_utc': checked_at, 'summary': {'due_count': len(due_rows), 'claimed_count': 0, 'outcome_counts': {}}, 'due': [row.to_dict() | {'claim_token': None} for row in due_rows], 'outcomes': []}
    conn = connect(db_path)
    claimed: tuple[ClaimedCompany, ...] = ()
    completed: set[int] = set()
    outcomes: list[dict[str, Any]] = []
    try:
        init_db(conn)
        if shared_permits:
            if shared_client_factory is None:
                raise ValueError("Shared execution requires an explicit transport factory")
            if host_client is not None or scrape is not scrape_screener_company_path:
                raise ValueError('Shared Screener execution requires the canonical transport/workflow')
            host_client = shared_client_factory(conn,owner=worker_id)
        if scrape is scrape_screener_company_path and host_client is None:
            host_client = ScreenerHostClient()
        claimed = claim_due_companies(conn, now_utc=checked_at, limit=limit, worker_id=worker_id)
        log_event(logger, 'screener_queue_claimed', worker_id=worker_id, claimed_count=len(claimed), due_at_utc=checked_at)
        stop_host = False
        for position, item in enumerate(claimed):
            if stop_host:
                break
            started_at = now_utc()
            if skip_if_already_accepted(conn, item, worker_id=worker_id, now_utc=started_at):
                completed.add(item.queue_item_id)
                outcomes.append({'queue_item_id': item.queue_item_id, 'status': 'skipped_already_accepted'})
                log_event(logger, 'screener_queue_skipped', worker_id=worker_id, source='screener', host='www.screener.in', queue_item_id=item.queue_item_id, reporting_scope=item.reporting_scope, attempt=item.attempt_count + 1, rejection_code='already_accepted')
                continue
            if shared_permits:
                host_client.bind([('company',item.queue_item_id,item.claim_token)],now_utc)
                host_client.check()
            mark_running(conn, item, worker_id=worker_id, now_utc=started_at)
            try:
                result = scrape(item.company_path_normalized, item.reporting_scope, expected_result_period=item.result_period, db_path=db_path, snapshot_root=snapshot_root, export_root=export_root, **{'host_client': host_client} if host_client is not None else {})
                if shared_permits:
                    host_client.check()
                complete_company(conn, item, parse_id=result.parse_id, company_id=result.company_id, export_path=str(result.export_path.resolve()), worker_id=worker_id, now_utc=now_utc())
                completed.add(item.queue_item_id)
                outcomes.append({'queue_item_id': item.queue_item_id, 'status': 'accepted', 'parse_id': result.parse_id, 'company_id': result.company_id, 'export_path': str(result.export_path.resolve())})
                log_event(logger, 'screener_queue_accepted', worker_id=worker_id, source='screener', host='www.screener.in', queue_item_id=item.queue_item_id, company_id=result.company_id, reporting_scope=item.reporting_scope, attempt=item.attempt_count + 1)
            except Exception as exc:
                from ...core.host_request_contracts import AdministrativeDefer
                from ...db.document_work_claims import OwnershipLost
                if shared_permits and isinstance(exc,OwnershipLost):
                    raise
                if shared_permits and isinstance(exc,AdministrativeDefer):
                    from ...db.screener_host_defer import defer
                    host_client.check()
                    host_client.preserve_partial(snapshot_root=snapshot_root,now_utc=now_utc())
                    defer(conn,'company',item.queue_item_id,item.claim_token,now=now_utc(),retry_at=exc.retry_at,dispatched=host_client.transport.dispatched,reason=exc.reason)
                    completed.add(item.queue_item_id)
                    outcomes.append({'queue_item_id':item.queue_item_id,'status':'administrative_defer','reason':exc.reason,'retry_at':exc.retry_at,'dispatched':host_client.transport.dispatched})
                    continue
                retryable, stop_host = _failure_policy(exc, item.origin)
                error = _safe_error(exc)
                fail_company(conn, item, worker_id=worker_id, now_utc=now_utc(), error=error, retryable=retryable, stop_host=stop_host)
                completed.add(item.queue_item_id)
                outcomes.append({'queue_item_id': item.queue_item_id, 'status': 'failed_retryable' if retryable else 'failed_terminal', 'stop_host': stop_host, 'error': error})
                log_event(logger, 'screener_queue_rejected', worker_id=worker_id, source='screener', host='www.screener.in', queue_item_id=item.queue_item_id, reporting_scope=item.reporting_scope, attempt=item.attempt_count + 1, retryable=retryable, retry_reason=error.get('type'), policy_rejection='standalone' in error.get('message', '').lower())
    finally:
        if shared_permits and host_client is not None:
            host_client.close()
        remaining = tuple((item for item in claimed if item.queue_item_id not in completed))
        if remaining:
            release_claims(conn, remaining, worker_id=worker_id, now_utc=now_utc())
        conn.close()
    counts = Counter((item['status'] for item in outcomes))
    log_event(logger, 'screener_queue_throughput', worker_id=worker_id, source='screener', host='www.screener.in', claimed_count=len(claimed), completed_count=len(outcomes), accepted_count=counts.get('accepted', 0), rejected_count=len(outcomes) - counts.get('accepted', 0))
    return {'schema_version': DISCOVERY_SCHEMA_VERSION, 'mode': 'execute', 'worker_id': worker_id, 'checked_at_utc': checked_at, 'summary': {'due_count': len(claimed), 'claimed_count': len(claimed), 'completed_count': len(outcomes), 'outcome_counts': dict(sorted(counts.items()))}, 'claimed': [item.to_dict() | {'claim_token': '<redacted>'} for item in claimed], 'outcomes': outcomes}
