"""Operator-authorized browser publisher transport and original/DOM capture adapters."""
import re
import time
from urllib.parse import urlsplit
from playwright.sync_api import Error, TimeoutError
from ...documents.downloader import DownloadPolicy, DownloadResult
from ...documents.credit_rating_resolver import is_genuine_html_credit_report
from ...documents.recovery.browser_rating.errors import PublisherRateLimit
from ...documents.recovery.browser_rating.validation import validate_report, is_india_rating_commentary

def fetch_report(browser, url, company_name, timeout_ms=120000, aliases=(), *, validate_report=validate_report,
                 is_genuine_html_credit_report=is_genuine_html_credit_report,
                 is_india_rating_commentary=is_india_rating_commentary,
                 DownloadPolicy=DownloadPolicy, DownloadResult=DownloadResult,
                 time=time, Error=Error, TimeoutError=TimeoutError, shared_requests=None, policy=None):
    policy = policy or DownloadPolicy()
    context = browser.new_context(accept_downloads=True, service_workers='block')
    if shared_requests is not None:
        try:
            shared_requests.install(context,documents_only=any(
                host in url for host in ('crisil.com','crisilratings.com','bcrisp.in')))
        except BaseException:
            context.close()
            raise
    elif any(host in url for host in ('crisil.com', 'crisilratings.com', 'bcrisp.in')):
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
    def api_get(target):
        if shared_requests is None: return context.request.get(target,timeout=timeout_ms)
        from types import SimpleNamespace
        status,final,headers,body = shared_requests.viewer(target)
        return SimpleNamespace(status=status,url=final,headers={k.lower():v for k,v in headers.items()},
            ok=200<=status<300,body=lambda:body)
    try:
        # Chromium's PDF viewer can invalidate Network.getResponseBody before
        # we read it. Fetch explicit PDF URLs through the browser session instead.
        if urlsplit(navigation_url).path.lower().endswith('.pdf'):
            response = api_get(navigation_url)
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
        if 'icra.in' in url and not downloads and (shared_requests is None or not pdf_responses):
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
                        pdf = api_get(viewer_url)
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
        if shared_requests is not None: shared_requests.check()
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
    except Exception:
        if shared_requests is not None: shared_requests.check()
        raise
    finally:
        if shared_requests is not None: shared_requests.close()
        context.close()


