"""Finite, audited browser recovery of previously unavailable rating reports.

Normal automated host policy is unchanged. Seeding and running this separate
operator-directed acquisition requires an explicit --operator-authorized flag.
The database ledger is the resume boundary, and publisher HTML is retained with
its PDF derivative distinguished from publisher-original PDF downloads.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
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
    with conn:
        conn.execute('''INSERT INTO browser_rating_recovery_workers
            (worker_id,run_id,status,heartbeat_at_utc,stopped_at_utc) VALUES(?,?,?,?,?)
            ON CONFLICT(worker_id) DO UPDATE SET status=excluded.status,
            heartbeat_at_utc=excluded.heartbeat_at_utc,stopped_at_utc=excluded.stopped_at_utc''',
            (owner, run_id, status, utc_now(), utc_now() if status == 'STOPPED' else None))


def release_owned_jobs(conn, run_id, owner):
    with conn:
        return conn.execute('''UPDATE browser_rating_recovery_jobs SET status='PENDING',
            lease_owner=NULL,lease_expires_at_utc=NULL,updated_at_utc=?
            WHERE run_id=? AND status='RUNNING' AND lease_owner=?''',
            (utc_now(), run_id, owner)).rowcount

from bs4 import BeautifulSoup
from playwright.sync_api import Error, TimeoutError, sync_playwright
from pypdf import PdfReader

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

AGENCY_HOSTS = (
    'www.icra.in', 'www.crisil.com', 'www.crisilratings.com',
    'www.indiaratings.co.in', 'www.acuite.in', 'connect.acuite.in',
    'www.infomerics.com', 'bcrisp.in', 'www.bcrisp.in',
    'www.brickworkratings.com', 'www.careratings.com',
)
SETTLED = ('SKIPPED_POLICY', 'TERMINAL_FAILURE', 'RETRYABLE_FAILURE', 'INTERACTION_REQUIRED')


class PublisherRateLimit(ValueError):
    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        super().__init__('HTTP 429: publisher rate limit; retained for retry')


class PublisherFileUnavailable(ValueError):
    """The publisher serves a missing-file notice instead of its report."""


def transient_error(error):
    return isinstance(error, TimeoutError) or any(marker in str(error) for marker in (
        'ERR_NAME_NOT_RESOLVED', 'ERR_EMPTY_RESPONSE', 'ERR_ABORTED',
        'ERR_CONNECTION_RESET', 'HTTP 502', 'HTTP 503', 'HTTP 504',
    ))


def retry_delay(value, consecutive=1):
    if value:
        try:
            return max(60, int(value))
        except ValueError:
            try:
                return max(60, int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()))
            except (ValueError, TypeError):
                pass
    return min(6 * 3600, 3600 * 2 ** min(consecutive - 1, 3))


def cool_down(conn, run_id, host, retry_after=None):
    row = conn.execute('SELECT consecutive_blocks FROM browser_rating_recovery_hosts WHERE run_id=? AND target_host=?', (run_id, host)).fetchone()
    consecutive = int(row[0]) + 1 if row else 1
    until = (datetime.now(timezone.utc) + timedelta(seconds=retry_delay(retry_after, consecutive))).isoformat()
    with conn:
        conn.execute('''INSERT INTO browser_rating_recovery_hosts
          (run_id,target_host,blocked_until_utc,last_http_status,consecutive_blocks,reason,updated_at_utc)
          VALUES (?,?,?,429,?,'PUBLISHER_RATE_LIMIT',?)
          ON CONFLICT(run_id,target_host) DO UPDATE SET blocked_until_utc=excluded.blocked_until_utc,
            last_http_status=429,consecutive_blocks=excluded.consecutive_blocks,
            reason=excluded.reason,updated_at_utc=excluded.updated_at_utc''',
          (run_id,host,until,consecutive,utc_now()))
    return until


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def seed(conn, run_id):
    now = utc_now()
    before = conn.total_changes
    with conn:
        conn.execute(f'''INSERT OR IGNORE INTO browser_rating_recovery_jobs
          (run_id,artifact_id,artifact_source_id,requested_url,target_host,
           company_name,created_at_utc,updated_at_utc)
          SELECT ?, a.artifact_id,min(s.artifact_source_id),q.requested_url,
                 q.target_host,c.current_legal_name,?,?
          FROM (
            SELECT artifact_id,requested_url,target_host,status FROM credit_rating_download_queue
            UNION ALL
            SELECT artifact_id,requested_url,target_host,status FROM artifact_download_queue
          ) q JOIN document_artifacts a USING(artifact_id)
          JOIN companies c USING(company_id)
          JOIN artifact_sources s ON s.artifact_id=q.artifact_id AND s.document_url=q.requested_url
          WHERE a.artifact_type='credit_rating'
          AND q.target_host IN ({','.join('?' for _ in AGENCY_HOSTS)})
          AND q.status IN ({','.join('?' for _ in SETTLED)})
          AND NOT EXISTS(SELECT 1 FROM artifact_contents ac WHERE ac.artifact_id=q.artifact_id)
          GROUP BY a.artifact_id,q.requested_url,q.target_host
          ORDER BY coalesce(a.event_date,'') DESC,a.artifact_id DESC''',
          (run_id, now, now, *AGENCY_HOSTS, *SETTLED))
    return conn.total_changes - before


def report(conn, run_id):
    return [dict(r) for r in conn.execute('''SELECT target_host,status,count(*) jobs,
        sum(attempt_count) attempts FROM browser_rating_recovery_jobs
        WHERE run_id=? GROUP BY target_host,status ORDER BY target_host,status''', (run_id,))]


def claim(conn, run_id, hosts, owner, job_ids=()):
    now = utc_now()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    conn.execute('BEGIN IMMEDIATE')
    try:
        conn.execute('''UPDATE browser_rating_recovery_jobs SET status='PENDING',
            lease_owner=NULL,lease_expires_at_utc=NULL WHERE run_id=?
            AND status='RUNNING' AND lease_expires_at_utc < ?''', (run_id, now))
        host_clause = f" AND target_host IN ({','.join('?' for _ in hosts)})" if hosts else ''
        job_clause = f" AND browser_job_id IN ({','.join('?' for _ in job_ids)})" if job_ids else ''
        row = conn.execute(f'''SELECT * FROM browser_rating_recovery_jobs
            WHERE run_id=? AND status='PENDING' {host_clause} {job_clause}
            AND NOT EXISTS(SELECT 1 FROM browser_rating_recovery_hosts health
              WHERE health.run_id=browser_rating_recovery_jobs.run_id
              AND health.target_host=browser_rating_recovery_jobs.target_host
              AND health.blocked_until_utc > ?)
            ORDER BY browser_job_id LIMIT 1''', (run_id, *hosts, *job_ids, now)).fetchone()
        if row:
            conn.execute('''UPDATE browser_rating_recovery_jobs SET status='RUNNING',
                attempt_count=attempt_count+1,lease_owner=?,lease_expires_at_utc=?,
                updated_at_utc=? WHERE browser_job_id=?''',
                (owner, expires, now, row['browser_job_id']))
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise


def is_india_rating_commentary(body, final_url):
    """NRAC updates need not contain a facilities table or rationale heading."""
    if not re.match(r'https://www\.indiaratings\.co\.in/pressrelease/\d+/?$', final_url):
        return False
    soup = BeautifulSoup(body, 'html.parser')
    for element in soup(['script', 'style', 'nav', 'footer']):
        element.decompose()
    text = soup.get_text(' ', strip=True)
    return (len(text) >= 1200 and 'India Ratings and Research' in text
            and bool(re.search(r'\brat(?:ing|ings|ed)\b', text, re.I))
            and bool(re.search(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+20\d{2}', text))
            and not re.search(r'captcha|access denied|you have been blocked', text, re.I))


def validate_report(body, media_type, final_url, company_name, aliases=()):
    if media_type == 'application/pdf':
        if not body.startswith(b'%PDF-'):
            raise ValueError('Not PDF bytes')
        reader = PdfReader(io.BytesIO(body), strict=False)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 500:
            raise ValueError('Encrypted or implausible PDF page count')
        text = '\n'.join(page.extract_text() or '' for page in reader.pages[:5])
        evidence = {'page_count': len(reader.pages), 'text_length': len(text)}
        if len(text.strip()) < 100 and re.search(r'\bno file to view\b', text, re.I):
            raise PublisherFileUnavailable('PUBLISHER_SOURCE_UNAVAILABLE: PDF says NO FILE TO VIEW')
        if len(text.strip()) < 100 or not re.search(r'\brat(?:ing|ings|ed)\b', text, re.I):
            # Preserve the original PDF; OCR is validation evidence, not a replacement file.
            import pdfplumber
            import pytesseract
            ocr_pages = []
            with pdfplumber.open(io.BytesIO(body)) as pdf:
                for page in pdf.pages[:5]:
                    ocr_pages.append(pytesseract.image_to_string(
                        page.to_image(resolution=150).original, lang='eng',
                        config='--psm 6', timeout=60))
            text = '\n'.join(ocr_pages)
            evidence.update(validation_text_origin='ocr', ocr_page_count=len(ocr_pages),
                            text_length=len(text), ocr_engine='tesseract')
            if len(text.strip()) < 100 or not re.search(r'\brat(?:ing|ings|ed)\b', text, re.I):
                raise ValueError('PDF has no usable rating report text after OCR')
    elif media_type == 'text/html':
        genuine, evidence = is_genuine_html_credit_report(body, final_url=final_url)
        if not genuine:
            missing_text = BeautifulSoup(body, 'html.parser').get_text(' ', strip=True)
            if len(missing_text) < 200 and re.search(r'\bfile not found\b', missing_text, re.I):
                raise PublisherFileUnavailable('PUBLISHER_SOURCE_UNAVAILABLE: HTML says File not found')
        if not genuine and not is_india_rating_commentary(body, final_url):
            raise ValueError(f'Not a genuine HTML rating report: {evidence}')
        evidence['india_rating_commentary'] = not genuine
        soup = BeautifulSoup(body, 'html.parser')
        for element in soup(['script', 'style', 'nav', 'footer']):
            element.decompose()
        text = soup.get_text(' ', strip=True)
    else:
        raise ValueError('Unsupported media type')
    match = match_company(text, company_name, aliases)
    evidence['company_name_match'] = bool(match)
    if match:
        evidence['company_identity_match'] = match
    if not match:
        raise ValueError(f'REVIEW_REQUIRED: company name not found: {company_name}')
    return evidence


def fetch_report(browser, url, company_name, timeout_ms=120000, aliases=()):
    context = browser.new_context(accept_downloads=True, service_workers='block')
    if any(host in url for host in ('crisil.com', 'crisilratings.com', 'bcrisp.in')):
        # These are static HTML documents. Avoid re-fetching logos, fonts and
        # scripts for every report; the original HTML remains intact in storage.
        context.route('**/*', lambda route: route.continue_() if route.request.resource_type == 'document' else route.abort())
    page = context.new_page()
    downloads = []
    pdf_responses = []
    context.on('page', lambda popup: popup.on('download', lambda d: downloads.append(d)))
    page.on('download', lambda d: downloads.append(d))
    navigation_url = url.replace('/ShowRationaleReport/?', '/ShowRationaleReport?')
    evidence = {'method': 'operator_authorized_playwright', 'clicked_labels': [], 'navigation_urls': [navigation_url]}
    def capture(response):
        if response.status == 200 and response.headers.get('content-type', '').split(';')[0] == 'application/pdf':
            if not re.search(r'criteria|methodolog|disclaimer|complexity|POSH|Annexure.?[IV]+_', response.url, re.I):
                pdf_responses.append(response)
    context.on('response', capture)
    started = time.monotonic()
    response = None
    try:
        # Chromium's PDF viewer can invalidate Network.getResponseBody before
        # we read it. Fetch explicit PDF URLs through the browser session instead.
        if urlsplit(navigation_url).path.lower().endswith('.pdf'):
            response = context.request.get(navigation_url, timeout=timeout_ms)
            if response.ok:
                pdf_responses.append(response)
            evidence['direct_pdf_request'] = True
        else:
            try:
                response = page.goto(navigation_url, wait_until='domcontentloaded', timeout=timeout_ms)
            except (Error, TimeoutError) as error:
                evidence['navigation_error'] = str(error)[:600]
                if not downloads and not pdf_responses:
                    raise
        # A historic Brickwork staging route can point to the same report id
        # on the production route. Both URLs remain in the attempt evidence.
        if response and response.status >= 400 and 'STAG_BLR_HTML' in url:
            alternate = url.replace('STAG_BLR_HTML', 'BLRHTML').replace('http://', 'https://')
            evidence['navigation_urls'].append(alternate)
            response = page.goto(alternate, wait_until='domcontentloaded', timeout=timeout_ms)
        if response and response.status == 429:
            raise PublisherRateLimit(response.headers.get('retry-after'))
        if response and response.status >= 400:
            raise ValueError(f'HTTP {response.status}: {page.url}')
        if 'icra.in' in url and not downloads:
            button = page.get_by_text('Download', exact=True).filter(visible=True).first
            button.wait_for(state='visible', timeout=15000)
            evidence['clicked_labels'].append('Download')
            try:
                with page.expect_download(timeout=10000):
                    button.click(timeout=10000)
            except TimeoutError:
                # The same report's viewer link is an explicit fallback in the
                # publisher page. Fetch it with this browser's session cookies.
                viewer = page.get_by_text('Report not visible? Tap to open', exact=True).first
                if not downloads and not pdf_responses and viewer.count():
                    href = viewer.get_attribute('href')
                    if href:
                        from urllib.parse import urljoin
                        viewer_url = urljoin(page.url, href)
                        evidence['navigation_urls'].append(viewer_url)
                        pdf = context.request.get(viewer_url, timeout=timeout_ms)
                        if pdf.ok and pdf.body().startswith(b'%PDF-'):
                            pdf_responses.append(pdf)
                if not downloads and not pdf_responses:
                    raise ValueError('ICRA download and publisher viewer both unavailable')
        elif not downloads and not pdf_responses and 'indiaratings.co.in' in url:
            page.wait_for_timeout(15000)
            button = page.get_by_text(re.compile(r'^\s*Download(?:\s+PDF)?\s*$', re.I)).filter(visible=True).first
            if button.count():
                evidence['clicked_labels'].append(button.inner_text())
                try:
                    with page.expect_download(timeout=10000):
                        button.click(timeout=5000)
                except TimeoutError:
                    pass
        policy = DownloadPolicy()
        if downloads:
            download = downloads[-1]
            failure = download.failure()
            if failure:
                raise ValueError(f'Download failed: {failure}')
            path = download.path()
            if not path or path.stat().st_size > policy.max_bytes:
                raise ValueError('Missing or oversized download')
            body = path.read_bytes()
            final_url = download.url
            media_type = 'application/pdf'
            headers = {'content-type': media_type}
            status = None  # A download event does not expose its HTTP status.
            evidence['suggested_filename'] = download.suggested_filename
        elif pdf_responses:
            pdf = pdf_responses[0]
            body = pdf.body()
            final_url, status, headers = pdf.url, pdf.status, pdf.headers
            media_type = 'application/pdf'
        else:
            if response is None:
                raise ValueError('No report response')
            body = response.body()
            final_url, status, headers = page.url, response.status, response.headers
            media_type = 'text/html'
            evidence['html_capture'] = 'original_http_response'
            genuine, _ = is_genuine_html_credit_report(body, final_url=final_url)
            if not genuine and 'indiaratings.co.in' in url:
                # Preserve the HTTP shell separately. The rendered DOM and its
                # PDF are explicitly classified as generated snapshots.
                rendered = page.content().encode('utf-8')
                rendered_genuine, _ = is_genuine_html_credit_report(rendered, final_url=final_url)
                if rendered_genuine or is_india_rating_commentary(rendered, final_url):
                    evidence['_original_http_body'] = body
                    evidence['html_capture'] = 'browser_rendered_dom_snapshot'
                    body = rendered
        if not 0 < len(body) <= policy.max_bytes:
            raise ValueError('Empty or oversized report')
        evidence.update(validate_report(body, media_type, final_url, company_name, aliases))
        return DownloadResult(
            requested_url=url, final_url=final_url, http_status=status,
            response_headers={k: v for k, v in headers.items() if k in ('content-type', 'content-length', 'content-disposition')},
            redirects=(), declared_media_type=media_type, detected_media_type=media_type,
            body=body, byte_size=len(body), result_status='validated',
            elapsed_ms=int((time.monotonic()-started)*1000)), evidence
    finally:
        context.close()


def import_report(conn, job, result, evidence, run_id):
    assert_document_store_ready()
    existing = conn.execute('SELECT content_id FROM artifact_contents WHERE artifact_id=? LIMIT 1',
                            (job['artifact_id'],)).fetchone()
    if existing:
        return 'SKIPPED_EXISTING', int(existing['content_id']), evidence
    ids = tuple(r[0] for r in conn.execute('''SELECT artifact_source_id FROM artifact_sources
        WHERE artifact_id=? AND document_url=?''', (job['artifact_id'], job['requested_url'])))
    now = utc_now()
    browser_snapshot = evidence.get('html_capture') == 'browser_rendered_dom_snapshot'
    if browser_snapshot:
        with conn:
            blob_id, digest = store_response_blob(conn, body=evidence.pop('_original_http_body'),
                media_type='text/html', observed_at_utc=now)
        evidence.update({'original_http_blob_id': blob_id, 'original_http_sha256': digest})
    saved = persist_download_result(conn, artifact_id=job['artifact_id'], artifact_source_ids=ids,
        result=result, policy=DownloadPolicy(),
        started_at_utc=(datetime.fromisoformat(now) - timedelta(milliseconds=result.elapsed_ms)).isoformat(),
        fetched_at_utc=now,
        content_origin='generated_derivative' if browser_snapshot else 'publisher_original',
        official_override=False if browser_snapshot else None)
    content_id = saved.content_id
    evidence.update({'download_attempt_id': saved.download_attempt_id, 'content_sha256': saved.content_sha256,
                     'byte_size': saved.byte_size, 'run_id': run_id})
    if result.detected_media_type == 'text/html':
        try:
            rendered = render_and_persist_html_pdf(conn, artifact_id=job['artifact_id'],
                source_content_id=content_id, source_url=result.final_url or job['requested_url'],
                allow_browser_snapshot=browser_snapshot)
            evidence['rendered_content_id'] = rendered.rendered_content_id
            evidence['renderer_version'] = HTML_PDF_RENDERER_VERSION
        except Exception as error:
            # The original remains usable even if derivative generation fails.
            evidence['pdf_derivative_error'] = f'{type(error).__name__}: {error}'
    with conn:
        conn.execute("UPDATE document_artifacts SET acquisition_status='downloaded' WHERE artifact_id=? AND acquisition_status <> 'validated'", (job['artifact_id'],))
        conn.execute('''UPDATE credit_rating_download_queue SET status='SUCCEEDED',
          last_result_status='OPERATOR_BROWSER_RECOVERED',last_error=NULL,
          next_attempt_at_utc=NULL,lease_owner=NULL,lease_expires_at_utc=NULL,updated_at_utc=?
          WHERE artifact_id=? AND status <> 'SUCCEEDED' ''', (now, job['artifact_id']))
        conn.execute('''UPDATE artifact_download_queue SET status='SKIPPED_ALREADY_HAVE_CONTENT',
          last_result_status='OPERATOR_BROWSER_RECOVERED',last_error=NULL,
          next_attempt_at_utc=NULL,lease_owner=NULL,lease_expires_at_utc=NULL,updated_at_utc=?
          WHERE artifact_id=? AND status <> 'SUCCEEDED' ''', (now, job['artifact_id']))
    return 'SUCCEEDED', content_id, evidence


def finish(conn, job, status, content_id=None, final_url=None, error=None, evidence=None):
    with conn:
        conn.execute('''UPDATE browser_rating_recovery_jobs SET status=?,content_id=?,
          final_url=?,error=?,evidence_json=?,lease_owner=NULL,lease_expires_at_utc=NULL,
          updated_at_utc=? WHERE browser_job_id=?''',
          (status, content_id, final_url, error, json.dumps(evidence or {}, sort_keys=True),
           utc_now(), job['browser_job_id']))


def process_reports(conn, args, owner):
    """Extract and normalize this run's imported reports, with durable outcomes."""
    from ..pipeline.extract_document import extract_document
    from ..pipeline.normalize_credit_rating import normalize_credit_rating_document
    processed = 0
    while not STOP.is_set() and (not args.limit or processed < args.limit):
        worker_state(conn, args.run_id, owner, 'EXTRACTING')
        job = conn.execute('''SELECT * FROM browser_rating_recovery_jobs
            WHERE run_id=? AND status IN ('SUCCEEDED','SKIPPED_EXISTING') AND content_id IS NOT NULL
            AND json_extract(evidence_json,'$.processing_status') IS NULL
            ORDER BY browser_job_id LIMIT 1''', (args.run_id,)).fetchone()
        if job is None:
            pending = conn.execute("SELECT count(*) FROM browser_rating_recovery_jobs WHERE run_id=? AND status IN ('PENDING','RUNNING')", (args.run_id,)).fetchone()[0]
            if args.follow and pending:
                worker_state(conn, args.run_id, owner, 'WAITING')
                STOP.wait(10)
                continue
            break
        evidence = json.loads(job['evidence_json'])
        try:
            original = extract_document(artifact_id=job['artifact_id'], content_id=job['content_id'], db_path=args.db)
            evidence['original_extraction_id'] = original['extraction']['extraction_id']
            pdf_id = evidence.get('rendered_content_id', job['content_id'])
            pdf = original if pdf_id == job['content_id'] else extract_document(
                artifact_id=job['artifact_id'], content_id=pdf_id, db_path=args.db)
            evidence['pdf_extraction_id'] = pdf['extraction']['extraction_id']
            if pdf['extraction']['extraction_status'] != 'ok':
                raise ValueError('PDF extraction is not usable for rating normalization')
            normalized = normalize_credit_rating_document(artifact_id=job['artifact_id'],
                extraction_id=pdf['extraction']['extraction_id'], db_path=args.db)
            evidence['normalization_id'] = normalized['normalization']['normalization_id']
            evidence['normalization_status'] = normalized['normalization']['normalization_status']
            evidence['processing_status'] = 'DONE'
        except Exception as error:
            evidence['processing_status'] = 'FAILED'
            evidence['processing_error'] = f'{type(error).__name__}: {error}'[:3000]
        with conn:
            conn.execute('UPDATE browser_rating_recovery_jobs SET evidence_json=?,updated_at_utc=? WHERE browser_job_id=?',
                (json.dumps(evidence, sort_keys=True), utc_now(), job['browser_job_id']))
        emit({'artifact_id': job['artifact_id'], 'processing_status': evidence['processing_status'],
              'normalization_id': evidence.get('normalization_id'), 'error': evidence.get('processing_error')})
        processed += 1


def repair_pdf_derivatives(conn, args):
    """Version a corrected PDF copy; original HTML and old versions are immutable."""
    jobs = conn.execute('''SELECT job.*,content.content_origin FROM browser_rating_recovery_jobs job
        JOIN artifact_contents content ON content.content_id=job.content_id
        WHERE job.run_id=? AND job.status IN ('SUCCEEDED','SKIPPED_EXISTING')
        AND content.media_type='text/html' ORDER BY job.browser_job_id''', (args.run_id,)).fetchall()
    count = 0
    for job in jobs:
        if args.limit and count >= args.limit:
            break
        evidence = json.loads(job['evidence_json'])
        if evidence.get('renderer_version') == HTML_PDF_RENDERER_VERSION:
            continue
        assert_document_store_ready()
        rendering = render_and_persist_html_pdf(conn, artifact_id=job['artifact_id'],
            source_content_id=job['content_id'], source_url=job['final_url'] or job['requested_url'],
            allow_browser_snapshot=job['content_origin'] == 'generated_derivative')
        previous = evidence.get('rendered_content_id')
        if previous and previous != rendering.rendered_content_id:
            evidence.setdefault('previous_rendered_content_ids', []).append(previous)
            if evidence.get('normalization_id'):
                evidence.setdefault('previous_normalization_ids', []).append(evidence['normalization_id'])
            evidence.pop('processing_status', None)
            evidence.pop('processing_error', None)
        evidence['rendered_content_id'] = rendering.rendered_content_id
        evidence['renderer_version'] = HTML_PDF_RENDERER_VERSION
        with conn:
            conn.execute('UPDATE browser_rating_recovery_jobs SET evidence_json=?,updated_at_utc=? WHERE browser_job_id=?',
                (json.dumps(evidence, sort_keys=True), utc_now(), job['browser_job_id']))
        count += 1
    emit({'run_id': args.run_id, 'corrected_pdf_derivatives': count})


def serve_bridge(conn, args):
    try:
        _serve_bridge(conn, args)
    finally:
        owner = f'headed-browser:{args.run_id}'
        release_owned_jobs(conn, args.run_id, owner)
        worker_state(conn, args.run_id, owner, 'STOPPED')


def _serve_bridge(conn, args):
    """JSON-lines bridge for the visible host browser and the container DB."""
    owner = f'headed-browser:{args.run_id}'
    for line in sys.stdin:
        try:
            request = json.loads(line)
            action = request['action']
            if action == 'release':
                emit({'released': release_owned_jobs(conn, args.run_id, owner)})
                continue
            worker_state(conn, args.run_id, owner, 'DOWNLOADING')
            if action == 'claim':
                job = claim(conn, args.run_id, ['www.icra.in'], owner, request.get('job_ids', ()))
                cooldown = conn.execute('SELECT blocked_until_utc FROM browser_rating_recovery_hosts WHERE run_id=? AND target_host=? AND blocked_until_utc > ?', (args.run_id, 'www.icra.in', utc_now())).fetchone()
                worker_state(conn, args.run_id, owner, 'DOWNLOADING' if job else 'WAITING')
                emit({'job': dict(job) if job else None, 'cooldown_until_utc': cooldown[0] if cooldown else None})
                continue
            job = conn.execute('''SELECT * FROM browser_rating_recovery_jobs
                WHERE browser_job_id=? AND run_id=? AND status='RUNNING' AND lease_owner=?''',
                (request['browser_job_id'], args.run_id, owner)).fetchone()
            if job is None:
                raise ValueError('Bridge job is not owned by this run')
            if action == 'rate_limit':
                until = cool_down(conn, args.run_id, 'www.icra.in', request.get('retry_after'))
                finish(conn, job, 'PENDING', error='Publisher HTTP 429; waiting for cooldown', evidence={'cooldown_until_utc': until})
                emit({'status': 'PENDING', 'cooldown_until_utc': until})
            elif action == 'import':
                body = base64.b64decode(request['body_base64'], validate=True)
                if not 0 < len(body) <= DownloadPolicy().max_bytes:
                    raise ValueError('Bridge body size is invalid')
                evidence = request.get('evidence', {})
                evidence.update(validate_report(body, 'application/pdf', request['final_url'],
                    job['company_name'], company_names(conn, job['artifact_id'])))
                result = DownloadResult(requested_url=job['requested_url'], final_url=request['final_url'],
                    http_status=request.get('http_status'), response_headers=request.get('response_headers', {}),
                    redirects=(), declared_media_type='application/pdf', detected_media_type='application/pdf',
                    body=body, byte_size=len(body), result_status='validated', elapsed_ms=request.get('elapsed_ms', 0))
                status, content_id, evidence = import_report(conn, job, result, evidence, args.run_id)
                finish(conn, job, status, content_id, result.final_url, evidence=evidence)
                emit({'status': status, 'artifact_id': job['artifact_id'], 'content_id': content_id, 'byte_size': len(body)})
            elif action == 'finish':
                status = request['status']
                if status not in ('FAILED', 'REVIEW_REQUIRED', 'SKIPPED_EXISTING', 'PENDING'):
                    raise ValueError('Invalid bridge completion status')
                existing = conn.execute('SELECT content_id FROM artifact_contents WHERE artifact_id=? LIMIT 1', (job['artifact_id'],)).fetchone()
                finish(conn, job, status, content_id=existing[0] if existing else None,
                    error=request.get('error'), evidence=request.get('evidence'))
                emit({'status': status, 'artifact_id': job['artifact_id']})
            else:
                raise ValueError('Unknown bridge action')
        except Exception as error:
            emit({'error': f'{type(error).__name__}: {error}'})


def main():
    STOP.clear()
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
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
    if args.bridge:
        serve_bridge(conn, args)
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
    # Container restart reuses its hostname/PID; the previous process is gone.
    release_owned_jobs(conn, args.run_id, owner)
    count = 0
    last_host_at = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            while not STOP.is_set() and (not args.limit or count < args.limit):
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
                        STOP.wait(max(1, min(60, seconds)))
                        continue
                    break
                evidence = {}
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
                            if STOP.wait(wait):
                                release_owned_jobs(conn, args.run_id, owner)
                                break
                        worker_state(conn, args.run_id, owner, 'DOWNLOADING')
                        result, evidence = fetch_report(browser, job['requested_url'], job['company_name'],
                            aliases=company_names(conn, job['artifact_id']))
                        last_host_at[job['target_host']] = time.monotonic()
                        final_url = result.final_url
                        status, content_id, evidence = import_report(conn, job, result, evidence, args.run_id)
                except PublisherRateLimit as exc:
                    until = cool_down(conn, args.run_id, job['target_host'], exc.retry_after)
                    status, error = 'PENDING', str(exc)
                    evidence = {'retry_after': exc.retry_after, 'cooldown_until_utc': until}
                except Exception as exc:
                    last_host_at[job['target_host']] = time.monotonic()
                    error = f'{type(exc).__name__}: {exc}'[:3000]
                    status = 'REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in error else 'FAILED'
                    if isinstance(exc, PublisherFileUnavailable):
                        evidence['resolution'] = 'PUBLISHER_SOURCE_UNAVAILABLE'
                        evidence['resolution_confirmed_at_utc'] = utc_now()
                    elif transient_error(exc) and job['attempt_count'] < 3:
                        status = 'PENDING'
                    # A storage failure stops the batch instead of failing every source.
                    if isinstance(exc, RuntimeError) and 'store' in str(exc).lower():
                        finish(conn, job, 'PENDING', error=error)
                        raise
                finish(conn, job, status, content_id, final_url, error, evidence)
                emit({'run_id': args.run_id, 'browser_job_id': job['browser_job_id'],
                      'artifact_id': job['artifact_id'], 'host': job['target_host'],
                      'status': status, 'content_id': content_id, 'error': error,
                      'byte_size': evidence.get('byte_size'), 'pdf_derivative_error': evidence.get('pdf_derivative_error')})
                count += 1
        finally:
            browser.close()
            release_owned_jobs(conn, args.run_id, owner)
            worker_state(conn, args.run_id, owner, 'STOPPED')
            conn.close()


if __name__ == '__main__':
    main()
