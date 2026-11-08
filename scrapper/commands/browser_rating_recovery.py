"""Finite, audited browser recovery of previously unavailable rating reports.

Normal automated host policy is unchanged. Seeding and running this separate
operator-directed acquisition requires an explicit --operator-authorized flag.
The database ledger is the resume boundary, and publisher HTML is retained with
its PDF derivative distinguished from publisher-original PDF downloads.
"""
from __future__ import annotations

from scrapper.core.attempts import claim_attempt, retry_remaining, retry_delay, BROWSER_MAX_INVOCATIONS

import argparse
import base64
import io
import json
from scrapper.db.document_work_claims import OwnershipLost
from scrapper.operations.workers.renewal import queue_renewal
import secrets
import os
import re
import socket
import signal
import threading
import sys
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

STOP = threading.Event()


def worker_state(conn, run_id, owner, status):
    return _ledger.worker_state(conn, run_id, owner, status, utc_now=utc_now)


def release_owned_jobs(conn, run_id, owner):
    return _ledger.release_owned_jobs(conn, run_id, owner, utc_now=utc_now)

from bs4 import BeautifulSoup
from playwright.sync_api import Error, TimeoutError, sync_playwright
from pypdf import PdfReader

from ..documents.recovery.browser_rating import bridge as _bridge
from ..documents.recovery.browser_rating import ledger as _ledger
from ..documents.recovery.browser_rating import validation as _validation
from ..documents.recovery.browser_rating import persistence as _persistence
from ..documents.recovery.browser_rating import processing as _processing
from ..documents.recovery.browser_rating.errors import PublisherRateLimit, PublisherFileUnavailable
from ..sources.documents import browser_rating as _publisher
from ..documents.recovery.browser_rating.ledger import AGENCY_HOSTS, SETTLED

from ..config import DB_PATH
from ..db.database import connect, init_db
from ..db.document_download_repo import persist_download_result, store_response_blob
from ..db.html_document_rendering_repo import render_and_persist_html_pdf
from ..db.repo import utc_now
from ..documents.credit_rating_resolver import is_genuine_html_credit_report
from ..documents.downloader import DownloadPolicy, DownloadResult
from ..documents.html_pdf import HTML_PDF_RENDERER_VERSION
from ..documents.rating_identity import company_names, match_company, name_key
from ..documents.store import assert_document_store_ready







def transient_error(error):
    return isinstance(error, TimeoutError) or any(marker in str(error) for marker in (
        'ERR_NAME_NOT_RESOLVED', 'ERR_EMPTY_RESPONSE', 'ERR_ABORTED',
        'ERR_CONNECTION_RESET', 'HTTP 502', 'HTTP 503', 'HTTP 504',
    ))


def retry_delay(value, consecutive=1):
    return _ledger.retry_delay(value, consecutive, datetime=datetime, parsedate_to_datetime=parsedate_to_datetime)


def cool_down(conn, run_id, host, retry_after=None):
    return _ledger.cool_down(conn, run_id, host, retry_after, utc_now=utc_now, datetime=datetime, retry_delay=retry_delay)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def seed(conn, run_id):
    return _ledger.seed(conn, run_id, utc_now=utc_now)


def report(conn, run_id):
    return _ledger.report(conn, run_id)


def claim(conn, run_id, hosts, owner, job_ids=()):
    return _ledger.claim(conn, run_id, hosts, owner, job_ids, utc_now=utc_now, datetime=datetime)


def is_india_rating_commentary(body, final_url):
    return _validation.is_india_rating_commentary(body, final_url)


def validate_report(body, media_type, final_url, company_name, aliases=()):
    return _validation.validate_report(
        body, media_type, final_url, company_name, aliases,
        PdfReader=PdfReader, is_genuine_html_credit_report=is_genuine_html_credit_report,
        is_india_rating_commentary=is_india_rating_commentary, match_company=match_company)


def fetch_report(browser, url, company_name, timeout_ms=120000, aliases=()):
    return _publisher.fetch_report(
        browser, url, company_name, timeout_ms, aliases,
        validate_report=validate_report, is_genuine_html_credit_report=is_genuine_html_credit_report,
        is_india_rating_commentary=is_india_rating_commentary,
        DownloadPolicy=DownloadPolicy, DownloadResult=DownloadResult,
        time=time, Error=Error, TimeoutError=TimeoutError)


def _shared_fetch_report(browser, url, company_name, timeout_ms=120000, aliases=(), *, shared_requests):
    return _publisher.fetch_report(
        browser, url, company_name, timeout_ms, aliases,
        validate_report=validate_report, is_genuine_html_credit_report=is_genuine_html_credit_report,
        is_india_rating_commentary=is_india_rating_commentary,
        DownloadPolicy=DownloadPolicy, DownloadResult=DownloadResult,
        time=time, Error=Error, TimeoutError=TimeoutError,shared_requests=shared_requests)


def import_report(conn, job, result, evidence, run_id):
    return _persistence.import_report(conn, job, result, evidence, run_id,
        assert_document_store_ready=assert_document_store_ready, utc_now=utc_now,
        store_response_blob=store_response_blob, persist_download_result=persist_download_result,
        DownloadPolicy=DownloadPolicy, datetime=datetime,
        render_and_persist_html_pdf=render_and_persist_html_pdf,
        HTML_PDF_RENDERER_VERSION=HTML_PDF_RENDERER_VERSION)


def finish(conn, job, status, content_id=None, final_url=None, error=None, evidence=None):
    return _ledger.finish(conn, job, status, content_id, final_url, error, evidence, utc_now=utc_now)


def process_reports(conn, args, owner):
    return _processing.process_reports(conn, args, owner,
        STOP=STOP, worker_state=worker_state, emit=emit, utc_now=utc_now)


def repair_pdf_derivatives(conn, args):
    return _processing.repair_pdf_derivatives(conn, args, emit=emit, utc_now=utc_now,
        assert_document_store_ready=assert_document_store_ready,
        render_and_persist_html_pdf=render_and_persist_html_pdf,
        HTML_PDF_RENDERER_VERSION=HTML_PDF_RENDERER_VERSION)


def serve_bridge(conn, args):
    return _serve_bridge(conn, args)


def _serve_bridge(conn, args):
    return _serve_observed_bridge(conn, args)


def _serve_observed_bridge(conn, args, lifecycle=None, *, shared_permits=False, authorization=None):
    return _bridge.serve(conn, args, lines=sys.stdin, emit=emit,
        worker_state=worker_state, release_owned_jobs=release_owned_jobs,
        claim=claim, cool_down=cool_down, finish=finish,
        validate_report=validate_report, company_names=company_names,
        import_report=import_report, DownloadPolicy=DownloadPolicy,
        DownloadResult=DownloadResult, utc_now=utc_now,
        renewal_factory=lambda c, j: queue_renewal(c, "browser", j, seconds=900, now=utc_now),
        observe=lifecycle.beat if lifecycle is not None else None,
        shared_permits=shared_permits,authorization=authorization)


def main(*, shared_permits=False, authorization=None):
    from ..operations.workers.lifecycle import WorkerLifecycle, stderr_heartbeat
    STOP.clear()
    with WorkerLifecycle(event=STOP, observer=stderr_heartbeat).signals() as lifecycle:
        return _main(lifecycle,shared_permits=shared_permits,authorization=authorization)


def _main(lifecycle, *, shared_permits=False, authorization=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DB_PATH)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--seed', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--process', action='store_true', help='Extract and normalize imported reports')
    parser.add_argument('--repair-pdfs', action='store_true', help='Version corrected HTML PDF derivatives')
    parser.add_argument('--bridge', action='store_true', help='Serve the visible-browser JSON bridge')
    parser.add_argument('--follow', action='store_true', help='Wait for this run to finish downloading')
    parser.add_argument('--operator-authorized', action='store_true')
    parser.add_argument('--host', action='append', default=[])
    parser.add_argument('--job-id', action='append', type=int, default=[], help='Retry only these ledger jobs')
    parser.add_argument('--limit', type=int, default=0, help='0 drains the finite run')
    parser.add_argument('--throttle-seconds', type=float, default=4)
    args = parser.parse_args()
    if (args.apply or args.seed or args.process or args.bridge or args.repair_pdfs) and not args.operator_authorized:
        parser.error('This separate operator-directed run requires --operator-authorized')
    if args.limit < 0 or args.throttle_seconds < 4:
        parser.error('Limit must be nonnegative and host delay at least four seconds')
    if any(h not in AGENCY_HOSTS for h in args.host):
        parser.error('Only known rating-agency hosts are accepted')
    conn = connect(args.db)
    init_db(conn)
    broker = None
    if shared_permits:
        from ..documents.recovery.browser_rating.host_permit_bridge import Broker
        from ..operations.workers.browser_requests import BrowserRequests, AdministrativeDefer
        from ..db.browser_host_defer import assert_unexpired
        try:
            broker = Broker(conn,args.run_id,authorization)
            if args.host and any(not authorization.covers(h,time.time()) for h in args.host):
                raise ValueError('Requested browser hosts exceed the trusted finite receipt')
            args.host = args.host or [h for h in AGENCY_HOSTS if authorization.covers(h,time.time())]
            if not args.host: raise ValueError('No authorized rating publisher hosts')
        except BaseException:
            conn.close()
            raise
    if args.bridge:
        from ..operations.workers.health import bind_role_health
        bind_role_health(lifecycle, conn, 'browser', dependencies={
            'document_store': lambda: bool(assert_document_store_ready()),
            'bridge_streams': lambda: not sys.stdin.closed and not sys.stdout.closed})
        _serve_observed_bridge(conn, args, lifecycle=lifecycle,shared_permits=shared_permits,authorization=authorization)
        conn.close()
        return
    if args.seed:
        emit({'run_id': args.run_id, 'seeded': seed(conn, args.run_id)})
    emit({'run_id': args.run_id, 'counts': report(conn, args.run_id)})
    if args.repair_pdfs:
        repair_pdf_derivatives(conn, args)
        conn.close()
        return
    if args.process:
        owner = f'{socket.gethostname()}:{os.getpid()}:extract'
        try:
            process_reports(conn, args, owner)
        finally:
            worker_state(conn, args.run_id, owner, 'STOPPED')
            conn.close()
        return
    if not args.apply:
        return
    assert_document_store_ready()
    owner = f'{socket.gethostname()}:{os.getpid()}'
    owner = f'{owner}:{secrets.token_hex(16)}'
    # Container restart reuses its hostname/PID; the previous process is gone.
    release_owned_jobs(conn, args.run_id, owner)
    count = 0
    last_host_at = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            from ..operations.workers.health import bind_role_health
            bind_role_health(lifecycle, conn, 'browser', dependencies={
                'browser': browser.is_connected,
                'document_store': lambda: bool(assert_document_store_ready())})
            while not STOP.is_set() and (not args.limit or count < args.limit):
                lifecycle.beat("READY")
                if broker is not None: broker.setup()
                worker_state(conn, args.run_id, owner, 'DOWNLOADING')
                job = claim(conn, args.run_id, args.host, owner, args.job_id)
                if job is None:
                    host_clause = f" AND job.target_host IN ({','.join('?' for _ in args.host)})" if args.host else ''
                    blocked = conn.execute(f'''SELECT min(health.blocked_until_utc) FROM browser_rating_recovery_jobs job
                      JOIN browser_rating_recovery_hosts health USING(run_id,target_host)
                      WHERE job.run_id=? AND job.status='PENDING' {host_clause}
                      AND health.blocked_until_utc > ?''', (args.run_id, *args.host, utc_now())).fetchone()[0]
                    if blocked:
                        emit({'run_id': args.run_id, 'cooldown_until_utc': blocked, 'hosts': args.host})
                        seconds = (datetime.fromisoformat(blocked) - datetime.now(timezone.utc)).total_seconds()
                        worker_state(conn, args.run_id, owner, 'WAITING')
                        lifecycle.wait(max(1, min(60, seconds)), state="COOLDOWN")
                        continue
                    break
                lifecycle.claimed(job['browser_job_id'])
                with queue_renewal(conn, "browser", job, seconds=900, now=utc_now) as renewal:
                    evidence = {}
                    shared_requests = None
                    content_id = final_url = error = None
                    try:
                        existing = conn.execute('SELECT content_id FROM artifact_contents WHERE artifact_id=? LIMIT 1', (job['artifact_id'],)).fetchone()
                        if existing:
                            status, content_id = 'SKIPPED_EXISTING', existing['content_id']
                        else:
                            # Storage is checked before requesting, as well as before importing.
                            assert_document_store_ready()
                            wait = last_host_at.get(job['target_host'], 0) + args.throttle_seconds - time.monotonic()
                            if wait > 0:
                                worker_state(conn, args.run_id, owner, 'THROTTLED')
                                if lifecycle.wait(wait, state="THROTTLED"):
                                    release_owned_jobs(conn, args.run_id, owner)
                                    break
                            lifecycle.beat("WORKING")
                            worker_state(conn, args.run_id, owner, 'DOWNLOADING')
                            if broker is not None:
                                def exchange(request):
                                    renewal.check(conn)
                                    from ..db.documents.recovery.browser_rating_bridge import owned_job
                                    current = owned_job(conn,job['browser_job_id'],args.run_id,owner)
                                    if current is None or current['claim_token'] != job['claim_token']:
                                        raise OwnershipLost('Browser claim was replaced')
                                    assert_unexpired(current,utc_now())
                                    return broker.handle(request,job)
                                shared_requests = BrowserRequests(exchange,job,STOP,authorization=authorization)
                            fetcher = _shared_fetch_report if shared_requests is not None else fetch_report
                            result, evidence = fetcher(browser, job['requested_url'], job['company_name'],
                                aliases=company_names(conn, job['artifact_id']),
                                **({'shared_requests':shared_requests} if shared_requests is not None else {}))
                            last_host_at[job['target_host']] = time.monotonic()
                            final_url = result.final_url
                            renewal.check(conn)
                            status, content_id, evidence = import_report(conn, job, result, evidence, args.run_id)
                    except PublisherRateLimit as exc:
                        until = cool_down(conn, args.run_id, job['target_host'], exc.retry_after)
                        status, error = 'PENDING', str(exc)
                        evidence = {'retry_after': exc.retry_after, 'cooldown_until_utc': until}
                    except OwnershipLost:
                        raise
                    except Exception as exc:
                        if shared_requests is not None and isinstance(exc,AdministrativeDefer):
                            deferred = shared_requests.defer(exc)
                            emit(dict(deferred,run_id=args.run_id,browser_job_id=job['browser_job_id']))
                            count += 1
                            lifecycle.finished()
                            continue
                        last_host_at[job['target_host']] = time.monotonic()
                        error = f'{type(exc).__name__}: {exc}'[:3000]
                        status = 'REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in error else 'FAILED'
                        if isinstance(exc, PublisherFileUnavailable):
                            evidence['resolution'] = 'PUBLISHER_SOURCE_UNAVAILABLE'
                            evidence['resolution_confirmed_at_utc'] = utc_now()
                        elif transient_error(exc) and retry_remaining(claim_attempt(job, 'browser'), BROWSER_MAX_INVOCATIONS):
                            status = 'PENDING'
                        # A storage failure stops the batch instead of failing every source.
                        if isinstance(exc, RuntimeError) and 'store' in str(exc).lower():
                            renewal.check(conn)
                            finish(conn, job, 'PENDING', error=error)
                            raise
                    if shared_requests is not None:
                        shared_requests.close()
                        broker.assert_drained(job)
                    renewal.check(conn)
                    finish(conn, job, status, content_id, final_url, error, evidence)
                    emit({'run_id': args.run_id, 'browser_job_id': job['browser_job_id'],
                          'artifact_id': job['artifact_id'], 'host': job['target_host'],
                          'status': status, 'content_id': content_id, 'error': error,
                          'byte_size': evidence.get('byte_size'), 'pdf_derivative_error': evidence.get('pdf_derivative_error')})
                count += 1
                lifecycle.finished()
        finally:
            browser.close()
            release_owned_jobs(conn, args.run_id, owner)
            worker_state(conn, args.run_id, owner, 'STOPPED')
            conn.close()


if __name__ == '__main__':
    main()
