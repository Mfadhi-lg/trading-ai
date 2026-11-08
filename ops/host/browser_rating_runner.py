"""Host browser runner owner; dependencies supplied per call by the compatible command adapter."""

class PublisherRateLimit(ValueError):
    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        super().__init__('HTTP 429: publisher rate limit')


def hide_mac_browser(browser, *, hooks):
    """Hide only this automation app, keeping personal browser windows alone."""
    session = browser.new_browser_cdp_session()
    try:
        processes = session.send('SystemInfo.getProcessInfo')['processInfo']
        pid = int(next(process['id'] for process in processes if process['type'] == 'browser'))
    finally:
        session.detach()
    script = f'tell application "System Events" to set visible of (first process whose unix id is {pid}) to false'
    check = f'tell application "System Events" to get visible of (first process whose unix id is {pid})'
    result = hooks.subprocess.run(['osascript', '-e', script, '-e', check], check=True,
        capture_output=True, text=True, timeout=15)
    if result.stdout.strip() != 'false':
        raise RuntimeError('Automation browser could not be hidden')
    return pid


def main(*, hooks):
    from scrapper.operations.workers.lifecycle import WorkerLifecycle, stderr_heartbeat
    lifecycle = WorkerLifecycle(event=hooks.threading.Event(), signal_api=hooks.signal, observer=stderr_heartbeat)
    with lifecycle.signals():
        return _run(hooks=hooks, stopping=lifecycle)


def _run(*, hooks, stopping, shared_permits=False, authorization=None):
    from scrapper.core.attempts import claim_attempt, retry_remaining, BROWSER_MAX_INVOCATIONS
    parser = hooks.argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--background', action='store_true', help='Reuse a hidden browser window')
    parser.add_argument('--job-id', action='append', type=int, default=[], help='Download only these ledger jobs')
    args = parser.parse_args()
    hooks.assert_configured_writable_targets()
    hooks.assert_host_targets()
    bridge = hooks.subprocess.Popen(hooks.bridge_command(), stdin=hooks.subprocess.PIPE, stdout=hooks.subprocess.PIPE, text=True, bufsize=1)
    def exchange(request):
        return hooks.bridge_exchange(bridge, request)
    if shared_permits:
        from .browser_host_requests import handshake, BrowserRequests, AdministrativeDefer
        try:
            handshake(exchange,authorization)
        except BaseException:
            bridge.stdin.close()
            bridge.wait(timeout=30)
            raise
    count = 0
    last_finished = 0
    with hooks.sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        background_context = background_page = None
        if args.background and not shared_permits:
            background_context = browser.new_context(accept_downloads=True, viewport={'width': 1440, 'height': 1000})
            background_context.route('**/*', lambda route: route.abort()
                if route.request.resource_type in ('image', 'font', 'media') else route.continue_())
            background_page = background_context.new_page()
            session = background_context.new_cdp_session(background_page)
            window = session.send('Browser.getWindowForTarget')
            session.send('Browser.setWindowBounds', {'windowId': window['windowId'], 'bounds': {'windowState': 'minimized'}})
            bounds = session.send('Browser.getWindowBounds', {'windowId': window['windowId']})
            print(hooks.json.dumps({'background_window': bounds['bounds']['windowState']}), flush=True)
            session.detach()
            if hooks.sys.platform == 'darwin':
                print(hooks.json.dumps({'browser_hidden': True, 'browser_pid': hooks.hide_mac_browser(browser)}), flush=True)
        try:
            while not stopping.is_set() and (not args.limit or count < args.limit):
                claim = exchange({'action': 'claim', 'job_ids': args.job_id})
                job = claim['job']
                if job is None:
                    stopping.beat("IDLE")
                    if claim.get('cooldown_until_utc'):
                        until = claim['cooldown_until_utc']
                        print(hooks.json.dumps({'cooldown_until_utc': until}), flush=True)
                        seconds = (hooks.datetime.fromisoformat(until) - hooks.datetime.now(hooks.timezone.utc)).total_seconds()
                        stopping.wait(max(1, min(60, seconds)), state="COOLDOWN")
                        continue
                    break
                stopping.claimed(job['browser_job_id'])
                delay = last_finished + 4 - hooks.time.monotonic()
                if delay > 0:
                    if stopping.wait(delay, state="THROTTLED"):
                        break
                stopping.beat("WORKING")
                context = background_context or browser.new_context(accept_downloads=True, viewport={'width': 1440, 'height': 1000},
                    **({'service_workers':'block'} if shared_permits else {}))
                requests = BrowserRequests(exchange,job,stopping,authorization=authorization) if shared_permits else None
                if requests is not None:
                    requests.install(context)
                elif not background_context:
                    context.route('**/*', lambda route: route.abort()
                        if route.request.resource_type in ('image', 'font', 'media') else route.continue_())
                page = background_page or context.new_page()
                download = None
                started = hooks.time.monotonic()
                evidence = {'method': 'operator_authorized_playwright_headed', 'clicked_labels': []}
                blocked = False
                try:
                    # Wait for the actual download control, rather than every
                    # unrelated page resource finishing DOMContentLoaded.
                    navigation_url = job['requested_url'].replace('/ShowRationaleReport/?', '/ShowRationaleReport?')
                    evidence['navigation_url'] = navigation_url
                    response = page.goto(navigation_url, wait_until='commit', timeout=90000)
                    if requests is not None: requests.check()
                    text = page.locator('body').first.inner_text(timeout=30000)
                    if 'You have been blocked' in text or 'FINGERPRINT_RISK_LEVEL' in text:
                        blocked = True
                        raise ValueError('Publisher security service blocked this browser; run stopped')
                    if response and response.status == 429:
                        raise hooks.PublisherRateLimit(response.headers.get('retry-after'))
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
                    except hooks.TimeoutError:
                        viewer = page.get_by_text('Report not visible? Tap to open', exact=True).first
                        href = viewer.get_attribute('href')
                        if not href:
                            raise ValueError('No report download or viewer link')
                        if requests is not None:
                            status, url, headers, payload = requests.viewer(hooks.urljoin(page.url, href))
                            from types import SimpleNamespace
                            pdf = SimpleNamespace(status=status,url=url,headers=headers,ok=200<=status<300,body=lambda:payload)
                        else:
                            pdf = context.request.get(hooks.urljoin(page.url, href), timeout=120000)
                        if pdf.status == 429:
                            raise hooks.PublisherRateLimit(pdf.headers.get('retry-after'))
                        if not pdf.ok:
                            raise ValueError(f'PDF viewer HTTP {pdf.status}')
                        body, final_url, http_status = pdf.body(), pdf.url, pdf.status
                        evidence['viewer_fallback'] = True
                    if requests is not None: requests.check()
                    if not body.startswith(b'%PDF-'):
                        raise ValueError('Publisher returned non-PDF content')
                    result = exchange({'action': 'import', 'browser_job_id': job['browser_job_id'], 'claim_attempt_count': claim_attempt(job, 'browser'), 'claim_token': job['claim_token'],
                        'body_base64': hooks.base64.b64encode(body).decode(), 'final_url': final_url,
                        'http_status': http_status, 'response_headers': {'content-type': 'application/pdf'},
                        'elapsed_ms': int((hooks.time.monotonic()-started)*1000), 'evidence': evidence})
                except hooks.PublisherRateLimit as error:
                    result = exchange({'action': 'rate_limit', 'browser_job_id': job['browser_job_id'], 'claim_attempt_count': claim_attempt(job, 'browser'), 'claim_token': job['claim_token'],
                        'retry_after': error.retry_after})
                except Exception as error:
                    if requests is not None and requests.failure is not None:
                        error = requests.failure
                    if requests is not None and isinstance(error, AdministrativeDefer):
                        result = requests.defer(error)
                        result.update({'browser_job_id':job['browser_job_id'],'artifact_id':job['artifact_id']})
                        print(hooks.json.dumps(result),flush=True)
                        count += 1
                        stopping.finished()
                        continue
                    message = f'{type(error).__name__}: {error}'[:3000]
                    if 'PUBLISHER_SOURCE_UNAVAILABLE' in message:
                        evidence.update(resolution='PUBLISHER_SOURCE_UNAVAILABLE',
                            publisher_placeholder_sha256=hooks.hashlib.sha256(body).hexdigest())
                    result = exchange({'action': 'finish', 'browser_job_id': job['browser_job_id'], 'claim_attempt_count': claim_attempt(job, 'browser'), 'claim_token': job['claim_token'],
                        'status': 'PENDING' if blocked or (isinstance(error, hooks.TimeoutError) and retry_remaining(claim_attempt(job, 'browser'), BROWSER_MAX_INVOCATIONS))
                            else 'REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in message else 'FAILED',
                        'error': message, 'evidence': evidence})
                    result['error'] = message
                finally:
                    if requests is not None: requests.close()
                    if download is not None:
                        download.delete()
                    if not background_context:
                        context.close()
                    last_finished = hooks.time.monotonic()
                result.update({'browser_job_id': job['browser_job_id'], 'claim_attempt_count': claim_attempt(job, 'browser'), 'claim_token': job['claim_token'], 'artifact_id': job['artifact_id']})
                print(hooks.json.dumps(result), flush=True)
                count += 1
                stopping.finished()
                if blocked:
                    raise SystemExit(2)
        finally:
            if background_context:
                background_context.close()
            browser.close()
            bridge.stdin.close()
            bridge.wait(timeout=30)
            print(hooks.json.dumps({'worker_status': 'STOPPED', 'reason': 'shutdown' if stopping.is_set() else 'queue_drained'}), flush=True)


