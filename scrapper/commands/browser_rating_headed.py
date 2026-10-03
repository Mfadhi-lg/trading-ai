"""Run the user's visible Chromium flow; import PDFs through the live DB bridge."""
import argparse
import base64
import hashlib
import json
import subprocess
import sys
import signal
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urljoin
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from playwright.sync_api import sync_playwright, TimeoutError
from scrapper.config import assert_configured_writable_targets
from scrapper.commands.browser_rating_host_config import assert_host_targets, bridge_command


class PublisherRateLimit(ValueError):
    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        super().__init__('HTTP 429: publisher rate limit')


def hide_mac_browser(browser):
    """Hide only this automation app, keeping personal browser windows alone."""
    session = browser.new_browser_cdp_session()
    try:
        processes = session.send('SystemInfo.getProcessInfo')['processInfo']
        pid = int(next(process['id'] for process in processes if process['type'] == 'browser'))
    finally:
        session.detach()
    script = f'tell application "System Events" to set visible of (first process whose unix id is {pid}) to false'
    check = f'tell application "System Events" to get visible of (first process whose unix id is {pid})'
    result = subprocess.run(['osascript', '-e', script, '-e', check], check=True,
        capture_output=True, text=True, timeout=15)
    if result.stdout.strip() != 'false':
        raise RuntimeError('Automation browser could not be hidden')
    return pid


def main():
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--background', action='store_true', help='Reuse a hidden browser window')
    parser.add_argument('--job-id', action='append', type=int, default=[], help='Download only these ledger jobs')
    args = parser.parse_args()
    assert_configured_writable_targets()
    assert_host_targets()
    bridge = subprocess.Popen(bridge_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    def exchange(request):
        bridge.stdin.write(json.dumps(request) + '\n')
        bridge.stdin.flush()
        line = bridge.stdout.readline()
        if not line:
            raise RuntimeError('Live database bridge disconnected')
        result = json.loads(line)
        if result.get('error'):
            raise ValueError(result['error'])
        return result
    count = 0
    last_finished = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        background_context = background_page = None
        if args.background:
            background_context = browser.new_context(accept_downloads=True, viewport={'width': 1440, 'height': 1000})
            background_context.route('**/*', lambda route: route.abort()
                if route.request.resource_type in ('image', 'font', 'media') else route.continue_())
            background_page = background_context.new_page()
            session = background_context.new_cdp_session(background_page)
            window = session.send('Browser.getWindowForTarget')
            session.send('Browser.setWindowBounds', {'windowId': window['windowId'], 'bounds': {'windowState': 'minimized'}})
            bounds = session.send('Browser.getWindowBounds', {'windowId': window['windowId']})
            print(json.dumps({'background_window': bounds['bounds']['windowState']}), flush=True)
            session.detach()
            if sys.platform == 'darwin':
                print(json.dumps({'browser_hidden': True, 'browser_pid': hide_mac_browser(browser)}), flush=True)
        try:
            while not stopping.is_set() and (not args.limit or count < args.limit):
                claim = exchange({'action': 'claim', 'job_ids': args.job_id})
                job = claim['job']
                if job is None:
                    if claim.get('cooldown_until_utc'):
                        until = claim['cooldown_until_utc']
                        print(json.dumps({'cooldown_until_utc': until}), flush=True)
                        seconds = (datetime.fromisoformat(until) - datetime.now(timezone.utc)).total_seconds()
                        stopping.wait(max(1, min(60, seconds)))
                        continue
                    break
                delay = last_finished + 4 - time.monotonic()
                if delay > 0:
                    if stopping.wait(delay):
                        break
                context = background_context or browser.new_context(accept_downloads=True, viewport={'width': 1440, 'height': 1000})
                if not background_context:
                    context.route('**/*', lambda route: route.abort()
                        if route.request.resource_type in ('image', 'font', 'media') else route.continue_())
                page = background_page or context.new_page()
                download = None
                started = time.monotonic()
                evidence = {'method': 'operator_authorized_playwright_headed', 'clicked_labels': []}
                blocked = False
                try:
                    # Wait for the actual download control, rather than every
                    # unrelated page resource finishing DOMContentLoaded.
                    navigation_url = job['requested_url'].replace('/ShowRationaleReport/?', '/ShowRationaleReport?')
                    evidence['navigation_url'] = navigation_url
                    response = page.goto(navigation_url, wait_until='commit', timeout=90000)
                    text = page.locator('body').first.inner_text(timeout=30000)
                    if 'You have been blocked' in text or 'FINGERPRINT_RISK_LEVEL' in text:
                        blocked = True
                        raise ValueError('Publisher security service blocked this browser; run stopped')
                    if response and response.status == 429:
                        raise PublisherRateLimit(response.headers.get('retry-after'))
                    if response and response.status >= 400:
                        raise ValueError(f'HTTP {response.status}')
                    button = page.get_by_text('Download', exact=True).filter(visible=True).first
                    button.wait_for(state='visible', timeout=60000)
                    # The site's large hover menu can intercept pointer clicks.
                    # Keyboard activation uses the same visible download control.
                    page.mouse.move(0, 0)
                    button.focus()
                    evidence['clicked_labels'].append('Download (keyboard activation)')
                    try:
                        with page.expect_download(timeout=60000) as event:
                            button.press('Enter')
                        download = event.value
                        failure = download.failure()
                        if failure:
                            raise ValueError(failure)
                        path = download.path()
                        if not path or path.stat().st_size > 256 * 1024 * 1024:
                            raise ValueError('Missing or oversized PDF')
                        body = path.read_bytes()
                        final_url = download.url
                        http_status = None
                        evidence['suggested_filename'] = download.suggested_filename
                    except TimeoutError:
                        viewer = page.get_by_text('Report not visible? Tap to open', exact=True).first
                        href = viewer.get_attribute('href')
                        if not href:
                            raise ValueError('No report download or viewer link')
                        pdf = context.request.get(urljoin(page.url, href), timeout=120000)
                        if pdf.status == 429:
                            raise PublisherRateLimit(pdf.headers.get('retry-after'))
                        if not pdf.ok:
                            raise ValueError(f'PDF viewer HTTP {pdf.status}')
                        body, final_url, http_status = pdf.body(), pdf.url, pdf.status
                        evidence['viewer_fallback'] = True
                    if not body.startswith(b'%PDF-'):
                        raise ValueError('Publisher returned non-PDF content')
                    result = exchange({'action': 'import', 'browser_job_id': job['browser_job_id'],
                        'body_base64': base64.b64encode(body).decode(), 'final_url': final_url,
                        'http_status': http_status, 'response_headers': {'content-type': 'application/pdf'},
                        'elapsed_ms': int((time.monotonic()-started)*1000), 'evidence': evidence})
                except PublisherRateLimit as error:
                    result = exchange({'action': 'rate_limit', 'browser_job_id': job['browser_job_id'],
                        'retry_after': error.retry_after})
                except Exception as error:
                    message = f'{type(error).__name__}: {error}'[:3000]
                    if 'PUBLISHER_SOURCE_UNAVAILABLE' in message:
                        evidence.update(resolution='PUBLISHER_SOURCE_UNAVAILABLE',
                            publisher_placeholder_sha256=hashlib.sha256(body).hexdigest())
                    result = exchange({'action': 'finish', 'browser_job_id': job['browser_job_id'],
                        'status': 'PENDING' if blocked or (isinstance(error, TimeoutError) and job['attempt_count'] < 3)
                            else 'REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in message else 'FAILED',
                        'error': message, 'evidence': evidence})
                    result['error'] = message
                finally:
                    if download is not None:
                        download.delete()
                    if not background_context:
                        context.close()
                    last_finished = time.monotonic()
                result.update({'browser_job_id': job['browser_job_id'], 'artifact_id': job['artifact_id']})
                print(json.dumps(result), flush=True)
                count += 1
                if blocked:
                    raise SystemExit(2)
        finally:
            if background_context:
                background_context.close()
            browser.close()
            bridge.stdin.close()
            bridge.wait(timeout=30)
            print(json.dumps({'worker_status': 'STOPPED', 'reason': 'shutdown' if stopping.is_set() else 'queue_drained'}), flush=True)


if __name__ == '__main__':
    main()
